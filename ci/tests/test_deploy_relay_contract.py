"""The supported shell promotes only corrected development Relay sources."""

import json

import pytest

from ci.tests.test_deploy_release_verification import DEV_TARGET, VerificationHarness
from core.tests.deployment_fixtures import RELAY_URL, secret_references


def registered_container(harness, workload):
    path = harness.state / f"registered-website-dev-{workload}.json"
    return json.loads(path.read_text())["containerDefinitions"][0]


def test_shell_preserves_relay_configuration_for_all_three_sources(tmp_path):
    harness = VerificationHarness(tmp_path)
    result = harness.deploy()
    assert result.returncode == 0, result.stderr
    for workload in ("web", "worker", "migration"):
        container = registered_container(harness, workload)
        assert container["secrets"] == secret_references(DEV_TARGET)
        environment = {item["name"]: item["value"] for item in container["environment"]}
        assert environment["RELAY_BASE_URL"] == RELAY_URL
    commands = []
    for call in harness.calls():
        if call[:2] == ["ecs", "run-task"]:
            override = json.loads(call[call.index("--overrides") + 1])
            commands.extend(override["containerOverrides"][0]["command"])
    migration = commands[0]
    assert migration.index("migrate --noinput") < migration.index("sync_relay_schedules")
    assert migration.index("sync_relay_schedules") < migration.index("import_mail_templates")
    assert "dev_schema_reset" not in " ".join(commands)
    assert harness.receipt()["outcome"] == "promoted"


@pytest.mark.parametrize(
    "workload,revision", [("web", "41"), ("worker", "17"), ("migration", "latest")]
)
def test_shell_refuses_invalid_relay_source_before_registration_or_launch(
    tmp_path, workload, revision
):
    harness = VerificationHarness(tmp_path)
    source = harness.state / f"taskdef-website-dev-{workload}-{revision}.json"
    document = json.loads(source.read_text())
    document["taskDefinition"]["containerDefinitions"][0]["secrets"][2]["valueFrom"] += "version"
    source.write_text(json.dumps(document))
    result = harness.deploy()
    assert result.returncode != 0
    assert not (harness.state / f"registered-website-dev-{workload}.json").exists()
    assert harness.update_service_calls() == []
    for call in harness.calls():
        assert call[:2] != ["ecs", "run-task"]
