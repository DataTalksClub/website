from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.db import DatabaseError, OperationalError, close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from core.idempotency import IdempotencyConflict
from core.models import AuditEvent, RevisionConflict, Sponsor, SponsorRevision
from core.sponsors import (
    InvalidSponsor,
    SponsorRevisionConflict,
    archive_sponsor,
    create_sponsor,
    export_sponsor_directory,
    get_sponsor,
    list_sponsors,
    public_events_hub_sponsors,
    reactivate_sponsor,
    resolve_public_sponsors,
    update_sponsor,
)


def _create(payload: dict, *, key: str | None = None, actor_ref: str = "user:188"):
    return create_sponsor(
        payload=payload,
        source="studio",
        idempotency_key=key or str(uuid.uuid4()),
        actor_ref=actor_ref,
    )


def _payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "key": "acme",
        "name": "Acme Analytics",
        "url": "https://acme.example",
        "tagline": "Data for everyone",
        "lifecycle": "draft",
        "assignments": [
            {"placement": "events_hub", "position": 1, "enabled": True},
        ],
    }
    data.update(overrides)
    return data


class SponsorServiceTests(TestCase):
    def test_create_validates_fields_and_writes_one_revision_and_audit(self) -> None:
        result = _create(_payload(name="  Acme Analytics  "))
        self.assertFalse(result.replayed)
        self.assertEqual(result.sponsor["name"], "Acme Analytics")
        self.assertEqual(result.sponsor["revision"], 1)
        self.assertEqual(result.sponsor["lifecycle"], "draft")
        self.assertEqual(Sponsor.objects.count(), 1)
        self.assertEqual(SponsorRevision.objects.count(), 1)
        audit = AuditEvent.objects.get(action="core.sponsor.created")
        self.assertEqual(audit.target_label, "acme")
        serialized = str(audit.changes) + str(audit.metadata)
        self.assertNotIn("Acme Analytics", serialized)
        self.assertNotIn("https://acme.example", serialized)
        self.assertNotIn("Data for everyone", serialized)

    def test_duplicate_key_including_archived_is_rejected(self) -> None:
        created = _create(_payload())
        archive_sponsor(
            sponsor_id=created.sponsor["id"],
            confirmed=True,
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        with self.assertRaises(InvalidSponsor):
            _create(_payload())

    def test_markup_http_and_overlength_are_rejected_before_write(self) -> None:
        cases = (
            _payload(name="Unsafe <script>"),
            _payload(tagline="line\nbreak"),
            _payload(url="http://acme.example"),
            _payload(url="https://user:pass@acme.example"),
            _payload(key="ACME"),
            _payload(name="x" * 121),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(InvalidSponsor):
                    _create(payload)
        self.assertFalse(Sponsor.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="core.sponsor.created").exists())

    def test_rekeying_is_impossible_and_update_is_revision_guarded(self) -> None:
        created = _create(_payload())
        with self.assertRaises(InvalidSponsor):
            update_sponsor(
                sponsor_id=created.sponsor["id"],
                payload={
                    "key": "other",
                    "name": "Renamed",
                    "lifecycle": "draft",
                    "assignments": [],
                },
                expected_revision=1,
                source="studio",
                idempotency_key=str(uuid.uuid4()),
                actor_ref="user:188",
            )
        updated = update_sponsor(
            sponsor_id=created.sponsor["id"],
            payload={
                "name": "Acme Analytics",
                "url": "https://acme.example",
                "tagline": "Updated",
                "lifecycle": "active",
                "assignments": [
                    {"placement": "events_hub", "position": 1, "enabled": True},
                ],
            },
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        self.assertEqual(updated.sponsor["revision"], 2)
        self.assertEqual(updated.sponsor["lifecycle"], "active")
        with self.assertRaises(SponsorRevisionConflict):
            update_sponsor(
                sponsor_id=created.sponsor["id"],
                payload={
                    "name": "Stale",
                    "lifecycle": "active",
                    "assignments": [],
                },
                expected_revision=1,
                source="studio",
                idempotency_key=str(uuid.uuid4()),
                actor_ref="user:188",
            )
        self.assertEqual(Sponsor.objects.get().name, "Acme Analytics")

    def test_lifecycle_archive_and_reactivate_are_confirmed_and_append_only(self) -> None:
        created = _create(_payload(lifecycle="active"))
        archived = archive_sponsor(
            sponsor_id=created.sponsor["id"],
            confirmed=True,
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        self.assertEqual(archived.sponsor["lifecycle"], "archived")
        self.assertEqual(Sponsor.objects.count(), 1)
        self.assertEqual(SponsorRevision.objects.count(), 2)
        with self.assertRaises(InvalidSponsor):
            archive_sponsor(
                sponsor_id=created.sponsor["id"],
                confirmed=False,
                expected_revision=2,
                source="studio",
                idempotency_key=str(uuid.uuid4()),
                actor_ref="user:188",
            )
        reactivated = reactivate_sponsor(
            sponsor_id=created.sponsor["id"],
            confirmed=True,
            expected_revision=2,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        self.assertEqual(reactivated.sponsor["lifecycle"], "active")
        self.assertEqual(SponsorRevision.objects.count(), 3)
        self.assertEqual(
            set(AuditEvent.objects.values_list("action", flat=True)),
            {
                "core.sponsor.created",
                "core.sponsor.archived",
                "core.sponsor.reactivated",
            },
        )

    def test_placement_position_collision_and_active_cap(self) -> None:
        _create(_payload(lifecycle="active"))
        with self.assertRaises(InvalidSponsor):
            _create(_payload(key="beta", name="Beta", lifecycle="active"))
        for index in range(2, 25):
            _create(
                _payload(
                    key=f"sponsor-{index}",
                    name=f"Sponsor {index}",
                    lifecycle="active",
                    assignments=[
                        {"placement": "events_hub", "position": index, "enabled": True},
                    ],
                )
            )
        with self.assertRaises(InvalidSponsor):
            _create(
                _payload(
                    key="sponsor-25",
                    name="Sponsor 25",
                    lifecycle="active",
                    assignments=[
                        {"placement": "events_hub", "position": 25, "enabled": True},
                    ],
                )
            )

    def test_public_resolution_is_uncached_ordered_and_omits_non_active(self) -> None:
        _create(
            _payload(
                key="zeta",
                name="Zeta",
                lifecycle="active",
                assignments=[{"placement": "events_hub", "position": 2, "enabled": True}],
            )
        )
        _create(
            _payload(
                key="alpha",
                name="Alpha",
                lifecycle="active",
                assignments=[{"placement": "events_hub", "position": 1, "enabled": True}],
            )
        )
        _create(_payload(key="drafty", name="Drafty", lifecycle="draft", assignments=[]))
        archived = _create(
            _payload(
                key="old",
                name="Old",
                lifecycle="active",
                assignments=[{"placement": "events_hub", "position": 3, "enabled": True}],
            )
        )
        archive_sponsor(
            sponsor_id=archived.sponsor["id"],
            confirmed=True,
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        resolved = resolve_public_sponsors()
        self.assertEqual([item["name"] for item in resolved], ["Alpha", "Zeta"])
        alpha = get_sponsor(Sponsor.objects.get(key="alpha").id)
        assert alpha is not None
        archive_sponsor(
            sponsor_id=alpha["id"],
            confirmed=True,
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        self.assertEqual([item["name"] for item in resolve_public_sponsors()], ["Zeta"])

    def test_public_resolution_fails_closed_on_database_error(self) -> None:
        with mock.patch(
            "core.sponsors.resolve_public_sponsors",
            side_effect=DatabaseError("unavailable"),
        ):
            with self.assertLogs("core.sponsors", level="WARNING") as captured:
                self.assertEqual(public_events_hub_sponsors(), ())
        self.assertIn("DatabaseError", " ".join(captured.output))
        self.assertNotIn("password", " ".join(captured.output))

    def test_export_is_formula_safe_bounded_and_redacted(self) -> None:
        _create(_payload(name='=HYPERLINK("https://evil.invalid")', assignments=[]))
        result = export_sponsor_directory(
            confirmed=True,
            reason="directory review",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        self.assertIn("'=HYPERLINK", result.csv)
        self.assertTrue(
            result.csv.startswith(
                "key,name,url,placement,position,lifecycle,revision,created_at,updated_at"
            )
        )
        self.assertNotIn("email", result.csv.splitlines()[0])
        self.assertNotIn("consent", result.csv.splitlines()[0])
        audit = AuditEvent.objects.get(action="core.sponsor_directory.exported")
        self.assertEqual(audit.metadata["count"], 1)
        self.assertNotIn("HYPERLINK", str(audit.changes) + str(audit.metadata))
        export_key = str(uuid.uuid4())
        first = export_sponsor_directory(
            confirmed=True,
            reason="repeatable review",
            idempotency_key=export_key,
            actor_ref="user:188",
        )
        replay = export_sponsor_directory(
            confirmed=True,
            reason="repeatable review",
            idempotency_key=export_key,
            actor_ref="user:188",
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.csv, first.csv)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.sponsor_directory.exported").count(),
            2,
        )

    def test_identical_create_replays_and_divergent_key_conflicts(self) -> None:
        key = str(uuid.uuid4())
        first = _create(_payload(), key=key)
        replay = _create(_payload(), key=key)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.sponsor["id"], first.sponsor["id"])
        self.assertEqual(Sponsor.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            _create(_payload(name="Other"), key=key)

    def test_list_is_bounded_and_filterable(self) -> None:
        _create(_payload())
        _create(_payload(key="beta", name="Beta", lifecycle="active", assignments=[]))
        query = type(
            "Query",
            (),
            {
                "page": 1,
                "page_size": 1,
                "sort": ("key",),
                "filters": {"lifecycle": "active"},
            },
        )()
        page = list_sponsors(query)
        items = page["items"]
        self.assertIsInstance(items, list)
        assert isinstance(items, list)
        first = items[0]
        self.assertIsInstance(first, dict)
        assert isinstance(first, dict)
        self.assertEqual(page["total_count"], 1)
        self.assertEqual(first["key"], "beta")
        self.assertEqual(len(items), 1)


class SponsorConcurrencyTests(TransactionTestCase):
    def test_concurrent_updates_have_one_winner(self) -> None:
        created = _create(_payload())
        sponsor_id = created.sponsor["id"]

        def update_in_thread(name: str) -> object:
            close_old_connections()
            try:
                return update_sponsor(
                    sponsor_id=sponsor_id,
                    payload={
                        "name": name,
                        "lifecycle": "draft",
                        "assignments": [],
                    },
                    expected_revision=1,
                    source="studio",
                    idempotency_key=str(uuid.uuid4()),
                    actor_ref="user:188",
                )
            except Exception as error:
                return error
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(update_in_thread, "First winner")
            second = pool.submit(update_in_thread, "Second winner")
            results = [first.result(), second.result()]
        winners = [item for item in results if not isinstance(item, Exception)]
        conflicts = [
            item
            for item in results
            if isinstance(item, (SponsorRevisionConflict, RevisionConflict, OperationalError))
        ]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(conflicts), 1, results)
        self.assertEqual(SponsorRevision.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.sponsor.updated").count(),
            1,
        )
        self.assertEqual(Sponsor.objects.get().revision, 2)
