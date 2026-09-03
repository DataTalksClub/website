from django.test import TestCase

from accounts.identity_resolution import (
    AccountEmailResolutionStatus,
    resolve_accounts_by_email,
)
from accounts.models import AccountIdentityAlias, CustomUser


class AccountEmailResolutionTestCase(TestCase):
    def create_user(self, username, email, **kwargs):
        return CustomUser.objects.create(
            username=username,
            email=email,
            password="password",
            **kwargs,
        )

    def create_alias(self, source, survivor):
        return AccountIdentityAlias.objects.create(
            source_user_id=source.pk,
            survivor=survivor,
            source_snapshot_id="a" * 64,
            mapping_checksum="b" * 64,
            review_reference="issue-234-test",
        )

    def test_batch_normalizes_case_whitespace_and_casefolds(self):
        legacy = self.create_user(
            "legacy",
            "  Straße@Example.COM  ",
        )
        active = self.create_user(
            "active",
            "active@example.com",
            identity_state=CustomUser.IdentityState.ACTIVE,
        )

        with self.assertNumQueries(2):
            results = resolve_accounts_by_email(
                [
                    " STRASSE@example.com ",
                    "ACTIVE@EXAMPLE.COM",
                    "missing@example.com",
                    "ACTIVE@EXAMPLE.COM",
                ]
            )

        self.assertEqual(results["strasse@example.com"].user, legacy)
        self.assertEqual(results["active@example.com"].user, active)
        self.assertEqual(
            results["missing@example.com"].status,
            AccountEmailResolutionStatus.NOT_FOUND,
        )

    def test_distinct_eligible_collision_is_ambiguous(self):
        first = self.create_user("first", "collision@example.com")
        second = self.create_user("second", "other@example.com")
        CustomUser.objects.filter(pk=second.pk).update(normalized_email=first.normalized_email)

        result = resolve_accounts_by_email(["COLLISION@example.com"])["collision@example.com"]

        self.assertEqual(
            result.status,
            AccountEmailResolutionStatus.AMBIGUOUS,
        )
        self.assertIsNone(result.user)
        self.assertEqual(result.matched_user_ids, (first.pk, second.pk))

    def test_collision_with_unavailable_candidate_fails_closed(self):
        eligible = self.create_user("eligible", "collision@example.com")
        unavailable = self.create_user(
            "unavailable",
            "other@example.com",
            identity_state=CustomUser.IdentityState.QUARANTINED,
        )
        CustomUser.objects.filter(pk=unavailable.pk).update(
            normalized_email=eligible.normalized_email,
        )

        result = resolve_accounts_by_email(["COLLISION@example.com"])[
            "collision@example.com"
        ]

        self.assertEqual(
            result.status,
            AccountEmailResolutionStatus.UNAVAILABLE,
        )
        self.assertIsNone(result.user)
        self.assertEqual(result.matched_user_ids, (eligible.pk, unavailable.pk))

    def test_absorbed_identity_resolves_only_to_available_survivor(self):
        source = self.create_user(
            "source",
            "former@example.com",
            identity_state=CustomUser.IdentityState.ABSORBED,
        )
        survivor = self.create_user(
            "survivor",
            "current@example.com",
            identity_state=CustomUser.IdentityState.ACTIVE,
        )
        self.create_alias(source, survivor)

        result = resolve_accounts_by_email([" former@EXAMPLE.com "])["former@example.com"]

        self.assertEqual(
            result.status,
            AccountEmailResolutionStatus.AVAILABLE,
        )
        self.assertEqual(result.user, survivor)
        self.assertEqual(result.related_user_ids, (source.pk, survivor.pk))

    def test_unavailable_identity_states_and_broken_alias_fail_closed(self):
        quarantined = self.create_user(
            "quarantined",
            "quarantined@example.com",
            identity_state=CustomUser.IdentityState.QUARANTINED,
        )
        inactive = self.create_user(
            "inactive",
            "inactive@example.com",
            is_active=False,
        )
        absorbed = self.create_user(
            "absorbed",
            "absorbed@example.com",
            identity_state=CustomUser.IdentityState.ABSORBED,
        )

        results = resolve_accounts_by_email([quarantined.email, inactive.email, absorbed.email])

        for normalized_email in (
            "quarantined@example.com",
            "inactive@example.com",
            "absorbed@example.com",
        ):
            with self.subTest(normalized_email=normalized_email):
                self.assertEqual(
                    results[normalized_email].status,
                    AccountEmailResolutionStatus.UNAVAILABLE,
                )
                self.assertIsNone(results[normalized_email].user)

    def test_unavailable_survivor_and_inconsistent_alias_fail_closed(self):
        absorbed = self.create_user(
            "absorbed",
            "absorbed@example.com",
            identity_state=CustomUser.IdentityState.ABSORBED,
        )
        unavailable_survivor = self.create_user(
            "unavailable-survivor",
            "survivor@example.com",
            identity_state=CustomUser.IdentityState.QUARANTINED,
        )
        self.create_alias(absorbed, unavailable_survivor)
        aliased_legacy = self.create_user(
            "aliased-legacy",
            "aliased@example.com",
        )
        other_survivor = self.create_user(
            "other-survivor",
            "other@example.com",
        )
        self.create_alias(aliased_legacy, other_survivor)

        results = resolve_accounts_by_email([absorbed.email, aliased_legacy.email])

        self.assertEqual(
            results["absorbed@example.com"].status,
            AccountEmailResolutionStatus.UNAVAILABLE,
        )
        self.assertEqual(
            results["aliased@example.com"].status,
            AccountEmailResolutionStatus.UNAVAILABLE,
        )
