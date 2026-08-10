from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import cast
from unittest import mock

from django.db import DatabaseError
from django.test import RequestFactory, TestCase

from core import configuration
from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
    OperationalSettingDefinitionConflict,
    register_operational_setting,
)
from core.context_processors import site_context
from core.idempotency import IdempotencyConflict, JsonObject
from core.models import (
    AuditEvent,
    IdempotencyRecord,
    OperationalSetting,
    OperationalSettingRevision,
)
from core.site_settings import (
    ANNOUNCEMENT_ENABLED,
    ANNOUNCEMENT_ENABLED_KEY,
    ANNOUNCEMENT_MESSAGE,
    ANNOUNCEMENT_MESSAGE_KEY,
    InvalidSiteSettingsBatch,
    SiteSettingsRevisionConflict,
    public_announcement,
    query_site_settings,
    update_site_settings,
)


def setting_update(key: str, value: object, revision: int) -> dict[str, object]:
    return {"key": key, "value": value, "expected_revision": revision}


def update_settings(
    updates: list[dict[str, object]],
    *,
    key: str | None = None,
    actor_ref: str = "user:114",
):
    return update_site_settings(
        updates=updates,
        source="studio",
        idempotency_key=key or str(uuid.uuid4()),
        actor_ref=actor_ref,
    )


def settings_from_result(result: object) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        raise AssertionError("site settings query returned an invalid test shape")
    value = result.get("settings")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise AssertionError("site settings query returned an invalid test shape")
    return cast(list[dict[str, object]], value)


def queried_settings() -> list[dict[str, object]]:
    return settings_from_result(query_site_settings())


