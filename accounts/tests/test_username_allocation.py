"""Bounded, truncation-correct username allocation (audit REL-12).

A full-length (150-character) username base used to make every suffixed
candidate identical -- the suffix was appended and then truncated away -- so
a single collision looped forever.  Allocation now truncates the base before
appending, bounds the sequential scan, falls back to the row's deterministic
CMP source key, refuses when even that is taken, and retries one username
race at the save site.
"""

from __future__ import annotations

import unittest.mock

from django.test import TestCase

from accounts.models import CustomUser
from accounts.services.cmp_learner_import import (
    _MAX_USERNAME_COLLISIONS,
    _MAX_USERNAME_LENGTH,
    CmpLearnerImportError,
    _save_new_account,
    _unique_username,
)

FULL = "a" * _MAX_USERNAME_LENGTH


def truncated(base: str, suffix: str) -> str:
    return f"{base[: _MAX_USERNAME_LENGTH - len(suffix)]}{suffix}"


def make_user(username: str) -> CustomUser:
    return CustomUser.objects.create_user(
        username=username,
        email=f"{username[:12]}@example.invalid",
    )


def take_sequential_candidates(base: str) -> None:
    """Occupy the base and every bounded sequential suffix candidate."""

    make_user(base)
    for number in range(1, _MAX_USERNAME_COLLISIONS + 1):
        make_user(truncated(base, f"-{number}"))


class UniqueUsernameTests(TestCase):
    def test_a_full_length_base_progresses_on_first_collision(self) -> None:
        make_user(FULL)

        allocated = _unique_username(FULL)

        self.assertEqual(allocated, truncated(FULL, "-1"))
        self.assertNotEqual(allocated, FULL)
        self.assertLessEqual(len(allocated), _MAX_USERNAME_LENGTH)

    def test_suffix_width_growth_stays_unique_and_within_limit(self) -> None:
        # Collisions across a suffix-width transition (-9 to -10): the
        # truncation boundary moves underneath the suffix, which is exactly
        # where the old append-then-truncate collapsed every candidate.
        make_user(FULL)
        for number in range(1, 10):
            make_user(truncated(FULL, f"-{number}"))

        allocated = _unique_username(FULL)

        self.assertEqual(allocated, truncated(FULL, "-10"))
        self.assertEqual(len(allocated), _MAX_USERNAME_LENGTH)
        self.assertFalse(CustomUser.objects.filter(username=allocated).exists())

    def test_sequential_collisions_terminate_at_the_documented_bound(self) -> None:
        take_sequential_candidates(FULL)

        allocated = _unique_username(FULL, source_id=4242)

        self.assertEqual(allocated, truncated(FULL, "-cmp-4242"))
        self.assertLessEqual(len(allocated), _MAX_USERNAME_LENGTH)

    def test_exhausting_even_the_source_key_refuses_without_looping(self) -> None:
        take_sequential_candidates(FULL)
        make_user(truncated(FULL, "-cmp-7"))

        with self.assertRaises(CmpLearnerImportError) as error:
            _unique_username(FULL, source_id=7)

        self.assertEqual(str(error.exception), "username-unallocatable")

    def test_a_free_first_candidate_ends_the_probe_immediately(self) -> None:
        with unittest.mock.patch("accounts.services.cmp_learner_import.CustomUser") as objects:
            # The allocator reads CustomUser.objects.filter(...), so the
            # manager is the mock's own ``objects`` child.
            objects.objects.filter.return_value.exists.return_value = False

            allocated = _unique_username("free-name")

        self.assertEqual(allocated, "free-name")
        self.assertEqual(objects.objects.filter.call_count, 1)


class UsernameRaceRetryTests(TestCase):
    def test_a_concurrent_insert_of_the_checked_name_is_retried(self) -> None:
        loser = make_user("race-name")
        loser_email = loser.email
        account = CustomUser(username="race-name", email="winner@example.invalid")
        account.set_unusable_password()

        _save_new_account(account, source_id=99)

        # The raced name kept its original owner; the new account landed on
        # the next candidate instead of failing the batch.
        loser.refresh_from_db()
        self.assertEqual(loser.username, "race-name")
        self.assertEqual(loser.email, loser_email)
        account.refresh_from_db()
        self.assertNotEqual(account.username, "race-name")
        self.assertLessEqual(len(account.username), _MAX_USERNAME_LENGTH)

    def test_the_retry_walks_the_same_bounded_ladder(self) -> None:
        make_user("race-name")
        make_user(truncated("race-name", "-1"))
        account = CustomUser(username="race-name", email="winner@example.invalid")
        account.set_unusable_password()

        _save_new_account(account, source_id=7)

        # The retry re-probes from the base: with the base and -1 taken, the
        # next sequential candidate wins; the source-key hatch stays in
        # reserve for a saturated namespace.
        account.refresh_from_db()
        self.assertEqual(account.username, truncated("race-name", "-2"))
