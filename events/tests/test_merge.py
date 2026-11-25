"""The reviewed duplicate-event merge, and what a merge leaves behind.

Every record here is synthetic.  A pair mirrors the shape the 2026-10-23 bulk
load produced: an ``_data/events.yaml`` record with a description and a
speaker, and a Luma-minted record carrying the registrations and nothing else.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest import mock

from community_base.events.models import Event, EventHost, Host
from django.test import TestCase

from content.models import EventMergedIdentity, EventSource
from core.models import AuditEvent
from event_qna.ids import opaque_id
from event_qna.models import EventQnaQuestion, EventQnaSession
from event_registrants.models import (
    EventRegistrantIdentity,
    EventRegistrantImportProgress,
    EventRegistration,
)
from events.identity import (
    EventIdentityError,
    EventIdentityNotFound,
    canonical_detail_path,
    create_event_identity,
    resolve_source_identity,
)
from events.merge import (
    AUDIT_ACTION,
    EventMergeRunAborted,
    MergePair,
    merge_pair,
    merge_pairs,
)

LEGACY_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
LUMA_REPOSITORY = "dtc-historical-source/luma"
LUMA_REVISION = "luma-aggregate-v1"


def _identity(label: str) -> EventRegistrantIdentity:
    return EventRegistrantIdentity.objects.create(normalized_email=f"{label}@example.test")


class MergeFixture(TestCase):
    def setUp(self) -> None:
        self.keep = create_event_identity(
            event_type="podcast",
            speakers=[("speakerone", "Speaker One")],
            title="Machine Learning Engineering in Finance",
            starts_at=datetime(2024, 1, 15, 16, 0, tzinfo=UTC),
            source_repository=LEGACY_REPOSITORY,
            source_revision="a" * 40,
            source_key="2024-01-15-machine-learning-engineering-in-finance",
        )
        self.keep.description_html = "<p>A reviewed description.</p>"
        self.keep.description = "A reviewed description."
        self.keep.save(update_fields=["description_html", "description", "updated_at"])

        self.duplicate = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Machine Learning Engineering in Finances",
            starts_at=datetime(2024, 1, 15, 11, 30, tzinfo=UTC),
            source_repository=LUMA_REPOSITORY,
            source_revision=LUMA_REVISION,
            source_key="evt-Synthetic1",
        )
        # The duplicate is shaped like the Luma rows written before every event
        # had to carry a type and a speaker: neither.
        self.duplicate.tags = []
        self.duplicate.save(update_fields=["tags"])
        self.duplicate.event_host_links.all().delete()
        self.identities = [_identity(f"person-{index}") for index in range(3)]
        for identity in self.identities:
            EventRegistration.objects.create(
                event=self.duplicate, identity=identity, provider="luma", status="approved"
            )
        self.pair = MergePair(keep_id=self.keep.pk, duplicate_id=self.duplicate.pk)
        self.duplicate_public_id = self.duplicate.public_id
        self.duplicate_slug = self.duplicate.slug


class DryRunTests(MergeFixture):
    def test_the_dry_run_shows_both_records_and_writes_nothing(self) -> None:
        report = merge_pair(self.pair)

        self.assertEqual(report["outcome"], "would_merge")
        self.assertFalse(report["applied"])
        diff = report["diff"]
        self.assertFalse(diff["title"]["same"])
        self.assertEqual(diff["start_datetime"]["keep"], "2024-01-15T16:00:00+00:00")
        self.assertEqual(diff["start_datetime"]["duplicate"], "2024-01-15T11:30:00+00:00")
        self.assertEqual(diff["has_description"], {"keep": True, "duplicate": False, "same": False})
        self.assertEqual(diff["hosts"]["keep"], [{"host": "speakerone", "role": "speaker"}])
        self.assertEqual(diff["registrations"]["duplicate"]["total"], 3)
        self.assertEqual(report["plan"]["registrations_moved"], 3)
        self.assertEqual(report["plan"]["qna_sessions_deleted"], 1)
        self.assertEqual(report["plan"]["retired_public_id"], self.duplicate_public_id)
        self.assertEqual(
            report["plan"]["redirect_target"], canonical_detail_path(self.keep.content_id)
        )

        self.assertTrue(Event.objects.filter(pk=self.duplicate.pk).exists())
        self.assertEqual(EventRegistration.objects.filter(event=self.duplicate).count(), 3)
        self.assertFalse(EventMergedIdentity.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action=AUDIT_ACTION).exists())

    def test_the_report_never_carries_an_attendee_value(self) -> None:
        rendered = json.dumps(merge_pair(self.pair))

        self.assertNotIn("@example.test", rendered)


class ApplyTests(MergeFixture):
    def test_apply_moves_everything_onto_the_kept_event_and_deletes_the_duplicate(self) -> None:
        duplicate_content_id = self.duplicate.content_id
        duplicate_calendar_uid = self.duplicate.calendar_uid

        report = merge_pair(self.pair, apply=True)

        self.assertEqual(report["outcome"], "merged")
        self.assertTrue(report["applied"])
        self.assertFalse(Event.objects.filter(pk=self.pair.duplicate_id).exists())
        self.assertEqual(EventRegistration.objects.filter(event=self.keep).count(), 3)
        self.assertFalse(EventSource.objects.filter(source_key="evt-Synthetic1").exists())
        merged = EventMergedIdentity.objects.get()
        self.assertEqual(merged.event, self.keep)
        self.assertEqual(merged.merged_row_id, self.pair.duplicate_id)
        self.assertEqual(merged.public_id, self.duplicate_public_id)
        self.assertEqual(merged.content_id, duplicate_content_id)
        self.assertEqual(merged.calendar_uid, duplicate_calendar_uid)
        self.assertEqual(
            (merged.repository, merged.revision, merged.source_key),
            (LUMA_REPOSITORY, LUMA_REVISION, "evt-Synthetic1"),
        )
        # The duplicate's empty Q&A session went with it; the kept one stays.
        self.assertEqual(EventQnaSession.objects.filter(event=self.keep).count(), 1)
        self.assertEqual(EventQnaSession.objects.filter(event_id=self.pair.duplicate_id).count(), 0)

    def test_the_kept_record_keeps_its_own_title_description_hosts_and_time(self) -> None:
        merge_pair(self.pair, apply=True)

        self.keep.refresh_from_db()
        self.assertEqual(self.keep.title, "Machine Learning Engineering in Finance")
        self.assertEqual(self.keep.description, "A reviewed description.")
        self.assertEqual(self.keep.start_datetime, datetime(2024, 1, 15, 16, 0, tzinfo=UTC))
        self.assertEqual(
            list(self.keep.event_host_links.values_list("host__external_ref", flat=True)),
            ["speakerone"],
        )

    def test_start_from_duplicate_is_an_explicit_choice(self) -> None:
        merge_pair(self.pair, apply=True, start_from_duplicate=True)

        self.keep.refresh_from_db()
        self.assertEqual(self.keep.start_datetime, datetime(2024, 1, 15, 11, 30, tzinfo=UTC))

    def test_a_registrant_already_on_the_kept_event_is_not_doubled(self) -> None:
        EventRegistration.objects.create(
            event=self.keep, identity=self.identities[0], provider="eventbrite", status="attending"
        )

        report = merge_pair(self.pair, apply=True)

        self.assertEqual(report["plan"]["registrations_moved"], 2)
        self.assertEqual(report["plan"]["registrations_dropped_already_registered"], 1)
        rows = EventRegistration.objects.filter(event=self.keep)
        self.assertEqual(rows.count(), 3)
        self.assertEqual(rows.filter(identity=self.identities[0]).count(), 1)

    def test_hosts_only_the_duplicate_has_are_appended(self) -> None:
        guest = Host.objects.create(name="Guest Two", slug="guesttwo", external_ref="guesttwo")
        EventHost.objects.create(event=self.duplicate, host=guest, role="speaker", position=0)

        report = merge_pair(self.pair, apply=True)

        self.assertEqual(report["plan"]["hosts_added"], 1)
        self.assertEqual(
            list(
                self.keep.event_host_links.order_by("position").values_list(
                    "host__external_ref", flat=True
                )
            ),
            ["speakerone", "guesttwo"],
        )

    def test_one_audit_record_names_the_pair_and_its_counts(self) -> None:
        merge_pair(self.pair, apply=True)

        audit = AuditEvent.objects.get(action=AUDIT_ACTION)
        self.assertEqual(audit.target_id, self.keep.content_id)
        self.assertEqual(audit.outcome, "succeeded")
        self.assertEqual(audit.changes["registrations_moved"], 3)
        self.assertEqual(audit.metadata["duplicate_row_id"], self.pair.duplicate_id)
        self.assertNotIn("@example.test", json.dumps(audit.metadata))

    def test_replaying_an_applied_pair_writes_nothing(self) -> None:
        merge_pair(self.pair, apply=True)

        replay = merge_pair(self.pair, apply=True)

        self.assertEqual(replay["outcome"], "already_merged")
        self.assertEqual(replay["retired_public_id"], self.duplicate_public_id)
        self.assertEqual(AuditEvent.objects.filter(action=AUDIT_ACTION).count(), 1)
        self.assertEqual(EventRegistration.objects.filter(event=self.keep).count(), 3)

    def test_a_chain_of_merges_repoints_the_earlier_merge(self) -> None:
        merge_pair(self.pair, apply=True)
        survivor = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="The Real Survivor",
            starts_at=datetime(2024, 1, 15, 16, 0, tzinfo=UTC),
            source_repository=LEGACY_REPOSITORY,
            source_revision="a" * 40,
            source_key="2024-01-15-the-real-survivor",
        )
        EventQnaSession.objects.filter(event=self.keep).delete()

        merge_pair(MergePair(keep_id=survivor.pk, duplicate_id=self.keep.pk), apply=True)

        self.assertEqual(
            set(EventMergedIdentity.objects.values_list("event_id", flat=True)), {survivor.pk}
        )


class RefusalTests(MergeFixture):
    def _assert_untouched(self) -> None:
        self.assertTrue(Event.objects.filter(pk=self.duplicate.pk).exists())
        self.assertEqual(EventRegistration.objects.filter(event=self.duplicate).count(), 3)
        self.assertFalse(EventMergedIdentity.objects.exists())

    def test_events_days_apart_are_refused_unless_allowed(self) -> None:
        self.duplicate.start_datetime = datetime(2024, 1, 20, 11, 30, tzinfo=UTC)
        self.duplicate.save(update_fields=["start_datetime"])

        refused = merge_pair(self.pair, apply=True)

        self.assertEqual(refused["outcome"], "refused")
        self.assertEqual(refused["reason"], "start_dates_too_far_apart")
        self.assertIn("diff", refused)
        self._assert_untouched()
        allowed = merge_pair(self.pair, apply=True, allow_date_mismatch=True)
        self.assertEqual(allowed["outcome"], "merged")

    def test_a_wider_window_is_an_explicit_option(self) -> None:
        self.duplicate.start_datetime = datetime(2024, 1, 18, 11, 30, tzinfo=UTC)
        self.duplicate.save(update_fields=["start_datetime"])

        self.assertEqual(merge_pair(self.pair)["outcome"], "refused")
        self.assertEqual(merge_pair(self.pair, max_days_apart=3)["outcome"], "would_merge")

    def test_a_duplicate_with_a_real_qna_question_is_refused(self) -> None:
        EventQnaQuestion.objects.create(
            question_id=opaque_id(),
            session=EventQnaSession.objects.get(event=self.duplicate),
            text="Will this be recorded?",
            participant_digest="a" * 64,
        )

        refused = merge_pair(self.pair, apply=True)

        self.assertEqual(refused["reason"], "duplicate_qna_session_not_empty")
        self._assert_untouched()

    def test_a_kept_event_with_rows_from_the_same_provider_is_refused(self) -> None:
        EventRegistration.objects.create(
            event=self.keep, identity=_identity("other"), provider="luma", status="approved"
        )

        refused = merge_pair(self.pair, apply=True)

        self.assertEqual(refused["reason"], "kept_event_has_same_provider_registrations")
        self._assert_untouched()

    def test_unknown_events_are_refused(self) -> None:
        missing = Event.objects.order_by("-pk").first().pk + 1000
        self.assertEqual(
            merge_pair(MergePair(self.keep.pk, missing))["reason"], "duplicate_unknown"
        )
        self.assertEqual(
            merge_pair(MergePair(missing, self.duplicate.pk))["reason"], "keep_unknown"
        )

    def test_a_pair_already_merged_elsewhere_is_refused(self) -> None:
        merge_pair(self.pair, apply=True)
        # Created here rather than read from seeded reference data, which only
        # the SQLite harness loads.
        other = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="A Different Event",
            starts_at=datetime(2024, 1, 15, 16, 0, tzinfo=UTC),
            source_repository=LEGACY_REPOSITORY,
            source_revision="a" * 40,
            source_key="2024-01-15-a-different-event",
        )

        refused = merge_pair(MergePair(other.pk, self.pair.duplicate_id), apply=True)

        self.assertEqual(refused["reason"], "duplicate_merged_into_another_event")

    def test_an_error_mid_run_names_the_pairs_already_committed(self) -> None:
        second = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="A Second Event",
            starts_at=datetime(2024, 2, 1, 16, 0, tzinfo=UTC),
            source_repository=LEGACY_REPOSITORY,
            source_revision="a" * 40,
            source_key="2024-02-01-a-second-event",
        )
        second_duplicate = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="A Second Event",
            starts_at=datetime(2024, 2, 1, 11, 30, tzinfo=UTC),
            source_repository=LUMA_REPOSITORY,
            source_revision=LUMA_REVISION,
            source_key="evt-Synthetic2",
        )
        failing = MergePair(keep_id=second.pk, duplicate_id=second_duplicate.pk)
        real_merge = merge_pair

        def merge_or_fail(pair: MergePair, **options: object) -> dict[str, object]:
            if pair == failing:
                raise RuntimeError("synthetic failure")
            return real_merge(pair, **options)  # type: ignore[arg-type]

        with (
            mock.patch("events.merge.merge_pair", side_effect=merge_or_fail),
            self.assertRaises(EventMergeRunAborted) as raised,
        ):
            merge_pairs([self.pair, failing], apply=True)

        self.assertEqual(raised.exception.failed_pair, failing.label())
        self.assertEqual(raised.exception.error, "RuntimeError")
        self.assertEqual([result["outcome"] for result in raised.exception.completed], ["merged"])
        self.assertFalse(Event.objects.filter(pk=self.pair.duplicate_id).exists())
        self.assertTrue(Event.objects.filter(pk=second_duplicate.pk).exists())

    def test_a_run_rejects_ambiguous_pair_lists(self) -> None:
        with self.assertRaises(ValueError):
            merge_pairs([self.pair, MergePair(self.keep.pk + 1, self.duplicate.pk)])
        with self.assertRaises(ValueError):
            merge_pairs([self.pair, MergePair(self.duplicate.pk, self.keep.pk + 1)])

    def test_pair_syntax(self) -> None:
        self.assertEqual(MergePair.parse("150:422"), MergePair(150, 422))
        for bad in ("150", "150:", ":422", "0:422", "a:b", "150:422:1", "-1:2"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                MergePair.parse(bad)


class RetiredUrlTests(MergeFixture):
    def test_the_retired_public_url_redirects_once_to_the_kept_event(self) -> None:
        merge_pair(self.pair, apply=True)
        target = canonical_detail_path(self.keep.content_id)

        for source in (
            f"/events/{self.duplicate_public_id}/{self.duplicate_slug}",
            f"/events/{self.duplicate_public_id}/any-stale-slug",
            f"/events/{self.duplicate_public_id}",
            # Trailing-slash spellings: the slash alias answers them in the same single hop.
            f"/events/{self.duplicate_public_id}/{self.duplicate_slug}/",
            f"/events/{self.duplicate_public_id}/any-stale-slug/",
            f"/events/{self.duplicate_public_id}/",
        ):
            with self.subTest(source=source):
                response = self.client.get(f"{source}?utm_source=x", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{target}?utm_source=x")
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")

        register = self.client.get(
            f"/events/{self.duplicate_public_id}/{self.duplicate_slug}/register", follow=False
        )
        self.assertEqual(register.status_code, 302)
        self.assertEqual(register.headers["Location"], target)

    def test_a_never_assigned_public_id_still_404s(self) -> None:
        merge_pair(self.pair, apply=True)
        unused = (Event.objects.order_by("-public_id").first().public_id or 0) + 1000

        self.assertEqual(self.client.get(f"/events/{unused}/anything").status_code, 404)
        self.assertEqual(self.client.get(f"/events/{unused}/anything/").status_code, 404)
        self.assertEqual(self.client.get(f"/events/{unused}/").status_code, 404)
        self.assertEqual(self.client.get(f"/events/{unused}/anything/register").status_code, 404)

    def test_the_kept_events_own_slash_spelling_still_reaches_its_canonical(self) -> None:
        merge_pair(self.pair, apply=True)
        target = canonical_detail_path(self.keep.content_id)

        response = self.client.get(f"/events/{self.keep.public_id}/", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], target)

    def test_a_retired_public_id_is_never_reassigned(self) -> None:
        merge_pair(self.pair, apply=True)

        with self.assertRaisesMessage(EventIdentityError, "event_public_id_retired"):
            create_event_identity(
                event_type="webinar",
                speakers=[("synthetic-speaker", "Synthetic Speaker")],
                title="Someone Else",
                source_repository=LEGACY_REPOSITORY,
                source_revision="a" * 40,
                source_key="2030-01-01-someone-else",
                public_id=self.duplicate_public_id,
            )


class MergedSourceIdentityTests(MergeFixture):
    """The duplicate's provider identity keeps resolving, to the kept event."""

    def test_ingest_lookups_follow_the_merge_and_content_lookups_do_not(self) -> None:
        merge_pair(self.pair, apply=True)
        followed = resolve_source_identity(
            repository=LUMA_REPOSITORY,
            revision=LUMA_REVISION,
            source_key="evt-Synthetic1",
            follow_merges=True,
        )

        self.assertEqual(followed, self.keep)
        with self.assertRaises(EventIdentityNotFound):
            resolve_source_identity(
                repository=LUMA_REPOSITORY, revision=LUMA_REVISION, source_key="evt-Synthetic1"
            )

    def test_the_merged_provider_identity_cannot_be_minted_again(self) -> None:
        from scripts.prod.registrant_import import create_provider_event_identity

        merge_pair(self.pair, apply=True)

        with self.assertRaisesMessage(EventIdentityError, "source_identity_merged"):
            create_provider_event_identity(
                event_type="webinar",
                speakers=[("synthetic-speaker", "Synthetic Speaker")],
                provider="luma",
                external_event_identifier="evt-Synthetic1",
                title="Again",
            )

    def test_a_registrant_refresh_lands_on_the_kept_event(self) -> None:
        from scripts.prod.registrant_import import (
            PendingEventRegistrants,
            RegistrantRow,
            import_registrants,
        )

        # The duplicate's rows came from a completed import, so a refresh replaces them.
        EventRegistrantImportProgress.objects.create(
            provider="luma", external_event_identifier="evt-Synthetic1", completed=True
        )
        merge_pair(self.pair, apply=True)
        rows = tuple(
            RegistrantRow(
                external_registrant_identifier=f"guest-{index}",
                normalized_email=f"person-{index}@example.test",
                status="approved",
                registered_at_raw="",
            )
            for index in range(4)
        )

        report = import_registrants(
            provider="luma",
            pending=[
                PendingEventRegistrants(
                    external_event_identifier="evt-Synthetic1", read_rows=lambda: rows
                )
            ],
            refresh=True,
        )

        self.assertEqual(report.events_awaiting_identity, 0)
        self.assertEqual(report.events_refreshed, 1)
        self.assertEqual(EventRegistration.objects.filter(event=self.keep).count(), 4)
