from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit

import pytest
import yaml  # type: ignore[import-untyped]

from compatibility.source_config import (
    FAQ_FROZEN_GENERATION_TIME,
    FAQ_WEBSITE_UV_LOCK_SHA256,
    PINNED_LEGACY_SOURCES,
    RUSTKYLL_0_4_10_LINUX_AMD64_SHA256,
    RUSTKYLL_0_4_10_LINUX_AMD64_URL,
    RUSTKYLL_0_5_3_LINUX_AMD64_BINARY_SHA256,
    RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_SHA256,
    RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_URL,
    PinnedLegacySource,
    generated_contract_kind,
    generated_public_path,
    pinned_source,
)
from scripts import build_pinned_legacy_sources
from scripts.render_course_platform_inventory import route_entries

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "_docs/compatibility"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FAQ_ID = re.compile(r"^[0-9A-Za-z]{10}$")
GENERATED_ROW_KEYS = {
    "classification",
    "content_sha256",
    "contract_kind",
    "expected_status",
    "machine_contract_seed",
    "public_path",
    "public_path_percent_encoded",
    "schema_version",
    "source_id",
    "source_path",
    "source_revision",
}


def load_json(name: str) -> Any:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


def load_jsonl(name: str) -> list[dict[str, object]]:
    path = CONTRACT_ROOT / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def source_map() -> dict[str, PinnedLegacySource]:
    return {source.source_id: source for source in PINNED_LEGACY_SOURCES}


def test_source_configuration_matches_selected_planning_pins() -> None:
    planning = yaml.safe_load((ROOT / "_docs/planning/sources/index.yaml").read_text())
    selected = {item["id"]: item for item in planning["sources"] if item["id"] in source_map()}

    assert set(selected) == set(source_map())
    for source_id, source in source_map().items():
        locator, revision = selected[source_id]["locator"].rsplit("/tree/", maxsplit=1)
        assert source.repository.removesuffix(".git") == locator
        assert source.revision == revision
        source.validate()


