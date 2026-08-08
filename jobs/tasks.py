from __future__ import annotations

import os
import uuid

from jobs.dispatch import relay_due_jobs
from jobs.execution import execute_job, sweep_expired_jobs
from jobs.heartbeat import prune_stale_heartbeats


def execute_durable_job(job_id: str) -> str:
    parsed_job_id = uuid.UUID(job_id)
    worker_id = f"q2-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return execute_job(parsed_job_id, worker_id=worker_id)


def sweep_and_relay() -> dict[str, int]:
    recovered, exhausted = sweep_expired_jobs()
    relayed = relay_due_jobs()
    stale_heartbeats = prune_stale_heartbeats()
    return {
        "recovered": recovered,
        "exhausted": exhausted,
        "relayed": relayed,
        "stale_heartbeats": stale_heartbeats,
    }
