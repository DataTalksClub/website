"""Synthetic references only; real solutions must never be public fixtures."""

from datetime import timedelta
from unittest.mock import patch

import yaml
from django.db import DataError, OperationalError
from django.test import Client, SimpleTestCase
from django.utils import timezone

from courses.models.homework_solution import HomeworkSolution
from courses.tests.homework_view_base import HomeworkDetailViewTestBase
from courses.views.url_utils import cohort_url
from scripts.prod.sync_homework_solutions import (
    SolutionSyncError,
    checkout_snapshot,
    github_snapshot,
    sync_snapshot,
    sync_solutions,
)


class HomeworkSolutionTests(HomeworkDetailViewTestBase):
    def setUp(self):
        super().setUp()
        self.path = (
            f"{self.course.course.slug}/{self.course.year}/{self.homework.slug}/answers.yaml"
        )
        self.manifest = {
            "schema_version": 1,
            "cohort": self.course.identifier,
            "homework_slug": self.homework.slug,
            "published": False,
            "publish_at": None,
            "answers": [{"question_id": "synthetic", "answer": "synthetic-private-reference"}],
        }
        self.solution_url = cohort_url(
            self.course, "cohort_homework_solution", homework_slug=self.homework.slug
        )

    def snapshot(self, manifest=None, markdown="## Synthetic explanation\n\nSynthetic reference."):
        documents = {
            self.path: yaml.safe_dump(self.manifest if manifest is None else manifest),
            self.path.replace("answers.yaml", "solution.md"): markdown,
        }
        return "a" * 40, documents, documents.__getitem__

    def test_unpublished_payload_denied_for_every_user_and_scoring_state(self):
        sync_snapshot(self.snapshot())
        for state in ("OP", "CL", "SC"):
            self.homework.state = state
            self.homework.save()
            for logged_in in (False, True):
                client = Client()
                if logged_in:
                    client.force_login(self.user)
                detail = client.get(self.homework_url())
                self.assertEqual(detail.status_code, 200)
                self.assertNotContains(detail, self.solution_url)
                self.assertNotContains(detail, "synthetic-private-reference")
                denied = client.get(self.solution_url)
                self.assertEqual(denied.status_code, 404)
                self.assertNotIn(b"synthetic-private-reference", denied.content)
                self.assertIn("no-store", denied["Cache-Control"])

    def test_published_link_and_page_for_anonymous_and_stepper(self):
        self.manifest["published"] = True
        sync_snapshot(self.snapshot())
        for logged_in in (False, True):
            client = Client()
            if logged_in:
                client.force_login(self.user)
            detail = client.get(self.homework_url())
            self.assertContains(detail, self.solution_url)
            page = client.get(self.solution_url)
            self.assertContains(page, "Synthetic explanation")
            self.assertContains(page, "synthetic-private-reference")
            self.assertContains(page, "Back to Test Homework")
            self.assertIn("noindex", page["X-Robots-Tag"])
            self.assertIn("no-store", page["Cache-Control"])

    def test_date_boundary_release_and_revocation(self):
        instant = timezone.now()
        self.manifest["publish_at"] = instant.isoformat()
        sync_snapshot(self.snapshot())
        with patch(
            "courses.models.homework_solution.timezone.now",
            return_value=instant - timedelta(seconds=1),
        ):
            self.assertEqual(self.client.get(self.solution_url).status_code, 404)
        with patch("courses.models.homework_solution.timezone.now", return_value=instant):
            self.assertEqual(self.client.get(self.solution_url).status_code, 200)
        with patch(
            "courses.models.homework_solution.timezone.now",
            return_value=instant + timedelta(seconds=1),
        ):
            self.assertEqual(self.client.get(self.solution_url).status_code, 200)
        self.manifest["publish_at"] = None
        sync_snapshot(self.snapshot())
        self.assertEqual(self.client.get(self.solution_url).status_code, 404)

    def test_published_flag_overrides_future_date_and_does_not_change_grading(self):
        self.manifest.update(
            published=True, publish_at=(timezone.now() + timedelta(days=1)).isoformat()
        )
        sync_snapshot(self.snapshot())
        self.assertEqual(self.client.get(self.solution_url).status_code, 200)
        self.homework.refresh_from_db()
        self.assertEqual(self.homework.state, "OP")
        self.assertEqual(self.question1.correct_answer, "1")

    def test_markdown_sanitized_and_structured_answers_escaped(self):
        self.manifest["published"] = True
        self.manifest["answers"][0]["answer"] = '<img src=x onerror="alert(1)">'
        sync_snapshot(
            self.snapshot(
                markdown="## Synthetic\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))"
            )
        )
        page = self.client.get(self.solution_url)
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertNotContains(page, 'href="javascript:')
        self.assertNotContains(page, "<img src=x")

    def test_directory_name_is_independent_of_exact_homework_slug(self):
        sha, docs, read = self.snapshot()
        docs = {
            path.replace(f"/{self.homework.slug}/", "/04-devops/"): value
            for path, value in docs.items()
        }
        sync_snapshot((sha, docs, docs.__getitem__))
        self.assertEqual(HomeworkSolution.objects.get().homework_id, self.homework.pk)
        self.assertIn("/04-devops/", HomeworkSolution.objects.get().source_path)

    def test_idempotent_and_source_commit_recorded(self):
        sync_snapshot(self.snapshot())
        pk = HomeworkSolution.objects.get().pk
        sync_snapshot(self.snapshot())
        row = HomeworkSolution.objects.get()
        self.assertEqual(row.pk, pk)
        self.assertEqual(row.source_commit, "a" * 40)
        self.assertEqual(row.source_path, self.path)

    def test_invalid_manifests_preserve_prior_snapshot(self):
        sync_snapshot(self.snapshot())
        invalid_updates: tuple[dict[str, object], ...] = (
            {"published": "false"},
            {"cohort": "missing"},
            {"publish_at": "2026-01-01"},
            {"schema_version": True},
            {"answers": []},
            {"extra": "value"},
            {"answers": [{"question_id": "../bad", "answer": "synthetic"}]},
        )
        for updates in invalid_updates:
            bad = {**self.manifest, **updates}
            with self.assertRaises((SolutionSyncError, ValueError)):
                sync_snapshot(self.snapshot(bad))
            self.assertEqual(HomeworkSolution.objects.get().source_commit, "a" * 40)

    def test_whole_snapshot_rolls_back_and_removed_source_is_revoked(self):
        sync_snapshot(self.snapshot())
        sha, docs, read = self.snapshot({**self.manifest, "published": True})
        docs["zzz/2026/missing/answers.yaml"] = yaml.safe_dump(self.manifest)
        docs["zzz/2026/missing/solution.md"] = "Synthetic"
        with self.assertRaises(SolutionSyncError):
            sync_snapshot((sha, docs, docs.__getitem__))
        self.assertFalse(HomeworkSolution.objects.get().published)
        # Removal is tested with a second valid target retained in the snapshot.
        from courses.models import Homework

        other = Homework.objects.create(
            course=self.course, slug="other", title="Other", due_date=timezone.now()
        )
        other_path = self.path.replace(f"/{self.homework.slug}/", "/other/")
        docs = {
            other_path: yaml.safe_dump({**self.manifest, "homework_slug": other.slug}),
            other_path.replace("answers.yaml", "solution.md"): "Synthetic",
        }
        sync_snapshot((sha, docs, docs.__getitem__))
        self.assertFalse(HomeworkSolution.objects.filter(homework=self.homework).exists())
        self.assertTrue(HomeworkSolution.objects.filter(homework=other).exists())

    def test_duplicate_yaml_keys_and_path_escape_denied(self):
        sha, docs, read = self.snapshot()
        docs[self.path] += "published: true\n"
        with self.assertRaises(SolutionSyncError):
            sync_snapshot((sha, docs, docs.__getitem__))
        with self.assertRaises(SolutionSyncError):
            sync_snapshot((sha, {"../2026/x/answers.yaml": ""}, lambda path: ""))

    def test_command_redacts_provider_and_parser_errors(self):
        with patch(
            "scripts.prod.sync_homework_solutions.github_snapshot",
            side_effect=ValueError("synthetic-private-reference"),
        ):
            with self.assertRaises(SolutionSyncError) as raised:
                sync_solutions()
            self.assertNotIn("synthetic-private-reference", str(raised.exception))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SolutionSyncError):
                github_snapshot()

    def test_database_errors_are_redacted_and_failed_write_is_rolled_back(self):
        sync_snapshot(self.snapshot())
        for error_type in (DataError, OperationalError):

            def failed_write(*args, error_type=error_type, **kwargs):
                HomeworkSolution.objects.filter(homework=self.homework).update(published=True)
                raise error_type("synthetic-private-diagnostic")

            with (
                patch(
                    "scripts.prod.sync_homework_solutions.checkout_snapshot",
                    return_value=self.snapshot(),
                ),
                patch.object(
                    HomeworkSolution.objects, "update_or_create", side_effect=failed_write
                ),
                self.assertRaises(SolutionSyncError) as raised,
            ):
                sync_solutions(checkout="synthetic-private-checkout")
            self.assertEqual(
                str(raised.exception), "Private solutions sync failed; no database changes applied."
            )
            self.assertNotIn("synthetic-private-diagnostic", str(raised.exception))
            self.assertFalse(HomeworkSolution.objects.get().published)

    def test_wrong_cohort_direct_route_cannot_read_solution(self):
        self.manifest["published"] = True
        sync_snapshot(self.snapshot())
        self.assertEqual(
            self.client.get(
                self.solution_url.replace(f"/{self.course.identifier}/", "/another/", 1)
            ).status_code,
            404,
        )

    def test_empty_snapshot_never_revokes_existing_data(self):
        with self.assertRaises(SolutionSyncError):
            sync_snapshot(("a" * 40, {}, lambda path: ""))


