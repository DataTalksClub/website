"""Hydrating media never guesses where the bytes come from.

``--source github`` reads 438 of the records out of
``DataTalksClub/datatalksclub.github.io``, the repository this codebase has to work
without.  It used to be the default, so a fresh clone or a bucket re-hydration reached
for it by accident.  No other source suits every machine either, so the command refuses
and says what each one needs rather than picking one.
"""

from __future__ import annotations

import contextlib
import io
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.prod.sync_public_media_hydrate import _parser, main


class HydrateSourceSelectionTests(SimpleTestCase):
    def test_the_source_has_no_default(self) -> None:
        defaults = _parser().parse_args([])
        self.assertIsNone(defaults.source)

    def test_no_source_refuses_and_names_every_alternative(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main([])
        self.assertEqual(raised.exception.code, 2)
        message = stderr.getvalue()
        for source in ("--source checkout", "--source store", "--source github"):
            self.assertIn(source, message)
        self.assertIn("datatalksclub.github.io", message)

    def test_no_source_reaches_no_network(self) -> None:
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")) as opened:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main([])
        opened.assert_not_called()
