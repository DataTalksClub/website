"""Canonical Django runtime release identity."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from deploy.release_identity import IdentityError, validate_schema2_version

LOCAL_VERSION = "local-development-build-version-not-configured"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class RuntimeIdentity:
    version: str
    source_sha: str | None
    image_digest: str | None

    def payload(self, *, status: str = "ok") -> dict[str, str | None]:
        return {
            "status": status,
            "version": self.version,
            "source_sha": self.source_sha,
            "image_digest": self.image_digest,
        }


def read_runtime_identity(environment: Mapping[str, str] | None = None) -> RuntimeIdentity:
    values = os.environ if environment is None else environment
    version = values.get("VERSION", LOCAL_VERSION)
    source_sha = values.get("SOURCE_SHA") or None
    image_digest = values.get("IMAGE_DIGEST") or None
    deployed = values.get("DTC_ENVIRONMENT") in {"development", "production"}

    if "APP_VERSION" in values:
        raise RuntimeError("APP_VERSION must not be configured as a runtime identity")
    if version == LOCAL_VERSION and source_sha is None and image_digest is None:
        if deployed:
            raise RuntimeError("deployed runtime release identity is not configured")
        return RuntimeIdentity(version, None, None)
    if source_sha is None or SOURCE_SHA_PATTERN.fullmatch(source_sha) is None:
        raise RuntimeError("SOURCE_SHA is not a full lowercase source SHA")
    if image_digest is None or IMAGE_DIGEST_PATTERN.fullmatch(image_digest) is None:
        raise RuntimeError("IMAGE_DIGEST is not an immutable sha256 digest")
    try:
        validate_schema2_version(version, source_sha)
    except IdentityError as error:
        raise RuntimeError("VERSION is not a sealed release version") from error
    return RuntimeIdentity(version, source_sha, image_digest)
