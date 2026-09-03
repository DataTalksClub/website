#!/bin/sh
set -eu

GUNICORN_ACCESS_LOG_FORMAT='%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s request_id="%({x-request-id}o)s" correlation_id="%({x-correlation-id}o)s"'

case "${1:-web}" in
  web)
    exec uv run --no-sync gunicorn website.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --timeout 90 --access-logfile - --access-logformat "$GUNICORN_ACCESS_LOG_FORMAT" --error-logfile -
    ;;
  worker)
    exec uv run --no-sync python manage.py run_job_worker
    ;;
  *)
    echo "Usage: entrypoint.sh [web|worker]" >&2
    exit 64
    ;;
esac
