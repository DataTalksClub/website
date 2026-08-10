#!/usr/bin/env python3
"""Render the adopted course-platform route and command inventory."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.test")

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.core.management import get_commands, load_command_class  # noqa: E402
from django.urls import URLPattern, URLResolver  # noqa: E402

URL_SURFACES = (
    ("Accounts", "accounts.urls", "accounts/"),
    ("Compatibility API", "api.urls", "api/"),
    ("Studio Courses", "cadmin.urls", "studio/courses/"),
    ("Public courses", "courses.urls", ""),
)
SOURCE_APP_LABELS = ("accounts", "api", "cadmin", "courses", "data")
SOURCE_COMMAND_APPS = {"accounts", "api", "cadmin", "courses", "data"}
CONVERTER_TOKEN = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")


@dataclass(frozen=True)
class RouteEntry:
    surface: str
    module: str
    route: str
    name: str
    callback: str

    def example_path(self) -> str:
        def replace(match: re.Match[str]) -> str:
            if match.group("converter") == "int":
                return "1"
            return "sample"

        return "/" + CONVERTER_TOKEN.sub(replace, self.route)


@dataclass(frozen=True)
class CommandEntry:
    name: str
    app: str
    help: str


def _callback_name(pattern: URLPattern) -> str:
    callback = pattern.callback
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        return f"{view_class.__module__}.{view_class.__name__}"
    callback_name = getattr(callback, "__name__", callback.__class__.__name__)
    return f"{callback.__module__}.{callback_name}"


def _walk_patterns(
    patterns: list[URLPattern | URLResolver],
    *,
    surface: str,
    module: str,
    prefix: str,
) -> list[RouteEntry]:
    entries = []
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            entries.extend(
                _walk_patterns(
                    pattern.url_patterns,
                    surface=surface,
                    module=module,
                    prefix=route,
                )
            )
            continue
        entries.append(
            RouteEntry(
                surface=surface,
                module=module,
                route=route,
                name=pattern.name or "",
                callback=_callback_name(pattern),
            )
        )
    return entries


def route_entries() -> list[RouteEntry]:
    entries = []
    for surface, module_name, mount in URL_SURFACES:
        module = importlib.import_module(module_name)
        entries.extend(
            _walk_patterns(
                module.urlpatterns,
                surface=surface,
                module=module_name,
                prefix=mount,
            )
        )
    return entries


def command_entries() -> list[CommandEntry]:
    entries = []
    for name, app_label in sorted(get_commands().items()):
        if app_label not in SOURCE_COMMAND_APPS:
            continue
        command = load_command_class(app_label, name)
        help_text = " ".join((command.help or "").split())
        entries.append(CommandEntry(name=name, app=app_label, help=help_text))
    return entries


def migration_names(app_label: str) -> list[str]:
    migration_dir = REPO_ROOT / app_label / "migrations"
    return sorted(path.stem for path in migration_dir.glob("[0-9]*.py"))


def _table_value(value: str) -> str:
    escaped = value.replace("|", "\\|")
    return f"`{escaped}`" if escaped else "—"


def render_inventory() -> str:
    routes = route_entries()
    commands = command_entries()
    route_counts = {
        surface: sum(entry.surface == surface for entry in routes)
        for surface, _module, _mount in URL_SURFACES
    }
    lines = [
        "# Adopted course-platform behavior inventory",
        "",
        "This file is generated from the pinned URLconfs, Django app registry, migration files,",
        "and management-command registry by `scripts/render_course_platform_inventory.py`.",
        "`core.tests.test_course_platform_adoption` smoke-resolves every listed route through the",
        "unified root URLconf, loads every listed command, checks the original app/migration",
        "identities, and verifies all copied destination checksums.",
        "",
        "## Surface summary",
        "",
        "| Surface | Mounted URLconf | Routes |",
        "| --- | --- | ---: |",
    ]
    for surface, module, _mount in URL_SURFACES:
        lines.append(f"| {surface} | `{module}` | {route_counts[surface]} |")
    lines.extend(
        [
            f"| **Total** |  | **{len(routes)}** |",
            "",
            "The compatibility API and Studio Courses rows below retain the complete adopted",
            "behavior; issue #115 changes the management adapter names and mount, not its logic.",
            "",
            "## Routes",
            "",
        ]
    )
    for surface, module, _mount in URL_SURFACES:
        lines.extend(
            [
                f"### {surface}",
                "",
                f"Mounted from `{module}`.",
                "",
                "| Route | Name | Callback |",
                "| --- | --- | --- |",
            ]
        )
        for route_entry in (row for row in routes if row.surface == surface):
            lines.append(
                f"| {_table_value('/' + route_entry.route)} | "
                f"{_table_value(route_entry.name)} | "
                f"{_table_value(route_entry.callback)} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Management commands",
            "",
            "| Command | Owning app | Registered help |",
            "| --- | --- | --- |",
        ]
    )
    for command_entry in commands:
        lines.append(
            f"| {_table_value(command_entry.name)} | {_table_value(command_entry.app)} | "
            f"{command_entry.help or '—'} |"
        )
    lines.extend(
        [
            "",
            "## Preserved app and migration identities",
            "",
            "| App label | App module | Original numbered migrations |",
            "| --- | --- | --- |",
        ]
    )
    for app_label in SOURCE_APP_LABELS:
        app_config = apps.get_app_config(app_label)
        migrations = migration_names(app_label)
        migration_text = ", ".join(migrations) if migrations else "none"
        lines.append(
            f"| {_table_value(app_label)} | {_table_value(app_config.name)} | "
            f"{_table_value(migration_text)} |"
        )
    lines.extend(
        [
            "",
            "The original numbered graph remains an unchanged prefix. Additive target migrations",
            "extend the adopted identity only through reviewed product issues. Migration squashing",
            "remains deferred until the production-like parity gate in `migration-squash-gate.md`",
            "can be exercised.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", type=Path, metavar="PATH")
    group.add_argument("--check", type=Path, metavar="PATH")
    args = parser.parse_args()

    rendered = render_inventory()
    if args.write:
        output = args.write if args.write.is_absolute() else REPO_ROOT / args.write
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return 0
    if args.check:
        output = args.check if args.check.is_absolute() else REPO_ROOT / args.check
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"course-platform inventory is stale: {output}")
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
