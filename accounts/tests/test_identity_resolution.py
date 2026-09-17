from django.test import TestCase

from accounts.identity_resolution import (
    AccountEmailResolutionStatus,
    resolve_accounts_by_email,
)
from accounts_ext.models import (
    AccountIdentityAlias,
    IdentityState,
    normalized_email_of,
)
from accounts.models import User


class AccountEmailResolutionTestCase(TestCase):
    def create_user(self, username, email, **kwargs):
        identity_state = kwargs.pop("identity_state", None)
        normalized_email = kwargs.pop("normalized_email", None)
        user = User.objects.create(
            username=username,
            email=email,
            password="password",
            **kwargs,
        )
        identity_defaults = {}
        if identity_state is not None:
            identity_defaults["identity_state"] = identity_state
        if normalized_email is not None:
            identity_defaults["normalized_email"] = normalized_email
        if identity_defaults:
            IdentityState.objects.update_or_create(user=user, defaults=identity_defaults)
        return user

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
            identity_state=IdentityState.States.ACTIVE,
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
        IdentityState.objects.filter(user=second).update(
            normalized_email=normalized_email_of(first)
        )

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
            identity_state=IdentityState.States.QUARANTINED,
        )
        IdentityState.objects.filter(user=unavailable).update(
            normalized_email=normalized_email_of(eligible),
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
            identity_state=IdentityState.States.ABSORBED,
        )
        survivor = self.create_user(
            "survivor",
            "current@example.com",
            identity_state=IdentityState.States.ACTIVE,
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
            identity_state=IdentityState.States.QUARANTINED,
        )
        inactive = self.create_user(
            "inactive",
            "inactive@example.com",
            is_active=False,
        )
        absorbed = self.create_user(
            "absorbed",
            "absorbed@example.com",
            identity_state=IdentityState.States.ABSORBED,
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
            identity_state=IdentityState.States.ABSORBED,
        )
        unavailable_survivor = self.create_user(
            "unavailable-survivor",
            "survivor@example.com",
            identity_state=IdentityState.States.QUARANTINED,
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
