"""Unit tests for the ``dtc`` CLI."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from dtc_cli.cli import cli


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "events" in result.output
    assert "login" in result.output


def test_events_create_sends_expected_payload() -> None:
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.post.return_value = {
        "event": {"id": "11111111-1111-1111-1111-111111111111", "public_id": 999}
    }
    with patch("dtc_cli.commands.events.get_client", return_value=fake_client):
        result = runner.invoke(
            cli,
            [
                "events",
                "create",
                "--title",
                "Test Event",
                "--type",
                "webinar",
                "--start-datetime",
                "2026-12-01T17:00:00+01:00",
                "--speakers",
                "alexeygrigorev",
                "--sponsor-sharing",
                "--collect-company",
                "--reminder-intervals",
                "24h",
            ],
        )
    assert result.exit_code == 0, result.output
    fake_client.post.assert_called_once()
    path, kwargs = fake_client.post.call_args[0][0], fake_client.post.call_args[1]
    assert path == "/api/v1/admin/events"
    body = kwargs["json_body"]
    assert body["title"] == "Test Event"
    assert body["type"] == "webinar"
    assert body["speakers"] == ["alexeygrigorev"]
    assert body["sponsor_sharing_enabled"] is True
    assert body["collect_company"] is True
    assert body["reminder_intervals"] == "24h"
    assert body["publish"] is True


def test_events_export_registrants_csv(tmp_path) -> None:
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.get.return_value = {
        "items": [
            {
                "email": "a@example.com",
                "display_name": "Alice",
                "status": "confirmed",
                "company": "Acme",
                "newsletter_consent": True,
                "sponsor_sharing_consent": True,
                "created_at": "2026-11-15T12:00:00+00:00",
                "verified_at": "2026-11-15T12:01:00+00:00",
                "attended_at": None,
            }
        ]
    }
    out_file = tmp_path / "regs.csv"
    with patch("dtc_cli.commands.events.get_client", return_value=fake_client):
        result = runner.invoke(
            cli,
            [
                "events",
                "export-registrants",
                "11111111-1111-1111-1111-111111111111",
                "--output",
                str(out_file),
            ],
        )
    assert result.exit_code == 0, result.output
    content = out_file.read_text(encoding="utf-8")
    assert "a@example.com" in content
    assert "Acme" in content
    assert json.loads(result.output)["exported"] == 1


def test_device_login_uses_server_routes() -> None:
    """The CLI must hit the community_base device routes (slashless, /token to poll)."""
    import httpx
    from dtc_cli.commands import auth

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/auth/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "dc",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://x/device",
                    "expires_in": 600,
                    "interval": 1,
                },
            )
        if request.url.path == "/api/auth/device/token":
            return httpx.Response(200, json={"token": "tok"})
        return httpx.Response(404)

    real_client = httpx.Client
    with (
        patch.object(auth.time, "sleep", lambda _s: None),
        patch.object(
            auth.httpx,
            "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        ),
    ):
        assert auth._poll_device_flow("https://x", "t") == "tok"
    assert seen == ["/api/auth/device", "/api/auth/device/token"]
