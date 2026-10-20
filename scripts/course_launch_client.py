"""Bearer profiles and safe HTTP output for the course operator CLI."""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from community_base.device_login.client import (
    DeviceLoginError,
)
from community_base.device_login.client import (
    default_label as cb_default_label,
)
from community_base.device_login.client import (
    device_login as cb_device_login,
)
from community_base.device_login.storage import (
    read_private_json,
    save_private_json,
)


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
    try:
        profile = read_private_json(path)
        if not isinstance(profile, dict):
            raise ValueError("invalid profile")
        for field in ("url", "token"):
            if field in profile and not isinstance(profile[field], str):
                raise ValueError("invalid profile")
        return profile
    except PermissionError as error:
        raise ClientError("Credential profile must have mode 0600.", 3) from error
    except (ValueError, OSError) as error:
        raise ClientError("Cannot read credential profile.", 3) from error


def write_profile(profile):
    save_private_json(profile_path(), profile)


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
    return cb_default_label("course-launch CLI")


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


class _AnonymousPostSession:
    def post(self, url, json=None, timeout=None):
        parsed = urlsplit(url)
        path = parsed.path
        base = f"{parsed.scheme}://{parsed.netloc}"
        status_code, data = anonymous_post(base, path, json)

        class _Response:
            def __init__(self, sc, d):
                self.status_code = sc
                self._data = d
                self.text = "" if isinstance(d, dict) else str(d)

            def json(self):
                return self._data

        return _Response(status_code, data)


def device_login_flow(base, *, label=None, printer=None, timeout=600):
    """Run the browser device flow and return the minted credential dict."""
    try:
        return cb_device_login(
            base,
            label=label or default_device_label(),
            printer=printer or device_prompt_printer,
            timeout=timeout,
            session=_AnonymousPostSession(),
        )
    except DeviceLoginError as error:
        exit_code = 3 if error.code in {"access_denied", "expired_token", "timeout"} else 7
        raise ClientError(str(error), exit_code) from error


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
