from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ci.content_invariants import ContentInvariantError, build_invariant_artifact
from ci.verification import build_plan
from tests_ci.helpers import repository_with_change, selection_for


def plan_for_catalog(tmp_path: Path, records: list[dict[str, object]]):
    body = json.dumps(records) + "\n"
    repository, base, head = repository_with_change(
        tmp_path,
        {"data/catalog.json": body},
        initial={
            "data/catalog.json": ('[{"id":"baseline","title":"Baseline","url":"/baseline"}]\n')
        },
    )
    selection, changes = selection_for(("data/catalog.json",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=changes,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    return repository, plan


def test_structured_content_proves_count_identity_order_url_and_metadata(tmp_path: Path) -> None:
    repository, plan = plan_for_catalog(
        tmp_path,
        [
            {"id": "one", "title": "One", "url": "https://example.com/one"},
            {"id": "two", "description": "Two", "path": "/two"},
        ],
    )
    result = build_invariant_artifact(repository=repository, plan=plan)
    file_result = result["files"][0]
    assert result["record_count"] == 2
    assert file_result["identity_fields"] == ["id", "id"]
    assert file_result["identity_unique"] is True
    assert file_result["identity_order_sha256"]
    assert file_result["canonical_url_order_sha256"]
    assert file_result["metadata_order_sha256"]
    assert file_result["records_with_url"] == 2
    assert file_result["url_complete_count"] == file_result["url_total_count"] == 2
    assert file_result["metadata_complete_count"] == file_result["metadata_total_count"] == 2


@pytest.mark.parametrize(
    "records",
    [
        [
            {"id": "same", "title": "One", "url": "/one"},
            {"id": "same", "title": "Two", "url": "/two"},
        ],
        [{"id": "one", "title": "One", "url": "javascript:alert(1)"}],
        [{"id": "one", "url": "/one"}],
        [{"title": "No stable identity", "url": "/one"}],
        [{"id": "one", "title": "No canonical URL"}],
        [],
    ],
    ids=[
        "duplicate-identity",
        "invalid-url",
        "missing-metadata",
        "missing-stable-identity",
        "missing-canonical-url",
        "empty-catalog",
    ],
)
def test_structured_content_invariants_fail_closed(
    tmp_path: Path, records: list[dict[str, object]]
) -> None:
    repository, plan = plan_for_catalog(tmp_path, records)
    with pytest.raises(ContentInvariantError):
        build_invariant_artifact(repository=repository, plan=plan)
