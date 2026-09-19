"""The site-owned rendering pipeline for the documentation pages.

The shared ``community_base.knowledge_base`` app stores a page and its rendered
HTML; it does not render this corpus. The documentation repository is a Jekyll
"Just the Docs" site, so its bodies carry kramdown attribute blocks, Liquid
``relative_url`` filters and source-era link destinations the package's markdown
renderer knows nothing about. This module is that pipeline: it takes one raw
documentation body and returns sanitized HTML plus the heading metadata the
table of contents reads.

It runs at sync time. ``content.sync_parsers.docs`` renders each page once and
hands the result to the package app, which sanitizes and stores it verbatim
instead of re-rendering it. Nothing renders a documentation body on a request.

Moved here byte for byte from the retired ``content/docs_projection.py``; the
storage and hierarchy halves of that module are the package app's now.
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import mistune

from .services import sanitize_rendered_html

#: The mount every documentation path hangs from.
DOCS_ROOT_PATH = "/docs/"
_DOCS_PREFIXES = ("/courses/", "/general/", "/activities/", "/assets/")
_LIQUID_RELATIVE_URL = re.compile(
    r"{{\s*(['\"])(?P<path>.*?)\1\s*\|\s*relative_url\s*}}",
    re.DOTALL,
)
_KRAMDOWN_ATTRIBUTE_LINE = re.compile(r"(?m)^\s*\{:\s*[^}\n]+\}\s*$")
_KRAMDOWN_INLINE_ATTRIBUTE = re.compile(r"\]\((?P<url>[^)\n]+)\)\{:\s*[^}\n]+\}")
_HEADING = re.compile(
    r"(?P<open><h(?P<level>[1-6])>)(?P<body>.*?)(?P<close></h(?P=level)>)",
    re.DOTALL,
)
_MARKDOWN = mistune.create_markdown(escape=False, plugins=("strikethrough", "table"))


class _HeadingText(HTMLParser):
    """Collect visible text from one rendered heading without trusting source HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _slugify_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", html.unescape(value))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "section"


