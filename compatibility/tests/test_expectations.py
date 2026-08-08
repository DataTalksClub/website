from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from compatibility.expectations import (
    DEFAULT_EXPECTATION_SCHEMA,
    ApprovedExpectation,
    ApprovedExpectationSet,
    Disposition,
    ExpectationDecodeError,
    ExpectationValidationError,
    ExpectedResponse,
    QueryPolicy,
    approved_expectation_id,
    dumps_expectations,
    loads_expectations,
)
from compatibility.models import (
    PageMetadata,
    Reference,
    ReferenceKind,
    ReviewState,
    SitemapEntry,
    SitemapState,
    StructuredData,
)
from compatibility.schema import load_schema, validate_record

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
DOCS_URL = "https://datatalks.club/docs/"
OLD_URL = "https://datatalks.club/docs/old/?q=machine+learning&q="
NEW_URL = "https://datatalks.club/docs/new/"


def metadata(
    url: str = DOCS_URL,
    *,
    soft_404: bool = False,
    client_redirect_url: str = "",
) -> PageMetadata:
    return PageMetadata(
        title="DataTalks.Club documentation",
        description="Practical community documentation",
        first_heading="Documentation",
        language="en",
        robots=("follow", "index"),
        canonical_url=url,
        client_redirect_url=client_redirect_url,
        alternates=(("en", url),),
        social_metadata=(
            ("og:image", "https://datatalks.club/images/docs.png"),
            ("twitter:card", "summary_large_image"),
        ),
        structured_data=(
            StructuredData("Organization", "https://datatalks.club/#organization"),
            StructuredData("WebSite", "https://datatalks.club/#website"),
        ),
        fragments=("introduction", "setup"),
        references=(
            Reference(ReferenceKind.ASSET, "https://datatalks.club/docs/assets/site.css"),
            Reference(ReferenceKind.FORM_ACTION, "https://datatalks.club/docs/search/"),
            Reference(ReferenceKind.INTERNAL_LINK, f"{url}#setup"),
        ),
        main_content_fingerprint=SHA_D,
        soft_404=soft_404,
    )


def response(
    url: str = DOCS_URL,
    *,
    status: int = 200,
    page_metadata: PageMetadata | None = None,
) -> ExpectedResponse:
    return ExpectedResponse(
        status=status,
        final_url=url,
        content_type="text/html; charset=utf-8",
        response_last_modified="Sat, 08 Aug 2026 06:30:00 GMT",
        response_content_language="en",
        response_robots=("follow", "index"),
        body_sha256=SHA_C,
        metadata=page_metadata or metadata(url),
        sitemap=SitemapState((SitemapEntry(url, "2026-08-08"),)),
    )


def preserve(url: str = DOCS_URL, scope: str = "docs") -> ApprovedExpectation:
    return ApprovedExpectation.preserve(
        source_scope=scope,
        public_url=url,
        expected_response=response(url),
    )


def expectation_set(*items: ApprovedExpectation) -> ApprovedExpectationSet:
    return ApprovedExpectationSet.create(
        manifest_sha256=SHA_A,
        differences_sha256=SHA_B,
        public_contracts_sha256=SHA_C,
        expectations=tuple(items),
    )


def test_expectation_set_round_trips_canonically_and_validates_schema() -> None:
    keep = preserve()
    redirect = ApprovedExpectation.redirect(
        source_scope="main",
        public_url="https://datatalks.club/old.html",
        redirect_status=301,
        redirect_target="https://datatalks.club/new.html",
        query_policy=QueryPolicy.EXACT,
        expected_response=response(
            "https://datatalks.club/new.html",
            page_metadata=metadata("https://datatalks.club/new.html"),
        ),
        owner="web-team",
        reason="The canonical replacement contains equivalent content",
        test_reference="compatibility/tests/test_expectations.py::test_redirect",
    )
    retired = ApprovedExpectation.retire(
        source_scope="faq",
        public_url="https://datatalks.club/faq/retired.html",
        owner="content-team",
        reason="The reviewed page has no replacement",
        test_reference="compatibility/tests/test_expectations.py::test_retire",
    )
    approved = expectation_set(keep, redirect, retired)

    encoded = dumps_expectations(approved)
    record = json.loads(encoded)

    validate_record(record, load_schema(DEFAULT_EXPECTATION_SCHEMA))
    assert loads_expectations(encoded) == approved
    assert dumps_expectations(loads_expectations(encoded)) == encoded
    assert len(approved.sha256) == 64
    assert approved.scopes == ("docs", "faq", "main")
    assert approved.by_public_url()[DOCS_URL] == keep
    with pytest.raises(FrozenInstanceError):
        approved.expectations = ()  # type: ignore[misc]


def test_empty_set_is_valid_evidence_but_contains_no_implicit_approvals() -> None:
    approved = expectation_set()

    assert approved.expectations == ()
    assert approved.scopes == ()
    assert loads_expectations(dumps_expectations(approved)) == approved


