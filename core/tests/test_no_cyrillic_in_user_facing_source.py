"""No Cyrillic in the source this site ships to users.

DataTalks.Club is English-only.  Russian text has reached the platform before:
an unaudited import set event tags to "Академия", and Illustrator layer and
gradient IDs ("Слой_1", "Безымянный_градиент_13") sat inside the brand SVGs
served from the docs assets and the certificate.  Single Cyrillic homoglyphs
(a Cyrillic "с" for a Latin "c") are just as invisible in review, so every
character in a Unicode Cyrillic block fails, not only whole words.

Scanned, from the git index plus untracked non-ignored files:

* templates -- every file under a configured template directory: each
  ``settings.TEMPLATES[*]["DIRS"]`` entry (for example
  ``course_platform_templates/``) and each installed project app's
  ``templates/``, plus any other ``templates/`` directory as a backstop;
* static files -- every file under a ``static/`` directory, except ``vendor/``
  third-party bundles (their Unicode tables are not ours to edit);
* non-test Python -- application, script and migration code, but not tests,
  test support, or browser suites;
* ``email_templates/`` -- the transactional mail sources;
* ``content/`` -- docs assets and any markdown that becomes a page;
* data files that reach users -- ``.txt``/``.json``/``.yaml``/``.csv`` files
  inside an installed project app (``courses/countries.txt``, the generated
  display names in ``courses/random_names/``) and ``scripts/banners/`` (the
  social banner copy).

Database-owned content is guarded at its write path instead (for example
``events.admin_services`` refuses Cyrillic display tags).

``ALLOWED`` is the explicit allowlist, keyed by path with the reason the text
must exist.  It is empty: add an entry only for text that genuinely has to be
Cyrillic, and the test fails when an entry stops matching anything.
"""

from __future__ import annotations

import subprocess
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

from core.cyrillic import CYRILLIC_PATTERN

ROOT = Path(__file__).resolve().parents[2]

#: path -> reason.  Must stay minimal; see the module docstring.
ALLOWED: dict[str, str] = {}

BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".avif",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".zip",
        ".gz",
        ".mp4",
        ".webm",
    }
)
NON_SHIPPED_TOP_LEVEL = frozenset(
    {"_docs", "e2e", "playwright_tests", "test_support", "tests_ci", ".agents", ".claude"}
)
DATA_SUFFIXES = frozenset({".txt", ".json", ".yaml", ".yml", ".csv", ".tsv"})
#: Repository directories outside an app whose data files become public output.
EXTRA_DATA_ROOTS = ("scripts/banners/",)


def _relative_dir(path: str | Path) -> str | None:
    """``path`` as a repository-relative directory prefix, or None outside ROOT."""

    try:
        relative = Path(path).resolve().relative_to(ROOT)
    except ValueError:
        return None
    return f"{relative.as_posix()}/" if relative.parts else None


@cache
def app_roots() -> tuple[str, ...]:
    """Directories of the installed apps that live in this repository."""

    roots = {_relative_dir(config.path) for config in apps.get_app_configs()}
    return tuple(sorted(root for root in roots if root is not None))


def configured_template_dirs() -> list[Path]:
    """``settings.TEMPLATES[*]["DIRS"]``, typed."""

    dirs: list[Path] = []
    for engine in cast(list[dict[str, Any]], settings.TEMPLATES):
        dirs.extend(Path(directory) for directory in engine.get("DIRS", []))
    return dirs


@cache
def template_roots() -> tuple[str, ...]:
    """Every template directory Django is configured to load from, in this repo."""

    roots: set[str] = set()
    for directory in configured_template_dirs():
        relative = _relative_dir(directory)
        if relative is not None:
            roots.add(relative)
    for app_dir in app_roots():
        roots.add(f"{app_dir}templates/")
    return tuple(sorted(roots))