class SiteSettingsRegistryTests(TestCase):
    def test_exact_public_definitions_are_deterministic(self) -> None:
        self.assertEqual(
            (ANNOUNCEMENT_ENABLED.key, ANNOUNCEMENT_MESSAGE.key),
            (ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY),
        )
        self.assertEqual(ANNOUNCEMENT_ENABLED.group, "site.announcement")
        self.assertEqual(ANNOUNCEMENT_ENABLED.value_type, "boolean")
        self.assertIs(ANNOUNCEMENT_ENABLED.default, False)
        self.assertEqual(ANNOUNCEMENT_MESSAGE.group, "site.announcement")
        self.assertEqual(ANNOUNCEMENT_MESSAGE.value_type, "string")
        self.assertEqual(ANNOUNCEMENT_MESSAGE.default, "")
        for definition in (ANNOUNCEMENT_ENABLED, ANNOUNCEMENT_MESSAGE):
            with self.subTest(definition=definition.key):
                self.assertEqual(definition.version, 1)
                self.assertEqual(definition.lifecycle, "active")
                self.assertEqual(definition.cache_policy, "uncached")
                self.assertEqual(definition.sensitivity, "public")
                self.assertTrue(definition.label)
                self.assertIn("only when", definition.description)
                self.assertTrue(definition.docs_reference.startswith("_docs/"))

    def test_registry_rejects_duplicates_and_incomplete_or_unsafe_metadata(self) -> None:
        base = OperationalSettingDefinition(
            key="tests.site.registry",
            group="tests.site",
            label="Site registry test",
            description="A complete safe test definition.",
            value_type=OperationalSetting.ValueType.STRING,
            default="",
            validation={"max_length": 10},
            docs_reference="_docs/specs/01-platform-architecture.md#configuration",
            lifecycle="active",
            cache_policy="uncached",
            sensitivity="public",
        )
        invalid_definitions = (
            replace(base, group="Not valid"),
            replace(base, label="  "),
            replace(base, label="Bearer label-secret"),
            replace(base, description=""),
            replace(base, description="Contact private@example.test"),
            replace(base, docs_reference="https://example.invalid/settings"),
            replace(base, version=0),
            replace(base, lifecycle="draft"),
            replace(base, cache_policy="memoized"),
            replace(base, sensitivity="secret"),
            replace(base, validation={"api_token": "never"}),
            replace(base, validation={"example": ["Bearer metadata-secret"]}),
            replace(base, default="Bearer default-secret"),
            replace(base, default=False),
            replace(base, value_type="unsupported"),
            replace(base, validator=lambda _value: False),
        )
        for definition in invalid_definitions:
            with (
                self.subTest(definition=definition),
                mock.patch.object(configuration, "_registry", dict(configuration._registry)),
                self.assertRaises(InvalidOperationalSetting),
            ):
                register_operational_setting(definition)

        with mock.patch.object(configuration, "_registry", dict(configuration._registry)):
            register_operational_setting(base)
            with self.assertRaises(OperationalSettingDefinitionConflict):
                register_operational_setting(base)
            with self.assertRaises(OperationalSettingDefinitionConflict):
                register_operational_setting(replace(base, label="Conflicting label"))

    def test_registry_owns_immutable_copies_of_nested_metadata(self) -> None:
        caller_default: JsonObject = {"display": {"label": "Initial"}}
        caller_validation: JsonObject = {"shape": {"required": ["display"]}}
        definition = OperationalSettingDefinition(
            key="tests.site.immutable",
            group="tests.site",
            label="Immutable registry test",
            description="Nested metadata cannot mutate the code-owned registry.",
            value_type=OperationalSetting.ValueType.JSON_OBJECT,
            default=caller_default,
            validation=caller_validation,
            docs_reference="_docs/specs/01-platform-architecture.md#configuration",
            lifecycle="active",
            cache_policy="uncached",
            sensitivity="public",
        )

        with mock.patch.object(configuration, "_registry", dict(configuration._registry)):
            registered = register_operational_setting(definition)
            caller_default["display"] = {"label": "Caller mutation"}
            caller_validation["shape"] = {"required": []}
            self.assertIsInstance(registered.default, dict)
            assert isinstance(registered.default, dict)
            registered.default["display"] = {"label": "Return mutation"}
            registered.validation["shape"] = {"required": []}

            first_read = next(
                item
                for item in configuration.registered_operational_settings()
                if item.key == definition.key
            )
            self.assertEqual(first_read.default, {"display": {"label": "Initial"}})
            self.assertEqual(
                first_read.validation,
                {"shape": {"required": ["display"]}},
            )

            self.assertIsInstance(first_read.default, dict)
            assert isinstance(first_read.default, dict)
            first_read.default["display"] = {"label": "Enumeration mutation"}
            second_read = next(
                item
                for item in configuration.registered_operational_settings()
                if item.key == definition.key
            )
            self.assertEqual(second_read.default, {"display": {"label": "Initial"}})


class SiteSettingsQueryTests(TestCase):
    def test_defaults_and_overrides_use_one_bounded_query_and_ignore_unknown_rows(self) -> None:
        OperationalSetting.objects.create(
            key=ANNOUNCEMENT_MESSAGE_KEY,
            value_type=OperationalSetting.ValueType.STRING,
            value="Office hours",
            source="studio",
            definition_version=1,
            revision=3,
        )
        OperationalSetting.objects.create(
            key="unknown.public.row",
            value_type=OperationalSetting.ValueType.STRING,
            value="must not be enumerated",
            source="studio",
            definition_version=1,
            revision=1,
        )

        with self.assertNumQueries(1):
            result = query_site_settings()

        settings = settings_from_result(result)
        self.assertEqual(
            [item["key"] for item in settings],
            [ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY],
        )
        self.assertEqual(settings[0]["value"], False)
        self.assertEqual(settings[0]["source"], "code_default")
        self.assertEqual(settings[0]["revision"], 0)
        self.assertEqual(settings[1]["value"], "Office hours")
        self.assertEqual(settings[1]["source"], "studio")
        self.assertEqual(settings[1]["revision"], 3)
        self.assertNotIn("must not be enumerated", json.dumps(result))

    def test_corrupt_site_row_fails_closed_in_public_context(self) -> None:
        OperationalSetting.objects.create(
            key=ANNOUNCEMENT_MESSAGE_KEY,
            value_type=OperationalSetting.ValueType.STRING,
            value="not exposed",
            source="untrusted_source",
            definition_version=1,
            revision=1,
        )

        with self.assertRaises(InvalidOperationalSetting):
            query_site_settings()
        with self.assertLogs("core.context_processors", level="WARNING") as captured:
            context = site_context(RequestFactory().get("/"))

        self.assertIsNone(context["site_announcement"])
        self.assertNotIn("not exposed", " ".join(captured.output))

    def test_consecutive_queries_observe_committed_changes_without_cache(self) -> None:
        self.assertEqual(queried_settings()[1]["value"], "")

        update_settings([setting_update(ANNOUNCEMENT_MESSAGE_KEY, "First message", 0)])
        self.assertEqual(
            queried_settings()[1]["value"],
            "First message",
        )

        update_settings([setting_update(ANNOUNCEMENT_MESSAGE_KEY, "Second message", 1)])
        self.assertEqual(
            queried_settings()[1]["value"],
            "Second message",
        )


