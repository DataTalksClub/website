from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from core.models import RevisionConflict
from management_auth.idempotency import (
    ManagementIdempotencyConflict,
    SecretUnavailableOnReplay,
)
from management_auth.models import APICredential, APIPrincipal, APIRateAdmission
from management_auth.rate_limits import RateLimitExceeded, admit
from management_auth.services import (
    CredentialCreationFailed,
    CredentialStateConflict,
    create_principal,
    issue_credential_once,
    note_credential_used,
    replace_principal_permissions,
    revoke_credential,
    rotate_credential_once,
    set_principal_active,
)
from management_auth.tokens import GeneratedToken
from management_registry import CAPABILITY_REGISTRY


def _fixed_token(prefix: str, secret: str) -> GeneratedToken:
    return GeneratedToken(
        raw=f"dtca_v1_{prefix * 16}_{secret * 43}",
        prefix=prefix * 16,
        secret=secret * 43,
    )


class PostgreSQLManagementConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self) -> None:
        self.permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="concurrent service",
            identity_snapshot="service:concurrent",
            permissions=(self.permission,),
        )

    def run_concurrently(self, first, second):
        barrier = threading.Barrier(2)

        def run(callback):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, callback) for callback in (first, second)]
            return [future.result(timeout=30) for future in futures]

    def issue(self, key: str, token: GeneratedToken):
        return self.issue_for(self.principal, key, token)

    def issue_for(self, target: APIPrincipal, key: str, token: GeneratedToken):
        return issue_credential_once(
            actor_principal=APIPrincipal.objects.get(pk=self.principal.pk),
            target_principal_id=target.id,
            name="concurrent credential",
            scopes=("studio.home.read",),
            idempotency_key=key,
            actor_permission="core.access_studio",
            token_factory=lambda: token,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_identical_create_linearizes_to_one_secret_and_safe_replay(self) -> None:
        token = _fixed_token("a", "s")

        def attempt():
            try:
                self.issue("same-create", token)
                return "created"
            except SecretUnavailableOnReplay:
                return "secret-unavailable"

        results = self.run_concurrently(attempt, attempt)
        self.assertEqual(sorted(results), ["created", "secret-unavailable"])
        self.assertEqual(APICredential.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_conflicting_create_linearizes_to_one_effect_and_one_conflict(self) -> None:
        def attempt(name: str, token: GeneratedToken):
            try:
                issue_credential_once(
                    actor_principal=APIPrincipal.objects.get(pk=self.principal.pk),
                    target_principal_id=self.principal.id,
                    name=name,
                    scopes=("studio.home.read",),
                    idempotency_key="conflicting-create",
                    actor_permission="core.access_studio",
                    token_factory=lambda: token,
                )
                return "created"
            except ManagementIdempotencyConflict:
                return "conflict"

        results = self.run_concurrently(
            lambda: attempt("first request", _fixed_token("g", "a")),
            lambda: attempt("second request", _fixed_token("h", "b")),
        )
        self.assertEqual(sorted(results), ["conflict", "created"])
        self.assertEqual(APICredential.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_prefix_collision_has_one_winner_and_bounded_loser(self) -> None:
        token = _fixed_token("b", "t")

        def attempt(key: str):
            try:
                self.issue(key, token)
                return "created"
            except CredentialCreationFailed:
                return "collision"

        results = self.run_concurrently(lambda: attempt("first"), lambda: attempt("second"))
        self.assertEqual(sorted(results), ["collision", "created"])
        self.assertEqual(APICredential.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_rotate_rotate_and_rotate_revoke_have_one_revision_winner(self) -> None:
        created = self.issue("seed", _fixed_token("c", "u"))
        credential = APICredential.objects.get(pk=str(created.response["credential_id"]))

        def rotate(key: str, token: GeneratedToken):
            try:
                rotate_credential_once(
                    actor_principal=APIPrincipal.objects.get(pk=self.principal.pk),
                    credential_id=credential.id,
                    expected_revision=credential.revision,
                    idempotency_key=key,
                    actor_permission="core.access_studio",
                    token_factory=lambda: token,
                )
                return "rotated"
            except (RevisionConflict, CredentialStateConflict):
                return "lost"

        results = self.run_concurrently(
            lambda: rotate("rotate-one", _fixed_token("d", "v")),
            lambda: rotate("rotate-two", _fixed_token("e", "w")),
        )
        self.assertEqual(sorted(results), ["lost", "rotated"])
        self.assertEqual(APICredential.objects.filter(predecessor=credential).count(), 1)

        successor = APICredential.objects.get(predecessor=credential)

        def rotate_successor():
            return rotate("rotate-successor", _fixed_token("f", "x"))

        def revoke_successor():
            try:
                revoke_credential(
                    actor_principal=APIPrincipal.objects.get(pk=self.principal.pk),
                    credential_id=successor.id,
                    expected_revision=successor.revision,
                    actor_permission="core.access_studio",
                )
                return "revoked"
            except RevisionConflict:
                return "lost"

        # Use the successor revision in the rotation side of this second race.
        credential = successor
        results = self.run_concurrently(rotate_successor, revoke_successor)
        self.assertIn("lost", results)
        self.assertEqual(sum(result in {"rotated", "revoked"} for result in results), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_revoke_revoke_and_principal_revision_cas_have_one_winner(self) -> None:
        created = self.issue("revoke-seed", _fixed_token("i", "c"))
        credential = APICredential.objects.get(pk=str(created.response["credential_id"]))

        def revoke():
            try:
                revoke_credential(
                    actor_principal=APIPrincipal.objects.get(pk=self.principal.pk),
                    credential_id=credential.id,
                    expected_revision=credential.revision,
                    actor_permission="core.access_studio",
                )
                return "revoked"
            except RevisionConflict:
                return "lost"

        self.assertEqual(sorted(self.run_concurrently(revoke, revoke)), ["lost", "revoked"])
        credential.refresh_from_db()
        self.assertIsNotNone(credential.revoked_at)
        self.assertEqual(credential.revision, 2)

        self.principal.refresh_from_db()
        expected_revision = self.principal.revision

        def disable():
            try:
                set_principal_active(
                    principal_id=self.principal.id,
                    is_active=False,
                    expected_revision=expected_revision,
                )
                return "disabled"
            except RevisionConflict:
                return "lost"

        def remove_permission():
            try:
                replace_principal_permissions(
                    principal_id=self.principal.id,
                    permissions=(),
                    expected_revision=expected_revision,
                )
                return "permission-removed"
            except RevisionConflict:
                return "lost"

        results = self.run_concurrently(disable, remove_permission)
        self.assertEqual(results.count("lost"), 1)
        self.assertEqual(sum(item in {"disabled", "permission-removed"} for item in results), 1)
        self.principal.refresh_from_db()
        self.assertEqual(self.principal.revision, expected_revision + 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_disable_and_permission_removal_linearize_with_issue(self) -> None:
        def issue_after_gate(key: str, token: GeneratedToken):
            try:
                self.issue(key, token)
                return "issued"
            except (CredentialStateConflict, PermissionError):
                return "denied"

        def disable():
            principal = APIPrincipal.objects.get(pk=self.principal.pk)
            set_principal_active(
                principal_id=principal.id,
                is_active=False,
                expected_revision=principal.revision,
            )
            return "disabled"

        before = APICredential.objects.count()
        results = self.run_concurrently(
            lambda: issue_after_gate("gate-disable", _fixed_token("j", "d")),
            disable,
        )
        self.assertEqual(results.count("disabled"), 1)
        self.assertEqual(sum(result in {"issued", "denied"} for result in results), 1)
        self.assertIn(APICredential.objects.count() - before, {0, 1})
        self.principal.refresh_from_db()
        self.assertFalse(self.principal.is_active)

        principal = APIPrincipal.objects.get(pk=self.principal.pk)
        set_principal_active(
            principal_id=principal.id,
            is_active=True,
            expected_revision=principal.revision,
        )

        def remove_permission():
            principal = APIPrincipal.objects.get(pk=self.principal.pk)
            replace_principal_permissions(
                principal_id=principal.id,
                permissions=(),
                expected_revision=principal.revision,
            )
            return "permission-removed"

        before = APICredential.objects.count()
        results = self.run_concurrently(
            lambda: issue_after_gate("gate-permission", _fixed_token("k", "e")),
            remove_permission,
        )
        self.assertEqual(results.count("permission-removed"), 1)
        self.assertEqual(sum(result in {"issued", "denied"} for result in results), 1)
        self.assertIn(APICredential.objects.count() - before, {0, 1})
        self.principal.refresh_from_db()
        self.assertFalse(self.principal.permissions.exists())

    @skipUnlessDBFeature("has_select_for_update")
    def test_linked_user_disable_linearizes_with_human_credential_issue(self) -> None:
        user_model = get_user_model()
        user = user_model.objects.create_user(username="concurrent-human")
        user.user_permissions.add(self.permission)
        human = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="concurrent human",
            identity_snapshot="human:concurrent",
            user=user,
            permissions=(self.permission,),
        )

        def issue_human():
            try:
                self.issue_for(human, "human-disable", _fixed_token("l", "f"))
                return "issued"
            except PermissionError:
                return "denied"

        def disable_user():
            with transaction.atomic():
                locked = user_model.objects.select_for_update().get(pk=user.pk)
                locked.is_active = False
                locked.save(update_fields=("is_active",))
            return "disabled"

        before = APICredential.objects.count()
        results = self.run_concurrently(issue_human, disable_user)
        self.assertEqual(results.count("disabled"), 1)
        self.assertEqual(sum(result in {"issued", "denied"} for result in results), 1)
        self.assertIn(APICredential.objects.count() - before, {0, 1})
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    @skipUnlessDBFeature("has_select_for_update")
    def test_distinct_actor_permission_removal_fences_the_target_effect(self) -> None:
        high_risk = Permission.objects.get(
            content_type__app_label="core",
            codename="execute_high_risk_fixture",
        )
        user_model = get_user_model()
        actor_user = user_model.objects.create_user(username="concurrent-distinct-actor")
        actor_user.user_permissions.add(high_risk)
        actor = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="concurrent distinct actor",
            identity_snapshot="human:concurrent-distinct-actor",
            user=actor_user,
            permissions=(high_risk,),
        )

        def issue_for_target():
            try:
                issue_credential_once(
                    actor_principal=APIPrincipal.objects.get(pk=actor.pk),
                    target_principal_id=self.principal.id,
                    name="distinct actor credential",
                    scopes=("studio.home.read",),
                    idempotency_key="distinct-actor-permission-race",
                    actor_permission="core.execute_high_risk_fixture",
                    token_factory=lambda: _fixed_token("o", "i"),
                )
                return "issued"
            except PermissionError:
                return "denied"

        def remove_actor_permission():
            selected = APIPrincipal.objects.get(pk=actor.pk)
            replace_principal_permissions(
                principal_id=selected.id,
                permissions=(),
                expected_revision=selected.revision,
            )
            return "permission-removed"

        before = APICredential.objects.count()
        results = self.run_concurrently(issue_for_target, remove_actor_permission)
        self.assertEqual(results.count("permission-removed"), 1)
        self.assertEqual(sum(result in {"issued", "denied"} for result in results), 1)
        self.assertIn(APICredential.objects.count() - before, {0, 1})
        actor.refresh_from_db()
        self.assertFalse(actor.permissions.exists())

    @skipUnlessDBFeature("has_select_for_update")
    def test_distinct_human_actor_disable_fences_the_target_effect(self) -> None:
        high_risk = Permission.objects.get(
            content_type__app_label="core",
            codename="execute_high_risk_fixture",
        )
        user_model = get_user_model()
        actor_user = user_model.objects.create_user(username="concurrent-disabled-actor")
        actor_user.user_permissions.add(high_risk)
        actor = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="concurrent disabled actor",
            identity_snapshot="human:concurrent-disabled-actor",
            user=actor_user,
            permissions=(high_risk,),
        )

        def issue_for_target():
            try:
                issue_credential_once(
                    actor_principal=APIPrincipal.objects.get(pk=actor.pk),
                    target_principal_id=self.principal.id,
                    name="disabled actor credential",
                    scopes=("studio.home.read",),
                    idempotency_key="distinct-actor-disable-race",
                    actor_permission="core.execute_high_risk_fixture",
                    token_factory=lambda: _fixed_token("p", "j"),
                )
                return "issued"
            except PermissionError:
                return "denied"

        def disable_actor_user():
            with transaction.atomic():
                locked = user_model.objects.select_for_update().get(pk=actor_user.pk)
                locked.is_active = False
                locked.save(update_fields=("is_active",))
            return "disabled"

        before = APICredential.objects.count()
        results = self.run_concurrently(issue_for_target, disable_actor_user)
        self.assertEqual(results.count("disabled"), 1)
        self.assertEqual(sum(result in {"issued", "denied"} for result in results), 1)
        self.assertIn(APICredential.objects.count() - before, {0, 1})
        actor_user.refresh_from_db()
        self.assertFalse(actor_user.is_active)

    @skipUnlessDBFeature("has_select_for_update")
    def test_actor_credential_revoke_after_authentication_fences_the_target_effect(self) -> None:
        high_risk = Permission.objects.get(
            content_type__app_label="core",
            codename="execute_high_risk_fixture",
        )
        actor = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="credential-fenced actor",
            identity_snapshot="service:credential-fenced-actor",
            permissions=(high_risk,),
        )
        actor_credential_result = issue_credential_once(
            actor_principal=actor,
            target_principal_id=actor.id,
            name="credential-fenced actor token",
            scopes=("management.credentials.create.fixture",),
            idempotency_key="credential-fenced-actor-bootstrap",
            actor_permission="core.execute_high_risk_fixture",
            token_factory=lambda: _fixed_token("q", "k"),
        )
        actor_credential_id = str(actor_credential_result.response["credential_id"])
        capability = CAPABILITY_REGISTRY.require("management.credentials.create.fixture")
        authenticated = threading.Event()
        revoked = threading.Event()

        def mutate_after_stale_authentication():
            close_old_connections()
            try:
                stale_credential = APICredential.objects.select_related("principal").get(
                    pk=actor_credential_id
                )
                stale_actor = stale_credential.principal
                authenticated.set()
                if not revoked.wait(timeout=10):
                    raise TimeoutError("credential revocation did not reach the barrier")
                try:
                    issue_credential_once(
                        actor_principal=stale_actor,
                        actor_credential=stale_credential,
                        actor_capability=capability,
                        target_principal_id=self.principal.id,
                        name="must not be issued",
                        scopes=("studio.home.read",),
                        idempotency_key="revoked-actor-mutation",
                        actor_permission=capability.django_permission,
                        token_factory=lambda: _fixed_token("r", "l"),
                    )
                    return "issued"
                except PermissionError:
                    return "denied"
            finally:
                connection.close()

        def revoke_after_authentication():
            close_old_connections()
            try:
                if not authenticated.wait(timeout=10):
                    raise TimeoutError("authentication did not reach the barrier")
                with transaction.atomic():
                    credential = APICredential.objects.select_for_update().get(
                        pk=actor_credential_id
                    )
                    credential.revoked_at = timezone.now()
                    credential.revision += 1
                    credential.save(
                        update_fields=("revoked_at", "revision", "updated_at"),
                    )
                return "revoked"
            finally:
                revoked.set()
                connection.close()

        before = APICredential.objects.count()
        with ThreadPoolExecutor(max_workers=2) as executor:
            mutation = executor.submit(mutate_after_stale_authentication)
            revocation = executor.submit(revoke_after_authentication)
            results = [mutation.result(timeout=30), revocation.result(timeout=30)]

        self.assertEqual(results, ["denied", "revoked"])
        self.assertEqual(APICredential.objects.count(), before)

    @skipUnlessDBFeature("has_select_for_update")
    def test_rate_admission_has_one_winner_at_the_boundary(self) -> None:
        now = timezone.now()
        for _ in range(11):
            admit(
                cost_class=APIRateAdmission.CostClass.READ,
                cost=10,
                principal=self.principal,
                now=now,
            )

        def attempt():
            try:
                admit(
                    cost_class=APIRateAdmission.CostClass.READ,
                    cost=10,
                    principal=APIPrincipal.objects.get(pk=self.principal.pk),
                    now=now + timedelta(microseconds=1),
                )
                return "admitted"
            except RateLimitExceeded:
                return "limited"

        self.assertEqual(sorted(self.run_concurrently(attempt, attempt)), ["admitted", "limited"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_last_used_barrier_allows_at_most_one_write_per_fifteen_minutes(self) -> None:
        created = self.issue("last-used-seed", _fixed_token("m", "g"))
        credential = APICredential.objects.get(pk=str(created.response["credential_id"]))
        observed_at = timezone.now()

        def note():
            return note_credential_used(
                APICredential.objects.get(pk=credential.pk),
                now=observed_at,
            )

        self.assertEqual(sorted(self.run_concurrently(note, note)), [False, True])
        credential.refresh_from_db()
        self.assertEqual(credential.last_used_at, observed_at)

        too_soon = observed_at + timedelta(minutes=14, seconds=59)

        def note_too_soon():
            return note_credential_used(
                APICredential.objects.get(pk=credential.pk),
                now=too_soon,
            )

        self.assertEqual(self.run_concurrently(note_too_soon, note_too_soon), [False, False])
        credential.refresh_from_db()
        self.assertEqual(credential.last_used_at, observed_at)

        boundary = observed_at + timedelta(minutes=15)

        def note_boundary():
            return note_credential_used(
                APICredential.objects.get(pk=credential.pk),
                now=boundary,
            )

        self.assertEqual(sorted(self.run_concurrently(note_boundary, note_boundary)), [False, True])
        credential.refresh_from_db()
        self.assertEqual(credential.last_used_at, boundary)

    @skipUnlessDBFeature("has_select_for_update")
    def test_database_guards_reject_direct_identity_and_scope_updates(self) -> None:
        created = self.issue("immutable-seed", _fixed_token("n", "h"))
        credential = APICredential.objects.get(pk=str(created.response["credential_id"]))

        with self.assertRaises(DatabaseError), transaction.atomic():
            APIPrincipal.objects.filter(pk=self.principal.pk).update(
                identity_snapshot="service:changed-directly"
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            APICredential.objects.filter(pk=credential.pk).update(scopes=["studio.audit.browse"])

        self.principal.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(self.principal.identity_snapshot, "service:concurrent")
        self.assertEqual(credential.scopes, ["studio.home.read"])
