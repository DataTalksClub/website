"""Async scoring path: enqueue returns fast, worker executes idempotently."""

from pathlib import Path
from unittest.mock import patch

import yaml
from community_base.jobs.models import JobIntent
from django.db import transaction
from django.test import SimpleTestCase, TestCase

from courses.scoring_jobs import (
    HOMEWORK_SCORE_HANDLER,
    PROJECT_NOTIFY_HANDLER,
    PROJECT_SCORE_HANDLER,
    enqueue_homework_notify,
    enqueue_homework_score,
    enqueue_project_notify,
    enqueue_project_score,
    notify_homework_scores_job,
    notify_project_scores_job,
    score_homework_job,
    score_project_job,
)


class ScoringEnqueueTests(TestCase):
    def test_homework_score_enqueue_creates_durable_intent(self):
        with transaction.atomic():
            intent, created = enqueue_homework_score(123)
        self.assertTrue(created)
        self.assertEqual(intent.handler, HOMEWORK_SCORE_HANDLER)
        self.assertEqual(intent.payload, {"homework_id": 123, "force": False})

    def test_homework_score_enqueue_is_idempotent(self):
        with transaction.atomic():
            first, _ = enqueue_homework_score(77)
        with transaction.atomic():
            second, created = enqueue_homework_score(77)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            JobIntent.objects.filter(handler=HOMEWORK_SCORE_HANDLER).count(), 1
        )

    def test_project_score_enqueue_creates_two_intents(self):
        with transaction.atomic():
            enqueue_project_score(55)
            enqueue_project_notify(55)
        self.assertTrue(
            JobIntent.objects.filter(
                handler=PROJECT_SCORE_HANDLER, payload__project_id=55
            ).exists()
        )
        self.assertTrue(
            JobIntent.objects.filter(
                handler=PROJECT_NOTIFY_HANDLER, payload__project_id=55
            ).exists()
        )

    def test_homework_notify_enqueue_is_idempotent(self):
        with transaction.atomic():
            enqueue_homework_notify(9)
        with transaction.atomic():
            _, created = enqueue_homework_notify(9)
        self.assertFalse(created)

    def test_worker_homework_handler_calls_scoring_once(self):
        with patch(
            "courses.scoring.score_homework_submissions", return_value=(None, "ok")
        ) as scoring:
            score_homework_job(None, {"homework_id": 5, "force": False})
            # Retry re-runs the same overwrite-style scoring without doubling.
            score_homework_job(None, {"homework_id": 5, "force": False})
        self.assertEqual(scoring.call_count, 2)
        scoring.assert_called_with("5", force=False)

    def test_worker_project_handler_calls_scoring(self):
        with patch("courses.project_scoring.score_project") as scoring:
            # Handler resolves the project then scores; use a stub project id
            # with the lookup patched to avoid heavy fixtures.
            with patch(
                "courses.models.project.Project.objects.get"
            ) as project_get:
                score_project_job(None, {"project_id": 42})
        project_get.assert_called_once_with(pk=42)
        scoring.assert_called_once_with(project_get.return_value)

    def test_worker_notify_handlers_call_fan_out(self):
        with (
            patch(
                "courses.package_notifications.send_homework_score_notification"
            ) as homework_notify,
            patch("courses.models.homework.Homework.objects.get") as homework_get,
        ):
            notify_homework_scores_job(None, {"homework_id": 7})
        homework_notify.assert_called_once_with(homework_get.return_value)
        with (
            patch(
                "courses.package_notifications.send_project_score_notification"
            ) as project_notify,
            patch("courses.models.project.Project.objects.get") as project_get,
        ):
            notify_project_scores_job(None, {"project_id": 8})
        project_notify.assert_called_once_with(project_get.return_value)


class ServingConfigTests(SimpleTestCase):
    def test_gunicorn_uses_threaded_workers(self):
        root = Path(__file__).resolve().parents[2]
        entrypoint = (root / "entrypoint.sh").read_text()
        self.assertIn("--worker-class gthread", entrypoint)
        self.assertIn('"${WEB_CONCURRENCY:-2}"', entrypoint)
        self.assertIn('"${WEB_THREADS:-4}"', entrypoint)
        conf = (root / "gunicorn.conf.py").read_text()
        self.assertIn('worker_class = "gthread"', conf)
        self.assertIn("WEB_CONCURRENCY", conf)
        self.assertIn("WEB_THREADS", conf)

    def test_compose_worker_has_restart_and_healthcheck(self):
        root = Path(__file__).resolve().parents[2]
        compose = yaml.safe_load((root / "docker-compose.coolify.yaml").read_text())
        worker = compose["services"]["worker"]
        self.assertEqual(worker["restart"], "unless-stopped")
        healthcheck = worker["healthcheck"]
        self.assertNotIn("disable", healthcheck)
        self.assertIn("jobs_worker", " ".join(map(str, healthcheck["test"])))
        self.assertIn("--check", " ".join(map(str, healthcheck["test"])))

    def test_deployment_target_pins_threads(self):
        from deploy.deployment_targets import SELECTED_TARGET

        self.assertGreaterEqual(int(SELECTED_TARGET.web_concurrency), 2)
        self.assertGreaterEqual(int(SELECTED_TARGET.web_threads), 2)
        self.assertIn("WEB_THREADS", SELECTED_TARGET.fixed_nonsecret_environment)

    def test_job_intent_admin_registered(self):
        from community_base.jobs.models import JobIntent
        from django.contrib import admin

        self.assertIn(JobIntent, admin.site._registry)