class SiteSettingsCommandTests(TestCase):
    def test_batch_normalizes_writes_once_and_replays_without_value_leakage(self) -> None:
        idempotency_key = str(uuid.uuid4())
        updates = [
            setting_update(ANNOUNCEMENT_MESSAGE_KEY, "  Office hours & news  ", 0),
            setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0),
        ]

        first = update_settings(updates, key=idempotency_key)
        replay = update_settings(
            [
                setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0),
                setting_update(ANNOUNCEMENT_MESSAGE_KEY, "Office hours & news", 0),
            ],
            key=idempotency_key,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.settings, replay.settings)
        self.assertEqual(
            [item["key"] for item in first.settings],
            [ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY],
        )
        self.assertTrue(all(item["changed"] is True for item in first.settings))
        self.assertEqual(OperationalSetting.objects.count(), 2)
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        event = AuditEvent.objects.get()
        revisions = tuple(OperationalSettingRevision.objects.order_by("key"))
        self.assertEqual({revision.audit_event_id for revision in revisions}, {event.id})
        evidence = json.dumps(
            {
                "changes": event.changes,
                "metadata": event.metadata,
                "idempotency": IdempotencyRecord.objects.get().result,
            },
            sort_keys=True,
        )
        self.assertNotIn("Office hours", evidence)
        self.assertNotIn(idempotency_key, evidence)
        self.assertEqual(
            event.metadata["affected_keys"],
            [ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY],
        )

    def test_invalid_second_item_and_stale_second_item_are_all_or_none(self) -> None:
        with self.assertRaises(InvalidSiteSettingsBatch):
            update_settings(
                [
                    setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0),
                    setting_update(ANNOUNCEMENT_MESSAGE_KEY, "unsafe\nline", 0),
                ]
            )
        self.assertFalse(OperationalSetting.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

        update_settings([setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0)])
        with self.assertRaises(SiteSettingsRevisionConflict) as caught:
            update_settings(
                [
                    setting_update(ANNOUNCEMENT_ENABLED_KEY, False, 1),
                    setting_update(ANNOUNCEMENT_MESSAGE_KEY, "Proposed", 1),
                ]
            )
        self.assertEqual(caught.exception.key, ANNOUNCEMENT_MESSAGE_KEY)
        self.assertEqual(caught.exception.actual, 0)
        enabled = OperationalSetting.objects.get(key=ANNOUNCEMENT_ENABLED_KEY)
        self.assertIs(enabled.value, True)
        self.assertEqual(enabled.revision, 1)
        self.assertFalse(OperationalSetting.objects.filter(key=ANNOUNCEMENT_MESSAGE_KEY).exists())
        self.assertEqual(OperationalSettingRevision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_validation_boundaries_and_exact_shapes_fail_before_mutation(self) -> None:
        valid_message = "x" * 500
        update_settings([setting_update(ANNOUNCEMENT_MESSAGE_KEY, valid_message, 0)])
        self.assertEqual(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY).value,
            valid_message,
        )

        invalid_updates: tuple[object, ...] = (
            [],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "x" * 501, 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "tab\there", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "trailing tab\t", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "\nleading newline", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "nul\0here", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "line\u2028separator", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "paragraph\u2029separator", 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "<markup>", 1)],
            [setting_update(ANNOUNCEMENT_ENABLED_KEY, 1, 0)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, False, 1)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "new", True)],
            [setting_update(ANNOUNCEMENT_MESSAGE_KEY, "new", -1)],
            [setting_update("site.unknown", "new", 0)],
            [
                setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0),
                setting_update(ANNOUNCEMENT_ENABLED_KEY, False, 0),
            ],
            [
                {
                    **setting_update(ANNOUNCEMENT_MESSAGE_KEY, "new", 1),
                    "source": "client",
                }
            ],
            [
                setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0),
                setting_update(ANNOUNCEMENT_MESSAGE_KEY, "new", 1),
                setting_update("site.unknown", "new", 0),
            ],
        )
        for updates in invalid_updates:
            with self.subTest(updates=updates), self.assertRaises(InvalidSiteSettingsBatch):
                update_settings(updates)  # type: ignore[arg-type]
        setting = OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY)
        self.assertEqual(setting.value, valid_message)
        self.assertEqual(setting.revision, 1)

    def test_no_op_and_audit_failure_do_not_create_change_evidence(self) -> None:
        no_op = update_settings(
            [
                setting_update(ANNOUNCEMENT_ENABLED_KEY, False, 0),
                setting_update(ANNOUNCEMENT_MESSAGE_KEY, "   ", 0),
            ]
        )
        self.assertTrue(all(item["changed"] is False for item in no_op.settings))
        self.assertFalse(OperationalSetting.objects.exists())
        self.assertFalse(OperationalSettingRevision.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

        with mock.patch(
            "core.site_settings.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                update_settings([setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0)])
        self.assertFalse(OperationalSetting.objects.exists())
        self.assertFalse(OperationalSettingRevision.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_actor_key_conflicts_but_other_actor_has_an_independent_scope(self) -> None:
        shared_key = str(uuid.uuid4())
        update_settings(
            [setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0)],
            key=shared_key,
        )
        with self.assertRaises(IdempotencyConflict):
            update_settings(
                [setting_update(ANNOUNCEMENT_ENABLED_KEY, False, 1)],
                key=shared_key,
            )

        other = update_settings(
            [setting_update(ANNOUNCEMENT_ENABLED_KEY, False, 1)],
            key=shared_key,
            actor_ref="user:115",
        )
        self.assertFalse(other.replayed)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)


class PublicAnnouncementTests(TestCase):
    def test_banner_requires_enabled_nonempty_message_and_escapes_plain_text(self) -> None:
        self.assertIsNone(public_announcement())
        update_settings([setting_update(ANNOUNCEMENT_ENABLED_KEY, True, 0)])
        self.assertIsNone(public_announcement())

        update_settings([setting_update(ANNOUNCEMENT_MESSAGE_KEY, 'Hours & "news"', 0)])
        self.assertEqual(public_announcement(), {"message": 'Hours & "news"'})

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('aria-label="Site announcement"'), 1)
        self.assertIn("Hours &amp; &quot;news&quot;", html)
        self.assertLess(html.index("</header>"), html.index("site-announcement"))
        self.assertLess(
            html.index("site-announcement"),
            html.index('<main id="main-content"'),
        )
        self.assertNotIn('role="alert"', html)

    def test_public_database_failure_keeps_page_available_without_banner(self) -> None:
        with mock.patch(
            "core.context_processors.public_announcement",
            side_effect=DatabaseError("database unavailable"),
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'aria-label="Site announcement"')
