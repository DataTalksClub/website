from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase, TestCase

from content.models import ContentRelation, ContentRelease
from content.public_data import public_projection
from content.services import (
    ActivateContentRelease,
    activate_content_release,
    create_content_source,
)
from content_sync.dtc_content.adapter import (
    DtcContentValidationError,
    adapt_dtc_content_checkout,
)
from content_sync.dtc_content.contract import (
    ACCEPTED_ADOPTED_SOURCE_SET_SHA256,
    ACCEPTED_BUNDLE_SHA256,
    ACCEPTED_COMPARISON_SHA256,
    ACCEPTED_CONTENT_COMMIT,
    ACCEPTED_CONTENT_TREE,
    ACCEPTED_COUNTS,
    DTC_CONTENT_CONTRACT,
    EDITORIAL_OVERLAY_PATH,
    EDITORIAL_OVERLAY_SHA256,
    EDITORIAL_OVERLAY_TARGETS,
    ORIGINAL_MIGRATION_COMMIT,
    REPAIR_MANIFEST_PATH,
    REPAIR_MANIFEST_SHA256,
    REPAIRED_BASELINE_CI_RUN,
    REPAIRED_BASELINE_COMMIT,
    REPAIRED_BASELINE_TREE,
    REPLACEMENT_ATTESTATION_SHA256,
)
from content_sync.dtc_content.parity import verify_initial_projection_parity
from content_sync.dtc_content.preparation import prepare_dtc_content_candidate
from content_sync.dtc_content.repository import verify_dtc_content_checkout
from core.models import AuditEvent
from core.services import ServiceContext

ACCEPTED_CHECKOUT = os.getenv("DTC_CONTENT_ACCEPTED_CHECKOUT", "")
BASELINE_CHECKOUT = os.getenv("DTC_CONTENT_BASELINE_CHECKOUT", "")
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / ".tmp" / "issue-103-tests"


