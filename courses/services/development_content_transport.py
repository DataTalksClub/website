"""Private, version-pinned S3 transport for the development content bootstrap."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from courses.services.development_content_import import (
    APPROVED_SOURCE_SIZE,
    DevelopmentContentImportError,
)


# Scratch data belongs in the project-local, gitignored .tmp/, never a shared
# system temporary directory. The private subdirectory is created 0700 so the
# staging root carries no group or other write bit of its own.
_EPHEMERAL_STAGING_ROOT = Path(__file__).resolve().parents[2] / ".tmp" / "course-content-transport"
_STAGING_DIRECTORY_PREFIX = "dtc-course-content-"
_STAGING_FILENAME = "artifact.sqlite3"


def _assert_transport_metadata(response: dict, expected_kms_key_arn: str) -> None:
    if response.get("ServerSideEncryption") != "aws:kms":
        raise DevelopmentContentImportError("transport-not-kms-encrypted")
    if response.get("SSEKMSKeyId") != expected_kms_key_arn:
        raise DevelopmentContentImportError("transport-kms-key-mismatch")
    if int(response.get("ContentLength", -1)) != APPROVED_SOURCE_SIZE:
        raise DevelopmentContentImportError("transport-size-mismatch")


def _close_response_body(response: dict | None) -> None:
    if response is None:
        return
    body = response.get("Body")
    if body is None:
        return
    try:
        body.close()
    except Exception:
        pass


def _validated_ephemeral_staging_root() -> Path:
    root = _EPHEMERAL_STAGING_ROOT
    try:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        raise DevelopmentContentImportError("transport-local-storage-failed") from None
    try:
        metadata = root.lstat()
    except OSError:
        raise DevelopmentContentImportError("transport-local-storage-failed") from None
    permissions = stat.S_IMODE(metadata.st_mode)
    shared_writes = permissions & (stat.S_IWGRP | stat.S_IWOTH)
    if (
        not root.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (shared_writes and not permissions & stat.S_ISVTX)
    ):
        raise DevelopmentContentImportError("transport-local-storage-failed")
    try:
        writable = os.access(root, os.W_OK | os.X_OK, effective_ids=True)
    except (NotImplementedError, OSError):
        raise DevelopmentContentImportError("transport-local-storage-failed") from None
    if not writable:
        raise DevelopmentContentImportError("transport-local-storage-failed")
    return root


def _remove_private_staging(directory: Path | None, path: Path | None) -> bool:
    cleaned = True
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleaned = False
    if directory is not None:
        try:
            directory.rmdir()
        except OSError:
            cleaned = False
    return cleaned


def _create_private_staging_file() -> tuple[Path, Path, int]:
    root = _validated_ephemeral_staging_root()
    directory: Path | None = None
    path: Path | None = None
    descriptor = -1
    owned_directory = False
    try:
        directory = Path(tempfile.mkdtemp(prefix=_STAGING_DIRECTORY_PREFIX, dir=root))
        if directory.parent != root or not directory.name.startswith(_STAGING_DIRECTORY_PREFIX):
            raise OSError("unexpected staging directory")
        directory_metadata = directory.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
        ):
            raise OSError("unsafe staging directory")
        owned_directory = True
        os.chmod(directory, 0o700, follow_symlinks=False)
        directory_metadata = directory.lstat()
        if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
            raise OSError("staging directory is not private")

        path = directory / _STAGING_FILENAME
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise OSError("no-follow file creation is unavailable")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
        if (
            not stat.S_ISREG(descriptor_metadata.st_mode)
            or descriptor_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
            or stat.S_ISLNK(path_metadata.st_mode)
            or path_metadata.st_dev != descriptor_metadata.st_dev
            or path_metadata.st_ino != descriptor_metadata.st_ino
        ):
            raise OSError("unsafe staging file")
        return directory, path, descriptor
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if owned_directory:
            _remove_private_staging(directory, path)
        raise DevelopmentContentImportError("transport-local-storage-failed") from None


@contextmanager
def downloaded_s3_artifact(
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_bucket_owner: str,
    expected_kms_key_arn: str,
) -> Iterator[Path]:
    """Download one immutable encrypted object without rendering its locator."""

    if not all((bucket, key, version_id, expected_bucket_owner, expected_kms_key_arn)):
        raise DevelopmentContentImportError("transport-argument-missing")
    import boto3

    client = boto3.client("s3")
    request = {
        "Bucket": bucket,
        "Key": key,
        "VersionId": version_id,
        "ExpectedBucketOwner": expected_bucket_owner,
    }
    response = None
    try:
        head = client.head_object(**request)
        _assert_transport_metadata(head, expected_kms_key_arn)
        response = client.get_object(**request)
        _assert_transport_metadata(response, expected_kms_key_arn)
        body = response.get("Body")
        if body is None or not callable(getattr(body, "iter_chunks", None)):
            raise DevelopmentContentImportError("transport-response-invalid")
    except DevelopmentContentImportError:
        _close_response_body(response)
        raise
    except Exception:
        _close_response_body(response)
        raise DevelopmentContentImportError("transport-download-failed") from None

    staging_directory = None
    path = None
    descriptor = -1
    total = 0
    try:
        try:
            staging_directory, path, descriptor = _create_private_staging_file()
            destination = os.fdopen(descriptor, "wb")
            descriptor = -1
            with destination:
                try:
                    for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > APPROVED_SOURCE_SIZE:
                            raise DevelopmentContentImportError("transport-size-mismatch")
                        try:
                            destination.write(chunk)
                        except OSError:
                            raise DevelopmentContentImportError(
                                "transport-local-storage-failed"
                            ) from None
                except DevelopmentContentImportError:
                    raise
                except Exception:
                    raise DevelopmentContentImportError("transport-download-failed") from None
                try:
                    destination.flush()
                    os.fsync(destination.fileno())
                except OSError:
                    raise DevelopmentContentImportError("transport-local-storage-failed") from None
        except DevelopmentContentImportError:
            raise
        except Exception:
            raise DevelopmentContentImportError("transport-local-storage-failed") from None
        if total != APPROVED_SOURCE_SIZE:
            raise DevelopmentContentImportError("transport-size-mismatch")
        if path is None:
            raise DevelopmentContentImportError("transport-local-storage-failed")
        yield path
    finally:
        cleaned = True
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                cleaned = False
        _close_response_body(response)
        if not _remove_private_staging(staging_directory, path):
            cleaned = False
        if not cleaned:
            raise DevelopmentContentImportError("transport-local-cleanup-failed")


def delete_s3_artifact_version(
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_bucket_owner: str,
) -> None:
    import boto3

    try:
        boto3.client("s3").delete_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id,
            ExpectedBucketOwner=expected_bucket_owner,
        )
    except Exception:
        raise DevelopmentContentImportError("transport-delete-failed") from None
