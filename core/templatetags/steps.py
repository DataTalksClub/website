"""The site's one numbered-steps list.

Every "how it works" / process / numbered sequence on the site is drawn by
``templates/core/_steps.html`` (see ``_docs/design/design-system.md`` →
"Numbered steps").  The partial takes a list of ``{"title", "body"}`` steps, so a
page whose steps already arrive as a list (a database record, a content file)
can include it directly when the keys match.

Most pages, though, keep their step copy in the template itself, or hold it
under other keys (``text``, ``label``/``value``, a bare string).  This block
tag lets the copy stay exactly where it lives while the markup stays in the
one partial::

    {% load steps %}
    {% steps %}
      {% step title="Register, free" %}
        One form gets you the schedule.
      {% endstep %}
      {% for item in record.steps %}
        {% step title=item.title %}{{ item.text }}{% endstep %}
      {% endfor %}
    {% endsteps %}

``{% steps heading_level=4 %}`` sets the step title's heading level (default
3).  ``title`` is optional: a step without one is a numbered line of body
copy.  The body is ordinary template output -- autoescaped, links and inline
code allowed -- and an empty body draws no paragraph.
"""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

register = template.Library()

_COLLECTOR = "_steps_collector"


class StepsNode(template.Node):
    def __init__(self, nodelist: template.NodeList, heading_level):
        self.nodelist = nodelist
        self.heading_level = heading_level

    def render(self, context) -> str:
        collected: list[dict[str, str]] = []
        with context.push({_COLLECTOR: collected}):
            self.nodelist.render(context)
        level = self.heading_level.resolve(context) if self.heading_level else 3
        return render_to_string(
            "core/_steps.html",
            {"steps": collected, "heading_level": level},
        )


class StepNode(template.Node):
    def __init__(self, nodelist: template.NodeList, title):
        self.nodelist = nodelist
        self.title = title

    def render(self, context) -> str:
        collected = context.get(_COLLECTOR)
        if collected is None:
            raise template.TemplateSyntaxError("{% step %} must sit inside {% steps %}.")
        body = self.nodelist.render(context).strip()
        title = self.title.resolve(context) if self.title else ""
        # The body is the rendered (already autoescaped) template output.
        collected.append({"title": title, "body": mark_safe(body)})  # noqa: S308
        return ""


def _keyword(parser, bits: list[str], tag: str, name: str):
    found = None
    for bit in bits[1:]:
        key, sep, value = bit.partition("=")
        if not sep or key != name:
            raise template.TemplateSyntaxError(f"{{% {tag} %}} only takes {name}=...")
        found = parser.compile_filter(value)
    return found


@register.tag(name="steps")
def do_steps(parser, token):
    bits = token.split_contents()
    heading_level = _keyword(parser, bits, "steps", "heading_level")
    nodelist = parser.parse(("endsteps",))
    parser.delete_first_token()
    return StepsNode(nodelist, heading_level)


@register.tag(name="step")
def do_step(parser, token):
    bits = token.split_contents()
    title = _keyword(parser, bits, "step", "title")
    nodelist = parser.parse(("endstep",))
    parser.delete_first_token()
    return StepNode(nodelist, title)