def _overlay_fixture() -> tempfile.TemporaryDirectory[str]:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="overlay-", dir=SCRATCH_ROOT)
    root = Path(temporary.name) / "content"
    source = Path(ACCEPTED_CHECKOUT)
    for relative in (
        "migration.yaml",
        REPAIR_MANIFEST_PATH,
        EDITORIAL_OVERLAY_PATH,
        *EDITORIAL_OVERLAY_TARGETS,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    return temporary


def _overlay_diagnostic(root: Path) -> str:
    try:
        adapt_dtc_content_checkout(
            root,
            commit_sha=ACCEPTED_CONTENT_COMMIT,
            source_tree_sha=ACCEPTED_CONTENT_TREE,
        )
    except DtcContentValidationError as error:
        return error.diagnostics[0].code
    raise AssertionError("invalid overlay fixture unexpectedly passed")


def _write_overlay_manifest(root: Path, manifest: dict[str, object]) -> str:
    path = root / EDITORIAL_OVERLAY_PATH
    path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@skipUnless(ACCEPTED_CHECKOUT, "DTC_CONTENT_ACCEPTED_CHECKOUT is not configured")
class AcceptedDtcContentCheckoutTests(SimpleTestCase):
    def test_accepted_checkout_is_exact_deterministic_and_projection_equivalent(self) -> None:
        root = Path(ACCEPTED_CHECKOUT)
        first = verify_dtc_content_checkout(root, expected_commit=ACCEPTED_CONTENT_COMMIT)
        second = verify_dtc_content_checkout(root, expected_commit=ACCEPTED_CONTENT_COMMIT)

        self.assertEqual(first, second)
        self.assertEqual(first.tree_sha, ACCEPTED_CONTENT_TREE)
        self.assertEqual(first.bundle.counts, ACCEPTED_COUNTS)
        self.assertEqual(first.bundle.bundle_sha256, ACCEPTED_BUNDLE_SHA256)
        self.assertEqual(len(first.bundle.documents), 561)
        self.assertEqual(len(first.bundle.assets), 815)
        self.assertEqual(first.bundle.original_migration_commit, ORIGINAL_MIGRATION_COMMIT)
        self.assertEqual(first.bundle.repaired_baseline_commit, REPAIRED_BASELINE_COMMIT)
        self.assertEqual(first.bundle.repaired_baseline_tree, REPAIRED_BASELINE_TREE)
        self.assertEqual(first.bundle.repaired_baseline_ci_run, REPAIRED_BASELINE_CI_RUN)
        self.assertEqual(first.bundle.repair_manifest_sha256, REPAIR_MANIFEST_SHA256)
        self.assertEqual(
            first.bundle.replacement_attestation_sha256,
            REPLACEMENT_ATTESTATION_SHA256,
        )
        self.assertEqual(first.bundle.editorial_overlay_path, EDITORIAL_OVERLAY_PATH)
        self.assertEqual(first.bundle.editorial_overlay_sha256, EDITORIAL_OVERLAY_SHA256)
        podcasts = [
            document for document in first.bundle.documents if document.content_kind == "podcast"
        ]
        self.assertEqual(len(podcasts), 205)
        for document in podcasts:
            metadata = json.loads(document.raw_structured_data)
            self.assertIsInstance(metadata["description"], str)
            self.assertTrue(metadata["description"].strip())
            for field in ("season", "episode"):
                self.assertIs(type(metadata[field]), int)
                self.assertGreater(metadata[field], 0)
        articles = {
            document.source_path: document
            for document in first.bundle.documents
            if document.content_kind == "article"
        }
        expected_omissions = {
            "articles/2022-10-02-naming-variables-in-machine-learning.md": 2,
            "articles/2025-02-26-building-ai-agent-that-thrives-in-real-world.md": 3,
        }
        for source_path, count in expected_omissions.items():
            document = articles[source_path]
            self.assertNotIn("user-images.githubusercontent.com", document.rendered_html)
            self.assertNotIn("s3.gifyu.com", document.rendered_html)
            self.assertNotIn("<img  />", document.rendered_html)
            assert document.adapter_metadata is not None
            self.assertEqual(len(document.adapter_metadata["omitted_remote_images"]), count)
        self.assertIsNotNone(first.projection_parity)
        assert first.projection_parity is not None
        self.assertEqual(first.projection_parity.status, "PASS")
        self.assertEqual(
            first.projection_parity.adopted_source_set_sha256,
            ACCEPTED_ADOPTED_SOURCE_SET_SHA256,
        )
        self.assertEqual(
            first.projection_parity.comparison_sha256,
            ACCEPTED_COMPARISON_SHA256,
        )
        self.assertEqual(first.projection_parity.counts["unobserved_legacy_contracts"], 10)
        self.assertEqual(
            first.projection_parity.counts["optional_unresolved_person_relations"],
            18,
        )

    def test_editorial_overlay_bytes_schema_and_binding_fail_closed(self) -> None:
        with _overlay_fixture() as temporary:
            root = Path(temporary) / "content"
            manifest_path = root / EDITORIAL_OVERLAY_PATH
            manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
            self.assertEqual(_overlay_diagnostic(root), "editorial_overlay_tampered")

        cases = (
            ("keys", "editorial_overlay_schema_invalid"),
            ("order", "editorial_overlay_target_order_invalid"),
            ("count", "editorial_overlay_target_count_invalid"),
            ("path", "editorial_overlay_target_path_invalid"),
            ("field", "editorial_overlay_contract_invalid"),
            ("target-field", "editorial_overlay_target_field_invalid"),
            ("baseline", "editorial_overlay_contract_invalid"),
            ("migration", "editorial_overlay_contract_invalid"),
        )
        for label, expected in cases:
            with self.subTest(label=label), _overlay_fixture() as temporary:
                root = Path(temporary) / "content"
                manifest = yaml.safe_load(
                    (root / EDITORIAL_OVERLAY_PATH).read_text(encoding="utf-8")
                )
                self.assertIsInstance(manifest, dict)
                if label == "keys":
                    manifest["unexpected"] = True
                elif label == "order":
                    manifest["targets"][0], manifest["targets"][1] = (
                        manifest["targets"][1],
                        manifest["targets"][0],
                    )
                elif label == "count":
                    manifest["targets"].pop()
                elif label == "path":
                    manifest["targets"][0]["path"] = "podcasts/../outside.yaml"
                elif label == "field":
                    manifest["field"] = "intro"
                elif label == "target-field":
                    manifest["targets"][0]["key"] = "intro"
                elif label == "baseline":
                    manifest["baseline_content_commit"] = "0" * 40
                else:
                    manifest["migration"]["sha256"] = "0" * 64
                pinned_digest = _write_overlay_manifest(root, manifest)
                with patch(
                    "content_sync.dtc_content.adapter.EDITORIAL_OVERLAY_SHA256",
                    pinned_digest,
                ):
                    self.assertEqual(_overlay_diagnostic(root), expected)

    def test_each_overlay_description_and_target_digest_is_bound(self) -> None:
        for relative in EDITORIAL_OVERLAY_TARGETS:
            with self.subTest(relative=relative, drift="description"), _overlay_fixture() as temp:
                root = Path(temp) / "content"
                target = root / relative
                metadata = yaml.safe_load(target.read_text(encoding="utf-8"))
                self.assertIsInstance(metadata, dict)
                metadata["description"] = f"{metadata['description']} drift"
                target.write_text(
                    yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                self.assertEqual(
                    _overlay_diagnostic(root),
                    "editorial_overlay_description_digest_mismatch",
                )

            with self.subTest(relative=relative, drift="target"), _overlay_fixture() as temp:
                root = Path(temp) / "content"
                target = root / relative
                target.write_bytes(target.read_bytes() + b"\n")
                self.assertEqual(
                    _overlay_diagnostic(root),
                    "editorial_overlay_target_digest_mismatch",
                )

        with _overlay_fixture() as temporary:
            root = Path(temporary) / "content"
            target = root / EDITORIAL_OVERLAY_TARGETS[0]
            metadata = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertIsInstance(metadata, dict)
            metadata["undeclared_metadata"] = True
            target.write_text(
                yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            self.assertEqual(
                _overlay_diagnostic(root),
                "editorial_overlay_target_digest_mismatch",
            )

    def test_projection_parity_blocks_semantic_and_evidence_drift(self) -> None:
        verified = verify_dtc_content_checkout(
            Path(ACCEPTED_CHECKOUT), expected_commit=ACCEPTED_CONTENT_COMMIT
        )
        bundle = verified.bundle
        article_index = next(
            index
            for index, document in enumerate(bundle.documents)
            if document.content_kind == "article"
        )
        transcript_index = next(
            index
            for index, document in enumerate(bundle.documents)
            if document.content_kind == "podcast_transcript"
        )
        podcast_index = next(
            index
            for index, document in enumerate(bundle.documents)
            if document.content_kind == "podcast"
        )
        person_relation_index = next(
            index
            for index, relation in enumerate(bundle.relations)
            if relation.target_kind == "person" and relation.is_required
        )

        def replace_document(index: int, **changes: object):
            documents = list(bundle.documents)
            documents[index] = replace(documents[index], **changes)  # type: ignore[arg-type]
            return replace(bundle, documents=tuple(documents))

        transcript = bundle.documents[transcript_index]
        structured = json.loads(transcript.raw_structured_data)
        segment = next(item for item in structured["segments"] if item.get("line"))
        segment["line"] = "tampered"
        transcript_drift = replace_document(
            transcript_index,
            raw_structured_data=json.dumps(structured, sort_keys=True),
        )
        relation_drift = list(bundle.relations)
        relation_drift[person_relation_index] = replace(
            relation_drift[person_relation_index], target_key="missing-person"
        )
        asset_drift = list(bundle.assets)
        asset_drift[0] = replace(asset_drift[0], checksum="0" * 64)

        article = bundle.documents[article_index]
        podcast = bundle.documents[podcast_index]
        podcast_metadata = json.loads(podcast.raw_structured_data)
        podcast_description_drift = dict(podcast_metadata)
        podcast_description_drift["description"] = "tampered description"
        podcast_season_drift = dict(podcast_metadata)
        podcast_season_drift["season"] = str(podcast_metadata["season"])
        podcast_episode_drift = dict(podcast_metadata)
        podcast_episode_drift["episode"] = float(podcast_metadata["episode"])
        cases = (
            (replace_document(article_index, title=f"{article.title} drift"), "title"),
            (replace_document(article_index, exact_public_path="/blog/drift.html"), "path"),
            (
                replace_document(article_index, canonical_url="https://example.invalid/"),
                "canonical",
            ),
            (replace_document(article_index, seo_description="drift"), "seo"),
            (replace(bundle, relations=tuple(relation_drift)), "relation"),
            (transcript_drift, "transcript"),
            (
                replace_document(
                    podcast_index,
                    raw_structured_data=json.dumps(podcast_description_drift, sort_keys=True),
                ),
                "description",
            ),
            (
                replace_document(
                    podcast_index,
                    raw_structured_data=json.dumps(podcast_season_drift, sort_keys=True),
                ),
                "season",
            ),
            (
                replace_document(
                    podcast_index,
                    raw_structured_data=json.dumps(podcast_episode_drift, sort_keys=True),
                ),
                "episode",
            ),
            (replace(bundle, assets=tuple(asset_drift)), "media"),
            (replace(bundle, commit_sha="0" * 40), "source pin"),
            (replace(bundle, repair_manifest_sha256="0" * 64), "repair"),
            (replace(bundle, editorial_overlay_sha256="0" * 64), "overlay"),
            (replace(bundle, repaired_baseline_commit="0" * 40), "repair misbinding"),
        )
        for candidate, label in cases:
            with self.subTest(label=label), self.assertRaises(DtcContentValidationError):
                verify_initial_projection_parity(candidate)

        projection_drift = deepcopy(public_projection())
        projection_drift["articles"][0]["title"] = "tampered projection title"
        with self.assertRaises(DtcContentValidationError):
            verify_initial_projection_parity(bundle, projection=projection_drift)

        for field in ("season", "episode"):
            for invalid in (None, True, "1", 1.0, 0, -1):
                with self.subTest(field=field, invalid=invalid):
                    projection_number_drift = deepcopy(public_projection())
                    projection_number_drift["podcasts"][0][field] = invalid
                    with self.assertRaises(DtcContentValidationError):
                        verify_initial_projection_parity(
                            bundle,
                            projection=projection_number_drift,
                        )


@skipUnless(ACCEPTED_CHECKOUT, "DTC_CONTENT_ACCEPTED_CHECKOUT is not configured")
class AcceptedDtcContentPreparationTests(TestCase):
    def _response_digest(self, path: str) -> tuple[int, str]:
        response = self.client.get(path)
        body = (
            b"".join(response.streaming_content)  # type: ignore[attr-defined]
            if response.streaming
            else response.content
        )
        return response.status_code, hashlib.sha256(body).hexdigest()

    def test_full_candidate_is_ready_replay_safe_and_publicly_inert(self) -> None:
        projection = public_projection()
        people = {
            str(key): str(record["public_path"])
            for key, record in projection["people_by_slug"].items()
        }
        content_media = next(
            record
            for record in projection["media"]
            if record["provenance"]["repository"] == "DataTalksClub/content"
        )
        paths = (
            projection["articles"][0]["public_path"],
            projection["podcasts"][0]["public_path"],
            projection["books"][0]["public_path"],
            content_media["public_path"],
        )
        before = {path: self._response_digest(path) for path in paths}
        context = ServiceContext(
            request_id="issue-103-accepted",
            correlation_id="issue-103-accepted",
            actor_ref="user:issue-103-tests",
        )
        source = create_content_source(
            DTC_CONTENT_CONTRACT.create_source_command(enabled=True), context=context
        )
        verified = verify_dtc_content_checkout(
            Path(ACCEPTED_CHECKOUT), expected_commit=ACCEPTED_CONTENT_COMMIT
        )
        first = prepare_dtc_content_candidate(
            source_id=source.id,
            expected_source_revision=source.revision,
            verified_checkout=verified,
            commit_sha=ACCEPTED_CONTENT_COMMIT,
            person_resolver=people.get,
            context=context,
        )
        source.refresh_from_db()
        audit_count = AuditEvent.objects.count()
        replays = []
        for _index in range(2):
            replay_verified = verify_dtc_content_checkout(
                Path(ACCEPTED_CHECKOUT), expected_commit=ACCEPTED_CONTENT_COMMIT
            )
            replay = prepare_dtc_content_candidate(
                source_id=source.id,
                expected_source_revision=source.revision,
                verified_checkout=replay_verified,
                commit_sha=ACCEPTED_CONTENT_COMMIT,
                person_resolver=people.get,
                context=context,
            )
            replays.append(replay)
            self.assertEqual(AuditEvent.objects.count(), audit_count)
        self.assertFalse(first.replayed)
        self.assertTrue(all(replay.replayed for replay in replays))
        self.assertTrue(all(first.release.id == replay.release.id for replay in replays))
        self.assertEqual(first.release.status, ContentRelease.Status.READY)
        self.assertEqual(
            (
                first.release.document_count,
                first.release.relation_count,
                first.release.asset_count,
            ),
            (561, 616, 815),
        )
        self.assertEqual(
            ContentRelation.objects.filter(
                source_document__release=first.release,
                target_kind="person",
                is_required=False,
                resolved_public_path__isnull=True,
            ).count(),
            18,
        )

        source.refresh_from_db()
        first.release.refresh_from_db()
        activate_content_release(
            ActivateContentRelease(
                source.id,
                first.release.id,
                source.revision,
                first.release.revision,
                "isolated issue-103 public continuity proof",
            ),
            context=context,
        )
        after = {path: self._response_digest(path) for path in paths}
        self.assertEqual(after, before)


@skipUnless(BASELINE_CHECKOUT, "DTC_CONTENT_BASELINE_CHECKOUT is not configured")
class RejectedBaselineDtcContentCheckoutTests(SimpleTestCase):
    def test_original_migration_commit_still_fails_at_first_missing_asset(self) -> None:
        with self.assertRaises(DtcContentValidationError) as raised:
            verify_dtc_content_checkout(
                Path(BASELINE_CHECKOUT),
                expected_commit=ORIGINAL_MIGRATION_COMMIT,
            )
        diagnostic = raised.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "referenced_asset_missing")
        self.assertEqual(
            diagnostic.source_path,
            "images/books/20241104-llm-engineer-s-handbook/preview.jpg",
        )
