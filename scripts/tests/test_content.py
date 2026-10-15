"""Parser and dispatch contract for ``scripts/content.py``.

Every advertised subcommand is driven through ``main`` with the subprocess
boundary replaced, so the test proves which script each workflow dispatches,
that the script exists in the tree, and that no network or database is touched.
The retired editorial ``checkout`` and ``drift`` commands dispatched to the
deleted ``scripts/prod/sync_content_verify.py`` and must stay unadvertised.
"""

from __future__ import annotations

import argparse
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts import content
from scripts.content import PROJECT_ROOT, build_parser, main

SUPPORTED_COMMANDS = ("sources", "pull-plan", "checkouts", "pull")


def _subcommands() -> set[str]:
    parser = build_parser()
    (action,) = (
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return set(action.choices)


class _Dispatch:
    """Record each ``subprocess.run`` call instead of starting a process."""

    def __init__(self, stdout: str = "") -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.stdout = stdout

    def __call__(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((tuple(command), kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")

    @property
    def scripts(self) -> list[str]:
        return [command[4] for command, _ in self.calls]


class ContentCommandParserTests(unittest.TestCase):
    def test_exactly_the_supported_workflows_are_advertised(self) -> None:
        self.assertEqual(_subcommands(), set(SUPPORTED_COMMANDS))
        for command in SUPPORTED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(build_parser().parse_args([command]).command, command)

    def test_the_retired_editorial_commands_are_rejected(self) -> None:
        for command in ("checkout", "drift"):
            with self.subTest(command=command), redirect_stdout(StringIO()):
                with mock.patch("sys.stderr", StringIO()), self.assertRaises(SystemExit):
                    build_parser().parse_args([command])

    def test_help_lists_only_the_supported_workflows(self) -> None:
        text = build_parser().format_help()
        for command in SUPPORTED_COMMANDS:
            self.assertIn(command, text)
        self.assertNotIn("drift", text)
        self.assertNotIn("sync_content_verify", Path(content.__file__).read_text())


class ContentCommandDispatchTests(unittest.TestCase):
    def _main(self, argv: list[str], stdout: str = "") -> tuple[int, _Dispatch]:
        dispatch = _Dispatch(stdout)
        with mock.patch.object(content.subprocess, "run", dispatch):
            with mock.patch.dict("os.environ", {}, clear=False) as environment:
                for name in ("CONTENT_DATABASE", "CONTENT_CHECKOUT_ROOT", "CONTENT_GIT_HOST"):
                    environment.pop(name, None)
                status = main(argv)
        return status, dispatch

    def assertDispatchedScriptsExist(self, dispatch: _Dispatch) -> None:
        self.assertTrue(dispatch.calls)
        for command, kwargs in dispatch.calls:
            self.assertEqual(command[:4], ("uv", "run", "--frozen", "python"))
            self.assertTrue((PROJECT_ROOT / command[4]).is_file(), command[4])
            self.assertEqual(kwargs["cwd"], PROJECT_ROOT)
            self.assertIs(kwargs["check"], True)

    def test_sources_registers_into_the_named_database(self) -> None:
        status, dispatch = self._main(["sources", "--database", ".tmp/a.sqlite3"])
        self.assertEqual(status, 0)
        self.assertEqual(
            [command for command, _ in dispatch.calls],
            [
                (
                    "uv",
                    "run",
                    "--frozen",
                    "python",
                    "scripts/prod/sync_course_repository_sources.py",
                    "--database",
                    ".tmp/a.sqlite3",
                )
            ],
        )
        self.assertDispatchedScriptsExist(dispatch)

    def test_pull_plan_prints_the_registered_checkout_plan(self) -> None:
        status, dispatch = self._main(["pull-plan"])
        self.assertEqual(status, 0)
        ((command, _),) = dispatch.calls
        self.assertEqual(
            command[4:],
            (
                "scripts/prod/sync_course_repositories.py",
                "--database",
                ".tmp/local.sqlite3",
                "--checkout-plan",
                "--from-disk",
                ".tmp/course-checkouts",
            ),
        )
        self.assertDispatchedScriptsExist(dispatch)

    def test_checkouts_feeds_the_plan_to_the_checked_refresh_helper(self) -> None:
        root = ".tmp/content-command-test-checkouts"
        with mock.patch.object(content.Path, "mkdir"):
            status, dispatch = self._main(
                ["checkouts", "--checkout-root", root], stdout="plan-line\n"
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            dispatch.scripts,
            ["scripts/prod/sync_course_repositories.py", "scripts/checkout_refresh.py"],
        )
        plan, refresh = dispatch.calls
        self.assertIn("--checkout-plan", plan[0])
        self.assertIs(plan[1]["capture_output"], True)
        self.assertEqual(refresh[0][5:], ("--host", "https://github.com"))
        self.assertEqual(refresh[1]["input"], "plan-line\n")
        self.assertDispatchedScriptsExist(dispatch)

    def test_pull_ingests_from_disk_and_forwards_extra_arguments(self) -> None:
        status, dispatch = self._main(["pull", "--checkout-root", ".tmp/x", "one", "--dry-run"])
        self.assertEqual(status, 0)
        ((command, _),) = dispatch.calls
        self.assertEqual(
            command[4:],
            (
                "scripts/prod/sync_course_repositories.py",
                "--database",
                ".tmp/local.sqlite3",
                "--from-disk",
                ".tmp/x",
                "one",
                "--dry-run",
            ),
        )
        self.assertDispatchedScriptsExist(dispatch)

    def test_a_failed_step_returns_its_exit_code(self) -> None:
        def fail(command, **kwargs):  # type: ignore[no-untyped-def]
            raise subprocess.CalledProcessError(3, command)

        with mock.patch.object(content.subprocess, "run", fail):
            self.assertEqual(main(["sources"]), 3)


if __name__ == "__main__":
    unittest.main()
