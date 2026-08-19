from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from unittest import mock

from django.db import DatabaseError, OperationalError, close_old_connections, connections
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from core.context_processors import site_context
from core.idempotency import IdempotencyConflict, JsonObject
from core.models import (
    AuditEvent,
    IdempotencyRecord,
    RevisionConflict,
    SiteNavigationEntry,
    SiteNavigationMenu,
    SiteNavigationRevision,
)
from core.navigation import (
    DEFAULT_PRIMARY_NAVIGATION,
    InvalidSiteNavigation,
    SiteNavigationRevisionConflict,
    default_navigation_entries,
    public_primary_navigation,
    query_site_navigation,
    replace_site_navigation,
)


def default_entries() -> list[JsonObject]:
    return [entry.as_dict() for entry in default_navigation_entries()]


def replace_menu(
    entries: list[JsonObject],
    *,
    revision: int = 0,
    key: str | None = None,
    actor_ref: str = "user:187",
    source: str = "studio",
):
    return replace_site_navigation(
        entries=entries,
        expected_revision=revision,
        source=source,
        idempotency_key=key or str(uuid.uuid4()),
        actor_ref=actor_ref,
    )


def queried_menu() -> dict[str, object]:
    result = query_site_navigation()
    if not isinstance(result, dict):
        raise AssertionError("navigation query returned an invalid test shape")
    return cast(dict[str, object], result)


def queried_entries() -> list[dict[str, object]]:
    entries = queried_menu()["entries"]
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise AssertionError("navigation query returned an invalid entry list")
    return cast(list[dict[str, object]], entries)


class SiteNavigationQueryTests(TestCase):
    def test_default_override_order_and_one_bounded_query(self) -> None:
        with self.assertNumQueries(1):
            result = queried_menu()
        self.assertEqual(result["source"], "code_default")
        self.assertEqual(result["revision"], 0)
        self.assertEqual(
            [
                (item["key"], item["label"], item["target"], item["position"])
                for item in queried_entries()
            ],
            [
                (key, label, target, position)
                for key, label, target, position, _visible in DEFAULT_PRIMARY_NAVIGATION
            ],
        )

        created = replace_menu(
            [
                *default_entries()[:8],
                {**default_entries()[8], "label": "Community"},
            ]
        )
        self.assertTrue(created.menu["changed"])
        with self.assertNumQueries(2):
            overridden = queried_menu()
        self.assertEqual(overridden["source"], "studio")
        self.assertEqual(overridden["revision"], 1)
        self.assertEqual(queried_entries()[8]["label"], "Community")

    def test_unusable_stored_row_raises_and_public_fails_closed(self) -> None:
        menu = SiteNavigationMenu.objects.create(key="primary", source="studio", revision=1)
        SiteNavigationEntry.objects.create(
            menu=menu,
            key="events",
            label="Events",
            target="unknown_route",
            position=1,
            visible=True,
        )
        with self.assertRaises(InvalidSiteNavigation):
            query_site_navigation()
        with self.assertLogs("core.navigation", level="WARNING") as captured:
            public = public_primary_navigation()
        self.assertEqual(
            [(item["label"], item["href"]) for item in public],
            [
                (label, reverse(target))
                for _key, label, target, _position, _visible in DEFAULT_PRIMARY_NAVIGATION
            ],
        )
        self.assertNotIn("unknown_route", " ".join(captured.output))

    def test_consecutive_queries_observe_committed_changes_without_cache(self) -> None:
        self.assertEqual(queried_menu()["revision"], 0)
        replace_menu([{**default_entries()[0], "label": "First"}] + default_entries()[1:])
        self.assertEqual(queried_entries()[0]["label"], "First")
        replace_menu(
            [{**default_entries()[0], "label": "Second"}] + default_entries()[1:],
            revision=1,
        )
        self.assertEqual(queried_entries()[0]["label"], "Second")