def test_expected_response_retains_existing_metadata_and_sitemap_vocabulary() -> None:
    expected = response()
    loaded = loads_expectations(dumps_expectations(expectation_set(preserve())))

    assert loaded.expectations[0].expected_response == expected
    assert expected.metadata.structured_data == (
        StructuredData("Organization", "https://datatalks.club/#organization"),
        StructuredData("WebSite", "https://datatalks.club/#website"),
    )
    assert expected.sitemap.entries[0].lastmod == "2026-08-08"


def test_preserve_requires_independent_approval_and_exact_safe_response() -> None:
    identifier = approved_expectation_id("docs", DOCS_URL)
    base = preserve()

    with pytest.raises(ExpectationValidationError, match="approved_parity"):
        replace(base, review_state=ReviewState.PROPOSED_PRESERVE)
    with pytest.raises(ExpectationValidationError, match="exception_fields"):
        replace(base, owner="web-team")
    with pytest.raises(ExpectationValidationError, match="end_at_public_url"):
        replace(base, expected_response=response(NEW_URL))
    with pytest.raises(ExpectationValidationError, match="status_is_not_approvable"):
        replace(base, expected_response=response(DOCS_URL, status=404))
    with pytest.raises(ExpectationValidationError, match="soft_404"):
        replace(
            base,
            expected_response=response(DOCS_URL, page_metadata=metadata(soft_404=True)),
        )
    with pytest.raises(ExpectationValidationError, match="client_redirect"):
        replace(
            base,
            expected_response=response(
                DOCS_URL,
                page_metadata=metadata(client_redirect_url=NEW_URL),
            ),
        )
    with pytest.raises(ExpectationValidationError, match="not_canonical"):
        replace(base, expectation_id="expectation-" + "0" * 24)
    assert identifier == base.expectation_id


@pytest.mark.parametrize("status", [301, 308])
def test_redirect_requires_exact_permanent_one_hop_contract(status: int) -> None:
    redirect = ApprovedExpectation.redirect(
        source_scope="docs",
        public_url="https://datatalks.club/docs/old/",
        redirect_status=status,
        redirect_target=NEW_URL,
        query_policy=QueryPolicy.PRESERVE_RAW,
        expected_response=response(
            NEW_URL,
            page_metadata=metadata(NEW_URL),
        ),
        owner="web-team",
        reason="The old document has an exact equivalent",
        test_reference="compatibility/tests/test_expectations.py::test_redirect",
    )

    assert redirect.resolved_redirect_target == NEW_URL
    assert redirect.redirect_status == status
    assert loads_expectations(dumps_expectations(expectation_set(redirect))).expectations == (
        redirect,
    )


def test_redirect_rejects_implicit_or_unsafe_policy() -> None:
    redirect = ApprovedExpectation.redirect(
        source_scope="docs",
        public_url="https://datatalks.club/docs/old/",
        redirect_status=301,
        redirect_target=NEW_URL,
        query_policy=QueryPolicy.EXACT,
        expected_response=response(NEW_URL, page_metadata=metadata(NEW_URL)),
        owner="web-team",
        reason="The old document has an exact equivalent",
        test_reference="compatibility/tests/test_expectations.py::test_redirect",
    )

    with pytest.raises(ExpectationValidationError, match="301_or_308"):
        replace(redirect, redirect_status=302)
    with pytest.raises(ExpectationValidationError, match="approved_exception"):
        replace(redirect, review_state=ReviewState.APPROVED_PARITY)
    with pytest.raises(ExpectationValidationError, match="requires_evidence"):
        replace(redirect, reason="")
    with pytest.raises(ExpectationValidationError, match="successful"):
        replace(redirect, expected_response=response(NEW_URL, status=404))
    with pytest.raises(ExpectationValidationError, match="exact_target"):
        replace(redirect, expected_response=response(DOCS_URL))
    with pytest.raises(ExpectationValidationError, match="homepage"):
        replace(
            redirect,
            redirect_target="https://datatalks.club/",
            expected_response=response(
                "https://datatalks.club/",
                page_metadata=metadata("https://datatalks.club/"),
            ),
        )
    with pytest.raises(ExpectationValidationError, match="must_not_contain_query"):
        replace(
            redirect,
            query_policy=QueryPolicy.PRESERVE_RAW,
            redirect_target=f"{NEW_URL}?fixed=1",
        )


