from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from ci import focused_tests
from ci.selection import (
    APPLICATION_TEST_LABELS,
    ChangeRecord,
    DiffParseError,
    UnsupportedStatus,
    classify_records,
    dump_selection,
    parse_name_status,
    selection_summary,
    validate_selection,
)

BASE = "1" * 40
HEAD = "2" * 40


@pytest.mark.parametrize(("root", "labels"), APPLICATION_TEST_LABELS.items())
def test_every_reviewed_application_closure_is_exact(root: str, labels: tuple[str, ...]) -> None:
    result = classify_records(
        (
            ChangeRecord("M", (f"{root}/alpha.py",)),
            ChangeRecord("A", (f"{root}/nested/beta.py",)),
        ),
        event="push",
        base=BASE,
        head=HEAD,
    )

    assert result["profile"] == "focused"
    assert result["reason"] == "single_application"
    assert result["application_roots"] == [root]
    assert result["test_labels"] == list(labels)
    assert result["test_labels"] == sorted(set(result["test_labels"]))
    assert validate_selection(result) is result


@pytest.mark.parametrize(
    ("changed_root", "importing_label"),
    [
        ("jobs", "events"),
        ("management_api", "studio"),
        ("management_auth", "accounts"),
        ("review_import", "courses"),
        ("content", "content_sync"),
    ],
)
def test_documented_reverse_import_closures_select_importing_suites(
    changed_root: str, importing_label: str
) -> None:
    result = classify_records(
        (ChangeRecord("M", (f"{changed_root}/service.py",)),),
        event="push",
        base=BASE,
        head=HEAD,
    )

    assert result["profile"] == "focused"
    assert result["reason"] == "single_application"
    assert importing_label in result["test_labels"]


@pytest.mark.parametrize(
    ("paths", "reason"),
    [
        (("api/a.py", "studio/b.py"), "cross_application"),
        (("api/a.py", "unknown/b.py"), "unknown_path"),
        (("accounts/a.py",), "shared_application"),
        (("core/a.py",), "shared_application"),
        (("events/a.py",), "testless_application"),
        (("content/migrations/0001.py",), "migration_changed"),
        (("content/templates/content/a.txt",), "template_changed"),
        (("content/static/content/a.css",), "static_changed"),
        (("content/views/page.html",), "html_changed"),
        ((".github/workflows/ci.yml",), "configuration_or_dependency"),
        (("deploy/cli.py",), "configuration_or_dependency"),
        (("scripts/a.py",), "configuration_or_dependency"),
        (("test_support/factories/context.py",), "configuration_or_dependency"),
        (("test_support/tests/test_marker_registry.py",), "configuration_or_dependency"),
        (("conftest.py",), "configuration_or_dependency"),
        (("e2e/pytest.ini",), "configuration_or_dependency"),
        (("sitecustomize.py",), "configuration_or_dependency"),
        (("pyproject.toml",), "configuration_or_dependency"),
        (("uv.lock",), "configuration_or_dependency"),
        (("_docs/contract.json",), "documentation_or_contract"),
        (("compatibility/generated.json",), "documentation_or_contract"),
        (("brand_new/a.py",), "unknown_path"),
    ],
)
def test_force_full_and_ambiguous_paths(paths: tuple[str, ...], reason: str) -> None:
    result = classify_records(
        tuple(ChangeRecord("M", (path,)) for path in paths),
        event="push",
        base=BASE,
        head=HEAD,
    )

    assert result["profile"] == "full"
    assert result["reason"] == reason
    assert result["test_labels"] == []


def test_name_status_parser_is_nul_safe_for_rename_copy_and_hostile_names() -> None:
    payload = (
        b"M\0content/space tab\tnewline\n$() ; `name`.py\0"
        b"R100\0content/old name.py\0content/new \xe2\x98\x83.py\0"
        b"C075\0content/-leading.py\0content/copy.py\0"
    )

    records = parse_name_status(payload)

    assert [record.status for record in records] == ["M", "R100", "C075"]
    assert records[0].paths == ("content/space tab\tnewline\n$() ; `name`.py",)
    assert records[1].paths == ("content/old name.py", "content/new ☃.py")
    assert classify_records(records, event="push", base=BASE, head=HEAD)["profile"] == "focused"


