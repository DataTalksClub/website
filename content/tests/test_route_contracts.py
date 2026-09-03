from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

from django.test import SimpleTestCase

from content.route_contracts import (
    ContractClassification,
    ContractInventoryError,
    ReviewState,
    canonical_percent_encoded_reference,
    dumps_public_contract_inventory,
    load_public_contract_inventory,
    public_contract_id,
    public_contract_inventory_sha256,
)

CONTRACT_DIGEST = "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"


class PublicRouteContractTests(SimpleTestCase):
    def test_inventory_classifications_and_identities_are_exact(self) -> None:
        contracts = load_public_contract_inventory()

        assert {contract.classification for contract in contracts} == {
            ContractClassification.PRESERVE
        }
        assert {contract.review_state for contract in contracts} == {ReviewState.PROPOSED_PRESERVE}
        assert len({contract.contract_id for contract in contracts}) == len(contracts)
        assert len({contract.public_url for contract in contracts}) == len(contracts)

    def test_inventory_preserves_exact_queries_fragments_and_provenance(self) -> None:
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
        assert legacy_space.percent_encoded_public_reference == "/images/authors/%20aashishnair.jpg"
        assert legacy_space.public_url == (
            "https://datatalks.club/images/authors/%20aashishnair.jpg"
        )
        assert not any(character.isspace() for character in legacy_space.public_url)

    def test_canonical_network_reference_preserves_existing_percent_escape_case(self) -> None:
        reference = "/case%2fexact#topic%3allms"

        assert canonical_percent_encoded_reference(reference) == reference

    def test_course_examples_are_route_contracts_and_machine_samples_are_deduplicated(self) -> None:
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
        assert absent_source_contract.source_locator == (
            "configured-machine-contract:/docs/robots.txt"
        )
        assert absent_source_contract.expected_status is None

    def test_course_contract_ids_are_stable_when_example_paths_collide(self) -> None:
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

    def test_inventory_serialization_is_canonical_and_repeatable(self) -> None:
        first_inventory = load_public_contract_inventory()
        second_inventory = load_public_contract_inventory()
        first = dumps_public_contract_inventory(first_inventory)
        second = dumps_public_contract_inventory(second_inventory)

        assert first == second
        assert first.endswith("\n")
        assert public_contract_inventory_sha256(first_inventory) == CONTRACT_DIGEST
        records = [json.loads(line) for line in first.splitlines()]
        assert [record["public_url"] for record in records] == sorted(
            record["public_url"] for record in records
        )
        assert {record["review_state"] for record in records} == {"proposed_preserve"}
        assert {record["schema_version"] for record in records} == {1}

    def test_public_contract_is_frozen_and_rejects_implicit_review_state(self) -> None:
        contract = load_public_contract_inventory()[0]

        with self.assertRaises(FrozenInstanceError):
            contract.public_url = "https://example.com/"  # type: ignore[misc]
        with self.assertRaisesRegex(ContractInventoryError, "review state must be explicit"):
            replace(contract, review_state="approved_parity")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ContractInventoryError, "classification must be explicit"):
            replace(contract, classification="preserve")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ContractInventoryError, "HTTP status"):
            replace(contract, expected_status=True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ContractInventoryError, "only supports a proposed preserve"):
            replace(contract, review_state=ReviewState.APPROVED_PARITY)
        with self.assertRaisesRegex(ContractInventoryError, "only supports a proposed preserve"):
            replace(
                contract,
                classification=ContractClassification.REDIRECT,
                review_state=ReviewState.APPROVED_EXCEPTION,
            )

    def test_public_contract_rejects_unsafe_network_references_and_sensitive_values(self) -> None:
        contract = load_public_contract_inventory()[0]

        with self.assertRaisesRegex(ContractInventoryError, "absolute HTTPS URL"):
            replace(contract, public_url="https://user:password@datatalks.club/articles.html")
        with self.assertRaisesRegex(ContractInventoryError, "absolute HTTPS URL"):
            replace(contract, public_url="https://datatalks.club/articles bad.html")
        with self.assertRaisesRegex(ContractInventoryError, "canonical public reference encoding"):
            replace(contract, percent_encoded_public_reference="/not-the-reference")

        sensitive_query = "/articles.html?email=person@example.com"
        with self.assertRaisesRegex(ContractInventoryError, "sensitive query value"):
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
        with self.assertRaisesRegex(ContractInventoryError, "sensitive fragment value"):
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
        with self.assertRaisesRegex(ContractInventoryError, "sensitive fragment value"):
            replace(
                contract,
                public_url=f"https://datatalks.club{encoded_query_shaped_fragment}",
                public_reference=query_shaped_fragment,
                percent_encoded_public_reference=encoded_query_shaped_fragment,
                path="/articles.html",
                query="",
                fragment="access_token=supersecret",
            )

        with self.assertRaisesRegex(ContractInventoryError, "root-relative"):
            replace(
                contract,
                public_url="https://datatalks.club/path",
                public_reference="//user:password@datatalks.club/path",
                percent_encoded_public_reference="/path",
                path="/path",
            )

    def test_public_contract_preserves_artifact_encoding_for_literal_percent_filename(self) -> None:
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
