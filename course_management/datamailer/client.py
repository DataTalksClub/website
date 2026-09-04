import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings

from core.runtime_config import get_bool_setting, get_int_setting, get_str_setting

from .client_campaigns import DatamailerCampaignClient
from .client_contacts import DatamailerContactClient
from .client_recipient_lists import DatamailerRecipientListClients
from .client_transactional import DatamailerTransactionalClient
from .client_types import DatamailerRequestData

logger = logging.getLogger(__name__)

# Bulk sends post the whole recipient list inline, so they need far longer
# than a single transactional message. This is not just a false-failure
# problem: when we hang up early Datamailer abandons the dispatch it was
# midway through, and every message it had created but not yet sent stays
# "queued" forever with no error and no retry. A July 2026 reminder run
# delivered to exactly one recipient -- the first in the list -- and
# stranded the rest.
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class DatamailerConfig:
    url: str
    api_key: str
    client: str
    audience: str
    from_email: str = ""
    strict: bool = False
    transactional_dry_run: bool = False
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_settings(cls) -> "DatamailerConfig | None":
        """Resolve the mailer from the runtime registry, keeping the key in the environment.

        Everything an operator tunes -- which endpoint, which client, which
        audience, which sender, whether a send is a dry run -- is resolved
        through ``core.runtime_config``, so a change reaches every running task
        without a restart.  ``DATAMAILER_API_KEY`` is not, and cannot be: the
        settings table is readable by anything that can read the database.
        """

        url = get_str_setting("datamailer.url")
        api_key = getattr(settings, "DATAMAILER_API_KEY", "")
        client = get_str_setting("datamailer.client")
        audience = get_str_setting("datamailer.audience")
        from_email = get_str_setting("datamailer.from_email")

        if not all([url, api_key, client, audience]):
            return None

        strict = get_bool_setting("datamailer.strict")
        transactional_dry_run = get_bool_setting("datamailer.transactional_dry_run")
        # The registry types the timeout as whole seconds; ``requests`` wants a float.
        timeout = float(get_int_setting("datamailer.timeout_seconds"))
        normalized_url = url.rstrip("/")
        return cls(
            url=normalized_url,
            api_key=api_key,
            client=client,
            audience=audience,
            from_email=from_email,
            strict=strict,
            transactional_dry_run=transactional_dry_run,
            timeout=timeout,
        )


class DatamailerClient:
    def __init__(
        self,
        config: DatamailerConfig,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self.contacts = DatamailerContactClient(config, self.request)
        self.recipient_lists = DatamailerRecipientListClients(
            config,
            self.request,
        )
        self.transactional = DatamailerTransactionalClient(
            config,
            self.request,
        )
        self.campaigns = DatamailerCampaignClient(config, self.request)

    def request(self, data: DatamailerRequestData) -> dict[str, Any] | None:
        url = f"{self.config.url}{data.path}"
        request_kwargs: dict[str, Any] = {
            "json": data.json,
            "timeout": self.config.timeout,
            "headers": {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        }
        if data.params is not None:
            request_kwargs["params"] = data.params

        response = self.session.request(
            data.method,
            url,
            **request_kwargs,
        )
        response.raise_for_status()

        if not response.content:
            return None

        return response.json()


def datamailer_enabled() -> bool:
    return DatamailerConfig.from_settings() is not None


def public_url(path: str) -> str:
    base_url = _notification_base_url()
    normalized_base_url = f"{base_url}/"
    normalized_path = path.lstrip("/")
    return urljoin(normalized_base_url, normalized_path)


def _notification_base_url() -> str:
    """Absolute base URL for links in notification emails.

    Notifications are built without a request, so they can't fall back to the
    request host the way in-app confirmation emails do. If ``PUBLIC_BASE_URL``
    is unset or lacks a scheme/host we must still emit an absolute URL with a
    real host — otherwise emails go out with unusable hostless links like
    ``http:///course-slug/leaderboard``.
    """
    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return base_url

    fallback = _fallback_base_url()
    if base_url:
        logger.warning(
            "PUBLIC_BASE_URL=%r has no scheme/host; using %r for "
            "notification links.",
            base_url,
            fallback,
        )
    return fallback


def _fallback_base_url() -> str:
    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if host and host != "*":
            return f"https://{host}"
    return "https://courses.datatalks.club"
