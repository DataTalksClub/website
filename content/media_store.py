"""Pluggable read-through store for the public projection media objects.

The 1,253 projection images are no longer carried in the git working tree.  Every
public ``/images/...`` request resolves its record from ``media.json`` first and then
reads the object through one of three interchangeable backends:

``local``
    a filesystem directory (the historic ``content/public_projection/media`` tree),
    used by developers and testers so the rendered pages show real artwork;
``memory``
    a deterministic, offline fixture derived from ``media.json`` that serves a minimal
    valid image per record.  It needs no hydrated tree, no network, and no credentials,
    and it refuses to activate under production settings;
``s3``
    the object store that owns the published release assets.

The object key is derived from the *matched record only*, never from the raw request
path, so request encoding, traversal, and the historic filename that contains a
literal space can never reach the store as attacker-controlled input.

Every served object is verified against the record's ``provenance.checksum`` before a
single byte reaches the client.  A record that exists but whose object is missing,
oversized, unreadable, slow, or corrupted fails closed; the public view maps that to a
``502`` rather than a ``404`` so an edge cache can never memorise an outage as
"not found".
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.runtime_config import get_int_setting, get_str_setting

PROJECTION_ROOT = Path(__file__).with_name("public_projection")
MEDIA_RECORDS_FILENAME = "media.json"
#: Every projection media record key is path-mirrored below this segment, which is also
#: the public URL prefix.  The historic on-disk tree drops it, because the local root is
#: itself named ``media``.
RECORD_KEY_PREFIX = "images/"

DEFAULT_BACKEND = "local"
#: Empty: record keys already start with ``images/``, so the objects sit at the bucket
#: root and share it with unrelated sections such as ``site-assets/``.
DEFAULT_S3_PREFIX = ""
DEFAULT_S3_TIMEOUT_SECONDS = 5.0
# The largest known projection object is 3,022,797 bytes.  The default ceiling keeps a
# wide margin above it while still bounding the per-request allocation.
DEFAULT_MAX_OBJECT_BYTES = 8 * 1024 * 1024
SUPPORTED_BACKENDS = ("local", "memory", "s3")
#: Environments that run the release image.  That image excludes the media tree from its
#: build context, so a deployed workload has to read the objects from the object store;
#: `local` is only a valid backend for a checkout that has the files on disk.
DEPLOYED_ENVIRONMENTS = frozenset({"development", "production"})

HYDRATE_COMMAND = "scripts/prod/sync_public_media_hydrate.py"


class MediaStoreError(RuntimeError):
    """A recorded media object could not be served safely.

    ``reason`` is a short, redacted, machine-readable token.  Neither the message nor
    the reason ever carries a bucket name, key, credential, or upstream detail.
    """

    reason = "media-store-error"


class MediaObjectMissing(MediaStoreError):
    reason = "object-missing"


class MediaObjectTooLarge(MediaStoreError):
    reason = "object-oversized"


class MediaStoreUnavailable(MediaStoreError):
    reason = "store-unavailable"


class MediaChecksumMismatch(MediaStoreError):
    reason = "checksum-mismatch"


class MediaRecordInvalid(MediaStoreError):
    reason = "record-invalid"


@dataclass(frozen=True, slots=True)
class MediaObject:
    """Bytes retrieved from a store, plus any checksum the store attested itself."""

    payload: bytes
    #: Lower-case hex sha256 reported by the store (S3 ``ChecksumSHA256``), when it
    #: supplies one.  It is an *additional* constraint: the digest of the retrieved
    #: bytes is always recomputed as well.
    attested_checksum: str | None = None


@dataclass(frozen=True, slots=True)
class MediaObjectStat:
    """Store-side metadata used by the operator verify tooling."""

    size: int
    checksum: str | None


@dataclass(frozen=True, slots=True)
class MediaStoreConfig:
    backend: str
    local_root: Path
    s3_bucket: str
    s3_prefix: str
    s3_region: str
    s3_endpoint_url: str
    s3_timeout_seconds: float
    maximum_object_bytes: int
    environment: str


def record_key(record: Mapping[str, Any]) -> str:
    """Return the validated, path-mirrored key of one media record.

    The key comes from the record, never from the request.  A record whose key is
    absent or unsafe is a projection defect and fails closed.
    """

    key = record.get("record_key")
    if not isinstance(key, str) or not key:
        raise MediaRecordInvalid("media record has no key")
    if not key.startswith(RECORD_KEY_PREFIX):
        raise MediaRecordInvalid("media record key is outside the projection namespace")
    if "\\" in key or "\x00" in key or key.startswith("/"):
        raise MediaRecordInvalid("media record key is unsafe")
    segments = key.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise MediaRecordInvalid("media record key is unsafe")
    return key


def record_checksum(record: Mapping[str, Any]) -> str:
    provenance = record.get("provenance")
    checksum = provenance.get("checksum") if isinstance(provenance, Mapping) else None
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise MediaRecordInvalid("media record has no usable checksum")
    return checksum.lower()


def record_filename(record: Mapping[str, Any]) -> str:
    """Return the exact basename used by the pinned ``Content-Disposition`` header."""

    return record_key(record).rsplit("/", 1)[-1]


def record_relative_path(record: Mapping[str, Any]) -> str:
    """Return the record's path inside a local media root."""

    return record_key(record)[len(RECORD_KEY_PREFIX) :]


