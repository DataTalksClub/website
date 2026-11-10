# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.10.11@sha256:3472e43b4e738cf911c99d41bb34331280efad54c73b1def654a6227bb59b2b4 AS uv

FROM python:3.13@sha256:ecbe213fb818d557fd19db4078244339e2e2e52874f9c81052d3094ea114d797 AS builder
COPY --from=uv /uv /uvx /bin/
# The builder uses the full python image because the pinned community-base
# dependency is a git source (D0.1a) and the locked sync needs git, which the
# slim image does not carry. The runtime stage never syncs (--no-sync) and
# keeps the prebuilt venv on the slim base.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
# Keep the runtime minimal: no OS package manager, no in-image HTTP client.
# Platform HTTP healthchecks probe GET /health/ready from outside the
# container; the contract test enforces the package-manager-free runtime.
RUN groupadd --system --gid 10001 dtc \
    && useradd --system --uid 10001 --gid dtc --home-dir /app --shell /usr/sbin/nologin dtc
WORKDIR /app
COPY --from=builder --chown=dtc:dtc /app/.venv /app/.venv
COPY --chown=dtc:dtc . .
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=website.settings.production \
    WEB_CONCURRENCY=2 \
    WEB_THREADS=4
RUN python -m compileall -q -j 0 /app -x '/app/\.venv' \
    && DTC_ENVIRONMENT=local DJANGO_SETTINGS_MODULE=website.settings.collectstatic python manage.py collectstatic --noinput
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["web"]
