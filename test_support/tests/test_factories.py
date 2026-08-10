from __future__ import annotations

import hashlib
import locale
import random
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from django.db.models import Model

from test_support.factories import (
    BUNDLE_LEAVES,
    LEAF_FACTORIES,
    SCENARIO_STATES,
    FactoryContext,
    RejectedDomainValue,
    build_all_bundles,
    build_scenario,
    canonical_json_bytes,
    create_current_scenario,
)

FROZEN_AT = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


def _logical_catalog(context: FactoryContext, *, reverse: bool = False) -> bytes:
    bundles = build_all_bundles(context, reverse=reverse)
    return canonical_json_bytes({bundle.name: bundle.logical_payload() for bundle in bundles})


def test_all_named_bundles_are_byte_deterministic_and_order_independent() -> None:
    first = FactoryContext("accepted-seed", "worker-a", FROZEN_AT)
    second = FactoryContext("accepted-seed", "worker-a", FROZEN_AT)
    reverse = FactoryContext("accepted-seed", "worker-a", FROZEN_AT)

    first_payload = _logical_catalog(first)
    assert first_payload == _logical_catalog(second)
    assert first_payload == _logical_catalog(reverse, reverse=True)
    assert (
        hashlib.sha256(first_payload).hexdigest()
        == hashlib.sha256(_logical_catalog(second)).hexdigest()
    )


def test_namespaces_change_only_physical_identity() -> None:
    first = build_all_bundles(FactoryContext("same-seed", "worker-a", FROZEN_AT))
    second = build_all_bundles(FactoryContext("same-seed", "worker-b", FROZEN_AT))
    assert {bundle.name: bundle.canonical_json() for bundle in first} == {
        bundle.name: bundle.canonical_json() for bundle in second
    }
    first_ids = {record.physical_id for bundle in first for record in bundle.records}
    second_ids = {record.physical_id for bundle in second for record in bundle.records}
    assert first_ids.isdisjoint(second_ids)


def test_catalog_covers_every_current_contract_and_state_without_false_equivalence() -> None:
    assert set(BUNDLE_LEAVES) == {
        "accounts_management",
        "adopted_courses",
        "editorial_content",
        "historical_event_totals",
        "operations_jobs",
        "provider_neutral_messaging",
    }
    for bundle in build_all_bundles(FactoryContext("catalog", "main", FROZEN_AT)):
        assert {record.state for record in bundle.records} == set(SCENARIO_STATES)

    account = build_all_bundles(FactoryContext("catalog", "main", FROZEN_AT))[0]
    assert all(record.values["public_person_link"] is None for record in account.records)
    courses = next(
        bundle
        for bundle in build_all_bundles(FactoryContext("catalog", "main", FROZEN_AT))
        if bundle.name == "adopted_courses"
    )
    assert all(not record.values["campaign_is_cohort"] for record in courses.records)
    totals = next(
        bundle
        for bundle in build_all_bundles(FactoryContext("catalog", "main", FROZEN_AT))
        if bundle.name == "historical_event_totals"
    )
    assert all(record.values["registration_rows"] == [] for record in totals.records)
    messaging = next(
        bundle
        for bundle in build_all_bundles(FactoryContext("catalog", "main", FROZEN_AT))
        if bundle.name == "provider_neutral_messaging"
    )
    assert all(record.values["provider_client"] is None for record in messaging.records)


def test_every_declared_leaf_has_a_named_factory_and_explicit_scenario_builder() -> None:
    expected = {f"{bundle}.{leaf}" for bundle, leaves in BUNDLE_LEAVES.items() for leaf in leaves}
    assert set(LEAF_FACTORIES) == expected
    context = FactoryContext("named-leaves", "main", FROZEN_AT)
    for name, factory in LEAF_FACTORIES.items():
        assert factory.name == name
        assert factory(context).factory == name

    for bundle, leaves in BUNDLE_LEAVES.items():
        for state in SCENARIO_STATES:
            scenario = build_scenario(context, bundle=bundle, state=state)
            assert scenario.name == bundle
            assert {record.factory.rpartition(".")[2] for record in scenario.records} == set(leaves)
            assert {record.state for record in scenario.records} == {state}


