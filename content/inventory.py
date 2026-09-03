from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .models import PUBLIC_CONTRACT_DIGEST
from .route_contracts import (
    DEFAULT_CONTRACT_DIRECTORY,
    PublicContract,
    ReviewState,
    load_public_contract_inventory,
    public_contract_inventory_sha256,
)

CONTENT_SOURCE_IDS = frozenset({"dtc-main-site", "dtc-docs", "dtc-faq", "dtc-podwiki"})


class ContentInventoryError(ValueError):
    """The checked route inventory cannot safely seed content provenance."""


def checked_public_contract_inventory_sha256(
    directory: Path = DEFAULT_CONTRACT_DIRECTORY,
) -> str:
    """Digest the route-contract inventory derived from the pinned source artifacts.

    The digest is taken over the canonical serialization rather than a second checked-in copy
    of the same rows, so the pinned value keeps meaning without storing derived bytes.
    """

    return public_contract_inventory_sha256(load_public_contract_inventory(directory))


def content_route_contracts(
    directory: Path = DEFAULT_CONTRACT_DIRECTORY,
) -> tuple[PublicContract, ...]:
    """Return exact base-path contracts owned by the four GitHub content sources.

    The route-contract loader remains the only schema decoder. Query and fragment contracts are
    evidence for the same route, not additional database route identities.
    """

    contracts = load_public_contract_inventory(directory)
    if (
        directory == DEFAULT_CONTRACT_DIRECTORY
        and public_contract_inventory_sha256(contracts) != PUBLIC_CONTRACT_DIGEST
    ):
        raise ContentInventoryError("checked public contract inventory digest changed")
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
