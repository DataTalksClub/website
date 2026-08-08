#!/usr/bin/env python3
"""Create clean pinned legacy checkouts and deterministic generated-site inputs.

Every mutable artifact stays below the current website repository's ``.tmp`` directory.  The
helper never reads a sibling checkout and refuses an existing checkout unless its origin, detached
HEAD, and tracked/untracked status match the code-owned source configuration exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from compatibility.source_config import (  # noqa: E402
    FAQ_FROZEN_GENERATION_TIME,
    FAQ_WEBSITE_UV_LOCK_SHA256,
    PINNED_LEGACY_SOURCES,
    RUSTKYLL_0_4_6_LINUX_AMD64_SHA256,
    PinnedLegacySource,
    SourceKind,
    generated_contract_kind,
    generated_public_path,
    pinned_source,
)

SCRATCH_ROOT = (REPOSITORY_ROOT / ".tmp").resolve()
DEFAULT_WORKSPACE = SCRATCH_ROOT / "legacy-compatibility-sources"
RUSTKYLL_0_4_6_URL = (
    "https://github.com/alexeygrigorev/rustkyll/releases/download/v0.4.6/rustkyll-linux-amd64"
)
MAX_BUILD_TOOL_BYTES = 32 * 1024 * 1024
MAX_SOURCE_TREE_FILES = 20_000
MAX_SOURCE_TREE_BYTES = 512 * 1024 * 1024
CONTRACT_ROOT = REPOSITORY_ROOT / "_docs/compatibility"
BASELINE_DATE = "2026-08-08"
ARTIFACT_NAMES = (
    "generated-path-baseline.jsonl",
    "faq-fragment-contracts.jsonl",
    "podwiki-graph-fragment-contracts.jsonl",
    "machine-contract-samples.json",
    "course-route-contracts.json",
    "source-build-provenance.json",
)
FAQ_ID = re.compile(r"^[0-9A-Za-z]{10}$")
BUILD_RUNTIME_ROOT = DEFAULT_WORKSPACE / "runtime"
FAQ_RUNNER = """
import importlib.util
from datetime import datetime
from pathlib import Path

module_path = Path("website/generate_website.py").resolve()
spec = importlib.util.spec_from_file_location("pinned_faq_generator", module_path)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load pinned FAQ generator")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2000, 1, 1, 0, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)

