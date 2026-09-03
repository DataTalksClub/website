"""What ``core.runtime_config`` promises, asserted rather than described.

The promise is narrow and worth pinning: four layers in a fixed order, a value
an operator writes in one process reaching another without a restart, a
registry that refuses a key it does not know, and a malformed value that never
takes the site down.
"""

from __future__ import annotations

import os
from unittest import mock

from django.conf import settings as django_settings
from django.test import TestCase, override_settings

from core import runtime_config
from core.configuration import (
    InvalidOperationalSetting,
    UnknownOperationalSetting,
    registered_operational_settings,
    validate_operational_setting_value,
)
from core.models import OperationalSetting
from core.operational_settings import OPERATIONAL_SETTING_KEYS
from core.runtime_config import (
    STAMP_TTL_SECONDS,
    get_setting,
    reset_runtime_settings_cache,
    resolve_runtime_setting,
    runtime_setting_snapshot,
)


def _store(key: str, value_type: str, value: object, *, revision: int = 1) -> OperationalSetting:
    return OperationalSetting.objects.create(
        key=key,
        value_type=value_type,
        value=value,
        source="admin_api",
        definition_version=1,
        revision=revision,
    )


class RuntimeSettingResolutionTests(TestCase):
    def setUp(self) -> None:
        reset_runtime_settings_cache()
        self.addCleanup(reset_runtime_settings_cache)

    def test_database_row_beats_environment_settings_and_default(self) -> None:
        key = "public_media.s3_prefix"
        with mock.patch.dict(os.environ, {"PUBLIC_MEDIA_S3_PREFIX": "from-environment"}):
            with override_settings(PUBLIC_MEDIA_S3_PREFIX="from-settings"):
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), "from-environment")

                _store(key, OperationalSetting.ValueType.STRING, "from-database")
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), "from-database")
                self.assertEqual(
                    resolve_runtime_setting(key).layer,
                    runtime_config.DATABASE_LAYER,
                )

    def test_environment_beats_settings(self) -> None:
        key = "public_media.s3_prefix"
        with mock.patch.dict(os.environ, {"PUBLIC_MEDIA_S3_PREFIX": "from-environment"}):
            with override_settings(PUBLIC_MEDIA_S3_PREFIX="from-settings"):
                reset_runtime_settings_cache()
                resolution = resolve_runtime_setting(key)
        self.assertEqual(resolution.value, "from-environment")
        self.assertEqual(resolution.layer, runtime_config.ENVIRONMENT_LAYER)

    def test_settings_beats_default(self) -> None:
        key = "public_media.s3_prefix"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLIC_MEDIA_S3_PREFIX", None)
            with override_settings(PUBLIC_MEDIA_S3_PREFIX="from-settings"):
                reset_runtime_settings_cache()
                resolution = resolve_runtime_setting(key)
        self.assertEqual(resolution.value, "from-settings")
        self.assertEqual(resolution.layer, runtime_config.SETTINGS_LAYER)

    def test_default_is_the_floor_for_a_key_no_layer_answers(self) -> None:
        # With no row, no environment variable and no settings attribute, the
        # definition default answers -- and for the canonical origin that floor
        # is the origin ``website.settings.base`` itself boots with.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CANONICAL_ORIGIN", None)
            with override_settings():
                del django_settings.CANONICAL_ORIGIN
                reset_runtime_settings_cache()
                resolution = resolve_runtime_setting("site.origin.canonical")
        self.assertEqual(resolution.value, "https://datatalks.club")
        self.assertEqual(resolution.layer, runtime_config.DEFAULT_LAYER)

    def test_boolean_integer_and_string_values_are_read_in_their_declared_type(self) -> None:
        reset_runtime_settings_cache()
        with mock.patch.dict(
            os.environ,
            {
                "DATAMAILER_STRICT": "yes",
                "DATAMAILER_TIMEOUT_SECONDS": "45",
                "DATAMAILER_CLIENT": "  spaced  ",
            },
        ):
            reset_runtime_settings_cache()
            self.assertIs(get_setting("datamailer.strict"), True)
            self.assertEqual(get_setting("datamailer.timeout_seconds"), 45)
            self.assertEqual(get_setting("datamailer.client"), "spaced")

    def test_float_settings_value_is_read_as_the_declared_integer(self) -> None:
        with override_settings(DATAMAILER_TIMEOUT_SECONDS=90.0):
            reset_runtime_settings_cache()
            value = get_setting("datamailer.timeout_seconds")
        self.assertEqual(value, 90)
        self.assertIsInstance(value, int)

    def test_unregistered_key_raises_rather_than_guessing(self) -> None:
        with self.assertRaises(UnknownOperationalSetting):
            get_setting("datamailer.not_a_setting")

    def test_public_announcement_keys_are_not_runtime_resolvable(self) -> None:
        # They are ``uncached``: a banner is read straight from the database on
        # every render, so resolving one through this module would be wrong.
        with self.assertRaises(UnknownOperationalSetting):
            get_setting("site.announcement.enabled")

    def test_malformed_environment_value_falls_through_instead_of_raising(self) -> None:
        with mock.patch.dict(os.environ, {"DATAMAILER_TIMEOUT_SECONDS": "not-a-number"}):
            with override_settings(DATAMAILER_TIMEOUT_SECONDS=30.0):
                reset_runtime_settings_cache()
                resolution = resolve_runtime_setting("datamailer.timeout_seconds")
        self.assertEqual(resolution.value, 30)
        self.assertEqual(resolution.layer, runtime_config.SETTINGS_LAYER)

    def test_out_of_range_environment_value_falls_through_to_the_default(self) -> None:
        with mock.patch.dict(os.environ, {"RELAY_LINK_BRIDGE_POOL_SIZE": "100000"}):
            with override_settings(RELAY_LINK_BRIDGE_POOL_SIZE=0):
                reset_runtime_settings_cache()
                resolution = resolve_runtime_setting("relay.link_bridge.pool_size")
        self.assertEqual(resolution.value, 16)
        self.assertEqual(resolution.layer, runtime_config.DEFAULT_LAYER)

    def test_snapshot_covers_every_runtime_key(self) -> None:
        reset_runtime_settings_cache()
        self.assertEqual(sorted(runtime_setting_snapshot()), sorted(OPERATIONAL_SETTING_KEYS))