def _heading_text(value: str) -> str:
    parser = _HeadingText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def _heading_ids(rendered: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    seen: dict[str, int] = {}
    headings: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        text = _heading_text(match.group("body"))
        base = _slugify_heading(text)
        count = seen.get(base, 0)
        seen[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        headings.append({"level": int(match.group("level")), "id": slug, "title": text})
        return (
            f'<h{match.group("level")} id="{slug}">{match.group("body")}</h{match.group("level")}>'
        )

    return _HEADING.sub(replace, rendered), tuple(headings)


def _docs_url(value: str) -> str:
    """Rewrite a source-root URL to the Django docs mount when it is a docs URL.

    The source uses Jekyll's ``relative_url`` filter.  Main-site links (for example ``/slack``)
    remain main-site links, while paths owned by the docs repository gain the ``/docs`` mount.
    """

    value = value.strip()
    if not value:
        return value
    if value.startswith("//"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return value
    if value == "/":
        return DOCS_ROOT_PATH
    if value.startswith(_DOCS_PREFIXES):
        return f"/docs{value}"
    return value


_INTERNAL_HUB_PATHS = {
    "/events.html": "/events",
    "/podcast.html": "/podcast",
    "/books.html": "/books",
    "/slack.html": "/slack",
    "/slack/guidelines.html": "/slack",
    "/slack": "/slack",
}
_NEWSLETTER_PATH = "/newsletter.html"
_LUMA_EVENTS_URL = "https://luma.com/dtc-events"
_COMMUNITY_WORKSPACE_HOST = "datatalks-club.slack.com"
_SLACK_CLIENT_HOST = "app.slack.com"
_COMMUNITY_WORKSPACE_ID = "T01ATQK62F8"


def _rewritten_internal_destination(value: str) -> str | None:
    """Return the allowlisted destination replacement, preserving query and fragment."""

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "datatalks.club":
            return None
    replacement = _INTERNAL_HUB_PATHS.get(parsed.path)
    if replacement is None:
        return None
    query_source = value.split("#", 1)[0]
    query = f"?{parsed.query}" if "?" in query_source else ""
    fragment = f"#{parsed.fragment}" if "#" in value else ""
    return f"{replacement}{query}{fragment}"


def _community_workspace_destination(value: str) -> str | None:
    """Return ``/slack`` for a DataTalks.Club community-workspace link destination.

    The workspace forms in the source are ``datatalks-club.slack.com/<anything>`` and
    ``app.slack.com/client/T01ATQK62F8/<channel>``, where ``T01ATQK62F8`` is this workspace's
    ID.  Both address Slack's own UI rather than anchors on ``/slack``, so unlike the hub
    aliases the rewrite drops the query and fragment.  Slack's product documentation
    (``slack.com/help/...``) and every other external host stay untouched.
    """

    parsed = urlsplit(value)
    if parsed.netloc == _COMMUNITY_WORKSPACE_HOST:
        return "/slack"
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == _SLACK_CLIENT_HOST and (
        segments[:1] == [_COMMUNITY_WORKSPACE_ID]
        or segments[:2] == ["client", _COMMUNITY_WORKSPACE_ID]
    ):
        return "/slack"
    return None


def _is_newsletter_destination(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "datatalks.club":
            return False
    return parsed.path == _NEWSLETTER_PATH


def _find_unescaped(value: str, start: int, target: str) -> int | None:
    """Find one unescaped character in Markdown text."""

    index = start
    while index < len(value):
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == target:
            return index
        index += 1
    return None


def _find_link_label_end(value: str, start: int) -> int | None:
    """Find a closing Markdown link label bracket, allowing nested brackets."""

    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _find_link_destination_end(value: str, start: int) -> int | None:
    """Find the closing parenthesis for one Markdown inline link."""

    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _link_destination_token(inner: str) -> tuple[int, int, str] | None:
    """Return the destination token offsets and value from Markdown link contents."""

    start = 0
    while start < len(inner) and inner[start].isspace():
        start += 1
    if start == len(inner):
        return None
    if inner[start] == "<":
        end = _find_unescaped(inner, start + 1, ">")
        if end is None:
            return None
        return start + 1, end, inner[start + 1 : end]
    end = start
    while end < len(inner) and not inner[end].isspace():
        end += 1
    return start, end, inner[start:end]


def _rewrite_markdown_links(value: str) -> str:
    """Apply the narrow docs-link compatibility policy to inline Markdown links.

    Parsing links here (instead of replacing URL-looking text globally) keeps code spans, prose,
    unrelated hosts, and source text outside an intended Markdown destination byte-for-byte intact.
    """

    output: list[str] = []
    cursor = 0
    index = 0
    while index < len(value):
        if value[index] == "`":
            run_end = index
            while run_end < len(value) and value[run_end] == "`":
                run_end += 1
            closing = value.find(value[index:run_end], run_end)
            if closing < 0:
                index = len(value)
            else:
                index = closing + run_end - index
            continue
        if value[index] != "[" or (index > 0 and value[index - 1] in {"\\", "!"}):
            index += 1
            continue
        label_end = _find_link_label_end(value, index + 1)
        if label_end is None or label_end + 1 >= len(value) or value[label_end + 1] != "(":
            index += 1
            continue
        destination_end = _find_link_destination_end(value, label_end + 2)
        if destination_end is None:
            index += 1
            continue
        inner = value[label_end + 2 : destination_end]
        token = _link_destination_token(inner)
        if token is None:
            index = destination_end + 1
            continue
        token_start, token_end, destination = token
        replacement_url = _rewritten_internal_destination(destination)
        if replacement_url is None:
            replacement_url = _community_workspace_destination(destination)
        remove_wrapper = _is_newsletter_destination(destination)
        replace_luma_label = destination == _LUMA_EVENTS_URL and value[index + 1 : label_end] == (
            "Luma"
        )
        if replace_luma_label:
            replacement_url = "/events"
        if replacement_url is None and not remove_wrapper and not replace_luma_label:
            index = destination_end + 1
            continue

        output.append(value[cursor:index])
        label = value[index + 1 : label_end]
        if remove_wrapper:
            output.append(label)
        else:
            rewritten_inner = inner
            if replacement_url is not None:
                is_angle_destination = inner[token_start - 1 : token_start] == "<"
                rewritten_destination = (
                    f"<{replacement_url}>" if is_angle_destination else replacement_url
                )
                if is_angle_destination:
                    rewritten_inner = (
                        inner[: token_start - 1] + rewritten_destination + inner[token_end + 1 :]
                    )
                else:
                    rewritten_inner = (
                        inner[:token_start] + rewritten_destination + inner[token_end:]
                    )
            if replace_luma_label:
                label = "our events page"
            output.append(f"[{label}]({rewritten_inner})")
        cursor = destination_end + 1
        index = cursor
    output.append(value[cursor:])
    return "".join(output)


def _prepare_markdown(raw: str) -> str:
    # Just the Docs uses these attribute-only lines for typography and button classes.  They are
    # presentation metadata rather than content, and allowing them through mistune would expose
    # literal ``{: ... }`` text.  Inline attributes are treated the same way.
    prepared = _KRAMDOWN_ATTRIBUTE_LINE.sub("", raw)
    prepared = _KRAMDOWN_INLINE_ATTRIBUTE.sub(lambda match: f"]({match.group('url')})", prepared)
    prepared = _LIQUID_RELATIVE_URL.sub(lambda match: _docs_url(match.group("path")), prepared)
    return _rewrite_markdown_links(prepared)


def render_docs_markdown(body: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Render one raw documentation body to sanitized HTML plus its headings.

    The returned HTML is what the knowledge base page stores; the headings are
    the page's own table of contents, carried in the page record because the
    package app owns no column for them.
    """

    if not isinstance(body, str):
        raise TypeError("A documentation body must be text.")
    rendered = str(_MARKDOWN(_prepare_markdown(body)))
    rendered, headings = _heading_ids(rendered)
    return sanitize_rendered_html("docs", rendered), headings
