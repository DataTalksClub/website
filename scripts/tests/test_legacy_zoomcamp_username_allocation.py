"""Bounded username allocation for the legacy zoomcamp importer (REL-12).

The allocator used to append a collision suffix and then truncate the result
to the field limit, which made every candidate identical once the suffix
outgrew the remaining space and looped forever.  Allocation now truncates
the base before appending, bounds the sequential scan, and ends at the
deterministic ``username_for_key`` name before refusing.  The acceptance
reproduces the collision with a fake ORM manager, so no database population
is needed to reach the bound.
"""

from __future__ import annotations

import unittest.mock

from django.test import SimpleTestCase

from scripts.prod.legacy_zoomcamp import identity
from scripts.prod.legacy_zoomcamp.identity import (
    _MAX_USERNAME_COLLISIONS,
    _MAX_USERNAME_LENGTH,
    _unique_username,
    sha1_hex,
    username_for_key,
)

#: The longest base ``_username_candidate`` can produce: the local part of an
#: email, sanitized, capped at ``_MAX_USERNAME_LENGTH - 6``.
FULL = "b" * (_MAX_USERNAME_LENGTH - 6)
EMAIL = f"{FULL}@example.invalid"


class FakeManager:
    """Answers ``exists()`` from a taken-name set and records every probe."""

    def __init__(self, *taken: str) -> None:
        self.taken = set(taken)
        self.queried: list[str] = []

    def filter(self, **kwargs: object) -> FakeManager.Query:
        username = kwargs["username"]
        assert isinstance(username, str)
        self.queried.append(username)
        return self.Query(self.taken, username)

    class Query:
        def __init__(self, taken: set[str], username: str) -> None:
            self.taken = taken
            self.username = username

        def exists(self) -> bool:
            return self.username in self.taken


def taken_names_for(base: str, *, through: int) -> set[str]:
    names = {base}
    names.update(
        f"{base[: _MAX_USERNAME_LENGTH - len(f'-{number}')]}-{number}"
        for number in range(1, through + 1)
    )
    return names


class BoundedAllocationTests(SimpleTestCase):
    def test_a_free_base_is_taken_without_a_probe(self) -> None:
        manager = FakeManager()

        with unittest.mock.patch.object(identity, "User") as user:
            user.objects = manager
            allocated = _unique_username(EMAIL)

        self.assertEqual(allocated, FULL)
        self.assertEqual(manager.queried, [FULL])

    def test_every_candidate_is_distinct_and_within_the_field_limit(self) -> None:
        manager = FakeManager(*taken_names_for(FULL, through=9))

        with unittest.mock.patch.object(identity, "User") as user:
            user.objects = manager
            allocated = _unique_username(EMAIL)

        self.assertEqual(allocated, f"{FULL}-10")
        self.assertEqual(len(manager.queried), len(set(manager.queried)))
        self.assertLessEqual(max(map(len, manager.queried)), _MAX_USERNAME_LENGTH)

    def test_the_sequential_scan_ends_at_the_deterministic_source_key(self) -> None:
        manager = FakeManager(*taken_names_for(FULL, through=_MAX_USERNAME_COLLISIONS))

        with unittest.mock.patch.object(identity, "User") as user:
            user.objects = manager
            allocated = _unique_username(EMAIL, source_key=sha1_hex(EMAIL))

        self.assertEqual(allocated, username_for_key(sha1_hex(EMAIL)))
        self.assertEqual(manager.queried[-1], username_for_key(sha1_hex(EMAIL)))
        # One base + the bounded suffixes + the one source-key candidate.
        self.assertEqual(len(manager.queried), _MAX_USERNAME_COLLISIONS + 2)

    def test_exhausting_every_candidate_refuses_without_looping(self) -> None:
        manager = FakeManager(*taken_names_for(FULL, through=_MAX_USERNAME_COLLISIONS))
        manager.taken.add(username_for_key(sha1_hex(EMAIL)))

        with (
            unittest.mock.patch.object(identity, "User") as user,
            self.assertRaises(RuntimeError) as error,
        ):
            user.objects = manager
            _unique_username(EMAIL, source_key=sha1_hex(EMAIL))

        self.assertEqual(str(error.exception), "username-unallocatable")
        self.assertEqual(len(manager.queried), _MAX_USERNAME_COLLISIONS + 2)

    def test_no_source_key_still_terminates_at_the_bound(self) -> None:
        manager = FakeManager(*taken_names_for(FULL, through=_MAX_USERNAME_COLLISIONS))

        with (
            unittest.mock.patch.object(identity, "User") as user,
            self.assertRaises(RuntimeError) as error,
        ):
            user.objects = manager
            _unique_username(EMAIL)

        self.assertEqual(str(error.exception), "username-unallocatable")
        self.assertEqual(len(manager.queried), _MAX_USERNAME_COLLISIONS + 1)
