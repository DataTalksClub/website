from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_course_platform_schema_inventory import (
    AUDIT_PATH,
    ValidationError,
    validate,
    validate_text,
)


def _source() -> str:
    return Path(AUDIT_PATH).read_text(encoding="utf-8")


def test_schema_inventory_has_exact_static_baseline() -> None:
    validate()


def test_validator_rejects_main_sha_drift() -> None:
    mutated = _source().replace(
        "4cc6ad41caba14da7fad10ea077e1ee10389addd",
        "a" * 40,
        1,
    )

    with pytest.raises(ValidationError, match="Snapshot SHA"):
        validate_text(mutated)


def test_validator_rejects_duplicate_model_key() -> None:
    source = _source()
    first = "| accounts.CustomUser | accounts | CustomUser"
    replacement = "| accounts.Token | accounts | CustomUser"

    with pytest.raises(ValidationError, match="duplicate keys"):
        validate_text(source.replace(first, replacement, 1))


def test_validator_rejects_unknown_model_key() -> None:
    source = _source()
    with pytest.raises(ValidationError, match="unknown or missing model keys"):
        validate_text(source.replace("| data.DatamailerSendAudit |", "| data.UnknownModel |", 1))


def test_validator_rejects_migration_count_drift() -> None:
    source = _source().replace(
        "Current migrations: `accounts=12; courses=41; data=5`",
        "Current migrations: `accounts=12; courses=40; data=5`",
        1,
    )

    with pytest.raises(ValidationError, match="Current migrations"):
        validate_text(source)


def test_validator_rejects_controlled_vocabulary_drift() -> None:
    source = _source().replace(
        "| courses.Course | courses | Course | courses_course | courses/models/course.py | "
        "courses/migrations/0001_initial.py | pinned-cmp | #30 | definition |",
        "| courses.Course | courses | Course | courses_course | courses/models/course.py | "
        "courses/migrations/0001_initial.py | pinned-cmp | #30 | unknown |",
        1,
    )

    with pytest.raises(ValidationError, match="invalid model classification"):
        validate_text(source)


def test_validator_rejects_missing_evidence_link() -> None:
    source = _source().replace(
        "[declaration](../../accounts/models.py); "
        "[migration](../../accounts/migrations/0001_initial.py)",
        "",
        1,
    )

    with pytest.raises(ValidationError, match="evidence cell needs"):
        validate_text(source)


def test_validator_rejects_omitted_unresolved_handoff() -> None:
    source = _source().replace(
        "| [#23](https://github.com/DataTalksClub/website/issues/23) | OPEN |",
        "| [#24](https://github.com/DataTalksClub/website/issues/24) | OPEN |",
        1,
    )

    with pytest.raises(ValidationError, match="unknown or duplicate unresolved hand-off"):
        validate_text(source)
