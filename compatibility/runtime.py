"""Fail-closed runtime resolution of independently approved exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from django.http import HttpResponse

from compatibility.expectations import (
    ApprovedExpectation,
    ApprovedExpectationSet,
    Disposition,
    QueryPolicy,
)

_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class RuntimeRuleError(ValueError):
    """Approved exception records cannot form an unambiguous one-hop registry."""


class RuntimeDisposition(StrEnum):
    REDIRECT = "redirect"
    RETIRE = "retire"


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    expectation_id: str
    disposition: RuntimeDisposition
    status: int
    target_url: str = ""


def _without_fragment(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _path_identity(value: str) -> tuple[str, str, str]:
    parts = urlsplit(value)
    return parts.scheme, parts.netloc.lower(), parts.path or "/"


class CompatibilityRuntimeRegistry:
    """Resolve only explicit redirect/retire records; unknowns remain unknown."""

    def __init__(self, expectation_set: ApprovedExpectationSet) -> None:
        if type(expectation_set) is not ApprovedExpectationSet:
            raise RuntimeRuleError("runtime_requires_approved_expectation_set")
        self._exact: dict[str, ApprovedExpectation] = {}
        self._preserve_query: dict[tuple[str, str, str], ApprovedExpectation] = {}
        exception_sources: set[str] = set()
        preserve_query_sources: set[tuple[str, str, str]] = set()

        for expectation in expectation_set.expectations:
            if expectation.disposition is Disposition.PRESERVE:
                continue
            source = _without_fragment(expectation.public_url)
            if source in self._exact:
                raise RuntimeRuleError("duplicate_runtime_source")
            self._exact[source] = expectation
            exception_sources.add(source)
            if expectation.query_policy is QueryPolicy.PRESERVE_RAW:
                if urlsplit(source).query:
                    raise RuntimeRuleError("preserve_raw_source_must_not_contain_query")
                identity = _path_identity(source)
                if identity in self._preserve_query:
                    raise RuntimeRuleError("duplicate_preserve_raw_source")
                self._preserve_query[identity] = expectation
                preserve_query_sources.add(identity)

        # A target that is itself an exception source creates a chain or a loop.
        for expectation in self._exact.values():
            if expectation.disposition is not Disposition.REDIRECT:
                continue
            target = _without_fragment(expectation.resolved_redirect_target)
            if target in exception_sources or _path_identity(target) in preserve_query_sources:
                raise RuntimeRuleError("runtime_redirect_chain_or_loop")

    def resolve(self, public_url: str, *, method: str = "GET") -> RuntimeDecision | None:
        normalized_method = method.upper()
        if normalized_method not in _KNOWN_METHODS:
            raise RuntimeRuleError("runtime_method_is_not_supported")
        source = _without_fragment(public_url)
        expectation = self._exact.get(source)
        if expectation is None:
            expectation = self._preserve_query.get(_path_identity(source))
        if expectation is None:
            return None
        if expectation.disposition is Disposition.RETIRE:
            return RuntimeDecision(
                expectation_id=expectation.expectation_id,
                disposition=RuntimeDisposition.RETIRE,
                status=410,
            )
        if expectation.disposition is not Disposition.REDIRECT:
            raise RuntimeRuleError("runtime_rule_has_invalid_disposition")
        assert expectation.redirect_status is not None
        if expectation.redirect_status == 301 and normalized_method not in {"GET", "HEAD"}:
            return None
        target_url = expectation.redirect_target
        if expectation.query_policy is QueryPolicy.PRESERVE_RAW:
            source_query = urlsplit(source).query
            target = urlsplit(target_url)
            target_url = urlunsplit(
                (target.scheme, target.netloc, target.path, source_query, target.fragment)
            )
        return RuntimeDecision(
            expectation_id=expectation.expectation_id,
            disposition=RuntimeDisposition.REDIRECT,
            status=expectation.redirect_status,
            target_url=target_url,
        )


def runtime_response(decision: RuntimeDecision | None) -> HttpResponse | None:
    """Adapt a decision to Django while preserving ``None`` for the normal 404 path."""

    if decision is None:
        return None
    if decision.disposition is RuntimeDisposition.RETIRE:
        return HttpResponse("Gone", status=410, content_type="text/plain; charset=utf-8")
    response = HttpResponse(status=decision.status)
    response["Location"] = decision.target_url
    return response