class SiteNavigationCommandTests(TestCase):
    def test_batch_normalizes_writes_once_and_replays_without_label_leakage(self) -> None:
        idempotency_key = str(uuid.uuid4())
        entries = [
            {**default_entries()[0], "label": "  Events & news  "},
            *default_entries()[1:],
        ]
        first = replace_menu(entries, key=idempotency_key)
        replay = replace_menu(
            [{**default_entries()[0], "label": "Events & news"}, *default_entries()[1:]],
            key=idempotency_key,
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.menu["entries"], replay.menu["entries"])
        self.assertEqual(first.menu["entries"][0]["label"], "Events & news")
        self.assertEqual(SiteNavigationMenu.objects.count(), 1)
        self.assertEqual(SiteNavigationEntry.objects.count(), 9)
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.site_navigation.updated").count(),
            1,
        )
        event = AuditEvent.objects.get(action="core.site_navigation.updated")
        evidence = json.dumps(
            {
                "changes": event.changes,
                "metadata": event.metadata,
                "idempotency": IdempotencyRecord.objects.get().result,
            },
            sort_keys=True,
        )
        self.assertNotIn("Events & news", evidence)
        self.assertNotIn(idempotency_key, evidence)

    def test_invalid_entry_is_all_or_none_and_stale_revision_does_not_write(self) -> None:
        with self.assertRaises(InvalidSiteNavigation):
            replace_menu(
                [
                    {**default_entries()[0], "label": "unsafe\nline"},
                    *default_entries()[1:],
                ]
            )
        self.assertFalse(SiteNavigationMenu.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

        replace_menu([{**default_entries()[0], "label": "Kept"}] + default_entries()[1:])
        with self.assertRaises(SiteNavigationRevisionConflict) as caught:
            replace_menu(
                [{**default_entries()[0], "label": "Stale"}] + default_entries()[1:],
                revision=0,
            )
        self.assertEqual(caught.exception.actual, 1)
        self.assertEqual(SiteNavigationMenu.objects.get().revision, 1)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "Kept")
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)

    def test_validation_boundaries_and_unsafe_targets_fail_before_mutation(self) -> None:
        valid = default_entries()
        valid[0] = {**valid[0], "label": "x" * 80}
        replace_menu(valid)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "x" * 80)

        invalid_cases: tuple[object, ...] = (
            [],
            valid
            + [{"key": "home", "label": "Home", "target": "home", "position": 10, "visible": True}]
            * 4,
            [{**valid[0], "label": "x" * 81}, *valid[1:]],
            [{**valid[0], "label": "tab\there"}, *valid[1:]],
            [{**valid[0], "label": "<markup>"}, *valid[1:]],
            [{**valid[0], "key": "Events"}, *valid[1:]],
            [{**valid[0], "target": "https://evil.example"}, *valid[1:]],
            [{**valid[0], "target": "//evil.example"}, *valid[1:]],
            [{**valid[0], "target": "/events?q=1"}, *valid[1:]],
            [{**valid[0], "target": "/events#top"}, *valid[1:]],
            [{**valid[0], "target": "https://user:pass@evil.example"}, *valid[1:]],
            [{**valid[0], "target": "../events"}, *valid[1:]],
            [{**valid[0], "target": "unknown"}, *valid[1:]],
            [{**valid[0], "position": 1}, {**valid[1], "position": 1}, *valid[2:]],
            [{**valid[0], "key": "blog"}, *valid[1:]],
            [{**valid[0], "source": "client"}, *valid[1:]],
            [{**valid[0], "visible": "yes"}, *valid[1:]],
        )
        for entries in invalid_cases:
            with self.subTest(entries=entries), self.assertRaises(InvalidSiteNavigation):
                replace_menu(entries, revision=1)  # type: ignore[arg-type]
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "x" * 80)
        self.assertEqual(SiteNavigationMenu.objects.get().revision, 1)

    def test_surviving_entry_cannot_silently_change_target(self) -> None:
        replace_menu([{**default_entries()[0], "label": "Gatherings"}] + default_entries()[1:])
        changed = [{**default_entries()[0], "label": "Gatherings", "target": "home"}]
        changed.extend(default_entries()[1:])
        with self.assertRaises(InvalidSiteNavigation):
            replace_menu(changed, revision=1)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").target, "events")

    def test_no_op_and_audit_failure_do_not_create_change_evidence(self) -> None:
        no_op = replace_menu(default_entries())
        self.assertFalse(no_op.menu["changed"])
        self.assertFalse(SiteNavigationMenu.objects.exists())
        self.assertFalse(SiteNavigationRevision.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="core.site_navigation.updated").exists())

        with mock.patch(
            "core.navigation.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                replace_menu([{**default_entries()[0], "label": "Changed"}] + default_entries()[1:])
        self.assertFalse(SiteNavigationMenu.objects.exists())
        self.assertFalse(SiteNavigationRevision.objects.exists())

    def test_same_actor_key_conflicts_but_other_actor_has_an_independent_scope(self) -> None:
        shared_key = str(uuid.uuid4())
        replace_menu(
            [{**default_entries()[0], "label": "First"}] + default_entries()[1:],
            key=shared_key,
        )
        with self.assertRaises(IdempotencyConflict):
            replace_menu(
                [{**default_entries()[0], "label": "Other"}] + default_entries()[1:],
                key=shared_key,
                revision=1,
            )
        other = replace_menu(
            [{**default_entries()[0], "label": "Other"}] + default_entries()[1:],
            key=shared_key,
            revision=1,
            actor_ref="user:188",
        )
        self.assertFalse(other.replayed)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)


