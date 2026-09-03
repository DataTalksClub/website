"""Run the digest-bound Django URL/link/SEO parity gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from compatibility.django import DjangoTargetCollector
from compatibility.expectations import ApprovedExpectationSet, loads_expectations
from compatibility.models import loads_jsonl
from compatibility.parity import evaluate_parity
from compatibility.report import ParityStatus, TargetBinding, dumps_report
from compatibility.schema import load_schema, validate_jsonl_records, validate_record
from core.runtime_endpoints import canonical_origin

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPOSITORY_ROOT / "_docs" / "compatibility"
DEFAULT_MANIFEST = CONTRACT_ROOT / "legacy-manifest.jsonl"
DEFAULT_DIFFERENCES = CONTRACT_ROOT / "legacy-manifest-differences.json"
DEFAULT_PUBLIC_CONTRACTS = CONTRACT_ROOT / "public-contracts.jsonl"
DIFFERENCE_SCHEMA = CONTRACT_ROOT / "legacy-manifest-differences.schema.json"
PUBLIC_CONTRACT_SCHEMA = CONTRACT_ROOT / "public-contracts.schema.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / ".tmp" / "compatibility" / "seo-parity-report.json"


def _existing_file(value: str) -> Path:
    path = Path(value)
    resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
    if not resolved.is_file() or resolved.is_symlink():
        raise argparse.ArgumentTypeError("input must be a regular existing file")
    return resolved


def _scratch_output(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    if candidate.is_symlink():
        raise argparse.ArgumentTypeError("output must not be a symlink")
    target = candidate.parent.resolve() / candidate.name
    scratch = (REPOSITORY_ROOT / ".tmp").resolve()
    if target == scratch or scratch not in target.parents:
        raise argparse.ArgumentTypeError("output must be below project .tmp")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifacts(manifest: Path, differences: Path, contracts: Path) -> None:
    loads_jsonl(manifest.read_text(encoding="utf-8"))
    validate_record(
        json.loads(differences.read_text(encoding="utf-8")),
        load_schema(DIFFERENCE_SCHEMA),
    )
    validate_jsonl_records(
        contracts.read_text(encoding="utf-8"),
        load_schema(PUBLIC_CONTRACT_SCHEMA),
    )


def _write_report(path: Path, value: str) -> None:
    try:
        path = _scratch_output(path)
    except argparse.ArgumentTypeError as exc:
        raise CommandError("compatibility report output must be below project .tmp") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CommandError("compatibility report output must not be a symlink")
    staging = path.parent / f"report.{secrets.token_hex(8)}.pending"
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
        os.replace(staging, path)
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise


class Command(BaseCommand):
    help = "Compare independently approved expectations with the target Django app"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--manifest", type=_existing_file, default=DEFAULT_MANIFEST)
        parser.add_argument("--differences", type=_existing_file, default=DEFAULT_DIFFERENCES)
        parser.add_argument(
            "--public-contracts", type=_existing_file, default=DEFAULT_PUBLIC_CONTRACTS
        )
        parser.add_argument("--expectations", type=_existing_file)
        parser.add_argument("--output", type=_scratch_output, default=DEFAULT_OUTPUT)
        parser.add_argument("--scope", action="append", default=[])
        parser.add_argument("--target-id", default="django-local")
        parser.add_argument("--target-origin", default=canonical_origin())
        parser.add_argument("--release-id", default=settings.APP_VERSION)
        parser.add_argument("--parser-version", default="compatibility-parser-v1")
        parser.add_argument("--route-sha256", required=True)
        parser.add_argument("--asset-sha256", required=True)
        parser.add_argument("--projection-sha256", required=True)

    def handle(self, *args: object, **options: object) -> None:
        del args
        manifest = options["manifest"]
        differences = options["differences"]
        public_contracts = options["public_contracts"]
        output_value = options["output"]
        assert isinstance(manifest, Path)
        assert isinstance(differences, Path)
        assert isinstance(public_contracts, Path)
        if not isinstance(output_value, (str, os.PathLike)):
            raise CommandError("compatibility report output is invalid")
        try:
            output = _scratch_output(output_value)
        except argparse.ArgumentTypeError as exc:
            raise CommandError("compatibility report output must be below project .tmp") from exc
        try:
            _validate_artifacts(manifest, differences, public_contracts)
            artifact_digests = (
                _sha256(manifest),
                _sha256(differences),
                _sha256(public_contracts),
            )
            expectation_path = options.get("expectations")
            if expectation_path is None:
                expectation_set = ApprovedExpectationSet.create(
                    manifest_sha256=artifact_digests[0],
                    differences_sha256=artifact_digests[1],
                    public_contracts_sha256=artifact_digests[2],
                )
            else:
                assert isinstance(expectation_path, Path)
                expectation_set = loads_expectations(expectation_path.read_text(encoding="utf-8"))
                if (
                    expectation_set.manifest_sha256,
                    expectation_set.differences_sha256,
                    expectation_set.public_contracts_sha256,
                ) != artifact_digests:
                    raise CommandError("approved expectations have stale artifact digests")

            requested_scope = options["scope"]
            assert isinstance(requested_scope, list)
            scope = tuple(str(item) for item in requested_scope) or (
                expectation_set.scopes or ("checked-real",)
            )
            selected = tuple(
                item for item in expectation_set.expectations if item.source_scope in set(scope)
            )
            hosts = {
                hostname for item in selected if (hostname := urlsplit(item.public_url).hostname)
            }
            observations = (
                tuple(
                    DjangoTargetCollector(allowed_hosts=hosts).observe(item.public_url)
                    for item in selected
                )
                if selected
                else ()
            )
            target = TargetBinding(
                target_id=str(options["target_id"]),
                target_origin=str(options["target_origin"]),
                release_id=str(options["release_id"]),
                parser_version=str(options["parser_version"]),
                route_sha256=str(options["route_sha256"]),
                asset_sha256=str(options["asset_sha256"]),
                projection_sha256=str(options["projection_sha256"]),
            )
            report = evaluate_parity(
                expectation_set,
                observations,
                target=target,
                scope=scope,
            )
            _write_report(output, dumps_report(report))
        except CommandError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError("compatibility gate input is invalid") from exc

        self.stdout.write(
            f"{report.status.value}: {report.matched_count}/{report.expectation_count} "
            f"matched, {report.blocking_finding_count} blocking, "
            f"{report.warning_finding_count} warnings; report={output}"
        )
        if report.status is not ParityStatus.PASS:
            raise CommandError("compatibility gate blocked")
