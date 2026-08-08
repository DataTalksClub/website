"""Deterministic comparison of approved expectations with Django observations."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from urllib.parse import urldefrag, urlsplit

from compatibility.expectations import (
    ApprovedExpectation,
    ApprovedExpectationSet,
    Disposition,
    ExpectedResponse,
)
from compatibility.links import ReferenceProblem, ReferenceSeverity, validate_reference_graph
from compatibility.models import PageMetadata, ReferenceKind
from compatibility.report import (
    Finding,
    FindingSeverity,
    ParityReport,
    TargetBinding,
)
from compatibility.target import TargetObservation


def _observation_id(url: str) -> str:
    return f"observation-{hashlib.sha256(url.encode()).hexdigest()[:24]}"


def _finding(
    expectation: ApprovedExpectation,
    code: str,
    field: str,
    action: str,
    *,
    severity: FindingSeverity = FindingSeverity.BLOCKING,
) -> Finding:
    return Finding.create(
        code=code,
        severity=severity,
        scope=expectation.source_scope,
        subject_id=expectation.expectation_id,
        field=field,
        required_action=action,
    )


def _append_once(findings: dict[str, Finding], finding: Finding) -> None:
    findings.setdefault(finding.finding_id, finding)


def _compare_field(
    findings: dict[str, Finding],
    expectation: ApprovedExpectation,
    *,
    expected: object,
    actual: object,
    code: str,
    field: str,
    action: str,
) -> None:
    if expected != actual:
        _append_once(findings, _finding(expectation, code, field, action))


def _reference_sets(metadata: PageMetadata) -> dict[ReferenceKind, frozenset[str]]:
    return {
        kind: frozenset(item.url for item in metadata.references if item.kind is kind)
        for kind in ReferenceKind
    }


def _compare_references(
    findings: dict[str, Finding],
    expectation: ApprovedExpectation,
    expected: PageMetadata,
    actual: PageMetadata,
    optional_external_urls: frozenset[str],
) -> None:
    expected_by_kind = _reference_sets(expected)
    actual_by_kind = _reference_sets(actual)
    codes = {
        ReferenceKind.INTERNAL_LINK: ("internal_reference_changed", "references.internal"),
        ReferenceKind.FORM_ACTION: ("form_action_changed", "references.form"),
        ReferenceKind.ASSET: ("asset_reference_changed", "references.asset"),
    }
    for kind, (code, field) in codes.items():
        if expected_by_kind[kind] != actual_by_kind[kind]:
            _append_once(
                findings,
                _finding(expectation, code, field, "restore_exact_reference_set"),
            )

    missing_external = (
        expected_by_kind[ReferenceKind.EXTERNAL_LINK] - actual_by_kind[ReferenceKind.EXTERNAL_LINK]
    )
    added_external = (
        actual_by_kind[ReferenceKind.EXTERNAL_LINK] - expected_by_kind[ReferenceKind.EXTERNAL_LINK]
    )
    required_missing = missing_external - optional_external_urls
    optional_missing = missing_external & optional_external_urls
    if required_missing or added_external:
        _append_once(
            findings,
            _finding(
                expectation,
                "external_reference_changed",
                "references.external",
                "restore_exact_external_url_and_query",
            ),
        )
    if optional_missing:
        _append_once(
            findings,
            _finding(
                expectation,
                "optional_external_changed",
                "references.external_optional",
                "show_optional_external_warning",
                severity=FindingSeverity.WARNING,
            ),
        )


def _is_binary(content_type: str) -> bool:
    return not (
        content_type.startswith("text/")
        or content_type
        in {
            "application/json",
            "application/ld+json",
            "application/xhtml+xml",
            "application/xml",
        }
    )


def _compare_response(
    findings: dict[str, Finding],
    expectation: ApprovedExpectation,
    expected: ExpectedResponse,
    actual: TargetObservation,
    *,
    optional_external_urls: frozenset[str],
) -> None:
    for expected_value, actual_value, code, field, action in (
        (expected.status, actual.status, "status_changed", "response.status", "restore_status"),
        (
            expected.final_url,
            actual.final_url,
            "final_url_changed",
            "response.final_url",
            "restore_exact_final_url",
        ),
        (
            expected.content_type,
            actual.content_type,
            "content_type_changed",
            "response.content_type",
            "restore_content_type",
        ),
        (
            expected.response_last_modified,
            actual.response_last_modified,
            "last_modified_changed",
            "response.last_modified",
            "restore_last_modified",
        ),
        (
            expected.response_content_language,
            actual.response_content_language,
            "content_language_changed",
            "response.content_language",
            "restore_content_language",
        ),
        (
            expected.response_robots,
            actual.response_robots,
            "robots_header_changed",
            "response.robots",
            "restore_response_robots",
        ),
    ):
        _compare_field(
            findings,
            expectation,
            expected=expected_value,
            actual=actual_value,
            code=code,
            field=field,
            action=action,
        )

    before = expected.metadata
    after = actual.metadata
    metadata_comparisons: tuple[tuple[object, object, str, str, str], ...] = (
        (before.title, after.title, "title_changed", "metadata.title", "restore_title"),
        (
            before.description,
            after.description,
            "description_changed",
            "metadata.description",
            "restore_description",
        ),
        (
            before.first_heading,
            after.first_heading,
            "first_heading_changed",
            "metadata.first_heading",
            "restore_first_heading",
        ),
        (
            before.language,
            after.language,
            "language_changed",
            "metadata.language",
            "restore_language",
        ),
        (
            before.canonical_url,
            after.canonical_url,
            "canonical_changed",
            "metadata.canonical",
            "restore_production_canonical",
        ),
        (
            before.alternates,
            after.alternates,
            "alternates_changed",
            "metadata.alternates",
            "restore_alternates",
        ),
        (
            before.social_metadata,
            after.social_metadata,
            "social_metadata_changed",
            "metadata.social",
            "restore_social_image_metadata",
        ),
        (
            before.structured_data,
            after.structured_data,
            "structured_data_changed",
            "metadata.structured_data",
            "restore_structured_types_and_identifiers",
        ),
        (
            before.fragments,
            after.fragments,
            "fragment_set_changed",
            "metadata.fragments",
            "restore_literal_fragments",
        ),
        (
            before.robots,
            after.robots,
            "robots_meta_changed",
            "metadata.robots",
            "restore_meta_robots",
        ),
    )
    for metadata_expected, metadata_actual, code, field, action in metadata_comparisons:
        _compare_field(
            findings,
            expectation,
            expected=metadata_expected,
            actual=metadata_actual,
            code=code,
            field=field,
            action=action,
        )

    if before.main_content_fingerprint != after.main_content_fingerprint:
        code = (
            "server_rendered_body_missing"
            if before.main_content_fingerprint and not after.main_content_fingerprint
            else "body_fingerprint_changed"
        )
        _append_once(
            findings,
            _finding(
                expectation,
                code,
                "metadata.main_content_fingerprint",
                "restore_server_rendered_meaningful_content",
            ),
        )
    if after.soft_404:
        _append_once(
            findings,
            _finding(expectation, "soft_404", "metadata.soft_404", "restore_real_content"),
        )
    if after.client_redirect_url:
        _append_once(
            findings,
            _finding(
                expectation,
                "client_redirect",
                "metadata.client_redirect",
                "replace_with_approved_http_redirect",
            ),
        )
    if (
        ("noindex" in after.robots or "noindex" in actual.response_robots)
        and "noindex" not in before.robots
        and "noindex" not in expected.response_robots
    ):
        _append_once(
            findings,
            _finding(
                expectation,
                "production_noindex",
                "metadata.robots_indexability",
                "remove_accidental_production_noindex",
            ),
        )
    canonical = urlsplit(after.canonical_url) if after.canonical_url else None
    if canonical and canonical.hostname and canonical.hostname.endswith("dtcdev.click"):
        _append_once(
            findings,
            _finding(
                expectation,
                "canonical_non_production_origin",
                "metadata.canonical_origin",
                "restore_production_canonical",
            ),
        )
    _compare_references(findings, expectation, before, after, optional_external_urls)
    _compare_field(
        findings,
        expectation,
        expected=expected.sitemap,
        actual=actual.sitemap,
        code="sitemap_changed",
        field="sitemap.entries_lastmod",
        action="restore_canonical_sitemap_entries_and_lastmod",
    )
    if _is_binary(expected.content_type) and expected.body_sha256 != actual.body_sha256:
        _append_once(
            findings,
            _finding(
                expectation,
                "asset_body_changed",
                "response.body_sha256",
                "restore_exact_binary_asset",
            ),
        )


def _compare_expectation(
    findings: dict[str, Finding],
    expectation: ApprovedExpectation,
    actual: TargetObservation,
    *,
    optional_external_urls: frozenset[str],
) -> None:
    if actual.capture_error:
        code = (
            actual.capture_error
            if actual.capture_error
            in {
                "invalid_html",
                "invalid_json",
                "invalid_json_ld",
                "invalid_xml",
                "redirect_loop",
                "redirect_limit_exceeded",
            }
            else "capture_error"
        )
        _append_once(
            findings,
            _finding(expectation, code, "observation.capture", "repair_target_capture"),
        )

    if expectation.disposition is Disposition.RETIRE:
        if actual.status != 410:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "retirement_not_410",
                    "response.status",
                    "return_direct_410",
                ),
            )
        if actual.redirect_chain or actual.response_location or actual.metadata.client_redirect_url:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "retirement_redirected",
                    "response.redirect",
                    "remove_retirement_redirect",
                ),
            )
        return

    expected = expectation.expected_response
    assert expected is not None
    if expectation.disposition is Disposition.REDIRECT:
        if len(actual.redirect_chain) != 1:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "redirect_hop_count",
                    "response.redirect_chain",
                    "restore_exact_one_hop_redirect",
                ),
            )
        else:
            hop = actual.redirect_chain[0]
            if hop.status != expectation.redirect_status:
                _append_once(
                    findings,
                    _finding(
                        expectation,
                        "redirect_status_invalid",
                        "response.redirect_status",
                        "restore_approved_permanent_status",
                    ),
                )
            if hop.url != expectation.resolved_redirect_target:
                _append_once(
                    findings,
                    _finding(
                        expectation,
                        "redirect_target_mismatch",
                        "response.redirect_target",
                        "restore_exact_redirect_target_and_query",
                    ),
                )
        redirect_fetch_url = urldefrag(expectation.resolved_redirect_target)[0]
        if actual.final_url != redirect_fetch_url:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "redirect_target_mismatch",
                    "response.final_url",
                    "restore_exact_redirect_target_and_query",
                ),
            )
        if not 200 <= actual.status <= 299:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "redirect_target_failed",
                    "response.target_status",
                    "restore_successful_redirect_target",
                ),
            )
    elif actual.redirect_chain:
        _append_once(
            findings,
            _finding(
                expectation,
                "unexpected_redirect",
                "response.redirect_chain",
                "serve_preserved_path_directly",
            ),
        )
    _compare_response(
        findings,
        expectation,
        expected,
        actual,
        optional_external_urls=optional_external_urls,
    )


def _reference_findings(
    problems: Iterable[ReferenceProblem],
    expectations_by_url: dict[str, ApprovedExpectation],
) -> tuple[Finding, ...]:
    output: dict[str, Finding] = {}
    for problem in problems:
        expectation = expectations_by_url.get(problem.source_url)
        if expectation is None:
            continue
        field = f"references.{problem.code}"
        finding = _finding(
            expectation,
            problem.code,
            field,
            problem.required_action,
            severity=(
                FindingSeverity.WARNING
                if problem.severity is ReferenceSeverity.WARNING
                else FindingSeverity.BLOCKING
            ),
        )
        _append_once(output, finding)
    return tuple(output.values())


def evaluate_parity(
    expectation_set: ApprovedExpectationSet,
    observations: tuple[TargetObservation, ...],
    *,
    target: TargetBinding,
    scope: tuple[str, ...] | None = None,
    optional_external_urls: frozenset[str] = frozenset(),
) -> ParityReport:
    """Evaluate one exact approved scope and return a canonical fail-closed report."""

    selected_scope = tuple(sorted(set(expectation_set.scopes if scope is None else scope)))
    if not selected_scope:
        raise ValueError("parity_scope_must_not_be_empty")
    selected = tuple(
        item for item in expectation_set.expectations if item.source_scope in selected_scope
    )
    expectations_by_url = {item.public_url: item for item in selected}
    observations_by_url: dict[str, TargetObservation] = {}
    findings: dict[str, Finding] = {}
    for observation in observations:
        if observation.public_url in observations_by_url:
            raise ValueError("duplicate_target_observation_url")
        observations_by_url[observation.public_url] = observation

    approved_scopes = {item.source_scope for item in selected}
    missing_scopes = set(selected_scope) - approved_scopes
    if missing_scopes:
        for selected_item in sorted(missing_scopes):
            finding = Finding.create(
                code="approved_expectations_missing",
                severity=FindingSeverity.BLOCKING,
                scope=selected_item,
                subject_id="gate",
                field="coverage.expectations",
                required_action="approve_target_expectations",
            )
            _append_once(findings, finding)

    matched_count = 0
    for expectation in selected:
        candidate = observations_by_url.get(expectation.public_url)
        if candidate is None:
            _append_once(
                findings,
                _finding(
                    expectation,
                    "missing_observation",
                    "coverage.observation",
                    "capture_exact_target_url",
                ),
            )
            continue
        matched_count += 1
        _compare_expectation(
            findings,
            expectation,
            candidate,
            optional_external_urls=optional_external_urls,
        )

    extra_urls = set(observations_by_url) - set(expectations_by_url)
    for extra_url in sorted(extra_urls):
        selected_item = selected_scope[0]
        finding = Finding.create(
            code="extra_observation",
            severity=FindingSeverity.BLOCKING,
            scope=selected_item,
            subject_id=_observation_id(extra_url),
            field="coverage.observation",
            required_action="remove_or_approve_observation",
        )
        _append_once(findings, finding)

    internal_hosts = frozenset(urlsplit(item.public_url).hostname or "" for item in selected)
    problems = validate_reference_graph(
        tuple(observations_by_url.values()),
        internal_hosts=internal_hosts,
        optional_external_urls=optional_external_urls,
    )
    for finding in _reference_findings(problems, expectations_by_url):
        _append_once(findings, finding)

    complete = bool(selected) and not (
        missing_scopes or set(expectations_by_url) - set(observations_by_url) or extra_urls
    )
    return ParityReport.create(
        target=target,
        manifest_sha256=expectation_set.manifest_sha256,
        differences_sha256=expectation_set.differences_sha256,
        public_contracts_sha256=expectation_set.public_contracts_sha256,
        expectation_set_sha256=expectation_set.sha256,
        scope=selected_scope,
        complete=complete,
        expectation_count=len(selected),
        observation_count=len(observations),
        matched_count=matched_count,
        findings=tuple(findings.values()),
    )