class RuntimeSettingStampTests(TestCase):
    """A write in one process has to reach another one without a restart.

    The reader here never calls ``reset_runtime_settings_cache``: that would
    prove only that the cache can be emptied.  What has to hold is that the
    reader picks the write up *on its own*, from the settings table's stamp,
    once its own TTL has passed -- which is exactly what a second container
    does.
    """

    def setUp(self) -> None:
        reset_runtime_settings_cache()
        self.addCleanup(reset_runtime_settings_cache)

    def test_a_write_reaches_a_reader_that_never_reset_its_cache(self) -> None:
        key = "public_media.s3_bucket"
        clock = {"now": 1_000.0}
        with mock.patch.object(runtime_config, "_monotonic", lambda: clock["now"]):
            self.assertEqual(get_setting(key), "")

            # Another process writes the row.  This reader is told nothing.
            _store(key, OperationalSetting.ValueType.STRING, "written-elsewhere")

            # Inside the TTL the reader is still allowed to serve what it had.
            clock["now"] += STAMP_TTL_SECONDS / 2
            self.assertEqual(get_setting(key), "")

            # Past the TTL it re-reads the stamp, sees it move, and rebuilds.
            clock["now"] += STAMP_TTL_SECONDS
            self.assertEqual(get_setting(key), "written-elsewhere")

    def test_the_stamp_moves_on_an_update_not_only_on_an_insert(self) -> None:
        key = "public_media.s3_bucket"
        row = _store(key, OperationalSetting.ValueType.STRING, "first")
        clock = {"now": 2_000.0}
        with mock.patch.object(runtime_config, "_monotonic", lambda: clock["now"]):
            self.assertEqual(get_setting(key), "first")

            row.value = "second"
            row.revision += 1
            row.save(update_fields=("value", "revision", "updated_at"))

            clock["now"] += STAMP_TTL_SECONDS * 2
            self.assertEqual(get_setting(key), "second")

    def test_an_unreadable_settings_table_falls_back_instead_of_failing(self) -> None:
        """Three ways the database layer can decline, and one answer to all of them.

        A real database error is a system check before ``migrate``.  The two
        message-matched ones are the ``SimpleTestCase`` and pytest guards, which
        a unit that declares it needs no database raises: the same units read
        settings, and they must keep reading the value the process booted with.
        """

        from django.db import DatabaseError

        for error in (
            DatabaseError("relation does not exist"),
            AssertionError("Database queries to 'default' are not allowed"),
            RuntimeError("Database access not allowed, use the django_db mark"),
        ):
            with self.subTest(error=type(error).__name__):
                with override_settings(PUBLIC_MEDIA_S3_PREFIX="from-settings"):
                    reset_runtime_settings_cache()
                    with (
                        mock.patch.object(runtime_config, "_read_stamp", side_effect=error),
                        mock.patch.object(
                            runtime_config.OperationalSetting.objects,
                            "using",
                            side_effect=error,
                        ),
                    ):
                        self.assertEqual(get_setting("public_media.s3_prefix"), "from-settings")

    def test_an_unrelated_error_from_the_database_layer_still_propagates(self) -> None:
        with mock.patch.object(
            runtime_config,
            "_read_stamp",
            side_effect=AssertionError("something else entirely"),
        ):
            reset_runtime_settings_cache()
            with self.assertRaises(AssertionError):
                get_setting("public_media.s3_prefix")


