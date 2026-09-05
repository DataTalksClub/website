from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from core.services import ServiceContext
from events.current_registration import (
    CurrentRegistrationInputError,
    load_current_registration_input,
)
from events.importers import source_reference_digest
from events.models import HistoricalRegistrationAggregateRevision, HistoricalRegistrationSourceRun
from events.queries import published_event_records
from events.services import (
    HistoricalRegistrationConflict,
    activate_explicit_current_source,
    public_registration_total,
    stage_derived_source,
)
from jobs.models import DurableJob
from scripts.prod.registration_sources.luma import derive_luma


def tree_checksum(root: Path) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class CurrentRegistrationInputTests(SimpleTestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.path = Path(self.temporary.name) / "current-registration.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_input_is_bounded_exact_and_sorted_without_attendee_fields(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "mapping_set_revision": 7,
                    "mappings": [
                        {
                            "provider": "eventbrite",
                            "provider_event_identity": "123456",
                            "canonical_source": {
                                "repository": "DataTalksClub/datatalksclub.github.io",
                                "revision": "b" * 40,
                                "source_key": "second-event",
                            },
                        },
                        {
                            "provider": "luma",
                            "provider_event_identity": "synthetic-current-id",
                            "canonical_source": {
                                "repository": "DataTalksClub/datatalksclub.github.io",
                                "revision": "a" * 40,
                                "source_key": "first-event",
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        parsed = load_current_registration_input(self.path)

        self.assertEqual(parsed.mapping_set_revision, 7)
        self.assertEqual(
            [(item.provider, item.provider_event_identity) for item in parsed.mappings],
            [("eventbrite", "123456"), ("luma", "synthetic-current-id")],
        )
        self.assertNotIn("email", repr(parsed))
        self.assertNotIn("guest", repr(parsed))

    def test_input_rejects_title_date_and_attendee_fields(self) -> None:
        payload = {
            "schema_version": 1,
            "mapping_set_revision": 1,
            "mappings": [
                {
                    "provider": "luma",
                    "provider_event_identity": "synthetic-current-id",
                    "canonical_source": {
                        "repository": "DataTalksClub/datatalksclub.github.io",
                        "revision": "a" * 40,
                        "source_key": "first-event",
                    },
                    "title": "A tempting title match",
                    "date": "2026-09-01",
                    "email": "must-not-enter-the-contract@example.test",
                }
            ],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesMessage(CurrentRegistrationInputError, "mapping_shape_invalid"):
            load_current_registration_input(self.path)

    def test_symlinked_input_is_rejected(self) -> None:
        target = Path(self.temporary.name) / "target.json"
        target.write_text("{}", encoding="utf-8")
        self.path.symlink_to(target)

        with self.assertRaisesMessage(CurrentRegistrationInputError, "input_symlink"):
            load_current_registration_input(self.path)


class ExplicitCurrentRegistrationTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.source = Path(self.temporary.name) / "luma"
        self.source.mkdir()
        self.event = published_event_records()[0]
        self.context = ServiceContext(
            correlation_id="explicit-current-registration-test",
            actor_ref="system:explicit-current-registration-test",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_source(self) -> tuple[str, str]:
        current_id = "synthetic-current-id"
        legacy_id = "synthetic-legacy-id"
        current_url = "https://example.test/synthetic-current"
        legacy_url = "https://example.test/synthetic-legacy"
        for stem, event_id, event_url in (
            ("current", current_id, current_url),
            ("legacy", legacy_id, legacy_url),
        ):
            (self.source / f"{stem}.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": event_id,
                        "event_url": event_url,
                    }
                ),
                encoding="utf-8",
            )
        with (self.source / "current.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writeheader()
            for index, status in enumerate(("approved", "approved", "declined")):
                writer.writerow(
                    {
                        "event_id": current_id,
                        "guest_id": f"current-guest-{index}",
                        "approval_status": status,
                        "ignored_email": f"current-private-{index}@example.test",
                    }
                )
        with (self.source / "legacy.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": legacy_id,
                    "guest_id": "legacy-guest-0",
                    "approval_status": "approved",
                    "ignored_email": "legacy-private@example.test",
                }
            )
        return current_id, legacy_id

    def _derived(self):
        current_id, legacy_id = self._write_source()
        provenance = self.event["provenance"]
        derived = derive_luma(
            self.source,
            expected_checksum=tree_checksum(self.source),
            mapping_bridge={
                current_id: {
                    "repository": provenance["repository"],
                    "revision": provenance["revision"],
                    "source_key": provenance["source_key"],
                    "slug": self.event["slug"],
                }
            },
            allow_partial_mapping=True,
        )
        return derived, current_id, legacy_id

    def test_explicit_current_bridge_activates_only_current_count_and_replay_is_idempotent(
        self,
    ) -> None:
        derived, current_id, legacy_id = self._derived()
        run, created = stage_derived_source(
            provider="luma",
            derived=derived,
            reference_digest=source_reference_digest("synthetic-current-source"),
            mapping_set_revision=1,
            actor=None,
            context=self.context,
        )

        self.assertTrue(created)
        current = run.aggregate_revisions.get(external_event_identifier=current_id)
        legacy = run.aggregate_revisions.get(external_event_identifier=legacy_id)
        self.assertEqual(str(current.event_id), self.event["identity_id"])
        self.assertIsNone(legacy.event_id)
        self.assertEqual(run.aggregate_revisions.count(), 2)
        self.assertNotIn("current-private", repr(run))
        self.assertNotIn("legacy-private", repr(run))

        with patch("django_q.tasks.async_task"):
            activated = activate_explicit_current_source(
                run.id,
                external_event_identifiers=(current_id,),
                reason_code="current_event_activation",
                actor=None,
                context=self.context,
            )

        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 2)
        self.assertEqual(activated.state, HistoricalRegistrationSourceRun.State.ACTIVE)
        current.refresh_from_db()
        legacy.refresh_from_db()
        self.assertIsNone(legacy.event_id)
        self.assertEqual(current.state, HistoricalRegistrationAggregateRevision.State.ACTIVE)
        self.assertEqual(legacy.state, HistoricalRegistrationAggregateRevision.State.STAGED)
        detail = self.client.get(self.event["public_path"])
        self.assertContains(detail, "2 registered")
        body = detail.content.decode()
        for forbidden in (
            "synthetic-current-id",
            "synthetic-legacy-id",
            "current-private",
            "legacy-private",
            "current-guest",
            "legacy-guest",
        ):
            self.assertNotIn(forbidden, body)

        with patch("django_q.tasks.async_task"):
            replay, replay_created = stage_derived_source(
                provider="luma",
                derived=derived,
                reference_digest=source_reference_digest("synthetic-current-source"),
                mapping_set_revision=1,
                actor=None,
                context=self.context,
            )
            replay_activated = activate_explicit_current_source(
                replay.id,
                external_event_identifiers=(current_id,),
                reason_code="current_event_activation",
                actor=None,
                context=self.context,
            )

        self.assertFalse(replay_created)
        self.assertEqual(replay.id, run.id)
        self.assertEqual(replay_activated.id, run.id)
        self.assertEqual(HistoricalRegistrationSourceRun.objects.count(), 1)
        self.assertEqual(run.aggregate_revisions.count(), 2)
        self.assertEqual(DurableJob.objects.count(), 1)
        self.assertEqual(public_registration_total(self.event), total)

    def test_explicit_mapping_cannot_retarget_an_existing_provider_identity(self) -> None:
        derived, current_id, _legacy_id = self._derived()
        run, _created = stage_derived_source(
            provider="luma",
            derived=derived,
            reference_digest=source_reference_digest("synthetic-current-source"),
            mapping_set_revision=1,
            actor=None,
            context=self.context,
        )
        other_event = published_event_records()[1]
        other_provenance = other_event["provenance"]
        changed_derived = derive_luma(
            self.source,
            expected_checksum=tree_checksum(self.source),
            mapping_bridge={
                current_id: {
                    "repository": other_provenance["repository"],
                    "revision": other_provenance["revision"],
                    "source_key": other_provenance["source_key"],
                    "slug": other_event["slug"],
                }
            },
            allow_partial_mapping=True,
        )

        with self.assertRaisesMessage(HistoricalRegistrationConflict, "explicit_mapping_conflict"):
            stage_derived_source(
                provider="luma",
                derived=changed_derived,
                reference_digest=source_reference_digest("synthetic-current-source"),
                mapping_set_revision=1,
                actor=None,
                context=self.context,
            )

        run.refresh_from_db()
        aggregate = run.aggregate_revisions.get(external_event_identifier=current_id)
        self.assertEqual(run.state, HistoricalRegistrationSourceRun.State.STAGED)
        self.assertEqual(str(aggregate.event_id), self.event["identity_id"])
        self.assertEqual(HistoricalRegistrationSourceRun.objects.count(), 1)

    def test_luma_provider_url_remains_an_exact_legacy_bridge_key(self) -> None:
        current_id, _legacy_id = self._write_source()
        event_url = "https://example.test/synthetic-current"
        provenance = self.event["provenance"]
        derived = derive_luma(
            self.source,
            expected_checksum=tree_checksum(self.source),
            mapping_bridge={
                event_url: {
                    "repository": provenance["repository"],
                    "revision": provenance["revision"],
                    "source_key": provenance["source_key"],
                    "slug": self.event["slug"],
                }
            },
        )

        self.assertEqual(derived.candidates[0].external_event_identifier, current_id)
        self.assertIsNotNone(derived.candidates[0].proposal)

    def test_explicit_mapping_does_not_activate_quarantined_current_candidate(self) -> None:
        current_id, _legacy_id = self._write_source()
        with (self.source / "current.csv").open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writerow(
                {
                    "event_id": current_id,
                    "guest_id": "current-guest-unknown",
                    "approval_status": "unknown",
                    "ignored_email": "quarantine-private@example.test",
                }
            )
        provenance = self.event["provenance"]
        derived = derive_luma(
            self.source,
            expected_checksum=tree_checksum(self.source),
            mapping_bridge={
                current_id: {
                    "repository": provenance["repository"],
                    "revision": provenance["revision"],
                    "source_key": provenance["source_key"],
                    "slug": self.event["slug"],
                }
            },
            allow_partial_mapping=True,
        )

        with self.assertRaisesMessage(
            HistoricalRegistrationConflict, "explicit_mapping_not_activatable"
        ):
            stage_derived_source(
                provider="luma",
                derived=derived,
                reference_digest=source_reference_digest("synthetic-quarantined-source"),
                mapping_set_revision=1,
                actor=None,
                context=self.context,
            )
        self.assertFalse(HistoricalRegistrationSourceRun.objects.exists())
