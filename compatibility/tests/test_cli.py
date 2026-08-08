import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from compatibility.crawler import CrawlCheckpoint, HttpResponse
from compatibility.models import (
    Capture,
    Classification,
    CompatibilityRow,
    ManifestProvenance,
    ObservationOrigin,
    PageMetadata,
    Reference,
    ReferenceKind,
    SourceRevision,
    dumps_jsonl,
    loads_jsonl,
)
from scripts import build_legacy_manifest as manifest_cli
from scripts.build_legacy_manifest import CliError, _policy, _seed_urls, _verify_robots, main

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "https://github.com/DataTalksClub/example.git"


@pytest.fixture
def cli_workspace() -> Iterator[Path]:
    workspace = ROOT / ".tmp/tests" / f"legacy-manifest-cli-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    yield workspace
    for item in workspace.iterdir():
        item.unlink()
    workspace.rmdir()


def _capture(
    origin: ObservationOrigin,
    path: str,
    *,
    status: int = 200,
    metadata: PageMetadata | None = None,
) -> Capture:
    return Capture.create(
        origin=origin,
        requested_url=f"https://datatalks.club{path}",
        status=status,
        source_repository=REPOSITORY if origin is ObservationOrigin.SOURCE else "",
        source_path=(path.removeprefix("/") or "index.html")
        if origin is ObservationOrigin.SOURCE
        else "",
        content_type="text/html",
        metadata=metadata,
    )


def _metadata(
    *,
    canonical: str,
    fragments: tuple[str, ...],
    asset: str,
) -> PageMetadata:
    references = (
        Reference(ReferenceKind.ASSET, f"https://datatalks.club{asset}"),
        Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/articles.html"),
    )
    return PageMetadata(
        title="Known page",
        canonical_url=canonical,
        fragments=fragments,
        references=references,
    )


def _write_manifest(path: Path, rows: tuple[CompatibilityRow, ...]) -> None:
    provenance = ManifestProvenance.create(
        generated_at="2026-08-08T10:00:00Z",
        tool_version="test-crawler/1",
    )
    path.write_text(dumps_jsonl(provenance, rows), encoding="utf-8")


def _merge_provenance(*, production: bool) -> ManifestProvenance:
    current = manifest_cli._provenance("2026-08-08T10:00:00Z", _policy())
    return current if production else replace(current, production_origins=())


def _write_merge_input(
    path: Path,
    provenance: ManifestProvenance,
    capture: Capture,
) -> None:
    row = (
        CompatibilityRow(Classification.preserve(), source_capture=capture)
        if capture.origin is ObservationOrigin.SOURCE
        else CompatibilityRow(Classification.preserve(), production_capture=capture)
    )
    path.write_text(dumps_jsonl(provenance, (row,)), encoding="utf-8")


def _comparison_rows() -> tuple[CompatibilityRow, ...]:
    source_metadata = _metadata(
        canonical="https://datatalks.club/known.html",
        fragments=("kept-fragment", "removed-fragment"),
        asset="/assets/removed.css",
    )
    production_metadata = _metadata(
        canonical="https://datatalks.club/new-canonical.html",
        fragments=("added-fragment", "kept-fragment"),
        asset="/assets/added.css",
    )
    return (
        CompatibilityRow(
            Classification.preserve(),
            source_capture=_capture(ObservationOrigin.SOURCE, "/removed.html"),
        ),
        CompatibilityRow(
            Classification.preserve(),
            production_capture=_capture(ObservationOrigin.PRODUCTION, "/added.html"),
        ),
        CompatibilityRow(
            Classification.preserve(),
            source_capture=_capture(
                ObservationOrigin.SOURCE,
                "/known.html",
                metadata=source_metadata,
            ),
            production_capture=_capture(
                ObservationOrigin.PRODUCTION,
                "/known.html",
                metadata=production_metadata,
            ),
        ),
    )


