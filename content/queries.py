from __future__ import annotations

from dataclasses import dataclass

from django.db.models import F

from core.services import ServiceContext

from .models import ContentAsset, ContentDocument, ContentRelease


@dataclass(frozen=True, slots=True)
class ResolvePublicDocument:
    exact_public_path: str


@dataclass(frozen=True, slots=True)
class ResolvePublicAsset:
    stable_public_path: str


@dataclass(frozen=True, slots=True)
class PublishedDocument:
    content_kind: str
    stable_key: str
    exact_public_path: str
    slug: str
    title: str
    summary: str
    canonical_url: str
    seo_title: str
    seo_description: str
    seo_image_url: str
    rendered_html: str
    noindex: bool
    edit_url: str


@dataclass(frozen=True, slots=True)
class PublishedAsset:
    stable_public_path: str
    storage_key: str
    content_type: str
    size: int
    checksum: str


def _is_exact_public_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not value.startswith("//")
        and "?" not in value
        and "#" not in value
        and not any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    )


def resolve_public_document(
    query: ResolvePublicDocument,
    *,
    context: ServiceContext,
    using: str = "default",
) -> PublishedDocument | None:
    del context
    if not _is_exact_public_path(query.exact_public_path):
        return None
    row = (
        ContentDocument.objects.using(using)
        .filter(
            exact_public_path=query.exact_public_path,
            is_published=True,
            release__status=ContentRelease.Status.ACTIVE,
            release__source__enabled=True,
            release_id=F("release__source__active_release_id"),
        )
        .values(
            "content_kind",
            "stable_key",
            "exact_public_path",
            "slug",
            "title",
            "summary",
            "canonical_url",
            "seo_title",
            "seo_description",
            "seo_image_url",
            "rendered_html",
            "noindex",
            "edit_url",
        )
        .first()
    )
    if row is None:
        return None
    return PublishedDocument(
        content_kind=row["content_kind"],
        stable_key=row["stable_key"],
        exact_public_path=str(row["exact_public_path"]),
        slug=row["slug"],
        title=row["title"],
        summary=row["summary"],
        canonical_url=row["canonical_url"],
        seo_title=row["seo_title"],
        seo_description=row["seo_description"],
        seo_image_url=row["seo_image_url"],
        rendered_html=row["rendered_html"],
        noindex=row["noindex"],
        edit_url=row["edit_url"],
    )


def resolve_public_asset(
    query: ResolvePublicAsset,
    *,
    context: ServiceContext,
    using: str = "default",
) -> PublishedAsset | None:
    del context
    if not _is_exact_public_path(query.stable_public_path):
        return None
    row = (
        ContentAsset.objects.using(using)
        .filter(
            stable_public_path=query.stable_public_path,
            release__status=ContentRelease.Status.ACTIVE,
            release__source__enabled=True,
            release_id=F("release__source__active_release_id"),
        )
        .values(
            "stable_public_path",
            "storage_key",
            "content_type",
            "size",
            "checksum",
        )
        .first()
    )
    if row is None:
        return None
    return PublishedAsset(
        stable_public_path=row["stable_public_path"],
        storage_key=row["storage_key"],
        content_type=row["content_type"],
        size=row["size"],
        checksum=row["checksum"],
    )