class RuntimeSettingRegistryContractTests(TestCase):
    def test_operational_key_tuple_equals_every_stamped_registration(self) -> None:
        """Adding a definition without listing it must fail here, not in production.

        ``OPERATIONAL_SETTING_KEYS`` is what the admin API iterates, so a
        setting that is registered but unlisted would be resolvable by code and
        invisible to the operator who has to change it.
        """

        stamped = sorted(
            definition.key
            for definition in registered_operational_settings()
            if definition.cache_policy == "stamped"
        )
        self.assertEqual(stamped, sorted(OPERATIONAL_SETTING_KEYS))
        self.assertEqual(len(set(OPERATIONAL_SETTING_KEYS)), len(OPERATIONAL_SETTING_KEYS))

    def test_no_registered_setting_name_looks_secret_bearing(self) -> None:
        fragments = ("secret", "token", "password", "apikey", "credential", "key")
        for definition in registered_operational_settings():
            with self.subTest(key=definition.key):
                for name in (definition.key, definition.env_var, definition.settings_attr):
                    normalized = "".join(
                        character for character in name.casefold() if character.isalnum()
                    )
                    for fragment in fragments:
                        if fragment == "key" and definition.key.endswith("_key"):
                            continue
                        self.assertNotIn(fragment, normalized)

    def test_every_stamped_setting_is_operational_and_active(self) -> None:
        for definition in registered_operational_settings():
            if definition.cache_policy != "stamped":
                continue
            with self.subTest(key=definition.key):
                self.assertEqual(definition.sensitivity, "operational")
                self.assertEqual(definition.lifecycle, "active")


