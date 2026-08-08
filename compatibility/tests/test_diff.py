from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from compatibility.diff import (
    DIFFERENCE_SCHEMA_VERSION,
    DifferenceKind,
    _safe_string,
    diff_rows,
    diff_source_production,
    dumps_differences,
)
from compatibility.models import (
    Capture,
    Classification,
    CompatibilityRow,
    ObservationOrigin,
    PageMetadata,
    Reference,
    ReferenceKind,
)

REPOSITORY = "https://github.com/DataTalksClub/example.git"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "_docs"
    / "compatibility"
    / "legacy-manifest-differences.schema.json"
)


def capture(
    origin: ObservationOrigin,
    path: str,
    *,
    status: int = 200,
    metadata: PageMetadata | None = None,
    body_sha256: str = "",
) -> Capture:
    return Capture.create(
        origin=origin,
        requested_url=f"https://datatalks.club{path}",
        status=status,
        source_repository=REPOSITORY if origin is ObservationOrigin.SOURCE else "",
        source_path=(path.removeprefix("/") or "index.html")
        if origin is ObservationOrigin.SOURCE
        else "",
        content_type="text/html",
        metadata=metadata,
        body_sha256=body_sha256,
    )


def row(item: Capture) -> CompatibilityRow:
    if item.origin is ObservationOrigin.SOURCE:
        return CompatibilityRow(Classification.preserve(), source_capture=item)
    return CompatibilityRow(Classification.preserve(), production_capture=item)


def metadata(
    *,
    title: str = "Legacy page",
    canonical: str = "https://datatalks.club/kept.html",
    client_redirect: str = "",
    fragments: tuple[str, ...] = ("kept",),
    assets: tuple[str, ...] = (),
) -> PageMetadata:
    references = [
        Reference(ReferenceKind.ASSET, f"https://datatalks.club{asset}") for asset in assets
    ]
    references.append(
        Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/articles.html")
    )
    return PageMetadata(
        title=title,
        canonical_url=canonical,
        client_redirect_url=client_redirect,
        fragments=fragments,
        references=tuple(sorted(references, key=lambda item: (item.kind.value, item.url))),
    )


def test_removed_route_fragment_asset_and_changed_canonical_are_actionable() -> None:
    before = (
        row(capture(ObservationOrigin.SOURCE, "/removed.html")),
        row(
            capture(
                ObservationOrigin.SOURCE,
                "/kept.html",
                metadata=metadata(
                    title="Before",
                    fragments=("kept", "removed-fragment"),
                    assets=("/assets/removed.css",),
                ),
            )
        ),
    )
    after = (
        row(capture(ObservationOrigin.SOURCE, "/added.html")),
        row(
            capture(
                ObservationOrigin.SOURCE,
                "/kept.html",
                metadata=metadata(
                    title="After",
                    canonical="https://datatalks.club/new-canonical.html",
                    fragments=("added-fragment", "kept"),
                    assets=("/assets/added.css",),
                ),
            )
        ),
    )

    differences = diff_rows(before, after)
    by_kind = {kind: [item for item in differences if item.kind is kind] for kind in DifferenceKind}

    assert [item.public_url for item in by_kind[DifferenceKind.ROUTE_REMOVED]] == [
        "https://datatalks.club/removed.html"
    ]
    assert [item.public_url for item in by_kind[DifferenceKind.ROUTE_ADDED]] == [
        "https://datatalks.club/added.html"
    ]
    assert [item.subject for item in by_kind[DifferenceKind.FRAGMENT_REMOVED]] == [
        "removed-fragment"
    ]
    assert [item.subject for item in by_kind[DifferenceKind.FRAGMENT_ADDED]] == ["added-fragment"]
    assert [item.subject for item in by_kind[DifferenceKind.ASSET_REMOVED]] == [
        "https://datatalks.club/assets/removed.css"
    ]
    assert [item.subject for item in by_kind[DifferenceKind.ASSET_ADDED]] == [
        "https://datatalks.club/assets/added.css"
    ]
    assert [
        (item.before, item.after, item.required_action)
        for item in by_kind[DifferenceKind.CANONICAL_CHANGED]
    ] == [
        (
            "https://datatalks.club/kept.html",
            "https://datatalks.club/new-canonical.html",
            "restore_or_approve_canonical",
        )
    ]
    assert any(
        item.kind is DifferenceKind.FIELD_CHANGED
        and item.field == "source_capture.metadata.title"
        and item.before == "Before"
        and item.after == "After"
        and item.required_action == "review_field_change"
        for item in differences
    )
    assert all(item.schema_version == DIFFERENCE_SCHEMA_VERSION for item in differences)
    assert all(item.difference_id.startswith("sha256:") for item in differences)


