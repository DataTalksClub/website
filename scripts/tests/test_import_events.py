"""Event identity replay, content replay, and registration-aggregate coverage.

The adapters work; the per-event mapping decisions are the backlog.  These tests
lock in that a run reports the ratio rather than a bare success, and that the
reviewed content records land on the identities they describe.
"""

from __future__ import annotations

import csv
import json
import pkgutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase

import scripts.prod
from content.models import EventSource
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


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_luma_pair(
    root: Path,
    stem: str,
    *,
    event_id: str,
    event_url: str,
    title: str,
    start_at: str,
    statuses: tuple[str, ...],
) -> None:
    """One synthetic Luma CSV/JSON export pair. The guest values are canaries."""

    (root / f"{stem}.json").write_text(
        json.dumps({"schema_version": 1, "event_id": event_id, "event_url": event_url}),
        encoding="utf-8",
    )
    _write_csv(
        root / f"{stem}.csv",
        ["guest_id", "email", "approval_status", "event_id", "event_name", "event_start_at"],
        [
            {
                "guest_id": f"guest-{index}",
                "email": f"canary-{index}@example.test",
                "approval_status": status,
                "event_id": event_id,
                "event_name": title,
                "event_start_at": start_at,
            }
            for index, status in enumerate(statuses)
        ],
    )


