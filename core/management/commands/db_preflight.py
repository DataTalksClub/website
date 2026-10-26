"""Check the deploy database role and object ownership before migrating (D-deploy).

Mirrors Relay's ``db_preflight`` (relay commit c9253d7,
``docs/database-ownership.md``): the candidate image connects as the
expected migration role for the expected database, and every object in
``public`` must already be owned by that role. A Coolify deploy runs this
from ``entrypoint.sh web-migrate`` before ``migrate``; any mismatch fails
the container start, so the dev served-SHA gate (and the prod one) fails
the workflow instead of replacing serving containers with a release that
cannot own its schema.

The 2026-10-26 dev restart loop (``must be owner of table
courses_courseregistration`` on 0029) was the staging app connecting as
``website_app`` against database ``website_dev`` while every object was
owned by ``website_dev``. This check fails closed on exactly that shape:
wrong role, wrong database, or any object owned by someone else.

No DDL here: ownership is audited read-only and migrations run after, in
the same startup sequence. The Coolify compose runs a single ``web``
service as the migration owner, so no session lock is taken. The audit
covers every routine and relation in ``public`` with no extension-member
exclusion: no migration in this repository creates an extension object,
so any non-role-owned object is a misconfiguration by definition.
"""

from __future__ import annotations

import os
from collections import Counter
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

#: The Coolify databases and the role that must own every public object in
#: each. Prod serves ``website`` as ``website_app``; dev serves
#: ``website_dev`` as ``website_dev``. Explicit flags always win; anything
#: else fails closed.
ROLE_BY_DATABASE = {
    "website": "website_app",
    "website_dev": "website_dev",
}

OBJECTS_SQL = """
SELECT c.relkind::text, format('%I.%I', n.nspname, c.relname),
       pg_get_userbyid(c.relowner)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
UNION ALL
SELECT p.prokind::text, p.oid::regprocedure::text, pg_get_userbyid(p.proowner)
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
ORDER BY 1, 2
"""


def resolve_expected(role_flag, database_flag, database_url):
    """The (role, database) pair the container must connect as.

    Explicit flags win. Otherwise the database name comes from
    ``DATABASE_URL`` and the role from the fixed Coolify map above.
    Anything unrecognized fails closed: silently skipping the ownership
    check would re-admit the misconfiguration this command exists to
    catch.
    """

    if database_flag:
        database = database_flag
    else:
        if not database_url:
            raise CommandError(
                "DB preflight needs --expected-database or DATABASE_URL; refusing to guess."
            )
        database = urlparse(database_url).path.lstrip("/") or ""
        if not database:
            raise CommandError("DB preflight could not read a database name from DATABASE_URL.")
    if role_flag:
        role = role_flag
    else:
        role = ROLE_BY_DATABASE.get(database, "")
        if not role:
            raise CommandError(
                f"DB preflight knows no migration role for database={database}; "
                "pass --expected-role explicitly."
            )
    return role, database


class Command(BaseCommand):
    help = "Fail unless this image connects as the migration role and owns every public object."

    def add_arguments(self, parser):
        parser.add_argument("--expected-role", default="")
        parser.add_argument("--expected-database", default="")

    def handle(self, *args, **options):
        connection = connections["default"]
        if connection.vendor != "postgresql":
            raise CommandError(
                "Deploy DB preflight requires PostgreSQL; refusing to skip ownership checks."
            )
        role, database = resolve_expected(
            options["expected_role"],
            options["expected_database"],
            os.environ.get("DATABASE_URL", ""),
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user, session_user, current_database()")
            current, session, actual_database = cursor.fetchone()
            if current != role or session != role or actual_database != database:
                raise CommandError(
                    f"DB preflight identity mismatch: expected database={database} role={role}; "
                    f"got database={actual_database} current_user={current} "
                    f"session_user={session}. Do not replace serving containers."
                )
            cursor.execute(OBJECTS_SQL)
            objects = cursor.fetchall()
        counts = Counter((kind, owner) for kind, _name, owner in objects)
        self.stdout.write(f"DB preflight database={database} migration_role={role}")
        for (kind, owner), count in sorted(counts.items()):
            self.stdout.write(f"owner={owner} kind={kind} count={count}")
        wrong = [(kind, name, owner) for kind, name, owner in objects if owner != role]
        if wrong:
            for kind, name, owner in wrong:
                self.stderr.write(
                    f"Ownership mismatch: {name} kind={kind} owner={owner}; expected={role}"
                )
            raise CommandError(
                f"DB preflight FAILED: {len(wrong)} public objects are not owned by "
                f"migration role {role}. Do not replace serving containers."
            )
        self.stdout.write(f"Ownership OK: {len(objects)} public objects owned by {role}.")
        self.stdout.write(self.style.SUCCESS("DB preflight PASS."))