def test_source_production_404_transition_is_a_route_removal() -> None:
    source = capture(ObservationOrigin.SOURCE, "/lost.html", status=200)
    production = capture(ObservationOrigin.PRODUCTION, "/lost.html", status=404)
    differences = diff_source_production(
        (CompatibilityRow(Classification.preserve(), source, production),)
    )

    assert differences == (
        next(item for item in differences if item.kind is DifferenceKind.ROUTE_REMOVED),
    )
    assert differences[0].field == "status"
    assert differences[0].before == 200
    assert differences[0].after == 404
    assert differences[0].required_action == "restore_or_classify_route"


def test_source_production_ignores_operational_crawl_accounting() -> None:
    source = capture(ObservationOrigin.SOURCE, "/same.html", body_sha256="a" * 64)
    production = replace(
        capture(ObservationOrigin.PRODUCTION, "/same.html", body_sha256="a" * 64),
        response_count=2,
        transfer_bytes=1_234,
    )

    differences = diff_source_production(
        (CompatibilityRow(Classification.preserve(), source, production),)
    )

    assert differences == ()


def test_client_redirect_target_change_is_observational_and_actionable() -> None:
    before = row(
        capture(
            ObservationOrigin.SOURCE,
            "/client-redirect.html",
            metadata=metadata(client_redirect="https://datatalks.club/old-target.html"),
        )
    )
    after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/client-redirect.html",
            metadata=metadata(client_redirect="https://datatalks.club/new-target.html"),
        )
    )

    differences = diff_rows((before,), (after,))

    client_redirect = next(
        item for item in differences if item.field.endswith("metadata.client_redirect_url")
    )
    assert client_redirect.kind is DifferenceKind.FIELD_CHANGED
    assert client_redirect.before == "https://datatalks.club/old-target.html"
    assert client_redirect.after == "https://datatalks.club/new-target.html"
    assert client_redirect.required_action == "review_field_change"


def test_reordered_manifests_and_difference_records_serialize_identically() -> None:
    first_before = row(capture(ObservationOrigin.SOURCE, "/first.html"))
    second_before = row(capture(ObservationOrigin.SOURCE, "/second.html"))
    first_after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/first.html",
            metadata=PageMetadata(title="Changed first"),
        )
    )
    added = row(capture(ObservationOrigin.SOURCE, "/added.html"))

    forward = diff_rows((first_before, second_before), (first_after, added))
    reversed_inputs = diff_rows((second_before, first_before), (added, first_after))

    assert forward == reversed_inputs
    assert dumps_differences(forward) == dumps_differences(reversed(forward))
    document = json.loads(dumps_differences(forward))
    assert document["record_kind"] == "legacy_manifest_differences"
    assert document["schema_version"] == DIFFERENCE_SCHEMA_VERSION
    assert dumps_differences(forward) == (
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    )


def test_committed_schema_matches_the_emitted_version_and_vocabulary() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    difference_properties = schema["$defs"]["difference"]["properties"]

    assert schema["properties"]["record_kind"]["const"] == "legacy_manifest_differences"
    assert schema["properties"]["schema_version"]["const"] == DIFFERENCE_SCHEMA_VERSION
    assert set(difference_properties["kind"]["enum"]) == {kind.value for kind in DifferenceKind}
    assert set(difference_properties["required_action"]["enum"]) == {
        "restore_or_approve_asset",
        "restore_or_approve_canonical",
        "restore_or_approve_fragment",
        "restore_or_classify_route",
        "review_and_baseline_asset",
        "review_and_baseline_fragment",
        "review_and_baseline_route",
        "review_field_change",
    }


