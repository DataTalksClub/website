"""Bearer profiles and safe HTTP output for the course operator CLI."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ClientError(RuntimeError):
    def __init__(self, message, exit_code=1):
        self.exit_code = exit_code
        super().__init__(message)


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def profile_path():
    return Path(
        os.getenv("DTC_PROFILE_PATH", "~/.config/dtc-course-launch/credentials.json")
    ).expanduser()


def read_profile():
    path = profile_path()
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ClientError("Credential profile must have mode 0600.", 3)
    try:
        profile = json.loads(path.read_text())
        if not isinstance(profile, dict):
            raise ValueError("invalid profile")
        for field in ("url", "token"):
            if field in profile and not isinstance(profile[field], str):
                raise ValueError("invalid profile")
        return profile
    except (ValueError, OSError) as error:
        raise ClientError("Cannot read credential profile.", 3) from error


def write_profile(profile):
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(profile, output)


def configuration(url=None, token=None):
    profile = read_profile()
    base = url or os.getenv("DTC_URL") or profile.get("url") or "http://localhost:8000"
    base = base.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.username
        or not parsed.hostname
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ClientError("Use an origin URL without credentials, path or query.", 2)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ClientError("Use HTTPS, or HTTP on localhost.", 2)
    credential = token or os.getenv("DTC_TOKEN")
    if not credential and profile.get("url") == base:
        credential = profile.get("token")
    if not credential:
        raise ClientError("Sign in with login or supply DTC_TOKEN.", 3)
    return base.rstrip("/"), credential


def request(method, path, *, url=None, token=None, payload=None, key=None, revision=None):
    base, credential = configuration(url, token)
    headers = {"Authorization": f"Bearer {credential}", "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    if revision is not None:
        headers["If-Match"] = f'"rev-{revision}"'
    try:
        with build_opener(NoCredentialRedirect()).open(
            Request(base + path, data=body, headers=headers, method=method), timeout=30
        ) as response:
            return json.load(response)
    except HTTPError as error:
        codes = {400: 6, 401: 3, 403: 3, 404: 4, 409: 5, 422: 6, 428: 6}
        raise ClientError(
            response_error_message(error, credential),
            codes.get(error.code, 7),
        ) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ClientError("Server unavailable or returned an invalid response.", 7) from None


def response_error_message(error, credential):
    fallback = f"Request rejected (HTTP {error.code}); check permissions, input and revision."
    try:
        document = json.loads(error.read(65536))
    except (ValueError, OSError):
        return fallback
    if not isinstance(document, dict) or not isinstance(document.get("error"), dict):
        return fallback
    message = document["error"].get("message")
    if not isinstance(message, str) or not message or len(message) > 1000:
        return fallback
    if credential in message or "\x00" in message:
        return fallback
    return message
