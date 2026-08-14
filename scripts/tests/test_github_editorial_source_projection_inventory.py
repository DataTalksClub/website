from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_github_editorial_source_projection_inventory import (
    ARTIFACTS,
    AUDIT_PATH,
    MANIFEST_MEMBER_TEXT,
    SOURCE_PINS,
    ValidationError,
    validate,
    validate_text,
)


def _audit_text() -> str:
    return Path(AUDIT_PATH).read_text(encoding="utf-8")


def _replace_once(source: str, old: str, new: str) -> str:
    assert old in source, f"test fixture no longer contains {old!r}"
    return source.replace(old, new, 1)


def test_inventory_matches_the_exact_checked_in_snapshot() -> None:
    validate()


def test_validator_rejects_snapshot_sha_drift() -> None:
    source = _audit_text()
    mutated = _replace_once(
        source,
        "539bd8c6ff73661e174af7183f6f49d181efa1fa",
        "0" * 40,
    )

    with pytest.raises(ValidationError, match="snapshot SHA"):
        validate_text(mutated)


@pytest.mark.parametrize("source_key", sorted(SOURCE_PINS))
def test_validator_rejects_each_selected_source_pin_drift(source_key: str) -> None:
    source = _audit_text()
    revision = SOURCE_PINS[source_key]["revision"]
    mutated = _replace_once(source, revision, "1" * 40)

    with pytest.raises(ValidationError, match="source revision"):
        validate_text(mutated)


def test_validator_rejects_content_pattern_drift() -> None:
    source = _audit_text()
    mutated = _replace_once(source, "articles/*.md", "articles/*.txt")

    with pytest.raises(ValidationError, match="allowed patterns|content pattern"):
        validate_text(mutated)


def test_validator_rejects_content_count_drift() -> None:
    source = _audit_text()
    mutated = _replace_once(source, "source_owned_media=815", "source_owned_media=814")

    with pytest.raises(ValidationError, match="content source counts"):
        validate_text(mutated)


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_validator_rejects_each_projection_path_drift(artifact) -> None:
    source = _audit_text()
    mutated = _replace_once(source, artifact.path, artifact.path + ".drift")

    with pytest.raises(ValidationError, match="projection path"):
        validate_text(mutated)


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_validator_rejects_each_projection_hash_drift(artifact) -> None:
    source = _audit_text()
    mutated = _replace_once(source, artifact.sha256, "2" * 64)

    with pytest.raises(ValidationError, match="projection SHA-256"):
        validate_text(mutated)


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_validator_rejects_each_projection_count_drift(artifact) -> None:
    source = _audit_text()
    mutated = _replace_once(source, artifact.counts, artifact.counts + "; drift=1")

    with pytest.raises(ValidationError, match="projection observed count"):
        validate_text(mutated)


def test_validator_rejects_manifest_membership_drift() -> None:
    source = _audit_text()
    mutated = _replace_once(source, MANIFEST_MEMBER_TEXT, MANIFEST_MEMBER_TEXT.replace("books.json, ", ""))

    with pytest.raises(ValidationError, match="manifest member"):
        validate_text(mutated)


def test_validator_rejects_invalid_ownership_vocabulary() -> None:
    source = _audit_text()
    mutated = _replace_once(
        source,
        "| `articles` | `github-editorial-read` |",
        "| `articles` | `unowned` |",
    )

    with pytest.raises(ValidationError, match="ownership vocabulary"):
        validate_text(mutated)


def test_validator_rejects_missing_checked_in_evidence_link() -> None:
    source = _audit_text()
    mutated = _replace_once(
        source,
        "[`articles.json`](../../content/public_projection/articles.json);",
        "articles.json;",
    )

    with pytest.raises(ValidationError, match="local path link"):
        validate_text(mutated)


def test_validator_rejects_unresolved_handoff_not_open() -> None:
    source = _audit_text()
    mutated = _replace_once(source, "| `OPEN` | GitHub-backed", "| `CLOSED` | GitHub-backed")

    with pytest.raises(ValidationError, match="must remain OPEN"):
        validate_text(mutated)
