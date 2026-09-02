"""Resolve repository-relative markdown links to curriculum or upstream URLs.

A lesson is imported verbatim, so its links are the repository's own filesystem
paths.  Where the target is a page this site publishes -- another unit, a
module, a homework -- the link becomes that page's canonical route.  Where it is
anything else the repository holds -- a notebook, a script, a directory, a
Markdown file that was never imported -- there is no page here, and leaving the
path alone points the reader at a URL under this domain that cannot exist.  Such
a target resolves to the file in the upstream repository instead.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

from django.urls import reverse

from courses.models import Homework, Unit
from courses.services.unit_assets import unit_repository

_MARKDOWN_LINK_RE = re.compile(
    r'(?<!\!)'
    r'\[(?P<label>[^\]]*)\]'
    r'\((?P<target>[^)\s]+)'
    r'(?P<title>\s+"[^"]*")?'
    r'\)'
)


def _is_external(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(
        ("http://", "https://", "mailto:", "tel:", "ftp://", "//")
    )


def _unit_url(unit: Unit) -> str:
    cohort = unit.module.cohort
    return reverse(
        "unit",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": unit.module.slug,
            "unit_slug": unit.slug,
        },
    )


def _module_url(module) -> str:
    cohort = module.cohort
    return reverse(
        "module",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": module.slug,
        },
    )


def _repository_root_relative(resolved_path: str) -> str:
    """Return a normalized path relative to the repository root."""

    return "" if resolved_path in {".", "/"} else resolved_path.lstrip("/")


def _homework_url(homework) -> str:
    cohort = homework.course
    return reverse(
        "homework",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_year": cohort.identifier,
            "homework_slug": homework.slug,
        },
    )


def rewrite_unit_markdown_links(markdown: str, unit: Unit) -> str:
    """Rewrite resolvable repository-relative ``.md`` links in unit content.

    Course repositories use filesystem paths such as ``04-dataset.md`` and
    ``../../02-vector-search/lessons/01-intro.md``. The website serves the
    same content through canonical unit routes, so keeping those source links
    in rendered Markdown makes navigation land on a 404. Source paths are the
    stable bridge between the repository and the imported curriculum graph.
    """

    if not markdown or not unit.source_path:
        return markdown

    cohort = unit.module.cohort
    units: Iterable[Unit] = (
        Unit.objects.filter(
            module__cohort=cohort,
            source_path__isnull=False,
        )
        .select_related("module", "module__cohort", "module__cohort__course")
    )
    units_by_path = {
        candidate.source_path: candidate
        for candidate in units
        if candidate.source_path
    }

    modules = cohort.modules.filter(source_path__isnull=False).select_related(
        "cohort", "cohort__course", "terminal_homework"
    )
    modules_by_directory = {
        posixpath.dirname(module.source_path): module
        for module in modules
        if module.source_path
    }
    # A lesson that links to ``homework.md`` means the page that publishes
    # those instructions.  The homework record states the Markdown file it was
    # imported from, which is the direct bridge; a repository that keeps the
    # module's homework beside its ``module.yaml`` -- the ML curriculum does --
    # is covered by the module's own directory, the same shape the README rule
    # above already uses.
    homework_by_instructions = {
        homework.instructions_source_path: homework
        for homework in Homework.objects.filter(course=cohort).select_related(
            "course", "course__course"
        )
        if homework.instructions_source_path
    }
    homework_by_module_directory = {
        directory: module.terminal_homework
        for directory, module in modules_by_directory.items()
        if module.terminal_homework_id
    }
    current_directory = posixpath.dirname(unit.source_path)

    repository = unit_repository(unit)

    def resolve_target(target: str) -> str | None:
        if not target or target.startswith("#") or _is_external(target):
            return None

        path_part, separator, fragment = target.partition("#")
        if not path_part or path_part.startswith("/"):
            return None

        resolved_path = posixpath.normpath(
            posixpath.join(current_directory, path_part)
        )
        # ``..`` that climbs out of the repository is not a link this site can
        # honestly resolve in any direction.
        if resolved_path.startswith(".."):
            return None
        suffix = f"#{fragment}" if separator else ""

        if path_part.lower().endswith(".md"):
            target_unit = units_by_path.get(resolved_path)
            if target_unit is not None:
                return f"{_unit_url(target_unit)}{suffix}"

            basename = posixpath.basename(resolved_path).casefold()
            if basename == "readme.md":
                target_module = modules_by_directory.get(posixpath.dirname(resolved_path))
                if target_module is not None:
                    return f"{_module_url(target_module)}{suffix}"

            target_homework = homework_by_instructions.get(resolved_path)
            if target_homework is None and basename == "homework.md":
                target_homework = homework_by_module_directory.get(
                    posixpath.dirname(resolved_path)
                )
            if target_homework is not None:
                return f"{_homework_url(target_homework)}{suffix}"

        # Everything else the repository points at -- a notebook, a script, a
        # dataset, a PDF, a whole ``code/`` directory, a Markdown file that was
        # never imported as a unit -- has no page on this site.  Serving the
        # source path unchanged makes it a link to a path under
        # ``datatalks.club`` that cannot exist, so send the reader to the file
        # where it does: the upstream repository.  Branch, not commit, for the
        # same reason unit images resolve to the branch tip.
        if repository is None or not repository.is_github:
            return None
        is_directory = path_part.rstrip().endswith(("/", "/.", "/..")) or path_part in {".", ".."}
        return (
            f"{repository.browse_url(_repository_root_relative(resolved_path), directory=is_directory)}"
            f"{suffix}"
        )

    def replace_link(match: re.Match[str]) -> str:
        rewritten = resolve_target(match.group("target"))
        if rewritten is None:
            return match.group(0)
        title = match.group("title") or ""
        return f'[{match.group("label")}]({rewritten}{title})'

    return _MARKDOWN_LINK_RE.sub(replace_link, markdown)


__all__ = ["rewrite_unit_markdown_links"]
