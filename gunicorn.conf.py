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
max_requests = 1000
max_requests_jitter = 100
