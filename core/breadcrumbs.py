"""One page's breadcrumb ancestry, written once and read by both renderings.

A detail page says where it sits twice: in the visible trail at the top of the
header, and — on the content surfaces — in the ``BreadcrumbList`` JSON-LD the
page publishes.  Those were two hand-maintained copies that had already drifted,
so a page's levels are declared here once and both renderings read the same
value:

* :func:`trail` builds the levels from parent to page;
* :meth:`Trail.published_items` is what ``content.public_views._json_ld``
  serialises, and it is the only place the site-root level is added;
* ``{% breadcrumbs %}`` (``core.templatetags.breadcrumbs``) draws the same
  object as the visible ``nav.breadcrumbs`` primitive.

The visible trail omits the site root deliberately.  "Home" is already the
masthead's own job, so drawing it as a crumb spends a level on the one
destination every page already offers; the published list keeps it because
``BreadcrumbList`` is read as a path from the site root.  That difference is a
single documented rule here rather than a divergence between two templates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HOME_LABEL = "Home"
HOME_PATH = "/"


@dataclass(frozen=True, slots=True)
class Crumb:
    """One level of a trail: what it is called, and where it goes.

    An empty ``url`` is a level that names something the reader cannot navigate
    to — the Studio root when its route is not installed, for example.  It is
    drawn as plain text rather than as a link that goes nowhere.
    """

    label: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class Trail:
    """The ancestors of a page, plus the page itself.

    ``current`` is the page the trail was built for.  It is published in the
    JSON-LD, because a ``BreadcrumbList`` that stops at the parent does not say
    which page it describes.  Whether it is also *drawn* is the visible trail's
    decision and is documented on the template tag.
    """

    ancestors: tuple[Crumb, ...] = ()
    current: Crumb | None = field(default=None)

    def published_items(self) -> tuple[tuple[str, str], ...]:
        """The ``(name, path)`` levels of the published ``BreadcrumbList``."""

        levels = [(HOME_LABEL, HOME_PATH)]
        levels.extend((crumb.label, crumb.url) for crumb in self.ancestors)
        if self.current is not None:
            levels.append((self.current.label, self.current.url))
        return tuple(levels)


def trail(*levels: tuple[str, str]) -> Trail:
    """Build a trail from parent-to-page ``(label, url)`` levels.

    The last level is the page itself; everything before it is an ancestor.  A
    page with no ancestor of its own — a person profile sits directly under the
    site root — passes one level and gets a trail whose only entry is itself.
    """

    if not levels:
        raise ValueError("a breadcrumb trail names at least the page itself")
    crumbs = tuple(Crumb(label, url) for label, url in levels)
    return Trail(ancestors=crumbs[:-1], current=crumbs[-1])