def test_source_configuration_pins_tools_mounts_and_machine_contracts() -> None:
    sources = source_map()

    assert len(sources) == 5
    assert RUSTKYLL_0_4_10_LINUX_AMD64_URL == (
        "https://github.com/alexeygrigorev/rustkyll/releases/download/v0.4.10/rustkyll-linux-amd64"
    )
    assert RUSTKYLL_0_4_10_LINUX_AMD64_SHA256 == (
        "ab96b800eb8427591841232ed2d0619f011b639200df6b4514ac9680caa6130e"
    )
    assert RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_URL == (
        "https://files.pythonhosted.org/packages/32/f4/"
        "9cae847680982c09346f8db66568a9ecb11d2e8de411c9829c7c8e2c4415/"
        "rustkyll-0.5.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    )
    assert RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_SHA256 == (
        "348c622cac08cdd2361c4300161b7da34b7f7162bf0ad3d9fd9a0cd053f54a8e"
    )
    assert RUSTKYLL_0_5_3_LINUX_AMD64_BINARY_SHA256 == (
        "c8c2e6c732ecc224c28c170782114980b4707514835e7f587293f78bd38f2fba"
    )
    assert FAQ_WEBSITE_UV_LOCK_SHA256 == (
        "f8070e0954a5bca6e7bd58c76854fd51b906b47a5d5f93b7423541ef436dc8f8"
    )
    assert FAQ_FROZEN_GENERATION_TIME == "2000-01-01 00:00:00"
    assert {source.path_prefix for source in sources.values()} == {"/", "/docs", "/faq", "/podwiki"}
    assert sum(len(source.machine_contracts) for source in sources.values()) == 50
    assert (
        "/podwiki/search/?q=machine+learning&level=section"
        in sources["dtc-podwiki"].machine_contracts
    )
    assert "/podwiki/graph/#topic%3Allms" in sources["dtc-podwiki"].machine_contracts
    assert sources["dtc-main-site"].build_tool_version == "v0.4.10"
    assert sources["dtc-docs"].build_tool_version == "v0.4.10"
    assert sources["dtc-podwiki"].build_tool_version == "0.5.3"
    assert sources["dtc-main-site"].build_tool_url == RUSTKYLL_0_4_10_LINUX_AMD64_URL
    assert sources["dtc-docs"].build_tool_url == RUSTKYLL_0_4_10_LINUX_AMD64_URL
    assert sources["dtc-podwiki"].build_tool_url == RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_URL
    assert sources["dtc-main-site"].build_tool_binary_sha256 == (RUSTKYLL_0_4_10_LINUX_AMD64_SHA256)
    assert sources["dtc-podwiki"].build_tool_binary_sha256 == (
        RUSTKYLL_0_5_3_LINUX_AMD64_BINARY_SHA256
    )


def test_generated_path_baseline_is_complete_and_provenance_backed() -> None:
    rows = load_jsonl("generated-path-baseline.jsonl")
    sources = source_map()
    counts = Counter(row["source_id"] for row in rows)

    assert len(rows) == 2_937
    assert counts == {
        "dtc-main-site": 2_301,
        "dtc-docs": 174,
        "dtc-faq": 152,
        "dtc-podwiki": 310,
    }
    assert len({(row["source_id"], row["public_path"]) for row in rows}) == len(rows)
    for row in rows:
        assert set(row) == GENERATED_ROW_KEYS
        source = sources[str(row["source_id"])]
        assert row["schema_version"] == 1
        assert row["source_revision"] == source.revision
        assert row["classification"] == "preserve"
        assert row["expected_status"] == 200
        assert SHA256.fullmatch(str(row["content_sha256"]))
        assert str(row["source_path"]).startswith("_site/")
        assert row["contract_kind"] == generated_contract_kind(str(row["source_path"])[6:])
        assert row["public_path"] == generated_public_path(source, str(row["source_path"])[6:])
        assert row["public_path_percent_encoded"] == quote(str(row["public_path"]), safe="/@")
        assert "?" not in str(row["public_path"])
        assert "#" not in str(row["public_path"])

    provenance = load_json("source-build-provenance.json")
    assert isinstance(provenance, dict)
    assert provenance["source_path_rows"] == len(rows)
    assert provenance["classification_default"] == "preserve"
    records = {record["source_id"]: record for record in provenance["records"]}
    assert set(records) == set(sources)
    for source_id, source in sources.items():
        record = records[source_id]
        assert record["repository"] == source.repository
        assert record["revision"] == source.revision
        assert record["build_tool"] == source.build_tool
        assert record["build_tool_version"] == source.build_tool_version
        assert record["build_tool_sha256"] == source.build_tool_sha256
        assert record["build_tool_binary_sha256"] == source.build_tool_binary_sha256
        assert record["build_tool_url"] == source.build_tool_url
        expected_overrides = list(source.deterministic_overrides)
        if source.source_id == "dtc-main-site":
            expected_overrides.append("source-date-epoch=1785872368")
        elif source.source_id == "dtc-docs":
            expected_overrides.append("source-date-epoch=1786017922")
        elif source.source_id == "dtc-podwiki":
            expected_overrides.append("source-date-epoch=1785736104")
        assert record["deterministic_overrides"] == expected_overrides
        if source.output_directory is None:
            assert record["output_file_count"] == 0
            assert record["output_tree_sha256"] is None
        else:
            assert record["output_file_count"] == counts[source_id]
            assert SHA256.fullmatch(record["output_tree_sha256"])


def test_machine_contract_samples_cover_configured_queries_fragments_and_absences() -> None:
    document = load_json("machine-contract-samples.json")
    assert isinstance(document, dict)
    samples = document["samples"]
    expected = {
        (source.source_id, contract)
        for source in PINNED_LEGACY_SOURCES
        for contract in source.machine_contracts
    }

    assert document["sample_count"] == 50
    assert {(row["source_id"], row["public_contract"]) for row in samples} == expected
    for row in samples:
        parsed = urlsplit(row["public_contract"])
        expected_kind = "fragment" if parsed.fragment else "query" if parsed.query else "path"
        assert row["contract_kind"] == expected_kind
        assert row["classification"] == "preserve"
        assert (row["path"], row["query"], row["fragment"]) == (
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    assert sum(row["contract_kind"] == "query" for row in samples) == 4
    assert sum(row["contract_kind"] == "fragment" for row in samples) == 1
    course_samples = [row for row in samples if row["source_id"] == "dtc-course-platform"]
    assert len(course_samples) == 22
    assert {
        row["public_contract"]
        for row in course_samples
        if row["course_route_contract_present"] is False
    } == {"/robots.txt"}
    assert all(
        row["course_route_contract_present"]
        for row in course_samples
        if row["public_contract"] != "/robots.txt"
    )

    missing = {
        (row["source_id"], row["public_contract"])
        for row in samples
        if not row["source_output_present"] and row["source_id"] != "dtc-course-platform"
    }
    differences = load_json("source-production-differences.json")
    assert isinstance(differences, dict)
    assert (
        missing
        == {
            (row["source_id"], row["public_contract"])
            for row in differences["known_source_output_absences"]
        }
        == {
            ("dtc-docs", "/docs/robots.txt"),
            ("dtc-podwiki", "/podwiki/robots.txt"),
        }
    )


def test_faq_fragment_inventory_covers_every_stable_anchor() -> None:
    rows = load_jsonl("faq-fragment-contracts.jsonl")
    generated_paths = {row["public_path"] for row in load_jsonl("generated-path-baseline.jsonl")}

    assert len(rows) == 1_401
    assert len({(row["public_path"], row["fragment_id"]) for row in rows}) == len(rows)
    assert len({row["course_slug"] for row in rows}) == 6
    for row in rows:
        assert row["source_id"] == "dtc-faq"
        assert row["source_revision"] == pinned_source("dtc-faq").revision
        assert row["classification"] == "preserve"
        assert FAQ_ID.fullmatch(str(row["fragment_id"]))
        assert str(row["source_path"]).startswith("_questions/")
        assert ".." not in Path(str(row["source_path"])).parts
        assert row["public_path"] in generated_paths
        assert row["public_path_with_fragment"] == (f"{row['public_path']}#{row['fragment_id']}")
        assert row["public_path_with_fragment_percent_encoded"] == (
            f"{quote(str(row['public_path']), safe='/')}#{quote(str(row['fragment_id']), safe='')}"
        )


def test_podwiki_graph_inventory_covers_every_graph_hash() -> None:
    rows = load_jsonl("podwiki-graph-fragment-contracts.jsonl")

    assert len(rows) == 1_072
    assert len({row["fragment_id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["source_id"] == "dtc-podwiki"
        assert row["source_revision"] == pinned_source("dtc-podwiki").revision
        assert row["source_path"] == "_site/graph/graph.json"
        assert row["public_path"] == "/podwiki/graph/"
        assert row["classification"] == "preserve"
        assert row["public_path_with_fragment_percent_encoded"] == (
            f"/podwiki/graph/#{quote(str(row['fragment_id']), safe='')}"
        )
        assert str(row["target_url"]).startswith(("/", "https://datatalks.club/"))
        assert row["target_type"]


def test_course_route_inventory_matches_all_adopted_urlconfs() -> None:
    document = load_json("course-route-contracts.json")
    assert isinstance(document, dict)
    rows = document["routes"]
    current_routes = route_entries()

    assert document["route_count"] == len(rows) == len(current_routes) == 89
    assert Counter(row["surface"] for row in rows) == {
        "Accounts": 9,
        "Compatibility API": 29,
        "Studio Courses": 26,
        "Public courses": 25,
    }

    def target_identity(row: Any) -> tuple[object, ...]:
        if isinstance(row, dict):
            return (
                row["surface"],
                row["route_pattern"],
                row["example_path"],
                row["name"],
            )
        return (
            row.surface,
            f"/{row.route}",
            row.example_path(),
            row.name or None,
        )

    current_by_target = {target_identity(row): row for row in current_routes}
    source_by_target = {target_identity(row): row for row in rows}
    assert len(current_by_target) == len(current_routes)
    assert len(source_by_target) == len(rows)
    assert set(source_by_target) == set(current_by_target)

    for identity, current in current_by_target.items():
        source_row = source_by_target[identity]
        if current.surface != "Studio Courses":
            assert source_row["urlconf"] == current.module
            assert source_row["callback"] == current.callback
            assert source_row["source_route_pattern"] == f"/{current.route}"
            assert source_row["source_example_path"] == current.example_path()
            assert source_row["source_name"] == (current.name or None)
            continue

        # The target half is active Django state. The source half remains the exact
        # mechanically mapped identity from the pinned CMP cadmin URLconf; it must not
        # be rewritten merely to make the active and source namespaces look identical.
        assert current.module == "studio_courses.urls"
        assert current.callback.startswith("studio_courses.")
        assert source_row["urlconf"] == "cadmin.urls"
        assert source_row["callback"] == current.callback.replace("studio_courses.", "cadmin.", 1)

    studio_rows = [row for row in rows if row["surface"] == "Studio Courses"]
    assert sum(row["route_pattern"] == "/studio/courses" for row in studio_rows) == 1
    assert sum(row["example_path"] == "/studio/courses" for row in studio_rows) == 1
    assert all(
        row["route_pattern"] == "/studio/courses"
        or row["route_pattern"].startswith("/studio/courses/")
        for row in studio_rows
    )
    assert all(
        row["example_path"] == "/studio/courses"
        or row["example_path"].startswith("/studio/courses/")
        for row in studio_rows
    )
    assert all(str(row["name"]).startswith("studio_courses_") for row in studio_rows)
    assert {row["source_id"] for row in studio_rows} == {"dtc-course-platform"}
    assert {row["source_revision"] for row in studio_rows} == {
        pinned_source("dtc-course-platform").revision
    }
    for row in studio_rows:
        suffix = (
            ""
            if row["route_pattern"] == "/studio/courses"
            else row["route_pattern"].removeprefix("/studio/courses/")
        )
        example_suffix = (
            ""
            if row["example_path"] == "/studio/courses"
            else row["example_path"].removeprefix("/studio/courses/")
        )
        assert row["source_route_pattern"] == f"/cadmin/{suffix}"
        assert row["source_example_path"] == f"/cadmin/{example_suffix}"
        assert row["source_name"] == (f"cadmin_{str(row['name']).removeprefix('studio_courses_')}")
    for row in rows:
        assert row["source_revision"] == pinned_source("dtc-course-platform").revision
        assert row["host"] == "courses.datatalks.club"
        assert row["classification"] == "preserve"
        assert row["expected_status"] is None
        assert row["authenticated_production_probe"] == "not_performed"
    assert not document["authenticated_production_probes_performed"]


def test_difference_ledger_records_completed_capture_without_approving_changes() -> None:
    differences = load_json("source-production-differences.json")
    assert isinstance(differences, dict)

    assert differences["status"] == "source_and_production_capture_complete_differences_unresolved"
    assert differences["checked_manifest"] == {
        "both_capture_rows": 2_937,
        "generated_at": "2026-08-08T05:55:00Z",
        "manifest_rows": 2_965,
        "manifest_sha256": "94a6469530a290147e94f825eb981836e1358b9d0a72c7f50f0eda6d638e1d7f",
        "production_capture_rows": 2_965,
        "production_input_sha256": (
            "4c38d0fbd2a405e661b9aac55843201dc367e0b4e49a31b02d806d764788f9f3"
        ),
        "production_only_rows": 28,
        "schema_version": 2,
        "source_capture_rows": 2_937,
        "source_input_sha256": ("5600bc6d5e0d13196888054bd707e79a9bb3b3aa634121d8babcfb138774b78a"),
        "source_only_rows": 0,
        "tool_version": "dtc-legacy-manifest-crawler/3",
    }
    assert differences["production_capture"] == {
        "client_redirect_rows": 2,
        "error_rows": 0,
        "http_redirect_chain_rows": 11,
        "response_count": 2_976,
        "soft_404_rows": 9,
        "status_counts": {"200": 2_951, "401": 4, "403": 2, "404": 7, "405": 1},
        "transfer_bytes": 258_215_736,
    }
    production_differences = differences["production_differences"]
    assert production_differences == [
        {
            "affected_urls": 2_965,
            "artifact": "legacy-manifest-differences.json",
            "difference_count": 4_918,
            "difference_kind_counts": {
                "asset_added": 17,
                "asset_removed": 19,
                "canonical_changed": 3,
                "field_changed": 4_805,
                "fragment_added": 21,
                "fragment_removed": 21,
                "route_added": 28,
                "route_removed": 4,
            },
            "review_state": "unresolved",
            "sha256": "72b2a68694b17c091ba09e29f1e44e2c052cd6d244b7c750abde3f66ad0cd4ff",
        }
    ]
    assert differences["source_contract_inventories"] == {
        "generated_path_rows": 2_937,
        "machine_contract_samples": 50,
        "faq_fragment_contracts": 1_401,
        "podwiki_graph_fragment_contracts": 1_072,
        "course_route_contracts": 89,
    }
    assert {row["difference_id"] for row in differences["known_source_build_differences"]} == {
        "dtc-docs-build-tool-version",
        "dtc-faq-generation-time",
        "dtc-main-malformed-meta-browser-fingerprint",
    }
    unresolved = {row["item"] for row in differences["unresolved"]}
    assert "source/production compatibility differences" in unresolved
    assert "authenticated course HTML/API probes" in unresolved

    classifications: list[object] = []
    classifications.extend(
        row["classification"] for row in load_jsonl("generated-path-baseline.jsonl")
    )
    classifications.extend(
        row["classification"] for row in load_jsonl("faq-fragment-contracts.jsonl")
    )
    classifications.extend(
        row["classification"] for row in load_jsonl("podwiki-graph-fragment-contracts.jsonl")
    )
    classifications.append(load_json("course-route-contracts.json")["classification_default"])
    assert set(classifications) == {"preserve"}


def test_source_builder_refuses_nonlocal_workspace_and_pins_reproducibility_controls() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        build_pinned_legacy_sources._workspace("/tmp/dtc-legacy-source")

    local = build_pinned_legacy_sources._workspace(str(ROOT / ".tmp/test-legacy-source"))
    assert local == (ROOT / ".tmp/test-legacy-source").resolve()
    assert build_pinned_legacy_sources.MAX_BUILD_TOOL_BYTES == 32 * 1024 * 1024
    assert "FrozenDateTime" in build_pinned_legacy_sources.FAQ_RUNNER
    assert "cls(2000, 1, 1, 0, 0, 0)" in build_pinned_legacy_sources.FAQ_RUNNER

    helper_source = (ROOT / "scripts/build_pinned_legacy_sources.py").read_text()
    for token in (
        '"--filter=blob:none"',
        '"--no-checkout"',
        '"--detach"',
        '"--frozen"',
        '"--untracked-files=all"',
    ):
        assert token in helper_source


def test_source_builder_child_environment_is_minimal_and_credential_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = {
        "AWS_ACCESS_KEY_ID": "aws-access-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-key",
        "AWS_SESSION_TOKEN": "aws-session-secret",
        "DATABASE_URL": "postgresql://user:secret@example.invalid/database",
        "DJANGO_SECRET_KEY": "django-secret",
        "GH_TOKEN": "github-cli-secret",
        "GITHUB_TOKEN": "github-actions-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/safe/bin:/usr/bin")

    runtime_root = ROOT / ".tmp/test-legacy-build-environment"
    environment = build_pinned_legacy_sources._build_environment(runtime_root)

    assert not set(secrets).intersection(environment)
    assert environment["PATH"] == "/safe/bin:/usr/bin"
    assert environment["HOME"] == str(runtime_root / "home")
    assert environment["TMPDIR"] == str(runtime_root / "tmp")
    assert environment["UV_CACHE_DIR"] == str(runtime_root / "cache/uv")
    assert environment["XDG_CACHE_HOME"] == str(runtime_root / "cache/xdg")
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["UV_NO_CONFIG"] == "1"
    assert environment["TZ"] == "UTC"
    assert environment["LC_ALL"] == environment["LANG"] == "C.UTF-8"
    assert set(environment) == {
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "HOME",
        "JEKYLL_ENV",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONHASHSEED",
        "PYTHONUTF8",
        "TMPDIR",
        "TZ",
        "UV_CACHE_DIR",
        "UV_NO_CONFIG",
        "XDG_CACHE_HOME",
    }


def test_source_builder_derives_epoch_from_the_exact_pinned_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = pinned_source("dtc-main-site")
    observed: list[tuple[list[str], Path]] = []

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
    ) -> str:
        del environment
        observed.append((arguments, cwd))
        return f"{source.revision}\n1723158000\n"

    monkeypatch.setattr(build_pinned_legacy_sources, "_run", fake_run)

    epoch = build_pinned_legacy_sources._source_date_epoch(ROOT, source)

    assert epoch == 1_723_158_000
    assert observed == [
        (
            [
                "git",
                "show",
                "-s",
                "--format=%H%n%ct",
                "--no-patch",
                source.revision,
            ],
            ROOT,
        )
    ]


@pytest.mark.parametrize(
    ("revision", "query_result"),
    [
        ("not-a-revision", None),
        ("a" * 40, ""),
        ("a" * 40, f"{'a' * 40}\n"),
        ("a" * 40, f"{'b' * 40}\n1723158000\n"),
        ("a" * 40, f"{'a' * 40}\nnot-an-epoch\n"),
        ("a" * 40, f"{'a' * 40}\n1723158000\nextra\n"),
        (
            "a" * 40,
            f"{'a' * 40}\n{build_pinned_legacy_sources.MAX_SOURCE_DATE_EPOCH + 1}\n",
        ),
    ],
)
def test_source_builder_rejects_invalid_revision_epoch_metadata_without_echo(
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    query_result: str | None,
) -> None:
    source = PinnedLegacySource(
        source_id="test-source",
        repository="https://github.com/DataTalksClub/example.git",
        revision=revision,
        public_base_url="https://datatalks.club/",
        path_prefix="/",
        source_kind=build_pinned_legacy_sources.SourceKind.RUSTKYLL_RELEASE,
        output_directory="_site",
        build_tool="rustkyll",
        build_tool_version="test",
    )

    def fake_run(*_args: object, **_kwargs: object) -> str:
        if query_result is None:
            raise AssertionError("malformed revisions must fail before git is called")
        return query_result

    monkeypatch.setattr(build_pinned_legacy_sources, "_run", fake_run)

    with pytest.raises(build_pinned_legacy_sources.BuildError) as raised:
        build_pinned_legacy_sources._source_date_epoch(ROOT, source)

    diagnostic = str(raised.value)
    assert len(diagnostic) <= 96
    assert "not-an-epoch" not in diagnostic
    assert str(build_pinned_legacy_sources.MAX_SOURCE_DATE_EPOCH + 1) not in diagnostic


def test_source_builder_adds_only_valid_source_date_epoch_to_child_environment() -> None:
    runtime_root = ROOT / ".tmp/test-source-date-epoch-environment"
    environment = build_pinned_legacy_sources._build_environment(
        runtime_root,
        source_date_epoch=1_723_158_000,
    )

    assert environment["SOURCE_DATE_EPOCH"] == "1723158000"
    assert len(environment) == 15
    for value in (-1, True, build_pinned_legacy_sources.MAX_SOURCE_DATE_EPOCH + 1):
        with pytest.raises(build_pinned_legacy_sources.BuildError):
            build_pinned_legacy_sources._build_environment(
                runtime_root,
                source_date_epoch=value,
            )


def test_source_builder_normalizes_every_tracked_regular_file_mtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = ROOT / ".tmp/tests" / f"source-mtime-{uuid.uuid4().hex}"
    first = checkout / "alpha.txt"
    second = checkout / "nested/beta.txt"
    second.parent.mkdir(parents=True)
    first.write_text("alpha", encoding="utf-8")
    second.write_text("beta", encoding="utf-8")

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
    ) -> str:
        del environment
        assert arguments == ["git", "ls-files", "-z", "--cached", "--"]
        assert cwd == checkout
        return "alpha.txt\0nested/beta.txt\0"

    monkeypatch.setattr(build_pinned_legacy_sources, "_run", fake_run)
    try:
        build_pinned_legacy_sources._normalize_tracked_source_mtimes(
            checkout,
            1_723_158_000,
        )
        expected = 1_723_158_000_000_000_000
        assert first.stat().st_mtime_ns == expected
        assert second.stat().st_mtime_ns == expected
    finally:
        shutil.rmtree(checkout)


def test_source_builder_rejects_unverifiable_or_unsafe_tracked_mtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = ROOT / ".tmp/tests" / f"source-mtime-reject-{uuid.uuid4().hex}"
    checkout.mkdir(parents=True)
    tracked = checkout / "tracked.txt"
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_run",
        lambda *_args, **_kwargs: "tracked.txt\0",
    )
    try:
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="tracked source file is missing",
        ):
            build_pinned_legacy_sources._normalize_tracked_source_mtimes(checkout, 1_723_158_000)

        tracked.write_text("tracked", encoding="utf-8")
        monkeypatch.setattr(os, "utime", lambda *_args, **_kwargs: None)
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="tracked source mtime could not be normalized",
        ):
            build_pinned_legacy_sources._normalize_tracked_source_mtimes(checkout, 1_723_158_000)
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="source date epoch is out of range",
        ):
            build_pinned_legacy_sources._normalize_tracked_source_mtimes(
                checkout,
                build_pinned_legacy_sources.MAX_SOURCE_DATE_EPOCH + 1,
            )
    finally:
        shutil.rmtree(checkout)


