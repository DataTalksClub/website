from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from compatibility.models import (
    MANIFEST_SCHEMA_VERSION,
    Capture,
    Classification,
    ClassificationKind,
    CompatibilityRow,
    ManifestDecodeError,
    ManifestProvenance,
    ManifestValidationError,
    ObservationOrigin,
    PageMetadata,
    RedirectHop,
    Reference,
    ReferenceKind,
    ReviewState,
    SitemapEntry,
    SitemapState,
    SourceRevision,
    StructuredData,
    dumps_jsonl,
    loads_jsonl,
)

REPOSITORY = "https://github.com/DataTalksClub/example.git"
REVISION = "0123456789abcdef0123456789abcdef01234567"
FINGERPRINT = "a" * 64
POLICY_HASH = "b" * 64


def source_capture(path: str = "/docs/") -> Capture:
    return Capture.create(
        origin=ObservationOrigin.SOURCE,
        requested_url=f"https://datatalks.club{path}",
        status=200,
        source_repository=REPOSITORY,
        source_path="_site/docs/index.html",
        content_type="text/html",
        metadata=PageMetadata(
            title="Documentation",
            description="Data engineering documentation",
            first_heading="Documentation",
            language="en",
            robots=("follow", "index"),
            canonical_url=f"https://datatalks.club{path}",
            alternates=(("en", f"https://datatalks.club{path}"),),
            social_metadata=(("og:title", "Documentation"),),
            structured_data=(StructuredData("WebPage", "docs"),),
            fragments=("start-here",),
            references=(
                Reference(ReferenceKind.ASSET, "https://datatalks.club/docs/assets/site.css"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/docs/start/"),
            ),
            main_content_fingerprint=FINGERPRINT,
        ),
        sitemap=SitemapState((SitemapEntry(f"https://datatalks.club{path}", "2026-08-08"),)),
    )


def production_capture(path: str = "/docs/") -> Capture:
    return Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=f"https://datatalks.club{path}",
        status=200,
        content_type="text/html",
        response_last_modified="Sat, 08 Aug 2026 06:30:00 GMT",
        response_robots=("follow", "index"),
        body_sha256="c" * 64,
        metadata=PageMetadata(title="Production documentation"),
    )


def provenance() -> ManifestProvenance:
    return ManifestProvenance.create(
        generated_at=datetime(2026, 8, 8, 6, 30, tzinfo=UTC),
        tool_version="dtc-legacy-manifest-crawler/1",
        source_revisions=(SourceRevision("docs", REPOSITORY, REVISION),),
        production_origins=("https://courses.datatalks.club", "https://datatalks.club"),
        allowlisted_hosts=("courses.datatalks.club", "datatalks.club"),
        crawl_policy_sha256=POLICY_HASH,
    )


@pytest.mark.parametrize(
    "generated_at",
    [
        "0000-01-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-08-08T24:00:00Z",
        "2026-08-08T12:60:00Z",
    ],
)
def test_provenance_rejects_impossible_calendar_timestamps(generated_at: str) -> None:
    with pytest.raises(ManifestValidationError, match="utc_rfc3339"):
        ManifestProvenance.create(generated_at=generated_at, tool_version="crawler/3")


def test_capture_create_is_strict_frozen_and_fills_final_url() -> None:
    capture = source_capture()

    assert capture.final_url == capture.requested_url
    assert capture.metadata.structured_data == (StructuredData("WebPage", "docs"),)
    with pytest.raises(FrozenInstanceError):
        capture.status = 404  # type: ignore[misc]
    with pytest.raises(ManifestValidationError, match="status"):
        Capture.create(
            origin=ObservationOrigin.SOURCE,
            requested_url="https://datatalks.club/",
            status=True,  # type: ignore[arg-type]
            source_repository=REPOSITORY,
            source_path="index.html",
        )
    with pytest.raises(ManifestValidationError, match="body_sha256"):
        Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url="https://datatalks.club/",
            status=200,
            body_sha256="not-a-digest",
        )
    with pytest.raises(ManifestValidationError, match="source_location"):
        Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url="https://datatalks.club/",
            status=200,
            source_repository=REPOSITORY,
            source_path="index.html",
        )


def test_redirect_hops_are_typed_and_must_end_at_final_url() -> None:
    target = "https://datatalks.club/docs/new/"
    capture = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url="https://datatalks.club/docs/old/",
        status=200,
        final_url=target,
        redirect_chain=(RedirectHop(301, target),),
    )

    assert capture.redirect_chain[0].status == 301
    with pytest.raises(ManifestValidationError, match="end_at_final"):
        Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url="https://datatalks.club/docs/old/",
            status=200,
            final_url="https://datatalks.club/docs/elsewhere/",
            redirect_chain=(RedirectHop(301, target),),
        )


