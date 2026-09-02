from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ci.content_update import (
    _ASSET_ROOTS,
    CONTRACT_VERSION,
    FAMILIES,
    ContentUpdateError,
    _failed_report,
    _family_contract,
    build_report,
    report_summary,
    validate_report,
)

ROOT = Path(__file__).resolve().parents[2]
# A count beyond this bound means the aggregator or the projection is broken, not that the
# catalogue grew: the largest projected collection is the wiki search index at a few thousand
# documents, and `validate_report` already caps the artifact bytes and file count.
MAX_PLAUSIBLE_COUNT = 1_000_000


def _projection_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize("family", FAMILIES)
def test_each_content_family_uses_the_same_safe_report_contract(family: str) -> None:
    report = build_report(repository=ROOT, family=family)

    assert report["contract_version"] == CONTRACT_VERSION
    assert report["family"] == family
    assert report["status"] == "pass"
    assert report["errors"] == []
    assert set(report["checks"]) == {
        "checksum",
        "projection_validation",
        "redaction",
        "source_pin",
    }
    assert all(value == "pass" for value in report["checks"].values())
    assert validate_report(report) == report
    assert report == build_report(repository=ROOT, family=family)

    # The report is metadata-only. Public asset filenames may contain words such as
    # "submission"; the contract bans sensitive fields and never copies their contents.
    serialized = json.dumps(report, sort_keys=True)
    assert "source_url" not in serialized
    assert "edit_url" not in serialized
    assert "learner" not in serialized.casefold()
    assert "registration" not in serialized.casefold()
    assert all(
        not any(term in key.casefold() for term in ("answer", "body", "email", "token"))
        for key in report
    )


def test_source_specific_counts_are_safe_aggregates() -> None:
    """Pin the shape and internal consistency of `counts`, not today's content volume.

    Exact literals here would restate -- more weakly, and in the wrong layer -- pins that
    already gate these bytes.  `content.public_data._checked_public_projection` recomputes
    `manifest.tree_sha256` over the whole projection tree on every load,
    `_validate_public_artifact_bindings` checks each declared file against
    `manifest.artifacts`, and `content_sync.dtc_content.contract` freezes the manifest and
    tree digests as the reviewed content authority.  No projected byte can move without one
    of those failing first, so a literal count adds no drift detection: it only adds a
    fourth place to re-pin on every reviewed content refresh.  It is also the weakest of
    the four -- a zero-sum edit keeps the totals -- and it is the one that went stale when
    the podcast catalogue was de-duplicated.  Assert instead what this report contract
    actually owns: that every family reports the expected, non-sensitive aggregate names,
    that no collection is silently empty or absurd, and that each number agrees with a
    count the projection independently declares for itself.
    """
    reports = {family: build_report(repository=ROOT, family=family) for family in FAMILIES}
    counts = {family: report["counts"] for family, report in reports.items()}

    assert {family: set(values) for family, values in counts.items()} == {
        "courses": {"courses"},
        "podwiki": {"graph_links", "graph_nodes", "search_documents", "wiki_pages"},
        "faq": {"assets", "courses", "questions", "sections"},
        "docs": {"assets", "pages"},
    }
    for values in counts.values():
        for value in values.values():
            assert isinstance(value, int) and not isinstance(value, bool)
            assert 0 < value < MAX_PLAUSIBLE_COUNT

    manifest = _projection_json("content/public_projection/manifest.json")
    graph = _projection_json("content/public_projection/wiki_graph.json")
    search = _projection_json("content/public_projection/wiki_search.json")

    # Each public aggregate is cross-checked against the count its own artifact declares, so
    # counting the wrong collection or emptying one still fails on any content revision.
    assert counts["courses"]["courses"] == manifest["counts"]["courses"]
    assert counts["podwiki"]["wiki_pages"] == manifest["counts"]["wiki"]
    assert counts["podwiki"]["graph_nodes"] == graph["counts"]["nodes"]
    assert counts["podwiki"]["graph_links"] == graph["counts"]["links"]
    # The search index declares no total, so require distinct document identities instead:
    # a count inflated by duplicates is a defect rather than a content refresh.
    assert counts["podwiki"]["search_documents"] == len({doc["id"] for doc in search["docs"]})

    # Every declared asset is backed by an artifact the report itself enumerated.
    for family in ("faq", "docs"):
        prefixes = tuple(f"{root}/" for root in _ASSET_ROOTS[family])
        enumerated = [
            item
            for item in reports[family]["projection"]["files"]
            if item["path"].startswith(prefixes)
        ]
        assert 0 < counts[family]["assets"] <= len(enumerated)


