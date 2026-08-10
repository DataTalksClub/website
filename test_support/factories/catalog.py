from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import FactoryContext, canonical_json_bytes, canonical_sha256

SCENARIO_STATES = (
    "minimal_valid",
    "complete_valid",
    "boundary_valid",
    "invalid_rejected",
    "stale_conflict",
    "privacy_redaction",
)

BUNDLE_LEAVES: dict[str, tuple[str, ...]] = {
    "accounts_management": (
        "custom_user",
        "staff_role",
        "staff_group",
        "staff_permission",
        "account_identity_alias",
        "account_identity_quarantine",
        "account_reconciliation_run",
        "management_principal",
        "management_credential",
        "staff_session",
        "one_time_token_representation",
    ),
    "editorial_content": (
        "content_source",
        "content_release",
        "active_content_path",
        "article_document",
        "podcast_document",
        "transcript_document",
        "book_document",
        "person_document",
        "public_course_document",
        "public_event_document",
        "faq_document",
        "docs_document",
        "wiki_document",
        "content_relation",
        "content_asset",
        "frozen_release_child",
    ),
    "adopted_courses": (
        "course",
        "registration_campaign",
        "course_registration",
        "enrollment",
        "homework",
        "question",
        "answer",
        "submission",
        "homework_statistics",
        "project",
        "project_submission",
        "project_vote",
        "review_criteria",
        "peer_review",
        "criteria_response",
        "project_evaluation_score",
        "project_statistics",
        "leaderboard_complaint",
        "wrapped_statistics",
        "user_wrapped_statistics",
    ),
    "historical_event_totals": (
        "historical_source_run",
        "historical_event_mapping",
        "historical_aggregate_revision",
        "historical_aggregate_slot",
        "historical_pointer_displacement",
        "historical_total_state",
        "mapping_conflict",
        "source_missing_quarantine",
        "aggregate_activation",
        "aggregate_rollback",
        "aggregate_to_native_boundary",
    ),
    "operations_jobs": (
        "audit_event",
        "operational_setting",
        "operational_setting_revision",
        "idempotency_record",
        "operation",
        "operation_revision_conflict",
        "durable_job",
        "job_lease",
        "job_result",
        "worker_heartbeat",
        "scheduler_lease",
    ),
    "provider_neutral_messaging": (
        "captured_message",
        "outbox_intent",
        "delivery_intent",
        "delivery_attempt",
        "provider_event",
        "suppression",
        "retry_result",
        "ambiguous_result",
        "simulator_response",
    ),
}


@dataclass(frozen=True, slots=True)
class FactorySpec:
    factory: str
    state: str
    logical_id: str
    physical_id: str
    values: dict[str, Any]

    def logical_payload(self) -> dict[str, Any]:
        return {
            "factory": self.factory,
            "logical_id": self.logical_id,
            "state": self.state,
            "values": self.values,
        }


@dataclass(frozen=True, slots=True)
class ScenarioBundle:
    name: str
    records: tuple[FactorySpec, ...]

    def logical_payload(self) -> dict[str, Any]:
        return {
            "bundle": self.name,
            "records": sorted(
                (record.logical_payload() for record in self.records),
                key=lambda item: (item["factory"], item["state"], item["logical_id"]),
            ),
        }

    def canonical_json(self) -> bytes:
        return canonical_json_bytes(self.logical_payload())

    def sha256(self) -> str:
        return canonical_sha256(self.logical_payload())


@dataclass(frozen=True, slots=True)
class LeafFactory:
    """A stable named leaf entry point in the shared deterministic catalog."""

    bundle: str
    leaf: str

    @property
    def name(self) -> str:
        return f"{self.bundle}.{self.leaf}"

    def __call__(
        self,
        context: FactoryContext,
        *,
        state: str = "minimal_valid",
    ) -> FactorySpec:
        if state not in SCENARIO_STATES:
            raise ValueError(f"unknown factory scenario state: {state}")
        return _record(context, self.bundle, self.leaf, state)

    def create(
        self,
        context: FactoryContext,
        *,
        state: str = "minimal_valid",
    ):
        """Persist this leaf through the composed current-domain scenario builder."""

        from .current_domain import create_current_leaf

        return create_current_leaf(
            context,
            bundle=self.bundle,
            leaf=self.leaf,
            state=state,
        )


LEAF_FACTORIES = {
    f"{bundle}.{leaf}": LeafFactory(bundle, leaf)
    for bundle, leaves in BUNDLE_LEAVES.items()
    for leaf in leaves
}


def _record(context: FactoryContext, bundle: str, leaf: str, state: str) -> FactorySpec:
    factory = f"{bundle}.{leaf}"
    logical_key = state
    values: dict[str, Any] = {
        "boundary": state == "boundary_valid",
        "conflict": state == "stale_conflict",
        "frozen_at": context.frozen_at,
        "label": f"Synthetic {leaf.replace('_', ' ')}",
        "rejected": state == "invalid_rejected",
        "redacted": state == "privacy_redaction",
        "seed_reference": canonical_sha256({"seed": context.seed})[:16],
        "url": f"https://{leaf.replace('_', '-')}.example.invalid/resource",
    }
    if bundle == "accounts_management":
        values["email"] = (
            "[REDACTED]" if state == "privacy_redaction" else "synthetic@example.invalid"
        )
        values["public_person_link"] = None
    elif bundle == "adopted_courses":
        values.update(
            {
                "campaign_is_cohort": False,
                "course_identity": "synthetic-course",
                "enrollment_identity": "synthetic-enrollment",
                "registration_identity": "synthetic-registration",
            }
        )
    elif bundle == "historical_event_totals":
        values.update(
            {
                "aggregate_only": True,
                "eligible_count": 3,
                "registration_rows": [],
            }
        )
    elif bundle == "provider_neutral_messaging":
        values.update(
            {
                "implementation": "in_memory_simulator",
                "provider_client": None,
                "recipient": "[REDACTED]"
                if state == "privacy_redaction"
                else "synthetic-recipient@example.invalid",
            }
        )
    return FactorySpec(
        factory=factory,
        state=state,
        logical_id=str(context.logical_uuid(factory, logical_key)),
        physical_id=str(context.physical_uuid(factory, logical_key)),
        values=values,
    )


def build_scenario(
    context: FactoryContext,
    *,
    bundle: str,
    state: str,
    reverse: bool = False,
) -> ScenarioBundle:
    """Build one explicit valid/invalid/conflict/privacy scenario for a bundle."""

    if bundle not in BUNDLE_LEAVES:
        raise ValueError(f"unknown factory bundle: {bundle}")
    if state not in SCENARIO_STATES:
        raise ValueError(f"unknown factory scenario state: {state}")
    leaves = list(BUNDLE_LEAVES[bundle])
    if reverse:
        leaves.reverse()
    return ScenarioBundle(
        name=bundle,
        records=tuple(LEAF_FACTORIES[f"{bundle}.{leaf}"](context, state=state) for leaf in leaves),
    )


def build_all_bundles(
    context: FactoryContext,
    *,
    reverse: bool = False,
) -> tuple[ScenarioBundle, ...]:
    names = list(BUNDLE_LEAVES)
    if reverse:
        names.reverse()
    bundles: list[ScenarioBundle] = []
    for name in names:
        states = reversed(SCENARIO_STATES) if reverse else SCENARIO_STATES
        records = [
            record
            for state in states
            for record in build_scenario(
                context,
                bundle=name,
                state=state,
                reverse=reverse,
            ).records
        ]
        bundles.append(ScenarioBundle(name=name, records=tuple(records)))
    return tuple(bundles)
