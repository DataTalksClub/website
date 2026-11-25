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


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + (b"x" * 10_000)
CREATE_ARGS = [
    "events",
    "create",
    "--title",
    "Test Event",
    "--type",
    "webinar",
    "--description",
    "What it is about.",
    "--start-datetime",
    "2026-12-01T17:00:00+01:00",
    "--speakers",
    "alexeygrigorev",
    "--sponsor-sharing",
    "--collect-company",
    "--reminder-intervals",
    "24h",
]


def test_events_create_sends_the_picture_staged_then_the_event(tmp_path) -> None:
    banner = tmp_path / "banner.png"
    banner.write_bytes(PNG_BYTES)
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.post.return_value = {
        "event": {"id": "11111111-1111-1111-1111-111111111111", "public_id": 999}
    }
    with patch("dtc_cli.commands.events.get_client", return_value=fake_client):
        result = runner.invoke(cli, [*CREATE_ARGS, "--banner", str(banner)])
    assert result.exit_code == 0, result.output
    calls = fake_client.post.call_args_list
    # 10,008 bytes in 4,096-byte chunks: three staged chunks, then the create call.
    assert len(calls) == 4
    chunks = [c.kwargs["json_body"]["picture_chunk"] for c in calls[:3]]
    assert {chunk["upload_id"] for chunk in chunks} == {chunks[0]["upload_id"]}
    assert [chunk["chunk_index"] for chunk in chunks] == [0, 1, 2]
    assert calls[0].kwargs["extra_headers"] is None
    assert calls[1].kwargs["extra_headers"] == {"X-Banner-Upload-Chunk": "1"}
    path, body = calls[3].args[0], calls[3].kwargs["json_body"]
    assert path == "/api/v1/admin/events"
    assert body["picture"] == {"upload_id": chunks[0]["upload_id"]}
    assert body["title"] == "Test Event"
    assert body["type"] == "webinar"
    assert body["speakers"] == ["alexeygrigorev"]
    assert body["sponsor_sharing_enabled"] is True
    assert body["collect_company"] is True
    assert body["reminder_intervals"] == "24h"
    assert body["publish"] is True


def test_events_create_renders_the_standard_picture_without_banner(tmp_path) -> None:
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.post.return_value = {"event": {"id": "x"}}

    def fake_render(body, *, client, generator_dir, output):
        assert body["title"] == "Test Event"
        output.write_bytes(PNG_BYTES[:100])
        return output

    with (
        patch("dtc_cli.commands.events.get_client", return_value=fake_client),
        patch("dtc_cli.commands.events.render_standard_picture", side_effect=fake_render),
    ):
        result = runner.invoke(cli, CREATE_ARGS)
    assert result.exit_code == 0, result.output
    calls = fake_client.post.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["json_body"]["picture_chunk"]["content_type"] == "image/png"
    assert "upload_id" in calls[1].kwargs["json_body"]["picture"]


def test_events_create_requires_a_description() -> None:
    runner = CliRunner()
    args = [a for a in CREATE_ARGS if a not in {"--description", "What it is about."}]
    with patch("dtc_cli.commands.events.get_client", return_value=MagicMock()):
        result = runner.invoke(cli, args)
    assert result.exit_code == 2
    assert "description" in result.output


def test_picture_labels_use_the_events_timezone() -> None:
    from dtc_cli.commands.events import _picture_labels

    assert _picture_labels("2022-12-13T16:00:00+00:00", "Europe/Berlin") == (
        "Tue 13 Dec 2022",
        "17:00 CET",
    )


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


def test_courses_peer_reviews_and_assign_commands() -> None:
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.get.return_value = {"course": "ml-zoomcamp", "projects": []}
    fake_client.post.return_value = {
        "course": "ml-zoomcamp",
        "batched": 3,
        "reviews_assigned": 3,
        "projects": [],
    }
    with patch("dtc_cli.commands.courses.get_client", return_value=fake_client):
        r1 = runner.invoke(
            cli,
            ["courses", "peer-reviews", "ml-zoomcamp", "--cohort", "2026", "--json"],
        )
        r2 = runner.invoke(
            cli,
            ["courses", "assign-peer-reviews", "ml-zoomcamp", "--project", "capstone", "--json"],
        )
    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    fake_client.get.assert_called_once_with(
        "/api/v1/admin/courses/ml-zoomcamp/peer-reviews",
        params={"cohort": "2026"},
    )
    fake_client.post.assert_called_once_with(
        "/api/v1/admin/courses/ml-zoomcamp/peer-reviews/assignments",
        json_body={"project": "capstone"},
    )
    assert json.loads(r2.output)["reviews_assigned"] == 3