@pytest.mark.parametrize("status", ["R100", "C100"])
def test_rename_and_copy_classify_both_paths(status: str) -> None:
    within = classify_records(
        (ChangeRecord(status, ("api/old.py", "api/new.py")),),
        event="push",
        base=BASE,
        head=HEAD,
    )
    across = classify_records(
        (ChangeRecord(status, ("api/old.py", "studio/new.py")),),
        event="push",
        base=BASE,
        head=HEAD,
    )
    forced = classify_records(
        (ChangeRecord(status, ("api/old.py", "api/templates/new.html")),),
        event="push",
        base=BASE,
        head=HEAD,
    )

    assert within["profile"] == "focused"
    assert within["changed_path_count"] == 2
    assert across["reason"] == "cross_application"
    assert forced["reason"] == "template_changed"


@pytest.mark.parametrize("payload", [b"", b"M\0content/a.py", b"M\0", b"R100\0only-one\0"])
def test_malformed_name_status_is_rejected(payload: bytes) -> None:
    with pytest.raises(DiffParseError):
        parse_name_status(payload)


@pytest.mark.parametrize("payload", [b"T\0content/a.py\0", b"R\0a\0b\0", b"R1\0a\0b\0", b"X\0a\0"])
def test_unsupported_name_status_is_rejected(payload: bytes) -> None:
    with pytest.raises(UnsupportedStatus):
        parse_name_status(payload)


def test_nonordinary_object_forces_full() -> None:
    result = classify_records(
        (ChangeRecord("M", ("api/link.py",), ordinary=False),),
        event="push",
        base=BASE,
        head=HEAD,
    )
    assert result["profile"] == "full"
    assert result["reason"] == "unsupported_file_mode"


@pytest.mark.parametrize("path", ["/content/a.py", "content/../core/a.py", "content//a.py"])
def test_malformed_repository_path_forces_full(path: str) -> None:
    result = classify_records((ChangeRecord("M", (path,)),), event="push", base=BASE, head=HEAD)
    assert result["profile"] == "full"
    assert result["reason"] == "unknown_path"


def test_schema_rejects_non_allowlisted_or_reordered_labels() -> None:
    result = classify_records(
        (ChangeRecord("M", ("studio/a.py",)),), event="push", base=BASE, head=HEAD
    )
    for labels in (["studio", "accounts", "core"], ["accounts", "core", "shell input"]):
        invalid = {**result, "test_labels": labels}
        with pytest.raises(ValueError):
            validate_selection(invalid)


def test_schema_rejects_focused_manual_or_focused_reason_on_full() -> None:
    focused = classify_records(
        (ChangeRecord("M", ("api/a.py",)),), event="push", base=BASE, head=HEAD
    )
    with pytest.raises(ValueError):
        validate_selection({**focused, "event": "workflow_dispatch"})
    with pytest.raises(ValueError):
        validate_selection({**focused, "profile": "full", "test_labels": []})


def test_json_and_summary_are_deterministic_and_do_not_include_filenames(tmp_path: Path) -> None:
    hostile = "content/name\n## injected ${{ secrets.TOKEN }}.py"
    selection = classify_records(
        (ChangeRecord("M", (hostile,)),), event="push", base=BASE, head=HEAD
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_selection(selection, first)
    dump_selection(selection, second)

    assert first.read_bytes() == second.read_bytes()
    assert hostile not in first.read_text(encoding="utf-8")
    assert hostile not in selection_summary(selection)
    assert json.loads(first.read_text(encoding="utf-8")) == selection


def test_focused_runner_uses_one_process_and_only_validated_labels(tmp_path: Path) -> None:
    selection = classify_records(
        (ChangeRecord("M", ("content/a.py",)),), event="push", base=BASE, head=HEAD
    )
    path = tmp_path / "selection.json"
    dump_selection(selection, path)

    with mock.patch("ci.focused_tests.subprocess.run") as run:
        focused_tests.run(str(path))

    run.assert_called_once_with(
        [
            focused_tests.sys.executable,
            "manage.py",
            "test",
            "accounts",
            "content.tests",
            "content_sync",
            "core",
        ],
        check=True,
    )
