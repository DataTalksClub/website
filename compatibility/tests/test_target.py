from __future__ import annotations

import hashlib
import json
from urllib.parse import unquote, urlsplit

import pytest
from django.test import override_settings

from compatibility.django import DjangoTargetCollector, DjangoTargetError
from compatibility.models import PageMetadata
from compatibility.schema import RecordSchemaError, load_schema, validate_record
from compatibility.target import (
    DEFAULT_TARGET_OBSERVATION_SCHEMA,
    TargetObservation,
    TargetObservationError,
    dumps_target_observations,
    loads_target_observations,
)

FIXTURE_SETTINGS = override_settings(
    ROOT_URLCONF="compatibility.tests.fixture_urls",
    ALLOWED_HOSTS=["datatalks.club", "courses.datatalks.club"],
    APPEND_SLASH=False,
    NOINDEX=False,
)


def collector(*, max_redirects: int = 4) -> DjangoTargetCollector:
    return DjangoTargetCollector(
        allowed_hosts={"datatalks.club", "courses.datatalks.club"},
        max_redirects=max_redirects,
    )


@FIXTURE_SETTINGS
def test_collects_server_rendered_metadata_references_and_structured_families() -> None:
    observation = collector().observe("https://datatalks.club/fixture/")

    assert observation.status == 200
    assert observation.raw_network_reference == "/fixture/"
    assert observation.content_type == "text/html"
    assert observation.response_content_language == "en"
    assert observation.metadata.title == "Compatibility fixture"
    assert observation.metadata.description == "Server-rendered parity fixture"
    assert observation.metadata.first_heading == "Compatibility fixture"
    assert observation.metadata.canonical_url == "https://datatalks.club/fixture/"
    assert observation.metadata.main_content_fingerprint
    assert "Café" in observation.metadata.fragments
    assert {item.type for item in observation.metadata.structured_data} == {
        "Answer",
        "BlogPosting",
        "BreadcrumbList",
        "Event",
        "FAQPage",
        "ListItem",
        "Organization",
        "PodcastEpisode",
        "Question",
        "SearchAction",
        "WebSite",
    }
    assert observation.capture_error == ""


@FIXTURE_SETTINGS
def test_follows_one_hop_and_can_retain_the_unfollowed_network_response() -> None:
    followed = collector().observe("https://datatalks.club/legacy")
    direct = collector().observe(
        "https://datatalks.club/legacy",
        follow_redirects=False,
    )

    assert followed.status == 200
    assert followed.final_url == "https://datatalks.club/fixture/"
    assert [(hop.status, hop.url) for hop in followed.redirect_chain] == [
        (301, "https://datatalks.club/fixture/")
    ]
    assert direct.status == 301
    assert direct.final_url == "https://datatalks.club/fixture/"
    assert direct.response_count == 1


@FIXTURE_SETTINGS
def test_redirect_retains_fragment_evidence_but_never_sends_fragment_to_django() -> None:
    observation = collector().observe("https://datatalks.club/fragment-redirect")

    assert observation.status == 200
    assert observation.final_url == "https://datatalks.club/fixture/"
    assert observation.redirect_chain[0].url == "https://datatalks.club/fixture/#Caf%C3%A9"


@FIXTURE_SETTINGS
def test_records_redirect_chain_loop_and_direct_statuses_without_network() -> None:
    chain = collector().observe("https://datatalks.club/chain-a")
    loop = collector().observe("https://datatalks.club/loop-a")
    gone = collector().observe("https://datatalks.club/gone")
    missing = collector().observe("https://datatalks.club/missing")

    assert len(chain.redirect_chain) == 2
    assert chain.status == 200
    assert loop.capture_error == "redirect_loop"
    assert len(loop.redirect_chain) == 2
    assert gone.status == 410
    assert missing.status == 404


