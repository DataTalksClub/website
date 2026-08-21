from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from ci.flake_policy import FLAKE_POLICY_LINE


def _quarantine_marker(item: Any) -> Any | None:
    markers = tuple(item.iter_markers("quarantine"))
    if not markers:
        return None
    if len(markers) != 1 or markers[0].args or set(markers[0].kwargs) != {"issue"}:
        raise pytest.UsageError(
            f"{item.nodeid}: quarantine marker must be exactly "
            "@pytest.mark.quarantine(issue=<number>)"
        )
    issue = markers[0].kwargs["issue"]
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise pytest.UsageError(
            f"{item.nodeid}: quarantine marker requires a positive tracking issue number"
        )
    return markers[0]


class _PolicyPlugin:
    def __init__(self) -> None:
        self.selected = 0
        self.quarantined = 0
        self.rerun = 0
        self.outcomes: dict[str, str] = {}
        self.validated: set[str] = set()

    def _validate_and_count(self, items: Iterable[Any]) -> None:
        for item in items:
            if item.nodeid in self.validated:
                continue
            self.validated.add(item.nodeid)
            if _quarantine_marker(item) is not None:
                self.quarantined += 1

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        self._validate_and_count(items)

    def pytest_deselected(self, items: list[Any]) -> None:
        self._validate_and_count(items)

    def pytest_collection_finish(self, session: Any) -> None:
        self.selected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.outcome == "rerun":
            self.rerun += 1
            return
        if report.when == "call":
            self.outcomes[report.nodeid] = report.outcome
        elif report.when == "setup" and report.outcome in {"failed", "skipped"}:
            self.outcomes[report.nodeid] = report.outcome
        elif report.when == "teardown" and report.outcome == "failed":
            self.outcomes[report.nodeid] = report.outcome

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        passed = sum(outcome == "passed" for outcome in self.outcomes.values())
        failed = sum(outcome == "failed" for outcome in self.outcomes.values())
        skipped = sum(outcome == "skipped" for outcome in self.outcomes.values())
        complete = exitstatus in {0, 5} and len(self.outcomes) == self.selected and self.rerun == 0
        terminalreporter = session.config.pluginmanager.getplugin("terminalreporter")
        line = (
            f"{FLAKE_POLICY_LINE} attempted={self.selected} passed={passed} failed={failed} "
            f"skipped={skipped} rerun={self.rerun} quarantined={self.quarantined} "
            f"complete={int(complete)}"
        )
        if terminalreporter is not None:
            terminalreporter.write_line(line)
        else:
            print(line)


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers",
        "quarantine(issue): tracked Playwright case excluded from blocking targets "
        "and run by the scheduled quarantine monitor",
    )
    config.pluginmanager.register(_PolicyPlugin(), "playwright-flake-policy")
