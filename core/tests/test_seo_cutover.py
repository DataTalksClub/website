from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from content.models import SyncedDocument
from content.tests.test_sync_parsers import _source
from core.bootstrap import BootstrapConfigurationError
from core.configuration import set_operational_setting
from core.models import AuditEvent, OperationalSetting
from core.runtime_config import get_setting, reset_runtime_settings_cache
from core.seo_cutover import noindex_for_apex_cutover
from core.views import DEVELOPMENT_ROBOTS_BODY, PRODUCTION_ROBOTS_BODY


class SeoCutoverTests(TestCase):
    def setUp(self):
        reset_runtime_settings_cache()
        self.addCleanup(reset_runtime_settings_cache)

    def test_indexing_requires_explicit_cutover_and_invalid_flags_fail_closed(self):
        for value in (None, "false", "0"):
            self.assertTrue(noindex_for_apex_cutover(value))
        for value in ("true", "1"):
            self.assertFalse(noindex_for_apex_cutover(value))
        for value in ("", "yes", "TRUE", " false"):
            with self.assertRaises(BootstrapConfigurationError):
                noindex_for_apex_cutover(value)

    def test_flag_selects_robots_body_and_public_response_header_together(self):
        for value, expected in ((None, DEVELOPMENT_ROBOTS_BODY), ("true", PRODUCTION_ROBOTS_BODY)):
            with (
                self.subTest(value=value),
                override_settings(NOINDEX=noindex_for_apex_cutover(value)),
            ):
                response = self.client.get("/robots.txt")
                self.assertEqual(response.content.decode(), expected)
                self.assertEqual("X-Robots-Tag" in response, value is None)

    def test_command_is_preview_first_audited_idempotent_and_preserves_source_identity(self):
        set_operational_setting(
            key="site.origin.canonical",
            value="https://prod.datatalks.club",
            source="test",
            expected_revision=0,
        )
        source = _source("dtc-content")
        row = SyncedDocument.objects.create(
            source=source,
            content_kind="podcast",
            stable_key="mentoring.md",
            slug="mentoring.md",
            title="Mentoring",
            public_path="/podcast/s01e05/mentoring.md",
            source_path="podcasts/s01/e05.yaml",
            checksum="a" * 64,
            record={
                "slug": "mentoring.md",
                "public_path": "/podcast/s01e05/mentoring.md",
                "season": 1,
                "episode": 5,
                "provenance": {"source_key": "mentoring.md"},
            },
        )
        call_command("prepare_public_seo", stdout=StringIO())
        self.assertEqual(get_setting("site.origin.canonical"), "https://prod.datatalks.club")
        row.refresh_from_db()
        self.assertEqual(row.slug, "mentoring.md")
        call_command("prepare_public_seo", apply=True, stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.slug, "mentoring")
        self.assertEqual(row.public_path, "/podcast/s01e05/mentoring")
        self.assertEqual(row.record["public_path"], row.public_path)
        self.assertEqual(row.stable_key, "mentoring.md")
        self.assertEqual(row.record["provenance"]["source_key"], row.stable_key)
        self.assertEqual(get_setting("site.origin.canonical"), "https://datatalks.club")
        revision = OperationalSetting.objects.get(key="site.origin.canonical").revision
        audit_count = AuditEvent.objects.count()
        call_command("prepare_public_seo", apply=True, stdout=StringIO())
        self.assertEqual(
            OperationalSetting.objects.get(key="site.origin.canonical").revision, revision
        )
        self.assertEqual(AuditEvent.objects.count(), audit_count)

    @override_settings(ENVIRONMENT="production")
    def test_production_requires_explicit_write_acknowledgement(self):
        with self.assertRaisesMessage(CommandError, "--allow-production-write"):
            call_command("prepare_public_seo", apply=True, stdout=StringIO())
