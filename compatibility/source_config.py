"""Pinned legacy sources used to build the compatibility baseline.

This module is deliberately data-only.  The crawler and build helper consume the same exact
repository revisions, public mounts, and machine-contract samples so a nearby developer checkout
can never become baseline evidence accidentally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit

SOURCE_CONFIG_SCHEMA_VERSION = 1
RUSTKYLL_0_4_6_LINUX_AMD64_SHA256 = (
    "8a8d05b5056cb34bd59a76f7952be48e7d6cb93782821c4a688f13a7908185f5"
)
FAQ_WEBSITE_UV_LOCK_SHA256 = "f8070e0954a5bca6e7bd58c76854fd51b906b47a5d5f93b7423541ef436dc8f8"
FAQ_FROZEN_GENERATION_TIME = "2000-01-01 00:00:00"

_SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class SourceKind(StrEnum):
    RUSTKYLL_RELEASE = "rustkyll_release"
    FAQ_PYTHON = "faq_python"
    RUSTKYLL_PYPI = "rustkyll_pypi"
    DJANGO_ROUTE_CONTRACTS = "django_route_contracts"


@dataclass(frozen=True, slots=True)
class PinnedLegacySource:
    source_id: str
    repository: str
    revision: str
    public_base_url: str
    path_prefix: str
    source_kind: SourceKind
    output_directory: str | None
    build_tool: str
    build_tool_version: str
    build_tool_sha256: str | None = None
    deterministic_overrides: tuple[str, ...] = ()
    machine_contracts: tuple[str, ...] = ()

    def validate(self) -> None:
        if _SOURCE_ID.fullmatch(self.source_id) is None:
            raise ValueError("invalid pinned source id")
        if _COMMIT_SHA.fullmatch(self.revision) is None:
            raise ValueError(f"invalid pinned revision for {self.source_id}")
        repository = urlsplit(self.repository)
        if (
            repository.scheme != "https"
            or repository.hostname != "github.com"
            or repository.username is not None
            or repository.password is not None
            or repository.query
            or repository.fragment
        ):
            raise ValueError(f"invalid pinned repository for {self.source_id}")
        public = urlsplit(self.public_base_url)
        if (
            public.scheme != "https"
            or public.hostname not in {"datatalks.club", "courses.datatalks.club"}
            or public.username is not None
            or public.password is not None
            or public.query
            or public.fragment
        ):
            raise ValueError(f"invalid public base URL for {self.source_id}")
        if not self.path_prefix.startswith("/") or (
            self.path_prefix != "/" and self.path_prefix.endswith("/")
        ):
            raise ValueError(f"invalid path prefix for {self.source_id}")
        if public.path.rstrip("/") != self.path_prefix.rstrip("/"):
            raise ValueError(f"public base URL does not match path prefix for {self.source_id}")
        if (
            self.output_directory is None
            and self.source_kind is not SourceKind.DJANGO_ROUTE_CONTRACTS
        ):
            raise ValueError(f"missing generated output directory for {self.source_id}")
        if (
            self.output_directory is not None
            and self.source_kind is SourceKind.DJANGO_ROUTE_CONTRACTS
        ):
            raise ValueError(
                f"route-only source cannot declare generated output for {self.source_id}"
            )
        if (
            self.build_tool_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.build_tool_sha256) is None
        ):
            raise ValueError(f"invalid build-tool digest for {self.source_id}")
        for contract in self.machine_contracts:
            if not contract.startswith("/"):
                raise ValueError(f"invalid machine contract for {self.source_id}")


PINNED_LEGACY_SOURCES = (
    PinnedLegacySource(
        source_id="dtc-main-site",
        repository="https://github.com/DataTalksClub/datatalksclub.github.io.git",
        revision="ee43d3fa0929faf691178d79f19528e6f15a83e5",
        public_base_url="https://datatalks.club/",
        path_prefix="/",
        source_kind=SourceKind.RUSTKYLL_RELEASE,
        output_directory="_site",
        build_tool="rustkyll-linux-amd64",
        build_tool_version="v0.4.6",
        build_tool_sha256=RUSTKYLL_0_4_6_LINUX_AMD64_SHA256,
        machine_contracts=(
            "/robots.txt",
            "/sitemap.xml",
            "/feed.xml",
            "/articles.html",
            "/podcast.html",
            "/events.html",
            "/books.html",
            "/people.html",
            "/tools.html",
        ),
    ),
    PinnedLegacySource(
        source_id="dtc-docs",
        repository="https://github.com/DataTalksClub/docs.git",
        revision="3f23e006ffdaa498bbc69697408853b6f5eb37dc",
        public_base_url="https://datatalks.club/docs",
        path_prefix="/docs",
        source_kind=SourceKind.RUSTKYLL_RELEASE,
        output_directory="_site",
        build_tool="rustkyll-linux-amd64",
        # The pinned deploy workflow and README use v0.4.6.  The source Makefile says v0.4.7;
        # that source-vs-deploy difference is intentionally recorded in the provenance report.
        build_tool_version="v0.4.6",
        build_tool_sha256=RUSTKYLL_0_4_6_LINUX_AMD64_SHA256,
        deterministic_overrides=("deploy-workflow-version-over-makefile-v0.4.7",),
        machine_contracts=(
            "/docs/robots.txt",
            "/docs/sitemap.xml",
            "/docs/assets/js/search-data.json",
        ),
    ),
    PinnedLegacySource(
        source_id="dtc-faq",
        repository="https://github.com/DataTalksClub/faq.git",
        revision="c8da1deea9e24945922702994de101dd90a5380a",
        public_base_url="https://datatalks.club/faq",
        path_prefix="/faq",
        source_kind=SourceKind.FAQ_PYTHON,
        output_directory="_site",
        build_tool="uv-run-frozen-website-generator",
        build_tool_version="Python 3.13 + website/uv.lock",
        build_tool_sha256=FAQ_WEBSITE_UV_LOCK_SHA256,
        deterministic_overrides=(f"generation_time={FAQ_FROZEN_GENERATION_TIME}",),
        machine_contracts=(
            "/faq/json/courses.json",
            "/faq/json/ai-dev-tools-zoomcamp.json",
            "/faq/json/data-engineering-zoomcamp.json",
            "/faq/json/llm-zoomcamp.json",
            "/faq/json/machine-learning-zoomcamp.json",
            "/faq/json/mlops-zoomcamp.json",
            "/faq/json/stock-markets-analytics-zoomcamp.json",
        ),
    ),
    PinnedLegacySource(
        source_id="dtc-podwiki",
        repository="https://github.com/DataTalksClub/podwiki.git",
        revision="988b79d0d655bf4755945c3118544cb9e0dbead6",
        public_base_url="https://datatalks.club/podwiki",
        path_prefix="/podwiki",
        source_kind=SourceKind.RUSTKYLL_PYPI,
        output_directory="_site",
        build_tool="rustkyll-pypi",
        build_tool_version="0.5.0",
        machine_contracts=(
            "/podwiki/robots.txt",
            "/podwiki/sitemap.xml",
            "/podwiki/graph/graph.json",
            "/podwiki/search/search-corpus.json",
            "/podwiki/search/?q=machine+learning",
            "/podwiki/search/?q=machine+learning&level=section",
            "/podwiki/search/?q=machine+learning&document_type=page",
            "/podwiki/search/?q=machine+learning&document_type=section",
            "/podwiki/graph/#topic%3Allms",
        ),
    ),
    PinnedLegacySource(
        source_id="dtc-course-platform",
        repository="https://github.com/DataTalksClub/course-management-platform.git",
        revision="98a235283904b4ef9ad29e196298540756cf1bcc",
        public_base_url="https://courses.datatalks.club/",
        path_prefix="/",
        source_kind=SourceKind.DJANGO_ROUTE_CONTRACTS,
        output_directory=None,
        build_tool="copied-django-urlconf",
        build_tool_version="issue-30-adoption",
        deterministic_overrides=("authenticated-production-probes-not-performed",),
        machine_contracts=(
            "/",
            "/robots.txt",
            "/accounts/login/",
            "/accounts/settings/",
            "/accounts/email/",
            "/accounts/password/reset/",
            "/accounts/settings/email-preferences/",
            "/accounts/toggle-dark-mode/",
            "/accounts/settings/toggle/",
            "/accounts/settings/timezone/",
            "/accounts/stop-impersonating/",
            "/api/health/",
            "/api/openapi.json",
            "/api/courses/",
            "/api/registration-campaigns/",
            "/api/datamailer/send-audits",
            "/api/datamailer/events",
            "/cadmin/",
            "/cadmin/campaigns/new/",
            "/cadmin/datamailer/",
            "/cadmin/datamailer/events/",
            "/cadmin/cloudwatch/",
        ),
    ),
)


def pinned_source(source_id: str) -> PinnedLegacySource:
    try:
        return {source.source_id: source for source in PINNED_LEGACY_SOURCES}[source_id]
    except KeyError as error:
        raise KeyError(f"unknown pinned legacy source: {source_id}") from error


def validate_pinned_sources() -> None:
    seen: set[str] = set()
    for source in PINNED_LEGACY_SOURCES:
        source.validate()
        if source.source_id in seen:
            raise ValueError(f"duplicate pinned source id: {source.source_id}")
        seen.add(source.source_id)


validate_pinned_sources()


def generated_public_path(source: PinnedLegacySource, generated_path: str) -> str:
    """Map one generated file path to its exact mounted public path."""

    if source.output_directory is None:
        raise ValueError("route-contract sources do not have generated files")
    relative = PurePosixPath(generated_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("generated path must be a safe relative path")
    if relative.name == "index.html":
        route = "/".join(relative.parts[:-1])
        suffix = f"/{route}/" if route else "/"
    else:
        suffix = f"/{relative.as_posix()}"
    if source.path_prefix == "/":
        return suffix
    return f"{source.path_prefix}{suffix}"


def generated_contract_kind(generated_path: str) -> str:
    suffix = PurePosixPath(generated_path).suffix.casefold()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json":
        return "json"
    if suffix in {".xml", ".rss", ".atom"}:
        return "xml"
    if suffix in {".txt", ".ics", ".yaml", ".yml"}:
        return "text"
    return "asset"
