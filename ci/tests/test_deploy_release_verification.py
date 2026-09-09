"""A green deploy proves a coherent running release (audit REL-06).

Services-stable alone accepts an old worker, a mixed web rollout, and a
database-unready site, and the old curl loop had no deadlines.  The deploy
orchestrator now verifies both services sit on the promoted definitions with
matching counts, every running task carries the promoted revision, a one-off
task on the promoted worker definition executes a synthetic durable job
(system.noop through the signed ingress -- no live email, no real provider)
inside the fixed tasks-stopped budget, and the HTTP checks carry connect and
overall deadlines plus the database-backed readiness endpoint.  Each fault is
driven end to end through a stateful fake ``aws``/``curl`` on ``PATH``.

REL-08 additions: the orchestrator promotes the task definition each service
is running (its ``:revision`` ARN) instead of the family's latest registered
revision -- the fake keeps a poisoned *latest* family document that would be
refused, so a regression to family-latest selection fails the deployment --
and it validates every source document against the reviewed deployment target
before the first registration, so a sidecar, a foreign role or another
architecture fails the deployment before any mutation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from deploy.deployment_targets import registered_target
from deploy.task_definitions import COMMANDS, config_for_target

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "387546586013.dkr.ecr.eu-west-1.amazonaws.com/website-production@sha256:" + "c" * 64
SOURCE_SHA = "abc1234" + "0" * 33
VERSION = "20260909-120000-abc1234"
DIGEST = "sha256:" + "c" * 64
REGION = "eu-west-1"
CLUSTER = "website-production"
WEB = "website-dev-web"
WORKER = "website-dev-worker"
WEB_OLD = f"arn:aws:ecs:{REGION}:387546586013:task-definition/{WEB}:41"
WORKER_OLD = f"arn:aws:ecs:{REGION}:387546586013:task-definition/{WORKER}:17"
WEB_ARN_NEW = f"arn:aws:ecs:{REGION}:387546586013:task-definition/{WEB}:42"
WORKER_ARN_NEW = f"arn:aws:ecs:{REGION}:387546586013:task-definition/{WORKER}:18"
OLD_COUNTS = {WEB: {"desired": 1, "running": 1}, WORKER: {"desired": 0, "running": 0}}

DEV_TARGET = registered_target("website-development")
DEV_CONFIG = config_for_target(DEV_TARGET)


def task_document(family: str, revision: int, **overrides: Any) -> str:
    """A task definition satisfying the reviewed website-development contract.

    ``overrides`` replaces top-level task fields, so a test can poison exactly
    one expectation.
    """

    workload = family.rsplit("-", 1)[-1]
    container: dict[str, Any] = {
        "name": workload,
        "image": "old-image:tag",
        "command": list(COMMANDS[workload]["command"]) if workload != "migration" else ["legacy"],
        "environment": [
            {"name": name, "value": value}
            for name, value in sorted(DEV_TARGET.fixed_nonsecret_environment.items())
        ],
        "secrets": [
            {
                "name": "DATABASE_URL",
                "valueFrom": (
                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                    ":secret:website-dev/database-url-abc123"
                ),
            },
            {
                "name": "DJANGO_SECRET_KEY",
                "valueFrom": (
                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                    ":secret:website-dev/django-secret-key-abc123"
                ),
            },
        ],
        "portMappings": [{"containerPort": 8000}],
    }
    task: dict[str, Any] = {
        "family": family,
        "revision": revision,
        "status": "ACTIVE",
        "taskDefinitionArn": (
            f"arn:aws:ecs:{REGION}:387546586013:task-definition/{family}:{revision}"
        ),
        "requiresAttributes": [],
        "compatibilities": ["FARGATE"],
        "registeredAt": 0,
        "registeredBy": "fixture",
        "cpu": "512",
        "memory": "1024",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        "executionRoleArn": DEV_TARGET.execution_role_arn,
        "taskRoleArn": DEV_TARGET.task_role_arn,
        "containerDefinitions": [container],
    }
    if workload == "migration":
        container["entryPoint"] = ["/bin/sh", "-lc"]
    task.update(overrides)
    return json.dumps({"taskDefinition": task})


FAKE_AWS = r"""#!/usr/bin/env python3
import json, os, sys, time, uuid
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
command = "wait " + args[2] if args[1] == "wait" else args[1]

