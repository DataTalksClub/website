"""Print only bounded ECS metadata for a failed one-off migration task.

Container reasons and stopped reasons are arbitrary strings and may contain
runtime data, so they are deliberately never copied into deployment logs.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from deploy.deployment_targets import SELECTED_TARGET

PHASES = {
    21: "schema_migration",
    22: "relay_schedule_sync",
    23: "mail_template_import",
}
STOP_CODES = frozenset(
    {
        "EssentialContainerExited",
        "TaskFailedToStart",
        "UserInitiated",
        "ServiceSchedulerInitiated",
        "SpotInterruption",
        "TerminationNotice",
    }
)
CONTAINER_REASON_PREFIXES = (
    "ResourceInitializationError",
    "CannotPullContainerError",
    "CannotStartContainerError",
    "OutOfMemoryError",
)


def summarize(task_result: dict[str, Any], expected_task_arn: str) -> dict[str, Any]:
    """Extract diagnostics without exposing arbitrary ECS reason text."""

    tasks = task_result.get("tasks", [])
    task = tasks[0] if isinstance(tasks, list) and len(tasks) == 1 else {}
    if not isinstance(task, dict) or task.get("taskArn") != expected_task_arn:
        return {"phase": "unknown", "ecs_stop_code": "unavailable"}

    containers = task.get("containers", [])
    migration = (
        next(
            (
                item
                for item in containers
                if isinstance(item, dict) and item.get("name") == "migration"
            ),
            {},
        )
        if isinstance(containers, list)
        else {}
    )
    exit_code = migration.get("exitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or not 0 <= exit_code <= 255:
        exit_code = None
    stop_code = task.get("stopCode")
    reason = migration.get("reason")
    reason_category = next(
        (
            prefix
            for prefix in CONTAINER_REASON_PREFIXES
            if isinstance(reason, str) and reason.startswith(prefix)
        ),
        "other" if reason else "none",
    )
    summary: dict[str, Any] = {
        "phase": PHASES.get(exit_code, "unknown") if exit_code is not None else "unknown",
        "exit_code": exit_code,
        "ecs_stop_code": stop_code if stop_code in STOP_CODES else "other",
        "container_reason_category": reason_category,
    }

    task_prefix = (
        f"arn:aws:ecs:{SELECTED_TARGET.aws_region}:{SELECTED_TARGET.aws_account_id}:"
        f"task/{SELECTED_TARGET.ecs_cluster_name}/"
    )
    task_id = expected_task_arn.removeprefix(task_prefix)
    if expected_task_arn.startswith(task_prefix) and re.fullmatch(r"[0-9a-f]{32}", task_id):
        summary["cloudwatch_log_group"] = f"/ecs/{SELECTED_TARGET.resource_namespace}/migration"
        summary["cloudwatch_log_stream"] = f"migration/migration/{task_id}"
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_result", type=Path)
    parser.add_argument("expected_task_arn")
    args = parser.parse_args()
    task_result = json.loads(args.task_result.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {"migration_task_diagnostic": summarize(task_result, args.expected_task_arn)},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
