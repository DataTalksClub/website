# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.10.11 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime
COPY --from=uv /uv /uvx /bin/
RUN groupadd --system --gid 10001 dtc \
    && useradd --system --uid 10001 --gid dtc --home-dir /app --shell /usr/sbin/nologin dtc
WORKDIR /app
COPY --from=builder --chown=dtc:dtc /app/.venv /app/.venv
COPY --chown=dtc:dtc . .
ENV PATH="/app/.venv/bin:$PATH" \
    UV_CACHE_DIR=/app/.cache/uv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=website.settings.production
RUN mkdir -p /app/.cache/uv \
    && DJANGO_SETTINGS_MODULE=website.settings.test uv run --no-sync python manage.py collectstatic --noinput \
    && chown -R dtc:dtc /app/.cache
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["web"]
