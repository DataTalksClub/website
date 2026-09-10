#!/usr/bin/env python3
"""Seed the local development database with safe, synthetic or public data.

This entry point deliberately contains only local seed commands.  It never reads
production exports, course checkouts, provider archives, or deployed database
configuration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_COMMANDS = (
    "seed_local_courses",
    "seed_local_questions",
    "seed_local_social_providers",
)


def configure_local_environment() -> None:
    """Set the local settings and reject an explicitly different environment."""

    configured_environment = os.environ.get("DTC_ENVIRONMENT")
    if configured_environment not in (None, "local"):
        raise RuntimeError("local seed data requires DTC_ENVIRONMENT=local")
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")


def seed_local_data() -> None:
    """Run the bounded local seed commands in their declared order."""

    configure_local_environment()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    import django

    django.setup()

    from django.core.management import call_command

    for command in SEED_COMMANDS:
        call_command(command)


def main() -> int:
    try:
        seed_local_data()
    except RuntimeError as error:
        print(f"seed_local_data: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
