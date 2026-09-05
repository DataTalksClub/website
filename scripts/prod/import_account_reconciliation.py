#!/usr/bin/env python3
"""Dry-run, apply, or rollback-check reviewed account-merge mappings.

One-time.  This never runs against a moving upstream -- it applies one
reviewed, checked-in mapping document, produced by a human review of the
dry-run's candidate groups, exactly once.  See
``scripts/prod/__init__.py`` for what the two sync models mean, and
``_docs/runbooks/production-data-migration.md`` /
``_docs/runbooks/ingest-script-inventory.md`` (CMP learner accounts, 4.3) for
the full reconciliation journey this script is the entry point for.

The migration runbook calls this step out by name as the one step in the
whole migration with **no rollback** -- merging two accounts reparents their
course history, and ``--rollback-check`` only proves the *evidence* to
reverse it is still intact (aliases, unchanged relationship checksums), it
does not reverse anything itself.

Replaces the retired ``accounts.management.commands.reconcile_accounts``
management command; the merge logic itself is not a management command
either now, it lives in ``scripts.prod.account_reconciliation`` -- see that
package's docstring for why the durable ``AccountReconciliationRun`` and
``AccountIdentityAlias``/``AccountIdentityQuarantine`` tables still have to
be real Django models registered under ``accounts`` (a Django model needs an
installed app; ``scripts/prod`` is plain scripts, not one), even though every
line that reads or writes them now lives here.

Idempotent and concurrency-safe.  Replaying the same ``--snapshot-id`` and
mapping document returns the first run's cached result rather than
re-merging; two simultaneous applies of the same mapping result in exactly
one merge, the other safely receiving the winner's result (proven by
``accounts/tests/test_account_reconciliation.py``, including a real two-
thread race, not just by reading the code).

    uv run --frozen python scripts/prod/import_account_reconciliation.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --snapshot-id <64-hex-char export digest>

    uv run --frozen python scripts/prod/import_account_reconciliation.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --snapshot-id <snapshot> --apply \\
        --mapping .tmp/identity/reviewed-mapping.json \\
        --output .tmp/identity/apply-report.json

    uv run --frozen python scripts/prod/import_account_reconciliation.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --snapshot-id <snapshot> --rollback-check \\
        --mapping .tmp/identity/reviewed-mapping.json
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"

ARTIFACT_ROOT = (PROJECT_ROOT / ".tmp").resolve()


def _artifact_path(value: str, *, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ARTIFACT_ROOT):
        raise ImportAccountReconciliationError(
            "identity artifacts must stay below project-local .tmp"
        )
    if must_exist and not resolved.is_file():
        raise ImportAccountReconciliationError("identity artifact is unavailable")
    return resolved


def _read_mapping(value: str) -> dict[str, Any]:
    path = _artifact_path(value, must_exist=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ImportAccountReconciliationError(
            "mapping artifact permissions must be 0600 or stricter"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImportAccountReconciliationError("mapping artifact is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ImportAccountReconciliationError("mapping artifact must contain one JSON object")
    return payload


def _write_report(report: dict[str, Any], output: str | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    path = _artifact_path(output, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    path.chmod(0o600)


class ImportAccountReconciliationError(RuntimeError):
    """A bounded refusal -- never renders a source value (an email, a name)."""


def _run(*, snapshot_id: str, options: argparse.Namespace) -> dict[str, Any]:
    from accounts.identity_values import validate_snapshot_id
    from scripts.prod.account_reconciliation import (
        apply_reviewed_mapping,
        dry_run_reconciliation,
        parse_mapping_document,
        validate_rollback_window,
    )

    validated_snapshot_id = validate_snapshot_id(snapshot_id)
    if not options.apply and not options.rollback_check:
        if options.mapping:
            raise ImportAccountReconciliationError("dry-run does not consume a mapping artifact")
        return dry_run_reconciliation(snapshot_id=validated_snapshot_id)

    if not options.mapping:
        raise ImportAccountReconciliationError("apply and rollback-check require --mapping")
    plan = parse_mapping_document(_read_mapping(options.mapping))
    if plan.snapshot_id != validated_snapshot_id:
        raise ImportAccountReconciliationError("mapping snapshot differs from --snapshot-id")
    if options.apply:
        return apply_reviewed_mapping(plan)
    return validate_rollback_window(plan)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(parser)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--mapping")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback-check", action="store_true")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    options = parser.parse_args(argv)
    if options.apply and options.rollback_check:
        print(json.dumps({"error": "choose either apply or rollback-check"}, indent=2))
        return 1
    configure_target(parser, options)

    from scripts.prod.account_reconciliation import ReconciliationBlocked, ReconciliationError

    # Every path below -- the reconciliation call itself, and writing either
    # its report or a blocked mapping's redacted quarantine report -- shares
    # one refusal path, so an artifact-path mistake (output escaping
    # project-local .tmp, a too-permissive mapping file) is always a clean
    # JSON error and exit 1, never a raw traceback.
    try:
        report = _run(snapshot_id=options.snapshot_id, options=options)
    except ReconciliationBlocked as error:
        try:
            safe_report = {"status": "quarantined", "conflicts": list(error.conflicts)}
            _write_report(safe_report, options.output)
        except (ReconciliationError, ImportAccountReconciliationError, ValueError) as write_error:
            print(json.dumps({"error": str(write_error)}, indent=2))
            return 1
        print(json.dumps({"error": "account mapping was quarantined"}, indent=2))
        return 1
    except (
        ReconciliationError,
        ImportAccountReconciliationError,
        ValueError,
    ) as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    try:
        _write_report(report, options.output)
    except (ReconciliationError, ImportAccountReconciliationError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
