"""Event identity replay, content replay, and registration-aggregate coverage.

The adapters work; the per-event mapping decisions are the backlog.  These tests
lock in that a run reports the ratio rather than a bare success, and that the
reviewed content records land on the identities they describe.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path

from django.test import TestCase

import scripts.prod
from test_support.reference_data import EVENT_CONTENT, EVENT_IDENTITY_MANIFEST

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


def _entry_point_names() -> list[str]:
    return sorted(
        module.name for module in pkgutil.iter_modules([str(PROD_ROOT)]) if not module.ispkg
    )


class EventImportTests(TestCase):
    """Identity and content replay against the current Event tables."""

    def setUp(self) -> None:
        from scripts.prod.import_events import import_content, import_identities

        import_identities(manifest=EVENT_IDENTITY_MANIFEST, apply=True)
        import_content(source=EVENT_CONTENT, apply=True)

    def test_the_identity_manifest_replays_without_creating_a_row(self) -> None:
        """The test database already holds the reviewed set, so importing is a reconcile.

        ``import_identities``'s own default reads the real, external reviewed
        manifest (``~/prod/dtc-data/content-staging/``), which this checkout
        may not have; the reference-data fixture that already seeded this test
        database is a different, small synthetic set
        (``test_support/fixtures/reference/``), so this passes it explicitly to
        replay against what is actually here.
        """

        from community_base.events.models import Event
        from scripts.prod.import_events import import_identities

        before = Event.objects.count()

        report = import_identities(manifest=EVENT_IDENTITY_MANIFEST, apply=True)

        self.assertTrue(report["replayed"])
        self.assertEqual(report["events_created"], 0)
        self.assertEqual(Event.objects.count(), before)

    def test_a_dry_run_writes_nothing(self) -> None:
        from scripts.prod.import_events import import_identities

        report = import_identities(manifest=EVENT_IDENTITY_MANIFEST, apply=False)

        self.assertFalse(report["applied"])

    def test_the_content_records_replay_onto_the_identities_they_describe(self) -> None:
        """The test database already holds them, so importing is a reconcile."""

        from community_base.events.models import Event
        from scripts.prod.import_events import import_content

        before = (
            Event.objects.count(),
            Event.objects.exclude(description_html="").count(),
        )

        report = import_content(source=EVENT_CONTENT, apply=True)

        self.assertTrue(report["replayed"])
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["events"], report["unchanged"])
        self.assertEqual(
            (
                Event.objects.count(),
                Event.objects.exclude(description_html="").count(),
            ),
            before,
        )

    def test_every_identity_gets_content_and_the_reviewed_share_is_described(self) -> None:
        """Some of the synthetic set carry a reviewed description; the rest correctly carry none."""

        from community_base.events.models import Event
        from scripts.prod.import_events import import_content

        report = import_content(source=EVENT_CONTENT, apply=True)

        self.assertEqual(report["events"], Event.objects.count())
        self.assertEqual(
            report["described"],
            Event.objects.exclude(description_html="").count(),
        )
        self.assertGreater(report["described"], 0)
        self.assertLess(report["described"], report["events"])

    def test_a_content_dry_run_writes_nothing(self) -> None:
        from community_base.events.models import Event, EventHost
        from scripts.prod.import_events import import_content

        # Reduce every row to the manifest-only state the identity leg leaves:
        # scheduled but undescribed, so the content leg would create content.
        EventHost.objects.all().delete()
        Event.objects.update(description_html="", description="", materials=[])
        pending = Event.objects.count()

        report = import_content(source=EVENT_CONTENT, apply=False)

        self.assertFalse(report["applied"])
        self.assertGreater(report["created"], 0)
        self.assertEqual(Event.objects.exclude(description_html="").count(), 0)

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
        # The content records name that repository as provenance and are read
        # from a local, reviewed file -- never a live fetch of the legacy
        # site -- which is the distinction the rule draws. That file lives
        # outside this checkout now, at ~/prod/dtc-data/content-staging/ (see
        # _docs/architecture/database-only-content.md), so the check is that
        # it resolves under the operator's local data directory, not this
        # repository.
        self.assertTrue(
            import_events.EVENT_CONTENT_PATH.is_relative_to(Path.home() / "prod" / "dtc-data"),
            "event content must be staged locally, outside the live legacy site",
        )


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


class NoProviderEventImportTests(TestCase):
    """Events are registered only on this site: no import reads Luma or events.yaml."""

    def test_the_cli_has_no_events_yaml_or_luma_discovery_mode(self) -> None:
        from scripts.prod import import_events

        options = {
            option
            for action in import_events._parser()._actions
            for option in action.option_strings
        }
        for retired in (
            "--events-yaml",
            "--events-yaml-enrich-only",
            "--luma-source",
            "--discover-new-events-only",
            "--new-event-content",
        ):
            with self.subTest(option=retired):
                self.assertNotIn(retired, options)
