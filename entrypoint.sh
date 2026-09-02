#!/bin/sh
set -eu

GUNICORN_ACCESS_LOG_FORMAT='%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s request_id="%({x-request-id}o)s" correlation_id="%({x-correlation-id}o)s"'
# The format above already excludes the query string, the client address and every
# header. Relay's recipient links put an opaque per-recipient token in the path
# instead, so the logger class redacts that one segment before it is written.
GUNICORN_LOGGER_CLASS='core.gunicorn_logging.RecipientTokenSafeLogger'

case "${1:-web}" in
  web)
    exec uv run --no-sync gunicorn website.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --access-logfile - --access-logformat "$GUNICORN_ACCESS_LOG_FORMAT" --logger-class "$GUNICORN_LOGGER_CLASS" --error-logfile -
    ;;
  worker)
    exec uv run --no-sync python manage.py run_job_worker
    ;;
  *)
    echo "Usage: entrypoint.sh [web|worker]" >&2
    exit 64
    ;;
esac
