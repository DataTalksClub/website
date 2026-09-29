"""Actual task termination is required even after ECS service stability."""

from copy import deepcopy

import pytest

from ci.tests.test_dev_reset_guards import FakeAws
from deploy import dev_reset_ecs, dev_reset_tasks

TARGET = dev_reset_ecs.TARGET
CLUSTER = (
    f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:cluster/{TARGET.ecs_cluster_name}"
)
TASK = (
    f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:task/{TARGET.ecs_cluster_name}/"
    + "a" * 32
)


class DrainingAws(FakeAws):
    def __init__(self, desired="RUNNING"):
        super().__init__()
        self.desired = desired
        self.stable = False
        self.polls = 0
        self.task = {
            "taskArn": TASK,
            "clusterArn": CLUSTER,
            "group": f"service:{TARGET.web_service_name}",
            "taskDefinitionArn": TARGET.task_definition_arn_prefix(TARGET.web_task_family) + "22",
            "lastStatus": "STOPPING",
        }

    def __call__(self, *arguments):
        action = arguments[1]
        if action == "list-tasks" and "--service-name" in arguments:
            self.calls.append(arguments)
            service = arguments[arguments.index("--service-name") + 1]
            desired = arguments[arguments.index("--desired-status") + 1]
            if service == TARGET.web_service_name and desired == self.desired:
                return {"taskArns": [TASK]}
            return {"taskArns": []}
        if action == "describe-tasks" and TASK in arguments:
            self.calls.append(arguments)
            if self.stable:
                self.polls += 1
                if self.polls >= 2:
                    self.task["lastStatus"] = "STOPPED"
            return {"tasks": [deepcopy(self.task)], "failures": []}
        if action == "wait":
            self.stable = True
        if action == "list-tasks" and self.task["lastStatus"] != "STOPPED":
            self.calls.append(arguments)
            return {"taskArns": [TASK]}
        return super().__call__(*arguments)


@pytest.mark.parametrize("desired", ["RUNNING", "STOPPED"])
def test_drain_waits_for_actual_stop_after_service_stability(monkeypatch, desired):
    fake = DrainingAws(desired)
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    monkeypatch.setattr(dev_reset_tasks.time, "sleep", lambda _: None)
    dev_reset_ecs.drain()
    assert fake.polls >= 2
    assert fake.task["lastStatus"] == "STOPPED"
    first_update = next(i for i, call in enumerate(fake.calls) if call[1] == "update-service")
    assert any(call[1] == "describe-tasks" for call in fake.calls[:first_update])
    assert fake.calls[-1][1] == "list-tasks"
    assert all(call[1] != "stop-task" for call in fake.calls)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("taskArn", "arn:foreign"),
        ("clusterArn", "arn:foreign"),
        ("group", "service:relay-dev-worker"),
        (
            "taskDefinitionArn",
            TARGET.task_definition_arn_prefix(TARGET.migration_task_family) + "1",
        ),
        ("lastStatus", None),
    ],
)
def test_capture_refuses_malformed_service_task_before_mutation(monkeypatch, field, value):
    fake = DrainingAws()
    fake.task[field] = value
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="drain task"):
        dev_reset_ecs.drain()
    assert all(call[1] != "update-service" for call in fake.calls)


@pytest.mark.parametrize("failure", ["missing", "failure", "identity", "read"])
def test_wait_refuses_incomplete_or_changed_observations(monkeypatch, failure):
    fake = DrainingAws()

    def broken_read(*arguments):
        response = fake(*arguments)
        if arguments[1] != "describe-tasks" or not fake.stable:
            return response
        if failure == "read":
            raise dev_reset_ecs.ResetRefused("AWS target check or service operation failed")
        if failure == "missing":
            return {"tasks": [], "failures": []}
        if failure == "failure":
            return {"tasks": [], "failures": [{"reason": "MISSING"}]}
        response["tasks"][0]["group"] = "service:relay-dev-worker"
        return response

    monkeypatch.setattr(dev_reset_ecs, "_aws", broken_read)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="incomplete|identity|AWS target"):
        dev_reset_ecs.drain()
    assert fake.polls == 1
    assert all(call[1] not in {"run-task", "stop-task"} for call in fake.calls)


def test_wait_times_out_without_force_stopping_or_sql(monkeypatch, capsys):
    fake = DrainingAws()
    clock = iter([0, 301])
    monkeypatch.setattr(dev_reset_tasks.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    monkeypatch.setattr(dev_reset_ecs.sys, "argv", ["dev_reset_ecs", "drain"])
    assert dev_reset_ecs.main() == 1
    assert "captured task still active" in capsys.readouterr().err
    assert fake.polls == 1
    assert all(call[1] not in {"run-task", "stop-task"} for call in fake.calls)


@pytest.mark.parametrize(
    "family", ["website-dev-migration", "website-dev-web", "website-dev-unknown"]
)
def test_final_scanner_still_refuses_new_or_competing_writer(monkeypatch, family):
    fake = DrainingAws()
    fake.writer = True

    def competing_writer(*arguments):
        response = fake(*arguments)
        if arguments[1] == "describe-tasks" and "arn:writer" in arguments:
            response["tasks"][0]["taskDefinitionArn"] = (
                f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:task-definition/{family}:1"
            )
        return response

    monkeypatch.setattr(dev_reset_ecs, "_aws", competing_writer)
    monkeypatch.setattr(dev_reset_tasks.time, "sleep", lambda _: None)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="writer task remains active"):
        dev_reset_ecs.drain()
    assert fake.task["lastStatus"] == "STOPPED"
    assert fake.polls == 2
    assert all(call[1] not in {"run-task", "stop-task"} for call in fake.calls)


@pytest.mark.parametrize("arns", [["arn:foreign"], [TASK, TASK], None])
def test_capture_rejects_bad_listing_without_describing_unknown_tasks(monkeypatch, arns):
    fake = DrainingAws()

    def invalid_listing(*arguments):
        if arguments[1] == "list-tasks" and "--service-name" in arguments:
            return {"taskArns": arns}
        return fake(*arguments)

    monkeypatch.setattr(dev_reset_ecs, "_aws", invalid_listing)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="drain task"):
        dev_reset_ecs.drain()
    assert all(call[1] not in {"update-service", "describe-tasks"} for call in fake.calls)


def test_aws_error_output_is_not_exposed(monkeypatch, capsys):
    def failed_command(*arguments, **kwargs):
        return dev_reset_ecs.subprocess.CompletedProcess(arguments, 1, b"", b"secret-token")

    monkeypatch.setattr(dev_reset_ecs.subprocess, "run", failed_command)
    monkeypatch.setattr(dev_reset_ecs.sys, "argv", ["dev_reset_ecs", "drain"])
    assert dev_reset_ecs.main() == 1
    error = capsys.readouterr().err
    assert "AWS target check or service operation failed" in error
    assert "secret-token" not in error
