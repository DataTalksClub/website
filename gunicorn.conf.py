"""Gunicorn serving config: threaded workers so one slow request cannot stall health.

WEB_CONCURRENCY defaults to 2 workers; WEB_THREADS defaults to 4 threads per
worker with the gthread class. The entrypoint keeps the same explicit CLI flags
(timeout 90, access-log format, logger class) so the source-policy contract
stays byte-stable; this file is the in-repo canonical record the deploy files
mirror.
"""

import os


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


workers = _int_env("WEB_CONCURRENCY", 2, 2)
threads = _int_env("WEB_THREADS", 4, 2)
worker_class = "gthread"
timeout = 90
bind = "0.0.0.0:8000"
keepalive = 5
# Recycling a worker drops its compiled templates, catalogue stamps and
# database connection, so its next request re-renders from scratch (measured
# 0.9-2.9 s per page on prod).  /health/ready probes count towards
# max_requests, so 1000 recycled both workers roughly every half hour on
# health checks alone, and those cold renders dominated real-traffic p95.
# 20000 keeps the leak-hedge recycle at a few-times-a-day cadence instead.
max_requests = 20000
max_requests_jitter = 2000
