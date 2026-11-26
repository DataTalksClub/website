"""The site's one breadcrumb trail.

Every page that shows a trail calls ``{% breadcrumbs %}``.  Before this tag the
markup was hand-written in 50-odd templates and had drifted: one page joined two
levels into a single crumb ("Machine Learning Zoomcamp registration"), one drew
``<ul>`` where the primitive asks for ``<ol>``, and each copy had to remember the
``aria-label``, the link/plain-text rule and the ``aria-current`` spelling for
itself.  The visual treatment already lived in one place — the ``.breadcrumbs``
primitive in ``templates/core/_design_system.html`` — so the markup is the half
that had no owner.

Two call shapes, one output:

* inline levels, for a page that knows its ancestors in the template::

      {% url 'course_list' as courses_url %}
      {% breadcrumbs "Courses" courses_url course.title family_url %}

  Positional arguments are read in ``label, url`` pairs.  A pair whose label is
  empty is dropped, so an ancestor that only sometimes exists costs no ``{% if
  %}`` around the call.  A pair with a label and no url is drawn as plain text —
  a level that names something the reader cannot navigate to.

* a :class:`core.breadcrumbs.Trail`, for a page that also publishes a
  ``BreadcrumbList``::

      {% breadcrumbs breadcrumb_trail nav_class="shell article-shell" %}

  The trail's ancestors are drawn; its own ``current`` stays in the published
  JSON-LD, which names the page it describes, and is not drawn.

The visible trail stops at the parent.  The ``h1`` directly below it already
says the page's name, so a last crumb repeating it ("Blog / Sponsor
DataTalks.Club: …" over the same title) is the same words twice.  The trail
reads "Blog", "Podcast / Season 25", "Courses / AI Dev Tools Zoomcamp", and the
heading finishes the sentence.  The drawn trail and the published
``BreadcrumbList`` therefore differ by exactly that last level, by design.

The exception is a page whose own crumb says something its ``h1`` does not —
the registration page under a heading that names the course, the wiki graph
under a heading that says "Wiki".  Such a call asks for the crumb explicitly:
``current="Registration"`` for inline levels, or ``show_current=True`` to draw a
Trail's own ``current``.  It is drawn as plain text on
``li[aria-current="page"]``, never as a link.  ``core/tests/test_breadcrumb_rule.py``
lists every call that asks, with its reason, so a crumb that repeats the
heading cannot creep back in.
"""

from __future__ import annotations

from collections.abc import Sequence

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

from core.breadcrumbs import Crumb, Trail

register = template.Library()

_TEMPLATE = "core/_breadcrumbs.html"


def _crumb(label: object, url: object) -> Crumb:
    return Crumb(str(label or ""), str(url or ""))


def _levels(arguments: Sequence[object]) -> tuple[list[Crumb], str]:
    """Read positional arguments into ancestors plus a Trail's own current label."""

    crumbs: list[Crumb] = []
    current = ""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if isinstance(argument, Trail):
            crumbs.extend(argument.ancestors)
            current = argument.current.label if argument.current is not None else ""
            index += 1
            continue
        if isinstance(argument, Crumb):
            crumbs.append(argument)
            index += 1
            continue
        if argument is None or argument == "":
            # A level with no label is dropped below anyway, and a view that
            # renders a template without its trail resolves the variable to the
            # empty string.  Reading that as "no trail" is the only honest
            # answer: the alternative is a page that raises rather than draws
            # one fewer crumb.  Pairs with an empty label are still pairs, so
            # this only skips a lone value.
            index += 1
            continue
        if isinstance(argument, Sequence) and not isinstance(argument, str | bytes):
            if len(argument) != 2:
                raise template.TemplateSyntaxError(
                    "{% breadcrumbs %} takes a crumb as a (label, url) pair"
                )
            crumbs.append(_crumb(argument[0], argument[1]))
            index += 1
            continue
        if index + 1 >= len(arguments):
            raise template.TemplateSyntaxError(
                "{% breadcrumbs %} takes levels as label/url pairs; "
                f"{argument!r} has no url after it"
            )
        crumbs.append(_crumb(argument, arguments[index + 1]))
        index += 2
    return [crumb for crumb in crumbs if crumb.label], current


@register.simple_tag
def breadcrumbs(
    *levels: object,
    current: object = "",
    show_current: bool = False,
    nav_class: str = "",
    aria_label: str = "Breadcrumb",
) -> SafeString:
    """Render the shared ``nav.breadcrumbs`` trail.

    ``nav_class`` adds the page's own layout classes ahead of ``breadcrumbs`` —
    the reading surfaces put the trail on their own ``shell`` so it lines up with
    the column beneath it.  The primitive's own class is always last and always
    present, so no caller can style the trail by forgetting it.

    ``current`` and ``show_current`` are the exception described in the module
    docstring: without one of them the trail ends at the parent.
    """

    ancestors, trail_current = _levels(levels)
    label = str(current or "") or (trail_current if show_current else "")
    if not ancestors and not label:
        # Nothing to draw. An empty nav landmark is worse than no landmark: a
        # screen reader announces "Breadcrumb navigation" and then finds it
        # holds nothing.
        return mark_safe("")  # noqa: S308 - the empty string is not markup
    return mark_safe(  # noqa: S308 - the partial escapes every value it draws
        render_to_string(
            _TEMPLATE,
            {
                "crumbs": ancestors,
                "current": label,
                "nav_class": f"{nav_class} breadcrumbs".strip(),
                "aria_label": aria_label,
            },
        ).strip()
    )