def test_stable_difference_id_does_not_depend_on_changed_value() -> None:
    before = row(
        capture(
            ObservationOrigin.SOURCE,
            "/stable-id.html",
            metadata=PageMetadata(title="Before"),
        )
    )
    first_after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/stable-id.html",
            metadata=PageMetadata(title="First candidate"),
        )
    )
    second_after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/stable-id.html",
            metadata=PageMetadata(title="Second candidate"),
        )
    )

    first = next(
        item
        for item in diff_rows((before,), (first_after,))
        if item.field == "source_capture.metadata.title"
    )
    second = next(
        item
        for item in diff_rows((before,), (second_after,))
        if item.field == "source_capture.metadata.title"
    )

    assert first.difference_id == second.difference_id
    assert first.after != second.after


def test_difference_does_not_include_response_bodies_or_duplicate_rows() -> None:
    before = row(
        capture(
            ObservationOrigin.PRODUCTION,
            "/digest.html",
            body_sha256="a" * 64,
        )
    )
    after = row(
        capture(
            ObservationOrigin.PRODUCTION,
            "/digest.html",
            body_sha256="b" * 64,
        )
    )

    serialized = dumps_differences(diff_rows((before,), (after,)))

    assert '"field":"production_capture.body_sha256"' in serialized
    assert "<html" not in serialized
    assert "response_body" not in serialized
    with pytest.raises(ValueError, match="duplicate_compatibility_row_url"):
        diff_rows((before, before), (after,))


def test_difference_records_reject_unredacted_personal_data() -> None:
    before = row(
        capture(
            ObservationOrigin.SOURCE,
            "/safe.html",
            metadata=PageMetadata(title="Before"),
        )
    )
    after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/safe.html",
            metadata=PageMetadata(title="After"),
        )
    )
    title_change = next(
        item
        for item in diff_rows((before,), (after,))
        if item.field == "source_capture.metadata.title"
    )

    with pytest.raises(ValueError, match="unredacted_private_value"):
        replace(title_change, after="Contact private-person@example.com")
    with pytest.raises(ValueError, match="unredacted_private_value"):
        replace(title_change, after="Bearer abcdefghijklmnopqrstuvwxyz")


def test_asset_density_suffixes_are_preserved_as_urls_in_actionable_diffs() -> None:
    before = row(
        capture(
            ObservationOrigin.SOURCE,
            "/assets.html",
            metadata=PageMetadata(
                references=(
                    Reference(
                        ReferenceKind.ASSET,
                        "https://datatalks.club/images/favicon_black@2x.png",
                    ),
                )
            ),
        )
    )
    after = row(
        capture(
            ObservationOrigin.SOURCE,
            "/assets.html",
            metadata=PageMetadata(
                references=(
                    Reference(
                        ReferenceKind.ASSET,
                        "https://datatalks.club/images/favicon_black@3x.png",
                    ),
                )
            ),
        )
    )

    asset_differences = [
        item
        for item in diff_rows((before,), (after,))
        if item.kind in {DifferenceKind.ASSET_ADDED, DifferenceKind.ASSET_REMOVED}
    ]

    assert [(item.kind, item.subject) for item in asset_differences] == [
        (
            DifferenceKind.ASSET_ADDED,
            "https://datatalks.club/images/favicon_black@3x.png",
        ),
        (
            DifferenceKind.ASSET_REMOVED,
            "https://datatalks.club/images/favicon_black@2x.png",
        ),
    ]
    assert "favicon_black@2x.png" in dumps_differences(asset_differences)
    assert "favicon_black@3x.png" in dumps_differences(asset_differences)


@pytest.mark.parametrize("query", ["email=person@example.com", "token=top-secret-value"])
def test_absolute_url_query_private_values_are_redacted_without_losing_structure(
    query: str,
) -> None:
    original = f"https://datatalks.club/download?{query}#kept-fragment"

    safe = _safe_string("subject", original)

    assert safe.startswith("https://datatalks.club/download?")
    assert safe.endswith("#kept-fragment")
    assert "example.com" not in safe
    assert "top-secret-value" not in safe
    assert "redacted-sha256-" in safe


def test_absolute_url_userinfo_is_rejected_without_echoing_credentials() -> None:
    unsafe = "https://private-user:private-password@datatalks.club/asset.png"

    with pytest.raises(ValueError) as raised:
        _safe_string("subject", unsafe)

    assert str(raised.value) == "url_contains_credentials"
    assert "private-user" not in str(raised.value)
    assert "private-password" not in str(raised.value)