def test_production_seed_set_is_exact_fragmentless_and_excludes_course_examples() -> None:
    seeds = _seed_urls(
        ROOT / "_docs/compatibility/generated-path-baseline.jsonl",
        ROOT / "_docs/compatibility/course-route-contracts.json",
    )
    assert len(seeds) == 2_965
    assert all("#" not in seed for seed in seeds)
    assert "https://courses.datatalks.club/api/health/" in seeds
    assert "https://courses.datatalks.club/robots.txt" in seeds
    assert "https://courses.datatalks.club/accounts/toggle-dark-mode/" in seeds
    assert "https://courses.datatalks.club/courses/example-course/" not in seeds
    assert "https://datatalks.club/podwiki/search/?q=machine+learning&document_type=page" in seeds


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "boolean_schema", "forged_revision", "forged_path"],
)
def test_source_seed_loader_rejects_noncanonical_or_malformed_baseline(
    cli_workspace: Path,
    mutation: str,
) -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "_docs/compatibility/generated-path-baseline.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    if mutation == "duplicate":
        rows.append(dict(rows[0]))
    elif mutation == "boolean_schema":
        rows[0]["schema_version"] = True
    elif mutation == "forged_revision":
        rows[0]["source_revision"] = "f" * 40
    else:
        rows[0]["source_path"] = "_site/../forged.html"
    baseline = cli_workspace / f"{mutation}.jsonl"
    baseline.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(CliError, match="generated path baseline"):
        manifest_cli._source_urls(baseline)


@pytest.mark.parametrize(
    "document",
    [
        {"authenticated_production_probes_performed": False},
        {
            **json.loads(
                (ROOT / "_docs/compatibility/course-route-contracts.json").read_text(
                    encoding="utf-8"
                )
            ),
            "schema_version": True,
        },
    ],
)
def test_seed_loader_rejects_minimal_or_malformed_course_inventory(
    cli_workspace: Path,
    document: dict[str, object],
) -> None:
    course_routes = cli_workspace / "course-routes.json"
    course_routes.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CliError, match="course route contract"):
        _seed_urls(
            ROOT / "_docs/compatibility/generated-path-baseline.jsonl",
            course_routes,
        )


def test_production_policy_is_seed_only_robots_gated_and_paced() -> None:
    policy = _policy()
    assert policy.discover_references is False
    assert policy.robots_required is True
    assert policy.bounds.request_interval_seconds >= 0.1
    assert policy.bounds.retry_backoff_seconds > 0


def test_partial_source_manifest_claims_only_the_selected_revision(
    cli_workspace: Path,
) -> None:
    output_tree = cli_workspace / "sources" / "dtc-main-site" / "_site"
    output_tree.mkdir(parents=True)
    (output_tree / "index.html").write_text(
        "<html><head><title>Selected</title></head><body>Selected tree</body></html>",
        encoding="utf-8",
    )
    output = cli_workspace / "partial-source.jsonl"
    try:
        assert (
            main(
                [
                    "source",
                    "--source-id",
                    "dtc-main-site",
                    "--workspace",
                    str(cli_workspace),
                    "--generated-at",
                    "2026-08-08T10:00:00Z",
                    "--output",
                    str(output),
                ]
            )
            == 0
        )
        provenance, rows = loads_jsonl(output.read_text(encoding="utf-8"))
        assert [revision.source_id for revision in provenance.source_revisions] == ["dtc-main-site"]
        assert len(rows) == 1
    finally:
        (output_tree / "index.html").unlink()
        output_tree.rmdir()
        output_tree.parent.rmdir()
        output_tree.parent.parent.rmdir()


