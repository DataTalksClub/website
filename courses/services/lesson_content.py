"""Pure helpers for source-managed lesson metadata and links.

The course repository stores Markdown-relative links because that is convenient
when reading the checkout on GitHub.  The website renders the same files under
cohort routes, so the renderer supplies a source-path-to-public-URL map and
rewrites only links that are known curriculum units/modules.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit, urlunsplit

_MARKDOWN_LINK_RE = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\n]+)\)")
_FENCED_CODE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*(?:\n|$)")


def canonical_module_path(*, course_slug: str, cohort_identifier: str, module_slug: str) -> str:
    """Return the public route for one module."""

    return (
        f"/courses/{quote(course_slug, safe='')}/{quote(cohort_identifier, safe='')}"
        f"/modules/{quote(module_slug, safe='')}"
    )


def canonical_unit_path(
    *, course_slug: str, cohort_identifier: str, module_slug: str, unit_slug: str
) -> str:
    """Return the public route for one unit."""

    module_path = canonical_module_path(
        course_slug=course_slug,
        cohort_identifier=cohort_identifier,
        module_slug=module_slug,
    )
    return f"{module_path}/{quote(unit_slug, safe='')}"


def canonical_github_source_url(*, repository_url: str, commit_sha: str, source_path: str) -> str:
    """Build an immutable GitHub URL for a source-provenance path."""

    parsed = urlsplit(repository_url)
    repository_path = parsed.path.rstrip("/").removesuffix(".git")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{repository_path}/blob/{quote(commit_sha, safe='')}/{quote(source_path, safe='/')}",
            "",
            "",
        )
    )


def rewrite_relative_lesson_links(
    markdown: str,
    *,
    current_source_path: str,
    public_urls_by_source_path: Mapping[str, str],
) -> str:
    """Rewrite known relative Markdown lesson links to canonical public URLs.

    Unknown files, external links, anchors, images, and links inside fenced code
    blocks are left untouched.  Query strings and fragments are preserved after
    a known target is rewritten.
    """

    if not markdown or not public_urls_by_source_path:
        return markdown

    fenced_ranges = tuple(
        (match.start(), match.end()) for match in _FENCED_CODE_RE.finditer(markdown)
    )

    def replace(match: re.Match[str]) -> str:
        if any(start <= match.start() < end for start, end in fenced_ranges):
            return match.group(0)

        target = match.group(2).strip()
        if target.startswith("<") and ">" in target:
            target_value, suffix = target[1:].split(">", 1)
            suffix = ">" + suffix
        else:
            target_value, separator, title = target.partition(" ")
            suffix = f"{separator}{title}" if separator else ""

        parsed = urlsplit(target_value)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match.group(0)

        resolved = posixpath.normpath(str(PurePosixPath(current_source_path).parent / parsed.path))
        if resolved == "." or resolved.startswith("../"):
            return match.group(0)

        public_url = public_urls_by_source_path.get(resolved)
        if public_url is None:
            return match.group(0)

        rewritten = urlunsplit(("", "", public_url, parsed.query, parsed.fragment))
        return f"[{match.group(1)}]({rewritten}{suffix})"

    return _MARKDOWN_LINK_RE.sub(replace, markdown)


__all__ = [
    "canonical_github_source_url",
    "canonical_module_path",
    "canonical_unit_path",
    "rewrite_relative_lesson_links",
]
