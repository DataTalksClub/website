from __future__ import annotations

DTC_CONTENT_SOURCE_ID = "dtc-content"
DTC_CONTENT_CONTRACT_SOURCE_ID = "dtc-main-site"
DTC_CONTENT_DOCUMENT_KINDS = frozenset({"article", "podcast", "podcast_transcript", "book"})
DTC_CONTENT_ASSET_PREFIXES = (
    "/images/posts/",
    "/images/podcast/",
    "/images/books/",
)


def source_owns_document_kind(source_stable_id: str, content_kind: str) -> bool:
    """Return whether a source may prepare this editorial document kind.

    The adopted kinds have one candidate authority. Other content kinds remain available to their
    existing adapters until their own migrations land.
    """

    is_adopted = content_kind in DTC_CONTENT_DOCUMENT_KINDS
    return is_adopted == (source_stable_id == DTC_CONTENT_SOURCE_ID)


def source_owns_asset_path(source_stable_id: str, public_path: str) -> bool:
    """Return whether a source may prepare an adopted media namespace."""

    is_adopted = public_path.startswith(DTC_CONTENT_ASSET_PREFIXES)
    return is_adopted == (source_stable_id == DTC_CONTENT_SOURCE_ID)


def compatible_contract_source(source_stable_id: str, contract_source_id: str) -> bool:
    """Keep legacy observations attached while their editorial source moves."""

    if source_stable_id == DTC_CONTENT_SOURCE_ID:
        return contract_source_id == DTC_CONTENT_CONTRACT_SOURCE_ID
    return source_stable_id == contract_source_id
