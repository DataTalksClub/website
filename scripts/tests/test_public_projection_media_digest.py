"""The media-free complete-tree digest and its explicit manifest scope declaration."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from scripts.projection_build import public_projection_source as source_loader
from scripts.projection_build.public_projection_source import (
    DEFAULT_PROJECTION_ROOT as PROJECTION_ROOT,
)
from scripts.projection_build.public_projection_source import (
    EXPECTED_MEDIA_STORAGE_FIELDS,
    EXPECTED_TREE_DIGEST_SCOPE,
    _tree_sha256,
)
from scripts import build_public_projection as projection_builder
from scripts import repin_projection_digests as repin


def _sample_tree(root: Path) -> None:
    (root / "media" / "authors").mkdir(parents=True)
    (root / "wiki_assets").mkdir(parents=True)
    (root / "media" / "authors" / "portrait.jpg").write_bytes(b"portrait")
    (root / "wiki_assets" / "og-default.png").write_bytes(b"asset")
    (root / "media.json").write_text("[]", encoding="utf-8")
    (root / "manifest.json").write_text("{}", encoding="utf-8")


class MediaFreeTreeDigestTests(SimpleTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        _sample_tree(self.root)

    def test_the_runtime_and_the_builder_agree_on_the_digest(self) -> None:
        self.assertEqual(_tree_sha256(self.root), projection_builder._tree_sha256(self.root))

    def test_the_checked_projection_digest_is_recomputed_not_hand_typed(self) -> None:
        manifest = json.loads((PROJECTION_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["tree_sha256"], _tree_sha256(PROJECTION_ROOT))
        self.assertEqual(manifest["tree_sha256"], projection_builder._tree_sha256(PROJECTION_ROOT))
        self.assertEqual(manifest["tree_digest_scope"], EXPECTED_TREE_DIGEST_SCOPE)
        self.assertEqual(
            {key: manifest["media_storage"][key] for key in EXPECTED_MEDIA_STORAGE_FIELDS},
            EXPECTED_MEDIA_STORAGE_FIELDS,
        )
        # The declared media count is not hand-typed either: it must match the
        # manifest's own declared collection counts, whatever those currently are.
        self.assertEqual(manifest["media_storage"]["count"], manifest["counts"]["media"])

    def test_the_repin_utility_is_idempotent_over_the_checked_projection(self) -> None:
        self.assertEqual(repin.main(["--check"]), 0)

    def test_media_changes_do_not_move_the_digest(self) -> None:
        before = _tree_sha256(self.root)
        (self.root / "media" / "authors" / "portrait.jpg").write_bytes(b"different bytes")
        (self.root / "media" / "authors" / "added.png").write_bytes(b"added")
        self.assertEqual(_tree_sha256(self.root), before)
        (self.root / "media" / "authors" / "portrait.jpg").unlink()
        self.assertEqual(_tree_sha256(self.root), before)
        shutil.rmtree(self.root / "media")
        self.assertEqual(_tree_sha256(self.root), before)

    def test_artifact_and_wiki_asset_changes_do_move_the_digest(self) -> None:
        before = _tree_sha256(self.root)
        (self.root / "media.json").write_text('[{"record_key": "images/a.jpg"}]', encoding="utf-8")
        after_artifact = _tree_sha256(self.root)
        self.assertNotEqual(after_artifact, before)
        (self.root / "wiki_assets" / "og-default.png").write_bytes(b"changed asset")
        self.assertNotEqual(_tree_sha256(self.root), after_artifact)

    def test_the_manifest_itself_stays_outside_the_digest(self) -> None:
        before = _tree_sha256(self.root)
        (self.root / "manifest.json").write_text('{"tree_sha256": "x"}', encoding="utf-8")
        self.assertEqual(_tree_sha256(self.root), before)

    def test_a_symlink_under_media_still_fails_closed(self) -> None:
        (self.root / "media" / "authors" / "alias.jpg").symlink_to(
            self.root / "media" / "authors" / "portrait.jpg"
        )
        with self.assertRaises(ImproperlyConfigured):
            _tree_sha256(self.root)
        with self.assertRaises(projection_builder.ProjectionBuildError):
            projection_builder._tree_sha256(self.root)

    def test_a_symlink_outside_media_still_fails_closed(self) -> None:
        (self.root / "alias.json").symlink_to(self.root / "media.json")
        with self.assertRaises(ImproperlyConfigured):
            _tree_sha256(self.root)


class ManifestScopeDeclarationTests(SimpleTestCase):
    """A manifest that does not declare the media-free scope must fail closed."""

    def _artifact_only_copy(self) -> Path:
        """Copy the projection without ``media/``.

        The media objects are outside the digest, so an artifact-only copy reproduces
        the checked digest exactly while leaving the real tree untouched by a test that
        must mutate a manifest.
        """

        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "public_projection"
        shutil.copytree(PROJECTION_ROOT, root, ignore=shutil.ignore_patterns("media"))
        return root

    def _load_with_manifest(self, mutate) -> None:
        root = self._artifact_only_copy()
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        source_loader.load_checked_projection(root)

    def test_a_missing_scope_declaration_is_rejected(self) -> None:
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._load_with_manifest(lambda manifest: manifest.pop("tree_digest_scope"))
        self.assertIn("tree digest scope", str(caught.exception))

    def test_a_whole_tree_scope_declaration_is_rejected(self) -> None:
        def mutate(manifest):
            manifest["tree_digest_scope"] = "complete projection tree; excludes manifest.json"

        with self.assertRaises(ImproperlyConfigured) as caught:
            self._load_with_manifest(mutate)
        self.assertIn("tree digest scope", str(caught.exception))

    def test_a_missing_media_storage_block_is_rejected(self) -> None:
        with self.assertRaises(ImproperlyConfigured) as caught:
            self._load_with_manifest(lambda manifest: manifest.pop("media_storage"))
        self.assertIn("media storage", str(caught.exception))

    def test_a_drifted_media_storage_count_is_rejected(self) -> None:
        def mutate(manifest):
            manifest["media_storage"] = {**manifest["media_storage"], "count": 1_252}

        with self.assertRaises(ImproperlyConfigured):
            self._load_with_manifest(mutate)

    def test_the_accepted_manifest_still_loads(self) -> None:
        # Smoke test: the checked manifest loads without raising.
        source_loader.load_checked_projection()

class MediaArtifactDigestTests(SimpleTestCase):
    """The media artifact and the manifest that describes it stay bound."""

    def test_the_media_artifact_digest_still_matches_the_manifest(self) -> None:
        manifest = json.loads((PROJECTION_ROOT / "manifest.json").read_text(encoding="utf-8"))
        payload = (PROJECTION_ROOT / "media.json").read_bytes()
        self.assertEqual(manifest["artifacts"]["media.json"], hashlib.sha256(payload).hexdigest())