def flag_value(flag):
    return args[args.index(flag) + 1]

with open(state / "calls.log", "a") as log:
    log.write(json.dumps(args) + "\n")

fault = os.environ.get("FAKE_FAIL_AT", "")

def read_updates():
    path = state / "updates.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}

def read_tasks():
    path = state / "tasks.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}

def service_view(name, updates):
    if name in updates:
        promoted = updates[name]
        deployments = [{"status": "PRIMARY", "taskDefinition": promoted["arn"]}]
        if os.environ.get("FAKE_MIXED_WEB") == "1" and name.endswith("-web"):
            deployments.append({"status": "ACTIVE", "taskDefinition": promoted["old_arn"]})
        return {
            "serviceName": name,
            "taskDefinition": promoted["arn"],
            "desiredCount": promoted["desired"],
            "runningCount": promoted["desired"],
            "deployments": deployments,
            "networkConfiguration": NETWORK_CONFIGURATION,
        }
    counts = {"website-dev-web": [1, 1], "website-production-web": [2, 2]}
    desired, running = counts.get(name, [0, 0])
    return {
        "serviceName": name,
        "taskDefinition": _old_arn(name),
        "desiredCount": desired,
        "runningCount": running,
        "deployments": [{"status": "PRIMARY"}],
        "networkConfiguration": NETWORK_CONFIGURATION,
    }

def _old_arn(name):
    return OLD[name]

NETWORK_CONFIGURATION = {
    "awsvpcConfiguration": {"subnets": ["subnet-1"], "securityGroups": ["sg-1"]}
}

def _arn(family, revision):
    return (
        f"arn:aws:ecs:eu-west-1:387546586013:task-definition/"
        f"{family}:{revision}"
    )


OLD = {
    "website-dev-web": _arn("website-dev-web", 41),
    "website-dev-worker": _arn("website-dev-worker", 17),
    "website-production-web": _arn("website-production-web", 80),
    "website-production-worker": _arn("website-production-worker", 60),
}

if command == "describe-services":
    updates = read_updates()
    # The orchestrator names both services as separate argv entries after
    # --services (the last flag on that call).
    names = args[args.index("--services") + 1:]
    print(json.dumps({"failures": [], "services": [service_view(name, updates) for name in names]}))
elif command == "describe-task-definition":
    reference = flag_value("--task-definition").split("/")[-1]
    family = reference.split(":")[0]
    # A revision-pinned reference (the orchestrator promotes the service's
    # active ARN) prefers that revision's document; a bare family name (the
    # migration source) reads the family's latest registered revision.
    if ":" in reference:
        pinned = state / f"taskdef-{family}-{reference.split(':')[1]}.json"
        path = pinned if pinned.exists() else state / f"taskdef-{family}.json"
    else:
        latest = state / f"taskdef-{family}-latest.json"
        path = latest if latest.exists() else state / f"taskdef-{family}.json"
    print(path.read_text())
elif command == "register-task-definition":
    document = next(
        json.loads(Path(arg[7:]).read_text())
        for arg in args
        if arg.startswith("file://")
    )
    family = document["family"]
    counters = json.loads((state / "revisions.json").read_text())
    counters[family] = counters.get(family, 40) + 1
    (state / "revisions.json").write_text(json.dumps(counters))
    arn = f"arn:aws:ecs:eu-west-1:387546586013:task-definition/{family}:{counters[family]}"
    print(json.dumps({"taskDefinition": {"taskDefinitionArn": arn}}))
elif command == "run-task":
    overrides = json.loads(flag_value("--overrides"))
    entry = overrides["containerOverrides"][0]
    container = entry["name"]
    command_line = " ".join(entry.get("command", []))
    if "jobs_ingress_selftest" in command_line:
        exit_code = 1 if fault == "selfcheck" else 0
    else:
        exit_code = int(os.environ.get("FAKE_MIGRATION_EXIT", "0"))
    task_arn = f"arn:aws:ecs:eu-west-1:387546586013:task/{uuid.uuid4()}"
    tasks = read_tasks()
    tasks[task_arn] = {"lastStatus": "STOPPED", "containers": {container: exit_code}}
    (state / "tasks.json").write_text(json.dumps(tasks))
    print(json.dumps({"failures": [], "tasks": [{"taskArn": task_arn}]}))
