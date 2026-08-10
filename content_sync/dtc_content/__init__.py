"""Deterministic adapter for the public DataTalksClub/content repository."""

from .adapter import (
    CandidateBundle,
    DtcContentDiagnostic,
    DtcContentValidationError,
    adapt_dtc_content_checkout,
)
from .contract import DTC_CONTENT_CONTRACT, DtcContentAdapterContract
from .parity import ProjectionParityEvidence, verify_initial_projection_parity
from .preparation import PreparedCandidateResult, prepare_dtc_content_candidate
from .repository import VerifiedCheckout, verify_dtc_content_checkout

__all__ = [
    "DTC_CONTENT_CONTRACT",
    "CandidateBundle",
    "DtcContentAdapterContract",
    "DtcContentDiagnostic",
    "DtcContentValidationError",
    "PreparedCandidateResult",
    "ProjectionParityEvidence",
    "VerifiedCheckout",
    "adapt_dtc_content_checkout",
    "prepare_dtc_content_candidate",
    "verify_dtc_content_checkout",
    "verify_initial_projection_parity",
]