def test_certificates_preview_and_issue_commands(tmp_path) -> None:
    import base64

    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.post.side_effect = [
        {
            "filename": "preview.pdf",
            "date": "9 February 2026",
            "content_base64": base64.b64encode(b"%PDF-x").decode(),
        },
        {"executed": False, "graduate_count": 2},
        {"executed": True, "graduate_count": 2},
    ]
    out = tmp_path / "p.pdf"
    with patch("dtc_cli.commands.certificates.get_client", return_value=fake_client):
        r1 = runner.invoke(
            cli, ["certificates", "preview", "c-2026", "--name", "Rick Astley", "--out", str(out)]
        )
        r2 = runner.invoke(cli, ["certificates", "issue", "c-2026"])
        r3 = runner.invoke(cli, ["certificates", "issue", "c-2026", "--yes"])
    assert r1.exit_code == 0, r1.output
    assert out.read_bytes() == b"%PDF-x"
    assert "9 February 2026" in r1.output
    assert r2.exit_code == 0 and r3.exit_code == 0
    calls = fake_client.post.call_args_list
    assert calls[1].kwargs["json_body"] == {}
    assert calls[2].kwargs["json_body"] == {"confirm": "c-2026"}


def test_events_upload_banner_chunks_large_png(tmp_path) -> None:
    banner = tmp_path / "banner.png"
    raw = b"\x89PNG\r\n\x1a\n" + (b"x" * 10_000)
    banner.write_bytes(raw)
    runner = CliRunner()
    fake_client = MagicMock()
    fake_client.post.side_effect = [
        {
            "upload_id": "u",
            "chunk_index": 0,
            "received_chunks": 1,
            "chunk_count": 3,
            "complete": False,
        },
        {
            "upload_id": "u",
            "chunk_index": 1,
            "received_chunks": 2,
            "chunk_count": 3,
            "complete": False,
        },
        {
            "event": {"id": "11111111-1111-1111-1111-111111111111"},
            "size_bytes": len(raw),
            "complete": True,
        },
    ]
    with patch("dtc_cli.commands.events.get_client", return_value=fake_client):
        result = runner.invoke(
            cli,
            [
                "events",
                "upload-banner",
                "11111111-1111-1111-1111-111111111111",
                "--file",
                str(banner),
            ],
        )
    assert result.exit_code == 0, result.output
    assert fake_client.post.call_count == 3
    calls = fake_client.post.call_args_list
    assert calls[0].kwargs["extra_headers"] is None
    assert calls[1].kwargs["extra_headers"] == {"X-Banner-Upload-Chunk": "1"}
    assert calls[2].kwargs["extra_headers"] == {"X-Banner-Upload-Chunk": "1"}
    upload_ids = {c.kwargs["json_body"]["upload_id"] for c in calls}
    assert len(upload_ids) == 1
    assert json.loads(result.output)["complete"] is True


def test_client_formats_cloudfront_403_clearly() -> None:
    import httpx
    import pytest
    from dtc_cli.client import APIError, DtcClient

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<HTML><TITLE>ERROR</TITLE><PRE>Generated by cloudfront</PRE></HTML>",
            headers={"server": "CloudFront", "x-amz-cf-id": "test-cf-id"},
        )

    client = DtcClient("https://prod.datatalks.club", "tok")
    client._force_ipv4 = True
    client._http = httpx.Client(
        base_url="https://prod.datatalks.club", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(APIError) as exc_info:
        client.get("/api/v1/admin/health")
    assert "CloudFront/WAF blocked request" in str(exc_info.value)
    assert "test-cf-id" in str(exc_info.value)
