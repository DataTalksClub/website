"""The editorial release helper is content-addressed and race-safe (REL-18).

``open_reviewed_release`` gives the three editorial importers one shared
release boundary: the release identity is the reviewed artifact's own digest
(never a sequence-derived fake Git SHA), identical artifacts replay to the
existing release instead of accumulating duplicate rows, and the sequence is
allocated under the source row lock.  The competing-activation test drives the
real ``import_faq.run`` end to end, with a full competing import winning the
activation race between the loser's release creation and its activation -- the
exact interleaving the audit describes -- and asserts the loser returns a
bounded superseded result instead of a raw traceback.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest.mock
from pathlib import Path

from django.test import TestCase

import scripts.prod
from content.models import ContentRelease, ContentSource
from scripts.prod.import_faq import REVIEWED_PATH
from scripts.prod.import_faq import run as run_faq_import
from scripts.prod.reviewed_release import (
    open_reviewed_release,
)

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


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


class CompetingActivationTests(TestCase):
    """The loser of an activation race gets a bounded, explained result.

    The migrated test database already carries the real editorial sources and
    the current reviewed artifact's release, so every run here imports a fresh
    mutation of that artifact: a fresh fingerprint is what makes a run create
    (and then try to activate) a genuinely new release.
    """

    def setUp(self) -> None:
        super().setUp()
        from content.faq_data import FAQ_SOURCE_STABLE_ID

        scratch = PROD_ROOT.parents[1] / ".tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="reviewed-release-race-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stable_id = FAQ_SOURCE_STABLE_ID

    def _mutated_payload(self, name: str) -> Path:
        payload = json.loads(REVIEWED_PATH.read_text(encoding="utf-8"))
        payload["courses"][0]["name"] = f"{payload['courses'][0]['name']} ({name})"
        path = self.root / f"faq-{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def faq_release_count(self) -> int:
        return ContentRelease.objects.filter(source__stable_id=self.stable_id).count()

    def test_a_competing_activation_returns_a_bounded_superseded_result(self) -> None:
        from content import services as content_services

        # The eventual loser: a fresh artifact, so this run really creates a
        # release and tries to activate it.
        mutated_loser = self._mutated_payload("loser")
        real_activate = content_services.activate_content_release
        state = {"armed": True}
        releases_before = self.faq_release_count()

        def racing_activate(command, *, context, **kwargs):
            # The interleaving under test: while this import sits between its
            # release creation and its activation call, a full competing
            # import of a different artifact lands and activates first.
            if state["armed"]:
                state["armed"] = False
                run_faq_import(path=self._mutated_payload("competitor"))
            return real_activate(command, context=context, **kwargs)

        with unittest.mock.patch.object(
            content_services, "activate_content_release", racing_activate
        ):
            loser = run_faq_import(path=mutated_loser)

        self.assertFalse(loser["applied"])
        self.assertEqual(loser["activation"], "superseded")
        self.assertIn("release", loser)
        self.assertIn("sequence", loser)

        # Exactly two releases were added (the loser, then the competitor);
        # the competitor is the one active release, and the loser's candidate
        # is ready but unactivated -- no release was deleted to fake a
        # successful import.
        source = ContentSource.objects.get(stable_id=self.stable_id)
        self.assertEqual(self.faq_release_count(), releases_before + 2)
        self.assertNotEqual(str(source.active_release_id), loser["release"])
        assert source.active_release is not None
        self.assertEqual(source.active_release.sequence, loser["sequence"] + 1)
        loser_release = ContentRelease.objects.get(id=loser["release"])
        self.assertEqual(loser_release.status, ContentRelease.Status.READY)

    def test_an_unchanged_rehearsal_is_a_replay_receipt_with_no_new_rows(self) -> None:
        mutated = self._mutated_payload("replayed")
        before = self.faq_release_count()

        first = run_faq_import(path=mutated)
        self.assertTrue(first["applied"])
        self.assertEqual(self.faq_release_count(), before + 1)
        document_total = ContentRelease.objects.get(id=first["release"]).document_count

        replay = run_faq_import(path=mutated)

        self.assertFalse(replay["applied"])
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["release"], first["release"])
        self.assertEqual(self.faq_release_count(), before + 1)
        self.assertEqual(
            ContentRelease.objects.get(id=first["release"]).document_count,
            document_total,
        )
