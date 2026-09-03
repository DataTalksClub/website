from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

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

_SAFE_COUNT_KEYS = {
    "courses": {"courses"},
    "podwiki": {"graph_links", "graph_nodes", "search_documents", "wiki_pages"},
    "faq": {"assets", "courses", "questions", "sections"},
    "docs": {"assets", "pages"},
}


def _load_json(relative_path: str) -> Any:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _report_artifact_paths(report: Mapping[str, Any]) -> set[str]:
    projection = report["projection"]
    assert isinstance(projection, Mapping)
    files = projection["files"]
    assert isinstance(files, list)
    paths = [item["path"] for item in files if isinstance(item, Mapping)]
    assert len(paths) == len(files)
    assert all(isinstance(path, str) and path for path in paths)
    assert len(set(paths)) == len(paths)
    return set(paths)


def _assert_safe_counts(report: Mapping[str, Any], family: str) -> dict[str, Any]:
    counts = report["counts"]
    assert isinstance(counts, Mapping)
    assert set(counts) == _SAFE_COUNT_KEYS[family]

    projection = report["projection"]
    assert isinstance(projection, Mapping)
    projection_bytes = projection["bytes"]
    assert isinstance(projection_bytes, int)
    assert not isinstance(projection_bytes, bool)
    assert projection_bytes > 0

    for value in counts.values():
        assert isinstance(value, int)
        assert not isinstance(value, bool)
        assert value > 0
        assert value <= projection_bytes
    return dict(counts)


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
    reports = {family: build_report(repository=ROOT, family=family) for family in FAMILIES}
    counts = {family: _assert_safe_counts(report, family) for family, report in reports.items()}

    courses = _load_json("content/public_projection/courses.json")
    assert isinstance(courses, list)
    course_slugs = []
    course_paths = []
    for course in courses:
        assert isinstance(course, Mapping)
        slug = course.get("slug")
        public_path = course.get("public_path")
        assert isinstance(slug, str) and slug
        assert isinstance(public_path, str) and public_path
        course_slugs.append(slug)
        course_paths.append(public_path)
    assert len(course_slugs) == len(set(course_slugs))
    assert len(course_paths) == len(set(course_paths))
    assert counts["courses"] == {"courses": len(courses)}

    wiki_pages = _load_json("content/public_projection/wiki.json")
    graph = _load_json("content/public_projection/wiki_graph.json")
    search = _load_json("content/public_projection/wiki_search.json")
    assert isinstance(wiki_pages, list)
    assert isinstance(graph, Mapping)
    assert isinstance(search, Mapping)

    wiki_slugs = []
    wiki_paths = []
    for page in wiki_pages:
        assert isinstance(page, Mapping)
        slug = page.get("slug")
        public_path = page.get("public_path")
        assert isinstance(slug, str) and slug
        assert isinstance(public_path, str) and public_path == f"/wiki/{slug}"
        wiki_slugs.append(slug)
        wiki_paths.append(public_path)
    assert len(wiki_slugs) == len(set(wiki_slugs))
    assert len(wiki_paths) == len(set(wiki_paths))

    nodes = graph.get("nodes")
    links = graph.get("links")
    assert isinstance(nodes, list)
    assert isinstance(links, list)
    node_ids = []
    for node in nodes:
        assert isinstance(node, Mapping)
        node_id = node.get("id")
        assert isinstance(node_id, str) and node_id
        node_ids.append(node_id)
    node_id_set = set(node_ids)
    assert len(node_ids) == len(node_id_set)
    assert {f"wiki:{slug}" for slug in wiki_slugs} <= node_id_set

    link_ids = []
    for link in links:
        assert isinstance(link, Mapping)
        kind = link.get("kind")
        source = link.get("source")
        target = link.get("target")
        weight = link.get("weight")
        assert isinstance(kind, str) and kind
        assert isinstance(source, str) and source in node_id_set
        assert isinstance(target, str) and target in node_id_set
        assert isinstance(weight, int) and not isinstance(weight, bool)
        link_ids.append((kind, source, target, weight))
    assert len(link_ids) == len(set(link_ids))

    search_documents = search.get("docs")
    assert isinstance(search_documents, list)
    search_ids = []
    search_graph_ids = []
    for document in search_documents:
        assert isinstance(document, Mapping)
        document_id = document.get("id")
        graph_id = document.get("graph_id")
        assert isinstance(document_id, str) and document_id
        assert isinstance(graph_id, str) and graph_id in node_id_set
        search_ids.append(document_id)
        search_graph_ids.append(graph_id)
    assert len(search_ids) == len(set(search_ids))
    assert search_graph_ids
    assert set(search_graph_ids) <= node_id_set
    assert counts["podwiki"] == {
        "graph_links": len(links),
        "graph_nodes": len(nodes),
        "search_documents": len(search_documents),
        "wiki_pages": len(wiki_pages),
    }

    faq = _load_json("content/faq_projection.json")
    assert isinstance(faq, Mapping)
    faq_declared_counts = faq.get("counts")
    faq_courses = faq.get("courses")
    assert isinstance(faq_declared_counts, Mapping)
    assert set(faq_declared_counts) == _SAFE_COUNT_KEYS["faq"]
    assert isinstance(faq_courses, list)

    faq_artifact_paths = {
        path
        for path in _report_artifact_paths(reports["faq"])
        if path.startswith("content/faq_assets/")
    }
    faq_course_slugs = []
    faq_section_ids = []
    faq_question_ids = []
    faq_asset_public_paths = set()
    faq_asset_artifact_paths = set()
    for course in faq_courses:
        assert isinstance(course, Mapping)
        slug = course.get("slug")
        sections = course.get("sections")
        assert isinstance(slug, str) and slug
        assert isinstance(sections, list)
        faq_course_slugs.append(slug)
        course_section_ids = []
        course_question_ids = []
        for section in sections:
            assert isinstance(section, Mapping)
            section_id = section.get("id")
            questions = section.get("questions")
            assert isinstance(section_id, str) and section_id
            assert isinstance(questions, list)
            section_key = (slug, section_id)
            assert section_key not in faq_section_ids
            faq_section_ids.append(section_key)
            course_section_ids.append(section_id)
            for question in questions:
                assert isinstance(question, Mapping)
                question_id = question.get("id")
                assert isinstance(question_id, str) and question_id
                assert question_id not in faq_question_ids
                assert question.get("course") == slug
                assert question.get("section_id") == section_id
                faq_question_ids.append(question_id)
                course_question_ids.append(question_id)
                images = question.get("images", [])
                assert isinstance(images, list)
                for image in images:
                    assert isinstance(image, Mapping)
                    source_path = image.get("source_path")
                    public_path = image.get("public_path")
                    assert isinstance(source_path, str) and "\\" not in source_path
                    source = PurePosixPath(source_path)
                    assert source_path == source.as_posix()
                    assert source.parts[:2] == ("images", slug)
                    assert len(source.parts) == 3
                    assert isinstance(public_path, str)
                    assert public_path == f"/faq/{source_path}"
                    artifact_path = (
                        PurePosixPath("content/faq_assets")
                        / PurePosixPath(slug)
                        / PurePosixPath(source.name)
                    ).as_posix()
                    assert artifact_path in faq_artifact_paths
                    faq_asset_public_paths.add(public_path)
                    faq_asset_artifact_paths.add(artifact_path)
        assert len(course_section_ids) == len(set(course_section_ids))
        assert course.get("section_count") == len(sections)
        assert course.get("question_count") == len(course_question_ids)
    assert len(faq_course_slugs) == len(set(faq_course_slugs))
    assert len(faq_asset_public_paths) == len(faq_asset_artifact_paths)
    faq_derived_counts = {
        "assets": len(faq_asset_public_paths),
        "courses": len(faq_courses),
        "questions": len(faq_question_ids),
        "sections": len(faq_section_ids),
    }
    assert dict(faq_declared_counts) == faq_derived_counts
    assert counts["faq"] == faq_derived_counts

    docs = _load_json("content/docs_projection.json")
    assert isinstance(docs, Mapping)
    docs_assets = docs.get("assets")
    docs_pages = docs.get("pages")
    assert isinstance(docs_assets, list)
    assert isinstance(docs_pages, list)

    docs_asset_sources = []
    docs_asset_public_paths = []
    declared_docs_artifacts = set()
    for asset in docs_assets:
        assert isinstance(asset, Mapping)
        source_path = asset.get("source_path")
        public_path = asset.get("public_path")
        assert isinstance(source_path, str) and "\\" not in source_path
        source = PurePosixPath(source_path)
        assert source_path == source.as_posix()
        assert source.parts and source.parts[0] == "assets"
        assert isinstance(public_path, str) and public_path == f"/docs/{source_path}"
        docs_asset_sources.append(source_path)
        docs_asset_public_paths.append(public_path)
        declared_docs_artifacts.add(
            (PurePosixPath("content/docs_assets") / PurePosixPath(*source.parts[1:])).as_posix()
        )
    assert len(docs_asset_sources) == len(set(docs_asset_sources))
    assert len(docs_asset_public_paths) == len(set(docs_asset_public_paths))
    report_docs_artifacts = {
        path
        for path in _report_artifact_paths(reports["docs"])
        if path.startswith("content/docs_assets/")
    }
    assert report_docs_artifacts == declared_docs_artifacts

    docs_page_sources = []
    docs_page_public_paths = []
    for page in docs_pages:
        assert isinstance(page, Mapping)
        source_path = page.get("source_path")
        public_path = page.get("public_path")
        assert isinstance(source_path, str) and source_path
        assert isinstance(public_path, str) and public_path
        docs_page_sources.append(source_path)
        docs_page_public_paths.append(public_path)
    assert len(docs_page_sources) == len(set(docs_page_sources))
    assert len(docs_page_public_paths) == len(set(docs_page_public_paths))
    assert counts["docs"] == {"assets": len(docs_assets), "pages": len(docs_pages)}


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
