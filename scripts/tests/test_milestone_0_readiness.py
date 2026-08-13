from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_milestone_0_readiness import (
    AUDIT_PATH,
    ValidationError,
    validate,
    validate_text,
)


def test_milestone_0_matrix_has_exact_live_snapshot_contract() -> None:
    rows = validate()

    assert len(rows) == 20
    assert {row.issue_number for row in rows} == set(range(12, 30)) | {30, 34}


def test_validator_rejects_unknown_status() -> None:
    source = Path(AUDIT_PATH).read_text(encoding="utf-8")
    mutated = source.replace("| partial |", "| unknown |", 1)

    with pytest.raises(ValidationError, match="invalid status"):
        validate_text(mutated)


def test_validator_rejects_duplicate_issue_numbers() -> None:
    source = Path(AUDIT_PATH).read_text(encoding="utf-8")
    mutated = source.replace(
        "| [#34](https://github.com/DataTalksClub/website/issues/34) |",
        "| [#30](https://github.com/DataTalksClub/website/issues/30) |",
        1,
    )

    with pytest.raises(ValidationError, match="duplicate issue numbers"):
        validate_text(mutated)


def test_validator_rejects_title_that_does_not_match_live_issue_snapshot() -> None:
    source = Path(AUDIT_PATH).read_text(encoding="utf-8")
    mutated = source.replace(
        "| Decision: Confirm MVP GitHub-backed editorial workflow |",
        "| Confirm MVP GitHub-backed editorial workflow |",
        1,
    )

    with pytest.raises(ValidationError, match="title does not match"):
        validate_text(mutated)
