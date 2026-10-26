"""The Coolify deploy database preflight (database-ownership gate).

The candidate image must connect as the migration role for its database
with every public object already owned by that role; anything else fails
the container start so the served-SHA gates fail the deploy instead of
shipping a release that cannot own its schema. These tests run on the
ordinary SQLite harness: the fail-closed engine guard and the pure role
resolution need no server, and the audit logic runs against a stubbed
cursor so no test depends on a live PostgreSQL catalog.
"""

from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.db import connections
from django.test import SimpleTestCase

from core.management.commands.db_preflight import resolve_expected


class ResolveExpectedTests(SimpleTestCase):
    def test_explicit_flags_win_over_the_url(self):
        self.assertEqual(
            resolve_expected("website_app", "website", "postgresql://other@host:5432/other"),
            ("website_app", "website"),
        )

    def test_coolify_databases_map_to_their_migration_roles(self):
        self.assertEqual(
            resolve_expected("", "", "postgresql://role@host:5432/website"),
            ("website_app", "website"),
        )
        self.assertEqual(
            resolve_expected("", "", "postgresql://role@host:5432/website_dev"),
            ("website_dev", "website_dev"),
        )

    def test_unknown_database_fails_closed(self):
        with self.assertRaises(CommandError):
            resolve_expected("", "", "postgresql://role@host:5432/customdb")

    def test_missing_url_and_flags_fail_closed(self):
        with self.assertRaises(CommandError):
            resolve_expected("", "", "")

    def test_pinned_environment_beats_url_derivation(self):
        self.assertEqual(
            resolve_expected(
                "",
                "",
                "postgresql://role@host:5432/website_dev",
                {"DEPLOY_EXPECTED_DATABASE": "website"},
            ),
            ("website_app", "website"),
        )
        self.assertEqual(
            resolve_expected(
                "",
                "",
                "postgresql://role@host:5432/website",
                {"DEPLOY_EXPECTED_ROLE": "website_dev"},
            ),
            ("website_dev", "website"),
        )

    def test_explicit_flags_beat_pinned_environment(self):
        self.assertEqual(
            resolve_expected(
                "website_dev",
                "website_dev",
                "postgresql://x@host:5432/website",
                {
                    "DEPLOY_EXPECTED_DATABASE": "website",
                    "DEPLOY_EXPECTED_ROLE": "website_app",
                },
            ),
            ("website_dev", "website_dev"),
        )


class DbPreflightCommandTests(SimpleTestCase):
    def _run(self, *arguments, cursor):
        connection = connections["default"]
        output = StringIO()
        with (
            mock.patch.object(connection, "vendor", "postgresql"),
            mock.patch.object(connection, "cursor", return_value=cursor),
        ):
            call_command("db_preflight", *arguments, stdout=output, stderr=StringIO())
        return output.getvalue()

    def _cursor(self, owner="website_app", database="website", wrong=(), session=None):
        rows = [("r", "public.courses_course", owner), ("i", "public.courses_course_pkey", owner)]
        rows.extend(("r", name, "website_dev") for name in wrong)
        cursor = mock.MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (owner, session or owner, database)
        cursor.fetchall.return_value = rows
        return cursor

    def test_aligned_ownership_passes(self):
        output = self._run(
            "--expected-role",
            "website_app",
            "--expected-database",
            "website",
            cursor=self._cursor(),
        )
        self.assertIn("DB preflight PASS.", output)

    def test_derived_coolify_identity_passes(self):
        # Without flags the command reads DATABASE_URL; point it at dev.
        with mock.patch.dict(
            "os.environ", {"DATABASE_URL": "postgresql://role@host:5432/website_dev"}
        ):
            output = self._run(cursor=self._cursor(owner="website_dev", database="website_dev"))
        self.assertIn("DB preflight PASS.", output)

    def test_wrong_role_for_the_database_fails(self):
        cursor = self._cursor(owner="website_app", database="website_dev")
        with self.assertRaises(CommandError):
            self._run(
                "--expected-role",
                "website_dev",
                "--expected-database",
                "website_dev",
                cursor=cursor,
            )

    def test_session_user_mismatch_fails(self):
        cursor = self._cursor(session="pgadmin")
        with self.assertRaises(CommandError):
            self._run(
                "--expected-role",
                "website_app",
                "--expected-database",
                "website",
                cursor=cursor,
            )

    def test_empty_schema_passes_for_bootstrap(self):
        cursor = self._cursor()
        cursor.fetchall.return_value = []
        output = self._run(
            "--expected-role",
            "website_app",
            "--expected-database",
            "website",
            cursor=cursor,
        )
        self.assertIn("Ownership OK: 0 public objects owned by website_app.", output)

    def test_foreign_owned_object_fails(self):
        cursor = self._cursor(wrong=["public.courses_courseregistration"])
        with self.assertRaises(CommandError):
            self._run(
                "--expected-role",
                "website_app",
                "--expected-database",
                "website",
                cursor=cursor,
            )

    def test_non_postgres_engine_refuses_to_skip(self):
        with self.assertRaisesRegex(CommandError, "requires PostgreSQL"):
            call_command("db_preflight", stdout=StringIO(), stderr=StringIO())