class NewEventIdentityDiscoveryTests(TestCase):
    """A genuinely new provider event gets a real identity; an already-known one does not.

    "Already known" means this database already has a `HistoricalEventMapping` row
    for the (provider, external id) pair -- whether `review_required` or `mapped` --
    which is the existing, separately tracked mapping-review backlog.  Racing ahead
    of that with a second, auto-created identity would very likely duplicate an
    event the reviewed manifest already describes under a different source key.
    """

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_luma_event(
        self,
        stem: str,
        *,
        event_id: str,
        event_url: str,
        title: str,
        start_at: str,
        statuses: tuple[str, ...],
    ) -> None:
        _write_luma_pair(
            self.root,
            stem,
            event_id=event_id,
            event_url=event_url,
            title=title,
            start_at=start_at,
            statuses=statuses,
        )

    def test_creates_an_identity_for_a_genuinely_new_event(self) -> None:
        from community_base.events.models import Event
        from events.identity import canonical_detail_path
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._write_luma_event(
            "2026-09-08_a-brand-new-event_evt-brandnew",
            event_id="evt-BrandNew",
            event_url="https://luma.com/brandnew",
            title="A Brand New Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved", "approved", "declined"),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["provider"], "luma")
        self.assertEqual(report["candidate_total"], 1)
        self.assertEqual(report["already_tracked_total"], 0)
        self.assertEqual(report["created_total"], 1)
        created = report["created_events"][0]
        self.assertEqual(created["title"], "A Brand New Event")
        self.assertEqual(created["start_at"], "2026-09-08T10:00:00.000Z")
        self.assertEqual(created["eligible_count"], 2)
        self.assertIsInstance(created["public_id"], int)
        self.assertIn("Auto-created", created["reason"])
        self.assertEqual(Event.objects.count(), before + 1)

        source = EventSource.objects.get(source_key="evt-BrandNew")
        event = source.event
        self.assertEqual(event.source_identity.repository, "dtc-historical-source/luma")
        self.assertEqual(event.public_id, created["public_id"])
        self.assertEqual(canonical_detail_path(event.content_id), created["canonical_path"])

    def _canonical_event(self, *, title: str, source_key: str):
        """One event shaped like a reviewed-manifest entry: a dated legacy source key."""

        from events.identity import create_event_identity

        return create_event_identity(
            title=title,
            source_repository="DataTalksClub/datatalksclub.github.io",
            source_revision="a" * 40,
            source_key=source_key,
        )

    def test_an_event_we_already_have_creates_nothing(self) -> None:
        """The bug: on a database built in production order nothing else caught this.

        Identities import first, discovery runs next and the aggregates are staged
        only afterwards, so the aggregate-revision guard is empty exactly when it
        is needed.  The export names the event by a Luma event id; the reviewed
        manifest describes it under a legacy ``_data/events.yaml`` source key.  The
        date and the exact title are the only thing the two share.
        """

        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        existing = self._canonical_event(
            title="An Event We Already Have",
            source_key="2026-09-08-an-event-we-already-have",
        )
        self._write_luma_event(
            "2026-09-08_an-event-we-already-have_evt-known",
            event_id="evt-Known",
            event_url="https://luma.com/known",
            title="an event   we ALREADY have",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved", "approved"),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["existing_event_total"], 1)
        matched = report["existing_events"][0]
        self.assertEqual(matched["external_event_identifier"], "evt-Known")
        self.assertEqual(matched["matched_event_public_id"], existing.public_id)
        self.assertEqual(matched["matched_date"], "2026-09-08")
        self.assertEqual(Event.objects.count(), before)
        # Recognising it must not have attached anything to the event we kept.
        existing.refresh_from_db()
        self.assertEqual(existing.source_identity.source_key, "2026-09-08-an-event-we-already-have")
        self.assertEqual(existing.source_identity.repository, "DataTalksClub/datatalksclub.github.io")

    def test_recognising_an_event_we_already_have_replays_as_a_no_op(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._canonical_event(
            title="An Event We Already Have",
            source_key="2026-09-08-an-event-we-already-have",
        )
        self._write_luma_event(
            "2026-09-08_an-event-we-already-have_evt-known",
            event_id="evt-Known",
            event_url="https://luma.com/known",
            title="An Event We Already Have",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        first = discover_new_luma_event_identities(luma_source=self.root)
        before = Event.objects.count()
        second = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual((first["created_total"], second["created_total"]), (0, 0))
        self.assertEqual((first["existing_event_total"], second["existing_event_total"]), (1, 1))
        self.assertEqual(Event.objects.count(), before)

    def test_two_events_sharing_the_date_and_title_are_reported_not_resolved(self) -> None:
        """Folding two real events into one is worse than a duplicate, so neither wins."""

        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._canonical_event(
            title="An Event With A Twin", source_key="2026-09-08-an-event-with-a-twin"
        )
        self._canonical_event(
            title="An Event With A Twin", source_key="2026-09-08-an-event-with-a-twin-second"
        )
        self._write_luma_event(
            "2026-09-08_an-event-with-a-twin_evt-twin",
            event_id="evt-Twin",
            event_url="https://luma.com/twin",
            title="An Event With A Twin",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["existing_event_total"], 0)
        self.assertEqual(report["ambiguous_total"], 1)
        ambiguous = report["ambiguous_events"][0]
        self.assertEqual(ambiguous["external_event_identifier"], "evt-Twin")
        self.assertEqual(ambiguous["candidate_event_total"], 2)
        self.assertEqual(Event.objects.count(), before)

    def test_the_same_title_on_another_date_is_still_a_new_event(self) -> None:
        """A recurring series repeats its title; a different date is a different session."""

        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._canonical_event(title="Monthly Meetup", source_key="2026-09-08-monthly-meetup")
        self._write_luma_event(
            "2026-10-08_monthly-meetup_evt-october",
            event_id="evt-October",
            event_url="https://luma.com/october",
            title="Monthly Meetup",
            start_at="2026-10-08T10:00:00.000Z",
            statuses=("approved",),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 1)
        self.assertEqual(report["existing_event_total"], 0)
        created = report["created_events"][0]
        # Flagged for an operator, because a rescheduled event looks like this too.
        self.assertEqual(created["existing_event_dates_with_this_title"], ["2026-09-08"])
        self.assertEqual(Event.objects.count(), before + 1)

    def test_an_export_event_with_no_readable_date_is_reported_not_created(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._write_luma_event(
            "undated_event_evt-undated",
            event_id="evt-Undated",
            event_url="https://luma.com/undated",
            title="An Undated Export Event",
            start_at="whenever",
            statuses=("approved",),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["undated_total"], 1)
        self.assertEqual(report["undated_events"][0]["external_event_identifier"], "evt-Undated")
        self.assertEqual(Event.objects.count(), before)

    def test_a_second_run_creates_nothing_new(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._write_luma_event(
            "2026-09-08_a-replayed-event_evt-replayed",
            event_id="evt-Replayed",
            event_url="https://luma.com/replayed",
            title="A Replayed Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        first = discover_new_luma_event_identities(luma_source=self.root)
        self.assertEqual(first["created_total"], 1)
        after_first = Event.objects.count()

        second = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(second["created_total"], 0)
        self.assertEqual(second["already_tracked_total"], 1)
        self.assertEqual(Event.objects.count(), after_first)

    def test_dry_run_creates_nothing(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        self._write_luma_event(
            "2026-09-08_a-preview-event_evt-preview",
            event_id="evt-Preview",
            event_url="https://luma.com/preview",
            title="A Preview Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root, apply=False)

        self.assertEqual(report["applied"], False)
        self.assertEqual(report["created_total"], 1)
        self.assertTrue(report["created_events"][0]["dry_run"])
        self.assertEqual(Event.objects.count(), before)

    def test_a_zero_registration_event_is_reported_not_created_or_dropped(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        (self.root / "2026-09-08_empty-event_evt-empty.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "evt-Empty",
                    "event_url": "https://luma.com/empty",
                }
            ),
            encoding="utf-8",
        )
        _write_csv(
            self.root / "2026-09-08_empty-event_evt-empty.csv",
            ["guest_id", "email", "approval_status", "event_id", "event_name", "event_start_at"],
            [],
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["already_tracked_total"], 0)
        self.assertEqual(report["no_metadata_total"], 1)
        self.assertEqual(report["no_metadata_events"][0]["external_event_identifier"], "evt-Empty")
        self.assertEqual(Event.objects.count(), before)

    def test_reported_eligible_count_matches_the_raw_export_exactly(self) -> None:
        """The owner asked specifically that registration numbers match reality."""

        from scripts.prod.import_events import discover_new_luma_event_identities

        statuses = ("approved", "approved", "approved", "declined", "pending")
        self._write_luma_event(
            "2026-09-08_a-counted-event_evt-counted",
            event_id="evt-Counted",
            event_url="https://luma.com/counted",
            title="A Counted Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=statuses,
        )
        raw_csv_row_total = len(statuses)
        raw_approved_total = sum(1 for status in statuses if status == "approved")

        report = discover_new_luma_event_identities(luma_source=self.root)

        created = report["created_events"][0]
        self.assertEqual(created["eligible_count"], raw_approved_total)
        self.assertEqual(created["eligible_count"], 3)
        # The raw export's own row count, read independently of the pipeline.
        with (self.root / "2026-09-08_a-counted-event_evt-counted.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            independently_counted_rows = sum(1 for _ in csv.DictReader(handle))
        self.assertEqual(independently_counted_rows, raw_csv_row_total)
        self.assertEqual(independently_counted_rows, 5)


class DuplicateProviderIdentityReconciliationTests(TestCase):
    """Naming, and only conditionally removing, the duplicates an earlier run wrote.

    Fixing discovery stops new duplicates; it does nothing for a database that
    already holds them.  Deleting an ``Event`` that carries a public id and a
    Q&A session is destructive, so the default is a report and the removal is
    narrow: no alias, no registration, no aggregate revision, no Q&A question
    and no co-host invite, or the row stays and a human decides.
    """

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _duplicate_pair(self):
        """One reviewed-manifest event and the duplicate an unguarded run minted."""

        from events.identity import create_event_identity
        from scripts.prod.registrant_import import create_provider_event_identity

        keep = create_event_identity(
            title="An Event We Already Have",
            source_repository="DataTalksClub/datatalksclub.github.io",
            source_revision="a" * 40,
            source_key="2026-09-08-an-event-we-already-have",
        )
        duplicate = create_provider_event_identity(
            provider="luma",
            external_event_identifier="evt-Known",
            title="An Event We Already Have",
        )
        _write_luma_pair(
            self.root,
            "2026-09-08_an-event-we-already-have_evt-known",
            event_id="evt-Known",
            event_url="https://luma.com/known",
            title="An Event We Already Have",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )
        return keep, duplicate

    def test_reporting_names_the_duplicate_and_changes_nothing(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import reconcile_duplicate_luma_identities

        keep, duplicate = self._duplicate_pair()
        before = Event.objects.count()

        report = reconcile_duplicate_luma_identities(luma_source=self.root)

        self.assertEqual(report["duplicate_total"], 1)
        self.assertEqual(report["removed"], False)
        self.assertEqual(report["removed_total"], 0)
        entry = report["duplicates"][0]
        self.assertEqual(entry["external_event_identifier"], "evt-Known")
        self.assertEqual(entry["duplicate_public_id"], duplicate.public_id)
        self.assertEqual(entry["keep_public_id"], keep.public_id)
        self.assertEqual(entry["dependent_rows"], {})
        self.assertTrue(entry["removable"])
        self.assertEqual(Event.objects.count(), before)

    def test_removal_deletes_the_inert_duplicate_and_keeps_the_event_we_had(self) -> None:
        from community_base.events.models import Event
        from scripts.prod.import_events import reconcile_duplicate_luma_identities

        keep, duplicate = self._duplicate_pair()

        report = reconcile_duplicate_luma_identities(luma_source=self.root, remove=True)

        self.assertEqual(report["removed_total"], 1)
        self.assertEqual(report["retained_total"], 0)
        self.assertFalse(Event.objects.filter(pk=duplicate.id).exists())
        self.assertTrue(Event.objects.filter(pk=keep.id).exists())

    def test_a_second_removal_run_finds_nothing_left_to_do(self) -> None:
        from scripts.prod.import_events import reconcile_duplicate_luma_identities

        self._duplicate_pair()
        reconcile_duplicate_luma_identities(luma_source=self.root, remove=True)

        replayed = reconcile_duplicate_luma_identities(luma_source=self.root, remove=True)

        self.assertEqual(replayed["duplicate_total"], 0)
        self.assertEqual(replayed["removed_total"], 0)

    def test_a_duplicate_carrying_dependent_rows_is_reported_and_kept(self) -> None:
        """Deleting this would destroy a real Q&A question, so a human decides."""

        from event_qna.ids import opaque_id
        from event_qna.models import EventQnaQuestion, EventQnaSession
        from community_base.events.models import Event
        from scripts.prod.import_events import reconcile_duplicate_luma_identities

        _keep, duplicate = self._duplicate_pair()
        session = EventQnaSession.objects.get(event=duplicate)
        EventQnaQuestion.objects.create(
            question_id=opaque_id(),
            session=session,
            text="Will this be recorded?",
            participant_digest="a" * 64,
        )

        report = reconcile_duplicate_luma_identities(luma_source=self.root, remove=True)

        self.assertEqual(report["duplicate_total"], 1)
        self.assertEqual(report["removable_total"], 0)
        self.assertEqual(report["retained_total"], 1)
        self.assertEqual(report["removed_total"], 0)
        self.assertEqual(report["duplicates"][0]["dependent_rows"], {"qna_question": 1})
        self.assertTrue(Event.objects.filter(pk=duplicate.id).exists())

    def test_a_provider_event_we_never_duplicated_is_not_reported(self) -> None:
        """Only an exact date-and-title twin counts; a genuinely new event is left alone."""

        from community_base.events.models import Event
        from scripts.prod.import_events import reconcile_duplicate_luma_identities
        from scripts.prod.registrant_import import create_provider_event_identity

        created = create_provider_event_identity(
            provider="luma",
            external_event_identifier="evt-GenuinelyNew",
            title="A Genuinely New Event",
        )
        _write_luma_pair(
            self.root,
            "2026-09-08_a-genuinely-new-event_evt-genuinelynew",
            event_id="evt-GenuinelyNew",
            event_url="https://luma.com/genuinely-new",
            title="A Genuinely New Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        report = reconcile_duplicate_luma_identities(luma_source=self.root, remove=True)

        self.assertEqual(report["duplicate_total"], 0)
        self.assertEqual(report["removed_total"], 0)
        self.assertTrue(Event.objects.filter(pk=created.id).exists())


class EventsYamlImportTests(TestCase):
    """Importing new events from _data/events.yaml without duplicating existing rows."""

    def test_dry_run_and_apply_and_replay(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        before = Event.objects.count()
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "events.yaml"
            yaml_path.write_text(
                "- time: 2099-11-16 17:00:00\n"
                "  title: 'Future Luma Webinar'\n"
                "  speakers: [alexeygrigorev]\n"
                "  type: webinar\n"
                "  link: https://luma.com/future-webinar\n"
                "- time: 2026-01-10 17:00:00\n"
                "  title: 'Past Podcast Episode'\n"
                "  speakers: [janedoe]\n"
                "  type: podcast\n"
                "  link: https://luma.com/past-podcast\n"
                "  youtube: https://www.youtube.com/watch?v=abc123\n"
                "  season: 12\n"
                "  episode: 5\n",
                encoding="utf-8",
            )

            dry = import_events_yaml(path=yaml_path, revision="abc123", apply=False)
            self.assertFalse(dry["applied"])
            self.assertEqual(dry["created_total"], 2)
            self.assertEqual(Event.objects.count(), before)

            applied = import_events_yaml(path=yaml_path, revision="abc123", apply=True)
            self.assertTrue(applied["applied"])
            self.assertEqual(applied["created_total"], 2)
            self.assertEqual(Event.objects.count(), before + 2)

            upcoming = Event.objects.get(title="Future Luma Webinar")
            self.assertEqual(upcoming.status, "upcoming")
            self.assertEqual(
                upcoming.materials,
                [{"label": "Register on Luma", "url": "https://luma.com/future-webinar"}],
            )
            self.assertEqual(
                list(upcoming.hosts.values_list("external_ref", flat=True)),
                ["alexeygrigorev"],
            )

            past = Event.objects.get(title="Past Podcast Episode")
            self.assertEqual(past.status, "completed")
            self.assertEqual(len(past.materials), 2)

            replay = import_events_yaml(path=yaml_path, revision="def456", apply=True)
            self.assertEqual(replay["created_total"], 0)
            self.assertEqual(replay["existing_event_total"], 2)
            self.assertEqual(Event.objects.count(), before + 2)


class EventsYamlDuplicateTests(TestCase):
    """The two duplicate classes the 2026-11-16 rebuild hit, reproduced synthetically.

    The first version of ``--events-yaml`` only recognised an existing event by
    a *dated* source key, so it created a second row for (a) every Luma-discovered
    event (``evt-...`` key) and (b) every podcast-style manifest entry whose key
    carries no date: 79 duplicates on a production-like rebuild.
    """

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.luma = self.root / "luma"
        self.luma.mkdir()

    def _yaml(self, text: str) -> Path:
        path = self.root / "events.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def _luma_event(self, *, event_id: str, title: str, start_at: str, slug: str):
        from scripts.prod.registrant_import import create_provider_event_identity

        _write_luma_pair(
            self.luma,
            event_id,
            event_id=event_id,
            event_url=f"https://luma.com/{slug}",
            title=title,
            start_at=start_at,
            statuses=("approved",),
        )
        return create_provider_event_identity(
            provider="luma", external_event_identifier=event_id, title=title, start_at=start_at
        )

    def _manifest_event(self, *, title: str, source_key: str, start: str):
        from datetime import datetime

        from events.identity import create_event_identity

        return create_event_identity(
            title=title,
            starts_at=datetime.fromisoformat(start),
            source_repository="DataTalksClub/datatalksclub.github.io",
            source_revision="a" * 40,
            source_key=source_key,
            event_type="podcast",
            status="upcoming",
        )

    def test_a_luma_discovered_event_is_matched_and_given_its_type_and_speakers(self) -> None:
        from community_base.events.models import Event

        from events.identity import decode_event_tags
        from scripts.prod.import_events import import_events_yaml

        luma = self._luma_event(
            event_id="evt-SyntheticReliable",
            title="Synthetic Reliable Agents On Live Data",
            start_at="2099-12-02T11:30:00+00:00",
            slug="synthrel",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-12-02 12:30:00\n"
            "  title: 'Synthetic Reliable Agents On Live Data'\n"
            "  speakers: [synthetic-speaker]\n"
            "  type: podcast\n"
            "  link: https://luma.com/synthrel\n"
        )

        dry = import_events_yaml(path=path, apply=False, luma_source=self.luma)
        report = import_events_yaml(path=path, apply=True, luma_source=self.luma)

        for result in (dry, report):
            self.assertEqual(result["created_total"], 0)
            self.assertEqual(result["existing_event_total"], 1)
            self.assertEqual(result["matched_events"][0]["matched_by"], "luma_url")
            self.assertEqual(result["matched_events"][0]["public_id"], luma.public_id)
            self.assertEqual(
                result["enriched_events"][0]["filled"], ["type", "speakers", "materials"]
            )
        self.assertEqual(Event.objects.count(), before)
        luma.refresh_from_db()
        self.assertEqual(decode_event_tags(luma.tags)[0], "podcast")
        self.assertEqual(
            list(luma.hosts.values_list("external_ref", flat=True)), ["synthetic-speaker"]
        )
        self.assertEqual(luma.materials[0]["url"], "https://luma.com/synthrel")

        replay = import_events_yaml(path=path, apply=True, luma_source=self.luma)
        self.assertEqual(replay["created_total"], 0)
        self.assertEqual(replay["enriched_total"], 0)

    def test_without_the_export_start_and_title_still_find_the_luma_event(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        luma = self._luma_event(
            event_id="evt-SyntheticPeople",
            title="Synthetic People Analytics with AI",
            start_at="2099-11-30T11:30:00+00:00",
            slug="synthpeople",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-11-30 12:30:00\n"
            "  title: 'Synthetic People Analytics With AI'\n"
            "  type: podcast\n"
        )

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["matched_events"][0]["matched_by"], "start_and_title")
        self.assertEqual(report["matched_events"][0]["public_id"], luma.public_id)
        self.assertEqual(Event.objects.count(), before)

    def test_a_nominal_hour_on_the_same_day_matches_a_differently_spelled_luma_title(
        self,
    ) -> None:
        """The legacy site's nominal 17:00 against Luma's real 12:30, and a leading "The"."""

        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        luma = self._luma_event(
            event_id="evt-SyntheticPlaybook",
            title="Synthetic Consultant Playbook: From Code to Impact",
            start_at="2099-08-17T10:30:00+00:00",
            slug="synthplay",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-08-17 17:00:00\n"
            "  title: 'The Synthetic Consultant Playbook From Code to Impact'\n"
            "  type: podcast\n"
        )

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["matched_events"][0]["matched_by"], "date_and_title")
        self.assertEqual(report["matched_events"][0]["public_id"], luma.public_id)
        self.assertEqual(Event.objects.count(), before)

    def test_an_undated_podcast_manifest_event_is_matched_and_left_untouched(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        manifest = self._manifest_event(
            title="Synthetic Roles in a Data Team",
            source_key="synthetic-roles",  # podcast-style: no date in the key
            start="2020-11-20T11:00:00+00:00",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2020-11-20 12:00:00\n"
            "  title: 'Synthetic Roles in a Data Team'\n"
            "  speakers: [synthetic-guest]\n"
            "  type: podcast\n"
            "  season: 1\n"
            "  episode: 3\n"
            "  link: https://example.com/synthetic-roles\n"
        )

        dry = import_events_yaml(path=path, apply=False)
        report = import_events_yaml(path=path, apply=True)

        for result in (dry, report):
            self.assertEqual(result["created_total"], 0)
            self.assertEqual(result["matched_events"][0]["matched_by"], "start_and_title")
            self.assertEqual(result["matched_events"][0]["public_id"], manifest.public_id)
            # A reviewed manifest row is never enriched from the legacy file.
            self.assertEqual(result["enriched_total"], 0)
        self.assertEqual(Event.objects.count(), before)
        manifest.refresh_from_db()
        self.assertEqual(manifest.materials, [])
        self.assertFalse(manifest.hosts.exists())

    def test_a_luma_url_on_another_date_is_not_the_same_occurrence(self) -> None:
        """A reused Luma listing: same URL, a different day.  Reported, then created."""

        from scripts.prod.import_events import import_events_yaml

        luma = self._luma_event(
            event_id="evt-SyntheticReused",
            title="Synthetic Reused Listing",
            start_at="2099-07-01T10:30:00+00:00",
            slug="synthreused",
        )
        path = self._yaml(
            "- time: 2099-04-14 17:00:00\n"
            "  title: 'Synthetic Reused Listing'\n"
            "  link: https://lu.ma/SynthReused\n"
        )

        report = import_events_yaml(path=path, apply=False, luma_source=self.luma)

        self.assertEqual(report["created_total"], 1)
        self.assertEqual(report["provider_url_date_conflict_total"], 1)
        self.assertEqual(
            report["provider_url_date_conflicts"][0]["provider_url_on_another_date_public_id"],
            luma.public_id,
        )

    def test_two_candidate_events_are_ambiguous_and_nothing_is_created(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        self._manifest_event(
            title="Synthetic Twin Session", source_key="synthetic-twin-a",
            start="2099-03-01T16:00:00+00:00",
        )
        self._manifest_event(
            title="Synthetic Twin Session", source_key="synthetic-twin-b",
            start="2099-03-01T16:00:00+00:00",
        )
        before = Event.objects.count()
        path = self._yaml("- time: 2099-03-01 17:00:00\n  title: 'Synthetic Twin Session'\n")

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["ambiguous_total"], 1)
        self.assertEqual(report["ambiguous_events"][0]["candidate_event_total"], 2)
        self.assertEqual(Event.objects.count(), before)

    def test_the_dry_run_separates_matches_from_creations_and_sees_repeats(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        self._luma_event(
            event_id="evt-SyntheticKnown",
            title="Synthetic Known Event",
            start_at="2099-05-05T15:00:00+00:00",
            slug="synthknown",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-05-05 17:00:00\n"
            "  title: 'Synthetic Known Event'\n"
            "- time: 2099-06-06 17:00:00\n"
            "  title: 'Synthetic Brand New Event'\n"
            "- time: 2099-06-06 17:00:00\n"
            "  title: 'Synthetic Brand-New Event'\n"
        )

        dry = import_events_yaml(path=path, apply=False)

        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(
            [(m["title"], m["matched_by"]) for m in dry["matched_events"]],
            [
                ("Synthetic Known Event", "start_and_title"),
                ("Synthetic Brand-New Event", "earlier_entry_in_this_file"),
            ],
        )
        self.assertEqual(
            [c["title"] for c in dry["created_events"]], ["Synthetic Brand New Event"]
        )

        applied = import_events_yaml(path=path, apply=True)

        self.assertEqual(applied["created_total"], 1)
        self.assertEqual(
            [m["matched_by"] for m in applied["matched_events"]],
            ["start_and_title", "earlier_entry_in_this_file"],
        )
        self.assertEqual(Event.objects.count(), before + 1)

    def test_a_luma_event_that_already_has_speakers_keeps_them(self) -> None:
        from community_base.events.models import EventHost, Host

        from scripts.prod.import_events import import_events_yaml

        luma = self._luma_event(
            event_id="evt-SyntheticReviewed",
            title="Synthetic Reviewed Speakers",
            start_at="2099-09-09T15:00:00+00:00",
            slug="synthreviewed",
        )
        host = Host.objects.create(
            slug="synthetic-reviewed-host", name="Reviewed", kind="speaker",
            external_ref="synthetic-reviewed-host",
        )
        EventHost.objects.create(event=luma, host=host, role="speaker", position=0)
        path = self._yaml(
            "- time: 2099-09-09 17:00:00\n"
            "  title: 'Synthetic Reviewed Speakers'\n"
            "  speakers: [synthetic-yaml-speaker]\n"
            "  type: webinar\n"
        )

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["enriched_events"][0]["filled"], ["type"])
        self.assertEqual(
            list(luma.hosts.values_list("external_ref", flat=True)),
            ["synthetic-reviewed-host"],
        )

    def test_a_luma_row_duplicating_a_reviewed_row_is_reported_not_enriched(self) -> None:
        """An earlier discovery minted a Luma twin of a manifest event; leave it bare."""

        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        manifest = self._manifest_event(
            title="Synthetic Agent In The Loop",
            source_key="synthetic-agent-in-the-loop-reviewed",
            start="2099-12-16T16:00:00+00:00",
        )
        luma = self._luma_event(
            event_id="evt-SyntheticTwin",
            title="Synthetic Agent In The Loop",
            start_at="2099-12-16T11:30:00+00:00",
            slug="synthtwin",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-12-16 17:00:00\n"
            "  title: 'Synthetic Agent In The Loop'\n"
            "  speakers: [synthetic-speaker]\n"
            "  type: webinar\n"
            "  link: https://luma.com/synthtwin\n"
        )

        report = import_events_yaml(path=path, apply=True, luma_source=self.luma)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["enriched_total"], 0)
        self.assertEqual(
            report["provider_duplicates"][0]["also_matches_public_id"], manifest.public_id
        )
        self.assertEqual(report["provider_duplicates"][0]["provider_public_id"], luma.public_id)
        self.assertEqual(Event.objects.count(), before)
        luma.refresh_from_db()
        self.assertEqual(luma.tags, [])

    def _production_shaped_row(self, *, title: str, source_key: str, start: str, link: str = ""):
        """A reviewed row whose source key matches neither the old nor the new title.

        Production's manifest keys were cut from an earlier spelling of the title
        (``writing``, ``2021-01-15-importance-of-writing``), and its Eventbrite link
        lives in ``materials`` -- the shape behind the 74 events a dry run against
        production would have recreated.
        """

        row = self._manifest_event(title=title, source_key=source_key, start=start)
        if link:
            row.materials = [{"label": "Register on Eventbrite", "url": link}]
            row.save(update_fields=["materials"])
        return row

    def test_a_production_shaped_row_is_found_by_its_eventbrite_link(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        row = self._production_shaped_row(
            title="The Importance of Writing in a Synthetic Career",
            source_key="2099-01-15-old-spelling-of-writing",
            start="2099-01-15T16:00:00+00:00",
            link="https://www.eventbrite.com/e/synthetic-writing-tickets-998877665544",
        )
        before = Event.objects.count()
        # Retitled since, and given the nominal hour: neither the key nor the
        # start/title tiers would see it.
        path = self._yaml(
            "- time: 2099-01-15 19:00:00\n"
            "  title: 'Writing Well: A Synthetic Career Skill'\n"
            "  type: podcast\n"
            "  link: https://eventbrite.com/e/998877665544\n"
        )

        report = import_events_yaml(path=path, apply=False)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["matched_events"][0]["matched_by"], "eventbrite_url")
        self.assertEqual(report["matched_events"][0]["public_id"], row.public_id)
        self.assertEqual(Event.objects.count(), before)

    def test_a_production_shaped_row_is_found_by_date_and_normalized_title(self) -> None:
        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        row = self._production_shaped_row(
            title="The Importance of Writing in a Tech Career (Synthetic)",
            source_key="synthetic-writing",
            start="2099-01-15T16:00:00+00:00",
        )
        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-01-15 19:00:00\n"
            "  title: 'Importance of Writing in a Tech Career - Synthetic'\n"
        )

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["matched_events"][0]["matched_by"], "date_and_title")
        self.assertEqual(report["matched_events"][0]["public_id"], row.public_id)
        self.assertEqual(Event.objects.count(), before)

    def test_a_luma_profile_or_calendar_url_names_no_event(self) -> None:
        from scripts.prod.import_events import _luma_url_key

        self.assertIsNone(_luma_url_key("https://luma.com/user/synthetic-host"))
        self.assertIsNone(_luma_url_key("https://lu.ma/calendar/manage/cal-synthetic"))
        self.assertEqual(_luma_url_key("https://lu.ma/SynthSlug/"), "synthslug")
        self.assertEqual(_luma_url_key("https://lu.ma/event/evt-SynthAbC"), "evt-SynthAbC")
        self.assertEqual(
            _luma_url_key("https://www.eventbrite.de/e/synthetic-tickets-123456789012"),
            "eventbrite:123456789012",
        )

    def test_two_same_title_entries_on_one_day_are_not_folded_together(self) -> None:
        """Two sessions of a series on one day: the date tier refuses, it does not merge."""

        from community_base.events.models import Event

        from scripts.prod.import_events import import_events_yaml

        before = Event.objects.count()
        path = self._yaml(
            "- time: 2099-10-10 10:00:00\n"
            "  title: 'Synthetic Office Hours'\n"
            "- time: 2099-10-10 17:00:00\n"
            "  title: 'Synthetic Office Hours'\n"
        )

        report = import_events_yaml(path=path, apply=True)

        self.assertEqual(report["created_total"], 1)
        self.assertEqual(report["ambiguous_total"], 1)
        self.assertEqual(report["ambiguous_events"][0]["same_day_same_title_entries"], 2)
        self.assertEqual(Event.objects.count(), before + 1)