@pytest.mark.parametrize(
    ("field_name", "production_provenance"),
    [
        (
            "generated_at",
            replace(
                _merge_provenance(production=True),
                generated_at="2026-08-08T10:00:01Z",
            ),
        ),
        (
            "tool_version",
            replace(
                _merge_provenance(production=True),
                tool_version="test-crawler/other",
            ),
        ),
        (
            "source_revisions",
            replace(
                _merge_provenance(production=True),
                source_revisions=(SourceRevision("other", REPOSITORY, "c" * 40),),
            ),
        ),
        (
            "allowlisted_hosts",
            replace(
                _merge_provenance(production=True),
                allowlisted_hosts=("datatalks.club",),
            ),
        ),
        (
            "crawl_policy_sha256",
            replace(
                _merge_provenance(production=True),
                crawl_policy_sha256="d" * 64,
            ),
        ),
    ],
)
def test_merge_rejects_mismatched_capture_provenance(
    cli_workspace: Path,
    field_name: str,
    production_provenance: ManifestProvenance,
) -> None:
    source_path = cli_workspace / "source.jsonl"
    production_path = cli_workspace / "production.jsonl"
    output = cli_workspace / "merged.jsonl"
    _write_merge_input(
        source_path,
        _merge_provenance(production=False),
        _capture(ObservationOrigin.SOURCE, "/only.html"),
    )
    _write_merge_input(
        production_path,
        production_provenance,
        _capture(ObservationOrigin.PRODUCTION, "/only.html"),
    )

    with pytest.raises(CliError, match=f"^merge provenance mismatch: {field_name}$"):
        main(
            [
                "merge",
                "--source",
                str(source_path),
                "--production",
                str(production_path),
                "--generated-at",
                "2026-08-08T10:00:00Z",
                "--output",
                str(output),
                "--allow-missing",
            ]
        )
    assert not output.exists()


def test_merge_rejects_matching_stale_provenance_instead_of_relabeling_observations(
    cli_workspace: Path,
) -> None:
    source_path = cli_workspace / "source.jsonl"
    production_path = cli_workspace / "production.jsonl"
    output = cli_workspace / "merged.jsonl"
    stale_revisions = (SourceRevision("stale", REPOSITORY, "c" * 40),)
    stale_source = replace(
        _merge_provenance(production=False),
        tool_version="stale-crawler/2",
        source_revisions=stale_revisions,
        crawl_policy_sha256="d" * 64,
    )
    stale_production = replace(
        _merge_provenance(production=True),
        tool_version="stale-crawler/2",
        source_revisions=stale_revisions,
        crawl_policy_sha256="d" * 64,
    )
    _write_merge_input(
        source_path,
        stale_source,
        _capture(ObservationOrigin.SOURCE, "/only.html"),
    )
    _write_merge_input(
        production_path,
        stale_production,
        _capture(ObservationOrigin.PRODUCTION, "/only.html"),
    )

    with pytest.raises(CliError, match="source merge provenance does not match current"):
        main(
            [
                "merge",
                "--source",
                str(source_path),
                "--production",
                str(production_path),
                "--generated-at",
                "2026-08-08T10:00:00Z",
                "--output",
                str(output),
                "--allow-missing",
            ]
        )
    assert not output.exists()


@pytest.mark.parametrize("wrong_input", ["source", "production"])
def test_merge_rejects_capture_rows_in_the_wrong_input(
    cli_workspace: Path,
    wrong_input: str,
) -> None:
    source_path = cli_workspace / "source.jsonl"
    production_path = cli_workspace / "production.jsonl"
    output = cli_workspace / "merged.jsonl"
    source_capture = _capture(ObservationOrigin.SOURCE, "/only.html")
    production_capture = _capture(ObservationOrigin.PRODUCTION, "/only.html")
    _write_merge_input(
        source_path,
        _merge_provenance(production=False),
        production_capture if wrong_input == "source" else source_capture,
    )
    _write_merge_input(
        production_path,
        _merge_provenance(production=True),
        source_capture if wrong_input == "production" else production_capture,
    )

    with pytest.raises(CliError, match=f"^{wrong_input} merge input contains an unexpected"):
        main(
            [
                "merge",
                "--source",
                str(source_path),
                "--production",
                str(production_path),
                "--generated-at",
                "2026-08-08T10:00:00Z",
                "--output",
                str(output),
                "--allow-missing",
            ]
        )
    assert not output.exists()


