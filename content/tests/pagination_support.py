"""Walking a paginated public catalogue from a test (issues #174, #175, #177, #178).

Several contracts are about the whole catalogue rather than about one page of it:
every checked record must be reachable, no event may link outside the site, the
concatenated pages must reproduce the projection exactly once.  Those tests read a
catalogue through here so they exercise the real links a visitor follows instead of
assembling `?page=` strings of their own, which the repository guard forbids anyway.
"""

from __future__ import annotations

import re

from django.test import Client

_PAGE_LINK = re.compile(r'href="[^"]*\?page=([1-9][0-9]{0,2})"')


def catalogue_page_bodies(client: Client, clean_path: str) -> tuple[str, ...]:
    """Return the rendered body of every page of one public catalogue, in order.

    The page count is read from the controls the first page renders: the shared
    paginator always offers the last page, so the largest page number linked from
    page one is the last page.  An unpaginated catalogue yields one body.
    """

    first = client.get(clean_path)
    assert first.status_code == 200, f"{clean_path} returned {first.status_code}"
    body = first.content.decode()
    linked = [int(number) for number in _PAGE_LINK.findall(body)]
    bodies = [body]
    for page_number in range(2, max(linked, default=1) + 1):
        response = client.get(f"{clean_path}?page={page_number}")
        assert response.status_code == 200, (
            f"{clean_path}?page={page_number} returned {response.status_code}"
        )
        bodies.append(response.content.decode())
    return tuple(bodies)


def catalogue_body(client: Client, clean_path: str) -> str:
    """Every page of one public catalogue concatenated into a single body."""

    return "".join(catalogue_page_bodies(client, clean_path))
