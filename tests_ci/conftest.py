"""Keep the local verification tests hermetic to declared-execution variables.

The CI workflow declares the container component's execution machine and
hosted image family at the workflow level, so every job -- including the one
running this directory -- inherits them.  Those declarations are planning-time
review inputs: a plan built with them authorizes the container component to
execute on the declared runner, while a process that plans and runs in one
place executes on its own host and must fail closed against any other machine.
Unit tests here simulate that single-host planner-plus-runner flow, so the
ambient declarations are removed for every test; the declaration contract has
its own tests, which set the values they need explicitly.
"""

from __future__ import annotations

import pytest

from ci.verification import (
    COMPONENT_ARCHITECTURE_VARIABLE,
    COMPONENT_RUNNER_IMAGE_VARIABLE,
)


@pytest.fixture(autouse=True)
def _remove_declared_execution_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        *COMPONENT_ARCHITECTURE_VARIABLE.values(),
        *COMPONENT_RUNNER_IMAGE_VARIABLE.values(),
    ):
        monkeypatch.delenv(variable, raising=False)
