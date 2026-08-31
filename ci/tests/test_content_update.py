from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ci.content_update import (
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
    expected = {
        "courses": {"courses": 12},
        "podwiki": {
            "graph_links": 13006,
            "graph_nodes": 1072,
            "search_documents": 2998,
            "wiki_pages": 282,
        },
        "faq": {"assets": 99, "courses": 6, "questions": 1401, "sections": 70},
        "docs": {"assets": 39, "pages": 106},
    }

    for family, counts in expected.items():
        assert build_report(repository=ROOT, family=family)["counts"] == counts


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
