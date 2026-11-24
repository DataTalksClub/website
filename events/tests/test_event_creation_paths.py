"""``events.identity.create_event_identity`` is the only code that creates an event.

Every event has a type and at least one speaker from the moment it exists.  The
type rides in ``Event.tags`` and the speakers are ``EventHost`` rows, so the
database cannot hold the rule on its own; it holds because one function writes
the row and its speakers in a single transaction, refuses either missing, and
nothing else constructs an ``Event``.  This test is what keeps "nothing else"
true: any other ``Event(...)`` or ``Event.objects.create/bulk_create/
get_or_create/update_or_create`` outside test code fails it.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CREATION = re.compile(
    r"\bEvent\(|\bEvent\.objects\.(?:create|bulk_create|get_or_create|update_or_create)\("
)
THE_ONE_PATH = Path("events/identity.py")
SKIPPED_PARTS = {".venv", "node_modules", "tests", "playwright_tests", "migrations", ".tmp"}


class EventCreationPathTests(SimpleTestCase):
    def test_no_code_outside_the_creation_service_constructs_an_event(self) -> None:
        root = Path(settings.BASE_DIR)
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            relative = path.relative_to(root)
            if SKIPPED_PARTS & set(relative.parts) or relative == THE_ONE_PATH:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if CREATION.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "Create events only through events.identity.create_event_identity, which "
            "refuses an event without a type and a speaker.",
        )

    def test_the_creation_service_is_where_the_one_constructor_lives(self) -> None:
        source = (Path(settings.BASE_DIR) / THE_ONE_PATH).read_text(encoding="utf-8")
        self.assertEqual(len(CREATION.findall(source)), 1)
