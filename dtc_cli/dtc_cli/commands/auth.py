"""Authentication commands for ``dtc`` (device login, token-stdin, logout, whoami)."""

from __future__ import annotations

import socket
import sys
import time
from typing import Any

import click
import httpx

from dtc_cli.client import APIError, get_client
from dtc_cli.config import (
    ConfigError,
    clear_profile,
    profile_path,
    read_profile,
    resolve_origin,
    write_profile,
)
from dtc_cli.output import emit


def _poll_device_flow(base_url: str, label: str) -> str:
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=False) as http:
        start_resp = http.post("/api/auth/device/start", json={"label": label})
        if start_resp.status_code >= 400:
            raise APIError(f"Device login start failed (HTTP {start_resp.status_code}).")
        start = start_resp.json()
        device_code = start["device_code"]
        user_code = start["user_code"]
        verification_uri = start.get("verification_uri_complete") or start.get("verification_uri")
        interval = max(1, int(start.get("interval") or 5))
        expires_in = max(60, int(start.get("expires_in") or 600))

        click.echo(f"Open: {verification_uri}", err=True)
        click.echo(f"Code: {user_code}", err=True)

        deadline = time.monotonic() + expires_in
        while time.monotonic() < deadline:
            time.sleep(interval)
            poll_resp = http.post("/api/auth/device/poll", json={"device_code": device_code})
            data = poll_resp.json()
            if poll_resp.status_code == 200 and data.get("token"):
                return str(data["token"])
            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise APIError(f"Device login failed: {error or poll_resp.status_code}")
        raise APIError("Device login timed out waiting for approval.")


@click.command("login")
@click.option("--url", default=None, help="Target origin (default: https://prod.datatalks.club).")
@click.option("--token-stdin", is_flag=True, help="Read bearer token from stdin.")
@click.option("--label", default=None, help="Device label for browser approval.")
def login_cmd(url: str | None, token_stdin: bool, label: str | None) -> None:
    """Sign in via browser device approval or --token-stdin."""
    try:
        base_url = resolve_origin(url)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    if token_stdin:
        token = sys.stdin.read().strip()
        if not token:
            raise click.ClickException("No token received on stdin.")
    else:
        device_label = label or f"dtc CLI ({socket.gethostname()})"
        token = _poll_device_flow(base_url, device_label)

    # Verify token against /api/v1/admin/health
    health = get_client(url=base_url, token=token).get("/api/v1/admin/health")
    saved = write_profile({"url": base_url, "token": token})
    emit({"status": "authenticated", "url": base_url, "profile": str(saved), "health": health})


@click.command("logout")
def logout_cmd() -> None:
    """Remove saved credentials from the local profile."""
    clear_profile()
    emit({"status": "logged_out", "profile": str(profile_path())})


@click.command("whoami")
def whoami_cmd() -> None:
    """Verify current credentials against the server."""
    profile = read_profile()
    client = get_client()
    health: dict[str, Any] = client.get("/api/v1/admin/health")
    emit(
        {
            "url": client.base_url,
            "profile": str(profile_path()),
            "configured": bool(profile),
            "health": health,
        }
    )
