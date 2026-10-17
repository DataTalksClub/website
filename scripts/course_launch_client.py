"""Bearer profiles and safe HTTP output for the course operator CLI."""

import getpass
import json
import os
import socket
import sys
import time
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
    base = origin(url)
    profile = read_profile()
    credential = token or os.getenv("DTC_TOKEN")
    if not credential and profile.get("url") == base:
        credential = profile.get("token")
    if not credential:
        raise ClientError("Sign in with login or supply DTC_TOKEN.", 3)
    return base.rstrip("/"), credential


def origin(url=None):
    """Validate the operator origin without requiring a credential."""
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
    return base.rstrip("/")


def default_device_label():
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = ""
    label = f"{user}@{host}".strip("@")
    return (label or "course-launch CLI")[:80]


def device_prompt_printer(message):
    print(message, file=sys.stderr)


def anonymous_post(base, path, payload):
    """POST JSON without a credential; used by the device flow only."""
    body = json.dumps(payload or {}).encode()
    try:
        with build_opener(NoCredentialRedirect()).open(
            Request(
                base + path,
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            ),
            timeout=30,
        ) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            document = json.loads(error.read(65536))
        except (ValueError, OSError):
            document = None
        if isinstance(document, dict) and isinstance(document.get("error"), str):
            return error.code, document
        raise ClientError(f"Login start failed (HTTP {error.code}).", 7) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ClientError("Server unavailable or returned an invalid response.", 7) from None


def device_login_flow(base, *, label=None, printer=None, timeout=600):
    """Run the browser device flow and return the minted credential dict."""
    say = printer or device_prompt_printer
    status, offer = anonymous_post(
        base, "/api/auth/device", {"label": label or default_device_label()}
    )
    if status != 200 or not isinstance(offer, dict):
        raise ClientError("Login start failed.", 7)
    for field in ("device_code", "user_code", "verification_uri"):
        if not offer.get(field):
            raise ClientError("Login start returned an invalid offer.", 7)
    device_code = offer["device_code"]
    user_code = offer["user_code"]
    url = offer.get("verification_uri_complete") or offer["verification_uri"]
    expires_in = offer.get("expires_in", 600) or 600
    interval = max(float(offer.get("interval", 5) or 5), 1.0)
    minutes = max(int(round(expires_in / 60)), 1)

    say("")
    say(f"  Confirm this code:  {user_code}")
    say(f"    Open in a browser:  {url}")
    say("    Open that link on a machine you trust and sign in as usual.")
    say(f"    Waiting for confirmation (the code is valid for {minutes} minute(s))...")
    say("")

    deadline = time.monotonic() + max(timeout, expires_in + 15)
    wait = interval
    while True:
        if time.monotonic() >= deadline:
            raise ClientError("Timed out waiting for browser approval.", 3)
        time.sleep(wait)
        status, document = anonymous_post(
            base,
            "/api/auth/device/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
        )
        if status == 200:
            if not isinstance(document, dict) or "token" not in document:
                raise ClientError("Login completed with an invalid response.", 7)
            return document
        error_code = document.get("error", "") if isinstance(document, dict) else ""
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down" or status == 429:
            wait = min(wait + 5, 60)
            continue
        if error_code == "access_denied":
            raise ClientError("The login was denied in the browser.", 3)
        if error_code == "expired_token":
            raise ClientError("The code expired. Run login again.", 3)
        raise ClientError("Login failed.", 7)


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