class SolutionSnapshotSecurityTests(SimpleTestCase):
    def test_checkout_symlinks_are_never_followed(self):
        def git(command, **kwargs):
            if "rev-parse" in command:
                return b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            if "ls-tree" in command:
                return (
                    b"120000 blob bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tx/2026/y/solution.md\0"
                )
            raise AssertionError("A symlink must not be read")

        with patch("scripts.prod.sync_homework_solutions.subprocess.check_output", side_effect=git):
            _, _, read = checkout_snapshot("/synthetic-private-checkout")
            with self.assertRaises(SolutionSyncError):
                read("x/2026/y/solution.md")

    def test_checkout_reads_commit_blobs_not_working_tree(self):
        commands = []

        def git(command, **kwargs):
            commands.append(command)
            if "rev-parse" in command:
                return b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            if "ls-tree" in command:
                return (
                    b"100644 blob bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tx/2026/y/solution.md\0"
                )
            if "-s" in command:
                return b"9\n"
            return b"Synthetic"

        with patch("scripts.prod.sync_homework_solutions.subprocess.check_output", side_effect=git):
            _, _, read = checkout_snapshot("/synthetic-private-checkout")
            self.assertEqual(read("x/2026/y/solution.md"), "Synthetic")
        self.assertTrue(any("--end-of-options" in command for command in commands))
        self.assertTrue(any(command[-2:] == ["blob", "b" * 40] for command in commands))

    def test_github_ref_and_private_repository_checked_before_content(self):
        with patch.dict("os.environ", {"ZOOMCAMP_SOLUTIONS_TOKEN": "synthetic-token"}):
            with self.assertRaises(SolutionSyncError):
                github_snapshot("../untrusted")
            with patch("scripts.prod.sync_homework_solutions.requests.Session") as session:
                response = session.return_value.get.return_value
                response.status_code = 200
                response.iter_content.return_value = [b'{"private": false}']
                with self.assertRaises(SolutionSyncError):
                    github_snapshot()
                self.assertEqual(session.return_value.get.call_count, 1)
                self.assertEqual(
                    session.return_value.get.call_args.args[0],
                    "https://api.github.com/repos/DataTalksClub/zoomcamp-solutions",
                )
                self.assertFalse(session.return_value.get.call_args.kwargs["allow_redirects"])
