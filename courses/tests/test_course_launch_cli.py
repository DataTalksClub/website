"""Permissions, API concurrency, Studio forms and CLI credential boundaries."""

import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from scripts.course_launch import main
from scripts.course_launch_client import configuration, write_profile


class CourseLaunchCliTests(TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / "cli-test-profile.json"
        self.path.unlink(missing_ok=True)
        self.environment = patch.dict(
            os.environ,
            {
                "DTC_PROFILE_PATH": str(self.path),
                "DTC_TOKEN": "synthetic-cli-token",
                "DTC_URL": "http://localhost:8000",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.path.unlink, missing_ok=True)

    def test_profile_permissions_env_override_and_logout(self):
        write_profile({"url": "http://localhost:8000", "token": "stored-synthetic"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(configuration()[1], "synthetic-cli-token")
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["logout", "--json"]), 0)
        self.assertFalse(self.path.exists())

    def test_cli_schedule_builds_revision_and_confirmation_contract(self):
        with (
            patch("scripts.course_launch.request", return_value={"status": "scheduled"}) as send,
            redirect_stdout(StringIO()),
        ):
            result = main(
                [
                    "announcements",
                    "schedule",
                    "--id",
                    "synthetic",
                    "--expected-revision",
                    "3",
                    "--confirmed",
                    "--scheduled-at",
                    "2027-01-01T17:00:00+00:00",
                    "--json",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(send.call_args.kwargs["revision"], 3)
        self.assertTrue(send.call_args.kwargs["payload"]["confirmed"])
        self.assertEqual(send.call_args.args[1], "/api/v1/admin/course-launch/dispatch")

    def test_login_never_echoes_or_serializes_token(self):
        output = StringIO()
        with (
            patch("scripts.course_launch.getpass.getpass", return_value="synthetic-private"),
            patch("scripts.course_launch.request", return_value={"principal_id": "test"}),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["login", "--json"]), 0)
        self.assertNotIn("synthetic-private", output.getvalue())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_json_flag_selects_machine_output_and_default_is_human(self):
        arguments = ["announcements", "cancel", "--id", "synthetic", "--expected-revision", "1"]
        human, machine = StringIO(), StringIO()
        with patch(
            "scripts.course_launch.request", return_value={"id": "synthetic", "status": "cancelled"}
        ):
            with redirect_stdout(human):
                self.assertEqual(main(arguments), 0)
            with redirect_stdout(machine):
                self.assertEqual(main([*arguments, "--json"]), 0)
        self.assertIn("Status: cancelled", human.getvalue())
        self.assertEqual(json.loads(machine.getvalue())["status"], "cancelled")

    def test_subcommand_help_has_an_actionable_example(self):
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["announcements", "schedule", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("Example:", output.getvalue())
        self.assertIn("--scheduled-at", output.getvalue())

    def test_http_error_surfaces_safe_api_guidance(self):
        from email.message import Message
        from io import BytesIO
        from urllib.error import HTTPError

        from scripts.course_launch_client import ClientError, request

        failure = HTTPError(
            "http://localhost:8000/api/v1/admin/course-launch",
            400,
            "Bad request",
            Message(),
            BytesIO(b'{"error":{"message":"Use an ISO 8601 timestamp with a UTC offset."}}'),
        )
        with patch("scripts.course_launch_client.build_opener") as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(ClientError) as caught:
                request("GET", "/api/v1/admin/course-launch")
        self.assertEqual(caught.exception.exit_code, 6)
        self.assertEqual(str(caught.exception), "Use an ISO 8601 timestamp with a UTC offset.")
