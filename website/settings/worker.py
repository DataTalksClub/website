"""Production domain-job runner; selected only by the Coolify worker service."""

from .production import *  # noqa: F403

# jobs_run_due calls the existing fenced runner in this dedicated process.
# Never use this settings module for Gunicorn: sync runs handlers inline.
COMMUNITY_BASE = {**COMMUNITY_BASE, "JOBS_BACKEND": "sync"}  # noqa: F405
