"""Scoped management credentials for compatibility-API staff tests.

Staff operations on the compatibility API require a real
``management_auth`` identity (audit BE-02), so staff test clients
authenticate with a scoped, expiring credential whose human principal
holds a Studio course-operations role -- the same authority path the
management API and Studio use.
"""

from datetime import timedelta

from django.utils import timezone

from accounts.studio_roles import set_single_studio_role
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token


def issue_staff_bearer(user, *, role="course_operator"):
    """Give ``user`` a Studio role and return its Bearer Authorization value."""
    set_single_studio_role(user, role)
    principal = create_principal(
        kind=APIPrincipal.Kind.HUMAN,
        name=f"compatibility-test:{user.username}",
        identity_snapshot=f"compatibility-test:{user.username}",
        user=user,
    )
    token = generate_token()
    APICredential.objects.create(
        principal=principal,
        name="compatibility API staff test credential",
        prefix=token.prefix,
        secret_digest=encode_secret(token.secret),
        digest_algorithm="pbkdf2_sha256",
        digest_version=1,
        scopes=["compatibility.course_operations"],
        expires_at=timezone.now() + timedelta(days=1),
    )
    return f"Bearer {token.raw}"