def test_merge_rejects_missing_machine_only_production_seed(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = cli_workspace / "source.jsonl"
    production_path = cli_workspace / "production.jsonl"
    output = cli_workspace / "merged.jsonl"
    source_capture = _capture(ObservationOrigin.SOURCE, "/only.html")
    production_capture = _capture(ObservationOrigin.PRODUCTION, "/only.html")
    _write_merge_input(source_path, _merge_provenance(production=False), source_capture)
    _write_merge_input(production_path, _merge_provenance(production=True), production_capture)
    monkeypatch.setattr(
        manifest_cli,
        "_source_urls",
        lambda _baseline: (source_capture.requested_url,),
    )
    monkeypatch.setattr(
        manifest_cli,
        "_seed_urls",
        lambda _baseline, _course_routes: (
            source_capture.requested_url,
            "https://datatalks.club/robots.txt",
        ),
    )

    with pytest.raises(CliError, match="exact authorized seed set"):
        main(
            [
                "merge",
                "--source",
                str(source_path),
                "--production",
                str(production_path),
                "--generated-at",
                "2026-08-08T10:00:00Z",
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_merge_rejects_missing_generated_source_capture(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = cli_workspace / "source.jsonl"
    production_path = cli_workspace / "production.jsonl"
    output = cli_workspace / "merged.jsonl"
    source_capture = _capture(ObservationOrigin.SOURCE, "/only.html")
    production_capture = _capture(ObservationOrigin.PRODUCTION, "/only.html")
    _write_merge_input(source_path, _merge_provenance(production=False), source_capture)
    _write_merge_input(production_path, _merge_provenance(production=True), production_capture)
    monkeypatch.setattr(
        manifest_cli,
        "_source_urls",
        lambda _baseline: (
            source_capture.requested_url,
            "https://datatalks.club/missing-generated.html",
        ),
    )
    monkeypatch.setattr(
        manifest_cli,
        "_seed_urls",
        lambda _baseline, _course_routes: (production_capture.requested_url,),
    )

    with pytest.raises(CliError, match="exact generated baseline set"):
        main(
            [
                "merge",
                "--source",
                str(source_path),
                "--production",
                str(production_path),
                "--generated-at",
                "2026-08-08T10:00:00Z",
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_robots_preflight_fetches_each_origin_and_denies_blocked_seed() -> None:
    class RobotsTransport:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def fetch(self, url: str) -> HttpResponse:
            self.requested.append(url)
            body = (
                b"User-agent: *\nDisallow: /private\n"
                if url == "https://datatalks.club/robots.txt"
                else b"User-agent: *\nDisallow:\n"
            )
            return HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(("content-type", "text/plain"),),
                body=body,
                redirect_chain=(),
                response_count=1,
                transfer_bytes=len(body),
            )

    transport = RobotsTransport()
    with pytest.raises(CliError, match="^robots policy disallows a production seed$"):
        _verify_robots(
            (
                "https://courses.datatalks.club/api/health/",
                "https://datatalks.club/private",
            ),
            transport,  # type: ignore[arg-type]
        )
    assert transport.requested == [
        "https://courses.datatalks.club/robots.txt",
        "https://datatalks.club/robots.txt",
    ]


def test_compare_writes_stable_schema_valid_actionable_differences(
    cli_workspace: Path,
) -> None:
    manifest = cli_workspace / "merged.jsonl"
    first_output = cli_workspace / "first-differences.json"
    second_output = cli_workspace / "second-differences.json"
    _write_manifest(manifest, _comparison_rows())

    first_status = main(
        [
            "compare",
            str(manifest),
            "--output",
            str(first_output),
            "--fail-on-difference",
        ]
    )
    second_status = main(
        [
            "compare",
            str(manifest),
            "--output",
            str(second_output),
            "--fail-on-difference",
        ]
    )

    assert first_status == second_status == 1
    assert first_output.read_bytes() == second_output.read_bytes()
    document = json.loads(first_output.read_text(encoding="utf-8"))
    assert document["record_kind"] == "legacy_manifest_differences"
    assert document["schema_version"] == 1
    records = document["differences"]
    assert {record["kind"] for record in records} == {
        "asset_added",
        "asset_removed",
        "canonical_changed",
        "fragment_added",
        "fragment_removed",
        "route_added",
        "route_removed",
    }
    assert {record["required_action"] for record in records} == {
        "restore_or_approve_asset",
        "restore_or_approve_canonical",
        "restore_or_approve_fragment",
        "restore_or_classify_route",
        "review_and_baseline_asset",
        "review_and_baseline_fragment",
        "review_and_baseline_route",
    }
    assert all(record["difference_id"].startswith("sha256:") for record in records)
    assert not tuple(cli_workspace.glob(".*.pending"))


def test_compare_versions_reports_same_granular_categories_independent_of_row_order(
    cli_workspace: Path,
) -> None:
    rows = _comparison_rows()
    removed, added, changed = rows
    before = cli_workspace / "before.jsonl"
    after = cli_workspace / "after.jsonl"
    first_output = cli_workspace / "versions-first.json"
    second_output = cli_workspace / "versions-second.json"
    assert changed.source_capture is not None
    assert changed.production_capture is not None
    before_changed = CompatibilityRow(
        Classification.preserve(), source_capture=changed.source_capture
    )
    after_changed = CompatibilityRow(
        Classification.preserve(),
        source_capture=Capture.create(
            origin=ObservationOrigin.SOURCE,
            requested_url=changed.production_capture.requested_url,
            status=changed.production_capture.status,
            source_repository=REPOSITORY,
            source_path="known.html",
            content_type=changed.production_capture.content_type,
            metadata=changed.production_capture.metadata,
        ),
    )
    _write_manifest(before, (removed, before_changed))
    _write_manifest(after, (after_changed, added))

    first_status = main(
        [
            "compare-versions",
            str(before),
            str(after),
            "--output",
            str(first_output),
        ]
    )
    _write_manifest(before, (before_changed, removed))
    _write_manifest(after, (added, after_changed))
    second_status = main(
        [
            "compare-versions",
            str(before),
            str(after),
            "--output",
            str(second_output),
        ]
    )

    assert first_status == second_status == 0
    assert first_output.read_bytes() == second_output.read_bytes()
    records = json.loads(first_output.read_text(encoding="utf-8"))["differences"]
    assert {record["kind"] for record in records} == {
        "asset_added",
        "asset_removed",
        "canonical_changed",
        "fragment_added",
        "fragment_removed",
        "route_added",
        "route_removed",
    }


def test_compare_output_is_atomic_and_does_not_replace_existing_file_on_failure(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = cli_workspace / "merged.jsonl"
    output = cli_workspace / "differences.json"
    _write_manifest(manifest, _comparison_rows())
    output.write_text("existing\n", encoding="utf-8")
    pending = ROOT / ".tmp/compatibility/differences.atomic-failure.pending"
    pending.unlink(missing_ok=True)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(manifest_cli.secrets, "token_hex", lambda _length: "atomic-failure")
    monkeypatch.setattr(manifest_cli.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        main(["compare", str(manifest), "--output", str(output)])

    assert output.read_text(encoding="utf-8") == "existing\n"
    assert not pending.exists()


def test_compare_rejects_invalid_schema_output_and_unsafe_targets(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = cli_workspace / "merged.jsonl"
    output = cli_workspace / "differences.json"
    _write_manifest(manifest, _comparison_rows())

    monkeypatch.setattr(manifest_cli, "dumps_differences", lambda _items: "{}\n")
    with pytest.raises(CliError, match="does not match checked-in schema"):
        main(["compare", str(manifest), "--output", str(output)])
    assert not output.exists()

    with pytest.raises(SystemExit):
        main(["compare", str(manifest), "--output", "/outside-project.json"])
    with pytest.raises(CliError, match="must differ from the input manifest"):
        main(["compare", str(manifest), "--output", str(manifest)])


def test_difference_validation_rejects_boolean_schema_version() -> None:
    with pytest.raises(CliError, match="does not match checked-in schema"):
        manifest_cli._validate_difference_document(
            json.dumps(
                {
                    "record_kind": "compatibility_differences",
                    "schema_version": True,
                    "differences": [],
                }
            )
        )


def test_manifest_output_is_repo_scoped_nofollow_and_atomic(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = cli_workspace / "work.jsonl"
    old_value = dumps_jsonl(
        ManifestProvenance.create(generated_at="2026-08-08T09:00:00Z", tool_version="old/1"),
        (
            CompatibilityRow(
                Classification.preserve(),
                production_capture=_capture(ObservationOrigin.PRODUCTION, "/old"),
            ),
        ),
    )
    new_value = dumps_jsonl(
        ManifestProvenance.create(generated_at="2026-08-08T10:00:00Z", tool_version="new/1"),
        (
            CompatibilityRow(
                Classification.preserve(),
                production_capture=_capture(ObservationOrigin.PRODUCTION, "/new"),
            ),
        ),
    )
    output.write_text(old_value, encoding="utf-8")
    pending = ROOT / ".tmp/compatibility/manifest.atomic-failure.pending"
    pending.unlink(missing_ok=True)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated manifest replace failure")

    monkeypatch.setattr(manifest_cli.secrets, "token_hex", lambda _length: "atomic-failure")
    monkeypatch.setattr(manifest_cli.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated manifest replace failure"):
        manifest_cli._write(output, new_value)

    assert output.read_text(encoding="utf-8") == old_value
    assert not pending.exists()
    outside = ROOT.parent / f"outside-manifest-{uuid.uuid4().hex}.jsonl"
    with pytest.raises(CliError, match="below the project root"):
        manifest_cli._write(outside, new_value)
    assert not outside.exists()

    target = cli_workspace / "target.jsonl"
    link = cli_workspace / "link.jsonl"
    target.write_text(old_value, encoding="utf-8")
    link.symlink_to(target)
    with pytest.raises(CliError, match="must not be a symlink"):
        manifest_cli._write(link, new_value)


def test_resume_discards_work_ahead_and_rejects_tampered_counters() -> None:
    completed = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url="https://datatalks.club/done",
        status=200,
        response_count=2,
        transfer_bytes=10,
    )
    ahead = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url="https://datatalks.club/ahead",
        status=200,
        response_count=1,
        transfer_bytes=4,
    )
    rows = (
        CompatibilityRow(Classification.preserve(), production_capture=ahead),
        CompatibilityRow(Classification.preserve(), production_capture=completed),
    )
    checkpoint = CrawlCheckpoint(
        policy_sha256="a" * 64,
        seeds_sha256="b" * 64,
        pending_urls=(ahead.requested_url,),
        completed_urls=(completed.requested_url,),
        response_count=2,
        total_bytes=10,
    )

    assert manifest_cli._resume_captures(rows, checkpoint) == [completed]
    tampered_responses = CrawlCheckpoint(
        policy_sha256=checkpoint.policy_sha256,
        seeds_sha256=checkpoint.seeds_sha256,
        pending_urls=checkpoint.pending_urls,
        completed_urls=checkpoint.completed_urls,
        response_count=0,
        total_bytes=10,
    )
    with pytest.raises(CliError, match="response count does not match"):
        manifest_cli._resume_captures(rows, tampered_responses)
    tampered_bytes = CrawlCheckpoint(
        policy_sha256=checkpoint.policy_sha256,
        seeds_sha256=checkpoint.seeds_sha256,
        pending_urls=checkpoint.pending_urls,
        completed_urls=checkpoint.completed_urls,
        response_count=2,
        total_bytes=0,
    )
    with pytest.raises(CliError, match="byte count does not match"):
        manifest_cli._resume_captures(rows, tampered_bytes)