class PublicNavigationTests(TestCase):
    def test_default_markup_and_override_are_visible_on_the_next_request(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for _key, label, target, _position, _visible in DEFAULT_PRIMARY_NAVIGATION:
            self.assertContains(response, f">{label}</a>")
            self.assertContains(response, f'href="{reverse(target)}"')

        replace_menu(
            [
                {**default_entries()[0], "label": "Gatherings"},
                {**default_entries()[1], "visible": False},
                *default_entries()[2:],
                {
                    "key": "home",
                    "label": "Home",
                    "target": "home",
                    "position": 10,
                    "visible": True,
                },
            ]
        )
        home = self.client.get("/")
        events = self.client.get(reverse("events"))
        self.assertContains(home, ">Gatherings</a>")
        self.assertNotContains(home, ">Courses</a>")
        self.assertContains(home, f'href="{reverse("home")}"')
        self.assertContains(events, 'aria-current="page"')
        self.assertContains(events, ">Gatherings</a>")

    def test_public_database_failure_keeps_the_default_menu(self) -> None:
        with mock.patch(
            "core.navigation.query_site_navigation",
            side_effect=DatabaseError("database unavailable"),
        ):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Events</a>")
        self.assertContains(response, ">Slack</a>")
        context = site_context(RequestFactory().get("/"))
        self.assertEqual(len(context["primary_navigation"]), 9)


class SiteNavigationConcurrencyTests(TransactionTestCase):
    def test_concurrent_updates_have_one_winner(self) -> None:
        replace_menu([{**default_entries()[0], "label": "Seed"}] + default_entries()[1:])

        def update_in_thread(label: str) -> object:
            close_old_connections()
            try:
                return replace_menu(
                    [{**default_entries()[0], "label": label}] + default_entries()[1:],
                    revision=1,
                    key=str(uuid.uuid4()),
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
            if isinstance(
                item,
                (SiteNavigationRevisionConflict, RevisionConflict, OperationalError),
            )
        ]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(conflicts), 1, results)
        self.assertEqual(SiteNavigationRevision.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.site_navigation.updated").count(),
            2,
        )
        self.assertEqual(SiteNavigationMenu.objects.get().revision, 2)
