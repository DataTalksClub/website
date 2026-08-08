from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from compatibility.contracts import (
    DEFAULT_CONTRACT_DIRECTORY,
    PublicContract,
    load_public_contract_inventory,
)
from compatibility.models import ReviewState

from .models import PUBLIC_CONTRACT_DIGEST

CONTENT_SOURCE_IDS = frozenset({"dtc-main-site", "dtc-docs", "dtc-faq", "dtc-podwiki"})


class ContentInventoryError(ValueError):
    """The checked route inventory cannot safely seed content provenance."""


def checked_public_contract_artifact_sha256(
    directory: Path = DEFAULT_CONTRACT_DIRECTORY,
) -> str:
    return sha256((directory / "public-contracts.jsonl").read_bytes()).hexdigest()


def content_route_contracts(
    directory: Path = DEFAULT_CONTRACT_DIRECTORY,
) -> tuple[PublicContract, ...]:
    """Return exact base-path contracts owned by the four GitHub content sources.

    The compatibility loader remains the only schema decoder. Query and fragment contracts are
    evidence for the same route, not additional database route identities.
    """

    artifact_digest = checked_public_contract_artifact_sha256(directory)
    if directory == DEFAULT_CONTRACT_DIRECTORY and artifact_digest != PUBLIC_CONTRACT_DIGEST:
        raise ContentInventoryError("checked public contract artifact digest changed")
    contracts = load_public_contract_inventory(directory)
    result = tuple(
        contract
        for contract in contracts
        if contract.source_id in CONTENT_SOURCE_IDS
        and not contract.query
        and not contract.fragment
        and contract.review_state is ReviewState.PROPOSED_PRESERVE
        and urlsplit(contract.percent_encoded_public_reference).path
        == contract.percent_encoded_public_reference
    )
    paths = [contract.percent_encoded_public_reference for contract in result]
    if len(paths) != len(set(paths)):
        raise ContentInventoryError("content base paths collide across source inventories")
    return result
