"""Credential profile storage and origin resolution for ``dtc``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_URL = "https://prod.datatalks.club"


class ConfigError(RuntimeError):
    """Invalid configuration or missing credentials."""


def profile_path() -> Path:
    raw = os.getenv("DTC_PROFILE_PATH", "~/.config/dtc/credentials.json")
    return Path(raw).expanduser()


def read_profile() -> dict[str, str]:
    path = profile_path()
    if not path.exists():
        return {}
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(f"Credential profile {path} must have mode 0600.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read credential profile: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Credential profile must be a JSON object.")
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def write_profile(profile: dict[str, str]) -> Path:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2)
            handle.write("\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    return path


def clear_profile() -> None:
    path = profile_path()
    if path.exists():
        path.unlink()


def resolve_origin(url: str | None = None) -> str:
    profile = read_profile()
    base = (url or os.getenv("DTC_URL") or profile.get("url") or DEFAULT_URL).rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.username
        or not parsed.hostname
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConfigError("Use an origin URL without credentials, path, or query.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ConfigError("Use HTTPS, or HTTP on localhost.")
    return base


def resolve_credentials(url: str | None = None, token: str | None = None) -> tuple[str, str]:
    base = resolve_origin(url)
    profile = read_profile()
    secret = token or os.getenv("DTC_TOKEN")
    if not secret and profile.get("url") == base:
        secret = profile.get("token")
    if not secret:
        raise ConfigError("Not logged in. Run 'dtc login' or set DTC_TOKEN.")
    return base, secret
