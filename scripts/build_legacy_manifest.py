#!/usr/bin/env python3
"""Build, validate, merge, and compare the versioned legacy compatibility manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.robotparser
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urldefrag, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from compatibility.contracts import (  # noqa: E402
    dumps_public_contract_inventory,
    load_public_contract_inventory,
    public_contract_inventory_sha256,
)
from compatibility.crawler import (  # noqa: E402
    CRAWLER_TOOL_VERSION,
    CRAWLER_USER_AGENT,
    AllowlistRule,
    BoundedHttpTransport,
    CrawlBounds,
    CrawlCheckpoint,
    CrawlPolicy,
    CrawlPolicyError,
    LocalTreeSource,
    crawl_http,
    inventory_local_tree,
    load_checkpoint,
    save_checkpoint,
)
from compatibility.diff import (  # noqa: E402
    diff_rows,
    diff_source_production,
    dumps_differences,
    merge_captures,
)
from compatibility.models import (  # noqa: E402
    Capture,
    CompatibilityRow,
    ManifestProvenance,
    ObservationOrigin,
    SourceRevision,
    dumps_jsonl,
    loads_jsonl,
)
from compatibility.redaction import percent_encode_filesystem_path  # noqa: E402
from compatibility.schema import (  # noqa: E402
    RecordSchemaError,
    load_schema,
    validate_jsonl_records,
    validate_record,
)
from compatibility.source_config import (  # noqa: E402
    PINNED_LEGACY_SOURCES,
    generated_contract_kind,
    generated_public_path,
    pinned_source,
)

DEFAULT_BASELINE = REPOSITORY_ROOT / "_docs/compatibility/generated-path-baseline.jsonl"
DEFAULT_COURSE_ROUTES = REPOSITORY_ROOT / "_docs/compatibility/course-route-contracts.json"
DEFAULT_WORKSPACE = REPOSITORY_ROOT / ".tmp/legacy-compatibility-sources"
DEFAULT_CHECKPOINT = REPOSITORY_ROOT / ".tmp/compatibility/production.checkpoint.json"
DEFAULT_PRODUCTION_WORK = REPOSITORY_ROOT / ".tmp/compatibility/production.jsonl"
DEFAULT_PUBLIC_CONTRACTS = REPOSITORY_ROOT / "_docs/compatibility/public-contracts.jsonl"
PUBLIC_CONTRACT_SCHEMA = REPOSITORY_ROOT / "_docs/compatibility/public-contracts.schema.json"
DIFFERENCE_SCHEMA = REPOSITORY_ROOT / "_docs/compatibility/legacy-manifest-differences.schema.json"


class CliError(RuntimeError):
    pass


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _scratch_path(value: str) -> Path:
    path = _path(value).resolve()
    scratch = (REPOSITORY_ROOT / ".tmp").resolve()
    if path == scratch or scratch not in path.parents:
        raise argparse.ArgumentTypeError("scratch path must be below project .tmp")
    return path


def _repository_output_path(value: str) -> Path:
    candidate = _path(value)
    if candidate.is_symlink():
        raise argparse.ArgumentTypeError("comparison output must not be a symlink")
    parent = candidate.parent.resolve()
    repository = REPOSITORY_ROOT.resolve()
    target = parent / candidate.name
    if target == repository or repository not in target.parents:
        raise argparse.ArgumentTypeError("comparison output must be below the project root")
    return target


def _manifest_output_path(value: str) -> Path:
    candidate = _path(value)
    if candidate.is_symlink():
        raise argparse.ArgumentTypeError("manifest output must not be a symlink")
    parent = candidate.parent.resolve()
    repository = REPOSITORY_ROOT.resolve()
    target = parent / candidate.name
    if target == repository or repository not in target.parents:
        raise argparse.ArgumentTypeError("manifest output must be below the project root")
    return target


def _scratch_manifest_path(value: str) -> Path:
    candidate = _path(value)
    if candidate.is_symlink():
        raise argparse.ArgumentTypeError("manifest output must not be a symlink")
    target = _scratch_path(value)
    return _manifest_output_path(str(target))


def _contract_output_path(value: str) -> Path:
    candidate = _path(value)
    if candidate.is_symlink():
        raise argparse.ArgumentTypeError("public-contract output must not be a symlink")
    parent = candidate.parent.resolve()
    repository = REPOSITORY_ROOT.resolve()
    target = parent / candidate.name
    if target == repository or repository not in target.parents:
        raise argparse.ArgumentTypeError("public-contract output must be below the project root")
    return target


def _write(path: Path, value: str) -> None:
    """Atomically write one strict manifest through project-local staging."""

    loads_jsonl(value)
    try:
        target = _manifest_output_path(str(path))
    except argparse.ArgumentTypeError as exc:
        raise CliError(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise CliError("manifest output must not be a symlink")
    scratch = REPOSITORY_ROOT / ".tmp/compatibility"
    scratch.mkdir(parents=True, exist_ok=True)
    staging = scratch / f"manifest.{secrets.token_hex(8)}.pending"
    descriptor = os.open(
        staging,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise


def _resume_captures(
    rows: Sequence[CompatibilityRow], checkpoint: CrawlCheckpoint
) -> list[Capture]:
    """Select the last checkpointed capture set, discarding atomic work-ahead rows."""

    captures = [row.production_capture for row in rows if row.production_capture is not None]
    by_url = {capture.requested_url: capture for capture in captures}
    if len(by_url) != len(captures):
        raise CliError("production work manifest has duplicate captures")
    completed = set(checkpoint.completed_urls)
    missing = completed - set(by_url)
    if missing:
        raise CliError("production work manifest is behind checkpoint")
    ahead = set(by_url) - completed
    if not ahead <= set(checkpoint.pending_urls):
        raise CliError("production work manifest has captures outside checkpoint")
    selected = [by_url[url] for url in checkpoint.completed_urls]
    if checkpoint.response_count != sum(capture.response_count for capture in selected):
        raise CliError("production checkpoint response count does not match captures")
    if checkpoint.total_bytes != sum(capture.transfer_bytes for capture in selected):
        raise CliError("production checkpoint byte count does not match captures")
    return selected


def _write_public_contracts(path: Path, value: str) -> None:
    """Atomically write canonical contracts via project-local scratch space."""

    target = _contract_output_path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise CliError("public-contract output must not be a symlink")
    scratch = REPOSITORY_ROOT / ".tmp/compatibility"
    scratch.mkdir(parents=True, exist_ok=True)
    staging = scratch / f"public-contracts.{secrets.token_hex(8)}.pending"
    descriptor = os.open(
        staging,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_comparison(path: Path, value: str) -> None:
    """Atomically write a schema-validated comparison inside the repository."""

    target = _repository_output_path(str(path))
    _validate_difference_document(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise CliError("comparison output must not be a symlink")
    scratch = REPOSITORY_ROOT / ".tmp/compatibility"
    scratch.mkdir(parents=True, exist_ok=True)
    staging = scratch / f"differences.{secrets.token_hex(8)}.pending"
    descriptor = os.open(
        staging,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_difference_document(value: str) -> None:
    """Validate the complete emitted document against its checked-in schema."""

    try:
        document = json.loads(value)
        validate_record(document, load_schema(DIFFERENCE_SCHEMA))
    except (OSError, RecordSchemaError, json.JSONDecodeError) as error:
        raise CliError("generated difference document does not match checked-in schema") from error


def _policy() -> CrawlPolicy:
    return CrawlPolicy(
        rules=(
            AllowlistRule(
                "https",
                "datatalks.club",
                443,
                ("/",),
                ("document_type", "level", "q"),
            ),
            AllowlistRule(
                "https",
                "courses.datatalks.club",
                443,
                ("/",),
                ("next",),
            ),
        ),
        bounds=CrawlBounds(
            max_response_bytes=16 * 1024 * 1024,
            max_total_bytes=1024 * 1024 * 1024,
            request_interval_seconds=0.1,
            retry_backoff_seconds=0.25,
            max_retry_after_seconds=30.0,
        ),
        discover_references=False,
        robots_required=True,
    )


def _provenance(generated_at: str, policy: CrawlPolicy) -> ManifestProvenance:
    revisions = tuple(
        SourceRevision(source.source_id, source.repository, source.revision)
        for source in PINNED_LEGACY_SOURCES
    )
    return ManifestProvenance.create(
        generated_at=generated_at,
        tool_version=CRAWLER_TOOL_VERSION,
        source_revisions=revisions,
        production_origins=("https://courses.datatalks.club/", "https://datatalks.club/"),
        allowlisted_hosts=tuple(sorted(policy.internal_hosts)),
        crawl_policy_sha256=policy.fingerprint,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CliError("contract artifact contains a duplicate JSON key")
        result[key] = value
    return result


def _load_strict_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CliError(f"{label} is not strict JSON") from error


def _load_strict_jsonl(path: Path, label: str) -> tuple[dict[str, object], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise CliError(f"{label} cannot be read") from error
    if not lines or any(not line for line in lines):
        raise CliError(f"{label} must contain nonblank JSONL rows")
    records: list[dict[str, object]] = []
    for line in lines:
        try:
            value = json.loads(line, object_pairs_hook=_unique_json_object)
        except json.JSONDecodeError as error:
            raise CliError(f"{label} contains invalid JSONL") from error
        if not isinstance(value, dict):
            raise CliError(f"{label} rows must be objects")
        records.append(value)
    return tuple(records)


def _validated_baseline_records(path: Path) -> tuple[dict[str, object], ...]:
    records = _load_strict_jsonl(path, "generated path baseline")
    if path.resolve() != DEFAULT_BASELINE.resolve():
        canonical = _load_strict_jsonl(DEFAULT_BASELINE, "canonical generated path baseline")
        if records != canonical:
            raise CliError("generated path baseline does not match the canonical artifact")
    if len(records) != 2_937:
        raise CliError("generated path baseline row count is invalid")
    expected_keys = {
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
    source_by_id = {source.source_id: source for source in PINNED_LEGACY_SOURCES}
    identities: set[tuple[str, str]] = set()
    for record in records:
        if set(record) != expected_keys:
            raise CliError("generated path baseline row shape is invalid")
        source_id = record["source_id"]
        if type(source_id) is not str or source_id not in source_by_id:
            raise CliError("generated path baseline source is invalid")
        source = source_by_id[str(source_id)]
        if source.output_directory is None or record["source_revision"] != source.revision:
            raise CliError("generated path baseline revision is invalid")
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise CliError("generated path baseline schema version is invalid")
        if record["classification"] != "preserve":
            raise CliError("generated path baseline classification is invalid")
        if type(record["expected_status"]) is not int or record["expected_status"] != 200:
            raise CliError("generated path baseline status is invalid")
        if type(record["machine_contract_seed"]) is not bool:
            raise CliError("generated path baseline machine marker is invalid")
        content_sha256 = record["content_sha256"]
        if type(content_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None:
            raise CliError("generated path baseline content digest is invalid")
        source_path = record["source_path"]
        if type(source_path) is not str:
            raise CliError("generated path baseline source path is invalid")
        relative_prefix = f"{source.output_directory}/"
        relative = source_path.removeprefix(relative_prefix)
        if (
            not source_path.startswith(relative_prefix)
            or not relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or "\\" in source_path
        ):
            raise CliError("generated path baseline source path is invalid")
        public_path = record["public_path"]
        if type(public_path) is not str or public_path != generated_public_path(source, relative):
            raise CliError("generated path baseline public path is invalid")
        if record["public_path_percent_encoded"] != percent_encode_filesystem_path(public_path):
            raise CliError("generated path baseline encoded path is invalid")
        if record["contract_kind"] != generated_contract_kind(relative):
            raise CliError("generated path baseline contract kind is invalid")
        identity = (source_id, public_path)
        if identity in identities:
            raise CliError("generated path baseline contains a duplicate public path")
        identities.add(identity)
    return records


def _validated_course_routes(path: Path) -> dict[str, object]:
    value = _load_strict_json(path, "course route contract")
    canonical = _load_strict_json(DEFAULT_COURSE_ROUTES, "canonical course route contract")
    if value != canonical:
        raise CliError("course route contract does not match the canonical artifact")
    if not isinstance(value, dict):
        raise CliError("course route contract must be an object")
    expected_keys = {
        "adoption_inventory",
        "authenticated_production_probe_reason",
        "authenticated_production_probes_performed",
        "classification_default",
        "route_count",
        "routes",
        "schema_version",
        "source_id",
        "source_revision",
    }
    if set(value) != expected_keys:
        raise CliError("course route contract shape is invalid")
    course_source = pinned_source("dtc-course-platform")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["source_id"] != course_source.source_id
        or value["source_revision"] != course_source.revision
        or value["classification_default"] != "preserve"
        or value["authenticated_production_probes_performed"] is not False
    ):
        raise CliError("course route contract provenance or probe policy is invalid")
    routes = value["routes"]
    if (
        type(value["route_count"]) is not int
        or not isinstance(routes, list)
        or value["route_count"] != len(routes)
        or len(routes) != 89
    ):
        raise CliError("course route contract count is invalid")
    expected_route_keys = {
        "authenticated_production_probe",
        "callback",
        "classification",
        "contract_kind",
        "example_path",
        "expected_status",
        "host",
        "name",
        "route_pattern",
        "source_example_path",
        "source_id",
        "source_name",
        "source_route_pattern",
        "source_revision",
        "surface",
        "urlconf",
    }
    identities: set[tuple[object, ...]] = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) != expected_route_keys:
            raise CliError("course route row shape is invalid")
        if (
            route["authenticated_production_probe"] != "not_performed"
            or route["classification"] != "preserve"
            or route["expected_status"] is not None
            or route["host"] != "courses.datatalks.club"
            or route["source_id"] != course_source.source_id
            or route["source_revision"] != course_source.revision
            or route["contract_kind"] not in {"api", "calendar", "html"}
        ):
            raise CliError("course route row policy is invalid")
        for key in (
            "callback",
            "example_path",
            "route_pattern",
            "source_example_path",
            "source_route_pattern",
            "surface",
            "urlconf",
        ):
            if type(route[key]) is not str or not route[key]:
                raise CliError("course route row value is invalid")
        if any(
            not str(route[key]).startswith("/")
            for key in (
                "example_path",
                "route_pattern",
                "source_example_path",
                "source_route_pattern",
            )
        ):
            raise CliError("course route row path is invalid")
        if route["name"] is not None and (type(route["name"]) is not str or not route["name"]):
            raise CliError("course route row name is invalid")
        if route["source_name"] is not None and (
            type(route["source_name"]) is not str or not route["source_name"]
        ):
            raise CliError("course source route name is invalid")
        identity = (
            route["urlconf"],
            route["route_pattern"],
            route["name"],
            route["callback"],
        )
        if identity in identities:
            raise CliError("course route contract contains a duplicate route")
        identities.add(identity)
    if Counter(route["surface"] for route in routes) != {
        "Accounts": 9,
        "Compatibility API": 29,
        "Studio Courses": 26,
        "Public courses": 25,
    }:
        raise CliError("course route surface counts are invalid")
    studio_routes = [route for route in routes if route["surface"] == "Studio Courses"]
    if any(
        not (
            str(route["route_pattern"]) == "/studio/courses"
            or str(route["route_pattern"]).startswith("/studio/courses/")
        )
        or not (
            str(route["example_path"]) == "/studio/courses"
            or str(route["example_path"]).startswith("/studio/courses/")
        )
        or not str(route["name"]).startswith("studio_courses_")
        or route["source_route_pattern"]
        != (
            "/cadmin/"
            if route["route_pattern"] == "/studio/courses"
            else f"/cadmin/{str(route['route_pattern']).removeprefix('/studio/courses/')}"
        )
        or route["source_example_path"]
        != (
            "/cadmin/"
            if route["example_path"] == "/studio/courses"
            else f"/cadmin/{str(route['example_path']).removeprefix('/studio/courses/')}"
        )
        or route["source_name"] != f"cadmin_{str(route['name']).removeprefix('studio_courses_')}"
        for route in studio_routes
    ):
        raise CliError("Studio Courses canonical route contract is invalid")
    if any(
        route["source_route_pattern"] != route["route_pattern"]
        or route["source_example_path"] != route["example_path"]
        or route["source_name"] != route["name"]
        for route in routes
        if route["surface"] != "Studio Courses"
    ):
        raise CliError("unchanged course route source identity is invalid")
    return value


def _source_urls(baseline: Path) -> tuple[str, ...]:
    records = _validated_baseline_records(baseline)
    source_by_id = {source.source_id: source for source in PINNED_LEGACY_SOURCES}
    urls: set[str] = set()
    for record in records:
        source_id = record["source_id"]
        if type(source_id) is not str:
            raise CliError("generated baseline source_id must be a string")
        source = source_by_id[source_id]
        origin = urlsplit(source.public_base_url)
        urls.add(f"{origin.scheme}://{origin.netloc}{record['public_path_percent_encoded']}")
    return tuple(sorted(urls))


def _seed_urls(baseline: Path, course_routes: Path) -> tuple[str, ...]:
    urls = set(_source_urls(baseline))
    for source in PINNED_LEGACY_SOURCES:
        origin = urlsplit(source.public_base_url)
        for contract in source.machine_contracts:
            urls.add(f"{origin.scheme}://{origin.netloc}{contract}")
    # The course route inventory is provenance and later authenticated-test input, not an
    # authorization to probe illustrative/user-specific examples in production.
    _validated_course_routes(course_routes)
    return tuple(sorted({urldefrag(url).url for url in urls}))


def _source(args: argparse.Namespace) -> int:
    policy = _policy()
    selected = (
        PINNED_LEGACY_SOURCES if args.source_id == "all" else (pinned_source(args.source_id),)
    )
    captures: list[Capture] = []
    for source in selected:
        if source.output_directory is None:
            continue
        root = args.workspace / "sources" / source.source_id / source.output_directory
        captures.extend(
            inventory_local_tree(
                LocalTreeSource(
                    root=root,
                    public_base_url=source.public_base_url,
                    repository=source.repository,
                    revision=source.revision,
                    source_path_prefix=source.output_directory,
                ),
                policy=policy,
            )
        )
    provenance = ManifestProvenance.create(
        generated_at=args.generated_at,
        tool_version=CRAWLER_TOOL_VERSION,
        source_revisions=(
            SourceRevision(source.source_id, source.repository, source.revision)
            for source in selected
        ),
        allowlisted_hosts=tuple(sorted(policy.internal_hosts)),
        crawl_policy_sha256=policy.fingerprint,
    )
    rows = merge_captures(captures, ())
    _write(args.output, dumps_jsonl(provenance, rows))
    print(f"source manifest: {len(rows)} rows")
    return 0


def _production(args: argparse.Namespace) -> int:
    policy = _policy()
    seeds = _seed_urls(args.baseline, args.course_routes)
    completed = []
    checkpoint = None
    provenance = _provenance(args.generated_at, policy)
    preflight_transport = BoundedHttpTransport(policy)
    robots_verifier = _verify_robots(seeds, preflight_transport)
    # Robots preflight is a separate control transaction. Capture accounting and
    # the one-invocation deadline begin only after every seed origin is verified.
    transport = BoundedHttpTransport(policy, robots_verifier=robots_verifier)
    crawl_deadline = time.monotonic() + policy.bounds.max_run_seconds
    if args.work_manifest.exists() or args.checkpoint.exists():
        if not args.work_manifest.exists() or not args.checkpoint.exists():
            raise CliError("resume requires both work manifest and checkpoint")
        recorded_provenance, rows = loads_jsonl(args.work_manifest.read_text(encoding="utf-8"))
        if recorded_provenance != provenance:
            raise CliError("resume provenance mismatch")
        checkpoint = load_checkpoint(args.checkpoint)
        completed = _resume_captures(rows, checkpoint)
    while True:
        run = crawl_http(
            seeds=seeds,
            policy=policy,
            origin=ObservationOrigin.PRODUCTION,
            checkpoint=checkpoint,
            completed_captures=completed,
            max_new_captures=args.chunk_size,
            transport=transport,
            deadline=crawl_deadline,
            robots_verified=True,
        )
        completed.extend(run.captures)
        rows = merge_captures((), completed)
        _write(args.work_manifest, dumps_jsonl(provenance, rows))
        save_checkpoint(args.checkpoint, run.checkpoint)
        checkpoint = run.checkpoint
        print(f"production checkpoint: {len(completed)} rows; complete={run.complete}")
        if run.complete or not args.run_to_completion:
            return 0


def _verify_robots(
    seeds: tuple[str, ...], transport: BoundedHttpTransport
) -> Callable[[str], None]:
    parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
    for seed in seeds:
        parts = urlsplit(seed)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in parsers:
            continue
        robots_url = f"{origin}/robots.txt"
        response = transport.fetch(robots_url)
        if response.status == 404:
            parsers[origin] = None
            continue
        if response.status != 200:
            raise CliError("robots policy could not be verified")
        try:
            body = response.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CliError("robots policy is not UTF-8") from error
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(body.splitlines())
        parsers[origin] = parser

    def verify(url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in parsers:
            raise CrawlPolicyError("redirect_origin_robots_not_verified")
        selected_parser = parsers[origin]
        if selected_parser is not None and not selected_parser.can_fetch(CRAWLER_USER_AGENT, url):
            raise CrawlPolicyError("robots_policy_disallows_redirect")

    for seed in seeds:
        try:
            verify(seed)
        except CrawlPolicyError as error:
            raise CliError("robots policy disallows a production seed") from error
    return verify


def _merge(args: argparse.Namespace) -> int:
    source_provenance, source_rows = loads_jsonl(args.source.read_text(encoding="utf-8"))
    production_provenance, production_rows = loads_jsonl(
        args.production.read_text(encoding="utf-8")
    )
    if source_provenance.production_origins:
        raise CliError("source merge input must not claim production origins")
    if not production_provenance.production_origins:
        raise CliError("production merge input must declare production origins")
    for field_name in (
        "generated_at",
        "tool_version",
        "source_revisions",
        "allowlisted_hosts",
        "crawl_policy_sha256",
    ):
        if getattr(source_provenance, field_name) != getattr(production_provenance, field_name):
            raise CliError(f"merge provenance mismatch: {field_name}")
    if args.generated_at != source_provenance.generated_at:
        raise CliError("merge generated-at does not match input provenance")
    expected_production_provenance = _provenance(args.generated_at, _policy())
    expected_source_provenance = ManifestProvenance.create(
        generated_at=expected_production_provenance.generated_at,
        tool_version=expected_production_provenance.tool_version,
        source_revisions=expected_production_provenance.source_revisions,
        allowlisted_hosts=expected_production_provenance.allowlisted_hosts,
        crawl_policy_sha256=expected_production_provenance.crawl_policy_sha256,
    )
    if source_provenance != expected_source_provenance:
        raise CliError("source merge provenance does not match current capture configuration")
    if production_provenance != expected_production_provenance:
        raise CliError("production merge provenance does not match current capture configuration")
    if any(row.source_capture is None or row.production_capture is not None for row in source_rows):
        raise CliError("source merge input contains an unexpected capture row")
    if any(
        row.production_capture is None or row.source_capture is not None for row in production_rows
    ):
        raise CliError("production merge input contains an unexpected capture row")
    source = [row.source_capture for row in source_rows if row.source_capture is not None]
    production = [
        row.production_capture for row in production_rows if row.production_capture is not None
    ]
    if not args.allow_missing:
        expected_source_urls = set(_source_urls(args.baseline))
        observed_source_urls = {capture.requested_url for capture in source}
        if observed_source_urls != expected_source_urls:
            raise CliError("source merge input does not cover the exact generated baseline set")
        expected_production_urls = set(_seed_urls(args.baseline, args.course_routes))
        observed_production_urls = {capture.requested_url for capture in production}
        if observed_production_urls != expected_production_urls:
            raise CliError("production merge input does not cover the exact authorized seed set")
    rows = merge_captures(source, production)
    missing = [
        row.public_url
        for row in rows
        if row.source_capture is not None and row.production_capture is None
    ]
    if missing and not args.allow_missing:
        raise CliError("merged manifest has source-only or production-only rows")
    provenance = ManifestProvenance.create(
        generated_at=args.generated_at,
        tool_version=production_provenance.tool_version,
        source_revisions=source_provenance.source_revisions,
        production_origins=production_provenance.production_origins,
        allowlisted_hosts=production_provenance.allowlisted_hosts,
        crawl_policy_sha256=production_provenance.crawl_policy_sha256,
    )
    _write(args.output, dumps_jsonl(provenance, rows))
    print(f"merged manifest: {len(rows)} rows")
    return 0


def _public_contracts(args: argparse.Namespace) -> int:
    contracts = load_public_contract_inventory()
    value = dumps_public_contract_inventory(contracts)
    try:
        validate_jsonl_records(value, load_schema(PUBLIC_CONTRACT_SCHEMA))
    except (OSError, RecordSchemaError) as error:
        raise CliError("generated public contracts do not match checked-in schema") from error
    output = _contract_output_path(str(args.output))
    digest = public_contract_inventory_sha256(contracts)
    if args.check:
        if output.read_text(encoding="utf-8") != value:
            raise CliError("public-contract artifact is stale; regenerate it without --check")
        print(f"valid public contracts: {len(contracts)} rows sha256={digest}")
        return 0
    _write_public_contracts(output, value)
    print(f"public contracts: {len(contracts)} rows sha256={digest}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    value = args.manifest.read_text(encoding="utf-8")
    _, rows = loads_jsonl(value)
    record_count = validate_jsonl_records(value, load_schema())
    if record_count != len(rows) + 1:
        raise CliError("manifest schema record count mismatch")
    print(f"valid manifest: {len(rows)} rows; schema_records={record_count}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    _, rows = loads_jsonl(args.manifest.read_text(encoding="utf-8"))
    differences = diff_source_production(rows)
    if args.output.resolve() == args.manifest.resolve():
        raise CliError("comparison output must differ from the input manifest")
    _write_comparison(args.output, dumps_differences(differences))
    print(f"source/production differences: {len(differences)}")
    return 1 if differences and args.fail_on_difference else 0


def _compare_versions(args: argparse.Namespace) -> int:
    _, before = loads_jsonl(args.before.read_text(encoding="utf-8"))
    _, after = loads_jsonl(args.after.read_text(encoding="utf-8"))
    differences = diff_rows(before, after)
    resolved_output = args.output.resolve()
    if resolved_output in {args.before.resolve(), args.after.resolve()}:
        raise CliError("comparison output must differ from input manifests")
    _write_comparison(args.output, dumps_differences(differences))
    print(f"manifest differences: {len(differences)}")
    return 1 if differences and args.fail_on_difference else 0


def _freshness(args: argparse.Namespace) -> int:
    provenance, _ = loads_jsonl(args.manifest.read_text(encoding="utf-8"))
    captured = datetime.fromisoformat(provenance.generated_at.replace("Z", "+00:00"))
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(UTC)
    age = (now - captured).total_seconds()
    if age < 0 or age > args.max_age_hours * 3600:
        raise CliError("manifest is outside the freshness window")
    print(f"fresh manifest: age_seconds={int(age)}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    source = commands.add_parser("source", help="inventory deterministic generated trees")
    source.add_argument("--source-id", default="all")
    source.add_argument("--workspace", type=_scratch_path, default=DEFAULT_WORKSPACE)
    source.add_argument("--generated-at", required=True)
    source.add_argument("--output", type=_manifest_output_path, required=True)
    source.set_defaults(handler=_source)
    production = commands.add_parser("production", help="run/resume bounded production crawl")
    production.add_argument("--baseline", type=_path, default=DEFAULT_BASELINE)
    production.add_argument("--course-routes", type=_path, default=DEFAULT_COURSE_ROUTES)
    production.add_argument("--generated-at", required=True)
    production.add_argument("--checkpoint", type=_scratch_path, default=DEFAULT_CHECKPOINT)
    production.add_argument(
        "--work-manifest", type=_scratch_manifest_path, default=DEFAULT_PRODUCTION_WORK
    )
    production.add_argument("--chunk-size", type=int, default=50)
    production.add_argument("--run-to-completion", action="store_true")
    production.set_defaults(handler=_production)
    merge = commands.add_parser("merge", help="combine source and production without overwrite")
    merge.add_argument("--source", type=_path, required=True)
    merge.add_argument("--production", type=_path, required=True)
    merge.add_argument("--baseline", type=_path, default=DEFAULT_BASELINE)
    merge.add_argument("--course-routes", type=_path, default=DEFAULT_COURSE_ROUTES)
    merge.add_argument("--generated-at", required=True)
    merge.add_argument("--output", type=_manifest_output_path, required=True)
    merge.add_argument("--allow-missing", action="store_true")
    merge.set_defaults(handler=_merge)
    public_contracts = commands.add_parser(
        "public-contracts", help="regenerate or validate the canonical public-contract inventory"
    )
    public_contracts.add_argument(
        "--output", type=_contract_output_path, default=DEFAULT_PUBLIC_CONTRACTS
    )
    public_contracts.add_argument("--check", action="store_true")
    public_contracts.set_defaults(handler=_public_contracts)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=_path)
    validate.set_defaults(handler=_validate)
    compare = commands.add_parser("compare")
    compare.add_argument("manifest", type=_path)
    compare.add_argument("--output", type=_repository_output_path, required=True)
    compare.add_argument("--fail-on-difference", action="store_true")
    compare.set_defaults(handler=_compare)
    versions = commands.add_parser("compare-versions")
    versions.add_argument("before", type=_path)
    versions.add_argument("after", type=_path)
    versions.add_argument("--output", type=_repository_output_path, required=True)
    versions.add_argument("--fail-on-difference", action="store_true")
    versions.set_defaults(handler=_compare_versions)
    freshness = commands.add_parser("freshness")
    freshness.add_argument("manifest", type=_path)
    freshness.add_argument("--now", required=True)
    freshness.add_argument("--max-age-hours", type=float, required=True)
    freshness.set_defaults(handler=_freshness)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "chunk_size", 1) <= 0:
        raise CliError("chunk size must be positive")
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CliError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
