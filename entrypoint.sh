#!/bin/sh
set -eu

GUNICORN_ACCESS_LOG_FORMAT='%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s request_id="%({x-request-id}o)s" correlation_id="%({x-correlation-id}o)s"'
# The format above already excludes the query string, the client address and every
# header. Relay's recipient links put an opaque per-recipient token in the path
# instead, so the logger class redacts that one segment before it is written.
GUNICORN_LOGGER_CLASS='core.gunicorn_logging.RecipientTokenSafeLogger'

case "${1:-web}" in
  web)
    exec uv run --no-sync gunicorn website.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --timeout 90 --access-logfile - --access-logformat "$GUNICORN_ACCESS_LOG_FORMAT" --logger-class "$GUNICORN_LOGGER_CLASS" --error-logfile -
    ;;
  worker)
    # D1.1: Relay executes durable jobs through the signed ingress. The
    # worker container is the submit pump that pushes due intents to Relay
    # and recovers expired leases; the run-due cadence is also synced as a
    # Relay schedule, so this loop is redundancy, not the only driver.
    while true; do
      uv run --no-sync python manage.py jobs_run_due --limit 100 || true
      uv run --no-sync python manage.py jobs_sweep --limit 100 || true
      sleep 10
    done
    ;;
  *)
    echo "Usage: entrypoint.sh [web|worker]" >&2
    exit 64
    ;;
esac
