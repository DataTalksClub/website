from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from compatibility.approved_targets import slack_target_expectation_set
from compatibility.contracts import ContractClassification, load_public_contract_inventory
from compatibility.diff import diff_source_production, dumps_differences
from compatibility.expectations import QueryPolicy, dumps_expectations, loads_expectations
from compatibility.models import (
    ClassificationKind,
    ReviewState,
    dumps_jsonl,
    loads_jsonl,
)
from compatibility.schema import load_schema, validate_jsonl_records, validate_record
from scripts.build_legacy_manifest import (
    DEFAULT_BASELINE,
    DEFAULT_COURSE_ROUTES,
    _policy,
    _provenance,
    _seed_urls,
    _source_urls,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "_docs/compatibility"
MANIFEST_PATH = CONTRACT_ROOT / "legacy-manifest.jsonl"
MANIFEST_SCHEMA_PATH = CONTRACT_ROOT / "legacy-manifest.schema.json"
DIFFERENCE_PATH = CONTRACT_ROOT / "legacy-manifest-differences.json"
EXPECTATIONS_PATH = CONTRACT_ROOT / "approved-expectations.json"
DIFFERENCE_SCHEMA_PATH = CONTRACT_ROOT / "legacy-manifest-differences.schema.json"
MANIFEST_SHA256 = "9c6bb79eb8484aa2a0befaf6b2531e5ad05f2f5fe6bfe6ef711c66cdb6cdca32"
DIFFERENCE_SHA256 = "10fdac5be5662686da0fbae4b8ee0b1fe20007d8e6e03349fd95f31ea7ac1fe6"
EXPECTATIONS_SHA256 = "410dbce285ba19d18e947b5ed039fac1ce553731267ef5631bcf94993d8b2220"
DOCS_REPOSITORY = "https://github.com/DataTalksClub/docs.git"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_checked_manifest_and_difference_artifacts_are_exact_and_reproducible() -> None:
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest_text = manifest_bytes.decode("utf-8")
    difference_bytes = DIFFERENCE_PATH.read_bytes()
    difference_text = difference_bytes.decode("utf-8")

    assert _sha256(manifest_bytes) == MANIFEST_SHA256
    assert _sha256(difference_bytes) == DIFFERENCE_SHA256
    assert validate_jsonl_records(manifest_text, load_schema(MANIFEST_SCHEMA_PATH)) == 2_966

    provenance, rows = loads_jsonl(manifest_text)
    assert provenance == _provenance(provenance.generated_at, _policy())
    assert dumps_jsonl(provenance, rows) == manifest_text
    assert len(rows) == 2_965

    difference_document = json.loads(difference_text)
    validate_record(difference_document, load_schema(DIFFERENCE_SCHEMA_PATH))
    assert dumps_differences(diff_source_production(rows)) == difference_text
    assert len(difference_document["differences"]) == 4_918
    assert Counter(item["kind"] for item in difference_document["differences"]) == {
        "asset_added": 17,
        "asset_removed": 19,
        "canonical_changed": 3,
        "field_changed": 4_805,
        "fragment_added": 21,
        "fragment_removed": 21,
        "route_added": 28,
        "route_removed": 4,
    }


def test_slack_target_expectation_is_a_redirect_separate_from_source_provenance() -> None:
    manifest_digest = _sha256(MANIFEST_PATH.read_bytes())
    difference_digest = _sha256(DIFFERENCE_PATH.read_bytes())
    contracts_path = CONTRACT_ROOT / "public-contracts.jsonl"
    contracts_digest = _sha256(contracts_path.read_bytes())
    artifact = EXPECTATIONS_PATH.read_text(encoding="utf-8")

    assert _sha256(artifact.encode()) == EXPECTATIONS_SHA256
    expectations = loads_expectations(artifact)
    expected = slack_target_expectation_set(
        manifest_sha256=manifest_digest,
        differences_sha256=difference_digest,
        public_contracts_sha256=contracts_digest,
    )
    assert expectations == expected
    assert dumps_expectations(expectations) == artifact
    assert len(expectations.expectations) == 1
    redirect = expectations.expectations[0]
    assert redirect.public_url == "https://datatalks.club/slack.html"
    assert redirect.redirect_status == 301
    assert redirect.redirect_target == "https://datatalks.club/slack"
    assert redirect.query_policy is QueryPolicy.PRESERVE_RAW

    source_contract = next(
        contract
        for contract in load_public_contract_inventory()
        if contract.public_reference == "/slack.html"
    )
    assert source_contract.classification is ContractClassification.PRESERVE
    assert source_contract.expected_status == 200
    assert source_contract.source_path == "_site/slack.html"


def test_checked_manifest_covers_the_exact_source_and_production_seed_sets() -> None:
    _provenance, rows = loads_jsonl(MANIFEST_PATH.read_text(encoding="utf-8"))
    source_urls = {row.public_url for row in rows if row.source_capture is not None}
    production_urls = {row.public_url for row in rows if row.production_capture is not None}

    assert source_urls == set(_source_urls(DEFAULT_BASELINE))
    assert production_urls == set(_seed_urls(DEFAULT_BASELINE, DEFAULT_COURSE_ROUTES))
    assert len(source_urls) == 2_937
    assert len(production_urls) == 2_965
    assert len(source_urls & production_urls) == 2_937
    assert len(production_urls - source_urls) == 28
    assert not source_urls - production_urls
    assert {row.classification.kind for row in rows} == {ClassificationKind.PRESERVE}
    assert {row.classification.review_state for row in rows} == {ReviewState.PROPOSED_PRESERVE}


def test_checked_production_capture_preserves_transport_accounting_and_observations() -> None:
    _provenance, rows = loads_jsonl(MANIFEST_PATH.read_text(encoding="utf-8"))
    captures = [row.production_capture for row in rows]
    assert all(capture is not None for capture in captures)
    production = [capture for capture in captures if capture is not None]

    assert Counter(capture.status for capture in production) == {
        200: 2_951,
        401: 4,
        403: 2,
        404: 7,
        405: 1,
    }
    assert not [capture for capture in production if capture.error_code]
    assert sum(capture.response_count for capture in production) == 2_976
    assert sum(capture.transfer_bytes for capture in production) == 258_215_736
    assert sum(bool(capture.redirect_chain) for capture in production) == 11
    assert sum(bool(capture.metadata.client_redirect_url) for capture in production) == 2
    assert sum(capture.metadata.soft_404 for capture in production) == 9


def test_parameterized_course_routes_remain_unprobed_and_outside_the_seed_set() -> None:
    document = json.loads(DEFAULT_COURSE_ROUTES.read_text(encoding="utf-8"))
    parameterized = [route for route in document["routes"] if "<" in route["route_pattern"]]
    production_urls = set(_seed_urls(DEFAULT_BASELINE, DEFAULT_COURSE_ROUTES))

    assert len(parameterized) == 68
    assert {route["authenticated_production_probe"] for route in parameterized} == {"not_performed"}
    assert (
        not {f"https://courses.datatalks.club{route['example_path']}" for route in parameterized}
        & production_urls
    )


def test_checked_docs_html_has_complete_deterministic_page_metadata() -> None:
    _provenance, rows = loads_jsonl(MANIFEST_PATH.read_text(encoding="utf-8"))
    docs_html = [
        capture
        for row in rows
        if (capture := row.source_capture) is not None
        and capture.source_repository == DOCS_REPOSITORY
        and capture.content_type == "text/html"
    ]

    assert len(docs_html) == 107
    assert all(capture.metadata.title for capture in docs_html)
    assert all(capture.metadata.first_heading for capture in docs_html)
    assert all(capture.metadata.main_content_fingerprint for capture in docs_html)

    brand = next(
        capture
        for capture in docs_html
        if capture.requested_url == "https://datatalks.club/docs/general/brand-assets/"
    )
    assert brand.metadata.title == "Brand Assets | DataTalks.Club Documentation"
    assert brand.metadata.first_heading == "Brand Assets"
    assert brand.metadata.main_content_fingerprint == (
        "5aabcda9884baf3db8892eab5adefe74c9c579f0b8e10728ee80c4bbb78373aa"
    )
