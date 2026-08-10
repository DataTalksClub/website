from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from content_sync.dtc_content import DtcContentValidationError
from content_sync.dtc_content.repository import (
    DtcContentCheckoutError,
    verify_dtc_content_checkout,
)


class Command(BaseCommand):
    help = "Verify one immutable DataTalksClub/content checkout without network access"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--checkout", required=True, type=Path)
        parser.add_argument("--expected-commit", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        try:
            verified = verify_dtc_content_checkout(
                options["checkout"],
                expected_commit=options["expected_commit"],
            )
        except DtcContentCheckoutError as error:
            raise CommandError(error.code) from error
        except DtcContentValidationError as error:
            diagnostic = error.diagnostics[0]
            raise CommandError(f"{diagnostic.code}:{diagnostic.source_path}") from error
        bundle = verified.bundle
        report = {
            "adapter_type": bundle.adapter_type,
            "bundle_sha256": bundle.bundle_sha256,
            "commit_sha": verified.commit_sha,
            "counts": dict(bundle.counts),
            "editorial_overlay_path": bundle.editorial_overlay_path,
            "editorial_overlay_sha256": bundle.editorial_overlay_sha256,
            "migration_sha256": bundle.migration_sha256,
            "projection_parity": (
                verified.projection_parity.as_dict()
                if verified.projection_parity is not None
                else None
            ),
            "public_contracts_sha256": bundle.public_contracts_sha256,
            "repaired_baseline_commit": bundle.repaired_baseline_commit,
            "repaired_baseline_tree": bundle.repaired_baseline_tree,
            "repair_manifest_path": bundle.repair_manifest_path,
            "repair_manifest_sha256": bundle.repair_manifest_sha256,
            "replacement_attestation_sha256": bundle.replacement_attestation_sha256,
            "schema_version": bundle.schema_version,
            "source_tree_sha": verified.tree_sha,
            "status": "PASS",
        }
        self.stdout.write(json.dumps(report, separators=(",", ":"), sort_keys=True))
