"""One reviewed vocabulary of credential-shaped query keys.

The preview decorator, the public alias redirects, and the response cache
policy all ask the same question — does this request carry a
credential-shaped query key? — so one code-owned list decides where a
private variant must never enter a shared or stored response object.
Consumers inspect key names only: values are never read, reflected, or
logged.
"""

from __future__ import annotations

from urllib.parse import parse_qsl

from django.http import HttpRequest

SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "code",
        "credential",
        "jwt",
        "password",
        "preview_token",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)


def has_sensitive_query_key(request: HttpRequest) -> bool:
    """Whether any decoded query key is credential-shaped.

    The raw query string is parsed here rather than through ``request.GET``:
    a WSGI environ value is read back latin-1 and ``request.GET`` raises on
    any request that carried a raw non-ASCII byte, while a bounded privacy
    check must classify — never crash on — such a query.  Keys come back
    percent-decoded (encoded spellings of a listed key are caught) and case
    folding catches capitalisation.  An empty value still counts: the key
    name alone makes the URL private.  Only key names are inspected; values
    stay unread.
    """

    raw_query = request.META.get("QUERY_STRING", "")
    return any(
        key.casefold() in SENSITIVE_QUERY_KEYS
        for key, _value in parse_qsl(raw_query, keep_blank_values=True)
    )
