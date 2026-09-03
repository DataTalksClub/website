"""The website side of Relay's recipient-link seam.

Relay builds every open-pixel, click and unsubscribe URL it puts in mail from a
single ``PUBLIC_BASE_URL``.  The owner's decision is that the public unsubscribe
surface lives on ``datatalks.club`` and that ``relay.datatalks.club`` is an
operator surface going behind OIDC, so ``PUBLIC_BASE_URL`` names this site and
this site has to answer the three paths Relay embeds.

Relay is reachable only in-VPC, over plain HTTP, on a private name.  This module
is the one adapter that crosses that seam.  It deliberately holds no token logic
of its own: hashing, lookup, scope semantics, engagement counters and the
unsubscribe mutation stay in Relay, which owns the recipient data.  The website
contributes the public surface, the presentation and the failure behaviour.

Three properties this module exists to guarantee:

* A recipient token is identifying.  It never reaches a log record, an
  observability event, an exception message or an error report.  Diagnostics use
  ``token_fingerprint`` -- a truncated salt-free digest that is stable enough to
  correlate two lines and useless for reconstructing the token.
* A malformed token is rejected here, before any socket is opened, so junk and
  probe traffic costs Relay nothing.
* An unconfigured bridge is not a half-open bridge.  With no configured base URL
  every caller is told ``not_configured`` and the views answer 404.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter

from core.runtime_config import get_int_setting
from core.runtime_endpoints import relay_link_bridge_base_url

# Relay mints raw tokens with ``secrets.token_urlsafe(32)``, which is 43
# characters of URL-safe base64.  The bound is deliberately wider than that so a
# token-length change in Relay does not silently break every link, and still
# narrow enough that no path traversal, CRLF or query separator can survive.
TOKEN_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{16,128}\Z")
# The transparent 1x1 GIF Relay serves.  Held here as a constant rather than
# streamed from Relay so that a truncated or slow upstream body can never turn
# into a broken image in a mail client.
TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)
UNSUBSCRIBE_SCOPES = ("client", "audience", "global")

_session_lock = threading.Lock()
_session: requests.Session | None = None
_session_key: tuple[str, int] | None = None


class BridgeOutcome(Enum):
    """What the bridge learned, independent of how a view chooses to answer."""

    #: Relay accepted and recorded the request.
    RECORDED = "recorded"
    #: Relay answered, and its answer was "I do not know this link".
    REJECTED = "rejected"
    #: Relay answered, and its answer was "that request was malformed".
    INVALID = "invalid"
    #: The request never reached a verdict: no route, timeout, refusal, 5xx.
    UNAVAILABLE = "unavailable"
    #: No Relay is configured for this deployment.
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True, slots=True)
class BridgeResult:
    outcome: BridgeOutcome
    status_code: int | None = None

    @property
    def answered(self) -> bool:
        return self.outcome in {
            BridgeOutcome.RECORDED,
            BridgeOutcome.REJECTED,
            BridgeOutcome.INVALID,
        }


def token_fingerprint(token: str) -> str:
    """A short, non-reversible handle for one recipient token.

    Correlating two operational lines needs a stable handle; nothing needs the
    token itself.  Twelve hex characters of SHA-256 is enough to correlate and
    far too little to attack, and it never leaves this process as a full digest.
    """

    if not isinstance(token, str) or not token:
        return "absent"
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()[:12]


def is_well_formed_token(token: object) -> bool:
    return isinstance(token, str) and TOKEN_PATTERN.fullmatch(token) is not None


def is_safe_click_destination(destination: object) -> bool:
    """Mirror Relay's own destination rule before anything is forwarded.

    Relay accepts an absolute ``http``/``https`` URL with a host.  Applying the
    same rule here keeps a ``javascript:`` or ``data:`` destination from ever
    reaching a ``Location`` header, in the normal path and in every fallback.
    """

    if not isinstance(destination, str) or not destination or len(destination) > 2048:
        return False
    if any(character in destination for character in ("\r", "\n", "\t")):
        return False
    try:
        parsed = urlsplit(destination)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def bridge_base_url() -> str:
    """The configured private Relay base, or an empty string when unconfigured.

    Resolved through ``core.runtime_config``, so an operator can move the relay
    without replacing every running container; the value the process booted with
    is still the answer until they do.
    """

    configured = relay_link_bridge_base_url().strip()
    if not configured:
        return ""
    parsed = urlsplit(configured)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query:
        return ""
    return configured.rstrip("/")


def is_configured() -> bool:
    return bool(bridge_base_url())


def _pool() -> requests.Session:
    """One pooled session per process, keyed on the settings it was built from.

    The open pixel can arrive 130,000 times in the minutes after a send.  A new
    TCP connection per pixel would be pure waste against a single in-VPC host, so
    connections are pooled.  The key lets a test override settings without
    inheriting a session built for a different base URL.
    """

    global _session, _session_key
    pool_size = max(1, get_int_setting("relay.link_bridge.pool_size"))
    key = (bridge_base_url(), pool_size)
    with _session_lock:
        if _session is None or _session_key != key:
            session = requests.Session()
            # No adapter-level retries: every endpoint here decides its own
            # failure behaviour, and a silent retry would spend the caller's
            # latency budget without telling the caller it had done so.
            adapter = HTTPAdapter(
                pool_connections=pool_size,
                pool_maxsize=pool_size,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            session.trust_env = False
            _session = session
            _session_key = key
        return _session


def reset_pool() -> None:
    """Drop the pooled session.  Used by tests and by settings changes."""

    global _session, _session_key
    with _session_lock:
        if _session is not None:
            _session.close()
        _session = None
        _session_key = None


def _timeout(key: str, fallback: float) -> float:
    """One runtime-resolved timeout in seconds, never zero and never negative."""

    try:
        value = float(get_int_setting(key))
    except (LookupError, TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _call(
    method: str,
    path: str,
    *,
    timeout: float,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> requests.Response | None:
    base = bridge_base_url()
    if not base:
        return None
    try:
        return _pool().request(
            method,
            f"{base}{path}",
            params=params,
            data=data,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException:
        # The exception text can carry the full URL, and the URL carries the
        # token.  It is swallowed here rather than chained, logged or re-raised,
        # so no traceback or error report can ever hold a recipient token.
        return None


def _classify(response: requests.Response | None) -> BridgeResult:
    if response is None:
        return BridgeResult(BridgeOutcome.UNAVAILABLE)
    status = response.status_code
    if 200 <= status < 400:
        return BridgeResult(BridgeOutcome.RECORDED, status)
    if status == 404:
        return BridgeResult(BridgeOutcome.REJECTED, status)
    if status in {400, 405, 409, 410, 422}:
        return BridgeResult(BridgeOutcome.INVALID, status)
    # 401/403 mean the in-VPC route reached an authenticating proxy rather than
    # Relay; 5xx means Relay is unwell.  Neither is a verdict about the link.
    return BridgeResult(BridgeOutcome.UNAVAILABLE, status)


def record_open(token: str) -> BridgeResult:
    """Forward one open to Relay.  Never raises; the caller always has a GIF."""

    if not is_configured():
        return BridgeResult(BridgeOutcome.NOT_CONFIGURED)
    if not is_well_formed_token(token):
        return BridgeResult(BridgeOutcome.REJECTED)
    response = _call(
        "GET",
        f"/t/o/{quote(token, safe='')}.gif",
        timeout=_timeout("relay.link_bridge.open_timeout_seconds", 2.0),
    )
    return _classify(response)


def record_click(token: str, destination: str) -> BridgeResult:
    """Ask Relay to verify and record one click.  Never raises."""

    if not is_configured():
        return BridgeResult(BridgeOutcome.NOT_CONFIGURED)
    if not is_well_formed_token(token) or not is_safe_click_destination(destination):
        return BridgeResult(BridgeOutcome.INVALID)
    response = _call(
        "GET",
        f"/t/c/{quote(token, safe='')}",
        params={"u": destination},
        timeout=_timeout("relay.link_bridge.click_timeout_seconds", 3.0),
    )
    return _classify(response)


def load_unsubscribe(token: str) -> BridgeResult:
    """Ask Relay whether one unsubscribe token is still live.

    Relay's ``public_unsubscribe`` GET is side-effect free -- it renders a page
    and mutates nothing -- so this is a read, and the initial GET of the website
    page never changes a preference.
    """

    if not is_configured():
        return BridgeResult(BridgeOutcome.NOT_CONFIGURED)
    if not is_well_formed_token(token):
        return BridgeResult(BridgeOutcome.REJECTED)
    response = _call(
        "GET",
        f"/unsubscribe/{quote(token, safe='')}",
        timeout=_timeout("relay.link_bridge.unsubscribe_timeout_seconds", 10.0),
    )
    return _classify(response)


def submit_unsubscribe(token: str, scope: str) -> BridgeResult:
    """Apply one unsubscribe in Relay.  Never raises.

    Relay's endpoint is CSRF-exempt and idempotent for an already-unsubscribed
    recipient, which is what makes the durable replay in ``services`` safe to
    retry.
    """

    if not is_configured():
        return BridgeResult(BridgeOutcome.NOT_CONFIGURED)
    if not is_well_formed_token(token):
        return BridgeResult(BridgeOutcome.REJECTED)
    if scope not in UNSUBSCRIBE_SCOPES:
        return BridgeResult(BridgeOutcome.INVALID)
    response = _call(
        "POST",
        f"/unsubscribe/{quote(token, safe='')}",
        data={"scope": scope},
        timeout=_timeout("relay.link_bridge.unsubscribe_timeout_seconds", 10.0),
    )
    return _classify(response)
