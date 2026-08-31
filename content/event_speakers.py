"""The checked public view of speakers on an event detail page.

Event records own the ordered speaker credit (the source key, display name, and
profile path).  The biography is owned by the linked public person record.  This
small adapter joins those two records for rendering without copying a person's
bio into every event artifact.

Biography blocks remain untrusted text at this boundary.  The event template
renders them through ``public/_prose_body.html``, which escapes text and only
turns the already-supported Markdown link forms into anchors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

_BIO_BLOCK_KINDS = frozenset({"heading", "paragraph", "list_item"})


def _bio_blocks(person: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Copy the canonical person's renderable prose blocks into a view record.

    A malformed or absent person body degrades to an empty biography.  Every
    value retained here is consumed by the escaping ``public_text`` filter or an
    autoescaped template interpolation; this function never marks source HTML
    safe and never executes Markdown.
    """

    if not isinstance(person, Mapping):
        return ()
    raw_blocks = person.get("blocks", ())
    if not isinstance(raw_blocks, (list, tuple)):
        return ()

    blocks: list[dict[str, Any]] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping):
            continue
        kind = raw_block.get("kind")
        text = raw_block.get("text")
        if (
            not isinstance(kind, str)
            or kind not in _BIO_BLOCK_KINDS
            or not isinstance(text, str)
            or not text.strip()
        ):
            continue

        block: dict[str, Any] = {"kind": kind, "text": text}
        if kind == "heading":
            level = raw_block.get("level")
            if isinstance(level, int) and not isinstance(level, bool):
                block["level"] = level
            block_id = raw_block.get("id")
            if isinstance(block_id, str) and block_id.strip():
                block["id"] = block_id
        else:
            markdown = raw_block.get("markdown")
            if isinstance(markdown, str) and markdown.strip():
                block["markdown"] = markdown
        blocks.append(block)
    return tuple(blocks)


def event_speaker_records(
    credits: Any,
    *,
    people_by_slug: Mapping[str, dict[str, Any]],
    people_by_path: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Join event speaker credits to their canonical profile biographies.

    The checked event projection guarantees a list of mapping credits, but the
    adapter keeps the contract explicit and fails closed if that shape changes.
    A missing profile or empty/malformed profile body is a valid empty-bio state
    rather than a reason to drop the speaker credit.
    """

    if credits is None:
        return ()
    if not isinstance(credits, (list, tuple)):
        raise ImproperlyConfigured("Public event speakers must be a list.")

    records: list[dict[str, Any]] = []
    for credit in credits:
        if not isinstance(credit, Mapping):
            raise ImproperlyConfigured("Public event speaker must be a mapping.")
        copied = dict(credit)
        key = credit.get("key")
        public_path = credit.get("public_path")
        person = people_by_slug.get(key) if isinstance(key, str) and key else None
        if person is None and isinstance(public_path, str) and public_path:
            person = people_by_path.get(public_path)
        copied["bio_blocks"] = _bio_blocks(person)
        records.append(copied)
    return tuple(records)
