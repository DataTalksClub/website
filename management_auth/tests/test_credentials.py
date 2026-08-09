from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import MD5PasswordHasher
from django.contrib.auth.models import Permission
from django.core.checks import Tags, run_checks
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import RevisionConflict
from management_auth.constants import (
    DIGEST_ALGORITHM,
    PREFIX_COLLISION_RETRIES,
)
from management_auth.idempotency import (
    ManagementIdempotencyConflict,
    SecretUnavailableOnReplay,
)
from management_auth.models import (
    APICredential,
    APIPrincipal,
    APIRateAdmission,
    ImmutableManagementIdentity,
    ManagementIdempotencyRecord,
)
from management_auth.rate_limits import RateLimitExceeded, admit, verify_with_adaptive_limit
from management_auth.services import (
    CredentialCreationFailed,
    create_principal,
    issue_credential_once,
    normalize_scopes,
    replace_principal_permissions,
    revoke_credential,
    rotate_credential_once,
    set_principal_active,
)
from management_auth.tokens import GeneratedToken, encode_secret, parse_token, verify_secret


def _token(prefix_character: str, secret_character: str = "s") -> GeneratedToken:
    prefix = prefix_character * 16
    secret = secret_character * 43
    return GeneratedToken(
        raw=f"dtca_v1_{prefix}_{secret}",
        prefix=prefix,
        secret=secret,
    )


