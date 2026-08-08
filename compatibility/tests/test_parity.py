from __future__ import annotations

from dataclasses import replace

import pytest
from django.test import override_settings

from compatibility.django import DjangoTargetCollector
from compatibility.expectations import (
    ApprovedExpectation,
    ApprovedExpectationSet,
    ExpectedResponse,
    QueryPolicy,
)
from compatibility.models import (
    PageMetadata,
    RedirectHop,
    Reference,
    ReferenceKind,
    SitemapEntry,
    SitemapState,
)
from compatibility.parity import evaluate_parity
from compatibility.report import ParityStatus, TargetBinding
from compatibility.target import TargetObservation

MANIFEST = "a" * 64
DIFFERENCES = "b" * 64
CONTRACTS = "c" * 64

FIXTURE_SETTINGS = override_settings(
    ROOT_URLCONF="compatibility.tests.fixture_urls",
    ALLOWED_HOSTS=["datatalks.club"],
    APPEND_SLASH=False,
    NOINDEX=False,
)


def target_binding() -> TargetBinding:
    return TargetBinding(
        target_id="fixture",
        target_origin="https://datatalks.club",
        release_id="fixture-v1",
        parser_version="compatibility-parser-v1",
        route_sha256="d" * 64,
        asset_sha256="e" * 64,
        projection_sha256="f" * 64,
    )


def expected(observation: TargetObservation) -> ExpectedResponse:
    return ExpectedResponse(
        status=observation.status,
        final_url=observation.final_url,
        content_type=observation.content_type,
        response_last_modified=observation.response_last_modified,
        response_content_language=observation.response_content_language,
        response_robots=observation.response_robots,
        body_sha256=observation.body_sha256,
        metadata=observation.metadata,
        sitemap=observation.sitemap,
    )


def approved_set(observations: tuple[TargetObservation, ...]) -> ApprovedExpectationSet:
    expectations = tuple(
        ApprovedExpectation.preserve(
            source_scope="fixture",
            public_url=observation.public_url,
            expected_response=expected(observation),
        )
        for observation in observations
    )
    return ApprovedExpectationSet.create(
        manifest_sha256=MANIFEST,
        differences_sha256=DIFFERENCES,
        public_contracts_sha256=CONTRACTS,
        expectations=expectations,
    )


@FIXTURE_SETTINGS
def test_fixture_family_produces_complete_deterministic_pass() -> None:
    collector = DjangoTargetCollector(allowed_hosts={"datatalks.club"})
    urls = (
        "https://datatalks.club/assets/logo.bin",
        "https://datatalks.club/docs/Exact/",
        "https://datatalks.club/fixture/",
        "https://datatalks.club/submit/?next=%2Fok",
    )
    observations = tuple(collector.observe(url) for url in urls)
    expectations = approved_set(observations)

    report = evaluate_parity(expectations, observations, target=target_binding())

    assert report.status is ParityStatus.PASS
    assert report.complete is True
    assert report.expectation_count == report.observation_count == report.matched_count == 4
    assert report.findings == ()


@FIXTURE_SETTINGS
def test_injected_metadata_staging_soft404_fragment_asset_and_js_only_losses_block() -> None:
    collector = DjangoTargetCollector(allowed_hosts={"datatalks.club"})
    baseline = collector.observe("https://datatalks.club/fixture/")
    expectations = approved_set((baseline,))
    changed_metadata = replace(
        baseline.metadata,
        title="Changed",
        canonical_url="https://web." + "dtcdev.click/fixture/",
        fragments=(),
        references=tuple(
            item for item in baseline.metadata.references if item.kind is not ReferenceKind.ASSET
        ),
        main_content_fingerprint="",
        soft_404=True,
    )
    changed = replace(baseline, metadata=changed_metadata)

    report = evaluate_parity(expectations, (changed,), target=target_binding())

    assert report.status is ParityStatus.BLOCKED
    assert {
        "asset_reference_changed",
        "canonical_changed",
        "canonical_non_production_origin",
        "fragment_set_changed",
        "server_rendered_body_missing",
        "soft_404",
        "title_changed",
    }.issubset({finding.code for finding in report.findings})


