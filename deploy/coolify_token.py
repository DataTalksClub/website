"""Fail loudly when the Coolify API token stops working or nears expiry.

The Contabo staging deploy (``.github/workflows/deploy-coolify.yml``) drives
Coolify through its REST API with the ``coolify-staging`` environment secret
``COOLIFY_TOKEN``.  Coolify API tokens are Laravel Sanctum tokens and can carry
an expiry: on 2026-11-20 the original token expired at 22:12 UTC and the next
deploy died with a bare ``curl: (22) ... 401`` halfway through the job.

This module turns that into two explicit checks:

* ``check`` asks Coolify whether the token authenticates (``GET
  /api/v1/version``) and names the cause and the runbook on a 401/403.
* With ``--expires-at`` (the ``COOLIFY_TOKEN_EXPIRES_AT`` environment variable,
  recorded when the token is minted) it also fails once the expiry is within
  ``--warn-days``, so the scheduled ``coolify-token-check.yml`` workflow goes
  red weeks before a deploy can break.

Stdlib-only so the workflows can run it with the runner's ``python3``.
Rotation steps live in ``_docs/runbooks/coolify-api-token.md``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

RUNBOOK = "_docs/runbooks/coolify-api-token.md"
DEFAULT_WARN_DAYS = 30
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class TokenDecision:
    ok: bool
    message: str


def decide_auth(status: int | None, error: str = "") -> TokenDecision:
    """Classify the HTTP status Coolify returned for an authenticated GET."""
    if status is not None and 200 <= status < 300:
        return TokenDecision(True, f"Coolify API token authenticates (HTTP {status}).")
    if status in (401, 403):
        return TokenDecision(
            False,
            f"Coolify rejected COOLIFY_TOKEN (HTTP {status}): the token expired, "
            "was revoked, or lost its abilities. Mint a new scoped token and "
            f"update the coolify-staging secret; see {RUNBOOK}.",
        )
    if status is None:
        return TokenDecision(False, f"Coolify API unreachable: {error or 'no response'}.")
    return TokenDecision(False, f"Coolify API returned HTTP {status} for the token check.")


def decide_expiry(expires_at: str, *, today: dt.date, warn_days: int) -> TokenDecision:
    """Fail when the recorded token expiry is missing, past, or within warn_days."""
    value = expires_at.strip()
    if not value:
        return TokenDecision(
            False,
            "COOLIFY_TOKEN_EXPIRES_AT is not set; record the token expiry "
            f"(YYYY-MM-DD) as a coolify-staging variable; see {RUNBOOK}.",
        )
    try:
        expiry = dt.date.fromisoformat(value[:10])
    except ValueError:
        return TokenDecision(
            False, f"COOLIFY_TOKEN_EXPIRES_AT={value!r} is not a YYYY-MM-DD date; see {RUNBOOK}."
        )
    days_left = (expiry - today).days
    if days_left < 0:
        return TokenDecision(
            False, f"COOLIFY_TOKEN expired on {expiry}; rotate it now; see {RUNBOOK}."
        )
    if days_left <= warn_days:
        return TokenDecision(
            False,
            f"COOLIFY_TOKEN expires on {expiry} ({days_left} days left, "
            f"threshold {warn_days}); rotate it before deploys break; see {RUNBOOK}.",
        )
    return TokenDecision(True, f"COOLIFY_TOKEN expires on {expiry} ({days_left} days left).")


def probe(url: str, token: str) -> tuple[int | None, str]:
    """Return (HTTP status, error text) for GET {url}/api/v1/version."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/version",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def check(
    *,
    url: str,
    token: str,
    expires_at: str | None,
    warn_days: int,
    today: dt.date,
    prober: Callable[[str, str], tuple[int | None, str]] = probe,
) -> list[TokenDecision]:
    if not url or not token:
        return [TokenDecision(False, f"COOLIFY_URL or COOLIFY_TOKEN is empty; see {RUNBOOK}.")]
    decisions = [decide_auth(*prober(url, token))]
    if expires_at is not None:
        decisions.append(decide_expiry(expires_at, today=today, warn_days=warn_days))
    return decisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check"])
    parser.add_argument(
        "--expires-at",
        default=None,
        help="Recorded token expiry (YYYY-MM-DD); also fail when within --warn-days.",
    )
    parser.add_argument("--warn-days", type=int, default=DEFAULT_WARN_DAYS)
    args = parser.parse_args(argv)

    decisions = check(
        url=os.environ.get("COOLIFY_URL", ""),
        token=os.environ.get("COOLIFY_TOKEN", ""),
        expires_at=args.expires_at,
        warn_days=args.warn_days,
        today=dt.datetime.now(dt.UTC).date(),
    )
    for decision in decisions:
        if decision.ok:
            print(decision.message)
        else:
            print(f"::error::{decision.message}", file=sys.stderr)
    return 0 if all(decision.ok for decision in decisions) else 1


if __name__ == "__main__":
    sys.exit(main())