def test_source_builder_records_epoch_provenance_only_for_rustkyll_sources() -> None:
    main_site = pinned_source("dtc-main-site")
    faq = pinned_source("dtc-faq")

    assert build_pinned_legacy_sources._deterministic_overrides(
        main_site,
        1_723_158_000,
    ) == ["source-date-epoch=1723158000"]
    assert build_pinned_legacy_sources._deterministic_overrides(faq, None) == list(
        faq.deterministic_overrides
    )


def test_rustkyll_build_normalizes_sources_and_receives_the_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = pinned_source("dtc-main-site")
    workspace = ROOT / ".tmp/tests" / f"source-build-epoch-{uuid.uuid4().hex}"
    checkout = workspace / "sources" / source.source_id
    checkout.mkdir(parents=True)
    tool = workspace / "tools/rustkyll"
    observed_epochs: list[int] = []
    observed_environments: list[dict[str, str]] = []

    monkeypatch.setattr(build_pinned_legacy_sources, "verify_checkout", lambda *_args: None)
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_reset_generated_output",
        lambda *_args: None,
    )
    monkeypatch.setattr(build_pinned_legacy_sources, "_download_rustkyll", lambda *_args: tool)
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_normalize_tracked_source_mtimes",
        lambda _checkout, epoch: observed_epochs.append(epoch),
    )

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
    ) -> str:
        assert arguments == [str(tool), "build"]
        assert cwd == checkout
        assert environment is not None
        observed_environments.append(environment)
        output = checkout / str(source.output_directory)
        output.mkdir(parents=True)
        (output / "index.html").write_text("built", encoding="utf-8")
        return ""

    monkeypatch.setattr(build_pinned_legacy_sources, "_run", fake_run)
    try:
        output = build_pinned_legacy_sources.build_source(
            workspace,
            source,
            checkout,
            source_date_epoch=1_723_158_000,
        )

        assert output == checkout / "_site"
        assert observed_epochs == [1_723_158_000]
        assert [environment["SOURCE_DATE_EPOCH"] for environment in observed_environments] == [
            "1723158000"
        ]
    finally:
        shutil.rmtree(workspace)


