from __future__ import annotations

import argparse
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

QUALITY_CONTRACT_VERSION = 1
QUALITY_TARGETS = (
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


def inspect_makefile(text: str) -> QualityContract:
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

    missing = tuple(target for target in QUALITY_TARGETS if target not in definitions)
    if missing:
        raise QualityContractError(
            "selected release cannot satisfy quality-contract-v1; missing targets: "
            + ", ".join(missing)
        )

    aggregate_present = AGGREGATE_TARGET in definitions
    if aggregate_present and definitions[AGGREGATE_TARGET] != QUALITY_TARGETS:
        raise QualityContractError(
            "selected release verification-quality target does not match quality-contract-v1"
        )
    return QualityContract(
        aggregate_present=aggregate_present,
        targets=QUALITY_TARGETS,
        version=QUALITY_CONTRACT_VERSION,
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
    return root, inspect_makefile(text)


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