elif command == "list-tasks":
    service = flag_value("--service")
    updates = read_updates()
    arns = []
    if service in updates and updates[service]["desired"] > 0:
        arns = [f"arn:aws:ecs:eu-west-1:387546586013:task/{service}-running-1"]
    print(json.dumps({"taskArns": arns}))
elif command == "describe-tasks":
    tasks = read_tasks()
    requested = args[args.index("--tasks") + 1:]
    stale = os.environ.get("FAKE_STALE_TASKS") == "1"
    updates = read_updates()
    out = []
    for arn in requested:
        if arn in tasks:
            stopped = tasks[arn]
            containers = [
                {"name": name, "exitCode": code}
                for name, code in stopped["containers"].items()
            ]
            out.append(
                {
                    "taskArn": arn,
                    "lastStatus": stopped["lastStatus"],
                    "containers": containers,
                }
            )
        else:
            service = arn.split(":task/")[1].rsplit("-running-", 1)[0]
            promoted = updates.get(service, {}).get("arn")
            td_arn = OLD[service] if stale else (promoted or OLD[service])
            out.append(
                {
                    "taskArn": arn,
                    "lastStatus": "RUNNING",
                    "taskDefinitionArn": td_arn,
                    "containers": [{"name": "main", "exitCode": 0}],
                }
            )
    print(json.dumps({"tasks": out}))
elif command == "update-service":
    service = flag_value("--service")
    sleep_at = os.environ.get("FAKE_SLEEP_AT", "")
    if sleep_at == f"update-{service}":
        time.sleep(30)
    if fault == f"update-{service}":
        marker = state / f"fault-seen-{service}"
        # Fail once by default: the promote fails, the later recovery
        # restore of the same service succeeds.  FAKE_FAIL_EVERY_TIME=1
        # keeps failing, which is the "recovery itself fails" case.
        if marker.exists() and os.environ.get("FAKE_FAIL_EVERY_TIME") != "1":
            pass
        else:
            marker.write_text("1")
            sys.exit(5)
    updates = read_updates()
    stale_worker = os.environ.get("FAKE_STALE_WORKER") == "1" and service.endswith("-worker")
    updates[service] = {
        "arn": OLD[service] if stale_worker else flag_value("--task-definition"),
        "old_arn": OLD[service],
        "desired": int(flag_value("--desired-count")),
    }
    (state / "updates.json").write_text(json.dumps(updates))
    print(json.dumps({"service": {"serviceName": service}}))
elif command == "wait tasks-stopped":
    sys.exit(0)
elif command == "wait services-stable":
    if fault == "wait-stable":
        waits = state / "stable-waits.txt"
        count = int(waits.read_text()) if waits.exists() else 0
        waits.write_text(str(count + 1))
        if count == 0:
            time.sleep(0.2)
            sys.exit(3)
    sys.exit(0)
else:
    print(f"fake aws: unexpected command {command}", file=sys.stderr)
    sys.exit(64)
"""

FAKE_CURL = r"""#!/usr/bin/env python3
import json, os, sys, time

args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
out = args[args.index("--output") + 1]
max_time = int(args[args.index("--max-time") + 1])

if os.environ.get("FAKE_HANG") == "1":
    # Emulate real curl's --max-time: the connection never answers, and the
    # deadline aborts the transfer.
    time.sleep(max_time)
    sys.exit(28)
if os.environ.get("FAKE_READY_FAIL") == "1" and url.endswith("/health/ready"):
    sys.exit(22)
if os.environ.get("FAKE_FAIL_AT") == "health" and url.endswith("/api/health/"):
    # A served-but-wrong identity: the endpoint answers, the release is not
    # the promoted one, and the attempt must never count as success.
    payload = {
        "status": "ok",
        "version": "wrong",
        "source_sha": "wrong",
        "image_digest": "wrong",
    }
    with open(out, "w") as handle:
        json.dump(payload, handle)
    sys.exit(0)

if url.endswith("/api/health/"):
    payload = {
        "status": "ok",
        "version": os.environ["FAKE_HEALTH_VERSION"],
        "source_sha": os.environ["FAKE_HEALTH_SOURCE_SHA"],
        "image_digest": os.environ["FAKE_HEALTH_IMAGE_DIGEST"],
    }
else:
    payload = {"status": "ready"}
