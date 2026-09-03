"""Event identity replay, registration-aggregate coverage, and the named gap.

The adapters work; the per-event mapping decisions are the backlog.  These tests
lock in that a run reports the ratio rather than a bare success, and that event
content is declared missing rather than quietly absent.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


def _entry_point_names() -> list[str]:
    return sorted(
        module.name for module in pkgutil.iter_modules([str(PROD_ROOT)]) if not module.ispkg
    )


class EventImportTests(TestCase):
    """The identity replay, the coverage report, and the named content gap."""

    def test_the_identity_manifest_replays_without_creating_a_row(self) -> None:
        """The test database already holds the reviewed set, so importing is a reconcile."""

        from events.models import Event, EventAlias
        from scripts.prod.import_events import import_identities

        before = (Event.objects.count(), EventAlias.objects.count())

        report = import_identities(apply=True)

        self.assertTrue(report["replayed"])
        self.assertEqual(report["events_created"], 0)
        self.assertEqual(report["aliases_created"], 0)
        self.assertEqual((Event.objects.count(), EventAlias.objects.count()), before)

    def test_a_dry_run_writes_nothing(self) -> None:
        from scripts.prod.import_events import import_identities

        report = import_identities(apply=False)

        self.assertFalse(report["applied"])

    def test_the_coverage_report_states_the_activated_ratio(self) -> None:
        """An operator must see 3 of 383, not a bare success."""

        from scripts.prod.import_events import activation_coverage

        coverage = activation_coverage(
            source_report={"luma": {"events": 174}, "eventbrite": {"events": 209}},
            staged={
                "sources": {
                    "luma": {"explicit_mapping_total": 3, "legacy_review_required_total": 171},
                    "eventbrite": {
                        "explicit_mapping_total": 0,
                        "legacy_review_required_total": 209,
                    },
                }
            },
        )

        self.assertEqual(coverage["provider_events"], 383)
        self.assertEqual(coverage["activated"], 3)
        self.assertEqual(coverage["review_required"], 380)
        self.assertIn("3 of 383", coverage["summary"])
        self.assertIn("380 await mapping review", coverage["summary"])

    def test_event_content_is_a_named_gap_rather_than_a_silent_omission(self) -> None:
        """Its only current source is the legacy site, which is not permitted."""

        from scripts.prod.import_events import EVENT_CONTENT

        self.assertFalse(EVENT_CONTENT["imported"])
        self.assertEqual(EVENT_CONTENT["reason"], "source_decision_pending")
        self.assertIn("datatalksclub.github.io", EVENT_CONTENT["detail"])

    def test_no_production_import_reads_the_legacy_site(self) -> None:
        """The repository must function without DataTalksClub/datatalksclub.github.io."""

        from scripts.prod import import_events

        for path in sorted(PROD_ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if "datatalksclub.github.io" not in stripped:
                    continue
                with self.subTest(module=path.name, line=stripped[:60]):
                    # Naming the retired source in prose is how the gap stays
                    # visible; reading from it is what is ruled out.
                    self.assertFalse(
                        stripped.startswith(("import ", "from ")),
                        "a production importer must not read the legacy site",
                    )
        self.assertFalse(import_events.EVENT_CONTENT["imported"])


class OrchestratorEventLegTests(TestCase):
    """The rehearsal composes this module rather than keeping a second copy."""

    def test_the_orchestrator_has_one_registration_implementation(self) -> None:
        """It composes scripts/prod/import_events.py rather than copying it."""

        source = (PROD_ROOT.parents[1] / "scripts" / "prepare_local_data.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from scripts.prod.import_events import", source)
        self.assertNotIn("stage_derived_source(", source)
        self.assertNotIn("activate_explicit_current_source(", source)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