def test_podwiki_build_uses_only_the_digest_verified_pypi_wheel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = pinned_source("dtc-podwiki")
    workspace = ROOT / ".tmp/tests" / f"source-build-wheel-{uuid.uuid4().hex}"
    checkout = workspace / "sources" / source.source_id
    checkout.mkdir(parents=True)
    wheel = workspace / "tools" / Path(str(source.build_tool_url)).name
    observed: list[tuple[list[str], Path, dict[str, str]]] = []

    monkeypatch.setattr(build_pinned_legacy_sources, "verify_checkout", lambda *_args: None)
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_reset_generated_output",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_download_rustkyll_wheel",
        lambda *_args: wheel,
    )
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_normalize_tracked_source_mtimes",
        lambda *_args: None,
    )
    monkeypatch.setattr(shutil, "which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
    ) -> str:
        assert environment is not None
        observed.append((arguments, cwd, environment))
        output = checkout / str(source.output_directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("built", encoding="utf-8")
        return ""

    monkeypatch.setattr(build_pinned_legacy_sources, "_run", fake_run)
    try:
        output = build_pinned_legacy_sources.build_source(
            workspace,
            source,
            checkout,
            source_date_epoch=1_785_736_104,
        )

        assert output == checkout / "_site"
        rustkyll_arguments = observed[0][0]
        assert rustkyll_arguments == [
            "/usr/bin/uvx",
            "--no-config",
            "--from",
            str(wheel),
            "rustkyll",
            "build",
            "--baseurl",
            "/podwiki",
        ]
        assert "rustkyll==" not in " ".join(rustkyll_arguments)
        assert all(
            environment["SOURCE_DATE_EPOCH"] == "1785736104" for _, _, environment in observed
        )
    finally:
        shutil.rmtree(workspace)


def test_pypi_wheel_must_contain_the_separately_pinned_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = ROOT / ".tmp/tests" / f"source-wheel-digest-{uuid.uuid4().hex}"
    wheel = workspace / "rustkyll-9.9.9-py3-none-manylinux_2_17_x86_64.whl"
    binary = b"published rustkyll binary"
    source = PinnedLegacySource(
        source_id="test-source",
        repository="https://github.com/DataTalksClub/example.git",
        revision="a" * 40,
        public_base_url="https://datatalks.club/",
        path_prefix="/",
        source_kind=build_pinned_legacy_sources.SourceKind.RUSTKYLL_PYPI,
        output_directory="_site",
        build_tool="rustkyll-pypi",
        build_tool_version="9.9.9",
        build_tool_sha256="b" * 64,
        build_tool_url="https://files.pythonhosted.org/packages/test/rustkyll.whl",
        build_tool_binary_sha256=hashlib.sha256(binary).hexdigest(),
    )
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        build_pinned_legacy_sources,
        "_download_pinned_build_tool",
        lambda *_args, **_kwargs: wheel,
    )
    try:
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("rustkyll/bin/rustkyll", binary)
        assert build_pinned_legacy_sources._download_rustkyll_wheel(workspace, source) == wheel

        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("rustkyll/bin/rustkyll", b"different binary")
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="Rustkyll wheel binary digest mismatch",
        ):
            build_pinned_legacy_sources._download_rustkyll_wheel(workspace, source)
    finally:
        shutil.rmtree(workspace)