def object_key(record: Mapping[str, Any], *, prefix: str) -> str:
    """Return the object-store key: ``<prefix>/<record_key>``, path mirrored."""

    key = record_key(record)
    normalized = prefix.strip("/")
    return f"{normalized}/{key}" if normalized else key


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@lru_cache(maxsize=1)
def media_records() -> tuple[dict[str, Any], ...]:
    """Return the checked media records without loading the whole public projection.

    The operator tooling and the offline fixture store both need the record set in
    contexts where the database-adapted projection is unavailable or unnecessary.  The
    artifact digest recorded in ``manifest.json`` is verified first so the tooling can
    never act on an edited ``media.json``.
    """

    manifest_path = PROJECTION_ROOT / "manifest.json"
    records_path = PROJECTION_ROOT / MEDIA_RECORDS_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = records_path.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Public projection media records cannot be read.") from exc
    expected = manifest.get("artifacts", {}).get(MEDIA_RECORDS_FILENAME)
    if _sha256_hex(payload) != expected:
        raise ImproperlyConfigured("Public projection media record digest mismatch.")
    try:
        records = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Public projection media records are not valid JSON.") from exc
    if not isinstance(records, list):
        raise ImproperlyConfigured("Public projection media records are not a list.")
    return tuple(records)


class MediaStore:
    """Read-only view of the published projection objects."""

    #: Short backend token used in redacted structured log events.
    name = "media-store"

    def expected_checksum(self, record: Mapping[str, Any]) -> str:
        """Return the sha256 the retrieved bytes must have."""

        return record_checksum(record)

    def fetch(self, record: Mapping[str, Any]) -> MediaObject:
        raise NotImplementedError

    def stat(self, record: Mapping[str, Any]) -> MediaObjectStat:
        """Return size/checksum metadata, downloading only when required."""

        payload = self.fetch(record).payload
        return MediaObjectStat(size=len(payload), checksum=_sha256_hex(payload))

    def existing_keys(self) -> tuple[str, ...]:
        """Return every key the store currently holds below its namespace."""

        return ()


def read_media_object(store: MediaStore, record: Mapping[str, Any]) -> bytes:
    """Fetch one object and return it only when its checksum matches the record."""

    expected = store.expected_checksum(record)
    obtained = store.fetch(record)
    computed = _sha256_hex(obtained.payload)
    if computed != expected:
        raise MediaChecksumMismatch("retrieved object digest does not match the record")
    if obtained.attested_checksum is not None and obtained.attested_checksum != computed:
        raise MediaChecksumMismatch("store attested a different digest than it returned")
    return obtained.payload