@FIXTURE_SETTINGS
@pytest.mark.parametrize(
    "reference",
    [
        "/echo/Case?x=1&x=&q=A+B&q=A%20B",
        "/echo/Caf%C3%A9?encoded=%2f&other=%2F",
        "/echo/e%CC%81?blank=&blank=",
        "/echo/a%2Fb",
    ],
)
def test_preserves_exact_raw_path_and_query_for_django(reference: str) -> None:
    observation = collector().observe(f"https://datatalks.club{reference}")
    parsed = urlsplit(reference)
    decoded_path = unquote(parsed.path)
    expected_payload = {
        "path": decoded_path,
        "path_info": decoded_path,
        "query": parsed.query,
        "raw_uri": reference,
        "request_uri": reference,
        "value": decoded_path.removeprefix("/echo/"),
    }
    canonical = json.dumps(
        expected_payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    # The observation retains the exact network identity independently of WSGI's
    # decoded PATH_INFO; a direct response check proves Django received RAW_URI too.
    assert observation.raw_network_reference == reference
    assert observation.status == 200
    assert (
        observation.metadata.main_content_fingerprint
        == hashlib.sha256(canonical.encode()).hexdigest()
    )


@FIXTURE_SETTINGS
def test_encoded_separator_and_literal_path_remain_distinct_network_contracts() -> None:
    encoded = collector().observe("https://datatalks.club/echo/a%2Fb")
    literal = collector().observe("https://datatalks.club/echo/a/b")

    assert encoded.raw_network_reference == "/echo/a%2Fb"
    assert literal.raw_network_reference == "/echo/a/b"
    assert encoded.raw_network_reference != literal.raw_network_reference
    # Django routes both through decoded PATH_INFO; RAW_URI provenance prevents the
    # gate from silently treating the observations as the same contract.
    assert encoded.metadata.main_content_fingerprint != (literal.metadata.main_content_fingerprint)


def test_rejects_disallowed_hosts_methods_and_private_target_values() -> None:
    with pytest.raises(DjangoTargetError, match="host_is_not_allowed"):
        collector().observe("https://evil.example/path")
    with pytest.raises(DjangoTargetError, match="method_must_be_get_or_head"):
        collector().observe("https://datatalks.club/fixture/", method="POST")

    with pytest.raises(TargetObservationError, match="private_data"):
        TargetObservation(
            requested_url="https://datatalks.club/path?token=secret",
            raw_network_reference="/path?token=secret",
            status=404,
            final_url="https://datatalks.club/path?token=secret",
            response_count=1,
            transfer_bytes=0,
        )


def test_observation_rejects_private_malformed_oversized_and_untyped_values() -> None:
    def unsafe_observation(**overrides: object) -> TargetObservation:
        values: dict[str, object] = {
            "requested_url": "https://datatalks.club/path",
            "raw_network_reference": "/path",
            "status": 200,
            "final_url": "https://datatalks.club/path",
            "response_count": 1,
            "transfer_bytes": 0,
        }
        values.update(overrides)
        return TargetObservation(**values)  # type: ignore[arg-type]

    with pytest.raises(TargetObservationError, match="response_location_contains_credentials"):
        unsafe_observation(response_location="/" + "ghp_" + "a" * 30)
    with pytest.raises(TargetObservationError, match="private_data"):
        unsafe_observation(metadata=PageMetadata(title="github_pat_" + "a" * 30))
    with pytest.raises(TargetObservationError, match="content_type_is_too_long"):
        unsafe_observation(content_type="x" * 256)
    with pytest.raises(TargetObservationError, match="content_type_contains_invalid_unicode"):
        unsafe_observation(content_type="\ud800")
    with pytest.raises(TargetObservationError, match="too_long"):
        unsafe_observation(metadata=PageMetadata(title="x" * 16_385))
    with pytest.raises(TargetObservationError, match="metadata_must_be_page_metadata"):
        unsafe_observation(metadata={})
    with pytest.raises(TargetObservationError, match="body_sha256_must_be_string"):
        unsafe_observation(body_sha256=object())
    with pytest.raises(TargetObservationError, match="malformed_percent_escape"):
        unsafe_observation(
            requested_url="https://datatalks.club/bad%GG",
            raw_network_reference="/bad%GG",
            final_url="https://datatalks.club/bad%GG",
        )
    with pytest.raises(TargetObservationError, match="credential"):
        unsafe_observation(
            requested_url="https://datatalks.club/password=hunter2",
            raw_network_reference="/password=hunter2",
            final_url="https://datatalks.club/password=hunter2",
        )


def test_unicode_path_is_not_misclassified_as_private_and_failed_capture_is_not_coerced() -> None:
    unicode_observation = TargetObservation(
        requested_url="https://datatalks.club/Café",
        raw_network_reference="/Café",
        status=200,
        final_url="https://datatalks.club/Café",
        response_count=1,
        transfer_bytes=0,
    )
    failed = TargetObservation(
        requested_url="https://datatalks.club/loop",
        raw_network_reference="/loop",
        status=301,
        final_url="https://datatalks.club/loop",
        response_count=1,
        transfer_bytes=0,
        capture_error="redirect_loop",
    )

    assert unicode_observation.public_url.endswith("/Café")
    with pytest.raises(TargetObservationError, match="failed_observation"):
        failed.as_capture()


@FIXTURE_SETTINGS
def test_observation_document_is_canonical_strict_and_schema_validated() -> None:
    collected = collector().observe("https://datatalks.club/fixture/")
    encoded = dumps_target_observations((collected,))

    assert loads_target_observations(encoded) == (collected,)
    assert dumps_target_observations(loads_target_observations(encoded)) == encoded

    document = json.loads(encoded)
    document["observations"][0]["metadata"]["unexpected"] = True
    with pytest.raises(RecordSchemaError, match="extra_fields"):
        validate_record(document, load_schema(DEFAULT_TARGET_OBSERVATION_SCHEMA))
    noncanonical = (
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    with pytest.raises(TargetObservationError, match="failed_schema_validation"):
        loads_target_observations(noncanonical)

    arbitrary = json.loads(encoded)
    arbitrary["observations"][0]["metadata"] = {"arbitrary": True}
    arbitrary["observations"][0]["sitemap"] = {"anything": True}
    with pytest.raises(RecordSchemaError):
        validate_record(arbitrary, load_schema(DEFAULT_TARGET_OBSERVATION_SCHEMA))

    oversized = json.loads(encoded)
    oversized["observations"][0]["content_type"] = "x" * 256
    with pytest.raises(RecordSchemaError, match="string_too_long"):
        validate_record(oversized, load_schema(DEFAULT_TARGET_OBSERVATION_SCHEMA))

    escaped_surrogate = json.loads(encoded)
    escaped_surrogate["observations"][0]["content_type"] = "\ud800"
    escaped_text = json.dumps(escaped_surrogate, separators=(",", ":"), sort_keys=True) + "\n"
    with pytest.raises(TargetObservationError, match="invalid"):
        loads_target_observations(escaped_text)

    duplicate = encoded.replace(
        '"record_kind":"target_observation_set"',
        '"record_kind":"target_observation_set","record_kind":"target_observation_set"',
    )
    with pytest.raises(TargetObservationError, match="duplicate_key"):
        loads_target_observations(duplicate)