def test_failed_capture_is_actionable_without_claiming_response_data() -> None:
    failure = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url="https://datatalks.club/redirect-loop",
        status=0,
        error_code="redirect_loop",
    )

    assert failure.error_code == "redirect_loop"
    assert CompatibilityRow(Classification.preserve(), None, failure).production_capture is failure
    with pytest.raises(ManifestValidationError, match="status_zero_and_error_code"):
        Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url="https://datatalks.club/redirect-loop",
            status=0,
        )
    with pytest.raises(ManifestValidationError, match="must_not_claim_response_data"):
        Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url="https://datatalks.club/redirect-loop",
            status=0,
            error_code="redirect_loop",
            body_sha256="d" * 64,
        )


def test_source_revision_and_provenance_are_canonical() -> None:
    second = SourceRevision(
        "courses",
        "https://github.com/DataTalksClub/course-management-platform.git",
        "f" * 40,
    )
    first = SourceRevision("docs", REPOSITORY, REVISION)

    manifest_provenance = ManifestProvenance.create(
        generated_at=datetime(2026, 8, 8, 8, 30, tzinfo=UTC),
        tool_version="crawler/1",
        source_revisions=(first, second),
        production_origins=("https://datatalks.club", "https://courses.datatalks.club"),
        allowlisted_hosts=("datatalks.club", "courses.datatalks.club"),
    )

    assert manifest_provenance.generated_at == "2026-08-08T08:30:00Z"
    assert [item.source_id for item in manifest_provenance.source_revisions] == [
        "courses",
        "docs",
    ]
    assert manifest_provenance.production_origins == (
        "https://courses.datatalks.club",
        "https://datatalks.club",
    )
    with pytest.raises(ManifestValidationError, match="timezone_aware"):
        ManifestProvenance.create(
            generated_at=datetime(2026, 8, 8, 8, 30),
            tool_version="crawler/1",
        )


def test_metadata_rejects_nondeterministic_collection_order() -> None:
    with pytest.raises(ManifestValidationError, match="page_robots_must_be_unique_and_sorted"):
        PageMetadata(robots=("index", "follow"))
    with pytest.raises(ManifestValidationError, match="page_references_must_be_unique_and_sorted"):
        PageMetadata(
            references=(
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/z"),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/a.css"),
            )
        )


def test_row_preserves_source_and_production_observations_separately() -> None:
    source = source_capture()
    production = production_capture()

    row = CompatibilityRow(Classification.preserve(), source, production)

    assert row.source_capture is source
    assert row.production_capture is production
    assert row.source_capture.metadata.title == "Documentation"
    assert row.production_capture.metadata.title == "Production documentation"


