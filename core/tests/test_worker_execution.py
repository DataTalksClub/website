"""A dedicated worker executes durable jobs without a Relay task round trip."""

import importlib.util
import io
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import yaml
from community_base.jobs.dispatch import dispatch_after_commit
from community_base.jobs.models import JobIntent
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.management.commands.jobs_pending_check import Command as PendingJobCheckCommand
from core.management.commands.jobs_worker import Command as WorkerCommand
from core.tests import test_bootstrap_context
from core.worker_healthcheck import heartbeat_is_fresh, touch_worker_heartbeat


class WorkerSettingsTests(SimpleTestCase):
    def test_gunicorn_warmup_failure_is_visible_without_exception_text(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "website_gunicorn_config",
            root / "gunicorn.conf.py",
        )
        assert spec is not None
        assert spec.loader is not None
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)

        with (
            patch("logging.getLogger") as get_logger,
            patch("django.urls.get_resolver", side_effect=RuntimeError("private warmup data")),
        ):
            config.post_worker_init(None)

        logger = get_logger.return_value
        logger.warning.assert_called_once()
        message, exception_type = logger.warning.call_args.args
        self.assertIn("gunicorn_warmup_failed", message)
        self.assertEqual(exception_type, "RuntimeError")
        self.assertNotIn("private warmup data", str(logger.warning.call_args))

    def test_coolify_web_migrates_before_serving_and_releasing_worker(self):
        root = Path(__file__).resolve().parents[2]
        compose = yaml.safe_load((root / "docker-compose.coolify.yaml").read_text())
        web = compose["services"]["web"]
        worker = compose["services"]["worker"]
        self.assertEqual(web["command"], ["web-migrate"])
        self.assertEqual(web["stop_grace_period"], "40s")
        self.assertEqual(web["healthcheck"]["interval"], "30s")
        self.assertEqual(web["healthcheck"]["start_period"], "90s")
        self.assertEqual(
            worker["depends_on"]["web"]["condition"],
            "service_healthy",
        )
        self.assertEqual(worker["cpus"], "1.0")
        self.assertEqual(worker["mem_limit"], "240m")
        self.assertEqual(
            worker["healthcheck"]["test"],
            ["CMD", "/app/.venv/bin/python", "-S", "-m", "core.worker_healthcheck"],
        )
        scratch = root / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            output = directory / "commands"
            python = directory / "python"
            python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$WORKER_TEST_OUTPUT"\n'
                'case "$*" in *"manage.py web_bootstrap"*) exit "$BOOTSTRAP_EXIT";;'
                ' *"manage.py sync_relay_schedules"*) exit "$SYNC_EXIT";;'
                ' *"manage.py import_mail_templates"*) exit "$IMPORT_EXIT";; esac\n'
                "exit 0\n"
            )
            python.chmod(0o755)
            gunicorn = directory / "gunicorn"
            gunicorn.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WORKER_TEST_OUTPUT"\nexit 9\n')
            gunicorn.chmod(0o755)
            release = [
                "manage.py web_bootstrap --noinput",
                "manage.py sync_relay_schedules",
                "manage.py import_mail_templates",
            ]
            gunicorn_command = "website.wsgi:application "
            for mode, exits, expected_exit, expected_commands in (
                ("web-migrate", ("0", "0", "0"), 9, [*release, gunicorn_command]),
                ("web-migrate", ("21", "0", "0"), 21, release[:1]),
                ("web-migrate", ("0", "22", "0"), 9, [*release, gunicorn_command]),
                ("web-migrate", ("0", "0", "23"), 9, [*release, gunicorn_command]),
                ("web", ("0", "0", "0"), 9, [gunicorn_command]),
            ):
                with self.subTest(mode=mode, exits=exits):
                    output.write_text("")
                    result = subprocess.run(
                        ["/bin/sh", str(root / "entrypoint.sh"), mode],
                        env={
                            **os.environ,
                            "PATH": str(directory),
                            "WORKER_TEST_OUTPUT": str(output),
                            "BOOTSTRAP_EXIT": exits[0],
                            "SYNC_EXIT": exits[1],
                            "IMPORT_EXIT": exits[2],
                        },
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, expected_exit, result.stderr)
                    commands = output.read_text().splitlines()
                    self.assertEqual(len(commands), len(expected_commands))
                    for actual, expected in zip(commands, expected_commands, strict=True):
                        self.assertTrue(actual.startswith(expected), actual)
                    if exits[1] != "0":
                        self.assertIn("Relay schedule sync failed", result.stderr)
                    if exits[2] != "0":
                        self.assertIn("Relay mail-template import failed", result.stderr)

    def test_entrypoint_preserves_other_worker_environments_and_executes_worker(self):
        root = Path(__file__).resolve().parents[2]
        scratch = root / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            output = directory / "commands"
            python = directory / "python"
            python.write_text(
                '#!/bin/sh\nprintf "%s:%s\\n" "$DJANGO_SETTINGS_MODULE" "$*" '
                '>> "$WORKER_TEST_OUTPUT"\nexit 9\n'
            )
            python.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", str(root / "entrypoint.sh"), "worker"],
                env={
                    **os.environ,
                    "PATH": str(directory),
                    "DJANGO_SETTINGS_MODULE": "website.settings.development",
                    "WORKER_TEST_OUTPUT": str(output),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 9, result.stderr)
            self.assertEqual(
                output.read_text().splitlines(),
                ["website.settings.development:manage.py jobs_worker"],
            )

    def test_worker_keeps_production_security_and_relay_email(self):
        result = test_bootstrap_context.BootstrapParserTests().import_deployed_settings(
            "production",
            command=(
                "from website.settings import worker, production; "
                "assert worker.COMMUNITY_BASE['JOBS_BACKEND'] == 'sync'; "
                "assert production.COMMUNITY_BASE['JOBS_BACKEND'] == 'relay'; "
                "assert worker.COMMUNITY_BASE['MAIL_BACKEND'] == 'relay'; "
                "assert worker.DATABASES == production.DATABASES; "
                "assert worker.SECURE_SSL_REDIRECT and not worker.DEBUG"
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class WorkerLoopTests(SimpleTestCase):
    def test_polls_both_commands_without_checks_and_pauses_between_ticks(self):
        command = WorkerCommand(stdout=io.StringIO(), stderr=io.StringIO())
        with (
            patch("core.management.commands.jobs_worker.call_command") as poll,
            patch("core.management.commands.jobs_worker.close_old_connections") as close,
            patch("core.management.commands.jobs_worker.touch_worker_heartbeat") as heartbeat,
            patch(
                "core.management.commands.jobs_worker.time.sleep",
                side_effect=[None, KeyboardInterrupt],
            ) as sleep,
            patch.object(command, "check", side_effect=AssertionError("worker ran checks")),
        ):
            with self.assertRaises(KeyboardInterrupt):
                command.run_from_argv(["manage.py", "jobs_worker"])
        self.assertEqual(
            [call.args[0] for call in poll.call_args_list], ["jobs_run_due", "jobs_sweep"] * 2
        )
        for call in poll.call_args_list:
            self.assertEqual(call.kwargs["limit"], 100)
            self.assertTrue(call.kwargs["skip_checks"])
            self.assertIs(call.kwargs["stdout"], command.stdout)
        self.assertEqual(close.call_count, 2)
        self.assertEqual(heartbeat.call_count, 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(10,), (10,)])

    def test_failure_does_not_skip_sweep_sleep_or_next_poll(self):
        command = WorkerCommand(stdout=io.StringIO(), stderr=io.StringIO())
        with (
            patch(
                "core.management.commands.jobs_worker.call_command",
                side_effect=[RuntimeError("private job data"), None, None, None],
            ) as poll,
            patch("core.management.commands.jobs_worker.close_old_connections"),
            patch("core.management.commands.jobs_worker.touch_worker_heartbeat"),
            patch(
                "core.management.commands.jobs_worker.time.sleep",
                side_effect=[None, KeyboardInterrupt],
            ),
        ):
            with self.assertLogs("core.management.commands.jobs_worker", level="ERROR") as logs:
                with self.assertRaises(KeyboardInterrupt):
                    command.handle()
        self.assertEqual(
            [call.args[0] for call in poll.call_args_list], ["jobs_run_due", "jobs_sweep"] * 2
        )
        self.assertIn("jobs_run_due failed (RuntimeError)", logs.output[0])
        self.assertNotIn("private job data", logs.output[0])
        extra = logs.records[0].__dict__
        self.assertTrue(extra["traceback"])
        self.assertEqual(extra["exception_type"], "RuntimeError")

    def test_compose_worker_healthcheck_runs_stdlib_only_and_detects_stale_heartbeat(self):
        root = Path(__file__).resolve().parents[2]
        heartbeat = root / ".tmp" / "worker-heartbeat-test"
        heartbeat.parent.mkdir(exist_ok=True)
        heartbeat.write_text("alive")
        self.addCleanup(heartbeat.unlink, missing_ok=True)
        old_time = 1
        os.utime(heartbeat, (old_time, old_time))

        with patch("core.worker_healthcheck.HEARTBEAT_FILE", heartbeat):
            self.assertFalse(heartbeat_is_fresh(now=100, max_age_seconds=90))

        compose = yaml.safe_load((root / "docker-compose.coolify.yaml").read_text())
        self.assertEqual(
            compose["services"]["worker"]["healthcheck"]["test"],
            ["CMD", "/app/.venv/bin/python", "-S", "-m", "core.worker_healthcheck"],
        )

    def test_worker_healthcheck_accepts_a_recent_heartbeat(self):
        scratch = Path(__file__).resolve().parents[2] / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as path:
            heartbeat = Path(path) / "heartbeat"
            with patch("core.worker_healthcheck.HEARTBEAT_FILE", heartbeat):
                touch_worker_heartbeat()
                self.assertTrue(heartbeat_is_fresh())


class WorkerExecutionTests(TransactionTestCase):
    @override_settings(COMMUNITY_BASE={"JOBS_BACKEND": "sync"})
    def test_persistent_worker_executes_real_commands_and_then_sleeps(self):
        with patch("community_base.jobs.dispatch._best_effort_submit"):
            with transaction.atomic():
                intent, _ = dispatch_after_commit("system.noop", "persistent-worker-test:1", {})
        output = io.StringIO()
        with (
            patch("community_base.jobs.backends.relay.submit") as relay,
            patch(
                "core.management.commands.jobs_worker.time.sleep", side_effect=KeyboardInterrupt
            ) as sleep,
        ):
            with self.assertRaises(KeyboardInterrupt):
                call_command("jobs_worker", stdout=output)
        relay.assert_not_called()
        sleep.assert_called_once_with(10)
        intent.refresh_from_db()
        self.assertEqual(intent.status, JobIntent.Status.SUCCEEDED)
        self.assertEqual(intent.attempts, 1)
        self.assertIsNone(intent.lease_token)
        self.assertIn("found=1 submitted=1", output.getvalue())
        self.assertIn("recovered=0 dead=0", output.getvalue())

    @override_settings(COMMUNITY_BASE={"JOBS_BACKEND": "sync"})
    def test_empty_worker_commands_skip_checks_and_still_report_results(self):
        for name, expected in (
            ("jobs_run_due", "found=0 submitted=0"),
            ("jobs_sweep", "recovered=0 dead=0"),
        ):
            with self.subTest(command=name):
                command = load_command_class(get_commands()[name], name)
                output = io.StringIO()
                command.stdout = command.stdout.__class__(output)
                with patch.object(command, "check", side_effect=AssertionError("poll ran checks")):
                    command.run_from_argv(["manage.py", name, "--skip-checks", "--limit", "100"])
                self.assertEqual(output.getvalue().strip(), expected)

    @override_settings(COMMUNITY_BASE={"JOBS_BACKEND": "sync"})
    def test_due_runner_executes_pending_intent_without_relay(self):
        with patch("community_base.jobs.dispatch._best_effort_submit"):
            with transaction.atomic():
                intent, _ = dispatch_after_commit("system.noop", "worker-test:1", {})
        self.assertEqual(intent.status, JobIntent.Status.PENDING)
        with patch("community_base.jobs.backends.relay.submit") as relay:
            call_command("jobs_run_due", limit=100, stdout=io.StringIO())
            call_command("jobs_sweep", limit=100, stdout=io.StringIO())
        relay.assert_not_called()
        intent.refresh_from_db()
        self.assertEqual(intent.status, JobIntent.Status.SUCCEEDED)
        self.assertEqual(intent.attempts, 1)
        self.assertIsNone(intent.lease_token)
        call_command("jobs_run_due", limit=100, stdout=io.StringIO())
        intent.refresh_from_db()
        self.assertEqual(intent.attempts, 1)


class PendingJobCheckTests(TransactionTestCase):
    def test_check_passes_when_no_due_pending_intent_exceeds_threshold(self):
        output = io.StringIO()
        command = PendingJobCheckCommand(stdout=output)

        command.handle(max_age_minutes=30)

        self.assertIn("pending_overdue=0", output.getvalue())

    def test_check_fails_with_aggregate_only_evidence_for_overdue_pending(self):
        now = timezone.now()
        old = now - timedelta(hours=2)
        intent = JobIntent.objects.create(
            handler="system.noop",
            key_hash="c" * 64,
            payload={"safe_test_marker": "never-log-payload"},
            payload_hash="d" * 64,
            available_at=old,
        )
        JobIntent.objects.filter(pk=intent.pk).update(created_at=old)
        JobIntent.objects.create(
            handler="system.noop",
            key_hash="e" * 64,
            payload={},
            payload_hash="d" * 64,
            status=JobIntent.Status.SUCCEEDED,
            available_at=old,
        )
        JobIntent.objects.create(
            handler="system.noop",
            key_hash="f" * 64,
            payload={},
            payload_hash="d" * 64,
            available_at=now + timedelta(hours=1),
        )
        errors = io.StringIO()
        command = PendingJobCheckCommand(stdout=io.StringIO(), stderr=errors)

        with self.assertRaises(CommandError):
            command.handle(max_age_minutes=30)

        self.assertIn("pending_count=1", errors.getvalue())
        self.assertIn("oldest_age_minutes=", errors.getvalue())
        self.assertNotIn("never-log-payload", errors.getvalue())
