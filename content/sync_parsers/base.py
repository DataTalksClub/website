"""Shared plumbing for the site parsers registered with the package engine.

The package orchestration calls, per registered parser, ``discover`` then
``upsert`` per item then ``soft_delete_missing``.  The record JSON these
parsers build is the same projection shape ``scripts/build_public_projection.py``
emits, so the staged catalogue and the synced rows stay structurally comparable
and the D2.2c cutover reads the rows without a second translation.

The record helpers are imported from the projection builder instead of copied:
it is the reviewed definition of these record shapes and it stays in the
repository for the staged pipeline.
"""

from contextvars import ContextVar
from typing import NoReturn

from community_base.content_sync.checkout import ImmutableCheckout

from content.models import SyncedDocument
from scripts import build_public_projection as builder

_active_checkout: ContextVar[ImmutableCheckout | None] = ContextVar(
    "content_sync_active_checkout", default=None
)


class ContentParserError(ValueError):
    """A source file failed its parse rules; the sync surfaces it as one bounded error."""


def activate(checkout):
    """Bind the checkout for this sync; ``upsert`` reads it because the package
    parser protocol does not hand the checkout past ``discover``."""

    return _active_checkout.set(checkout)


def active_checkout() -> ImmutableCheckout:
    checkout = _active_checkout.get()
    if checkout is None:
        raise RuntimeError("No active content sync checkout; upsert requires discover first")
    return checkout


def fail(code: str, source_path: str) -> NoReturn:
    raise ContentParserError(f"{code}: {source_path}")


def checksum_of(checkout, relative_path: str) -> str:
    return builder._sha256_bytes(checkout.read_bytes(relative_path))


def snapshot_path(checkout, relative_path: str):
    """Return the real file inside the immutable snapshot for Path-based helpers.

    The builder helpers read from disk; the checkout root is the private,
    already-snapshotted copy, so this never touches the source repository.
    """

    return checkout._validated_path(relative_path)


def upsert_document(
    source,
    *,
    content_kind,
    stable_key,
    slug,
    title,
    summary,
    public_path,
    source_path,
    checksum,
    record,
):
    """Write one parsed record; return ``(document, action)`` for the package result.

    The source checksum decides the action: the record derives deterministically
    from the source bytes, so an unchanged checksum means an unchanged row and
    the write is skipped.
    """

    values = {
        "slug": slug,
        "title": title,
        "summary": summary,
        "public_path": public_path,
        "source_path": source_path,
        "checksum": checksum,
        "record": record,
        "is_published": True,
    }
    document = SyncedDocument.objects.filter(
        source=source, content_kind=content_kind, stable_key=stable_key
    ).first()
    if document is None:
        return (
            SyncedDocument.objects.create(
                source=source,
                content_kind=content_kind,
                stable_key=stable_key,
                **values,
            ),
            "created",
        )
    if document.checksum == checksum:
        return document, "unchanged"
    for field, value in values.items():
        setattr(document, field, value)
    document.save()
    return document, "updated"


def delete_missing(source, content_kind, seen_keys: set[str]) -> int:
    deleted, _ = (
        SyncedDocument.objects.filter(source=source, content_kind=content_kind)
        .exclude(stable_key__in=seen_keys)
        .delete()
    )
    return deleted