def test_classification_review_state_is_explicit_and_approval_is_opt_in() -> None:
    assert Classification.preserve().review_state is ReviewState.PROPOSED_PRESERVE
    assert Classification.preserve(approved=True).review_state is ReviewState.APPROVED_PARITY
    with pytest.raises(ManifestValidationError, match="invalid_review_state"):
        Classification(ClassificationKind.PRESERVE, ReviewState.APPROVED_EXCEPTION)
    with pytest.raises(ManifestValidationError, match="explicitly_approved"):
        Classification(
            ClassificationKind.REDIRECT,
            redirect_target="https://datatalks.club/docs/new/",
            owner="seo-team",
            reason="Equivalent content moved",
            test_reference="compatibility/tests/test_redirects.py::test_old_docs",
        )


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: StructuredData("Article", "https://datatalks.club/article?access_token=unredacted"),
        lambda: PageMetadata(
            social_metadata=(("og:image", "https://datatalks.club/image?token=unredacted"),)
        ),
        lambda: PageMetadata(fragments=("access_token=unredacted",)),
        lambda: PageMetadata(
            references=(
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    "https://datatalks.club/page#access_token=unredacted",
                ),
            )
        ),
    ],
)
def test_models_reject_unredacted_sensitive_url_metadata(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(ManifestValidationError, match="unredacted_sensitive_value"):
        constructor()


def test_model_rejects_unredacted_client_redirect_sensitive_query() -> None:
    with pytest.raises(
        ManifestValidationError, match="page_client_redirect_url_contains_sensitive_query_key"
    ):
        PageMetadata(client_redirect_url="https://datatalks.club/new?token=unredacted")


def test_row_rejects_capture_origin_or_url_mismatch() -> None:
    with pytest.raises(ManifestValidationError, match="wrong_origin"):
        CompatibilityRow(Classification.preserve(), production_capture(), None)
    with pytest.raises(ManifestValidationError, match="urls_must_match"):
        CompatibilityRow(
            Classification.preserve(),
            source_capture("/docs/"),
            production_capture("/faq/"),
        )


@pytest.mark.parametrize("kind", ["redirect", "retire"])
def test_exception_classification_requires_owner_reason_and_test(kind: str) -> None:
    constructor = Classification.redirect if kind == "redirect" else Classification.retire
    arguments: dict[str, str] = {
        "owner": "seo-team",
        "reason": "Equivalent canonical destination approved",
        "test_reference": "compatibility/test_redirects.py::test_old_docs",
    }
    if kind == "redirect":
        arguments["target"] = "https://datatalks.club/docs/new/"
    for missing in ("owner", "reason", "test_reference"):
        invalid = arguments | {missing: ""}
        with pytest.raises(ManifestValidationError, match="requires_owner_reason_and_test"):
            constructor(**invalid)


def test_redirect_classification_requires_one_permanent_hop_to_exact_target() -> None:
    old = "https://datatalks.club/docs/old/"
    target = "https://datatalks.club/docs/new/"
    classification = Classification.redirect(
        target,
        owner="seo-team",
        reason="Equivalent content moved",
        test_reference="compatibility/test_redirects.py::test_old_docs",
    )
    valid = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=old,
        status=200,
        final_url=target,
        redirect_chain=(RedirectHop(301, target),),
    )

    assert CompatibilityRow(classification, None, valid).classification is classification
    temporary = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=old,
        status=200,
        final_url=target,
        redirect_chain=(RedirectHop(302, target),),
    )
    with pytest.raises(ManifestValidationError, match="permanent_status"):
        CompatibilityRow(classification, None, temporary)
    chain_target = "https://datatalks.club/docs/intermediate/"
    chain = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=old,
        status=200,
        final_url=target,
        redirect_chain=(RedirectHop(301, chain_target), RedirectHop(301, target)),
    )
    with pytest.raises(ManifestValidationError, match="exactly_one_hop"):
        CompatibilityRow(classification, None, chain)

    missing_target = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=old,
        status=404,
        final_url=target,
        redirect_chain=(RedirectHop(301, target),),
    )
    with pytest.raises(ManifestValidationError, match="successful_target"):
        CompatibilityRow(classification, None, missing_target)


@pytest.mark.parametrize("target_path", ["/", "/index.html"])
def test_redirect_to_homepage_catch_all_is_rejected(target_path: str) -> None:
    old = "https://datatalks.club/docs/missing/"
    target = f"https://datatalks.club{target_path}"
    classification = Classification.redirect(
        target,
        owner="seo-team",
        reason="Invalid blanket rule",
        test_reference="compatibility/test_redirects.py::test_missing",
    )
    capture = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=old,
        status=200,
        final_url=target,
        redirect_chain=(RedirectHop(301, target),),
    )

    with pytest.raises(ManifestValidationError, match="homepage_is_forbidden"):
        CompatibilityRow(classification, None, capture)


def test_manifest_rejects_many_to_one_homepage_equivalent_redirects() -> None:
    target = "https://datatalks.club/"
    classification = Classification.redirect(
        target,
        owner="seo-team",
        reason="Invalid blanket rule",
        test_reference="compatibility/tests/test_redirects.py::test_catch_all",
    )

    def redirect_row(origin: str) -> CompatibilityRow:
        capture = Capture.create(
            origin=ObservationOrigin.PRODUCTION,
            requested_url=origin,
            status=200,
            final_url=target,
            redirect_chain=(RedirectHop(301, target),),
        )
        return CompatibilityRow(classification, production_capture=capture)

    with pytest.raises(ManifestValidationError, match="must_not_collapse_to_homepage"):
        dumps_jsonl(
            provenance(),
            (
                redirect_row("https://legacy-one.example/"),
                redirect_row("https://legacy-two.example/"),
            ),
        )


def test_retire_classification_requires_production_410_without_redirect() -> None:
    classification = Classification.retire(
        owner="seo-team",
        reason="No equivalent replacement exists",
        test_reference="compatibility/test_retirements.py::test_removed_page",
    )
    retired = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url="https://datatalks.club/removed.html",
        status=410,
    )

    assert CompatibilityRow(classification, None, retired).production_capture is retired
    with pytest.raises(ManifestValidationError, match="production_410"):
        CompatibilityRow(classification, None, production_capture())


