"""Every public path this site serves.

The fixed routes are named here because they are the site's own structure -- a
hub exists whether or not anything is published under it. Everything else is
read from the database that publishes it, so an un-ingested database yields the
fixed routes and nothing more.

This is the inventory the canonical-route and SEO checks compare against; it is
not what any page renders.
"""

from __future__ import annotations

from events.queries import published_event_records

from . import catalogue

#: The routes the site serves regardless of what is published.
FIXED_PATHS = frozenset(
    {
        "/",
        "/blog",
        "/podcast",
        "/books",
        "/events",
        "/events/past",
        "/courses",
        "/wiki",
        "/docs/",
        "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        "/faq/",
        "/faq/ai-dev-tools-zoomcamp.html",
        "/wiki/graph",
        "/wiki/search",
        "/wiki/special-pages",
        "/wiki/feed.xml",
        "/wiki/sitemap.xml",
    }
)
#: The wiki's own subject listings, which are routes rather than records.
WIKI_SPECIAL_PATHS = frozenset(
    f"/wiki/special-pages/{category}"
    for category in ("guides", "comparisons", "roadmaps", "transitions", "how-tos")
)


def public_paths() -> tuple[str, ...]:
    """The fixed routes plus one path per published record, sorted."""

    paths = set(FIXED_PATHS) | set(WIKI_SPECIAL_PATHS)
    for published in (
        catalogue.articles,
        catalogue.podcasts,
        catalogue.books,
        catalogue.people,
        catalogue.wiki_pages,
        published_event_records,
    ):
        paths.update(record["public_path"] for record in published())
    return tuple(sorted(paths))