class LocalMediaStore(MediaStore):
    """Serve objects from a filesystem root, mirroring the historic projection tree."""

    name = "local"

    def __init__(self, *, root: Path, maximum_object_bytes: int) -> None:
        self._root = root
        self._maximum = maximum_object_bytes

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, record: Mapping[str, Any]) -> Path:
        return self._root / record_relative_path(record)

    def fetch(self, record: Mapping[str, Any]) -> MediaObject:
        path = self.path_for(record)
        try:
            if path.is_symlink():
                raise MediaStoreUnavailable("local media object is a symlink")
            if not path.is_file():
                raise MediaObjectMissing("local media object is absent")
            size = path.stat().st_size
        except OSError as exc:
            raise MediaStoreUnavailable("local media object cannot be inspected") from exc
        if size > self._maximum:
            raise MediaObjectTooLarge("local media object exceeds the configured bound")
        try:
            with path.open("rb") as handle:
                payload = handle.read(self._maximum + 1)
        except OSError as exc:
            raise MediaStoreUnavailable("local media object cannot be read") from exc
        if len(payload) > self._maximum:
            raise MediaObjectTooLarge("local media object exceeds the configured bound")
        return MediaObject(payload=payload)

    def stat(self, record: Mapping[str, Any]) -> MediaObjectStat:
        payload = self.fetch(record).payload
        return MediaObjectStat(size=len(payload), checksum=_sha256_hex(payload))

    def existing_keys(self) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        found = []
        for path in self._root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(self._root).as_posix()
            found.append(f"{RECORD_KEY_PREFIX}{relative}")
        return tuple(sorted(found))


def _png_fixture(comment: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return (
            len(data).to_bytes(4, "big")
            + payload
            + (zlib.crc32(payload) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"tEXt", b"Comment\x00" + comment)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00", 9))
        + chunk(b"IEND", b"")
    )


# A 1x1 baseline JFIF image.  The comment segment is inserted immediately after the
# mandatory APP0 segment so the JFIF marker keeps its required position.
_BASE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)
_JPEG_COMMENT_OFFSET = 20
# A 1x1 GIF89a image split at the end of its global colour table, where a comment
# extension is a legal block.
_GIF_HEADER = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
_GIF_BODY = b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


def _jpeg_fixture(comment: bytes) -> bytes:
    segment = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
    return _BASE_JPEG[:_JPEG_COMMENT_OFFSET] + segment + _BASE_JPEG[_JPEG_COMMENT_OFFSET:]


def _gif_fixture(comment: bytes) -> bytes:
    blocks = b"".join(
        bytes([len(comment[index : index + 255])]) + comment[index : index + 255]
        for index in range(0, len(comment), 255)
    )
    return _GIF_HEADER + b"\x21\xfe" + blocks + b"\x00" + _GIF_BODY


def _svg_fixture(comment: str) -> bytes:
    escaped = comment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1">'
        f'<title>{escaped}</title><rect width="1" height="1" fill="#f2f2f2"/></svg>\n'
    ).encode()


def deterministic_fixture(record: Mapping[str, Any]) -> bytes:
    """Build one minimal, valid, record-specific image for the offline store."""

    key = record_key(record)
    comment = key.encode("utf-8")[:200]
    content_type = str(record.get("content_type", "")).split(";", 1)[0].strip().lower()
    if content_type == "image/png":
        return _png_fixture(comment)
    if content_type == "image/gif":
        return _gif_fixture(comment)
    if content_type == "image/svg+xml":
        return _svg_fixture(key)
    if content_type == "image/jpeg":
        return _jpeg_fixture(comment)
    raise MediaRecordInvalid("media record declares an unsupported content type")


class MemoryMediaStore(MediaStore):
    """Deterministic offline fixture store derived from ``media.json``.

    Each record is served a minimal valid image of its recorded content type, and the
    digest of that image is the checksum the read path verifies against.  The store
    therefore exercises the real verification code path without a hydrated tree.
    """

    name = "memory"

    def __init__(self, *, maximum_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES) -> None:
        self._maximum = maximum_object_bytes
        self._objects: dict[str, bytes] = {}

    def _payload(self, record: Mapping[str, Any]) -> bytes:
        key = record_key(record)
        cached = self._objects.get(key)
        if cached is None:
            cached = deterministic_fixture(record)
            self._objects[key] = cached
        return cached

    def expected_checksum(self, record: Mapping[str, Any]) -> str:
        return _sha256_hex(self._payload(record))

    def fetch(self, record: Mapping[str, Any]) -> MediaObject:
        payload = self._payload(record)
        if len(payload) > self._maximum:
            raise MediaObjectTooLarge("fixture object exceeds the configured bound")
        return MediaObject(payload=payload)

    def existing_keys(self) -> tuple[str, ...]:
        return tuple(sorted(record_key(record) for record in media_records()))


