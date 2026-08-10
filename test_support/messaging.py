from __future__ import annotations

import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

_SYNTHETIC_RECIPIENT = re.compile(
    r"^[a-z0-9][a-z0-9.+_-]{0,63}@(example\.invalid|[a-z0-9-]+\.example\.invalid)$"
)
_EMAIL_BYTES_RE = re.compile(
    rb"(?i)(?<![a-z0-9.!#$%&'*+/=?^_`{|}~-])"
    rb"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    rb"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\."
    rb"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
    rb"(?![a-z0-9-])"
)
DEFAULT_ARTIFACT_CANARIES = (
    "person@example.com",
    "synthetic-token-canary",
    "credential-canary",
    "private-profile-canary",
    "registration-canary",
    "attendee-canary",
    "provider-payload-canary",
)
ARTIFACT_CANARIES_ENV = "DTC_TEST_ARTIFACT_CANARIES_JSON"


class CaptureSafetyError(ValueError):
    pass


def artifact_canaries() -> tuple[str, ...]:
    """Return code-owned canaries plus bounded run-specific protected values."""

    serialized = os.environ.get(ARTIFACT_CANARIES_ENV, "")
    if not serialized:
        return DEFAULT_ARTIFACT_CANARIES
    try:
        supplied = json.loads(serialized)
    except ValueError as error:
        raise CaptureSafetyError("artifact canary configuration is malformed") from error
    if (
        not isinstance(supplied, list)
        or len(supplied) > 64
        or any(not isinstance(value, str) or not value or len(value) > 512 for value in supplied)
    ):
        raise CaptureSafetyError("artifact canary configuration is malformed")
    return tuple(dict.fromkeys((*DEFAULT_ARTIFACT_CANARIES, *supplied)))


class SimulatedOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    SUPPRESSED = "suppressed"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    AMBIGUOUS = "ambiguous"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class CapturedMessage:
    purpose: str
    recipient: str
    subject: str
    body: str = field(repr=False)
    outcome: SimulatedOutcome

    def redacted_metadata(self) -> dict[str, str]:
        return {
            "outcome": self.outcome.value,
            "purpose": self.purpose,
            "recipient": "[REDACTED]",
            "subject": self.subject,
        }


class CaptureMailbox:
    """A provider-neutral dry-run mailbox that cannot construct a live client."""

    def __init__(self) -> None:
        self.messages: list[CapturedMessage] = []

    def send(
        self,
        *,
        purpose: str,
        recipient: str,
        subject: str,
        body: str,
        outcome: SimulatedOutcome = SimulatedOutcome.ACCEPTED,
    ) -> CapturedMessage:
        normalized = recipient.strip().casefold()
        if not _SYNTHETIC_RECIPIENT.fullmatch(normalized):
            raise CaptureSafetyError("capture mailbox accepts reserved synthetic recipients only")
        message = CapturedMessage(
            purpose=purpose,
            recipient="[REDACTED]",
            subject=subject,
            body=body,
            outcome=outcome,
        )
        self.messages.append(message)
        return message


def scan_artifacts(root: Path, *, canaries: tuple[str, ...]) -> tuple[Path, ...]:
    """Fail closed before artifact publication if any protected canary escaped."""

    boundary = root.resolve(strict=True)
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except OSError as error:
                raise CaptureSafetyError("artifact tree contains a broken symlink") from error
            if not target.is_relative_to(boundary):
                raise CaptureSafetyError("artifact scan crossed its ownership boundary")
            # Pytest creates internal convenience links below its owned basetemp.
            # Their targets are scanned through their canonical paths.
            continue
        if not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(boundary):
            raise CaptureSafetyError("artifact scan crossed its ownership boundary")
        if _artifact_contains_canary(path, canaries=canaries):
            found.append(path.relative_to(root))
    if found:
        relative_paths = ", ".join(path.as_posix() for path in found[:8])
        raise CaptureSafetyError(
            f"protected canary found in artifact publication set: {relative_paths}"
        )
    return tuple(found)


def owned_publication_roots(repository: Path, runtime_artifacts: Path) -> tuple[Path, ...]:
    """Return every existing process-owned artifact or screenshot handoff root."""

    boundary = repository.resolve(strict=True)
    roots = [runtime_artifacts]
    screenshots = repository / ".tmp" / "screenshots"
    if screenshots.exists():
        if screenshots.is_symlink() or not screenshots.is_dir():
            raise CaptureSafetyError("screenshot handoff root is unsafe")
        resolved = screenshots.resolve(strict=True)
        if not resolved.is_relative_to(boundary):
            raise CaptureSafetyError("screenshot handoff root crossed the repository boundary")
        roots.append(screenshots)
    return tuple(roots)


def redact_trace_emails(path: Path) -> None:
    """Remove email-shaped values from a Playwright trace before it can be published."""

    if not zipfile.is_zipfile(path):
        raise CaptureSafetyError("Playwright trace is not a safe archive")
    replacement = path.with_name(f".{path.name}.redacting")
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
            for member in source.infolist():
                member_path = Path(member.filename)
                mode = member.external_attr >> 16
                if member_path.is_absolute() or ".." in member_path.parts or stat.S_ISLNK(mode):
                    raise CaptureSafetyError("artifact archive contains an unsafe member")
                payload = b"" if member.is_dir() else source.read(member)
                target.writestr(member, _EMAIL_BYTES_RE.sub(b"[REDACTED]", payload))
        os.replace(replacement, path)
    except (OSError, zipfile.BadZipFile) as error:
        raise CaptureSafetyError("Playwright trace could not be redacted") from error
    finally:
        replacement.unlink(missing_ok=True)


def _artifact_contains_canary(path: Path, *, canaries: tuple[str, ...]) -> bool:
    encoded = tuple(canary.encode("utf-8") for canary in canaries if canary)
    payload = path.read_bytes()
    if _contains_protected_value(payload, encoded=encoded):
        return True
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                member_path = Path(member.filename)
                mode = member.external_attr >> 16
                if member_path.is_absolute() or ".." in member_path.parts or stat.S_ISLNK(mode):
                    raise CaptureSafetyError("artifact archive contains an unsafe member")
                if not member.is_dir():
                    payload = archive.read(member)
                    if _contains_protected_value(payload, encoded=encoded):
                        return True
    except (OSError, zipfile.BadZipFile) as error:
        raise CaptureSafetyError("artifact archive could not be scanned") from error
    return False


def _contains_protected_value(payload: bytes, *, encoded: tuple[bytes, ...]) -> bool:
    if any(canary in payload for canary in encoded):
        return True
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return _EMAIL_BYTES_RE.search(payload) is not None