module.datetime = FrozenDateTime
module.main()
""".strip()


class BuildError(RuntimeError):
    """A value-free pinned-source preparation or build failure."""


def _run(arguments: list[str], *, cwd: Path, environment: dict[str, str] | None = None) -> str:
    if environment is None:
        environment = _build_environment()
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"command failed for {cwd.name}: {arguments[0]}")
    return result.stdout


def _workspace(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    path = path.resolve()
    if path == SCRATCH_ROOT or SCRATCH_ROOT not in path.parents:
        raise argparse.ArgumentTypeError("workspace must be a child of the project .tmp directory")
    return path


def _checkout_path(workspace: Path, source: PinnedLegacySource) -> Path:
    return workspace / "sources" / source.source_id


def _normalize_repository(value: str) -> str:
    return value.removesuffix(".git").rstrip("/")


def verify_checkout(checkout: Path, source: PinnedLegacySource) -> None:
    if not (checkout / ".git").exists():
        raise BuildError(f"pinned checkout is missing for {source.source_id}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=checkout).strip()
    if head != source.revision:
        raise BuildError(f"pinned checkout revision mismatch for {source.source_id}")
    branch = _run(["git", "branch", "--show-current"], cwd=checkout).strip()
    if branch:
        raise BuildError(f"pinned checkout is not detached for {source.source_id}")
    origin = _run(["git", "remote", "get-url", "origin"], cwd=checkout).strip()
    if _normalize_repository(origin) != _normalize_repository(source.repository):
        raise BuildError(f"pinned checkout origin mismatch for {source.source_id}")
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=checkout,
    )
    if status:
        raise BuildError(f"pinned checkout is dirty for {source.source_id}")


def prepare_checkout(workspace: Path, source: PinnedLegacySource) -> Path:
    checkout = _checkout_path(workspace, source)
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                source.repository,
                str(checkout),
            ],
            cwd=REPOSITORY_ROOT,
        )
        _run(["git", "checkout", "--detach", source.revision], cwd=checkout)
    verify_checkout(checkout, source)
    return checkout


def _download_rustkyll(workspace: Path) -> Path:
    target = workspace / "tools" / "rustkyll-v0.4.6-linux-amd64"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            RUSTKYLL_0_4_6_URL,
            headers={"User-Agent": "dtc-compatibility-source-builder/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except ValueError as error:
                    raise BuildError("downloaded Rustkyll has invalid content length") from error
                if not 1 <= declared_length <= MAX_BUILD_TOOL_BYTES:
                    raise BuildError("downloaded Rustkyll exceeds the size limit")
            payload = response.read(MAX_BUILD_TOOL_BYTES + 1)
        if not payload or len(payload) > MAX_BUILD_TOOL_BYTES:
            raise BuildError("downloaded Rustkyll exceeds the size limit")
        if hashlib.sha256(payload).hexdigest() != RUSTKYLL_0_4_6_LINUX_AMD64_SHA256:
            raise BuildError("downloaded Rustkyll digest mismatch")
        target.write_bytes(payload)
        target.chmod(0o755)
    if not 1 <= target.stat().st_size <= MAX_BUILD_TOOL_BYTES:
        raise BuildError("cached Rustkyll exceeds the size limit")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != RUSTKYLL_0_4_6_LINUX_AMD64_SHA256:
        raise BuildError("cached Rustkyll digest mismatch")
    return target


def _build_environment(runtime_root: Path = BUILD_RUNTIME_ROOT) -> dict[str, str]:
    """Return the complete, credential-free environment for child processes.

    Pinned source repositories are public and their builds do not need caller credentials.  In
    particular, copying ``os.environ`` here would expose AWS, GitHub, database, Django, package
    registry, and credential-helper values to untrusted legacy build code.  Keep only the caller's
    executable search path, then provide isolated runtime directories and fixed locale/time values.
    """

    runtime_root = runtime_root.resolve()
    if runtime_root == SCRATCH_ROOT or SCRATCH_ROOT not in runtime_root.parents:
        raise BuildError("build runtime must be a child of the project .tmp directory")
    home = runtime_root / "home"
    cache = runtime_root / "cache"
    temporary = runtime_root / "tmp"
    for directory in (home, cache, temporary):
        directory.mkdir(parents=True, exist_ok=True)

    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "JEKYLL_ENV": "production",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONHASHSEED": "0",
        "PYTHONUTF8": "1",
        "TMPDIR": str(temporary),
        "TZ": "UTC",
        "UV_CACHE_DIR": str(cache / "uv"),
        "UV_NO_CONFIG": "1",
        "XDG_CACHE_HOME": str(cache / "xdg"),
    }


def _reset_generated_output(checkout: Path, source: PinnedLegacySource) -> None:
    if source.output_directory is None:
        return
    output = checkout / source.output_directory
    tracked = _run(
        ["git", "ls-files", "--", source.output_directory],
        cwd=checkout,
    ).strip()
    if tracked:
        raise BuildError(f"generated output overlaps tracked files for {source.source_id}")
    if output.is_symlink():
        raise BuildError(f"generated output is a symbolic link for {source.source_id}")
    if output.exists():
        if not output.is_dir():
            raise BuildError(f"generated output is not a directory for {source.source_id}")
        shutil.rmtree(output)


def build_source(workspace: Path, source: PinnedLegacySource, checkout: Path) -> Path | None:
    if source.source_kind is SourceKind.DJANGO_ROUTE_CONTRACTS:
        return None
    verify_checkout(checkout, source)
    _reset_generated_output(checkout, source)
    environment = _build_environment(workspace / "runtime")
    if source.source_kind is SourceKind.RUSTKYLL_RELEASE:
        tool = _download_rustkyll(workspace)
        arguments = [str(tool), "build"]
        if source.path_prefix != "/":
            arguments.extend(("--baseurl", source.path_prefix))
        _run(arguments, cwd=checkout, environment=environment)
    elif source.source_kind is SourceKind.FAQ_PYTHON:
        uv = shutil.which("uv")
        if uv is None:
            raise BuildError("uv is required to build the FAQ source")
        lock_digest = hashlib.sha256((checkout / "website/uv.lock").read_bytes()).hexdigest()
        if lock_digest != FAQ_WEBSITE_UV_LOCK_SHA256:
            raise BuildError("FAQ lockfile digest mismatch")
        environment["DTC_FAQ_FROZEN_GENERATION_TIME"] = FAQ_FROZEN_GENERATION_TIME
        _run(
            [
                uv,
                "run",
                "--project",
                str(checkout / "website"),
                "--frozen",
                "python",
                "-c",
                FAQ_RUNNER,
            ],
            cwd=checkout,
            environment=environment,
        )
    elif source.source_kind is SourceKind.RUSTKYLL_PYPI:
        uvx = shutil.which("uvx")
        uv = shutil.which("uv")
        if uvx is None or uv is None:
            raise BuildError("uv and uvx are required to build the Podwiki source")
        _run(
            [
                uvx,
                "--no-config",
                "--from",
                f"rustkyll=={source.build_tool_version}",
                "rustkyll",
                "build",
                "--baseurl",
                source.path_prefix,
            ],
            cwd=checkout,
            environment=environment,
        )
        _run(
            [
                uv,
                "run",
                "--project",
                str(REPOSITORY_ROOT),
                "--frozen",
                "python",
                "scripts/fix_absolute_urls.py",
                "--baseurl",
                source.path_prefix,
            ],
            cwd=checkout,
            environment=environment,
        )
        _run(
            [
                uv,
                "run",
                "--project",
                str(REPOSITORY_ROOT),
                "--frozen",
                "python",
                "scripts/prune_sitemap.py",
                "--baseurl",
                source.path_prefix,
            ],
            cwd=checkout,
            environment=environment,
        )
    else:  # pragma: no cover - the enum exhausts this in supported Python versions.
        raise BuildError(f"unsupported build kind for {source.source_id}")

    output = checkout / str(source.output_directory)
    if not output.is_dir() or not any(path.is_file() for path in output.rglob("*")):
        raise BuildError(f"generated output is missing for {source.source_id}")
    verify_checkout(checkout, source)
    return output


def _tree_digest(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for path in _bounded_files(root):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    return count, digest.hexdigest()


def _bounded_files(root: Path) -> tuple[Path, ...]:
    """Return a stable, bounded list of regular files below one real directory."""

    if root.is_symlink() or not root.is_dir():
        raise BuildError(f"source tree is not a real directory: {root.name}")
    resolved_root = root.resolve()
    candidates: list[Path] = []
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BuildError(f"source tree contains a symbolic link: {root.name}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved_root not in resolved.parents:
            raise BuildError(f"source tree path escapes its root: {root.name}")
        size = path.stat().st_size
        if size > MAX_BUILD_TOOL_BYTES:
            raise BuildError(f"source tree file exceeds the size limit: {root.name}")
        total_bytes += size
        if total_bytes > MAX_SOURCE_TREE_BYTES:
            raise BuildError(f"source tree exceeds the byte limit: {root.name}")
        candidates.append(path)
        if len(candidates) > MAX_SOURCE_TREE_FILES:
            raise BuildError(f"source tree exceeds the file limit: {root.name}")
    return tuple(sorted(candidates, key=lambda path: path.relative_to(root).parts))


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _jsonl_bytes(rows: list[dict[str, object]]) -> bytes:
    return (
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            for row in rows
        )
    ).encode()


def _generated_path_rows(
    workspace: Path,
) -> tuple[list[dict[str, object]], dict[str, Path]]:
    rows: list[dict[str, object]] = []
    outputs: dict[str, Path] = {}
    for source in PINNED_LEGACY_SOURCES:
        if source.output_directory is None:
            continue
        checkout = _checkout_path(workspace, source)
        verify_checkout(checkout, source)
        output = checkout / source.output_directory
        outputs[source.source_id] = output
        for path in _bounded_files(output):
            relative = path.relative_to(output).as_posix()
            public_path = generated_public_path(source, relative)
            rows.append(
                {
                    "classification": "preserve",
                    "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "contract_kind": generated_contract_kind(relative),
                    "expected_status": 200,
                    "machine_contract_seed": public_path in source.machine_contracts,
                    "public_path": public_path,
                    "public_path_percent_encoded": quote(public_path, safe="/@"),
                    "schema_version": 1,
                    "source_id": source.source_id,
                    "source_path": f"{source.output_directory}/{relative}",
                    "source_revision": source.revision,
                }
            )
    rows.sort(key=lambda row: (str(row["source_id"]), str(row["public_path"])))
    return rows, outputs


def _frontmatter(path: Path) -> dict[str, Any]:
    payload = path.read_text(encoding="utf-8")
    if not payload.startswith("---"):
        raise BuildError("FAQ question is missing YAML frontmatter")
    parts = payload.split("---", 2)
    if len(parts) != 3:
        raise BuildError("FAQ question has malformed YAML frontmatter")
    document = yaml.safe_load(parts[1])
    if not isinstance(document, dict):
        raise BuildError("FAQ question frontmatter is not an object")
    return document


def _faq_fragment_rows(workspace: Path) -> list[dict[str, object]]:
    source = pinned_source("dtc-faq")
    checkout = _checkout_path(workspace, source)
    questions = checkout / "_questions"
    rows: list[dict[str, object]] = []
    for path in _bounded_files(questions):
        if path.suffix != ".md":
            continue
        relative = path.relative_to(checkout).as_posix()
        question_relative = path.relative_to(questions)
        if len(question_relative.parts) < 3:
            raise BuildError("FAQ question path is missing its course or section")
        course_slug = question_relative.parts[0]
        fragment_id = str(_frontmatter(path).get("id", ""))
        if FAQ_ID.fullmatch(fragment_id) is None:
            raise BuildError("FAQ question has an invalid stable id")
        public_path = f"/faq/{course_slug}.html"
        reference = f"{public_path}#{fragment_id}"
        rows.append(
            {
                "classification": "preserve",
                "course_slug": course_slug,
                "fragment_id": fragment_id,
                "public_path": public_path,
                "public_path_with_fragment": reference,
                "public_path_with_fragment_percent_encoded": (
                    f"{quote(public_path, safe='/')}#{quote(fragment_id, safe='')}"
                ),
                "schema_version": 1,
                "source_id": source.source_id,
                "source_path": relative,
                "source_revision": source.revision,
            }
        )
    rows.sort(key=lambda row: Path(str(row["source_path"])).parts)
    if len({(row["public_path"], row["fragment_id"]) for row in rows}) != len(rows):
        raise BuildError("FAQ question fragments are not unique")
    return rows


def _bounded_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"source JSON is not a regular file: {path.name}")
    if path.stat().st_size > MAX_BUILD_TOOL_BYTES:
        raise BuildError(f"source JSON exceeds the size limit: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildError(f"source JSON is invalid: {path.name}") from error


def _podwiki_fragment_rows(workspace: Path) -> list[dict[str, object]]:
    source = pinned_source("dtc-podwiki")
    graph_path = _checkout_path(workspace, source) / "_site/graph/graph.json"
    graph = _bounded_json(graph_path)
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise BuildError("Podwiki graph does not contain a node list")
    rows: list[dict[str, object]] = []
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            raise BuildError("Podwiki graph contains a non-object node")
        fragment_id = node.get("id")
        target_type = node.get("type")
        target_url = node.get("url")
        if (
            not isinstance(fragment_id, str)
            or not fragment_id
            or not isinstance(target_type, str)
            or not target_type
            or not isinstance(target_url, str)
            or not target_url
        ):
            raise BuildError("Podwiki graph node is missing contract fields")
        reference = f"/podwiki/graph/#{fragment_id}"
        rows.append(
            {
                "classification": "preserve",
                "fragment_id": fragment_id,
                "public_path": "/podwiki/graph/",
                "public_path_with_fragment": reference,
                "public_path_with_fragment_percent_encoded": (
                    f"/podwiki/graph/#{quote(fragment_id, safe='')}"
                ),
                "schema_version": 1,
                "source_id": source.source_id,
                "source_path": "_site/graph/graph.json",
                "source_revision": source.revision,
                "target_type": target_type,
                "target_url": target_url,
            }
        )
    rows.sort(key=lambda row: str(row["fragment_id"]))
    if len({row["fragment_id"] for row in rows}) != len(rows):
        raise BuildError("Podwiki graph fragment ids are not unique")
    return rows


def _course_route_document(workspace: Path) -> dict[str, object]:
    source = pinned_source("dtc-course-platform")
    verify_checkout(_checkout_path(workspace, source), source)
    from scripts.render_course_platform_inventory import route_entries

    rows: list[dict[str, object]] = []
    for route in route_entries():
        route_pattern = f"/{route.route}"
        contract_kind = (
            "api"
            if route.surface == "Compatibility API"
            else "calendar"
            if route_pattern.endswith("calendar.ics")
            else "html"
        )
        rows.append(
            {
                "authenticated_production_probe": "not_performed",
                "callback": route.callback,
                "classification": "preserve",
                "contract_kind": contract_kind,
                "example_path": route.example_path(),
                "expected_status": None,
                "host": "courses.datatalks.club",
                "name": route.name or None,
                "route_pattern": route_pattern,
                "source_id": source.source_id,
                "source_revision": source.revision,
                "surface": route.surface,
                "urlconf": route.module,
            }
        )
    return {
        "adoption_inventory": "_docs/adoption/course-platform/behavior-inventory.md",
        "authenticated_production_probe_reason": (
            "No production credentials or production learner/operator data were used for "
            "baseline capture."
        ),
        "authenticated_production_probes_performed": False,
        "classification_default": "preserve",
        "route_count": len(rows),
        "routes": rows,
        "schema_version": 1,
        "source_id": source.source_id,
        "source_revision": source.revision,
    }


def _machine_contract_document(
    generated_rows: list[dict[str, object]], course_document: dict[str, object]
) -> dict[str, object]:
    generated = {
        (str(row["source_id"]), str(row["public_path"])): str(row["source_path"])
        for row in generated_rows
    }
    course_rows = course_document["routes"]
    if not isinstance(course_rows, list):
        raise BuildError("course route inventory is malformed")
    course_paths = {
        str(row["example_path"])
        for row in course_rows
        if isinstance(row, dict) and isinstance(row.get("example_path"), str)
    }
    samples: list[dict[str, object]] = []
    for source in PINNED_LEGACY_SOURCES:
        for contract in source.machine_contracts:
            parsed = urlsplit(contract)
            generated_path = generated.get((source.source_id, parsed.path))
            is_course = source.source_kind is SourceKind.DJANGO_ROUTE_CONTRACTS
            samples.append(
                {
                    "classification": "preserve",
                    "contract_kind": (
                        "fragment" if parsed.fragment else "query" if parsed.query else "path"
                    ),
                    "course_route_contract_present": (
                        contract in course_paths if is_course else None
                    ),
                    "fragment": parsed.fragment,
                    "path": parsed.path,
                    "public_contract": contract,
                    "query": parsed.query,
                    "schema_version": 1,
                    "source_id": source.source_id,
                    "source_output_path": generated_path,
                    "source_output_present": generated_path is not None,
                    "source_revision": source.revision,
                }
            )
    return {
        "classification_default": "preserve",
        "sample_count": len(samples),
        "samples": samples,
        "schema_version": 1,
    }


def _checked_provenance(outputs: dict[str, Path], source_path_rows: int) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for source in PINNED_LEGACY_SOURCES:
        output = outputs.get(source.source_id)
        file_count, tree_sha256 = (0, None) if output is None else _tree_digest(output)
        records.append(
            {
                "build_tool": source.build_tool,
                "build_tool_sha256": source.build_tool_sha256,
                "build_tool_version": source.build_tool_version,
                "deterministic_overrides": list(source.deterministic_overrides),
                "output_directory": source.output_directory,
                "output_file_count": file_count,
                "output_tree_sha256": tree_sha256,
                "path_prefix": source.path_prefix,
                "public_base_url": source.public_base_url,
                "repository": source.repository,
                "revision": source.revision,
                "source_id": source.source_id,
                "source_kind": source.source_kind.value,
            }
        )
    return {
        "baseline_date": BASELINE_DATE,
        "classification_default": "preserve",
        "records": sorted(records, key=lambda record: str(record["source_id"])),
        "schema_version": 1,
        "source_path_rows": source_path_rows,
    }


def build_artifact_payloads(workspace: Path) -> dict[str, bytes]:
    """Regenerate every checked source-derived artifact without network access."""

    for source in PINNED_LEGACY_SOURCES:
        verify_checkout(_checkout_path(workspace, source), source)
    generated_rows, outputs = _generated_path_rows(workspace)
    faq_rows = _faq_fragment_rows(workspace)
    podwiki_rows = _podwiki_fragment_rows(workspace)
    course_document = _course_route_document(workspace)
    machine_document = _machine_contract_document(generated_rows, course_document)
    provenance = _checked_provenance(outputs, len(generated_rows))
    return {
        "generated-path-baseline.jsonl": _jsonl_bytes(generated_rows),
        "faq-fragment-contracts.jsonl": _jsonl_bytes(faq_rows),
        "podwiki-graph-fragment-contracts.jsonl": _jsonl_bytes(podwiki_rows),
        "machine-contract-samples.json": _json_bytes(machine_document),
        "course-route-contracts.json": _json_bytes(course_document),
        "source-build-provenance.json": _json_bytes(provenance),
    }


def check_artifact_payloads(payloads: dict[str, bytes]) -> None:
    stale = [
        name
        for name in ARTIFACT_NAMES
        if not (CONTRACT_ROOT / name).is_file()
        or (CONTRACT_ROOT / name).read_bytes() != payloads[name]
    ]
    if stale:
        raise BuildError(f"checked source artifacts are stale: {', '.join(stale)}")


def write_artifact_payloads(workspace: Path, payloads: dict[str, bytes]) -> None:
    """Atomically replace fixed checked artifacts using project-local staging files."""

    staging = workspace / "artifact-staging" / secrets.token_hex(12)
    if staging.is_symlink():
        raise BuildError("artifact staging directory is a symbolic link")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        for name in ARTIFACT_NAMES:
            target = CONTRACT_ROOT / name
            if target.is_symlink():
                raise BuildError(f"checked artifact is a symbolic link: {name}")
            staged = staging / name
            staged.write_bytes(payloads[name])
            os.replace(staged, target)
    finally:
        shutil.rmtree(staging)


def _write_provenance(workspace: Path, records: list[dict[str, object]]) -> Path:
    target = workspace / "source-build-provenance.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": sorted(records, key=lambda item: str(item["source_id"])),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=_workspace, default=DEFAULT_WORKSPACE)
    parser.add_argument(
        "--source",
        action="append",
        choices=[source.source_id for source in PINNED_LEGACY_SOURCES],
        help="prepare/build only this source; repeat for more than one",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument(
        "--artifacts-only",
        action="store_true",
        help="regenerate checked artifacts from existing pinned checkouts without rebuilding",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="offline byte-for-byte check of artifacts from existing pinned checkouts",
    )
    arguments = parser.parse_args(argv)

    workspace = Path(arguments.workspace)
    if arguments.source and (arguments.artifacts_only or arguments.check):
        parser.error("--source cannot be combined with --artifacts-only or --check")
    if arguments.check or arguments.artifacts_only:
        payloads = build_artifact_payloads(workspace)
        if arguments.check:
            check_artifact_payloads(payloads)
            print(f"checked {len(payloads)} source artifacts")
        else:
            write_artifact_payloads(workspace, payloads)
            print(f"wrote {len(payloads)} source artifacts")
        return 0

    workspace.mkdir(parents=True, exist_ok=True)
    selected = (
        tuple(pinned_source(source_id) for source_id in arguments.source)
        if arguments.source
        else PINNED_LEGACY_SOURCES
    )
    records: list[dict[str, object]] = []
    for source in selected:
        checkout = prepare_checkout(workspace, source)
        output = None if arguments.prepare_only else build_source(workspace, source, checkout)
        file_count, tree_sha256 = (0, "") if output is None else _tree_digest(output)
        records.append(
            {
                "source_id": source.source_id,
                "repository": source.repository,
                "revision": source.revision,
                "public_base_url": source.public_base_url,
                "path_prefix": source.path_prefix,
                "build_tool": source.build_tool,
                "build_tool_version": source.build_tool_version,
                "build_tool_sha256": source.build_tool_sha256,
                "deterministic_overrides": list(source.deterministic_overrides),
                "output_directory": source.output_directory,
                "output_file_count": file_count,
                "output_tree_sha256": tree_sha256,
                "prepared_only": arguments.prepare_only,
            }
        )
        print(f"{source.source_id}: revision={source.revision} files={file_count}")
    provenance = _write_provenance(workspace, records)
    print(f"provenance={provenance.relative_to(REPOSITORY_ROOT)}")
    if not arguments.source and not arguments.prepare_only:
        payloads = build_artifact_payloads(workspace)
        write_artifact_payloads(workspace, payloads)
        print(f"wrote {len(payloads)} source artifacts")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
