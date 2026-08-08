#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    exec uv run --no-sync gunicorn website.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --access-logfile - --error-logfile -
    ;;
  worker)
    exec uv run --no-sync python manage.py run_job_worker
    ;;
  *)
    echo "Usage: entrypoint.sh [web|worker]" >&2
    exit 64
    ;;
esac
