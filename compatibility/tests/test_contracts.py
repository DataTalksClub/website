from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

import pytest

from compatibility.contracts import (
    ContractClassification,
    ContractInventoryError,
    ReviewState,
    canonical_percent_encoded_reference,
    dumps_public_contract_inventory,
    load_public_contract_inventory,
    public_contract_id,
    public_contract_inventory_sha256,
)
from compatibility.schema import RecordSchemaError, load_schema, validate_jsonl_records

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ARTIFACT = REPOSITORY_ROOT / "_docs/compatibility/public-contracts.jsonl"
CONTRACT_SCHEMA = REPOSITORY_ROOT / "_docs/compatibility/public-contracts.schema.json"
CONTRACT_DIGEST = "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"


def test_inventory_reconciles_every_source_artifact_exactly() -> None:
    contracts = load_public_contract_inventory()

    assert len(contracts) == 5_533
    assert Counter(contract.source_id for contract in contracts) == {
        "dtc-main-site": 2_301,
        "dtc-docs": 175,
        "dtc-faq": 1_553,
        "dtc-podwiki": 1_388,
        "dtc-course-platform": 116,
    }
    assert Counter(contract.contract_kind for contract in contracts) == {
        "api": 30,
        "asset": 1_510,
        "calendar": 2,
        "fragment": 2_474,
        "html": 1_301,
        "json": 14,
        "path": 3,
        "query": 4,
        "text": 188,
        "xml": 7,
    }
    assert sum(contract.machine_contract for contract in contracts) == 50
    assert sum(contract.route_pattern is not None for contract in contracts) == 115
    assert sum(contract.expected_status is None for contract in contracts) == 123
    assert {contract.classification for contract in contracts} == {ContractClassification.PRESERVE}
    assert {contract.review_state for contract in contracts} == {ReviewState.PROPOSED_PRESERVE}
    assert len({contract.contract_id for contract in contracts}) == len(contracts)
    assert len({contract.public_url for contract in contracts}) == len(contracts)


def test_inventory_preserves_exact_queries_fragments_and_provenance() -> None:
    contracts = load_public_contract_inventory()
    by_reference = {contract.public_reference: contract for contract in contracts}

    query = by_reference["/podwiki/search/?q=machine+learning&document_type=section"]
    assert query.public_url == (
        "https://datatalks.club/podwiki/search/?q=machine+learning&document_type=section"
    )
    assert query.path == "/podwiki/search/"
    assert query.query == "q=machine+learning&document_type=section"
    assert query.fragment == ""
    assert query.machine_contract is True
    assert query.source_path == "_site/search/index.html"

    graph = by_reference["/podwiki/graph/#topic%3Allms"]
    assert graph.fragment == "topic%3Allms"
    assert graph.percent_encoded_public_reference == "/podwiki/graph/#topic%3Allms"

    faq = by_reference["/faq/ai-dev-tools-zoomcamp.html#4487db3924"]
    assert faq.expected_status == 200
    assert faq.source_repository == "https://github.com/DataTalksClub/faq.git"
    assert faq.source_revision == "c8da1deea9e24945922702994de101dd90a5380a"
    assert faq.source_path == (
        "_questions/ai-dev-tools-zoomcamp/general/"
        "001_4487db3924_how-do-i-access-the-course-modules-and-materials.md"
    )

    encoded_graph_contract = next(
        contract
        for contract in contracts
        if contract.public_reference == "/podwiki/graph/#book:20201214-ml-bookcamp"
    )
    assert encoded_graph_contract.percent_encoded_public_reference == (
        "/podwiki/graph/#book%3A20201214-ml-bookcamp"
    )
    assert encoded_graph_contract.expected_status == 200

    legacy_space = by_reference["/images/authors/ aashishnair.jpg"]
    assert legacy_space.path == "/images/authors/ aashishnair.jpg"
    assert legacy_space.percent_encoded_public_reference == ("/images/authors/%20aashishnair.jpg")
    assert legacy_space.public_url == ("https://datatalks.club/images/authors/%20aashishnair.jpg")
    assert not any(character.isspace() for character in legacy_space.public_url)


def test_canonical_network_reference_preserves_existing_percent_escape_case() -> None:
    reference = "/case%2fexact#topic%3allms"

    assert canonical_percent_encoded_reference(reference) == reference