def test_source_builder_run_does_not_propagate_process_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_names = {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "DATABASE_URL",
        "DJANGO_SECRET_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    }
    for name in secret_names:
        monkeypatch.setenv(name, f"secret-{name.lower()}")
    observed_environment: dict[str, str] = {}

    def fake_run(
        arguments: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> SimpleNamespace:
        del arguments, cwd, capture_output, text, check
        observed_environment.update(env)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(build_pinned_legacy_sources.subprocess, "run", fake_run)

    assert build_pinned_legacy_sources._run(["git", "--version"], cwd=ROOT) == "ok\n"
    assert not secret_names.intersection(observed_environment)


def test_source_artifact_check_mode_is_offline_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = ROOT / ".tmp/tests" / f"source-artifact-check-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    payloads = {name: name.encode() for name in build_pinned_legacy_sources.ARTIFACT_NAMES}
    observed: list[tuple[str, object]] = []

    def fake_build_artifacts(path: Path) -> dict[str, bytes]:
        observed.append(("build", path))
        return payloads

    def fake_check_artifacts(value: dict[str, bytes]) -> None:
        observed.append(("check", value))

    def unexpected_network_or_build(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline check must not prepare a checkout or build a source")

    monkeypatch.setattr(
        build_pinned_legacy_sources, "build_artifact_payloads", fake_build_artifacts
    )
    monkeypatch.setattr(
        build_pinned_legacy_sources, "check_artifact_payloads", fake_check_artifacts
    )
    monkeypatch.setattr(
        build_pinned_legacy_sources, "prepare_checkout", unexpected_network_or_build
    )
    monkeypatch.setattr(build_pinned_legacy_sources, "build_source", unexpected_network_or_build)
    try:
        assert (
            build_pinned_legacy_sources.main(
                ["--workspace", str(workspace.relative_to(ROOT)), "--check"]
            )
            == 0
        )
        assert observed == [("build", workspace), ("check", payloads)]
    finally:
        workspace.rmdir()


def test_source_artifact_check_compares_every_file_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_root = ROOT / ".tmp/tests" / f"source-contracts-{uuid.uuid4().hex}"
    contract_root.mkdir(parents=True)
    payloads = {
        name: f"exact:{name}\n".encode() for name in build_pinned_legacy_sources.ARTIFACT_NAMES
    }
    monkeypatch.setattr(build_pinned_legacy_sources, "CONTRACT_ROOT", contract_root)
    try:
        for name, payload in payloads.items():
            (contract_root / name).write_bytes(payload)
        build_pinned_legacy_sources.check_artifact_payloads(payloads)

        stale = build_pinned_legacy_sources.ARTIFACT_NAMES[0]
        (contract_root / stale).write_bytes(payloads[stale] + b"changed")
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match=rf"^checked source artifacts are stale: {re.escape(stale)}$",
        ):
            build_pinned_legacy_sources.check_artifact_payloads(payloads)
    finally:
        for path in contract_root.iterdir():
            path.unlink()
        contract_root.rmdir()


def test_source_artifact_traversal_is_bounded_and_rejects_symlinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = ROOT / ".tmp/tests" / f"bounded-source-{uuid.uuid4().hex}"
    source_root.mkdir(parents=True)
    first = source_root / "first.txt"
    second = source_root / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    monkeypatch.setattr(build_pinned_legacy_sources, "MAX_SOURCE_TREE_FILES", 1)
    try:
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="^source tree exceeds the file limit: bounded-source-",
        ):
            build_pinned_legacy_sources._bounded_files(source_root)

        monkeypatch.setattr(build_pinned_legacy_sources, "MAX_SOURCE_TREE_FILES", 10)
        symlink = source_root / "linked.txt"
        symlink.symlink_to(first)
        with pytest.raises(
            build_pinned_legacy_sources.BuildError,
            match="^source tree contains a symbolic link: bounded-source-",
        ):
            build_pinned_legacy_sources._bounded_files(source_root)
        symlink.unlink()
    finally:
        first.unlink()
        second.unlink()
        source_root.rmdir()
