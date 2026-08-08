from __future__ import annotations

import pytest

from compatibility.expectations import (
    ApprovedExpectation,
    ApprovedExpectationSet,
    ExpectedResponse,
    QueryPolicy,
)
from compatibility.runtime import (
    CompatibilityRuntimeRegistry,
    RuntimeDisposition,
    RuntimeRuleError,
    runtime_response,
)

DIGEST = "a" * 64


def response(url: str) -> ExpectedResponse:
    return ExpectedResponse(status=200, final_url=url, content_type="text/html")


def expectation_set(*items: ApprovedExpectation) -> ApprovedExpectationSet:
    return ApprovedExpectationSet.create(
        manifest_sha256=DIGEST,
        differences_sha256="b" * 64,
        public_contracts_sha256="c" * 64,
        expectations=items,
    )


def redirect(
    source: str,
    target: str,
    *,
    status: int = 301,
    policy: QueryPolicy = QueryPolicy.EXACT,
) -> ApprovedExpectation:
    resolved = target
    if policy is QueryPolicy.PRESERVE_RAW:
        query = source.partition("?")[2]
        resolved = f"{target}?{query}" if query else target
    return ApprovedExpectation.redirect(
        source_scope="fixture",
        public_url=source,
        redirect_status=status,
        redirect_target=target,
        query_policy=policy,
        expected_response=response(resolved),
        owner="seo-team",
        reason="Approved fixture migration",
        test_reference="compatibility/tests/test_runtime.py",
    )


def test_resolves_exact_safe_redirect_and_preserves_raw_query_only_when_approved() -> None:
    exact = redirect(
        "https://datatalks.club/old?x=%2F&x=",
        "https://datatalks.club/new?x=%2F&x=",
    )
    preserve = redirect(
        "https://datatalks.club/legacy",
        "https://datatalks.club/current",
        policy=QueryPolicy.PRESERVE_RAW,
    )
    registry = CompatibilityRuntimeRegistry(expectation_set(exact, preserve))

    exact_decision = registry.resolve("https://datatalks.club/old?x=%2F&x=")
    preserved = registry.resolve("https://datatalks.club/legacy?x=1&x=&q=A+B&q=A%20B")

    assert exact_decision is not None
    assert exact_decision.target_url == "https://datatalks.club/new?x=%2F&x="
    assert registry.resolve("https://datatalks.club/old?x=%2f&x=") is None
    assert preserved is not None
    assert preserved.target_url == ("https://datatalks.club/current?x=1&x=&q=A+B&q=A%20B")


def test_301_is_safe_method_only_while_approved_308_preserves_non_get_method() -> None:
    permanent = redirect(
        "https://datatalks.club/get-only",
        "https://datatalks.club/current",
    )
    method_safe = redirect(
        "https://datatalks.club/method",
        "https://datatalks.club/current-method",
        status=308,
    )
    registry = CompatibilityRuntimeRegistry(expectation_set(permanent, method_safe))

    assert registry.resolve("https://datatalks.club/get-only", method="POST") is None
    decision = registry.resolve("https://datatalks.club/method", method="POST")
    assert decision is not None
    assert decision.status == 308


def test_direct_retirement_and_unknown_path_have_no_catchall() -> None:
    retired = ApprovedExpectation.retire(
        source_scope="fixture",
        public_url="https://datatalks.club/removed",
        owner="seo-team",
        reason="No replacement exists",
        test_reference="compatibility/tests/test_runtime.py",
    )
    registry = CompatibilityRuntimeRegistry(expectation_set(retired))

    decision = registry.resolve("https://datatalks.club/removed")
    assert decision is not None
    assert decision.disposition is RuntimeDisposition.RETIRE
    response_value = runtime_response(decision)
    assert response_value is not None
    assert response_value.status_code == 410
    assert "Location" not in response_value
    assert registry.resolve("https://datatalks.club/unknown") is None
    assert runtime_response(None) is None


def test_rejects_chain_loop_and_ambiguous_preserve_raw_sources() -> None:
    first = redirect(
        "https://datatalks.club/first",
        "https://datatalks.club/second",
    )
    second = redirect(
        "https://datatalks.club/second",
        "https://datatalks.club/final",
    )
    with pytest.raises(RuntimeRuleError, match="chain_or_loop"):
        CompatibilityRuntimeRegistry(expectation_set(first, second))

    ambiguous = redirect(
        "https://datatalks.club/query?seed=1",
        "https://datatalks.club/current",
        policy=QueryPolicy.PRESERVE_RAW,
    )
    with pytest.raises(RuntimeRuleError, match="source_must_not_contain_query"):
        CompatibilityRuntimeRegistry(expectation_set(ambiguous))
