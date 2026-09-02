from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

QUALITY_CONTRACT_VERSION = 2
QUALITY_TARGETS = (
    "database-portability-check",
    "security-check",
    "lint",
    "format-check",
    "typecheck",
    "migrations-check",
    "django-check",
    "deployment-check",
    "test-ci",
)
LEGACY_QUALITY_CONTRACT_VERSION = 0
# The pre-#141 fixture below predates `security-check` and still carries the
# retired `terminology-check` gate.  Its target list is written out rather than
# derived, so retiring a current target cannot silently rewrite what release
# archaeology runs against that frozen source.
LEGACY_QUALITY_TARGETS = (
    "terminology-check",
    "database-portability-check",
    "lint",
    "format-check",
    "typecheck",
    "migrations-check",
    "django-check",
    "deployment-check",
    "test-ci",
)
# One immutable pre-#141 fixture remains runnable for release archaeology.  It
# is never inferred from a missing target: only this exact historical source
# may use the legacy contract.
LEGACY_HISTORICAL_MAKEFILE_SHA256 = (
    "378a3234f9d2fab30a7d5f6e6f6e9235103ca12f24ea6b9940a57e07176b8285"
)
AGGREGATE_TARGET = "verification-quality"
MAX_MAKEFILE_BYTES = 256 * 1024
TARGET_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
TARGET_HEADER_RE = re.compile(rf"^(?P<targets>{TARGET_NAME}(?:[ \t]+{TARGET_NAME})*)[ \t]*:(?!=)")


class QualityContractError(ValueError):
    """The selected release cannot satisfy the versioned quality contract safely."""


@dataclass(frozen=True, slots=True)
class QualityContract:
    aggregate_present: bool
    targets: tuple[str, ...]
    version: int


def _logical_lines(text: str) -> tuple[str, ...]:
    logical: list[str] = []
    pending = ""
    for physical in text.splitlines():
        line = pending + physical.lstrip() if pending else physical
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending = line[:-1] + " "
            continue
        logical.append(line)
        pending = ""
    if pending:
        raise QualityContractError("selected release Makefile has an unfinished continuation")
    return tuple(logical)


def inspect_makefile(text: str, *, allow_legacy: bool = False) -> QualityContract:
    if "\x00" in text:
        raise QualityContractError("selected release Makefile contains a NUL byte")
    definitions: dict[str, tuple[str, ...]] = {}
    for line in _logical_lines(text):
        match = TARGET_HEADER_RE.match(line)
        if match is None:
            continue
        names = tuple(match.group("targets").split())
        prerequisites = tuple(line[match.end() :].split())
        for name in names:
            if name in definitions:
                raise QualityContractError(f"selected release Makefile redefines target {name}")
            definitions[name] = prerequisites

    legacy = allow_legacy and hashlib.sha256(text.encode("utf-8")).hexdigest() == (
        LEGACY_HISTORICAL_MAKEFILE_SHA256
    )
    expected_targets = LEGACY_QUALITY_TARGETS if legacy else QUALITY_TARGETS
    expected_version = LEGACY_QUALITY_CONTRACT_VERSION if legacy else QUALITY_CONTRACT_VERSION
    missing = tuple(target for target in expected_targets if target not in definitions)
    if missing:
        raise QualityContractError(
            f"selected release cannot satisfy quality-contract-v{expected_version}; "
            "missing targets: " + ", ".join(missing)
        )

    aggregate_present = AGGREGATE_TARGET in definitions
    if aggregate_present and definitions[AGGREGATE_TARGET] != expected_targets:
        raise QualityContractError(
            "selected release verification-quality target does not match "
            f"quality-contract-v{expected_version}"
        )
    return QualityContract(
        aggregate_present=aggregate_present,
        targets=expected_targets,
        version=LEGACY_QUALITY_CONTRACT_VERSION if legacy else QUALITY_CONTRACT_VERSION,
    )


def load_contract(repository: str | Path) -> tuple[Path, QualityContract]:
    root = Path(repository).resolve(strict=True)
    if not root.is_dir():
        raise QualityContractError("selected release repository is not a directory")
    makefile = root / "Makefile"
    if makefile.is_symlink() or not makefile.is_file():
        raise QualityContractError("selected release Makefile must be a regular non-symlink file")
    if makefile.stat().st_size > MAX_MAKEFILE_BYTES:
        raise QualityContractError("selected release Makefile is too large")
    try:
        text = makefile.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise QualityContractError("selected release Makefile is not UTF-8") from error
    return root, inspect_makefile(text, allow_legacy=True)


def run_quality_contract(
    repository: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    root, contract = load_contract(repository)
    environment = os.environ.copy()
    for name in ("GNUMAKEFLAGS", "MAKEFLAGS", "MAKEFILES", "MFLAGS", "VIRTUAL_ENV"):
        environment.pop(name, None)
    print(
        f"quality-contract-v{contract.version}: selected-source targets: "
        + " ".join(contract.targets),
        flush=True,
    )
    result = runner(
        ["make", "--no-print-directory", *contract.targets],
        cwd=root,
        env=environment,
        check=False,
        text=True,
    )
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run_quality_contract(args.repository)
    except (OSError, QualityContractError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
