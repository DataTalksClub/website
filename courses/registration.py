import html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bleach
import mistune

COUNTRIES_CONFIG_PATH = Path(__file__).with_name("countries.txt")
TOP_COUNTRIES_SECTION = "Top Countries"
YOUTUBE_EMBED_BASE_URL = "https://www.youtube.com/embed"


def _country_config_section(line):
    if not line.startswith("["):
        return None
    if not line.endswith("]"):
        return None

    section = line[1:-1].strip()
    return section


def _add_country_to_config(countries_by_region, top_countries, section, country):
    if section is None:
        return

    if section == TOP_COUNTRIES_SECTION:
        top_countries.append(country)
        return

    region_countries = countries_by_region.setdefault(section, [])
    region_countries.append(country)


def _build_countries_config():
    top_countries = []
    countries_by_region = {}
    section = None

    config_content = COUNTRIES_CONFIG_PATH.read_text(encoding="utf-8")
    config_lines = config_content.splitlines()
    for raw_line in config_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        section_name = _country_config_section(line)
        if section_name:
            section = section_name
            if section != TOP_COUNTRIES_SECTION:
                countries_by_region[section] = []
            continue

        _add_country_to_config(countries_by_region, top_countries, section, line)

    return countries_by_region, top_countries


COUNTRIES_BY_REGION, TOP_COUNTRIES = _build_countries_config()


def _build_country_region_map():
    country_region = {}
    for region, countries in COUNTRIES_BY_REGION.items():
        for country in countries:
            country_region[country] = region
    return country_region


COUNTRY_REGION = _build_country_region_map()


def ordered_countries():
    top_countries = []
    for country in TOP_COUNTRIES:
        if country in COUNTRY_REGION:
            top_countries.append(country)

    top_country_set = set(top_countries)
    remaining_countries = []
    for country in COUNTRY_REGION.keys():
        if country not in top_country_set:
            remaining_countries.append(country)
    remaining_countries.sort()

    countries = list(top_countries)
    countries.extend(remaining_countries)
    return countries


def _build_country_choices():
    country_choices = []
    countries = ordered_countries()
    for country in countries:
        country_choice = (country, country)
        country_choices.append(country_choice)
    return country_choices


COUNTRY_CHOICES = _build_country_choices()

# Course Markdown is learner-authored upstream content that legitimately mixes
# Markdown with raw HTML blocks (thumbnail links, callout tables, inline
# emphasis).  The renderer therefore emits that raw HTML and this allowlist --
# not an unverified trust marker -- decides what survives.  The shape mirrors
# the article/wiki policy in ``content.services``; the one deliberate difference
# is the image source rule, because course images live in the upstream course
# repository rather than in the projection media store.
ALLOWED_MARKDOWN_TAGS = [
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]
ALLOWED_MARKDOWN_ATTRIBUTES = {
    "a": ["href", "title", "rel", "target"],
    "div": ["aria-label", "class", "role", "tabindex"],
    "img": ["alt", "height", "loading", "src", "title", "width"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
}
ALLOWED_MARKDOWN_PROTOCOLS = ["http", "https", "mailto"]

_PUBLIC_IMAGE_SOURCE_RE = re.compile(r"\Ahttps?://[^\s]+\Z", re.IGNORECASE)
# Bleach can drop a rejected attribute but not the element that carried it.  An
# ``<img>`` that lost its source would still paint an empty bordered box, so the
# sanitized output drops the element itself.
_SOURCELESS_IMAGE_RE = re.compile(r"<img\b(?![^>]*\ssrc=)[^>]*>", re.IGNORECASE)


def _allowed_markdown_attribute(tag: str, name: str, value: str) -> bool:
    """Return whether one sanitized attribute survives on one element."""

    if name not in ALLOWED_MARKDOWN_ATTRIBUTES.get(tag, ()):
        return False
    if tag == "img" and name == "src":
        # Repository-relative sources are resolved before rendering.  Anything
        # still relative here cannot be fetched from this site, and ``data:``
        # or ``javascript:`` sources are never course content.
        return bool(_PUBLIC_IMAGE_SOURCE_RE.match(value.strip()))
    return True


class _CourseMarkdownRenderer(mistune.HTMLRenderer):
    """Render Mermaid fences as inert, escaped source for the browser runtime."""

    def block_code(self, code: str, info: str | None = None) -> str:
        language = (info or "").strip().partition(" ")[0].lower()
        if language == "mermaid":
            return f'<div class="mermaid">{html.escape(code)}</div>\n'
        return super().block_code(code, info)

    def heading(self, text: str, level: int, **attrs: object) -> str:
        """Keep the page title as the unit surface's only level-one heading."""

        return super().heading(text, max(2, level), **attrs)


def _render_course_table(_renderer: mistune.BaseRenderer, text: str) -> str:
    return (
        '<div class="prose-scroll" role="region" aria-label="Lesson data table" '
        'tabindex="0"><table>\n'
        f"{text}</table></div>\n"
    )


def _render_course_table_cell(
    _renderer: mistune.BaseRenderer,
    text: str,
    align: str | None = None,
    head: bool = False,
) -> str:
    del align
    if head:
        return f'  <th scope="col">{text}</th>\n'
    return f"  <td>{text}</td>\n"


def _course_tables(markdown: mistune.Markdown) -> None:
    """Install semantic, keyboard-scrollable Markdown tables."""

    from mistune.plugins.table import table

    table(markdown)
    if markdown.renderer:
        markdown.renderer.register("table", _render_course_table)
        markdown.renderer.register("table_cell", _render_course_table_cell)


# ``escape=False`` hands the raw HTML written by course authors to the renderer
# instead of printing it as literal source.  It is not a trust decision: every
# rendered fragment still passes the allowlist in ``render_markdown`` below.
_COURSE_MARKDOWN = mistune.create_markdown(
    renderer=_CourseMarkdownRenderer(escape=False), plugins=[_course_tables]
)


def region_for_country(country):
    return COUNTRY_REGION.get(country, "")


def render_markdown(markdown_text):
    if not markdown_text:
        return ""

    rendered_html = _COURSE_MARKDOWN(markdown_text)
    sanitized_html = bleach.clean(
        rendered_html,
        tags=ALLOWED_MARKDOWN_TAGS,
        attributes=_allowed_markdown_attribute,
        protocols=ALLOWED_MARKDOWN_PROTOCOLS,
    )
    return _SOURCELESS_IMAGE_RE.sub("", sanitized_html)


def markdown_has_mermaid(rendered_html: str) -> bool:
    """Return whether rendered course Markdown needs the Mermaid runtime."""

    return '<div class="mermaid">' in rendered_html


def _youtube_watch_video_id(url):
    parsed_url = urlparse(url)
    if "youtube.com" not in parsed_url.netloc:
        return None
    if parsed_url.path != "/watch":
        return None

    query = parse_qs(parsed_url.query)
    video_ids = query.get("v", [])
    if not video_ids:
        return None

    video_id = video_ids[0]
    return video_id


def _youtu_be_video_id(url):
    parsed_url = urlparse(url)
    if "youtu.be" not in parsed_url.netloc:
        return None

    video_id = parsed_url.path.lstrip("/")
    if not video_id:
        return None

    return video_id


def youtube_embed_url(url):
    if not url:
        return ""

    video_id = _youtube_watch_video_id(url)
    if video_id:
        embed_url = f"{YOUTUBE_EMBED_BASE_URL}/{video_id}"
        return embed_url

    video_id = _youtu_be_video_id(url)
    if video_id:
        embed_url = f"{YOUTUBE_EMBED_BASE_URL}/{video_id}"
        return embed_url

    return url
