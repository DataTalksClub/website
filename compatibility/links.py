"""Exact, network-free reference and literal-fragment validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urldefrag, urlsplit

from compatibility.models import ReferenceKind
from compatibility.target import TargetObservation


class ReferenceSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ReferenceProblem:
    code: str
    severity: ReferenceSeverity
    source_url: str
    target_url: str
    required_action: str


def _host(value: str) -> str:
    hostname = urlsplit(value).hostname or ""
    try:
        return hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _problem(
    code: str,
    source: TargetObservation,
    target_url: str,
    action: str,
    *,
    severity: ReferenceSeverity = ReferenceSeverity.ERROR,
) -> ReferenceProblem:
    return ReferenceProblem(
        code=code,
        severity=severity,
        source_url=source.public_url,
        target_url=target_url,
        required_action=action,
    )


def validate_reference_graph(
    observations: tuple[TargetObservation, ...],
    *,
    internal_hosts: frozenset[str],
    optional_external_urls: frozenset[str] = frozenset(),
) -> tuple[ReferenceProblem, ...]:
    """Resolve captured references without normalizing URL identity or using I/O.

    External URLs are intentionally not contacted. Their full URL, including raw
    query spelling, remains part of metadata parity. An explicit optional policy is
    surfaced as a warning so it cannot silently disappear from operator reports.
    """

    by_url: dict[str, TargetObservation] = {}
    for observation in observations:
        if observation.public_url in by_url:
            raise ValueError("duplicate_target_observation_url")
        by_url[observation.public_url] = observation
    normalized_hosts = frozenset(_host(f"https://{host}") for host in internal_hosts)
    problems: list[ReferenceProblem] = []

    for source in sorted(observations, key=lambda item: item.public_url):
        for reference in source.metadata.references:
            reference_host = _host(reference.url)
            is_external = (
                reference.kind is ReferenceKind.EXTERNAL_LINK
                or reference_host not in normalized_hosts
            )
            if is_external:
                if reference.url in optional_external_urls:
                    problems.append(
                        _problem(
                            "optional_external_not_fetched",
                            source,
                            reference.url,
                            "show_optional_external_status",
                            severity=ReferenceSeverity.WARNING,
                        )
                    )
                continue

            fragmentless, raw_fragment = urldefrag(reference.url)
            target = by_url.get(fragmentless)
            if target is None:
                code = {
                    ReferenceKind.ASSET: "asset_missing",
                    ReferenceKind.FORM_ACTION: "form_target_missing",
                }.get(reference.kind, "internal_target_missing")
                problems.append(
                    _problem(code, source, reference.url, "restore_exact_target_or_reference")
                )
                continue
            if target.capture_error:
                problems.append(
                    _problem(
                        "target_capture_error",
                        source,
                        reference.url,
                        "repair_target_capture",
                    )
                )
                continue
            if reference.kind is ReferenceKind.ASSET:
                if target.status != 200:
                    problems.append(
                        _problem(
                            "asset_bad_status",
                            source,
                            reference.url,
                            "restore_asset_with_direct_200",
                        )
                    )
                if target.redirect_chain:
                    problems.append(
                        _problem(
                            "asset_redirected",
                            source,
                            reference.url,
                            "serve_asset_at_exact_path",
                        )
                    )
            elif reference.kind is ReferenceKind.FORM_ACTION:
                if target.status in {404, 410} or target.status >= 500:
                    problems.append(
                        _problem(
                            "form_target_bad_status",
                            source,
                            reference.url,
                            "restore_form_action_target",
                        )
                    )
            else:
                if not 200 <= target.status <= 299:
                    problems.append(
                        _problem(
                            "internal_target_bad_status",
                            source,
                            reference.url,
                            "restore_internal_target",
                        )
                    )
                if target.redirect_chain:
                    problems.append(
                        _problem(
                            "internal_target_redirect_regression",
                            source,
                            reference.url,
                            "link_directly_to_approved_target",
                        )
                    )
            if raw_fragment:
                try:
                    fragment = unquote(raw_fragment, encoding="utf-8", errors="strict")
                except UnicodeError:
                    problems.append(
                        _problem(
                            "fragment_encoding_invalid",
                            source,
                            reference.url,
                            "restore_valid_exact_fragment",
                        )
                    )
                    continue
                if fragment not in target.metadata.fragments:
                    problems.append(
                        _problem(
                            "fragment_missing",
                            source,
                            reference.url,
                            "restore_literal_target_fragment",
                        )
                    )

    return tuple(
        sorted(
            problems,
            key=lambda item: (
                item.source_url,
                item.severity.value,
                item.code,
                item.target_url,
            ),
        )
    )
