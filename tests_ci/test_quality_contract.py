from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ci.quality_contract import (
    AGGREGATE_TARGET,
    QUALITY_TARGETS,
    QualityContractError,
    inspect_makefile,
    load_contract,
    run_quality_contract,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_MAKEFILE = ROOT / "tests_ci" / "fixtures" / "a220728-Makefile"
HISTORICAL_MAKEFILE_SHA256 = "378a3234f9d2fab30a7d5f6e6f6e9235103ca12f24ea6b9940a57e07176b8285"


def _target_definitions(*, include_aggregate: bool = True) -> str:
    lines: list[str] = []
    for target in QUALITY_TARGETS:
        lines.extend(
            (
                f"{target}:",
                '\t@test -z "$$VIRTUAL_ENV"',
                f"\t@printf '%s\\n' {target} >> ran-targets",
            )
        )
    if include_aggregate:
        lines.extend(
            (
                f"{AGGREGATE_TARGET}: {' '.join(QUALITY_TARGETS)}",
                "\t@touch forbidden-aggregate-recipe",
            )
        )
    return "\n".join(lines) + "\n"


def test_exact_a220728_historical_makefile_uses_the_legacy_compatible_contract() -> None:
    body = HISTORICAL_MAKEFILE.read_bytes()

    assert hashlib.sha256(body).hexdigest() == HISTORICAL_MAKEFILE_SHA256
    contract = inspect_makefile(body.decode("utf-8"))
    assert contract.version == 1
    assert contract.targets == QUALITY_TARGETS
    assert not contract.aggregate_present
    assert "verification-quality:" not in body.decode("utf-8")


def test_current_aggregate_dependencies_cannot_drift_from_the_versioned_contract() -> None:
    contract = inspect_makefile((ROOT / "Makefile").read_text(encoding="utf-8"))

    assert contract.aggregate_present
    assert contract.targets == QUALITY_TARGETS


def test_target_present_executes_only_explicit_contract_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Makefile").write_text(_target_definitions(), encoding="utf-8")
    monkeypatch.setenv("VIRTUAL_ENV", "/controller/environment")

    assert run_quality_contract(tmp_path) == 0
    assert not (tmp_path / "forbidden-aggregate-recipe").exists()
    assert (tmp_path / "ran-targets").read_text(encoding="utf-8").splitlines() == list(
        QUALITY_TARGETS
    )


@pytest.mark.parametrize(
    "body, message",
    [
        (
            _target_definitions(include_aggregate=False).replace("test-ci:\n", ""),
            "missing targets: test-ci",
        ),
        (
            _target_definitions().replace(
                f"{AGGREGATE_TARGET}: {' '.join(QUALITY_TARGETS)}",
                f"{AGGREGATE_TARGET}: lint",
            ),
            "does not match quality-contract-v1",
        ),
        (
            _target_definitions(include_aggregate=False) + "lint:\n\t@true\n",
            "redefines target lint",
        ),
    ],
    ids=("missing-primitive", "invalid-aggregate", "duplicate-primitive"),
)
def test_invalid_selected_source_tooling_fails_closed(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    (tmp_path / "Makefile").write_text(body, encoding="utf-8")

    with pytest.raises(QualityContractError, match=message):
        load_contract(tmp_path)
    assert not (tmp_path / "ran-targets").exists()
    assert not (tmp_path / "forbidden-aggregate-recipe").exists()


def test_symlinked_selected_source_makefile_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real.mk"
    target.write_text(_target_definitions(), encoding="utf-8")
    (tmp_path / "Makefile").symlink_to(target)

    with pytest.raises(QualityContractError, match="non-symlink"):
        load_contract(tmp_path)
