"""The editorial release helper is content-addressed and race-safe (REL-18).

``open_reviewed_release`` gave the three editorial importers
(``import_public_content``, ``import_faq``, ``import_docs``) one shared release
boundary: the release identity is the reviewed artifact's own digest (never a
sequence-derived fake Git SHA), identical artifacts replay to the existing
release instead of accumulating duplicate rows, and the sequence is allocated
under the source row lock.  All three importers were deleted once
``content/catalogue.py``, ``content/docs_projection.py`` and
``content/faq_data.py`` moved to reading ``content.models.SyncedDocument``
exclusively -- see ``_docs/architecture/database-only-content.md`` -- so the
generic, importer-independent coverage below is what remains.

The competing-activation test that used to drive a real ``import_faq.run`` end
to end (a full competing import winning the activation race between the loser's
release creation and its activation -- the exact interleaving audit REL-18
describes) was removed with it: there is no committed importer left that calls
``open_reviewed_release`` to exercise that race against.  If a future importer
adopts this helper again, that end-to-end race coverage should come back with
it.
"""

from __future__ import annotations

import hashlib

from django.test import TestCase

from content.models import ContentRelease
from scripts.prod.reviewed_release import (
    open_reviewed_release,
)


def expected_commit_sha(fingerprint: str) -> str:
    return hashlib.sha1(
        "\x00".join(("reviewed-test-v1", "reviewed-test-v1", fingerprint)).encode("utf-8")
    ).hexdigest()


def open_release(fingerprint: str, *, stable_id: str = "reviewed-release-test"):
    # A repository of its own: the (owner, name, branch) unique constraint is
    # shared with the real editorial sources the data migrations already
    # created, so the tests must not collide with those rows.
    return open_reviewed_release(
        stable_id=stable_id,
        display_name="Reviewed release test source",
        repository="DataTalksClub/reviewed-release-tests",
        path_allowlist=["/test/"],
        adapter_type="reviewed-test-v1",
        mount_path="/test/",
        parser_version="reviewed-test-v1",
        rendering_version="reviewed-test-v1",
        artifact_fingerprint=fingerprint,
        artifact_description={"fingerprint": fingerprint},
        request_provenance={"kind": "import", "source": "test"},
    )


class OpenReviewedReleaseTests(TestCase):
    def test_an_identical_artifact_replays_to_the_same_release(self) -> None:
        source_a, release_a, created_a = open_release("same-artifact")
        source_b, release_b, created_b = open_release("same-artifact")

        self.assertTrue(created_a)
        self.assertFalse(created_b)
        self.assertEqual(release_a.id, release_b.id)
        self.assertEqual(ContentRelease.objects.filter(source=source_a).count(), 1)
        self.assertEqual(source_a.id, source_b.id)

    def test_the_commit_sha_is_the_artifact_digest_not_the_sequence(self) -> None:
        _source, release, _created = open_release("digest-check")

        self.assertEqual(release.commit_sha, expected_commit_sha("digest-check"))
        # The old scheme synthesized the commit field from the allocation
        # counter; that value must never reappear (audit REL-18).
        self.assertNotEqual(release.commit_sha, f"{release.sequence:040x}")
        provenance = release.request_provenance
        self.assertEqual(provenance["commit_sha_origin"], "reviewed-artifact-digest")
        self.assertEqual(provenance["artifact_fingerprint"], "digest-check")

    def test_a_changed_artifact_allocates_a_fresh_increasing_sequence(self) -> None:
        _source, first, created_first = open_release("artifact-one")
        _source, second, created_second = open_release("artifact-two")

        self.assertTrue(created_first)
        self.assertTrue(created_second)
        self.assertNotEqual(first.commit_sha, second.commit_sha)
        self.assertEqual(second.sequence, first.sequence + 1)
        self.assertEqual(ContentRelease.objects.filter(source=first.source).count(), 2)

    def test_the_sequence_follows_releases_regardless_of_replays(self) -> None:
        _source, first, _created = open_release("stable-artifact")
        _source, replay, replayed = open_release("stable-artifact")
        _source, third, _created = open_release("unrelated-artifact")

        self.assertFalse(replayed)
        self.assertEqual(replay.sequence, first.sequence)
        self.assertEqual(third.sequence, first.sequence + 1)
