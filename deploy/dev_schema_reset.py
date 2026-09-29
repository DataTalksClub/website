"""One-shot reset of the reviewed development website schema inside its migration task."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

import psycopg

from core.bootstrap import BootstrapConfigurationError, RuntimeEnvironment, database_configuration

DATABASE = "dtc_website_dev"
ROLE = "website_dev"
SETTINGS = "website.settings.development"


class ResetRefused(RuntimeError):
    """A development-only guard could not be proven."""


def _assert_connection(cursor: psycopg.Cursor) -> None:
    cursor.execute(
        """SELECT current_database(), current_user, session_user,
                  current_schema(), current_schemas(false),
                  pg_get_userbyid(n.nspowner),
                  has_database_privilege(current_user, current_database(), 'CREATE')
           FROM pg_namespace n WHERE n.nspname = 'public'"""
    )
    row = cursor.fetchone()
    if row != (DATABASE, ROLE, ROLE, "public", ["public"], ROLE, True):
        raise ResetRefused("database, role, schema, or recreate privilege mismatch")
    cursor.execute(
        """SELECT count(*) FROM pg_stat_activity
           WHERE datname = current_database() AND pid <> pg_backend_pid()"""
    )
    active_connections = cursor.fetchone()
    if active_connections is None or active_connections[0] != 0:
        raise ResetRefused("another website database connection is active")
    cursor.execute(
        """SELECT nspname FROM pg_namespace
           WHERE nspname NOT IN ('public', 'pg_catalog', 'information_schema')
             AND nspname NOT LIKE 'pg_toast%'
             AND nspname NOT LIKE 'pg_temp_%'"""
    )
    if cursor.fetchone() is not None:
        raise ResetRefused("an additional schema needs a separate dependency review")
    cursor.execute("SELECT extname FROM pg_extension WHERE extnamespace = 'public'::regnamespace")
    if cursor.fetchone() is not None:
        raise ResetRefused("a public extension needs a separate dependency review")


def reset_public_schema(
    connection: psycopg.Connection,
    *,
    before_commit: Callable[[], None] | None = None,
) -> None:
    """Reset only public; any failure rolls the entire schema change back."""

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            _assert_connection(cursor)
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public AUTHORIZATION website_dev")
            cursor.execute("GRANT USAGE, CREATE ON SCHEMA public TO website_dev")
            if before_commit is not None:
                before_commit()


def main() -> int:
    if os.environ.get("DTC_ENVIRONMENT") != "development":
        print("Development environment guard refused schema reset", file=sys.stderr)
        return 1
    if os.environ.get("DJANGO_SETTINGS_MODULE") != SETTINGS:
        print("Development settings guard refused schema reset", file=sys.stderr)
        return 1
    database_url = os.environ.get("DATABASE_URL")
    try:
        if database_url is None:
            raise ResetRefused("website database secret is missing")
        database_configuration(
            environment=RuntimeEnvironment.DEVELOPMENT,
            database_url=database_url,
        )
        with psycopg.connect(database_url, autocommit=True) as connection:
            reset_public_schema(connection)
    except (BootstrapConfigurationError, psycopg.Error, ResetRefused):
        print("Development website schema reset refused or failed", file=sys.stderr)
        return 1
    print("Development website public schema reset completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
