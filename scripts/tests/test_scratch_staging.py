"""Whole-snapshot staging copies belong in the project-local, gitignored `.tmp/`.

Both of these services copy an entire database snapshot before reading it.  A
system temporary directory is shared with every other user and process on the
host, which is not where a copy of production data goes.
"""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


class ScratchStagingTests(TestCase):
    """Whole-snapshot staging copies belong in the project-local, gitignored .tmp/."""

    def test_the_cmp_snapshot_stages_below_the_repository_tmp(self) -> None:
        from courses.services.local_cmp_content_import import STAGING_ROOT

        self.assertTrue(STAGING_ROOT.is_relative_to(PROD_ROOT.parents[1]))
        self.assertEqual(STAGING_ROOT.name, ".tmp")

    def test_the_content_artifact_transport_stages_below_the_repository_tmp(self) -> None:
        from courses.services.development_content_transport import _EPHEMERAL_STAGING_ROOT

        repository_tmp = PROD_ROOT.parents[1] / ".tmp"
        self.assertTrue(_EPHEMERAL_STAGING_ROOT.is_relative_to(repository_tmp))

    def test_the_transport_staging_root_is_private(self) -> None:
        """A shared root would need a sticky bit; a private one carries no shared write bit."""

        import stat

        from courses.services.development_content_transport import (
            _validated_ephemeral_staging_root,
        )

        root = _validated_ephemeral_staging_root()
        mode = stat.S_IMODE(root.lstat().st_mode)
        self.assertFalse(mode & (stat.S_IWGRP | stat.S_IWOTH))
