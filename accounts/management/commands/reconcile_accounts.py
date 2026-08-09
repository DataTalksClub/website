from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from accounts.identity_values import validate_snapshot_id
from accounts.reconciliation import (
    ReconciliationBlocked,
    ReconciliationError,
    apply_reviewed_mapping,
    dry_run_reconciliation,
    parse_mapping_document,
    validate_rollback_window,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_ROOT = (PROJECT_ROOT / ".tmp").resolve()


def _artifact_path(value: str, *, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ARTIFACT_ROOT):
        raise CommandError("identity artifacts must stay below project-local .tmp")
    if must_exist and not resolved.is_file():
        raise CommandError("identity artifact is unavailable")
    return resolved


def _read_mapping(value: str) -> dict[str, Any]:
    path = _artifact_path(value, must_exist=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise CommandError("mapping artifact permissions must be 0600 or stricter")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError("mapping artifact is not valid JSON") from error
    if not isinstance(payload, dict):
        raise CommandError("mapping artifact must contain one JSON object")
    return payload


class Command(BaseCommand):
    help = "Dry-run, apply, or rollback-check reviewed account mappings."

    def add_arguments(self, parser):
        parser.add_argument("--snapshot-id", required=True)
        parser.add_argument("--mapping")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--rollback-check", action="store_true")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        del args
        if options["apply"] and options["rollback_check"]:
            raise CommandError("choose either apply or rollback-check")
        try:
            snapshot_id = validate_snapshot_id(options["snapshot_id"])
            report = self._run(snapshot_id=snapshot_id, options=options)
        except ReconciliationBlocked as error:
            safe_report = {
                "status": "quarantined",
                "conflicts": list(error.conflicts),
            }
            self._write_report(safe_report, options.get("output"))
            raise CommandError("account mapping was quarantined") from error
        except (ReconciliationError, ValueError) as error:
            raise CommandError(str(error)) from error
        self._write_report(report, options.get("output"))

    def _run(self, *, snapshot_id: str, options: dict[str, Any]):
        if not options["apply"] and not options["rollback_check"]:
            if options.get("mapping"):
                raise CommandError("dry-run does not consume a mapping artifact")
            return dry_run_reconciliation(snapshot_id=snapshot_id)

        mapping_path = options.get("mapping")
        if not mapping_path:
            raise CommandError("apply and rollback-check require --mapping")
        plan = parse_mapping_document(_read_mapping(mapping_path))
        if plan.snapshot_id != snapshot_id:
            raise CommandError("mapping snapshot differs from --snapshot-id")
        if options["apply"]:
            return apply_reviewed_mapping(plan)
        return validate_rollback_window(plan)

    def _write_report(self, report: dict[str, Any], output: str | None) -> None:
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if output is None:
            self.stdout.write(rendered, ending="")
            return
        path = _artifact_path(output, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        if path.exists():
            path.chmod(0o600)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            output_file.write(rendered)
        path.chmod(0o600)
        self.stdout.write("identity report written below project-local .tmp")
