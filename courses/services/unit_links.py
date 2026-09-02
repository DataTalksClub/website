"""Resolve repository-relative markdown links to curriculum URLs."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

from django.urls import reverse

from courses.models import Homework, Unit

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

    def resolve_target(target: str) -> str | None:
        if not target or target.startswith("#") or _is_external(target):
            return None

        path_part, separator, fragment = target.partition("#")
        if not path_part.lower().endswith(".md") or path_part.startswith("/"):
            return None

        resolved_path = posixpath.normpath(
            posixpath.join(current_directory, path_part)
        )
        target_unit = units_by_path.get(resolved_path)
        if target_unit is not None:
            suffix = f"#{fragment}" if separator else ""
            return f"{_unit_url(target_unit)}{suffix}"

        suffix = f"#{fragment}" if separator else ""
        basename = posixpath.basename(resolved_path).casefold()
        if basename == "readme.md":
            target_module = modules_by_directory.get(posixpath.dirname(resolved_path))
            if target_module is not None:
                return f"{_module_url(target_module)}{suffix}"

        target_homework = homework_by_instructions.get(resolved_path)
        if target_homework is None and basename == "homework.md":
            target_homework = homework_by_module_directory.get(posixpath.dirname(resolved_path))
        if target_homework is not None:
            return f"{_homework_url(target_homework)}{suffix}"

        return None

    def replace_link(match: re.Match[str]) -> str:
        rewritten = resolve_target(match.group("target"))
        if rewritten is None:
            return match.group(0)
        title = match.group("title") or ""
        return f'[{match.group("label")}]({rewritten}{title})'

    return _MARKDOWN_LINK_RE.sub(replace_link, markdown)


__all__ = ["rewrite_unit_markdown_links"]
