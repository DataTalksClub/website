"""One configurable source of truth for the development deployment hostname.

Three things must agree on where development is served: the Django settings the
deployed image runs with, the ECS task-definition environment the release tool
writes, and the base URL the deployed smoke test drives.  They all read the
hostname from here, so moving development to a different host is a configuration
change rather than an edit spread across the release tooling.

``DTC_DEVELOPMENT_HOSTNAME`` selects the host.  The selection is closed: only a
hostname in :data:`PERMITTED_DEVELOPMENT_HOSTNAMES` is accepted, so a mistyped or
injected value fails at import instead of widening ``ALLOWED_HOSTS`` on a
deployed service.  Adding a further development host stays a reviewed code
change; choosing between the reviewed ones does not.

The physical AWS identifiers of a deployment -- role ARNs, cluster and service
names, subnets, security groups -- are deliberately *not* here.  They belong to a
reviewed deployment target in ``deploy.deployment_targets``, which is what the
release pipeline binds to; this module exists so ``website.settings.development``
can name its host without importing the release tooling's target registry.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

DEVELOPMENT_HOSTNAME_VARIABLE = "DTC_DEVELOPMENT_HOSTNAME"
#: The historical development host.  Its stack has been decommissioned; the
#: default stays until the replacement service below is built, so that nothing
#: silently retargets at a hostname that does not resolve yet.
DEFAULT_DEVELOPMENT_HOSTNAME = "web.dtcdev.click"
#: Reviewed development hosts.  ``dev.datatalks.club`` is the planned
#: replacement and is listed so the move is a variable change; it is not the
#: default because the service behind it does not exist yet.
PERMITTED_DEVELOPMENT_HOSTNAMES = frozenset(
    {
        "web.dtcdev.click",
        "dev.datatalks.club",
    }
)


class DevelopmentTargetError(RuntimeError):
    """The configured development hostname is not a reviewed development host."""


def development_hostname(environ: Mapping[str, str] | None = None) -> str:
    """Return the selected development hostname, failing closed on anything else."""

    values = os.environ if environ is None else environ
    hostname = values.get(DEVELOPMENT_HOSTNAME_VARIABLE) or DEFAULT_DEVELOPMENT_HOSTNAME
    if hostname not in PERMITTED_DEVELOPMENT_HOSTNAMES:
        raise DevelopmentTargetError(
            f"{DEVELOPMENT_HOSTNAME_VARIABLE} must name a reviewed development host"
        )
    return hostname


def development_origin(environ: Mapping[str, str] | None = None) -> str:
    """Return the exact HTTPS origin of the selected development hostname."""

    return f"https://{development_hostname(environ)}"


DEVELOPMENT_HOSTNAME = development_hostname()
DEVELOPMENT_ORIGIN = development_origin()
