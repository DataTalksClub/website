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

WARMUP_TEMPLATES = (
    "core/home.html",
    "core/member_home.html",
    "core/tour.html",
    "accounts/account_settings.html",
    "account/welcome.html",
    "courses/course_list.html",
    "public/podcast_hub.html",
    "public/podcast_detail.html",
    "public/collection_hub.html",
    "public/article_detail.html",
    "public/events.html",
    "public/event_detail.html",
    "public/book_detail.html",
    "public/person_detail.html",
    "public/wiki_hub.html",
    "public/wiki_detail.html",
)


def post_worker_init(worker) -> None:  # noqa: ARG001
    """Warm process-local caches and templates before serving traffic.

    Every fresh or recycled worker otherwise compiles the large design-system
    templates and populates ``content.catalogue``, ``content.event_content``,
    ``content.wiki_reader``, ``content.docs_reader``, and timezone options on
    its first live request.  Any failure here is ignored so worker boot never
    fails closed on an empty or migrating database.
    """

    try:
        from django.template.loader import get_template
        from django.urls import get_resolver

        from accounts.services.timezones import build_timezone_options
        from content import (
            catalogue,
            docs_reader,
            event_content,
            podcast_content,
            tour_content,
            wiki_content,
        )
        from core.request_cache import bind_request_memo, reset_request_memo

        get_resolver()._populate()
        build_timezone_options()
        for template_name in WARMUP_TEMPLATES:
            get_template(template_name)

        token = bind_request_memo()
        try:
            catalogue.collection_counts()
            catalogue.wiki_graph()
            podcasts = podcast_content.ordered_podcasts()
            if podcasts:
                wiki_content.episode_graph(podcasts[0])
            event_content.event_groups()
            docs_reader.docs_page(docs_reader.DOCS_ROOT_PATH)
            tour_content.tour_page()
        finally:
            reset_request_memo(token)
    except Exception:
        return
