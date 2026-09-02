"""Resolve repository-relative unit assets to public upstream URLs.

Course units are imported verbatim from a course repository, so their bodies
reference images the way the repository does: ``images/thumbnail-1-01.jpg`` next
to the lesson file.  This site does not host those bytes.  Article and wiki
images take a different route -- ``content_sync`` downloads them into the
projection media store and rewrites every source to a site-root ``/images/...``
path -- but no equivalent asset pipeline exists for course repositories.  Until
one does, the honest resolution is the upstream raw URL for the same commit's
branch.

A reference that cannot be resolved that way is removed rather than rendered as
a broken picture, so a unit never ships an image box that can only 404.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from courses.models import Unit

DEFAULT_REPOSITORY_BRANCH = "main"
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_RAW_GITHUB_BASE = "https://raw.githubusercontent.com"

_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_HTML_IMAGE_SOURCE_RE = re.compile(
    r"""(?P<prefix>\bsrc\s*=\s*)(?P<quote>["'])(?P<value>[^"']*)(?P=quote)""",
    re.IGNORECASE,
)
_HTML_IMAGE_ALT_RE = re.compile(r"\balt\s*=", re.IGNORECASE)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]"
    r"\((?P<target>[^)\s]+)"
    r'(?P<title>\s+"[^"]*")?'
    r"\)"
)
_ABSOLUTE_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:")


@dataclass(frozen=True, slots=True)
class UnitRepository:
    """The upstream repository coordinates behind one imported unit."""

    base_url: str
    """``https://github.com/owner/name`` with no trailing slash."""

    repository_path: str
    """``owner/name`` with no surrounding slashes."""

    branch: str
    source_path: str
    is_github: bool

    @property
    def source_directory(self) -> str:
        directory = posixpath.dirname(self.source_path)
        return "" if directory == "." else directory

    def edit_url(self) -> str:
        """Return the upstream edit URL for the unit's own source file."""

        return (
            f"{self.base_url}/edit/"
            f"{quote(self.branch, safe='/')}/"
            f"{quote(self.source_path, safe='/')}"
        )

    def raw_url(self, repository_path: str) -> str:
        """Return the upstream raw URL for one repository-root-relative path."""

        return (
            f"{_RAW_GITHUB_BASE}/{self.repository_path}/"
            f"{quote(self.branch, safe='/')}/"
            f"{quote(repository_path, safe='/')}"
        )


def unit_repository(unit: Unit) -> UnitRepository | None:
    """Return the upstream repository for a unit, or ``None`` when unknown."""

    cohort = unit.module.cohort
    course_family = cohort.course
    repository_url = getattr(cohort, "github_repo_url", "") or getattr(
        course_family, "github_repo_url", ""
    )
    source_path = (unit.source_path or "").strip("/")
    if not repository_url or not source_path:
        return None

    parsed = urlsplit(repository_url.strip())
    repository_path = parsed.path.strip("/").removesuffix(".git")
    if not parsed.scheme or not parsed.netloc or not repository_path:
        return None

    branch = (
        getattr(cohort, "repository_branch", "")
        or getattr(course_family, "repository_branch", "")
        or DEFAULT_REPOSITORY_BRANCH
    )
    return UnitRepository(
        base_url=urlunsplit((parsed.scheme, parsed.netloc, f"/{repository_path}", "", "")),
        repository_path=repository_path,
        branch=str(branch),
        source_path=source_path,
        is_github=parsed.netloc.lower() in _GITHUB_HOSTS,
    )


def _resolve_image_source(source: str, repository: UnitRepository | None) -> str | None:
    """Return the public URL for one image reference, or ``None`` to drop it."""

    candidate = source.strip()
    if not candidate:
        return None
    if candidate.lower().startswith(_ABSOLUTE_PREFIXES):
        return candidate
    if candidate.startswith("#"):
        return None
    # ``raw.githubusercontent.com`` is the only upstream raw layout this code
    # knows.  Guessing one for another host would ship a broken picture.
    if repository is None or not repository.is_github:
        return None

    path_part = candidate.partition("#")[0].partition("?")[0]
    if not path_part:
        return None

    base_directory = "" if path_part.startswith("/") else repository.source_directory
    resolved = posixpath.normpath(posixpath.join(base_directory, unquote(path_part.lstrip("/"))))
    if resolved.startswith("..") or resolved in {".", "/"}:
        return None
    return repository.raw_url(resolved.lstrip("/"))


def _rewrite_html_image(match: re.Match[str], repository: UnitRepository | None, alt: str) -> str:
    tag = match.group(0)
    source_match = _HTML_IMAGE_SOURCE_RE.search(tag)
    if source_match is None:
        return ""

    resolved = _resolve_image_source(source_match.group("value"), repository)
    if resolved is None:
        return ""

    quote_character = source_match.group("quote")
    rewritten = (
        f"{tag[: source_match.start()]}"
        f"{source_match.group('prefix')}{quote_character}{resolved}{quote_character}"
        f"{tag[source_match.end() :]}"
    )
    if _HTML_IMAGE_ALT_RE.search(rewritten):
        return rewritten
    # Course repositories routinely omit ``alt``.  A thumbnail that is the only
    # content of a link would otherwise leave that link without an accessible
    # name, so the unit's own title stands in as the description.
    escaped_alt = alt.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return f'{rewritten[:-1].rstrip("/").rstrip()} alt="{escaped_alt}">'


def rewrite_unit_image_sources(markdown: str, unit: Unit) -> str:
    """Rewrite repository-relative image sources in one unit's Markdown."""

    if not markdown:
        return markdown

    repository = unit_repository(unit)
    alt_fallback = unit.title or "Course illustration"

    def replace_markdown_image(match: re.Match[str]) -> str:
        resolved = _resolve_image_source(match.group("target"), repository)
        if resolved is None:
            return match.group("alt")
        title = match.group("title") or ""
        return f"![{match.group('alt')}]({resolved}{title})"

    def replace_html_image(match: re.Match[str]) -> str:
        return _rewrite_html_image(match, repository, alt_fallback)

    rewritten = _MARKDOWN_IMAGE_RE.sub(replace_markdown_image, markdown)
    return _HTML_IMAGE_RE.sub(replace_html_image, rewritten)


__all__ = [
    "DEFAULT_REPOSITORY_BRANCH",
    "UnitRepository",
    "rewrite_unit_image_sources",
    "unit_repository",
]