with open(out, "w") as handle:
    json.dump(payload, handle)
"""


class VerificationHarness:
    """A scratch checkout root with a stateful fake aws/curl early on PATH."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "run"
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        self.state.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self.receipts = self.root / ".tmp" / "deploy-receipts"

        (self.bin / "aws").write_text(FAKE_AWS)
        (self.bin / "curl").write_text(FAKE_CURL)
        (self.bin / "aws").chmod(0o755)
        (self.bin / "curl").chmod(0o755)
        # The same fake under a second name: recovery_receipt.py runs as a
        # guarded Python under the test runtime, and the guard denies any
        # executable literally named "aws".  RECOVERY_AWS_CLI points it here.
        (self.bin / "awscli-fake").write_text(FAKE_AWS)
        (self.bin / "awscli-fake").chmod(0o755)
        python3 = self.bin / "python3"
        python3.symlink_to(sys.executable)

        (self.state / "describe-services.json").write_text("{}")
        # The service-active revisions are the promotion sources (REL-08); the
        # family-latest documents for the long-running services are POISONED
        # with a foreign task role, so any regression to family-latest
        # selection is refused and fails the deployment.
        foreign_role = "arn:aws:iam::387546586013:role/website-production-task-application"
        for family, revision in (
            (WEB, 41),
            (WORKER, 17),
        ):
            (self.state / f"taskdef-{family}-{revision}.json").write_text(
                task_document(family, revision)
            )
            latest = json.loads(task_document(family, 999))
            latest["taskDefinition"]["taskRoleArn"] = foreign_role
            (self.state / f"taskdef-{family}-latest.json").write_text(json.dumps(latest))
        (self.state / "taskdef-website-dev-migration-latest.json").write_text(
            task_document("website-dev-migration", 7)
        )
        (self.state / "revisions.json").write_text(
            json.dumps({WEB: 41, WORKER: 17, "website-dev-migration": 7})
        )

    def write_task_document(self, family: str, revision: int, **overrides: Any) -> None:
        """Replace the service-active document for one family (fault setup)."""

        (self.state / f"taskdef-{family}-{revision}.json").write_text(
            task_document(family, revision, **overrides)
        )

    def env(self, **overrides: str) -> dict[str, str]:
        environment = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", str(self.root)),
            "LANG": "C.UTF-8",
            "ECS_CLUSTER_NAME": CLUSTER,
            "FAKE_STATE": str(self.state),
            "FAKE_HEALTH_VERSION": VERSION,
            "FAKE_HEALTH_SOURCE_SHA": SOURCE_SHA,
            "FAKE_HEALTH_IMAGE_DIGEST": DIGEST,
            "HEALTH_POLL_SECONDS": "0",
            "HEALTH_CONNECT_TIMEOUT": "1",
            "HEALTH_MAX_TIME": "2",
            "RECOVERY_AWS_CLI": "awscli-fake",
        }
        environment.update(overrides)
        return environment

    def deploy(self, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            [
                "bash",
                str(ROOT / "deploy" / "deploy_website.sh"),
                "dev",
                IMAGE,
                VERSION,
                SOURCE_SHA,
            ],
            cwd=self.root,
            env=self.env(**overrides),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    def update_service_calls(self) -> list[tuple[str, str, str]]:
        parsed = []
        for call in self.calls():
            if "update-service" in call:
                parsed.append(
                    (
                        call[call.index("--service") + 1],
                        call[call.index("--task-definition") + 1],
                        call[call.index("--desired-count") + 1],
                    )
                )
        return parsed

    def receipt(self) -> dict[str, Any]:
        return json.loads((self.receipts / f"dev-{VERSION}.json").read_text())


def test_a_clean_rollout_verifies_tasks_selfcheck_and_readiness(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy()

    assert completed.returncode == 0, completed.stderr
    # The coherence, per-task and readiness checks all ran.
    assert any(args[:2] == ["ecs", "list-tasks"] for args in harness.calls())
    assert any(args[:2] == ["ecs", "describe-tasks"] for args in harness.calls())
    selfcheck = next(
        args
        for args in harness.calls()
        if args[:2] == ["ecs", "run-task"]
        and any("jobs_ingress_selftest" in str(part) for part in args)
    )
    assert "--task-definition" in selfcheck
    overrides = json.loads(selfcheck[selfcheck.index("--overrides") + 1])
    assert overrides["containerOverrides"][0]["name"] == "worker"
    receipt = harness.receipt()
    assert receipt["outcome"] == "promoted"


def test_the_promotion_sources_are_the_service_active_revisions(tmp_path: Path) -> None:
    """REL-08: services are promoted from their own ARN, migration from family."""

    harness = VerificationHarness(tmp_path)

    completed = harness.deploy()

    assert completed.returncode == 0, completed.stderr
    described = {
        args[args.index("--task-definition") + 1]
        for args in harness.calls()
        if args[:2] == ["ecs", "describe-task-definition"]
    }
    assert WEB_OLD in described
    assert WORKER_OLD in described
    assert "website-dev-migration" in described
    # The poisoned family-latest documents were never read.
    registered = [
        args[args.index("--task-definition") + 1]
        for args in harness.calls()
        if args[:2] == ["ecs", "update-service"]
    ]
    assert WEB_ARN_NEW in registered
    assert WORKER_ARN_NEW in registered


def test_a_sidecar_in_the_active_definition_fails_before_any_mutation(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)
    harness.write_task_document(
        WEB,
        41,
        containerDefinitions=[
            {
                "name": "web",
                "image": "old-image:tag",
                "command": ["web"],
                "environment": [],
                "secrets": [],
            },
            {"name": "log-router", "image": "log-agent:1"},
        ],
    )

    completed = harness.deploy()

    assert completed.returncode != 0
    assert "exactly one container" in completed.stderr
    commands = [args[1] for args in harness.calls() if len(args) > 1]
    assert "register-task-definition" not in commands
    assert "update-service" not in commands


def test_a_foreign_role_in_the_active_worker_definition_fails_before_registration(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)
    harness.write_task_document(
        WORKER,
        17,
        taskRoleArn="arn:aws:iam::387546586013:role/website-production-task-application",
    )

    completed = harness.deploy()

    assert completed.returncode != 0
    assert "task role" in completed.stderr
    commands = [args[1] for args in harness.calls() if len(args) > 1]
    # The refusal stops the flow between the worker's describe and its
    # registration: web (validated first) registered, the worker did not, and
    # no service was mutated.
    assert commands.count("register-task-definition") == 1
    assert "update-service" not in commands
    assert "run-task" not in commands


def test_an_old_stable_worker_prevents_the_success_record(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_STALE_WORKER="1")

    assert completed.returncode != 0
    receipt = harness.receipt()
    assert receipt["outcome"] == "in_progress"
    assert "recovery" in receipt


def test_a_mixed_web_rollout_prevents_the_success_record(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_MIXED_WEB="1")

    assert completed.returncode != 0
    assert harness.receipt()["outcome"] == "in_progress"


def test_a_database_unready_readiness_response_prevents_success(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_READY_FAIL="1")

    assert completed.returncode != 0
    assert harness.receipt()["outcome"] == "in_progress"


def test_a_hanging_connection_fails_within_the_http_deadline(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)
    started = time.monotonic()

    completed = harness.deploy(FAKE_HANG="1", HEALTH_MAX_ATTEMPTS="2", HEALTH_MAX_TIME="1")

    elapsed = time.monotonic() - started
    assert completed.returncode != 0
    # Two attempts, each bounded by the 1s max-time deadline: a hanging
    # connection cannot exceed the apparent retry budget.
    assert elapsed < 30
    assert harness.receipt()["outcome"] == "in_progress"


def test_a_failing_worker_selfcheck_prevents_the_success_record(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_FAIL_AT="selfcheck")

    assert completed.returncode != 0
    assert "cannot execute durable jobs" in completed.stderr
    assert harness.receipt()["outcome"] == "in_progress"


def test_the_promotion_flow_wires_the_new_checks_into_both_workflows() -> None:
    # The checks live in the shared orchestrator, so both workflows inherit
    # them; this pins that neither workflow bypasses deploy_website.sh.
    for name in ("deploy-dev.yml", "deploy-prod.yml"):
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / name).read_text(),
            Loader=yaml.BaseLoader,
        )
        deploy_steps = json.dumps(workflow["jobs"]["deploy"]["steps"])
        assert "deploy_dev.sh" in deploy_steps or "deploy_prod.sh" in deploy_steps
