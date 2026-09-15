"""Redirects for course routes retired by the issue #320 reversal.

The ``/courses/<family>/cohorts/<identifier>/...`` shape was canonical from
2026-09-07 until the owner reversed that decision on 2026-09-15: the flat
``/courses/<family>/<identifier>/...`` shape is canonical now, and the old
``cohorts/`` prefix is inbound-only. One generic redirect view covers every
retired route rather than duplicating a redirect per operation.
"""

from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect


def cohorts_prefix_redirect(
    request: HttpRequest,
    course_slug: str,
    legacy_suffix: str,
) -> HttpResponse:
    """301 an old ``.../cohorts/<suffix>`` URL to its flat replacement.

    ``legacy_suffix`` is everything after ``cohorts/`` (the identifier plus
    any further path segments); the query string is preserved as-is.
    """

    new_path = f"/courses/{course_slug}/{legacy_suffix}"
    query_string = request.META.get("QUERY_STRING", "")
    if query_string:
        new_path = f"{new_path}?{query_string}"
    return HttpResponsePermanentRedirect(new_path)
