"""CLI authentication against the actual local Django HTTP server."""

import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from accounts.studio_test_support import make_studio_user
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token
from scripts.course_launch import main

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def test_live_server_login_whoami_and_logout(live_server, monkeypatch):
    user = make_studio_user(username="cli-live-operator", roles=("site_admin",))
    token = live_credential(user)
    profile = Path(".tmp") / f"cli-live-profile-{uuid.uuid4().hex}.json"
    monkeypatch.setenv("DTC_PROFILE_PATH", str(profile))
    monkeypatch.setenv("DTC_TOKEN", "")
    monkeypatch.setenv("DTC_URL", "")
    monkeypatch.setattr("sys.stdin", StringIO(token))
    output = StringIO()
    try:
        with redirect_stdout(output):
            assert main(["login", "--url", live_server.url, "--token-stdin", "--json"]) == 0
            assert profile.stat().st_mode & 0o777 == 0o600
            assert main(["whoami", "--json"]) == 0
            assert main(["logout", "--json"]) == 0
        assert token not in output.getvalue()
        assert "principal_id" in output.getvalue()
        assert not profile.exists()
    finally:
        profile.unlink(missing_ok=True)


def live_credential(user):
    principal = APIPrincipal.objects.create(
        kind="human", name="Synthetic CLI actor", identity_snapshot=f"user:{user.pk}", user=user
    )
    principal.permissions.set(
        Permission.objects.filter(content_type__app_label="courses", codename="read_course_launch")
    )
    token = generate_token()
    APICredential.objects.create(
        principal=principal,
        name="Synthetic CLI credential",
        prefix=token.prefix,
        secret_digest=encode_secret(token.secret),
        digest_algorithm="pbkdf2_sha256",
        digest_version=1,
        scopes=["courses.launch.read"],
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return token.raw