def _is_test_python(path: PurePosixPath) -> bool:
    name = path.name
    return (
        "tests" in path.parts[:-1]
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def is_user_facing(relative: str) -> bool:
    """Return whether ``relative`` (a repository path) is in the scanned scope."""

    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or parts[0] in NON_SHIPPED_TOP_LEVEL or "vendor" in parts:
        return False
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    if path.suffix == ".py":
        return not _is_test_python(path)
    if "tests" in parts[:-1]:
        return False
    if relative.startswith(template_roots()) or "templates" in parts[:-1]:
        return True
    if "static" in parts[:-1] or parts[0] in {"email_templates", "content"}:
        return True
    return path.suffix.lower() in DATA_SUFFIXES and relative.startswith(
        (*app_roots(), *EXTRA_DATA_ROOTS)
    )


def _scanned_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    files = []
    for item in listed.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8")
        if is_user_facing(relative) and (ROOT / relative).is_file():
            files.append(relative)
    return files


def _cyrillic_lines(relative: str) -> list[str]:
    text = (ROOT / relative).read_bytes().decode("utf-8", errors="replace")
    if CYRILLIC_PATTERN.search(text) is None:
        return []
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = CYRILLIC_PATTERN.search(line)
        if match is not None:
            start = max(match.start() - 30, 0)
            found.append(f"{relative}:{number}: ...{line[start : match.start() + 30]}...")
    return found


class NoCyrillicInUserFacingSourceTests(SimpleTestCase):
    def test_user_facing_source_contains_no_cyrillic(self) -> None:
        files = _scanned_files()
        offending: list[str] = []
        matched_allowlist: set[str] = set()
        for relative in files:
            lines = _cyrillic_lines(relative)
            if not lines:
                continue
            if relative in ALLOWED:
                matched_allowlist.add(relative)
                continue
            offending.extend(lines)

        self.assertEqual(
            offending,
            [],
            "Cyrillic characters found in user-facing source. The site is "
            "English-only: translate the text, replace homoglyphs with their "
            "Latin letters, or rename tool-generated IDs (keeping url(#...) and "
            "href references consistent).",
        )
        self.assertEqual(
            sorted(set(ALLOWED) - matched_allowlist),
            [],
            "Stale ALLOWED entries: these files no longer contain Cyrillic.",
        )

    def test_scope_covers_each_shipped_category(self) -> None:
        files = set(_scanned_files())
        for expected in (
            "content/docs_assets/images/brand-assets/svg/logo_main.svg",
            "courses/static/courses/certificate-dtc-logo.svg",
            "events/admin_services.py",
            "email_templates/certificate-ready.md",
            "courses/countries.txt",
            "courses/random_names/adjectives.txt",
            "courses/random_names/famous_people.txt",
            "scripts/banners/banners.json",
        ):
            self.assertIn(expected, files)
        # Template scope is derived from settings and the installed apps, not a
        # hardcoded list: every configured directory must contribute files.
        roots = template_roots()
        self.assertIn("course_platform_templates/", roots)
        self.assertIn("templates/", roots)
        for directory in configured_template_dirs():
            root = _relative_dir(directory)
            if root is not None and directory.is_dir():
                self.assertTrue(
                    any(f.startswith(root) for f in files),
                    f"no scanned files under configured template dir {root}",
                )
        self.assertTrue(any(f.startswith("course_platform_templates/") for f in files))
        self.assertTrue(any("/templates/" in f or f.startswith("templates/") for f in files))

    def test_scope_excludes_tests_vendor_and_docs(self) -> None:
        for path in (
            "events/tests/test_event_content.py",
            "core/tests/test_no_cyrillic_in_user_facing_source.py",
            "courses/static/courses/vendor/mermaid/10/katex-7aea786e.js",
            "_docs/PROCESS.md",
            "playwright_tests/test_accessibility.py",
            "conftest.py",
            "content/docs_assets/images/brand-assets/png/logo_main@3x.png",
            "ci/ownership.json",
            "deploy/gate_b_manifest.json",
        ):
            self.assertFalse(is_user_facing(path), path)
        for path in (
            "templates/base.html",
            "core/migrations/0001_initial.py",
            "scripts/prod/import_events.py",
            "content/docs_assets/images/brand-assets/svg/logo_main.svg",
            "email_templates/README.md",
            "course_platform_templates/any_page.html",
            "courses/countries.txt",
            "courses/random_names/famous_people.txt",
            "scripts/banners/banners.json",
        ):
            self.assertTrue(is_user_facing(path), path)

    def test_pattern_catches_words_and_single_homoglyphs(self) -> None:
        self.assertIsNotNone(CYRILLIC_PATTERN.search('<g id="Слой_1">'))
        self.assertIsNotNone(CYRILLIC_PATTERN.search("topiс"))
        self.assertIsNone(CYRILLIC_PATTERN.search("topic, café, Zoë, Ω"))
