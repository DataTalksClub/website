"""A dedicated worker executes durable jobs without a Relay task round trip."""

import io
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from community_base.jobs.dispatch import dispatch_after_commit
from community_base.jobs.models import JobIntent
from django.core.management import call_command, get_commands, load_command_class
from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.management.commands.jobs_worker import Command as WorkerCommand
from core.tests import test_bootstrap_context


class WorkerSettingsTests(SimpleTestCase):
    def test_entrypoint_preserves_other_worker_environments_and_executes_worker(self):
        root = Path(__file__).resolve().parents[2]
        scratch = root / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            output = directory / "commands"
            uv = directory / "uv"
            uv.write_text(
                '#!/bin/sh\nprintf "%s:%s\\n" "$DJANGO_SETTINGS_MODULE" "$*" '
                '>> "$WORKER_TEST_OUTPUT"\nexit 9\n'
            )
            uv.chmod(0o755)
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
                [
                    "website.settings.development:run --no-sync python manage.py jobs_worker",
                ],
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
        self.assertEqual([call.args for call in sleep.call_args_list], [(10,), (10,)])

    def test_failure_does_not_skip_sweep_sleep_or_next_poll(self):
        errors = io.StringIO()
        command = WorkerCommand(stdout=io.StringIO(), stderr=errors)
        with (
            patch(
                "core.management.commands.jobs_worker.call_command",
                side_effect=[RuntimeError("private job data"), None, None, None],
            ) as poll,
            patch("core.management.commands.jobs_worker.close_old_connections"),
            patch(
                "core.management.commands.jobs_worker.time.sleep",
                side_effect=[None, KeyboardInterrupt],
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                command.handle()
        self.assertEqual(
            [call.args[0] for call in poll.call_args_list], ["jobs_run_due", "jobs_sweep"] * 2
        )
        self.assertIn("jobs_run_due failed (RuntimeError)", errors.getvalue())
        self.assertNotIn("private job data", errors.getvalue())


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
