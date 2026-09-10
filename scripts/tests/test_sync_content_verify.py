"""What the editorial drift check reports, and what it refuses to do.

The served side is built here from the fixture checkout by an independent little
walker rather than by the script's own reader, so a test that says "these agree"
is not the script agreeing with itself.

Nothing here needs the adapter, the bundle or ``parity.py``: a served record already
declares which repository owns it, which record it is, and what its source bytes
hashed to, so the whole check is a set-diff plus a digest compare.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from django.test import TestCase

from content.models import ContentDocument, ContentRelease, ContentSource
from content_sync.tests.helpers import fixture_checkout, initialize_fixture_repository
from core.models import AuditEvent
from scripts.prod.sync_content_verify import (
    BUCKET_LIMIT,
    EDITORIAL_REPOSITORY,
    ContentDriftRefused,
    build_report,
    compare_family,
    run_from_args,
    select_editorial_source,
)
from test_support.published_content import PublishedPage, publish_documents

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "prod" / "sync_content_verify.py"
SOURCE_STABLE_ID = "dtc-public-content"
ARTICLE_DATE = re.compile(r"^(?:\d{2}|\d{4})-\d{2}-\d{2}-(.+)$")
COMMIT_ENVIRONMENT = {
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Author",
}


# --------------------------------------------------------------------------
# Fixture repository plumbing
# --------------------------------------------------------------------------


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **COMMIT_ENVIRONMENT},
    )
    return completed.stdout.strip()


def commit_everything(root: Path, message: str) -> str:
    """Commit whatever the working tree now holds and return the new commit."""

    _git(root, "add", "--all")
    _git(root, "commit", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def set_tracking_head(root: Path, commit: str, *, branch: str = "main") -> None:
    """Point the remote-tracking branch at a commit, the way a refresh would.

    The check reads reachability out of ``refs/remotes/origin/<branch>`` and never
    resolves a ref over the wire, so a test says what upstream holds by writing this
    ref rather than by reaching GitHub.
    """

    _git(root, "update-ref", f"refs/remotes/origin/{branch}", commit)


@contextmanager
def editorial_checkout() -> Iterator[tuple[Path, str]]:
    """A committed fixture checkout whose origin is the editorial repository."""

    with fixture_checkout() as root:
        commit = initialize_fixture_repository(root)
        set_tracking_head(root, commit)
        yield root, commit


# --------------------------------------------------------------------------
# The served side, walked independently of the script
# --------------------------------------------------------------------------


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance(
    *, source_key: str, source_path: str, checksum: str, revision: str
) -> dict[str, str]:
    return {
        "repository": EDITORIAL_REPOSITORY,
        "revision": revision,
        "source_path": source_path,
        "source_key": source_key,
        "checksum": checksum,
        "source_url": (f"https://github.com/{EDITORIAL_REPOSITORY}/blob/{revision}/{source_path}"),
    }


def walk_checkout(root: Path, revision: str) -> list[dict[str, Any]]:
    """The records a projection built from this checkout at ``revision`` would carry."""

    records: list[dict[str, Any]] = []
    for path in sorted((root / "articles").rglob("*.md")):
        match = ARTICLE_DATE.fullmatch(path.stem)
        if match is None or path.name.startswith("_"):
            continue
        relative = path.relative_to(root).as_posix()
        records.append(
            {
                "kind": "article",
                "slug": match.group(1),
                "provenance": _provenance(
                    source_key=match.group(1),
                    source_path=relative,
                    checksum=_digest(path),
                    revision=revision,
                ),
            }
        )
    transcripts: dict[str, tuple[str, str]] = {}
    for path in sorted((root / "podcasts" / "transcripts").glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        transcripts[loaded["podcast"]] = (
            path.relative_to(root).as_posix(),
            _digest(path),
        )
    for path in sorted((root / "podcasts").glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        slug = loaded["slug"]
        record: dict[str, Any] = {
            "kind": "podcast",
            "slug": slug,
            "provenance": _provenance(
                source_key=slug,
                source_path=path.relative_to(root).as_posix(),
                checksum=_digest(path),
                revision=revision,
            ),
        }
        held = transcripts.get(slug)
        if held is not None:
            record["transcript_provenance"] = _provenance(
                source_key=slug,
                source_path=held[0],
                checksum=held[1],
                revision=revision,
            )
        records.append(record)
    for path in sorted((root / "books").glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        records.append(
            {
                "kind": "book",
                "slug": loaded["slug"],
                "provenance": _provenance(
                    source_key=loaded["slug"],
                    source_path=path.relative_to(root).as_posix(),
                    checksum=_digest(path),
                    revision=revision,
                ),
            }
        )
    for directory in ("posts", "podcast", "books"):
        for path in sorted((root / "images" / directory).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            records.append(
                {
                    "kind": "media",
                    "slug": relative,
                    "provenance": _provenance(
                        source_key=relative,
                        source_path=relative,
                        checksum=_digest(path),
                        revision=revision,
                    ),
                }
            )
    return records


def _page(record: dict[str, Any], index: int) -> PublishedPage:
    kind = record["kind"]
    slug = record["slug"]
    path = f"/{slug}" if kind == "media" else f"/-/{kind}/{index}"
    return PublishedPage(
        exact_public_path=path,
        title=f"{kind} {index}",
        content_kind=kind,
        slug=slug[:255],
        adapter_metadata={"record": record, "position": index},
    )


def register_source(**overrides: Any) -> ContentSource:
    """The editorial source this database registers, with ``overrides`` applied.

    The test database already carries a real ``dtc-public-content`` source and its
    real catalogue -- ``test_support/reference_data.py`` runs the public content
    import after ``migrate`` -- so this adjusts that row rather than inserting a
    second one the repository/branch uniqueness constraint would refuse.
    """

    values: dict[str, Any] = {
        "display_name": "DataTalks.Club editorial content",
        "repository_owner": "DataTalksClub",
        "repository_name": "content",
        "branch": "main",
        "path_allowlist": ["/"],
        "adapter_type": "reviewed-public-content-v1",
        "mount_path": "/-/public-content/",
        "enabled": True,
    }
    values.update(overrides)
    source, _created = ContentSource.objects.get_or_create(
        stable_id=values.pop("stable_id", SOURCE_STABLE_ID), defaults=values
    )
    for field_name, value in values.items():
        setattr(source, field_name, value)
    source.save()
    return source


def publish(records: Sequence[dict[str, Any]]) -> ContentSource:
    """Register the editorial source and activate a release publishing ``records``.

    A second activation supersedes the reference catalogue, exactly as a re-import
    does, so the check only ever sees the release the source now points at.
    """

    source = register_source()
    publish_documents(
        [_page(record, index) for index, record in enumerate(records)],
        stable_id=SOURCE_STABLE_ID,
    )
    source.refresh_from_db()
    return source


def report_for(root: Path, *, revision: str | None = None) -> dict[str, Any]:
    return build_report(source=select_editorial_source(), checkout=root, revision=revision)


def _namespace(
    root: Path, *, revision: str | None = None, plan: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(checkout=root, revision=revision, checkout_plan=plan)


def run_capturing(namespace: argparse.Namespace) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = run_from_args(namespace)
    return status, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class AgreementTests(TestCase):
    """A served catalogue built from the checkout is reported as the zero drift it is."""

    def test_a_matching_catalogue_is_clean_and_exits_zero(self) -> None:
        with editorial_checkout() as (root, commit):
            publish(walk_checkout(root, commit))
            status, stdout, stderr = run_capturing(_namespace(root))

        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertTrue(report["clean"])
        self.assertEqual(status, 0)
        for family, held in report["families"].items():
            with self.subTest(family=family):
                self.assertEqual(held["matched"], held["total"])
                self.assertTrue(held["clean"])
        self.assertEqual(report["revision_status"]["state"], "clean")
        self.assertEqual(report["served"]["source"], SOURCE_STABLE_ID)
        self.assertEqual(report["served"]["revisions"], [commit])

    def test_the_report_is_indented_sorted_json_with_no_record_body(self) -> None:
        with editorial_checkout() as (root, commit):
            publish(walk_checkout(root, commit))
            _status, stdout, _stderr = run_capturing(_namespace(root))
            checkout_path = str(root)

        report = json.loads(stdout)
        self.assertEqual(stdout, json.dumps(report, indent=2, sort_keys=True) + "\n")
        self.assertNotIn("blocks", stdout)
        self.assertNotIn("source_url", stdout)
        self.assertNotIn("fixture@example.invalid", stdout)
        self.assertNotIn(checkout_path, stdout)

    def test_an_article_that_moves_with_its_bytes_is_not_drift(self) -> None:
        """Identity is the stable key. Over fifty real files differ only by path."""

        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            source = root / "articles" / "2020-11-29-segmentation.md"
            moved = root / "articles" / "2020" / "2020-11-29-segmentation.md"
            moved.parent.mkdir(parents=True, exist_ok=True)
            moved.write_bytes(source.read_bytes())
            source.unlink()
            reorganised = commit_everything(root, "Reorganise articles by year")
            report = report_for(root, revision=reorganised)

        self.assertTrue(report["families"]["articles"]["clean"])
        self.assertEqual(report["families"]["articles"]["mismatched_count"], 0)
        self.assertEqual(report["families"]["articles"]["missing_count"], 0)
        self.assertEqual(report["families"]["articles"]["extra_count"], 0)


class DriftTests(TestCase):
    """One perturbation apiece, so a bucket cannot be right for the wrong reason."""

    def test_an_upstream_only_article_is_missing(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            (root / "articles" / "2026-01-02-brand-new.md").write_text(
                "---\ntitle: Brand new\n---\n\nBody.\n", encoding="utf-8"
            )
            added = commit_everything(root, "Add an article")
            status, stdout, _stderr = run_capturing(_namespace(root, revision=added))

        report = json.loads(stdout)
        self.assertEqual(report["families"]["articles"]["missing"], ["brand-new"])
        self.assertEqual(report["families"]["articles"]["missing_count"], 1)
        self.assertEqual(report["families"]["articles"]["extra_count"], 0)
        self.assertFalse(report["clean"])
        self.assertEqual(status, 1)

    def test_a_served_book_with_no_upstream_file_is_extra(self) -> None:
        with editorial_checkout() as (root, base):
            records = walk_checkout(root, base)
            records.append(
                {
                    "kind": "book",
                    "slug": "retired-book",
                    "provenance": _provenance(
                        source_key="retired-book",
                        source_path="books/retired-book.yaml",
                        checksum="0" * 64,
                        revision=base,
                    ),
                }
            )
            publish(records)
            status, stdout, _stderr = run_capturing(_namespace(root))

        report = json.loads(stdout)
        self.assertEqual(report["families"]["books"]["extra"], ["retired-book"])
        self.assertEqual(report["families"]["books"]["extra_count"], 1)
        self.assertEqual(report["families"]["books"]["missing_count"], 0)
        self.assertEqual(status, 1)

    def test_changed_podcast_bytes_are_mismatched_and_nothing_else_moves(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            episode = root / "podcasts" / "analytics-engineer-skills-tools.yaml"
            episode.write_text(
                episode.read_text(encoding="utf-8") + "\nextra: edited upstream\n",
                encoding="utf-8",
            )
            edited = commit_everything(root, "Edit one episode")
            report = report_for(root, revision=edited)

        podcasts = report["families"]["podcasts"]
        self.assertEqual(podcasts["mismatched"], ["analytics-engineer-skills-tools"])
        self.assertEqual(podcasts["mismatched_count"], 1)
        self.assertEqual(podcasts["matched"], podcasts["total"] - 1)
        for family in ("articles", "books", "podcast_transcripts", "media"):
            with self.subTest(family=family):
                self.assertTrue(report["families"][family]["clean"])

    def test_a_transcript_drifts_independently_of_its_episode(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            transcript = root / "podcasts" / "transcripts" / "analytics-engineer-skills-tools.yaml"
            transcript.write_text(
                transcript.read_text(encoding="utf-8").replace(
                    "header: Introductions", "header: Introduction"
                ),
                encoding="utf-8",
            )
            edited = commit_everything(root, "Correct one transcript")
            report = report_for(root, revision=edited)

        self.assertEqual(
            report["families"]["podcast_transcripts"]["mismatched"],
            ["analytics-engineer-skills-tools"],
        )
        self.assertTrue(report["families"]["podcasts"]["clean"])


class MediaAsymmetryTests(TestCase):
    """The served media set is the referenced subset by construction."""

    def test_an_unreferenced_upstream_image_leaves_the_run_clean(self) -> None:
        with editorial_checkout() as (root, base):
            records = walk_checkout(root, base)
            unreferenced = "images/books/20201214-ml-bookcamp/preview.jpg"
            published = [
                record for record in records if record["provenance"]["source_path"] != unreferenced
            ]
            publish(published)
            status, stdout, _stderr = run_capturing(_namespace(root))

        report = json.loads(stdout)
        media = report["families"]["media"]
        self.assertEqual(media["upstream_unreferenced"], [unreferenced])
        self.assertEqual(media["upstream_unreferenced_count"], 1)
        self.assertEqual(media["missing"], [])
        self.assertEqual(media["missing_count"], 0)
        self.assertTrue(media["clean"])
        self.assertTrue(report["clean"])
        self.assertEqual(status, 0)

    def test_a_served_image_upstream_deleted_is_extra(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            orphaned = "images/books/20201214-ml-bookcamp/preview.jpg"
            (root / orphaned).unlink()
            deleted = commit_everything(root, "Delete one image")
            status, stdout, _stderr = run_capturing(_namespace(root, revision=deleted))

        report = json.loads(stdout)
        self.assertEqual(report["families"]["media"]["extra"], [orphaned])
        self.assertFalse(report["clean"])
        self.assertEqual(status, 1)

    def test_changed_image_bytes_are_mismatched(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            changed = "images/podcast/badges/spotify.svg"
            path = root / changed
            path.write_text(path.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")
            edited = commit_everything(root, "Edit one image")
            report = report_for(root, revision=edited)

        self.assertEqual(report["families"]["media"]["mismatched"], [changed])
        self.assertFalse(report["clean"])


class RevisionStatusTests(TestCase):
    """Drift class (d): is the revision we built from still upstream's history?"""

    def test_a_served_revision_at_the_tracking_head_is_clean(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            report = report_for(root)

        self.assertEqual(report["revision_status"]["state"], "clean")
        self.assertEqual(report["revision_status"]["commits_behind"], 0)
        self.assertTrue(report["revision_status"]["revisions"][0]["reachable"])

    def test_a_reachable_older_revision_is_behind_by_a_counted_number(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            head = commit_everything(root, "One more upstream commit")
            set_tracking_head(root, head)
            status, stdout, _stderr = run_capturing(_namespace(root, revision=base))

        report = json.loads(stdout)
        self.assertEqual(report["revision_status"]["state"], "behind")
        self.assertEqual(report["revision_status"]["commits_behind"], 1)
        self.assertEqual(report["checkout"]["remote_head"], head)
        self.assertFalse(report["clean"])
        self.assertEqual(status, 1)

    def test_a_revision_upstream_never_had_is_unreachable(self) -> None:
        """The state today's production data is in, and #326 owns repairing it."""

        with editorial_checkout() as (root, base):
            local_only = commit_everything(root, "A commit only this clone has")
            publish(walk_checkout(root, local_only))
            set_tracking_head(root, base)
            status, stdout, _stderr = run_capturing(_namespace(root, revision=local_only))

        report = json.loads(stdout)
        self.assertEqual(report["revision_status"]["state"], "unreachable")
        self.assertFalse(report["revision_status"]["revisions"][0]["reachable"])
        self.assertFalse(report["clean"])
        self.assertEqual(status, 1)


class ScopeTests(TestCase):
    """Five families, selected by what each record says owns it."""

    def test_records_owned_by_another_repository_are_ignored(self) -> None:
        with editorial_checkout() as (root, base):
            records = walk_checkout(root, base)
            for repository, kind, slug in (
                ("DataTalksClub/datatalksclub.github.io", "people", "alexey"),
                ("DataTalksClub/podwiki", "wiki", "data-engineering"),
                ("DataTalksClub/course-management-platform", "course", "ml-zoomcamp"),
                ("DataTalksClub/datatalksclub.github.io", "media", "images/alexey.jpg"),
            ):
                records.append(
                    {
                        "kind": kind,
                        "slug": slug,
                        "provenance": {
                            "repository": repository,
                            "revision": "b" * 40,
                            "source_path": f"{slug}.md",
                            "source_key": slug,
                            "checksum": "1" * 64,
                        },
                    }
                )
            publish(records)
            report = report_for(root)

        self.assertTrue(report["clean"])
        self.assertEqual(
            sorted(report["families"]),
            sorted(["articles", "books", "media", "podcast_transcripts", "podcasts"]),
        )
        self.assertEqual(report["served"]["revisions"], [base])

    def test_a_draft_upstream_is_not_reported_as_missing(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            (root / "podcasts" / "_unpublished-draft.yaml").write_text(
                "slug: unpublished-draft\n", encoding="utf-8"
            )
            drafted = commit_everything(root, "Add a draft episode")
            report = report_for(root, revision=drafted)

        self.assertTrue(report["clean"])


class RefusalTests(TestCase):
    """Exit 2 is reserved for 'I could not look', never for 'we are behind'."""

    def _assert_refused(self, namespace: argparse.Namespace, condition: str) -> None:
        status, stdout, stderr = run_capturing(namespace)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertEqual(payload["condition"], condition)
        self.assertTrue(payload["error"])

    def test_a_checkout_from_another_repository_is_refused_and_never_parsed(self) -> None:
        with fixture_checkout() as root:
            base = initialize_fixture_repository(root)
            set_tracking_head(root, base)
            _git(root, "remote", "set-url", "origin", "https://github.com/attacker/content.git")
            publish(walk_checkout(root, base))
            self._assert_refused(_namespace(root), "checkout-origin-mismatch")

    def test_a_relative_checkout_is_refused(self) -> None:
        register_source()
        self._assert_refused(_namespace(Path("relative/checkout")), "checkout-not-absolute")

    def test_a_symlinked_checkout_is_refused(self) -> None:
        with editorial_checkout() as (root, _commit):
            register_source()
            link = root.parent / "linked"
            link.symlink_to(root, target_is_directory=True)
            self._assert_refused(_namespace(link), "checkout-symlink")

    def test_an_unresolvable_revision_is_refused(self) -> None:
        with editorial_checkout() as (root, _commit):
            register_source()
            self._assert_refused(_namespace(root, revision="c" * 40), "revision-unresolvable")

    def test_a_database_with_no_enabled_editorial_source_is_refused(self) -> None:
        with editorial_checkout() as (root, _commit):
            register_source(enabled=False)
            self._assert_refused(_namespace(root), "editorial-source-missing")

    def test_a_database_with_no_active_release_is_refused(self) -> None:
        """An un-ingested database is not 'everything is missing'."""

        with editorial_checkout() as (root, _commit):
            register_source(active_release=None)
            self._assert_refused(_namespace(root), "no-active-release")

    def test_argparse_refuses_a_run_with_no_target_using_exit_two(self) -> None:
        completed = subprocess.run(
            (sys.executable, str(SCRIPT)),
            check=False,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("--database", completed.stderr)


class WorkingCopyIndependenceTests(TestCase):
    """The answer comes from a named revision's tree, not from the working copy."""

    def test_a_dirty_checkout_at_another_head_still_reports(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            (root / "articles" / "2026-01-02-uncommitted.md").write_text(
                "---\ntitle: Uncommitted\n---\n", encoding="utf-8"
            )
            _git(root, "checkout", "--quiet", "-b", "elsewhere")
            other = commit_everything(root, "A different HEAD entirely")

            self.assertNotEqual(_git(root, "rev-parse", "HEAD"), base)
            (root / "books" / "20201214-ml-bookcamp.yaml").write_text(
                "slug: tampered\n", encoding="utf-8"
            )
            self.assertTrue(_git(root, "status", "--porcelain=v1"))

            status, stdout, _stderr = run_capturing(_namespace(root, revision=base))

        report = json.loads(stdout)
        self.assertEqual(report["checkout"]["revision"], base)
        self.assertNotEqual(report["checkout"]["revision"], other)
        self.assertTrue(report["clean"])
        self.assertEqual(status, 0)


class CheckoutPlanTests(TestCase):
    """What `scripts/content.py checkout` consumes, and it writes nothing."""

    def test_the_plan_names_the_registered_source_and_exits_zero(self) -> None:
        register_source()
        checkout = Path("/absolute/content-checkout")
        status, stdout, stderr = run_capturing(_namespace(checkout, plan=True))

        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, f"{SOURCE_STABLE_ID}\tDataTalksClub/content\tmain\t{checkout}\n")

    def test_the_plan_works_on_a_database_with_no_active_release(self) -> None:
        source = register_source(active_release=None)
        self.assertIsNone(source.active_release_id)
        status, stdout, _stderr = run_capturing(_namespace(Path("/absolute/x"), plan=True))
        self.assertEqual(status, 0)
        self.assertTrue(stdout.startswith(SOURCE_STABLE_ID))


class ReadOnlyTests(TestCase):
    """No write of any kind, including the state #273 owns."""

    def _snapshot(self) -> dict[str, Any]:
        return {
            "sources": list(
                ContentSource.objects.order_by("stable_id").values(
                    "stable_id",
                    "revision",
                    "enabled",
                    "active_release_id",
                    "last_reconciled_at",
                    "last_webhook_at",
                    "pending_follow_up",
                    "updated_at",
                )
            ),
            "releases": list(
                ContentRelease.objects.order_by("sequence").values(
                    "id", "status", "revision", "commit_sha", "updated_at"
                )
            ),
            "documents": list(
                ContentDocument.objects.order_by("content_kind", "stable_key").values(
                    "id", "content_kind", "stable_key", "checksum", "adapter_metadata"
                )
            ),
            "audit": AuditEvent.objects.count(),
        }

    def test_a_drifted_run_leaves_every_row_exactly_as_it_found_it(self) -> None:
        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            (root / "articles" / "2026-01-02-brand-new.md").write_text(
                "---\ntitle: Brand new\n---\n", encoding="utf-8"
            )
            drifted = commit_everything(root, "Add an article")
            before = self._snapshot()
            status, _stdout, _stderr = run_capturing(_namespace(root, revision=drifted))
            after = self._snapshot()

        self.assertEqual(status, 1)
        self.assertEqual(before, after)

    def test_the_script_names_no_networked_git_subcommand(self) -> None:
        """Every git invocation is a local object-database read.

        The whole test suite already denies an external network, so a networked call
        would fail loudly; this reads the source as well, because a refusal that only
        shows up under the guard is not the same as never having written one.
        """

        source = SCRIPT.read_text(encoding="utf-8")
        for command in (
            "fetch",
            "ls-remote",
            "clone",
            "pull",
            "push",
            "remote add",
            "remote update",
            "upload-pack",
        ):
            with self.subTest(command=command):
                self.assertNotIn(command, source)

    def test_a_full_run_completes_with_no_reachable_remote(self) -> None:
        """The fixture's origin address resolves to nothing this process may reach."""

        with editorial_checkout() as (root, base):
            publish(walk_checkout(root, base))
            status, _stdout, stderr = run_capturing(_namespace(root))

        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")


class ReportShapeTests(TestCase):
    """Bucket lists are truncated beside a full count, as `VerifyReport` does."""

    def test_a_bucket_is_truncated_to_twenty_entries_with_a_full_count(self) -> None:
        upstream = {f"record-{index:03d}": "a" * 64 for index in range(30)}
        family = compare_family({}, upstream, asymmetric=False)

        self.assertEqual(len(family["missing"]), BUCKET_LIMIT)
        self.assertEqual(family["missing_count"], 30)
        self.assertEqual(family["missing"], sorted(upstream)[:BUCKET_LIMIT])
        self.assertEqual(family["total"], 0)
        self.assertFalse(family["clean"])

    def test_media_reports_its_own_asymmetric_bucket(self) -> None:
        family = compare_family({}, {"images/posts/a.png": "a" * 64}, asymmetric=True)

        self.assertEqual(family["upstream_unreferenced"], ["images/posts/a.png"])
        self.assertEqual(family["upstream_unreferenced_count"], 1)
        self.assertEqual(family["missing"], [])
        self.assertTrue(family["clean"])


class ConventionTests(TestCase):
    """The declarations `scripts/tests/test_prod_conventions.py` reads."""

    def test_the_module_declares_a_git_synchronized_non_bootstrapping_entry_point(self) -> None:
        import scripts.prod.sync_content_verify as module

        self.assertEqual(module.SYNC_MODEL, "git-synchronized")
        self.assertFalse(module.BOOTSTRAPS_EMPTY_DATABASE)
        self.assertIn("from scripts.prod.target import", SCRIPT.read_text(encoding="utf-8"))

    def test_selection_refuses_rather_than_falling_back_to_a_hardcoded_name(self) -> None:
        """A stable id written into the script would have answered anyway."""

        register_source(enabled=False)
        register_source(stable_id="dtc-podwiki", repository_name="podwiki")
        with self.assertRaises(ContentDriftRefused) as raised:
            select_editorial_source()
        self.assertEqual(raised.exception.condition, "editorial-source-missing")