class EndpointSettingTests(TestCase):
    """The five settings that are a URL or an address, stored as themselves.

    They used to be stored in pieces -- a bare host, a scheme-less endpoint, a
    mailbox and a domain -- because the write path refused anything the log
    scrubber's patterns matched, which included the site's own public origin.
    The refusal is gone; what stands in its place is each setting's own
    validator, and what it refuses is what the pattern was aimed at.
    """

    def setUp(self) -> None:
        reset_runtime_settings_cache()
        self.addCleanup(reset_runtime_settings_cache)

    def test_an_ordinary_url_and_address_are_stored_as_themselves(self) -> None:
        accepted = {
            "site.origin.canonical": "https://datatalks.club",
            "datamailer.url": "https://mailer.example.com/api",
            "datamailer.from_email": "noreply@datatalks.club",
            "public_media.s3_endpoint_url": "https://storage.example.com",
            # Relay has no public listener; in-VPC it is plain http.
            "relay.link_bridge.base_url": "http://relay.internal:8000",
        }
        for key, value in accepted.items():
            with self.subTest(key=key):
                self.assertEqual(validate_operational_setting_value(key, value), value)
                _store(key, OperationalSetting.ValueType.STRING, value)
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), value)
                self.assertEqual(
                    resolve_runtime_setting(key).layer,
                    runtime_config.DATABASE_LAYER,
                )

    def test_a_url_carrying_a_credential_or_a_query_string_is_refused(self) -> None:
        refused = (
            # The two shapes a token travels in.
            ("site.origin.canonical", "https://operator:hunter2@datatalks.club"),
            ("datamailer.url", "https://mailer.example.com/api?access=abc"),
            ("relay.link_bridge.base_url", "http://relay.internal:8000/#abc"),
            # An origin is a scheme and a host: a path here would be appended to
            # every canonical link on the site.
            ("site.origin.canonical", "https://datatalks.club/courses"),
            ("site.origin.canonical", "http://datatalks.club"),
            ("site.origin.canonical", "datatalks.club"),
            # A list and a display name are not one sender.
            ("datamailer.from_email", "noreply@datatalks.club, other@datatalks.club"),
            ("datamailer.from_email", "DataTalks <noreply@datatalks.club>"),
            ("datamailer.from_email", "noreply@datatalks.club\nBcc: other@example.test"),
        )
        for key, value in refused:
            with self.subTest(key=key, value=value):
                with self.assertRaises(InvalidOperationalSetting):
                    validate_operational_setting_value(key, value)

    def test_the_named_sender_the_mailer_resolves_is_still_a_sender(self) -> None:
        """``courses`` is what this deployment configures, and it must survive.

        The mailer resolves a named sender of its own.  A validator that
        insisted on ``mailbox@domain`` would refuse it, and a refused
        environment value falls through to the next layer -- so course mail
        would go out with no sender at all rather than fail loudly.
        """

        self.assertEqual(
            validate_operational_setting_value("datamailer.from_email", "courses"),
            "courses",
        )

    def test_they_resolve_through_the_same_four_layers_as_any_other_key(self) -> None:
        key = "site.origin.canonical"
        with mock.patch.dict(os.environ, {"CANONICAL_ORIGIN": "https://from-environment.test"}):
            with override_settings(CANONICAL_ORIGIN="https://from-settings.test"):
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), "https://from-environment.test")

                os.environ.pop("CANONICAL_ORIGIN")
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), "https://from-settings.test")

                _store(key, OperationalSetting.ValueType.STRING, "https://from-database.test")
                reset_runtime_settings_cache()
                self.assertEqual(get_setting(key), "https://from-database.test")

    def test_an_operator_hands_a_value_back_by_clearing_it(self) -> None:
        """Clearing the row returns the environment's answer, not an empty URL.

        The composers used to give ``site.origin.canonical`` this behaviour by
        hand, because an empty stored host meant "keep what the process booted
        with".  The ordinary resolution order gives it for free.
        """

        key = "datamailer.url"
        with mock.patch.dict(os.environ, {"DATAMAILER_URL": "https://booted.example"}):
            row = _store(key, OperationalSetting.ValueType.STRING, "https://written.example")
            reset_runtime_settings_cache()
            self.assertEqual(get_setting(key), "https://written.example")

            row.delete()
            reset_runtime_settings_cache()
            self.assertEqual(get_setting(key), "https://booted.example")