def test_failed_report_is_valid_but_contains_only_a_diagnostic_code() -> None:
    report = _failed_report(_family_contract("faq"), "projection_file_missing")

    assert validate_report(report) == report
    assert report["status"] == "fail"
    assert report["errors"] == ["projection_file_missing"]
    assert "projection_file_missing" in report_summary(report)
    assert "answer" not in json.dumps(report).casefold()


def test_report_validation_rejects_a_sensitive_diagnostic_even_with_a_new_digest() -> None:
    report = build_report(repository=ROOT, family="docs")
    edited = {
        **report,
        "checks": {name: "not_run" for name in report["checks"]},
        "errors": ["email"],
        "status": "fail",
    }
    identity = dict(edited)
    identity.pop("report_sha256")
    edited["report_sha256"] = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()

    with pytest.raises(ContentUpdateError, match="report_redaction_failed"):
        validate_report(edited)


def _workflow(name: str) -> dict:
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_content_update_workflow_has_one_common_matrix_contract() -> None:
    workflow = _workflow("content-update.yml")
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["on"]["pull_request"]["paths"] == workflow["on"]["push"]["paths"]
    assert {
        "content/**",
        "content_sync/**",
        "courses/**",
        "api/views/course_repository_webhooks.py",
        "ci/content_update.py",
    }.issubset(workflow["on"]["push"]["paths"])
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "cancel-in-progress": "false",
        "group": "website-content-update-${{ github.ref }}",
    }

    job = workflow["jobs"]["validate"]
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == "20"
    assert job["strategy"] == {
        "fail-fast": "false",
        "matrix": {"family": list(FAMILIES)},
    }
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"] == {
        "clean": "true",
        "fetch-depth": "1",
        "persist-credentials": "false",
    }
    setup_uv = next(step for step in steps if step.get("uses") == "astral-sh/setup-uv@v6")
    assert setup_uv["with"] == {"enable-cache": "false", "version": "0.10.11"}
    assert any(step.get("uses") == "./.github/actions/content-update" for step in steps)
    artifact = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")
    assert artifact["if"] == "always()"
    assert artifact["with"]["if-no-files-found"] == "error"
    assert artifact["with"]["retention-days"] == "30"

    source_specific = "\n".join(step.get("run", "") for step in steps)
    for family in FAMILIES:
        assert family in source_specific or family in json.dumps(steps)
    assert "api/tests/test_course_repository_webhooks.py" in source_specific
    assert "content_sync/tests/test_webhook_delivery.py" in source_specific
    assert "secrets." not in json.dumps(workflow)
    assert "pull_request_target" not in json.dumps(workflow)
    assert "contents: write" not in json.dumps(workflow)


def test_content_update_action_delegates_common_work_to_the_script() -> None:
    action = yaml.safe_load(
        (ROOT / ".github" / "actions" / "content-update" / "action.yml").read_text(encoding="utf-8")
    )
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {"family", "output-directory"}
    runs = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "python -m ci.content_update" in runs
    assert "--summary --report" in runs
    assert "GITHUB_STEP_SUMMARY" in runs
    assert "cat " not in runs


def test_make_target_runs_all_four_families_or_one_explicit_family() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "content-update-check" in makefile
    assert "CONTENT_UPDATE_FAMILY ?= all" in makefile
    assert 'families="courses podwiki faq docs"' in makefile
    assert '--family "$$family"' in makefile
