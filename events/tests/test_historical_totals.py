from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from content.public_data import event_groups, public_projection
from core.models import AuditEvent
from core.services import ServiceContext
from events.importers import source_reference_digest
from events.models import (
    HistoricalRegistrationAggregateRevision,
    HistoricalRegistrationAggregateSlot,
    HistoricalRegistrationPointerDisplacement,
    HistoricalRegistrationSourceRun,
)
from events.services import (
    HistoricalRegistrationConflict,
    HistoricalRegistrationInvalid,
    activate_source,
    public_registration_total,
    registration_total_preview,
    replace_aggregate_with_row_projection,
    restore_aggregate_from_row_projection,
    rollback_source,
    safe_source_facts,
    stage_registered_source,
    validate_source,
)
from jobs.models import DurableJob


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


class HistoricalRegistrationTotalTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.source = Path(self.temporary.name) / "source"
        self.source.mkdir()
        self.event = public_projection()["events"][0]
        self.user = get_user_model().objects.create_user(
            username="synthetic-historical-reviewer",
            email="synthetic-reviewer@example.test",
            password="synthetic-password-112",
            is_staff=True,
        )
        self.context = ServiceContext(
            correlation_id="historical-test",
            actor_ref=f"user:{self.user.pk}",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_registration_total_selection_requires_exact_uuid_identity(self) -> None:
        with self.assertRaisesMessage(
            HistoricalRegistrationInvalid,
            "canonical_event_unavailable",
        ):
            registration_total_preview(self.event["slug"])

    def _write_source(self, statuses: tuple[str, ...], *, include_bridge: bool = True) -> dict:
        event_id = "synthetic-provider-event"
        event_url = "https://example.test/synthetic-provider-event"
        (self.source / "synthetic.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "event_url": event_url,
                }
            ),
            encoding="utf-8",
        )
        with (self.source / "synthetic.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writeheader()
            for index, status in enumerate(statuses):
                writer.writerow(
                    {
                        "event_id": event_id,
                        "guest_id": f"synthetic-guest-{index}",
                        "approval_status": status,
                        "ignored_email": f"private-canary-{index}@example.test",
                    }
                )
        provenance = self.event["provenance"]
        config: dict = {
            "provider": "luma",
            "reconciliation_profile": "synthetic",
            "path": str(self.source),
            "sha256": tree_checksum(self.source),
        }
        if include_bridge:
            config["mapping_bridge"] = {
                event_url: {
                    "repository": provenance["repository"],
                    "revision": provenance["revision"],
                    "source_key": provenance["source_key"],
                    "slug": self.event["slug"],
                }
            }
        return {"synthetic-luma": config}

    def _stage(self, statuses: tuple[str, ...] = ("approved",), *, include_bridge: bool = True):
        registry = self._write_source(statuses, include_bridge=include_bridge)
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            run, created = stage_registered_source(
                provider="luma",
                source_reference="synthetic-luma",
                mapping_set_revision=1,
                actor=self.user,
                context=self.context,
            )
        return run, created, registry

    def _map_validate_activate(self, statuses: tuple[str, ...] = ("approved",)):
        # The registry's own `mapping_bridge` (see `_write_source`) names the exact
        # provider-event-to-canonical-event pair, so staging resolves the aggregate
        # directly -- there is no separate mapping row or review step to act on.
        run, _created, registry = self._stage(statuses)
        aggregate = run.aggregate_revisions.get(
            state=HistoricalRegistrationAggregateRevision.State.STAGED
        )
        self.assertIsNotNone(aggregate.event_id)
        validate_source(
            run.id,
            reason_code="validated_counts",
            actor=self.user,
            context=self.context,
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                run.id,
                reason_code="approved_activation",
                actor=self.user,
                context=self.context,
            )
        run.refresh_from_db()
        aggregate.refresh_from_db()
        return run, aggregate, registry

    def _validated_run(
        self,
        *,
        provider: str,
        count: int,
        combination_policy: str,
        suffix: str,
    ) -> HistoricalRegistrationSourceRun:
        run = HistoricalRegistrationSourceRun.objects.create(
            provider=provider,
            adapter_version="synthetic-v1",
            schema_version="synthetic-v1",
            whole_source_checksum=hashlib.sha256(f"source-{suffix}".encode()).hexdigest(),
            source_reference_digest=hashlib.sha256(f"reference-{suffix}".encode()).hexdigest(),
            manifest_entry_total=1,
            manifest_event_total=1,
            parsed_row_total=count,
            eligible_row_total=count,
            excluded_row_total=0,
            quarantined_event_total=0,
            status_totals={"eligible": count},
            state_totals={"validated": 1},
            reason_codes=[],
            mapping_set_revision=1,
            policy_version="historical-registration-v1",
            state=HistoricalRegistrationSourceRun.State.VALIDATED,
            actor=self.user,
            actor_ref=f"user:{self.user.pk}",
        )
        HistoricalRegistrationAggregateRevision.objects.create(
            source_run=run,
            external_event_identifier=f"synthetic-{suffix}",
            event_id=self.event["identity_id"],
            eligible_count=count,
            excluded_count=0,
            quarantined_count=0,
            coverage_boundary="historical",
            status_policy_version="historical-status-v1",
            combination_policy=combination_policy,
            aggregate_checksum=hashlib.sha256(f"aggregate-{suffix}".encode()).hexdigest(),
            state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
        )
        return run

    def test_aggregate_models_store_no_attendee_registration_or_answer_fields(self) -> None:
        """The *aggregate-only* provenance models never gain an attendee field.

        Attendee-level rows now do land in the database (see
        events.models.EventRegistrantIdentity / EventRegistration, and
        _docs/runbooks/ingest-script-inventory.md section 9) -- but never in
        these five models, whose whole contract is aggregate counts and
        pointers, never an identity. That boundary is what this test locks in.
        """

        field_names = {
            field.name
            for model in (
                HistoricalRegistrationSourceRun,
                HistoricalRegistrationAggregateRevision,
                HistoricalRegistrationAggregateSlot,
                HistoricalRegistrationPointerDisplacement,
            )
            for field in model._meta.get_fields()
        }
        prohibited = {
            "attendee",
            "email",
            "guest_id",
            "answer",
            "consent",
            "source_path",
            "payload",
            "filename",
        }
        self.assertTrue(field_names.isdisjoint(prohibited))

    def test_registration_fact_model_stores_no_direct_identity_value(self) -> None:
        """EventRegistration is the one model that does hold an attendee fact.

        Even so, it never stores a name, email, or phone number directly --
        only a status, a timestamp, and an opaque provider token used solely
        for idempotent replay. The real identity value (an email) lives only
        in EventRegistrantIdentity.normalized_email, guarded by
        events.registrant_import's account-first matching.
        """

        from events.models import EventRegistration

        field_names = {field.name for field in EventRegistration._meta.get_fields()}
        prohibited = {
            "email",
            "name",
            "first_name",
            "last_name",
            "phone_number",
            "answer",
            "consent",
            "payload",
        }
        self.assertTrue(field_names.isdisjoint(prohibited))

    def test_source_run_and_aggregate_provenance_are_immutable(self) -> None:
        run, _created, _registry = self._stage()
        aggregate = run.aggregate_revisions.get()

        run.eligible_row_total += 1
        with self.assertRaisesMessage(ValueError, "source-run aggregate provenance"):
            run.save()
        aggregate.eligible_count += 1
        with self.assertRaisesMessage(ValueError, "aggregate revision provenance"):
            aggregate.save()

    def test_aggregate_event_resolution_is_immutable_once_set(self) -> None:
        run, aggregate, _registry = self._map_validate_activate()
        other_event = public_projection()["events"][1]

        aggregate.event_id = other_event["identity_id"]
        with self.assertRaisesMessage(ValueError, "event resolution is immutable"):
            aggregate.save()

    def test_replay_is_a_deterministic_noop_and_reference_is_stored_only_as_digest(self) -> None:
        run, created, registry = self._stage(("approved", "declined"))
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            replay, replay_created = stage_registered_source(
                provider="luma",
                source_reference="synthetic-luma",
                mapping_set_revision=1,
                actor=self.user,
                context=self.context,
            )
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(run.id, replay.id)
        self.assertEqual(HistoricalRegistrationSourceRun.objects.count(), 1)
        self.assertEqual(
            run.source_reference_digest,
            source_reference_digest("synthetic-luma"),
        )
        self.assertNotIn("synthetic-luma", repr(run.__dict__))

    def test_unresolved_aggregate_blocks_validation_and_public_count(self) -> None:
        run, _created, _registry = self._stage(include_bridge=False)
        with self.assertRaisesMessage(HistoricalRegistrationConflict, "aggregate_not_resolved"):
            validate_source(
                run.id,
                reason_code="attempted_validation",
                actor=self.user,
                context=self.context,
            )
        self.assertIsNone(public_registration_total(self.event))
        response = self.client.get(self.event["public_path"])
        self.assertNotContains(response, "data-registration-total-revision")

    def test_activation_publishes_only_exact_count_and_zero_ttl_with_durable_intent(self) -> None:
        run, _mapping, _registry = self._map_validate_activate(("approved", "approved", "declined"))
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 2)

        detail = self.client.get(self.event["public_path"])
        self.assertContains(detail, "2 registered")
        self.assertEqual(detail.headers["Cache-Control"], "no-store, max-age=0, s-maxage=0")
        self.assertEqual(detail.headers["X-Event-Registration-Total-Revision"], str(total.revision))
        body = detail.content.decode()
        for forbidden in (
            "synthetic-provider-event",
            "private-canary",
            "Attending",
            "attendee-card",
            "attendee-avatar",
        ):
            self.assertNotIn(forbidden, body)

        hub = self.client.get("/events")
        self.assertNotContains(hub, "data-registration-total-revision")
        self.assertEqual(DurableJob.objects.count(), 1)
        intent = DurableJob.objects.get()
        self.assertEqual(intent.handler, "events.registration_total.invalidate")
        self.assertEqual(intent.payload["path"], self.event["public_path"])
        self.assertEqual(intent.payload["total_revision"], total.revision)
        self.assertEqual(run.state, HistoricalRegistrationSourceRun.State.ACTIVE)

    def test_complete_zero_is_rendered_but_rollback_omits_incomplete_total(self) -> None:
        run, _mapping, _registry = self._map_validate_activate(("declined",))
        response = self.client.get(self.event["public_path"])
        self.assertContains(response, "0 registered")
        with patch("django_q.tasks.async_task"):
            rollback_source(
                run.id,
                reason_code="operator_rollback",
                actor=self.user,
                context=self.context,
            )
        self.assertIsNone(public_registration_total(self.event))
        response = self.client.get(self.event["public_path"])
        self.assertNotContains(response, "registered")
        self.assertEqual(DurableJob.objects.count(), 2)

    def test_past_event_labels_the_registration_count_as_registered(self) -> None:
        self.event = event_groups().recent[-1]
        self._map_validate_activate(("approved", "approved"))

        response = self.client.get(self.event["public_path"])

        self.assertContains(response, "2 registered")
        self.assertNotContains(response, "2 came")

    def test_cross_provider_replacement_never_adds_and_rollback_restores_prior_pointer(
        self,
    ) -> None:
        first = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="replacement",
            suffix="replacement-first",
        )
        second = self._validated_run(
            provider="eventbrite",
            count=5,
            combination_policy="replacement",
            suffix="replacement-second",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                first.id,
                reason_code="first_activation",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                second.id,
                reason_code="replacement_activation",
                actor=self.user,
                context=self.context,
            )
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 5)
        self.assertEqual(
            HistoricalRegistrationAggregateSlot.objects.filter(
                active_revision__isnull=False
            ).count(),
            1,
        )

        with patch("django_q.tasks.async_task"):
            rollback_source(
                second.id,
                reason_code="replacement_rollback",
                actor=self.user,
                context=self.context,
            )
        restored = public_registration_total(self.event)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.count, 3)

    def test_replacement_rollback_restores_complete_prior_additive_set(self) -> None:
        luma = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="additive_disjoint",
            suffix="additive-luma-before-replacement",
        )
        eventbrite = self._validated_run(
            provider="eventbrite",
            count=5,
            combination_policy="additive_disjoint",
            suffix="additive-eventbrite-before-replacement",
        )
        replacement = self._validated_run(
            provider="luma",
            count=7,
            combination_policy="replacement",
            suffix="replacement-over-additive-set",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                luma.id,
                reason_code="first_disjoint",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                eventbrite.id,
                reason_code="second_disjoint",
                actor=self.user,
                context=self.context,
            )
        before = public_registration_total(self.event)
        self.assertIsNotNone(before)
        assert before is not None
        self.assertEqual(before.count, 8)

        with patch("django_q.tasks.async_task"):
            activate_source(
                replacement.id,
                reason_code="replace_additive_set",
                actor=self.user,
                context=self.context,
            )
        active = public_registration_total(self.event)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.count, 7)
        replacement_aggregate = replacement.aggregate_revisions.get()
        self.assertEqual(replacement_aggregate.pointer_displacements.count(), 2)

        with patch("django_q.tasks.async_task"):
            rollback_source(
                replacement.id,
                reason_code="restore_additive_set",
                actor=self.user,
                context=self.context,
            )
        restored = public_registration_total(self.event)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.count, 8)
        self.assertEqual(
            sorted(
                HistoricalRegistrationAggregateSlot.objects.filter(
                    active_revision__isnull=False
                ).values_list("active_revision__eligible_count", flat=True)
            ),
            [3, 5],
        )
        self.assertEqual(
            set(
                HistoricalRegistrationAggregateRevision.objects.filter(
                    source_run__in=(luma, eventbrite)
                ).values_list("state", flat=True)
            ),
            {HistoricalRegistrationAggregateRevision.State.ACTIVE},
        )
        replacement_aggregate.refresh_from_db()
        self.assertEqual(
            replacement_aggregate.state,
            HistoricalRegistrationAggregateRevision.State.ROLLED_BACK,
        )
        displacement = replacement_aggregate.pointer_displacements.first()
        assert displacement is not None
        displacement.row_replacement_combination_policy = "replacement"
        with self.assertRaisesMessage(ValueError, "historical pointer displacement is immutable"):
            displacement.save()

    def test_same_run_candidates_cannot_replace_each_other_in_one_slot(self) -> None:
        luma = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="additive_disjoint",
            suffix="same-run-prior-luma",
        )
        eventbrite = self._validated_run(
            provider="eventbrite",
            count=5,
            combination_policy="additive_disjoint",
            suffix="same-run-prior-eventbrite",
        )
        replacement = self._validated_run(
            provider="luma",
            count=7,
            combination_policy="replacement",
            suffix="same-run-first-replacement",
        )
        HistoricalRegistrationAggregateRevision.objects.create(
            source_run=replacement,
            external_event_identifier="synthetic-same-run-second-replacement",
            event_id=self.event["identity_id"],
            eligible_count=9,
            excluded_count=0,
            quarantined_count=0,
            coverage_boundary="historical",
            status_policy_version="historical-status-v1",
            combination_policy=(
                HistoricalRegistrationAggregateRevision.CombinationPolicy.REPLACEMENT
            ),
            aggregate_checksum=hashlib.sha256(b"same-run-second-replacement").hexdigest(),
            state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                luma.id,
                reason_code="first_disjoint",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                eventbrite.id,
                reason_code="second_disjoint",
                actor=self.user,
                context=self.context,
            )
        before = public_registration_total(self.event)
        self.assertIsNotNone(before)
        assert before is not None
        self.assertEqual(before.count, 8)

        with (
            patch("django_q.tasks.async_task"),
            self.assertRaisesMessage(HistoricalRegistrationConflict, "same_run_slot_collision"),
        ):
            activate_source(
                replacement.id,
                reason_code="ambiguous_same_run_replacement",
                actor=self.user,
                context=self.context,
            )

        replacement.refresh_from_db()
        self.assertEqual(replacement.state, HistoricalRegistrationSourceRun.State.VALIDATED)
        self.assertEqual(
            set(replacement.aggregate_revisions.values_list("state", flat=True)),
            {HistoricalRegistrationAggregateRevision.State.VALIDATED},
        )
        self.assertFalse(
            replacement.aggregate_revisions.filter(
                state=HistoricalRegistrationAggregateRevision.State.ACTIVE
            ).exists()
        )
        self.assertFalse(HistoricalRegistrationPointerDisplacement.objects.exists())
        self.assertEqual(
            sorted(
                HistoricalRegistrationAggregateSlot.objects.filter(
                    active_revision__isnull=False
                ).values_list("active_revision__eligible_count", flat=True)
            ),
            [3, 5],
        )
        preserved = public_registration_total(self.event)
        self.assertIsNotNone(preserved)
        assert preserved is not None
        self.assertEqual(preserved.count, 8)

    def test_additive_rollback_removes_only_its_own_pointer(self) -> None:
        first = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="additive_disjoint",
            suffix="additive-remains",
        )
        second = self._validated_run(
            provider="eventbrite",
            count=5,
            combination_policy="additive_disjoint",
            suffix="additive-rolled-back",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                first.id,
                reason_code="first_disjoint",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                second.id,
                reason_code="second_disjoint",
                actor=self.user,
                context=self.context,
            )
            rollback_source(
                second.id,
                reason_code="remove_second_disjoint",
                actor=self.user,
                context=self.context,
            )
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 3)
        remaining = HistoricalRegistrationAggregateSlot.objects.get(active_revision__isnull=False)
        self.assertIsNotNone(remaining.active_revision)
        assert remaining.active_revision is not None
        self.assertEqual(remaining.active_revision.source_run_id, first.id)

    def test_replacement_rollback_restores_row_projection_pointer(self) -> None:
        aggregate_run = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="replacement",
            suffix="aggregate-before-row-pointer",
        )
        replacement_run = self._validated_run(
            provider="eventbrite",
            count=7,
            combination_policy="replacement",
            suffix="aggregate-over-row-pointer",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                aggregate_run.id,
                reason_code="aggregate_activation",
                actor=self.user,
                context=self.context,
            )
        slot = HistoricalRegistrationAggregateSlot.objects.get(active_revision__isnull=False)
        row_revision = uuid.uuid4()
        with patch("django_q.tasks.async_task"):
            replace_aggregate_with_row_projection(
                event_id=self.event["identity_id"],
                provider="luma",
                coverage_boundary="historical",
                replacement_revision_id=row_revision,
                eligible_count=4,
                expected_slot_revision=slot.revision,
                reason_code="reviewed_row_replacement",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                replacement_run.id,
                reason_code="replace_row_pointer",
                actor=self.user,
                context=self.context,
            )
            rollback_source(
                replacement_run.id,
                reason_code="restore_row_pointer",
                actor=self.user,
                context=self.context,
            )
        restored_slot = HistoricalRegistrationAggregateSlot.objects.get(
            replacement_revision_id=row_revision
        )
        self.assertEqual(restored_slot.replacement_eligible_count, 4)
        self.assertIsNone(restored_slot.active_revision_id)
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 4)

    def test_only_explicit_additive_disjoint_provider_coverage_is_summed(self) -> None:
        first = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="additive_disjoint",
            suffix="additive-first",
        )
        second = self._validated_run(
            provider="eventbrite",
            count=5,
            combination_policy="additive_disjoint",
            suffix="additive-second",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                first.id,
                reason_code="first_disjoint",
                actor=self.user,
                context=self.context,
            )
            activate_source(
                second.id,
                reason_code="second_disjoint",
                actor=self.user,
                context=self.context,
            )
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 8)

    def test_aggregate_to_row_replacement_pointer_is_exclusive_and_reversible(self) -> None:
        run = self._validated_run(
            provider="luma",
            count=3,
            combination_policy="replacement",
            suffix="row-replacement",
        )
        with patch("django_q.tasks.async_task"):
            activate_source(
                run.id,
                reason_code="aggregate_activation",
                actor=self.user,
                context=self.context,
            )
        slot = HistoricalRegistrationAggregateSlot.objects.get(active_revision__isnull=False)
        aggregate_id = slot.active_revision_id
        assert aggregate_id is not None
        replacement_id = uuid.uuid4()
        with patch("django_q.tasks.async_task"):
            replaced = replace_aggregate_with_row_projection(
                event_id=self.event["identity_id"],
                provider="luma",
                coverage_boundary="historical",
                replacement_revision_id=replacement_id,
                eligible_count=4,
                expected_slot_revision=slot.revision,
                reason_code="reviewed_row_replacement",
                actor=self.user,
                context=self.context,
            )
        self.assertIsNone(replaced.active_revision_id)
        self.assertEqual(replaced.replacement_revision_id, replacement_id)
        self.assertEqual(replaced.replacement_eligible_count, 4)
        self.assertEqual(
            HistoricalRegistrationAggregateRevision.objects.get(id=aggregate_id).state,
            HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
        )
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 4)

        with patch("django_q.tasks.async_task"):
            restored = restore_aggregate_from_row_projection(
                event_id=self.event["identity_id"],
                provider="luma",
                coverage_boundary="historical",
                expected_slot_revision=replaced.revision,
                reason_code="reviewed_replacement_rollback",
                actor=self.user,
                context=self.context,
            )
        self.assertEqual(restored.active_revision_id, aggregate_id)
        self.assertIsNone(restored.replacement_revision_id)
        total = public_registration_total(self.event)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 3)

    def test_audit_and_database_never_store_ignored_attendee_canary(self) -> None:
        self._map_validate_activate(("approved",))
        evidence = json.dumps(
            {
                "runs": list(HistoricalRegistrationSourceRun.objects.values()),
                "aggregates": list(HistoricalRegistrationAggregateRevision.objects.values()),
                "audits": list(AuditEvent.objects.values()),
            },
            default=str,
        )
        # Attendee-level values (an email, a guest id) never land here, at rest or in an
        # audit event -- unlike the provider's own *event* identifier just below, which
        # is not attendee PII (it is part of the event's public Luma/Eventbrite URL) and
        # is legitimately stored at rest, same as it always was on the removed
        # HistoricalEventMapping row; it is masked only at the API-serialization
        # boundary (see events.services._mask_identifier / get_run_detail).
        self.assertNotIn("private-canary", evidence)
        self.assertNotIn("synthetic-guest", evidence)
        self.assertIn("synthetic-provider-event", evidence)

    def test_safe_acceptance_facts_are_exact_aggregate_only_values(self) -> None:
        facts = safe_source_facts()
        self.assertEqual(
            facts["luma"],
            {
                "manifest_event_total": 159,
                "paired_json_total": 159,
                "paired_csv_total": 159,
                "parsed_row_total": 50_505,
                "unique_provider_event_guest_total": 50_505,
                "eligible_row_total": 50_456,
                "excluded_row_total": 49,
                "status_totals": {"approved": 50_456, "declined": 49},
                "nonempty_event_total": 157,
                "empty_event_total": 2,
                "exact_proposal_total": 64,
                "review_required_total": 95,
            },
        )
        self.assertEqual(facts["eventbrite"]["manifest_entry_total"], 210)
        self.assertEqual(facts["eventbrite"]["csv_total"], 209)
        self.assertEqual(facts["eventbrite"]["parsed_row_total"], 24_001)
        self.assertEqual(facts["eventbrite"]["exact_bridge_total"], 200)
        self.assertEqual(facts["eventbrite"]["review_required_total"], 9)
        self.assertEqual(facts["eventbrite"]["source_missing_total"], 27)
