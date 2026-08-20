from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.content_invariants import ContentInvariantError, _file_invariants
from ci.ownership import load_graph

ROOT = Path(__file__).resolve().parents[2]


def test_compatibility_manifest_invariants_use_nested_capture_identity_and_url(
    tmp_path: Path,
) -> None:
    relative_path = Path("_docs/compatibility/legacy-manifest.jsonl")
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "record_kind": "provenance",
                    "generated_at": "2026-08-08T05:55:00Z",
                },
                {
                    "record_kind": "compatibility_row",
                    "production_capture": {
                        "requested_url": "https://example.test/",
                        "metadata": {"title": "Example"},
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = _file_invariants(tmp_path, relative_path.as_posix(), load_graph()["large_content"])

    assert result["record_count"] == 2
    assert result["identity_fields"] == ["record_key", "record_key"]
    assert result["records_with_url"] == 2
    assert result["metadata_complete_count"] == 2


def test_article_faq_invariants_cover_the_nested_article_records() -> None:
    result = _file_invariants(
        ROOT,
        "content/article_faq.json",
        load_graph()["large_content"],
    )

    assert result["record_count"] == 10
    assert result["identity_fields"] == ["slug"] * 10
    assert result["records_with_url"] == 10
    assert result["metadata_complete_count"] == 10


def test_compatibility_manifest_rejects_missing_requested_url(tmp_path: Path) -> None:
    relative_path = Path("_docs/compatibility/legacy-manifest.jsonl")
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "record_kind": "compatibility_row",
                "production_capture": {"metadata": {"title": "Missing URL"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ContentInvariantError, match="requested URL"):
        _file_invariants(tmp_path, relative_path.as_posix(), load_graph()["large_content"])
