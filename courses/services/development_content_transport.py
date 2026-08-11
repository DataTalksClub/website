"""Private, version-pinned S3 transport for the development content bootstrap."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from courses.services.development_content_import import (
    APPROVED_SOURCE_SIZE,
    DevelopmentContentImportError,
)


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

    scratch = Path(settings.BASE_DIR) / ".tmp"
    path = None
    descriptor = -1
    total = 0
    try:
        try:
            scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix="course-content-",
                suffix=".sqlite3",
                dir=scratch,
            )
            path = Path(raw_path)
            os.chmod(path, 0o600)
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
                        destination.write(chunk)
                except DevelopmentContentImportError:
                    raise
                except Exception:
                    raise DevelopmentContentImportError(
                        "transport-download-failed"
                    ) from None
                destination.flush()
                os.fsync(destination.fileno())
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
        if descriptor >= 0:
            os.close(descriptor)
        _close_response_body(response)
        if path is not None:
            path.unlink(missing_ok=True)


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