class CredentialServiceTests(TestCase):
    def setUp(self) -> None:
        self.permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="fixture service",
            identity_snapshot="service:fixture",
            permissions=(self.permission,),
        )

    def test_exact_token_digest_and_one_time_replay_contract(self) -> None:
        generated = _token("a")
        result = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="fixture credential",
            scopes=("studio.home.read", "studio.home.read"),
            idempotency_key="fixture-create",
            actor_permission="core.access_studio",
            token_factory=lambda: generated,
        )
        credential = APICredential.objects.get()

        self.assertEqual(result.response["token"], generated.raw)
        self.assertEqual(parse_token(generated.raw).prefix, generated.prefix)  # type: ignore[union-attr]
        self.assertEqual(credential.prefix, generated.prefix)
        self.assertEqual(credential.digest_algorithm, DIGEST_ALGORITHM)
        self.assertTrue(credential.secret_digest.startswith(f"{DIGEST_ALGORITHM}$"))
        self.assertTrue(verify_secret(generated.secret, credential.secret_digest))
        self.assertEqual(credential.scopes, ["studio.home.read"])
        self.assertNotIn(generated.raw, str(credential.__dict__))
        self.assertNotIn(generated.raw, str(ManagementIdempotencyRecord.objects.get().__dict__))

        with self.assertRaises(SecretUnavailableOnReplay) as caught:
            issue_credential_once(
                actor_principal=self.principal,
                target_principal_id=self.principal.id,
                name="fixture credential",
                scopes=("studio.home.read", "studio.home.read"),
                idempotency_key="fixture-create",
                actor_permission="core.access_studio",
                token_factory=lambda: _token("b"),
            )
        self.assertNotIn("token", caught.exception.safe_result)
        self.assertEqual(APICredential.objects.count(), 1)

    def test_same_key_conflict_and_cross_principal_isolation(self) -> None:
        first = _token("a")
        issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="fixture credential",
            scopes=("studio.home.read",),
            idempotency_key="shared-key",
            actor_permission="core.access_studio",
            token_factory=lambda: first,
        )
        with self.assertRaises(ManagementIdempotencyConflict):
            issue_credential_once(
                actor_principal=self.principal,
                target_principal_id=self.principal.id,
                name="different credential",
                scopes=("studio.home.read",),
                idempotency_key="shared-key",
                actor_permission="core.access_studio",
                token_factory=lambda: _token("b"),
            )

        other = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="other service",
            identity_snapshot="service:other",
            permissions=(self.permission,),
        )
        result = issue_credential_once(
            actor_principal=other,
            target_principal_id=other.id,
            name="other credential",
            scopes=("studio.home.read",),
            idempotency_key="shared-key",
            actor_permission="core.access_studio",
            token_factory=lambda: _token("b"),
        )
        self.assertIn("token", result.response)
        self.assertEqual(ManagementIdempotencyRecord.objects.count(), 2)

    def test_prefix_collision_retries_exactly_five_and_unrelated_integrity_fails(self) -> None:
        first = _token("a")
        issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="seed credential",
            scopes=("studio.home.read",),
            idempotency_key="seed",
            actor_permission="core.access_studio",
            token_factory=lambda: first,
        )
        attempts = 0

        def collision():
            nonlocal attempts
            attempts += 1
            return first

        with self.assertRaises(CredentialCreationFailed):
            issue_credential_once(
                actor_principal=self.principal,
                target_principal_id=self.principal.id,
                name="collision credential",
                scopes=("studio.home.read",),
                idempotency_key="collision",
                actor_permission="core.access_studio",
                token_factory=collision,
            )
        self.assertEqual(attempts, PREFIX_COLLISION_RETRIES)
        self.assertEqual(APICredential.objects.count(), 1)

        with patch.object(APICredential, "save", side_effect=IntegrityError("other constraint")):
            with self.assertRaises(IntegrityError):
                issue_credential_once(
                    actor_principal=self.principal,
                    target_principal_id=self.principal.id,
                    name="broken credential",
                    scopes=("studio.home.read",),
                    idempotency_key="broken",
                    actor_permission="core.access_studio",
                    token_factory=lambda: _token("z"),
                )

    def test_expiry_rotation_revocation_and_immutable_authority(self) -> None:
        first = _token("a")
        created = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="fixture credential",
            scopes=("studio.home.read",),
            idempotency_key="create",
            actor_permission="core.access_studio",
            token_factory=lambda: first,
        )
        credential = APICredential.objects.get(pk=str(created.response["credential_id"]))
        rotated = rotate_credential_once(
            actor_principal=self.principal,
            credential_id=credential.id,
            expected_revision=credential.revision,
            idempotency_key="rotate",
            actor_permission="core.access_studio",
            overlap=timedelta(minutes=5),
            token_factory=lambda: _token("b"),
        )
        credential.refresh_from_db()
        successor = APICredential.objects.get(pk=str(rotated.response["credential_id"]))
        self.assertEqual(successor.scopes, ["studio.home.read"])
        self.assertEqual(successor.predecessor_id, credential.id)
        self.assertIsNotNone(credential.overlap_expires_at)
        self.assertIsNotNone(credential.rotated_at)
        assert credential.overlap_expires_at is not None
        assert credential.rotated_at is not None
        self.assertGreater(credential.overlap_expires_at, credential.rotated_at)

        revoked = revoke_credential(
            actor_principal=self.principal,
            credential_id=successor.id,
            expected_revision=successor.revision,
            actor_permission="core.access_studio",
        )
        self.assertIsNotNone(revoked.revoked_at)
        successor.scopes = ["studio.audit.browse"]
        with self.assertRaises(ImmutableManagementIdentity):
            successor.save()

    def test_human_service_shape_and_current_permissions_fail_closed(self) -> None:
        user = get_user_model().objects.create_user(username="human-principal")
        user.user_permissions.add(self.permission)
        human = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="fixture human",
            identity_snapshot="human:fixture",
            user=user,
            permissions=(self.permission,),
        )
        self.assertEqual(
            normalize_scopes(("studio.home.read",), principal=human),
            ("studio.home.read",),
        )
        user.user_permissions.remove(self.permission)
        with self.assertRaises(PermissionError):
            normalize_scopes(("studio.home.read",), principal=human)
        human.identity_snapshot = "human:changed"
        with self.assertRaises(ImmutableManagementIdentity):
            human.save()
        with self.assertRaises(ProtectedError):
            user.delete()
        human.delete()
        user.delete()
        self.assertFalse(get_user_model().objects.filter(username="human-principal").exists())
        with self.assertRaises(ValueError):
            create_principal(
                kind=APIPrincipal.Kind.SERVICE,
                name="bad service",
                identity_snapshot="service:bad",
                user=get_user_model().objects.create_user(username="bad-link"),
            )

    def test_stale_permission_change_does_not_mutate_authority(self) -> None:
        replacement = Permission.objects.get(
            content_type__app_label="core",
            codename="browse_audit",
        )
        set_principal_active(
            principal_id=self.principal.id,
            is_active=False,
            expected_revision=self.principal.revision,
        )

        with self.assertRaises(RevisionConflict):
            replace_principal_permissions(
                principal_id=self.principal.id,
                permissions=(replacement,),
                expected_revision=self.principal.revision,
            )

        self.principal.refresh_from_db()
        self.assertEqual(set(self.principal.permissions.all()), {self.permission})
        updated = replace_principal_permissions(
            principal_id=self.principal.id,
            permissions=(replacement,),
            expected_revision=self.principal.revision,
        )
        self.assertEqual(set(updated.permissions.all()), {replacement})

    @override_settings(
        PASSWORD_HASHERS=("django.contrib.auth.hashers.MD5PasswordHasher",),
    )
    def test_management_digest_ignores_password_hasher_settings_and_check_is_live(self) -> None:
        self.assertEqual(MD5PasswordHasher.algorithm, "md5")
        encoded = encode_secret("fixture-secret")
        self.assertTrue(encoded.startswith(f"{DIGEST_ALGORITHM}$"))
        self.assertTrue(verify_secret("fixture-secret", encoded))
        self.assertEqual(
            [
                error
                for error in run_checks(tags=[Tags.security])
                if error.id == "management_auth.E001"
            ],
            [],
        )


class RateLimitTests(TestCase):
    def test_exact_rolling_window_and_retry_after(self) -> None:
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="rate service",
            identity_snapshot="service:rate",
            permissions=(permission,),
        )
        now = timezone.now()
        for _ in range(12):
            admit(
                cost_class=APIRateAdmission.CostClass.READ,
                cost=10,
                principal=principal,
                now=now,
            )
        with self.assertRaises(RateLimitExceeded) as caught:
            admit(
                cost_class=APIRateAdmission.CostClass.READ,
                cost=1,
                principal=principal,
                now=now,
            )
        self.assertEqual(caught.exception.retry_after, 60)
        admit(
            cost_class=APIRateAdmission.CostClass.READ,
            cost=10,
            principal=principal,
            now=now + timedelta(seconds=61),
        )

    def test_adaptive_failure_limit_precedes_the_eleventh_digest(self) -> None:
        checks = 0

        def rejected() -> bool:
            nonlocal checks
            checks += 1
            return False

        for _ in range(10):
            self.assertFalse(verify_with_adaptive_limit(prefix="a" * 16, verifier=rejected))
        with self.assertRaises(RateLimitExceeded):
            verify_with_adaptive_limit(prefix="a" * 16, verifier=rejected)
        self.assertEqual(checks, 10)