def test_course_examples_are_route_contracts_and_machine_samples_are_deduplicated() -> None:
    contracts = load_public_contract_inventory()
    openapi = [
        contract
        for contract in contracts
        if contract.public_url == "https://courses.datatalks.club/api/openapi.json"
    ]

    assert len(openapi) == 1
    assert openapi[0].machine_contract is True
    assert openapi[0].expected_status is None
    assert openapi[0].route_pattern == "/api/openapi.json"
    assert openapi[0].route_name == "api_openapi_json"
    assert openapi[0].route_urlconf == "api.urls"
    assert openapi[0].source_path == "api/urls.py"
    assert openapi[0].source_locator == "api/urls.py:/api/openapi.json"
    assert openapi[0].percent_encoded_public_reference == "/api/openapi.json"

    generated_machine_contract = next(
        contract
        for contract in contracts
        if contract.public_url == "https://datatalks.club/articles.html"
    )
    assert generated_machine_contract.machine_contract is True
    assert generated_machine_contract.contract_kind == "html"
    assert generated_machine_contract.expected_status == 200

    absent_source_contract = next(
        contract
        for contract in contracts
        if contract.public_url == "https://datatalks.club/docs/robots.txt"
    )
    assert absent_source_contract.source_path is None
    assert absent_source_contract.source_locator == ("configured-machine-contract:/docs/robots.txt")
    assert absent_source_contract.expected_status is None


def test_course_contract_ids_are_stable_when_example_paths_collide() -> None:
    arguments = {
        "source_id": "dtc-course-platform",
        "public_reference": "/courses/example/",
        "route_name": "course_detail",
        "route_urlconf": "courses.urls",
        "route_callback": "courses.views.detail",
    }
    first = public_contract_id(**arguments, route_pattern="/courses/<slug:course_slug>/")
    repeated = public_contract_id(**arguments, route_pattern="/courses/<slug:course_slug>/")
    second = public_contract_id(
        **arguments,
        route_pattern="/courses/<str:legacy_course_id>/",
    )

    assert first == repeated == "contract-5d73e49581a473b6f2d68495"
    assert second == "contract-13e7d9defe6d3f609860aaf8"
    assert first != second


def test_inventory_serialization_is_canonical_and_repeatable() -> None:
    first_inventory = load_public_contract_inventory()
    second_inventory = load_public_contract_inventory()
    first = dumps_public_contract_inventory(first_inventory)
    second = dumps_public_contract_inventory(second_inventory)

    assert first == second
    assert first.endswith("\n")
    assert len(first.splitlines()) == 5_533
    assert public_contract_inventory_sha256(first_inventory) == CONTRACT_DIGEST
    records = [json.loads(line) for line in first.splitlines()]
    assert [record["public_url"] for record in records] == sorted(
        record["public_url"] for record in records
    )
    assert {record["review_state"] for record in records} == {"proposed_preserve"}
    assert {record["schema_version"] for record in records} == {1}


def test_public_contract_is_frozen_and_rejects_implicit_review_state() -> None:
    contract = load_public_contract_inventory()[0]

    with pytest.raises(FrozenInstanceError):
        contract.public_url = "https://example.com/"  # type: ignore[misc]
    with pytest.raises(ContractInventoryError, match="review state must be explicit"):
        replace(contract, review_state="approved_parity")  # type: ignore[arg-type]
    with pytest.raises(ContractInventoryError, match="classification must be explicit"):
        replace(contract, classification="preserve")  # type: ignore[arg-type]
    with pytest.raises(ContractInventoryError, match="HTTP status"):
        replace(contract, expected_status=True)  # type: ignore[arg-type]
    with pytest.raises(ContractInventoryError, match="only supports a proposed preserve"):
        replace(contract, review_state=ReviewState.APPROVED_PARITY)
    with pytest.raises(ContractInventoryError, match="only supports a proposed preserve"):
        replace(
            contract,
            classification=ContractClassification.REDIRECT,
            review_state=ReviewState.APPROVED_EXCEPTION,
        )