def test_jsonl_is_deterministic_sorted_and_round_trips_without_overwriting_captures() -> None:
    docs = CompatibilityRow(Classification.preserve(), source_capture(), production_capture())
    faq = CompatibilityRow(
        Classification.preserve(),
        source_capture("/faq/"),
        production_capture("/faq/"),
    )

    serialized = dumps_jsonl(provenance(), (faq, docs))
    decoded_provenance, rows = loads_jsonl(serialized)

    assert serialized == dumps_jsonl(provenance(), (docs, faq))
    assert decoded_provenance == provenance()
    assert [row.public_url for row in rows] == [
        "https://datatalks.club/docs/",
        "https://datatalks.club/faq/",
    ]
    assert rows[0].source_capture != rows[0].production_capture
    assert all(
        json.loads(line)["schema_version"] == MANIFEST_SCHEMA_VERSION
        for line in serialized.splitlines()
    )


def test_jsonl_rejects_unknown_nested_keys_duplicate_keys_and_non_finite_values() -> None:
    serialized = dumps_jsonl(
        provenance(),
        (CompatibilityRow(Classification.preserve(), source_capture(), production_capture()),),
    )
    lines = serialized.splitlines()
    row = json.loads(lines[1])
    row["source_capture"]["metadata"]["surprise"] = True
    unknown = lines[0] + "\n" + json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    with pytest.raises(ManifestValidationError, match="unexpected_keys"):
        loads_jsonl(unknown)
    duplicate = lines[0].replace('"record_kind":', '"record_kind":"provenance","record_kind":', 1)
    with pytest.raises(ManifestDecodeError, match="line_1_is_invalid"):
        loads_jsonl(duplicate + "\n" + lines[1] + "\n")
    non_finite = lines[0].replace(
        f'"schema_version":{MANIFEST_SCHEMA_VERSION}',
        '"schema_version":NaN',
    )
    with pytest.raises(ManifestDecodeError, match="line_1_is_invalid"):
        loads_jsonl(non_finite + "\n" + lines[1] + "\n")


def test_v1_manifest_is_explicitly_rejected_after_required_metadata_shape_change() -> None:
    serialized = dumps_jsonl(
        provenance(),
        (CompatibilityRow(Classification.preserve(), source_capture=source_capture()),),
    )
    v1 = serialized.replace(
        f'"schema_version":{MANIFEST_SCHEMA_VERSION}',
        '"schema_version":1',
    )

    with pytest.raises(ManifestDecodeError, match="unsupported_manifest_schema_version"):
        loads_jsonl(v1)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://user:password@datatalks.club/docs/",
        "https://datatalks.club/docs/?access_token=not-for-a-manifest",
        "https://datatalks.club/docs/?accessToken=not-for-a-manifest",
        "https://datatalks.club/docs/?q=person%40example.com",
        "https://datatalks.club/person%40example.com/profile",
    ],
)
def test_urls_reject_credentials_and_personal_data(unsafe_url: str) -> None:
    with pytest.raises(ManifestValidationError):
        Reference(ReferenceKind.EXTERNAL_LINK, unsafe_url)


def test_urls_reject_raw_internal_whitespace() -> None:
    with pytest.raises(ManifestValidationError, match="contains_raw_whitespace"):
        Reference(
            ReferenceKind.INTERNAL_LINK,
            "https://datatalks.club/images/path with space.jpg",
        )


def test_scaled_asset_name_is_not_misclassified_as_personal_data() -> None:
    reference = Reference(
        ReferenceKind.ASSET,
        "https://datatalks.club/images/photo@2x.jpg",
    )

    assert reference.url.endswith("photo@2x.jpg")


def test_sensitive_query_value_accepts_only_deterministic_redaction() -> None:
    redacted_url = "https://datatalks.club/callback?accessToken=" + "redacted-sha256-" + "e" * 64

    assert Reference(ReferenceKind.INTERNAL_LINK, redacted_url).url == redacted_url
    with pytest.raises(ManifestValidationError, match="sensitive_query_key"):
        Reference(
            ReferenceKind.INTERNAL_LINK,
            "https://datatalks.club/callback?accessToken=redacted",
        )


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Contact person@example.com",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJabcdefghijk.abcdefghijk.abcdefghijk",
    ],
)
def test_manifest_text_rejects_pii_and_credential_shapes(unsafe_text: str) -> None:
    with pytest.raises(ManifestValidationError):
        PageMetadata(title=unsafe_text)


def test_jsonl_rejects_noncanonical_order_and_duplicate_urls() -> None:
    docs = CompatibilityRow(Classification.preserve(), source_capture(), production_capture())
    faq = CompatibilityRow(
        Classification.preserve(),
        source_capture("/faq/"),
        production_capture("/faq/"),
    )
    canonical = dumps_jsonl(provenance(), (docs, faq)).splitlines()

    with pytest.raises(ManifestDecodeError, match="unique_and_sorted"):
        loads_jsonl("\n".join((canonical[0], canonical[2], canonical[1])) + "\n")
    with pytest.raises(ManifestValidationError, match="unique_public_urls"):
        dumps_jsonl(provenance(), (docs, docs))
