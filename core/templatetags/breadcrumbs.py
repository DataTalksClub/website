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
      {% breadcrumbs "Courses" courses_url current="registration" %}

  Positional arguments are read in ``label, url`` pairs.  A pair whose label is
  empty is dropped, so an ancestor that only sometimes exists costs no ``{% if
  %}`` around the call.  A pair with a label and no url is drawn as plain text —
  a level that names something the reader cannot navigate to.

* a :class:`core.breadcrumbs.Trail`, for a page that also publishes a
  ``BreadcrumbList``::

      {% breadcrumbs breadcrumb_trail nav_class="shell article-shell" %}

  The trail's own ``current`` becomes the last crumb, so the drawn trail and the
  published one cannot disagree: they are the same object.

``current`` is the page itself, drawn as plain text on ``li[aria-current="page"]``
and never as a link.  Leave it out and the trail stops at the page's parent,
which is what a page whose ``h1`` sits directly beneath the trail should do — a
last crumb repeating the heading word for word costs a whole row and says
nothing.  Pass it when the page's own name is not what the heading says, or when
the page publishes itself in a ``BreadcrumbList`` and the visible trail has to
match what is published.
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
    """Read positional arguments into ancestors plus the current page's label."""

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
    nav_class: str = "",
    aria_label: str = "Breadcrumb",
) -> SafeString:
    """Render the shared ``nav.breadcrumbs`` trail.

    ``nav_class`` adds the page's own layout classes ahead of ``breadcrumbs`` —
    the reading surfaces put the trail on their own ``shell`` so it lines up with
    the column beneath it.  The primitive's own class is always last and always
    present, so no caller can style the trail by forgetting it.
    """

    ancestors, trail_current = _levels(levels)
    label = str(current or "") or trail_current
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