def test_public_contract_rejects_unsafe_network_references_and_sensitive_values() -> None:
    contract = load_public_contract_inventory()[0]

    with pytest.raises(ContractInventoryError, match="absolute HTTPS URL"):
        replace(contract, public_url="https://user:password@datatalks.club/articles.html")
    with pytest.raises(ContractInventoryError, match="absolute HTTPS URL"):
        replace(contract, public_url="https://datatalks.club/articles bad.html")
    with pytest.raises(ContractInventoryError, match="canonical public reference encoding"):
        replace(contract, percent_encoded_public_reference="/not-the-reference")

    sensitive_query = "/articles.html?email=person@example.com"
    with pytest.raises(ContractInventoryError, match="sensitive query value"):
        replace(
            contract,
            public_url=f"https://datatalks.club{sensitive_query}",
            public_reference=sensitive_query,
            percent_encoded_public_reference=sensitive_query,
            path="/articles.html",
            query="email=person@example.com",
            fragment="",
        )

    sensitive_fragment = "/articles.html#person@example.com"
    with pytest.raises(ContractInventoryError, match="sensitive fragment value"):
        replace(
            contract,
            public_url="https://datatalks.club/articles.html#person%40example.com",
            public_reference=sensitive_fragment,
            percent_encoded_public_reference="/articles.html#person%40example.com",
            path="/articles.html",
            query="",
            fragment="person@example.com",
        )

    query_shaped_fragment = "/articles.html#access_token=supersecret"
    encoded_query_shaped_fragment = "/articles.html#access_token%3Dsupersecret"
    with pytest.raises(ContractInventoryError, match="sensitive fragment value"):
        replace(
            contract,
            public_url=f"https://datatalks.club{encoded_query_shaped_fragment}",
            public_reference=query_shaped_fragment,
            percent_encoded_public_reference=encoded_query_shaped_fragment,
            path="/articles.html",
            query="",
            fragment="access_token=supersecret",
        )

    with pytest.raises(ContractInventoryError, match="root-relative"):
        replace(
            contract,
            public_url="https://datatalks.club/path",
            public_reference="//user:password@datatalks.club/path",
            percent_encoded_public_reference="/path",
            path="/path",
        )


def test_public_contract_preserves_artifact_encoding_for_literal_percent_filename() -> None:
    contract = load_public_contract_inventory()[0]
    reference = "/assets/literal%2fname.json"
    encoded_reference = "/assets/literal%252fname.json"

    literal_percent = replace(
        contract,
        contract_id=public_contract_id(contract.source_id, reference),
        public_url=f"https://datatalks.club{encoded_reference}",
        public_reference=reference,
        percent_encoded_public_reference=encoded_reference,
        path=reference,
        query="",
        fragment="",
        source_locator="assets/literal%2fname.json",
        source_path="assets/literal%2fname.json",
        contract_kind="asset",
    )

    assert literal_percent.percent_encoded_public_reference == encoded_reference
    assert canonical_percent_encoded_reference(reference) == reference


def test_checked_in_public_contract_artifact_is_canonical_and_matches_its_schema() -> None:
    expected = dumps_public_contract_inventory(load_public_contract_inventory())
    artifact = CONTRACT_ARTIFACT.read_text(encoding="utf-8")
    schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    first_record = json.loads(artifact.splitlines()[0])

    assert artifact == expected
    assert sha256(artifact.encode()).hexdigest() == CONTRACT_DIGEST
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "https://datatalks.club/schemas/public-contract-v1.json"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(first_record)
    assert schema["properties"]["classification"]["const"] == "preserve"
    assert schema["properties"]["review_state"]["const"] == "proposed_preserve"
    assert schema["properties"]["percent_encoded_public_reference"]["type"] == "string"
    assert validate_jsonl_records(artifact, load_schema(CONTRACT_SCHEMA)) == 5_533


def test_public_contract_schema_rejects_unsafe_or_contradictory_artifact_rows() -> None:
    schema = load_schema(CONTRACT_SCHEMA)
    record = load_public_contract_inventory()[0].as_record()

    for key, value in (
        ("public_url", "https://user:password@datatalks.club/articles.html"),
        ("public_url", "https://datatalks.club/articles bad.html"),
        ("percent_encoded_public_reference", None),
        ("classification", "redirect"),
        ("review_state", "approved_parity"),
    ):
        changed = {**record, key: value}
        with pytest.raises(RecordSchemaError):
            validate_jsonl_records(
                json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n",
                schema,
            )


def test_public_contract_cli_regenerates_and_detects_stale_repo_scoped_output() -> None:
    relative_output = Path(f".tmp/compatibility/public-contracts-test-{os.getpid()}.jsonl")
    output = REPOSITORY_ROOT / relative_output
    command = [
        sys.executable,
        "scripts/build_legacy_manifest.py",
        "public-contracts",
        "--output",
        str(relative_output),
    ]
    output.unlink(missing_ok=True)
    try:
        generated = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert generated.returncode == 0, generated.stderr
        assert output.read_bytes() == CONTRACT_ARTIFACT.read_bytes()

        checked = subprocess.run(
            [*command, "--check"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stderr
        assert f"5533 rows sha256={CONTRACT_DIGEST}" in checked.stdout

        output.write_text("{}\n", encoding="utf-8")
        stale = subprocess.run(
            [*command, "--check"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert stale.returncode == 2
        assert "artifact is stale" in stale.stderr
    finally:
        output.unlink(missing_ok=True)


def test_public_contract_cli_rejects_output_outside_repository() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_legacy_manifest.py",
            "public-contracts",
            "--output",
            "/outside-public-contracts.jsonl",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must be below the project root" in result.stderr
