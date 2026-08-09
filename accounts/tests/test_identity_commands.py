from __future__ import annotations

import json
import stat
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

SNAPSHOT_ID = "a" * 64


class IdentityCommandArtifactTests(TestCase):
    def test_dry_run_report_is_restricted_redacted_and_project_local(self) -> None:
        artifact_root = Path(settings.BASE_DIR) / ".tmp"
        artifact_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="identity-command-",
            dir=artifact_root,
        ) as directory:
            output = Path(directory) / "dry-run.json"

            call_command(
                "reconcile_accounts",
                snapshot_id=SNAPSHOT_ID,
                output=str(output),
                stdout=StringIO(),
            )

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["write_performed"])
            self.assertFalse(report["outbound_side_effects"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)

    def test_mapping_must_be_restricted_and_artifacts_cannot_escape_tmp(self) -> None:
        artifact_root = Path(settings.BASE_DIR) / ".tmp"
        artifact_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="identity-mapping-",
            dir=artifact_root,
        ) as directory:
            mapping = Path(directory) / "mapping.json"
            mapping.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "snapshot_id": SNAPSHOT_ID,
                        "review_reference": "synthetic-command-review",
                        "mappings": [],
                    }
                ),
                encoding="utf-8",
            )
            mapping.chmod(0o644)

            with self.assertRaisesMessage(CommandError, "permissions"):
                call_command(
                    "reconcile_accounts",
                    snapshot_id=SNAPSHOT_ID,
                    mapping=str(mapping),
                    apply=True,
                    stdout=StringIO(),
                )

            with self.assertRaisesMessage(CommandError, "project-local .tmp"):
                call_command(
                    "reconcile_accounts",
                    snapshot_id=SNAPSHOT_ID,
                    output=str(Path(settings.BASE_DIR) / "identity-report.json"),
                    stdout=StringIO(),
                )