@FIXTURE_SETTINGS
def test_injected_jsonld_robots_sitemap_and_binary_asset_regressions_block() -> None:
    collector = DjangoTargetCollector(allowed_hosts={"datatalks.club"})
    fixture = collector.observe("https://datatalks.club/fixture/")
    sitemap = collector.observe("https://datatalks.club/sitemap.xml")
    asset = collector.observe("https://datatalks.club/assets/logo.bin")
    expectations = approved_set((fixture, sitemap, asset))
    bad_fixture = replace(
        fixture,
        capture_error="invalid_json_ld",
        metadata=replace(
            fixture.metadata,
            description="",
            first_heading="",
            robots=("noindex",),
            structured_data=(),
        ),
    )
    bad_sitemap = replace(
        sitemap,
        sitemap=SitemapState(
            entries=(
                SitemapEntry(
                    url="https://datatalks.club/fixture/",
                    lastmod="2026-08-07",
                ),
            )
        ),
    )
    bad_asset = replace(asset, body_sha256="0" * 64)

    report = evaluate_parity(
        expectations,
        (bad_fixture, bad_sitemap, bad_asset),
        target=target_binding(),
    )

    assert {
        "asset_body_changed",
        "description_changed",
        "first_heading_changed",
        "invalid_json_ld",
        "production_noindex",
        "robots_meta_changed",
        "sitemap_changed",
        "structured_data_changed",
    }.issubset({finding.code for finding in report.findings})


def test_exact_external_query_rewrite_and_optional_missing_policy_are_distinct() -> None:
    original = "https://external.example/resource?x=A+B&x=A%20B&blank="
    rewritten = "https://external.example/resource?x=A%20B&x=A+B&blank="
    baseline = TargetObservation(
        requested_url="https://datatalks.club/page",
        raw_network_reference="/page",
        status=200,
        final_url="https://datatalks.club/page",
        response_count=1,
        transfer_bytes=0,
        metadata=PageMetadata(references=(Reference(ReferenceKind.EXTERNAL_LINK, original),)),
    )
    rewritten_observation = replace(
        baseline,
        metadata=PageMetadata(references=(Reference(ReferenceKind.EXTERNAL_LINK, rewritten),)),
    )
    missing_optional = replace(baseline, metadata=PageMetadata())
    expectations = approved_set((baseline,))

    rewrite_report = evaluate_parity(
        expectations,
        (rewritten_observation,),
        target=target_binding(),
    )
    optional_report = evaluate_parity(
        expectations,
        (missing_optional,),
        target=target_binding(),
        optional_external_urls=frozenset({original}),
    )

    assert "external_reference_changed" in {finding.code for finding in rewrite_report.findings}
    assert optional_report.blocking_finding_count == 0
    assert optional_report.warning_finding_count == 1
    assert optional_report.status is ParityStatus.PASS


def test_unknown_content_type_uses_opaque_body_digest() -> None:
    baseline = TargetObservation(
        requested_url="https://datatalks.club/opaque",
        raw_network_reference="/opaque",
        status=200,
        final_url="https://datatalks.club/opaque",
        response_count=1,
        transfer_bytes=1,
        body_sha256="1" * 64,
    )
    expectations = approved_set((baseline,))

    report = evaluate_parity(
        expectations,
        (replace(baseline, body_sha256="0" * 64),),
        target=target_binding(),
    )

    assert report.status is ParityStatus.BLOCKED
    assert [finding.code for finding in report.findings] == ["asset_body_changed"]


@FIXTURE_SETTINGS
def test_fragment_redirect_compares_location_but_fetches_defragmented_target() -> None:
    captured = DjangoTargetCollector(allowed_hosts={"datatalks.club"}).observe(
        "https://datatalks.club/fragment-redirect"
    )
    observation = replace(captured, metadata=PageMetadata())
    expectation = ApprovedExpectation.redirect(
        source_scope="fixture",
        public_url=observation.public_url,
        redirect_status=301,
        redirect_target="https://datatalks.club/fixture/#Caf%C3%A9",
        query_policy=QueryPolicy.EXACT,
        expected_response=expected(observation),
        owner="seo-team",
        reason="Fixture fragment redirect",
        test_reference="compatibility/tests/test_parity.py",
    )
    expectations = ApprovedExpectationSet.create(
        manifest_sha256=MANIFEST,
        differences_sha256=DIFFERENCES,
        public_contracts_sha256=CONTRACTS,
        expectations=(expectation,),
    )

    report = evaluate_parity(
        expectations,
        (observation,),
        target=target_binding(),
    )

    assert observation.redirect_chain[0].url.endswith("/#Caf%C3%A9")
    assert observation.final_url == "https://datatalks.club/fixture/"
    assert report.status is ParityStatus.PASS


