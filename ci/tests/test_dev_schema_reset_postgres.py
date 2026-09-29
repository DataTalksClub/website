"""Disposable PostgreSQL contract for the one-shot development schema reset.

Set DTC_RESET_TEST_POSTGRES_URL to a disposable PostgreSQL superuser connection.
The fixture creates and removes only its two named disposable databases and role.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from deploy.dev_schema_reset import ResetRefused, reset_public_schema

PASSWORD = "synthetic-reset-test-password"


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    url = os.environ.get("DTC_RESET_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("disposable PostgreSQL URL is not configured")
    with psycopg.connect(url, autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("CREATE ROLE website_dev LOGIN PASSWORD 'synthetic-reset-test-password'")
            cursor.execute("CREATE DATABASE dtc_website_dev OWNER website_dev")
            cursor.execute("CREATE DATABASE dtc_relay_dev")
    yield url
    with psycopg.connect(url, autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("DROP DATABASE dtc_website_dev WITH (FORCE)")
            cursor.execute("DROP DATABASE dtc_relay_dev WITH (FORCE)")
            cursor.execute("DROP ROLE website_dev")


def _url(url: str, database: str, *, role: str = "website_dev") -> str:
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(url)
    if role == "website_dev":
        user = f"{role}:{PASSWORD}"
    else:
        user = "postgres:synthetic-reset-test-password"
    return urlunsplit((parsed.scheme, f"{user}@{parsed.hostname}:{parsed.port}", database, "", ""))


@pytest.fixture()
def website(postgres_url: str):
    with psycopg.connect(_url(postgres_url, "dtc_website_dev"), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER SCHEMA public OWNER TO website_dev")
            cursor.execute("CREATE TABLE public.keep_marker (id integer PRIMARY KEY)")
            cursor.execute("INSERT INTO public.keep_marker VALUES (1)")
        yield connection
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public AUTHORIZATION website_dev")


def _marker_exists(connection: psycopg.Connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.keep_marker') IS NOT NULL")
        result = cursor.fetchone()
        assert result is not None
        return result[0]


def _force_failure() -> None:
    raise RuntimeError("forced rollback")


def test_reset_is_transactional_and_relay_database_is_untouched(website, postgres_url: str) -> None:
    with psycopg.connect(
        _url(postgres_url, "dtc_relay_dev", role="postgres"), autocommit=True
    ) as relay:
        with relay.cursor() as cursor:
            cursor.execute("CREATE TABLE public.relay_marker (id integer PRIMARY KEY)")
            cursor.execute("INSERT INTO public.relay_marker VALUES (7)")
        with pytest.raises(RuntimeError, match="forced rollback"):
            reset_public_schema(website, before_commit=_force_failure)
        assert _marker_exists(website)
        reset_public_schema(website)
        assert not _marker_exists(website)
        with relay.cursor() as cursor:
            cursor.execute("SELECT id FROM public.relay_marker")
            assert cursor.fetchall() == [(7,)]


def test_extra_schema_refuses_before_sql_and_preserves_objects(website) -> None:
    with website.cursor() as cursor:
        cursor.execute("CREATE SCHEMA unrelated")
        cursor.execute("CREATE TABLE unrelated.marker (id integer)")
    with pytest.raises(ResetRefused, match="additional schema"):
        reset_public_schema(website)
    assert _marker_exists(website)
    with website.cursor() as cursor:
        cursor.execute("SELECT to_regclass('unrelated.marker') IS NOT NULL")
        assert cursor.fetchone()[0]
        cursor.execute("DROP SCHEMA unrelated CASCADE")


def test_wrong_role_owner_and_search_path_refuse_without_sql(website, postgres_url: str) -> None:
    with psycopg.connect(
        _url(postgres_url, "dtc_website_dev", role="postgres"), autocommit=True
    ) as admin:
        with pytest.raises(ResetRefused):
            reset_public_schema(admin)
        with admin.cursor() as cursor:
            cursor.execute("ALTER SCHEMA public OWNER TO postgres")
    with pytest.raises(ResetRefused):
        reset_public_schema(website)
    with psycopg.connect(
        _url(postgres_url, "dtc_website_dev", role="postgres"), autocommit=True
    ) as admin:
        with admin.cursor() as cursor:
            cursor.execute("ALTER SCHEMA public OWNER TO website_dev")
    with website.cursor() as cursor:
        cursor.execute("SET search_path TO pg_catalog, public")
    with pytest.raises(ResetRefused):
        reset_public_schema(website)
    assert _marker_exists(website)


def test_wrong_database_and_active_connection_refuse_without_sql(
    website, postgres_url: str
) -> None:
    relay_url = _url(postgres_url, "dtc_relay_dev")
    with psycopg.connect(relay_url, autocommit=True) as relay:
        with pytest.raises(ResetRefused):
            reset_public_schema(relay)
    website_url = _url(postgres_url, "dtc_website_dev")
    with psycopg.connect(website_url, autocommit=True):
        with pytest.raises(ResetRefused, match="another website database connection"):
            reset_public_schema(website)
    assert _marker_exists(website)


def test_missing_recreate_privilege_refuses_without_sql(website, postgres_url: str) -> None:
    admin_url = _url(postgres_url, "dtc_website_dev", role="postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("ALTER DATABASE dtc_website_dev OWNER TO postgres")
            cursor.execute("REVOKE CREATE ON DATABASE dtc_website_dev FROM website_dev")
    with pytest.raises(ResetRefused):
        reset_public_schema(website)
    assert _marker_exists(website)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("ALTER DATABASE dtc_website_dev OWNER TO website_dev")
