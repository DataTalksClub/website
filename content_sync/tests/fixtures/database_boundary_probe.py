"""Planted `SimpleTestCase` classes the boundary analysis must catch.

This file is a *fixture*, not a test module. It lives under ``fixtures/`` and is
never named ``test_*``, so neither the Django runner nor pytest collects it and
nothing here ever executes. ``content_sync/tests/test_database_boundary.py``
parses it with the same analysis it runs over the real test package and asserts
each planted class is found, so the guard is never only observed green.

The four planted defects reproduce the shapes the catalogue migration created in
``content_sync``: a direct catalogue read, an indirect one through a
module-level helper and two application modules, a bare model manager, and a
reader named only by an import inside the test body -- the idiom
``test_course_repository_transport_parity.py:237`` already uses, and the one
shape a module-level namespace walk misses. The fifth planted class is honestly
row-free and must stay unreported, because a guard that flags everything says
nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.test import SimpleTestCase

from content.models import ContentSource


def _planted_reader(root: Path) -> object:
    """A module-level helper reaching the catalogue through one more hop,
    the shape a real adapter-then-parity call chain had (#324)."""

    return _planted_reader_inner(root)


def _planted_reader_inner(root: Path) -> object:
    from content import catalogue

    return catalogue.articles()


def _planted_helper(root: Path) -> object:
    """A module-level helper, the shape ``_overlay_diagnostic`` has."""

    return _planted_reader(root)


class PlantedDirectCatalogueReaderTests(SimpleTestCase):
    def test_reads_the_catalogue_in_the_test_body(self) -> None:
        from content import catalogue

        self.assertIsInstance(catalogue.articles(), tuple)


class PlantedIndirectCatalogueReaderTests(SimpleTestCase):
    def helper(self) -> object:
        return _planted_helper(Path("/nonexistent"))

    def test_reaches_the_catalogue_through_a_sibling_and_a_helper(self) -> None:
        self.assertIsNotNone(self.helper())


class PlantedManagerAccessTests(SimpleTestCase):
    def test_touches_a_model_manager(self) -> None:
        self.assertEqual(ContentSource.objects.count(), 0)


class PlantedFunctionBodyImportTests(SimpleTestCase):
    def test_reads_the_catalogue_through_a_function_body_import(self) -> None:
        # Bound here and nowhere else in this module (a function defined in
        # this module would already be in its own namespace), so the analysis
        # can only follow it by reading imports inside a function body.
        from content.catalogue import articles

        self.assertIsInstance(articles(), tuple)


class PlantedRowFreeTests(SimpleTestCase):
    def test_hashes_bytes_and_nothing_else(self) -> None:
        self.assertEqual(len(hashlib.sha256(b"probe").hexdigest()), 64)