def test_redirect_chain_loop_target_failure_and_direct_retirement_are_evaluated() -> None:
    terminal = ExpectedResponse(
        status=200,
        final_url="https://datatalks.club/current",
        content_type="text/html",
    )
    redirect = ApprovedExpectation.redirect(
        source_scope="fixture",
        public_url="https://datatalks.club/old",
        redirect_status=301,
        redirect_target="https://datatalks.club/current",
        query_policy=QueryPolicy.EXACT,
        expected_response=terminal,
        owner="seo-team",
        reason="Fixture redirect",
        test_reference="compatibility/tests/test_parity.py",
    )
    retire = ApprovedExpectation.retire(
        source_scope="fixture",
        public_url="https://datatalks.club/gone",
        owner="seo-team",
        reason="Fixture retirement",
        test_reference="compatibility/tests/test_parity.py",
    )
    expectations = ApprovedExpectationSet.create(
        manifest_sha256=MANIFEST,
        differences_sha256=DIFFERENCES,
        public_contracts_sha256=CONTRACTS,
        expectations=(redirect, retire),
    )
    bad_redirect = TargetObservation(
        requested_url=redirect.public_url,
        raw_network_reference="/old",
        status=500,
        final_url="https://datatalks.club/current",
        response_count=2,
        transfer_bytes=0,
        redirect_chain=(
            RedirectHop(301, "https://datatalks.club/middle"),
            RedirectHop(301, "https://datatalks.club/current"),
        ),
        capture_error="redirect_loop",
    )
    bad_retire = TargetObservation(
        requested_url=retire.public_url,
        raw_network_reference="/gone",
        status=200,
        final_url=retire.public_url,
        response_count=1,
        transfer_bytes=0,
    )

    report = evaluate_parity(
        expectations,
        (bad_redirect, bad_retire),
        target=target_binding(),
    )

    assert {
        "redirect_hop_count",
        "redirect_loop",
        "redirect_target_failed",
        "retirement_not_410",
    }.issubset({finding.code for finding in report.findings})


def test_unapproved_checked_input_shape_and_missing_observation_cannot_pass() -> None:
    empty = ApprovedExpectationSet.create(
        manifest_sha256=MANIFEST,
        differences_sha256=DIFFERENCES,
        public_contracts_sha256=CONTRACTS,
    )
    blocked = evaluate_parity(
        empty,
        (),
        target=target_binding(),
        scope=("checked-real",),
    )

    assert blocked.status is ParityStatus.BLOCKED
    assert blocked.complete is False
    assert blocked.expectation_count == 0
    assert [finding.code for finding in blocked.findings] == ["approved_expectations_missing"]


@FIXTURE_SETTINGS
def test_each_requested_scope_requires_an_approved_expectation() -> None:
    collector = DjangoTargetCollector(allowed_hosts={"datatalks.club"})
    observation = collector.observe("https://datatalks.club/docs/Exact/")
    expectation = ApprovedExpectation.preserve(
        source_scope="docs",
        public_url=observation.public_url,
        expected_response=expected(observation),
    )
    expectations = ApprovedExpectationSet.create(
        manifest_sha256=MANIFEST,
        differences_sha256=DIFFERENCES,
        public_contracts_sha256=CONTRACTS,
        expectations=(expectation,),
    )

    report = evaluate_parity(
        expectations,
        (observation,),
        target=target_binding(),
        scope=("docs", "missing-adapter"),
    )

    assert report.status is ParityStatus.BLOCKED
    assert report.complete is False
    assert [(finding.scope, finding.code) for finding in report.findings] == [
        ("missing-adapter", "approved_expectations_missing")
    ]

    with pytest.raises(ValueError, match="parity_scope_must_not_be_empty"):
        evaluate_parity(
            expectations,
            (observation,),
            target=target_binding(),
            scope=(),
        )