def test_private_random_streams_and_sequences_do_not_use_module_global_random() -> None:
    context = FactoryContext("private-stream", "main", FROZEN_AT)
    global_state = random.getstate()
    values = [context.random_stream("course").randrange(10_000) for _ in range(4)]
    repeat = FactoryContext("private-stream", "different-worker", FROZEN_AT)
    assert values == [repeat.random_stream("course").randrange(10_000) for _ in range(4)]
    assert random.getstate() == global_state
    assert [context.next_sequence("course") for _ in range(3)] == [1, 2, 3]
    assert context.next_sequence("user") == 1


def test_frozen_environment_restores_random_timezone_and_locale_after_failure() -> None:
    context = FactoryContext("frozen", "main", FROZEN_AT)
    random_state = random.getstate()
    current_locale = locale.setlocale(locale.LC_ALL)
    with pytest.raises(RuntimeError, match="injected"):
        with context.frozen_environment():
            assert locale.setlocale(locale.LC_ALL) == "C"
            raise RuntimeError("injected")
    assert random.getstate() == random_state
    assert locale.setlocale(locale.LC_ALL) == current_locale


@pytest.mark.parametrize("namespace", ["", "../worker", "worker/name", "x" * 65])
def test_context_rejects_unsafe_execution_namespaces(namespace: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        FactoryContext("seed", namespace, FROZEN_AT)


@pytest.mark.django_db
def test_current_domain_scenarios_create_every_named_leaf_as_real_orm_or_simulator_value() -> None:
    context = FactoryContext("current-domain", "worker-current", FROZEN_AT)
    expected_labels = {
        "accounts_management.custom_user": "accounts.customuser",
        "editorial_content.content_source": "content.contentsource",
        "adopted_courses.course": "courses.course",
        "historical_event_totals.historical_source_run": "events.historicalregistrationsourcerun",
        "operations_jobs.operation": "core.operation",
        "provider_neutral_messaging.captured_message": "messaging.captured_message",
    }
    for bundle, leaves in BUNDLE_LEAVES.items():
        scenario = create_current_scenario(context, bundle=bundle)
        assert set(scenario.by_factory()) == {f"{bundle}.{leaf}" for leaf in leaves}
        for identity in scenario.identities:
            if bundle == "provider_neutral_messaging":
                assert not isinstance(identity.value, dict)
                assert identity.database_key is None
            else:
                assert isinstance(identity.value, Model)
                assert identity.database_key == str(identity.value.pk)
        representative = next(
            identity for identity in scenario.identities if identity.factory in expected_labels
        )
        assert representative.model_label == expected_labels[representative.factory]


@pytest.mark.django_db
def test_named_leaf_create_entry_points_return_the_requested_current_domain_value() -> None:
    representatives = (
        "accounts_management.custom_user",
        "editorial_content.content_source",
        "adopted_courses.course",
        "historical_event_totals.historical_source_run",
        "operations_jobs.operation",
        "provider_neutral_messaging.captured_message",
    )
    for index, name in enumerate(representatives):
        identity = LEAF_FACTORIES[name].create(
            FactoryContext("leaf-current-domain", f"leaf-worker-{index}", FROZEN_AT)
        )
        assert identity.factory == name
        assert not isinstance(identity.value, dict)
        if name.startswith("provider_neutral_messaging."):
            assert identity.database_key is None
        else:
            assert isinstance(identity.value, Model)
            assert identity.database_key == str(identity.value.pk)


@pytest.mark.django_db
def test_named_leaf_factories_are_independently_composable() -> None:
    context = FactoryContext("leaf-composition", "leaf-composition", FROZEN_AT)
    user = LEAF_FACTORIES["accounts_management.custom_user"].create(context)
    group = LEAF_FACTORIES["accounts_management.staff_group"].create(context)
    assert user.factory == "accounts_management.custom_user"
    assert group.factory == "accounts_management.staff_group"
    assert user.value.groups.model is group.value.__class__


@pytest.mark.django_db
def test_operations_have_distinct_lease_result_and_terminal_scenario_states() -> None:
    durable_jobs = []
    for state in SCENARIO_STATES:
        scenario = create_current_scenario(
            FactoryContext("operation-states", f"operation-{state}", FROZEN_AT),
            bundle="operations_jobs",
            state=state,
        )
        identities = scenario.by_factory()
        durable_jobs.append(identities["operations_jobs.durable_job"].value)
        lease = identities["operations_jobs.job_lease"].value
        result = identities["operations_jobs.job_result"].value
        assert len({lease.pk, result.pk, durable_jobs[-1].pk}) == 3
        assert lease.status == "running" and lease.lease_token is not None
        assert result.status in result.TERMINAL_STATUSES and result.completed_at is not None
    assert any(job.status == "running" and job.lease_token is not None for job in durable_jobs)
    assert any(job.status == "failed" and job.completed_at is not None for job in durable_jobs)


@pytest.mark.django_db
def test_provider_scenarios_cover_every_simulated_outcome() -> None:
    outcomes: set[str | None] = set()
    for state in SCENARIO_STATES:
        scenario = create_current_scenario(
            FactoryContext("provider-outcomes", f"provider-{state}", FROZEN_AT),
            bundle="provider_neutral_messaging",
            state=state,
        )
        outcomes.update(
            getattr(identity.value, "outcome", None)
            for identity in scenario.identities
            if identity.factory != "provider_neutral_messaging.captured_message"
        )
    assert outcomes == {
        "accepted",
        "ambiguous",
        "duplicate",
        "out_of_order",
        "permanent_failure",
        "retry",
        "suppressed",
        "transient_failure",
    }


@pytest.mark.django_db
def test_current_domain_canonical_payload_contains_live_normalized_orm_values() -> None:
    scenario = create_current_scenario(
        FactoryContext("canonical-values", "canonical-values", FROZEN_AT),
        bundle="operations_jobs",
        state="boundary_valid",
    )
    payload = scenario.logical_payload()
    identities = cast(list[dict[str, Any]], payload["identities"])
    job_payload = next(
        identity for identity in identities if identity["factory"] == "operations_jobs.durable_job"
    )
    assert job_payload["values"]["fields"]["status"] == "running"
    before = scenario.canonical_json()
    scenario.by_factory()["operations_jobs.durable_job"].value.status = "pending"
    assert scenario.canonical_json() != before


@pytest.mark.django_db
def test_every_current_domain_bundle_builds_all_six_states_and_rejects_invalid_input() -> None:
    for bundle in BUNDLE_LEAVES:
        for state in SCENARIO_STATES:
            context = FactoryContext("scenario-matrix", f"{bundle[:12]}-{state[:12]}", FROZEN_AT)
            scenario = create_current_scenario(context, bundle=bundle, state=state)
            assert {identity.state for identity in scenario.identities} == {state}
            if state == "invalid_rejected":
                assert any(
                    isinstance(identity.value, RejectedDomainValue)
                    for identity in scenario.identities
                )
            else:
                assert all(
                    not isinstance(identity.value, RejectedDomainValue)
                    for identity in scenario.identities
                )


@pytest.mark.django_db
def test_current_domain_namespaces_keep_logical_payload_equal_and_physical_rows_disjoint() -> None:
    for bundle in BUNDLE_LEAVES:
        first = create_current_scenario(
            FactoryContext("namespace-domain", f"{bundle[:20]}-a", FROZEN_AT),
            bundle=bundle,
        )
        second = create_current_scenario(
            FactoryContext("namespace-domain", f"{bundle[:20]}-b", FROZEN_AT),
            bundle=bundle,
        )
        assert first.canonical_json() == second.canonical_json()
        assert first.sha256() == second.sha256()
        assert {identity.physical_id for identity in first.identities}.isdisjoint(
            identity.physical_id for identity in second.identities
        )
        shared_domain_rows = {"auth.permission", "jobs.schedulerlease"}
        first_rows = {
            (identity.model_label, identity.database_key)
            for identity in first.identities
            if identity.database_key is not None and identity.model_label not in shared_domain_rows
        }
        second_rows = {
            (identity.model_label, identity.database_key)
            for identity in second.identities
            if identity.database_key is not None and identity.model_label not in shared_domain_rows
        }
        assert first_rows.isdisjoint(second_rows)
