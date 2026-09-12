"""Durable content-release cache invalidation intent.

Positive public edge caching stays disabled (deployed TTLs are pinned to zero),
so the handler validates the durable intent and performs no network call; it
remains the hand-off seam for the cache-provider rollout.
"""

from __future__ import annotations

import uuid

from community_base.jobs.registry import JobContext, JobPayload, register_handler
from community_base.jobs.runner import PermanentJobError

CONTENT_RELEASE_INVALIDATION_VERSION = 1
_TRANSITIONS = frozenset({"activation", "rollback"})


def release_invalidation_prefixes(mount_path: str) -> list[str]:
    """Return the reviewed wildcard coverage for one source's transition.

    The source mount covers its details, stable assets, and aliases. The
    site-root prefix stands in for hubs, feeds, and sitemap outputs that render
    any source's content; narrowing it waits for a real dependency graph, since
    an over-broad invalidation is safer than an incomplete one.
    """

    prefixes = [mount_path]
    if mount_path != "/":
        prefixes.append("/")
    return prefixes


def _is_release_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value


def _is_invalidation_prefix(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not value.startswith("//")
        and value.endswith("/")
        and "?" not in value
        and "#" not in value
        and all(not character.isspace() and 0x20 <= ord(character) != 0x7F for character in value)
    )


@register_handler("content.release.invalidate")
def invalidate_release_paths(context: JobContext, payload: JobPayload) -> None:
    """Consume one release-transition invalidation intent; no network today."""

    del context
    source_revision = payload.get("source_revision")
    from_release_id = payload.get("from_release_id")
    prefixes = payload.get("path_prefixes")
    if (
        payload.get("version") != CONTENT_RELEASE_INVALIDATION_VERSION
        or payload.get("transition") not in _TRANSITIONS
        or not _is_release_uuid(payload.get("source_id"))
        or not (from_release_id is None or _is_release_uuid(from_release_id))
        or not _is_release_uuid(payload.get("to_release_id"))
        or not isinstance(source_revision, int)
        or isinstance(source_revision, bool)
        or source_revision < 1
        or not isinstance(prefixes, list)
        or not prefixes
        or any(not _is_invalidation_prefix(prefix) for prefix in prefixes)
    ):
        raise PermanentJobError("invalid_release_invalidation_payload")
