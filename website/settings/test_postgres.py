"""PostgreSQL-backed Django settings for the confirm-flow regression job.

Ordinary CI stays on the owned SQLite harness (``website.settings.test``).
This module exists only for the ``postgres-confirm-regression`` CI job, which
must exercise PostgreSQL-only behavior: ``SELECT ... FOR UPDATE`` over an
outer join raises ``NotSupportedError`` on PostgreSQL while SQLite silently
accepts it. It reuses every test default except the database itself and the
SQLite-bound test runner.
"""

import os

from .test import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DTC_PG_DATABASE", "dtc_test"),
        "USER": os.getenv("DTC_PG_USER", "dtc"),
        "PASSWORD": os.getenv("DTC_PG_PASSWORD", ""),
        "HOST": os.getenv("DTC_PG_HOST", "127.0.0.1"),
        "PORT": os.getenv("DTC_PG_PORT", "5432"),
        "TEST": {"NAME": os.getenv("DTC_PG_TEST_DATABASE", "test_dtc_pg_confirm")},
    }
}

# The owned runner clones SQLite files per worker; unusable on PostgreSQL.
TEST_RUNNER = "django.test.runner.DiscoverRunner"