def _decoded_attested_checksum(value: Any) -> str | None:
    """Convert an S3 ``ChecksumSHA256`` header (base64) to lower-case hex."""

    if not isinstance(value, str) or not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 32:
        return None
    return raw.hex()


class S3MediaStore(MediaStore):
    """Read published objects from the release asset bucket.

    The client is built from the ambient role/credential chain.  This module owns no
    credential handling and never renders the bucket, key, or endpoint into an error.
    """

    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str,
        endpoint_url: str,
        timeout_seconds: float,
        maximum_object_bytes: int,
    ) -> None:
        if not bucket:
            raise ImproperlyConfigured("PUBLIC_MEDIA_S3_BUCKET is required for the s3 backend.")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._region = region
        self._endpoint_url = endpoint_url
        self._timeout = timeout_seconds
        self._maximum = maximum_object_bytes
        self._cached_client: Any = None

    @property
    def prefix(self) -> str:
        return self._prefix

    def client(self) -> Any:
        if self._cached_client is None:
            # boto3 is an existing pinned dependency and ships no type information.
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]

            self._cached_client = boto3.client(
                "s3",
                region_name=self._region or None,
                endpoint_url=self._endpoint_url or None,
                config=Config(
                    connect_timeout=self._timeout,
                    read_timeout=self._timeout,
                    # One initial attempt plus at most one bounded retry.
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._cached_client

    def key_for(self, record: Mapping[str, Any]) -> str:
        return object_key(record, prefix=self._prefix)

    @staticmethod
    def _is_absent(error: Exception) -> bool:
        response = getattr(error, "response", None)
        if not isinstance(response, Mapping):
            return False
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    def fetch(self, record: Mapping[str, Any]) -> MediaObject:
        key = self.key_for(record)
        try:
            response = self.client().get_object(
                Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
        except MediaStoreError:
            raise
        except Exception as exc:  # boto3/botocore raise a wide family of errors.
            if self._is_absent(exc):
                raise MediaObjectMissing("object is absent from the store") from None
            raise MediaStoreUnavailable("object store request failed") from None
        declared = response.get("ContentLength")
        if isinstance(declared, int) and declared > self._maximum:
            self._close(response)
            raise MediaObjectTooLarge("stored object exceeds the configured bound")
        body = response.get("Body")
        if body is None:
            raise MediaStoreUnavailable("object store returned no body")
        try:
            payload = body.read(self._maximum + 1)
        except Exception:
            raise MediaStoreUnavailable("object store body could not be read") from None
        finally:
            self._close(response)
        if not isinstance(payload, bytes):
            raise MediaStoreUnavailable("object store returned an unusable body")
        if len(payload) > self._maximum:
            raise MediaObjectTooLarge("stored object exceeds the configured bound")
        return MediaObject(
            payload=payload,
            attested_checksum=_decoded_attested_checksum(response.get("ChecksumSHA256")),
        )

    def stat(self, record: Mapping[str, Any]) -> MediaObjectStat:
        key = self.key_for(record)
        try:
            response = self.client().head_object(
                Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
        except Exception as exc:
            if self._is_absent(exc):
                raise MediaObjectMissing("object is absent from the store") from None
            raise MediaStoreUnavailable("object store request failed") from None
        checksum = _decoded_attested_checksum(response.get("ChecksumSHA256"))
        size = response.get("ContentLength")
        if checksum is None:
            obtained = self.fetch(record)
            return MediaObjectStat(
                size=len(obtained.payload), checksum=_sha256_hex(obtained.payload)
            )
        return MediaObjectStat(size=int(size or 0), checksum=checksum)

    def existing_keys(self) -> tuple[str, ...]:
        found: list[str] = []
        for key in self._iterate_keys():
            found.append(key)
        return tuple(sorted(found))

    def listing_prefix(self) -> str:
        """Return the key prefix that bounds a listing to projection media.

        Scoped to ``RECORD_KEY_PREFIX`` rather than to the configured prefix alone.
        With an empty configured prefix the objects live at the bucket root, where
        they share the namespace with unrelated sections such as ``site-assets/``;
        listing the whole bucket would report every one of those as an orphan.
        """

        return f"{self._prefix}/{RECORD_KEY_PREFIX}" if self._prefix else RECORD_KEY_PREFIX

    def _iterate_keys(self) -> Iterator[str]:
        marker: str | None = None
        request: dict[str, Any] = {"Bucket": self._bucket, "Prefix": self.listing_prefix()}
        while True:
            page_request = dict(request)
            if marker:
                page_request["ContinuationToken"] = marker
            try:
                page = self.client().list_objects_v2(**page_request)
            except Exception:
                raise MediaStoreUnavailable("object store listing failed") from None
            for entry in page.get("Contents", ()) or ():
                key = entry.get("Key")
                if isinstance(key, str):
                    yield key
            if not page.get("IsTruncated"):
                return
            marker = page.get("NextContinuationToken")
            if not marker:
                return

    def put(self, record: Mapping[str, Any], payload: bytes) -> None:
        try:
            self.client().put_object(
                Bucket=self._bucket,
                Key=self.key_for(record),
                Body=payload,
                ContentType=str(record["content_type"]),
                ChecksumAlgorithm="SHA256",
            )
        except Exception:
            raise MediaStoreUnavailable("object store upload failed") from None

    @staticmethod
    def _close(response: Mapping[str, Any]) -> None:
        body = response.get("Body")
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _configured_path(value: Any, default: Path) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        return Path(value)
    return default


def media_store_config() -> MediaStoreConfig:
    """Resolve the media store from the runtime registry, not the environment.

    ``memory`` is not a registered setting value: it is an offline test fixture
    selected by a settings override, and ``_build_media_store`` refuses it in a
    deployed environment.  So the registry answers for every real backend and
    the settings attribute is still consulted for the fixture.
    """

    settings_backend = str(getattr(settings, "PUBLIC_MEDIA_STORE_BACKEND", "")).strip().lower()
    if settings_backend and settings_backend not in SUPPORTED_BACKENDS:
        # A process booted with a backend nobody implements is a configuration
        # error, and the registry would quietly fall past it to the default.
        # Refuse it here so the misconfiguration is still visible.
        raise ImproperlyConfigured(
            "PUBLIC_MEDIA_STORE_BACKEND must be one of: " + ", ".join(SUPPORTED_BACKENDS)
        )
    backend = (
        settings_backend
        if settings_backend == "memory"
        else get_str_setting("public_media.store_backend").strip().lower()
    )
    if backend not in SUPPORTED_BACKENDS:
        raise ImproperlyConfigured(
            "PUBLIC_MEDIA_STORE_BACKEND must be one of: " + ", ".join(SUPPORTED_BACKENDS)
        )
    return MediaStoreConfig(
        backend=backend,
        local_root=_configured_path(
            getattr(settings, "PUBLIC_MEDIA_LOCAL_ROOT", None), PROJECTION_ROOT / "media"
        ),
        s3_bucket=get_str_setting("public_media.s3_bucket").strip(),
        s3_prefix=get_str_setting("public_media.s3_prefix").strip("/"),
        s3_region=get_str_setting("public_media.s3_region").strip(),
        s3_endpoint_url=get_str_setting("public_media.s3_endpoint_url"),
        # The registry types this as whole seconds; the store wants a float.
        s3_timeout_seconds=float(get_int_setting("public_media.s3_timeout_seconds")),
        maximum_object_bytes=get_int_setting("public_media.max_object_bytes"),
        environment=str(getattr(settings, "ENVIRONMENT", "")).strip().lower(),
    )


@lru_cache(maxsize=4)
def _build_media_store(config: MediaStoreConfig) -> MediaStore:
    if config.backend == "memory":
        if config.environment in DEPLOYED_ENVIRONMENTS:
            raise ImproperlyConfigured(
                "The offline media fixture store cannot serve a deployed environment."
            )
        return MemoryMediaStore(maximum_object_bytes=config.maximum_object_bytes)
    if config.backend == "s3":
        return S3MediaStore(
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            region=config.s3_region,
            endpoint_url=config.s3_endpoint_url,
            timeout_seconds=config.s3_timeout_seconds,
            maximum_object_bytes=config.maximum_object_bytes,
        )
    return LocalMediaStore(root=config.local_root, maximum_object_bytes=config.maximum_object_bytes)


def media_store() -> MediaStore:
    """Return the configured store.

    The instance is cached per resolved configuration, so an ``override_settings``
    block observes its own store without any explicit cache invalidation.
    """

    return _build_media_store(media_store_config())


def local_media_root() -> Path:
    return media_store_config().local_root
