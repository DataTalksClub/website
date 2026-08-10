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
RUSTKYLL_0_4_10_LINUX_AMD64_URL = (
    "https://github.com/alexeygrigorev/rustkyll/releases/download/v0.4.10/rustkyll-linux-amd64"
)
RUSTKYLL_0_4_10_LINUX_AMD64_SHA256 = (
    "ab96b800eb8427591841232ed2d0619f011b639200df6b4514ac9680caa6130e"
)
RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_URL = (
    "https://files.pythonhosted.org/packages/32/f4/"
    "9cae847680982c09346f8db66568a9ecb11d2e8de411c9829c7c8e2c4415/"
    "rustkyll-0.5.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
)
RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_SHA256 = (
    "348c622cac08cdd2361c4300161b7da34b7f7162bf0ad3d9fd9a0cd053f54a8e"
)
RUSTKYLL_0_5_3_LINUX_AMD64_BINARY_SHA256 = (
    "c8c2e6c732ecc224c28c170782114980b4707514835e7f587293f78bd38f2fba"
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
    build_tool_url: str | None = None
    build_tool_binary_sha256: str | None = None
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
        for digest in (self.build_tool_sha256, self.build_tool_binary_sha256):
            if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(f"invalid build-tool digest for {self.source_id}")
        if self.source_kind in {SourceKind.RUSTKYLL_RELEASE, SourceKind.RUSTKYLL_PYPI}:
            if (
                self.build_tool_sha256 is None
                or self.build_tool_url is None
                or self.build_tool_binary_sha256 is None
            ):
                raise ValueError(f"missing pinned build-tool artifact for {self.source_id}")
        elif self.build_tool_url is not None or self.build_tool_binary_sha256 is not None:
            raise ValueError(f"unexpected build-tool artifact for {self.source_id}")
        if self.build_tool_url is not None:
            build_tool_url = urlsplit(self.build_tool_url)
            if (
                build_tool_url.scheme != "https"
                or build_tool_url.hostname not in {"github.com", "files.pythonhosted.org"}
                or build_tool_url.username is not None
                or build_tool_url.password is not None
                or build_tool_url.query
                or build_tool_url.fragment
                or not PurePosixPath(build_tool_url.path).name
            ):
                raise ValueError(f"invalid build-tool URL for {self.source_id}")
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
        build_tool_version="v0.4.10",
        build_tool_sha256=RUSTKYLL_0_4_10_LINUX_AMD64_SHA256,
        build_tool_url=RUSTKYLL_0_4_10_LINUX_AMD64_URL,
        build_tool_binary_sha256=RUSTKYLL_0_4_10_LINUX_AMD64_SHA256,
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
        # Compatibility regeneration deliberately uses the reproducibility-fix release instead
        # of the source Makefile's historical v0.4.7 pin. The override remains explicit in the
        # provenance report while generated compatibility behavior stays byte-stable.
        build_tool_version="v0.4.10",
        build_tool_sha256=RUSTKYLL_0_4_10_LINUX_AMD64_SHA256,
        build_tool_url=RUSTKYLL_0_4_10_LINUX_AMD64_URL,
        build_tool_binary_sha256=RUSTKYLL_0_4_10_LINUX_AMD64_SHA256,
        deterministic_overrides=("compatibility-release-over-makefile-v0.4.7",),
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
        build_tool_version="0.5.3",
        build_tool_sha256=RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_SHA256,
        build_tool_url=RUSTKYLL_0_5_3_PYPI_LINUX_AMD64_URL,
        build_tool_binary_sha256=RUSTKYLL_0_5_3_LINUX_AMD64_BINARY_SHA256,
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
