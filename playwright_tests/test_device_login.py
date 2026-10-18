from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from playwright.sync_api import Page, expect

from core.models import AuditEvent
from management_auth.device_login import LAUNCH_SCOPES

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]

_LAUNCH_PERMISSIONS = (
    "read_course_launch",
    "manage_course_launch",
    "send_course_launch",
)


def _authenticated_cookie(page: Page, live_server, user) -> None:
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def _make_user(email: str, *, launch: bool = False):
    User = get_user_model()
    user = User.objects.create_user(username=email, email=email)
    if launch:
        permissions = Permission.objects.filter(
            content_type__app_label="courses", codename__in=_LAUNCH_PERMISSIONS
        )
        assert permissions.count() == 3, "launch permissions are missing"
        user.user_permissions.add(*permissions)
    return user


def _start_device_flow(base_url: str, label: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}/api/auth/device",
        data=json.dumps({"label": label}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _poll_token(base_url: str, device_code: str) -> tuple[int, dict]:
    from community_base.device_login.codes import sha256_hex
    from community_base.device_login.models import DeviceGrant

    DeviceGrant.objects.filter(device_code_hash=sha256_hex(device_code)).update(last_poll_at=None)
    req = urllib.request.Request(
        f"{base_url}/api/auth/device/token",
        data=json.dumps({"device_code": device_code}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_admin_device_login_browser_flow(page: Page, live_server) -> None:
    manager = _make_user("launch-admin-pw@example.com", launch=True)
    offer = _start_device_flow(live_server.url, "pw-agent@box")
    user_code = offer["user_code"]
    device_code = offer["device_code"]

    _authenticated_cookie(page, live_server, manager)
    page.goto(f"{live_server.url}/device?code={user_code}")

    expect(page.get_by_role("heading", name="Authorize this machine?")).to_be_visible()
    expect(page.get_by_text("pw-agent@box")).to_be_visible()

    page.get_by_role("button", name="Authorize").click()
    expect(page.get_by_role("status")).to_contain_text("Sign-in approved")

    status, credential = _poll_token(live_server.url, device_code)
    assert status == 200
    assert "token" in credential
    token = credential["token"]

    req = urllib.request.Request(
        f"{live_server.url}/api/v1/admin/course-launch?resource=identity",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        identity_data = json.loads(resp.read().decode("utf-8"))
        assert identity_data["scopes"] == sorted(LAUNCH_SCOPES)

    audit = AuditEvent.objects.filter(
        action="management.credential.created",
        target_type="management.credential",
    ).first()
    assert audit is not None
    assert audit.actor_id == manager.pk
    assert audit.outcome == AuditEvent.Outcome.SUCCEEDED
    assert audit.metadata.get("source") == "device_login"


def test_non_admin_device_login_browser_refusal(page: Page, live_server) -> None:
    member = _make_user("plain-member-pw@example.com", launch=False)
    offer = _start_device_flow(live_server.url, "unauthorized-pw@box")
    user_code = offer["user_code"]
    device_code = offer["device_code"]

    _authenticated_cookie(page, live_server, member)
    page.goto(f"{live_server.url}/device?code={user_code}")

    expect(page.get_by_role("heading", name="Authorize this machine?")).to_be_visible()
    expect(page.get_by_role("alert")).to_contain_text("Only admins can approve a device login.")
    expect(page.get_by_role("button", name="Authorize")).to_have_count(0)

    status, poll_result = _poll_token(live_server.url, device_code)
    assert poll_result.get("error") == "authorization_pending"


def test_anonymous_device_page_redirects_to_login(page: Page, live_server) -> None:
    page.goto(f"{live_server.url}/device")
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=/device")
