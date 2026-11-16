"""Bounded, read-only Slack roster client; credentials and profiles stay in memory."""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

SLACK_ID = re.compile(r"[UW][A-Z0-9]{5,31}\Z")
ERROR_CODES = frozenset(
    {
        "account_inactive",
        "invalid_auth",
        "missing_scope",
        "not_authed",
        "token_expired",
        "token_revoked",
        "not_allowed_token_type",
        "ratelimited",
        "access_denied",
    }
)


class SlackReadError(RuntimeError):
    """Only fixed diagnostic codes escape the provider boundary."""


@dataclass(frozen=True)
class SlackMember:
    slack_user_id: str
    email: str = field(default="", repr=False)
    inviter_slack_id: str = ""
    joined_at: datetime | None = None


class SlackReadClient:
    """Expose no write methods, follow no redirects, and verify workspace identity."""

    def __init__(self, token: str, team_id: str = ""):
        if not token or not team_id:
            raise SlackReadError("missing_configuration")
        self._token = token
        self._team_id = team_id

    def _call(self, method: str, **parameters: str | int) -> dict:
        if method not in {"auth.test", "users.list"}:
            raise SlackReadError("method_not_allowed")
        try:
            for attempt in range(3):
                response = requests.post(
                    f"https://slack.com/api/{method}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    data=parameters,
                    timeout=30,
                    allow_redirects=False,
                )
                if response.status_code != 429:
                    break
                delay = response.headers.get("Retry-After", "")
                if not delay.isdecimal() or not 1 <= int(delay) <= 60 or attempt == 2:
                    raise SlackReadError("ratelimited")
                time.sleep(int(delay))
            if response.status_code == 429:
                raise SlackReadError("ratelimited")
            if response.status_code != 200:
                raise SlackReadError("http_failed")
            result = response.json()
        except SlackReadError:
            raise
        except Exception:
            raise SlackReadError("transport_failed") from None
        if not isinstance(result, dict):
            raise SlackReadError("invalid_response")
        if result.get("ok") is not True:
            code = result.get("error")
            raise SlackReadError(
                code if isinstance(code, str) and code in ERROR_CODES else "provider_rejected"
            )
        return result

    def members(self) -> list[SlackMember]:
        identity = self._call("auth.test")
        if identity.get("team_id") != self._team_id:
            raise SlackReadError("unexpected_workspace")
        members: dict[str, SlackMember] = {}
        cursor = ""
        seen_cursors: set[str] = set()
        # Stay below Slack's documented 1000 row maximum. The larger page size matters
        # for a large workspace subject to the Tier 2 per-minute request allowance.
        started_at = time.monotonic()
        for _ in range(1000):
            if time.monotonic() - started_at > 1800:
                raise SlackReadError("pagination_timeout")
            page = self._call("users.list", limit=999, cursor=cursor, team_id=self._team_id)
            rows = page.get("members")
            if not isinstance(rows, list):
                raise SlackReadError("invalid_response")
            for row in rows:
                if not isinstance(row, dict):
                    raise SlackReadError("invalid_response")
                if (
                    any(
                        row.get(key)
                        for key in (
                            "deleted",
                            "is_bot",
                            "is_app_user",
                            "is_invited_user",
                            "is_stranger",
                        )
                    )
                    or row.get("id") == "USLACKBOT"
                ):
                    continue
                user_id = row.get("id")
                if not isinstance(user_id, str) or not SLACK_ID.fullmatch(user_id):
                    raise SlackReadError("invalid_response")
                row_team = row.get("team_id") or row.get("team")
                if row_team and row_team != self._team_id:
                    continue
                profile = row.get("profile") or {}
                if not isinstance(profile, dict):
                    raise SlackReadError("invalid_response")
                email = profile.get("email") or ""
                if not isinstance(email, str) or len(email) > 254:
                    raise SlackReadError("invalid_response")
                # Documented guest provenance only. A channel inviter is not a workspace inviter.
                inviter = profile.get("guest_invited_by") or ""
                if not isinstance(inviter, str) or (inviter and not SLACK_ID.fullmatch(inviter)):
                    raise SlackReadError("invalid_response")
                members[user_id] = SlackMember(user_id, email, inviter)
                if len(members) > 200000:
                    raise SlackReadError("pagination_limit")
            metadata = page.get("response_metadata") or {}
            if not isinstance(metadata, dict):
                raise SlackReadError("invalid_response")
            cursor = metadata.get("next_cursor") or ""
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise SlackReadError("invalid_response")
            if not cursor:
                if page.get("has_more"):
                    raise SlackReadError("incomplete_pagination")
                return list(members.values())
            if cursor in seen_cursors:
                raise SlackReadError("pagination_loop")
            seen_cursors.add(cursor)
        raise SlackReadError("pagination_limit")
