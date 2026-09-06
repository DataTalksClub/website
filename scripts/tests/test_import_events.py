"""Event identity replay, content replay, and registration-aggregate coverage.

The adapters work; the per-event mapping decisions are the backlog.  These tests
lock in that a run reports the ratio rather than a bare success, and that the
reviewed content records land on the identities they describe.
"""

from __future__ import annotations

import csv
import importlib
import json
import pkgutil
import tempfile
from pathlib import Path

from django.conf import settings
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
                    "luma": {"explicit_mapping_total": 3, "unresolved_total": 171},
                    "eventbrite": {
                        "explicit_mapping_total": 0,
                        "unresolved_total": 209,
                    },
                }
            },
        )

        self.assertEqual(coverage["provider_events"], 383)
        self.assertEqual(coverage["resolved"], 3)
        self.assertEqual(coverage["unresolved"], 380)
        self.assertIn("3 of 383", coverage["summary"])
        self.assertIn("380 remain unresolved", coverage["summary"])

    def test_the_content_records_replay_onto_the_identities_they_describe(self) -> None:
        """The test database already holds them, so importing is a reconcile."""

        from events.models import EventContent, EventLink, EventSpeaker
        from scripts.prod.import_events import import_content

        before = (
            EventContent.objects.count(),
            EventSpeaker.objects.count(),
            EventLink.objects.count(),
        )

        report = import_content(apply=True)

        self.assertTrue(report["replayed"])
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["events"], report["unchanged"])
        self.assertEqual(
            (
                EventContent.objects.count(),
                EventSpeaker.objects.count(),
                EventLink.objects.count(),
            ),
            before,
        )

    def test_every_identity_gets_content_and_the_reviewed_share_is_described(self) -> None:
        """159 of the 421 carry a reviewed description; the rest correctly carry none."""

        from events.models import Event, EventContent
        from scripts.prod.import_events import import_content

        report = import_content(apply=True)

        self.assertEqual(report["events"], Event.objects.count())
        self.assertEqual(report["events"], EventContent.objects.count())
        self.assertEqual(
            report["described"],
            EventContent.objects.exclude(description_html="").count(),
        )
        self.assertGreater(report["described"], 0)
        self.assertLess(report["described"], report["events"])

    def test_a_content_dry_run_writes_nothing(self) -> None:
        from events.models import EventContent
        from scripts.prod.import_events import import_content

        EventContent.objects.filter(description_html="").delete()
        remaining = EventContent.objects.count()

        report = import_content(apply=False)

        self.assertFalse(report["applied"])
        self.assertGreater(report["created"], 0)
        self.assertEqual(EventContent.objects.count(), remaining)

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
        # from inside this checkout, which is the distinction the rule draws.
        self.assertTrue(
            import_events.EVENT_CONTENT_PATH.is_relative_to(PROD_ROOT.parents[1]),
            "event content must be staged inside this repository",
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
        from events.identity import canonical_detail_path
        from events.models import Event
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

        event = Event.objects.get(
            source_repository="dtc-historical-source/luma", source_key="evt-BrandNew"
        )
        self.assertEqual(event.public_id, created["public_id"])
        self.assertEqual(canonical_detail_path(event.id), created["canonical_path"])

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

        from events.models import Event
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
        self.assertEqual(existing.source_key, "2026-09-08-an-event-we-already-have")
        self.assertEqual(existing.source_repository, "DataTalksClub/datatalksclub.github.io")

    def test_recognising_an_event_we_already_have_replays_as_a_no_op(self) -> None:
        from events.models import Event
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

        from events.models import Event
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

        from events.models import Event
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
        from events.models import Event
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
        from events.models import Event
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

    def test_an_event_already_staged_for_mapping_review_is_left_alone(self) -> None:
        """The 380-of-421 mapping-review backlog is a different, already-tracked gap.

        There is no separate mapping-review row any more (``HistoricalEventMapping``
        was removed -- see commit 2263e4f "Remove HistoricalEventMapping and its
        review-state machine").  A provider event now counts as already tracked
        once a ``HistoricalRegistrationAggregateRevision`` row exists for its
        ``(provider, external_event_identifier)`` pair, resolved or not -- exactly
        the check ``discover_new_provider_events`` makes.  This constructs an
        unresolved (``event=None``) aggregate revision to stand in for what used to
        be a ``review_required`` mapping row.
        """

        import hashlib

        from events.models import (
            Event,
            HistoricalRegistrationAggregateRevision,
            HistoricalRegistrationSourceRun,
        )
        from scripts.prod.import_events import discover_new_luma_event_identities

        run = HistoricalRegistrationSourceRun.objects.create(
            provider="luma",
            adapter_version="synthetic-v1",
            schema_version="synthetic-v1",
            whole_source_checksum=hashlib.sha256(b"source-already-staged").hexdigest(),
            source_reference_digest=hashlib.sha256(b"reference-already-staged").hexdigest(),
            manifest_entry_total=1,
            manifest_event_total=1,
            parsed_row_total=1,
            eligible_row_total=1,
            excluded_row_total=0,
            quarantined_event_total=0,
            status_totals={"eligible": 1},
            state_totals={"staged": 1},
            reason_codes=[],
            mapping_set_revision=1,
            policy_version="historical-registration-v1",
            state=HistoricalRegistrationSourceRun.State.STAGED,
            actor_ref="system:test-already-staged",
        )
        HistoricalRegistrationAggregateRevision.objects.create(
            source_run=run,
            external_event_identifier="evt-AlreadyStaged",
            event=None,
            eligible_count=1,
            excluded_count=0,
            quarantined_count=0,
            coverage_boundary="historical",
            status_policy_version="historical-status-v1",
            combination_policy=(
                HistoricalRegistrationAggregateRevision.CombinationPolicy.ADDITIVE_DISJOINT
            ),
            aggregate_checksum=hashlib.sha256(b"aggregate-already-staged").hexdigest(),
            state=HistoricalRegistrationAggregateRevision.State.STAGED,
        )
        self._write_luma_event(
            "2026-09-08_an-already-staged-event_evt-alreadystaged",
            event_id="evt-AlreadyStaged",
            event_url="https://luma.com/already-staged",
            title="An Already Staged Event",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved",),
        )

        before = Event.objects.count()
        report = discover_new_luma_event_identities(luma_source=self.root)

        self.assertEqual(report["created_total"], 0)
        self.assertEqual(report["already_tracked_total"], 1)
        self.assertEqual(Event.objects.count(), before)

    def test_dry_run_creates_nothing(self) -> None:
        from events.models import Event
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
        from events.models import Event
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

        from events.identity import create_event_identity, create_provider_event_identity

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
        from events.models import Event
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
        from events.models import Event
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

        from events.models import Event, EventQnaQuestion, EventQnaSession
        from events.qna.ids import opaque_id
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

        from events.identity import create_provider_event_identity
        from events.models import Event
        from scripts.prod.import_events import reconcile_duplicate_luma_identities

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


class RunAtomicityTests(TestCase):
    """A refused run leaves the database exactly as it found it.

    The registration leg validates its exports last, so before `run()` took a
    transaction a checksum refusal still left every earlier leg committed: a
    "fresh" database that had just exited 1 held 448 events, 1,684 aliases,
    421 content rows and 448 queued Q2 wakeups, and the retry started from a
    half-populated database that looked populated.
    """

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)
        self.luma_source = self.root / "luma"
        self.luma_source.mkdir()
        _write_luma_pair(
            self.luma_source,
            "2026-09-08_a-brand-new-event_evt-atomicity",
            event_id="evt-Atomicity",
            event_url="https://luma.com/atomicity",
            title="An Event Only This Export Knows About",
            start_at="2026-09-08T10:00:00.000Z",
            statuses=("approved", "approved", "declined"),
        )
        # The eventbrite leg is never reached: the luma checksum refuses first.
        # It only has to exist, because `run()` refuses a missing source before
        # it opens the transaction at all.
        self.eventbrite_source = self.root / "eventbrite.zip"
        self.eventbrite_source.write_bytes(b"not a real archive")
        self.new_event_content = self.root / "absent-new-event-content.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self):
        from scripts.prod.import_events import run

        return run(
            luma_source=self.luma_source,
            eventbrite_source=self.eventbrite_source,
            new_event_content_source=self.new_event_content,
        )

    def test_the_leg_that_rolls_back_really_does_write(self) -> None:
        """Without this, the rollback assertion below would pass vacuously."""

        from events.models import Event
        from scripts.prod.import_events import discover_new_luma_event_identities

        before = Event.objects.count()

        report = discover_new_luma_event_identities(luma_source=self.luma_source, apply=True)

        self.assertEqual(report["created_total"], 1)
        self.assertEqual(Event.objects.count(), before + 1)

    def test_a_refused_run_leaves_no_partial_row_behind(self) -> None:
        from events.models import (
            Event,
            EventAlias,
            EventContent,
            HistoricalRegistrationAggregateRevision,
            HistoricalRegistrationSourceRun,
        )
        from jobs.models import DurableJob
        from scripts.prod.import_events import EventImportError

        def counts() -> tuple[int, ...]:
            return (
                Event.objects.count(),
                EventAlias.objects.count(),
                EventContent.objects.count(),
                HistoricalRegistrationSourceRun.objects.count(),
                HistoricalRegistrationAggregateRevision.objects.count(),
                DurableJob.objects.count(),
            )

        before = counts()

        with self.assertRaises(EventImportError) as refusal:
            self._run()

        self.assertEqual(str(refusal.exception), "registration_source_validation_failed")
        self.assertEqual(counts(), before)

    def test_a_refused_run_queues_no_background_work(self) -> None:
        """The Q2 enqueue is an on-commit side effect, so it must not survive either."""

        from scripts.prod.import_events import EventImportError

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with self.assertRaises(EventImportError):
                self._run()

        self.assertEqual(callbacks, [])

    def test_the_refusal_names_no_source_value(self) -> None:
        """The opaque code is deliberate: this data is protected."""

        from scripts.prod.import_events import EventImportError

        with self.assertRaises(EventImportError) as refusal:
            self._run()

        message = str(refusal.exception)
        self.assertNotIn(str(self.luma_source), message)
        self.assertNotIn("evt-Atomicity", message)