def test_retire_is_independently_approved_and_has_no_response_or_redirect() -> None:
    retired = ApprovedExpectation.retire(
        source_scope="docs",
        public_url="https://datatalks.club/docs/retired/",
        owner="web-team",
        reason="The reviewed page has no replacement",
        test_reference="compatibility/tests/test_expectations.py::test_retire",
    )

    assert retired.disposition is Disposition.RETIRE
    with pytest.raises(ExpectationValidationError, match="direct_410"):
        replace(retired, expected_response=response())
    with pytest.raises(ExpectationValidationError, match="approved_exception"):
        replace(retired, review_state=ReviewState.APPROVED_PARITY)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"unknown": True}), "invalid"),
        (lambda value: value.update({"schema_version": True}), "invalid"),
        (
            lambda value: value["expectations"][0].update(
                {"expectation_id": "expectation-" + "0" * 24}
            ),
            "invalid",
        ),
    ],
)
def test_decoder_rejects_unknown_boolean_and_forged_values(mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    record = json.loads(dumps_expectations(expectation_set(preserve())))
    mutate(record)
    text = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"

    with pytest.raises(ExpectationDecodeError, match=message):
        loads_expectations(text)


def test_decoder_rejects_duplicate_keys_and_noncanonical_encoding() -> None:
    canonical = dumps_expectations(expectation_set(preserve()))
    duplicate = canonical.replace(
        '"schema_version":1',
        '"schema_version":1,"schema_version":1',
        1,
    )

    with pytest.raises(ExpectationDecodeError, match="duplicate_key"):
        loads_expectations(duplicate)
    with pytest.raises(ExpectationDecodeError, match="not_canonical"):
        loads_expectations(json.dumps(json.loads(canonical), indent=2) + "\n")


def test_set_rejects_duplicate_urls_unsorted_rows_and_wrong_digests() -> None:
    first = preserve()
    duplicate = replace(first)
    second = preserve("https://datatalks.club/articles.html", "main")

    with pytest.raises(ExpectationValidationError, match="ids_must_be_unique"):
        ApprovedExpectationSet(SHA_A, SHA_B, SHA_C, (first, duplicate))
    with pytest.raises(ExpectationValidationError, match="canonically_sorted"):
        ApprovedExpectationSet(SHA_A, SHA_B, SHA_C, (second, first))
    with pytest.raises(ExpectationValidationError, match="must_be_sha256"):
        ApprovedExpectationSet("not-a-digest", SHA_B, SHA_C)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: replace(item, public_url="https://datatalks.club/reset/alex@example.com"),
        lambda item: replace(item, public_url="https://datatalks.club/?token=secret"),
        lambda item: replace(item, source_scope="docs scope"),
    ],
)
def test_expectations_reject_private_or_unsafe_identity(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ExpectationValidationError):
        mutation(preserve())


def test_expectations_reject_private_evidence_and_oversized_metadata() -> None:
    retired = ApprovedExpectation.retire(
        source_scope="docs",
        public_url="https://datatalks.club/docs/retired/",
        owner="web-team",
        reason="The reviewed page has no replacement",
        test_reference="compatibility/tests/test_expectations.py::test_retire",
    )

    with pytest.raises(ExpectationValidationError, match="private_data"):
        replace(retired, reason="Contact private@example.com for approval")
    with pytest.raises(ExpectationValidationError, match="private_data"):
        replace(retired, reason="Approved with " + "ghp_" + "a" * 30)
    with pytest.raises(ExpectationValidationError, match="private_data"):
        replace(retired, test_reference="github_pat_" + "a" * 30)
    with pytest.raises(ExpectationValidationError, match="invalid_unicode"):
        replace(retired, reason="\ud800")
    with pytest.raises(ExpectationValidationError, match="private_data"):
        ExpectedResponse(
            status=200,
            final_url=DOCS_URL,
            metadata=PageMetadata(title="password=hunter2"),
        )
    oversized = PageMetadata(title="x" * 16_385)
    with pytest.raises(ExpectationValidationError, match="too_long"):
        ExpectedResponse(status=200, final_url=DOCS_URL, metadata=oversized)
    scaled_asset = ExpectedResponse(
        status=200,
        final_url=DOCS_URL,
        metadata=PageMetadata(
            social_metadata=(("og:image", "https://datatalks.club/logo@2x.png"),)
        ),
    )
    assert scaled_asset.metadata.social_metadata[0][1].endswith("logo@2x.png")

    serialized = dumps_expectations(
        ApprovedExpectationSet.create(
            manifest_sha256=SHA_A,
            differences_sha256=SHA_B,
            public_contracts_sha256=SHA_C,
            expectations=(retired,),
        )
    )
    escaped_surrogate = serialized.replace(
        '"reason":"The reviewed page has no replacement"',
        '"reason":"\\ud800"',
    )
    with pytest.raises(ExpectationDecodeError, match="invalid"):
        loads_expectations(escaped_surrogate)

    with pytest.raises(ExpectationValidationError, match="safe_https_url"):
        ApprovedExpectation.retire(
            source_scope="docs",
            public_url="https://datatalks.club/bad%GG",
            owner="web-team",
            reason="No replacement",
            test_reference="compatibility/tests/test_expectations.py::test_retire",
        )
    with pytest.raises(ExpectationValidationError, match="safe_https_url"):
        replace(retired, public_url="https://datatalks.club/password=hunter2")


def test_schema_files_are_committed_at_the_declared_location() -> None:
    assert DEFAULT_EXPECTATION_SCHEMA == (
        Path(__file__).resolve().parents[2]
        / "_docs"
        / "compatibility"
        / "approved-expectation.schema.json"
    )
    assert load_schema(DEFAULT_EXPECTATION_SCHEMA)["additionalProperties"] is False
