"""Durable, redacted recovery receipts for the deploy orchestrator (REL-02).

``deploy/deploy_website.sh`` promotes the web and worker services in two
independent ``update-service`` mutations.  If anything fails after the first
one, the site can be left with one or both services on the new release and no
record of what ran before.  This module closes that in two halves:

``capture``
    Before the first service mutation, snapshot the exact active state of both
    services -- task-definition ARN, desired count, deployment ids -- into a
    receipt file under ``.tmp/deploy-receipts/``.  The receipt is built from
    explicitly allowlisted fields only, so no container environment, secret
    reference, or other raw API payload can reach it, and it lives outside the
    working directory the deploy script deletes on exit.  A runner that dies
    mid-deployment therefore still leaves a usable recovery record.

``recover``
    After a post-mutation failure, restore every captured service to its exact
    prior task-definition ARN and desired count, wait (bounded) for both to
    stabilize, and append the outcome to the receipt.  Image rollback is not
    database rollback: a migration that already committed is not undone, and
    the receipt names nothing that would suggest otherwise.

The decision logic is code-owned here and exercised by
``ci/tests/test_deploy_recovery_receipt.py``; the shell only wires arguments.
Stdlib-only, so the orchestrator can run it with the runner's ``python3``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

#: The completion states a receipt's ``outcome`` field can carry.  ``capture``
#: seeds ``in_progress``; the orchestrator flips it to ``promoted`` on success.
OUTCOME_IN_PROGRESS = "in_progress"
OUTCOME_PROMOTED = "promoted"


def _completed_process(args: list[str], timeout: float | None = None):
    return subprocess.run(args, check=False, capture_output=True, timeout=timeout)  # noqa: S603


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_receipt(
    *,
    target: str,
    cluster: str,
    region: str,
    version: str,
    source_sha: str,
    image: str,
    service_names: list[str],
    services_document: dict[str, Any],
    controller_sha: str = "",
    dev_run_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """The allowlisted pre-mutation state of the requested services.

    Everything here is an identifier or a count: ARNs, deployment ids, a
    digest-pinned image reference, and the run identity the orchestrator was
    already given on its command line.  No raw ``describe-services`` payload
    and no container definition ever reaches the receipt.

    ``controller_sha`` and ``dev_run_id`` are the promotion's two provenance
    bindings (REL-07): the checkout whose deployment code drove the mutation,
    and the dev workflow run whose verified release is being promoted.  They
    may be empty for local orchestrator runs; the production workflow always
    supplies both.
    """

    by_name: dict[str, dict[str, Any]] = {}
    for service in services_document.get("services", []):
        name = service.get("serviceName")
        if isinstance(name, str):
            by_name[name] = service

    services: dict[str, Any] = {}
    for name in service_names:
        service = by_name.get(name)
        if service is None:
            # A missing service was reported in ``failures``; there is nothing
            # to restore, and recovery must not invent a target for it.
            services[name] = {"exists": False}
            continue
        services[name] = {
            "exists": True,
            "task_definition_arn": service["taskDefinition"],
            "desired_count": service["desiredCount"],
            "deployment_ids": [
                deployment["id"]
                for deployment in service.get("deployments", [])
                if "id" in deployment
            ],
        }

    return {
        "schema": SCHEMA_VERSION,
        "captured_at": (now or datetime.now(UTC)).isoformat(),
        "outcome": OUTCOME_IN_PROGRESS,
        "target": target,
        "cluster": cluster,
        "region": region,
        "version": version,
        "source_sha": source_sha,
        "image": image,
        "controller_sha": controller_sha,
        "dev_run_id": dev_run_id,
        # The mutation boundary this receipt guards: recovery runs only after
        # the first update-service, never for a pre-mutation failure.
        "first_mutation": "update-service",
        "services": services,
    }


def capture_command(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recovery_receipt.py capture")
    parser.add_argument("--target", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--service", action="append", required=True)
    parser.add_argument("--services-json", required=True)
    parser.add_argument(
        "--controller-sha",
        default="",
        help="Checkout SHA whose deployment code drives this promotion (REL-07)",
    )
    parser.add_argument(
        "--dev-run-id",
        default="",
        help="Dev workflow run whose verified release is being promoted (REL-07)",
    )
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)

    with open(arguments.services_json, encoding="utf-8") as handle:
        services_document = json.load(handle)

    receipt = build_receipt(
        target=arguments.target,
        cluster=arguments.cluster,
        region=arguments.region,
        version=arguments.version,
        source_sha=arguments.source_sha,
        image=arguments.image,
        service_names=arguments.service,
        services_document=services_document,
        controller_sha=arguments.controller_sha,
        dev_run_id=arguments.dev_run_id,
    )
    output = Path(arguments.output)
    _write_atomic(output, receipt)
    print(output)
    return 0


PROMOTED_ARN_PREFIX = "arn:aws:ecs:"

#: The observed task-definition ARNs a successful promotion records, keyed by
#: workload.  Written only by ``mark-promoted``, after every terminal
#: verification has passed, so ``outcome == "promoted"`` always carries the
#: pair the services were actually left on -- not just the requested one.
ObservedPair = dict[str, str]


def mark_promoted(
    receipt_path: Path,
    observed: ObservedPair | None = None,
) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for workload, arn in (observed or {}).items():
        if not arn.startswith(PROMOTED_ARN_PREFIX):
            raise ValueError(f"promoted {workload} task definition is not an ECS ARN")
    receipt["outcome"] = OUTCOME_PROMOTED
    receipt["promoted_at"] = datetime.now(UTC).isoformat()
    if observed:
        receipt["promoted"] = dict(observed)
    _write_atomic(receipt_path, receipt)


def recover_command(
    argv: list[str] | None = None,
    *,
    runner=_completed_process,
) -> int:
    parser = argparse.ArgumentParser(prog="recovery_receipt.py recover")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Bounded wait for both restored services to stabilize",
    )
    parser.add_argument(
        "--cli",
        default="aws",
        help=(
            "Command name for the AWS CLI.  The default is the real one; "
            "tests name their fake differently so the test runtime's "
            "network guard (which denies an executable literally named "
            "'aws' inside guarded Python) keeps applying to everything "
            "else."
        ),
    )
    arguments = parser.parse_args(argv)
    cli = arguments.cli

    receipt_path = Path(arguments.receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    cluster = receipt["cluster"]
    region = arguments.region

    restored: list[str] = []
    failed: list[dict[str, Any]] = []
    service_names: list[str] = []
    for name, snapshot in receipt.get("services", {}).items():
        if not snapshot.get("exists"):
            continue
        service_names.append(name)
        completed = runner(
            [
                cli,
                "ecs",
                "update-service",
                "--region",
                region,
                "--cluster",
                cluster,
                "--service",
                name,
                "--task-definition",
                snapshot["task_definition_arn"],
                "--desired-count",
                str(snapshot["desired_count"]),
            ]
        )
        if completed.returncode == 0:
            restored.append(name)
        else:
            failed.append({"service": name, "stage": "update-service"})

    stabilized = False
    if not failed and service_names:
        try:
            completed = runner(
                [
                    cli,
                    "ecs",
                    "wait",
                    "services-stable",
                    "--region",
                    region,
                    "--cluster",
                    cluster,
                    "--services",
                    *service_names,
                ],
                timeout=float(arguments.timeout_seconds),
            )
            stabilized = completed.returncode == 0
        except subprocess.TimeoutExpired:
            stabilized = False
        if not stabilized:
            failed.append({"service": "all", "stage": "services-stable"})

    receipt["recovery"] = {
        "attempted_at": datetime.now(UTC).isoformat(),
        "restored": restored,
        "failed": failed,
        "stabilized": stabilized,
        "note": (
            "Task-definition rollback only; an already-committed migration is "
            "not a database rollback."
        ),
    }
    _write_atomic(receipt_path, receipt)

    print(
        json.dumps(
            {
                "restored": restored,
                "failed": failed,
                "stabilized": stabilized,
                "receipt": str(receipt_path),
            },
            sort_keys=True,
        )
    )
    return 1 if failed else 0


class TimeoutExpired(TimeoutError):
    """The bounded services-stable wait outlived its deadline."""


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] not in {"capture", "recover", "mark-promoted"}:
        print(
            "usage: recovery_receipt.py {capture|recover|mark-promoted} ...",
            file=sys.stderr,
        )
        return 2
    if argv[0] == "capture":
        return capture_command(argv[1:])
    if argv[0] == "mark-promoted":
        parser = argparse.ArgumentParser(prog="recovery_receipt.py mark-promoted")
        parser.add_argument("receipt")
        parser.add_argument(
            "--web-task-definition",
            default="",
            help="ARN the web service was actually left on after verification",
        )
        parser.add_argument(
            "--worker-task-definition",
            default="",
            help="ARN the worker service was actually left on after verification",
        )
        promoted = parser.parse_args(argv[1:])
        observed = {
            workload: arn
            for workload, arn in (
                ("web", promoted.web_task_definition),
                ("worker", promoted.worker_task_definition),
            )
            if arn
        }
        mark_promoted(Path(promoted.receipt), observed)
        return 0
    return recover_command(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
