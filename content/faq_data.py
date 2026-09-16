"""Validated, source-backed FAQ data and rendering helpers.

FAQ courses are ``SyncedDocument`` rows written by the ``community_base.content_sync``
engine's ``dtc-faq`` parser. Views never import the source checkout or evaluate answer
text as a template; Markdown is rendered only after image tokens are resolved and the
shared HTML allow-list has sanitized the result.  Cached read models are keyed by the
synced state they were built from, so a sync rebuilds them instead of serving stale
courses or stale question links (audit ARC-02).
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Mapping
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import mistune
from django.db.models import Count, Max

from .models import SyncedDocument
from .services import sanitize_rendered_html

FAQ_ASSET_ROOT = Path(__file__).with_name("faq_assets")
#: The staged release that still publishes the FAQ until the pipeline retires;
#: ``scripts/prod/import_faq.py`` shares this name with the synced source
#: (``content.sync_parsers.faq.SOURCE_SLUG``).
FAQ_SOURCE_STABLE_ID = "dtc-faq"
FAQ_ENGINE_SOURCE_SLUG = "dtc-faq"
FAQ_CONTENT_KIND = "faq"
FAQ_SOURCE_REPOSITORY = "DataTalksClub/faq"
FAQ_SOURCE_REVISION = "c8da1deea9e24945922702994de101dd90a5380a"
FAQ_COURSE_ORDER = (
    "data-engineering-zoomcamp",
    "stock-markets-analytics-zoomcamp",
    "machine-learning-zoomcamp",
    "llm-zoomcamp",
    "ai-dev-tools-zoomcamp",
    "mlops-zoomcamp",
)
_QUESTION_ID = re.compile(r"^[A-Za-z0-9]{10}$", re.ASCII)
_IMAGE_TOKEN = re.compile(
    r"<\{IMAGE:(?P<bracket>[A-Za-z0-9_-]+)\}>|<>\{IMAGE:(?P<malformed>[A-Za-z0-9_-]+)\}|\{IMAGE:(?P<bare>[A-Za-z0-9_-]+)\}"
)
_FAQ_IMAGE_WRAPPER = re.compile(
    r"!\[(?P<label>(?:\\.|[^\]\\\r\n])*)\]"
    r"\(\s*(?:"
    r"<\{IMAGE:(?P<bracket>[A-Za-z0-9_-]+)\}>|"
    r"<>\{IMAGE:(?P<malformed>[A-Za-z0-9_-]+)\}|"
    r"\{IMAGE:(?P<bare>[A-Za-z0-9_-]+)\}"
    r")\s*\)"
)
_COMMUNITY_WORKSPACE_HOST = "datatalks-club.slack.com"
_SLACK_CLIENT_HOST = "app.slack.com"
_COMMUNITY_WORKSPACE_ID = "T01ATQK62F8"


class _FAQRenderer(mistune.HTMLRenderer):
    """Keep source image descriptions while adding a predictable lazy-loading hint."""

    def __init__(
        self,
        *,
        faq_course_slug: str | None = None,
        faq_question_links: Mapping[str, str] | None = None,
        faq_question_slugs: Mapping[str, str] | None = None,
        escape: bool = True,
    ) -> None:
        super().__init__(escape=escape)
        self._faq_course_slug = faq_course_slug
        self._faq_question_links = faq_question_links or {}
        self._faq_question_slugs = faq_question_slugs or {}

    def link(self, text: str, url: str, title: str | None = None) -> str:
        url = _community_slack_destination(url)
        url = _resolve_faq_question_link(
            url,
            course_slug=self._faq_course_slug,
            question_links=self._faq_question_links,
            question_slugs=self._faq_question_slugs,
        )
        return super().link(text, url, title)

    def image(self, text: str, url: str, title: str | None = None) -> str:
        source = mistune.escape(self.safe_url(url), quote=True)
        alt = mistune.escape(text, quote=True)
        result = f'<img src="{source}" alt="{alt}" loading="lazy"'
        if title:
            result += f' title="{mistune.escape(title, quote=True)}"'
        return result + " />"


_MARKDOWN = mistune.create_markdown(
    renderer=_FAQRenderer(escape=False),
    escape=False,
    plugins=("strikethrough", "table"),
)


def _faq_question_slug(filename: str) -> str | None:
    """Return the source filename's human-readable slug, if it has one."""

    stem = filename.removesuffix(".md")
    _sort_order, separator, remainder = stem.partition("_")
    if not separator or not _sort_order.isdigit() or not remainder:
        return None
    if len(remainder) > 11 and remainder[10] == "_" and _QUESTION_ID.fullmatch(remainder[:10]):
        remainder = remainder[11:]
    return remainder or None


def _add_faq_question_reference(
    references: dict[str, str | None], key: str, question_id: str
) -> None:
    """Add one reference while dropping ambiguous aliases from the bounded index."""

    existing = references.get(key)
    if existing is None and key in references:
        return
    if existing is not None and existing != question_id:
        references[key] = None
        return
    references[key] = question_id


@lru_cache(maxsize=32)
def _faq_question_reference_index(
    stamp: tuple[int, str],
    course_slug: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build same-course lookup maps for one FAQ render, bound to one synced state.

    The key names the synced state the maps were built from: a sync changes the
    stamp, so a warmed index never resolves a link to a question id the current
    rows do not publish, and the maps rebuild instead of serving stale ones
    (audit ARC-02).
    """

    courses = _faq_synced_catalogue(stamp)["courses"]
    course = next((held for held in courses if held["slug"] == course_slug), None)
    if course is None:
        return {}, {}

    filenames: dict[str, str | None] = {}
    slugs: dict[str, str | None] = {}
    for question in faq_questions(course):
        question_id = question["id"]
        source_path = question.get("source_path")
        if not isinstance(source_path, str):
            continue
        filename = PurePosixPath(source_path).name
        if not filename or filename in {".", ".."}:
            continue
        _add_faq_question_reference(filenames, filename, question_id)
        slug = _faq_question_slug(filename)
        if slug:
            _add_faq_question_reference(slugs, slug, question_id)

    # An ambiguous alias is deliberately omitted rather than guessed.  This keeps every
    # rewrite bounded to one projected question in the current course.
    return (
        {key: value for key, value in filenames.items() if value is not None},
        {key: value for key, value in slugs.items() if value is not None},
    )


def _community_slack_destination(value: str) -> str:
    """Send one community-workspace Slack link destination to the canonical ``/slack`` page.

    Workspace links point into Slack's own UI (``datatalks-club.slack.com/<anything>`` or
    ``app.slack.com/client/T01ATQK62F8/<channel>`` for this workspace's ID), so the rewrite
    drops their query and fragment.  Slack's product documentation (``slack.com/help/...``)
    and every other destination pass through unchanged.  Resolving this in the renderer's
    ``link`` hook keeps Markdown code spans and plain literal text byte-for-byte intact.
    """

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == _COMMUNITY_WORKSPACE_HOST or (
        parsed.netloc == _SLACK_CLIENT_HOST
        and (
            segments[:1] == [_COMMUNITY_WORKSPACE_ID]
            or segments[:2] == ["client", _COMMUNITY_WORKSPACE_ID]
        )
    ):
        return "/slack"
    return value


def _resolve_faq_question_link(
    value: str,
    *,
    course_slug: str | None,
    question_links: Mapping[str, str],
    question_slugs: Mapping[str, str],
) -> str:
    """Resolve one exact source-relative question reference to its public FAQ fragment."""

    if not course_slug or not value or "\\" in value or value.startswith("/"):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme or parsed.netloc or "?" in value or "#" in value or not parsed.path:
        return value

    path = parsed.path
    filename = PurePosixPath(path).name
    question_id = question_links.get(filename)
    if question_id is None and "/" not in path and path in question_slugs:
        question_id = question_slugs[path]
    if question_id is None:
        return value
    return f"/faq/{course_slug}.html#{question_id}"


def faq_sync_stamp() -> tuple[int, str]:
    """A cheap stamp that moves whenever the synced FAQ rows could have.

    One aggregate, with the same contract as ``content.catalogue.active_release_id``:
    an absent source or an empty one is a genuinely empty FAQ and reads as a zero
    count; a database failure raises, so an outage can never enter a cache below
    disguised as content.
    """

    stamp = SyncedDocument.objects.filter(
        source__slug=FAQ_ENGINE_SOURCE_SLUG, source__is_enabled=True
    ).aggregate(total=Count("id"), latest=Max("updated_at"))
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


@lru_cache(maxsize=2)
def _faq_synced_catalogue(stamp: tuple[int, str]) -> dict[str, Any]:
    """The published FAQ courses of exactly one synced state.

    Each course is one synced row: the page's own address and name are its
    columns, and the sections and questions beneath it are its structure,
    carried in the row's record. The parser stores that structure in its own
    vocabulary -- the question body, the source-relative image paths -- so
    :func:`_synced_course_record` translates it to the published shape rather
    than asking every reader to know both. Courses come back in the order the
    source publishes them.

    The query is bound to the stamp the cache key names, so a warmed catalogue
    always answers for the state it was built from, and a sync builds the next
    one instead of serving stale courses. A zero count is an absent pointer --
    an empty FAQ, not a failure -- so it answers without touching the database.
    """

    if not stamp[0]:
        return {"schema_version": 1, "courses": []}
    rows = list(
        SyncedDocument.objects.filter(
            source__slug=FAQ_ENGINE_SOURCE_SLUG,
            content_kind=FAQ_CONTENT_KIND,
            is_published=True,
        )
    )
    by_slug = {str(row.stable_key): _synced_course_record(row) for row in rows}
    ordered = [by_slug[slug] for slug in FAQ_COURSE_ORDER if slug in by_slug]
    ordered.extend(
        course for slug, course in sorted(by_slug.items()) if slug not in FAQ_COURSE_ORDER
    )
    return {"schema_version": 1, "courses": ordered}


def _synced_course_record(row: SyncedDocument) -> dict[str, Any]:
    """One FAQ course, as the views and renderers read it, from its synced row.

    The image public paths are derived the way the reviewed build derived them:
    the course's declared images publish flat under the course's asset route,
    named by their own file name, with the bytes checked in beside the app.
    """

    record = row.record if isinstance(row.record, dict) else {}
    course_slug = str(row.stable_key)
    sections: list[dict[str, Any]] = []
    for section in record.get("sections") or ():
        if not isinstance(section, dict):
            continue
        questions: list[dict[str, Any]] = []
        for question in section.get("questions") or ():
            if not isinstance(question, dict):
                continue
            questions.append(
                {
                    "id": str(question.get("id") or ""),
                    "slug": question.get("slug"),
                    "question": str(question.get("question") or ""),
                    "answer": question.get("body") if isinstance(question.get("body"), str) else "",
                    "sort_order": question.get("sort_order"),
                    "course": course_slug,
                    "section": str(section.get("name") or ""),
                    "section_id": str(section.get("id") or ""),
                    "source_path": question.get("source_path")
                    if isinstance(question.get("source_path"), str)
                    else "",
                    "images": [
                        {
                            "id": str(image.get("id") or ""),
                            "description": str(image.get("description") or ""),
                            "public_path": _faq_image_public_path(
                                course_slug, str(image.get("path") or "")
                            ),
                        }
                        for image in question.get("images") or ()
                        if isinstance(image, dict) and image.get("path")
                    ],
                }
            )
        sections.append(
            {
                "id": str(section.get("id") or ""),
                "name": str(section.get("name") or ""),
                "comment": str(section.get("comment") or ""),
                "questions": tuple(questions),
            }
        )
    return {
        "slug": course_slug,
        "public_path": row.public_path,
        "name": row.title,
        "slack_channel": str(record.get("slack_channel") or ""),
        # The published counts the feeds and the hub state: derived from the
        # structure itself, so they cannot disagree with it.
        "section_count": len(sections),
        "question_count": sum(len(section["questions"]) for section in sections),
        "sections": tuple(sections),
    }


def _faq_image_public_path(course_slug: str, source_path: str) -> str:
    """The published address of one declared FAQ image, from its source path."""

    return f"/faq/images/{course_slug}/{PurePosixPath(source_path).name}"


def faq_courses() -> tuple[dict[str, Any], ...]:
    return tuple(_faq_synced_catalogue(faq_sync_stamp())["courses"])


def faq_course(course_slug: str) -> dict[str, Any] | None:
    return next((course for course in faq_courses() if course["slug"] == course_slug), None)


#: Course-family slugs whose slug is not the FAQ document's own slug.  Every
#: other family already names its FAQ document directly (``llm-zoomcamp``,
#: ``mlops-zoomcamp``, ``ai-dev-tools-zoomcamp`` all appear verbatim in
#: ``FAQ_COURSE_ORDER``), so only the abbreviated family slugs are listed here
#: rather than guessed.
FAQ_COURSE_SLUG_BY_FAMILY_SLUG = {
    "de-zoomcamp": "data-engineering-zoomcamp",
    "ml-zoomcamp": "machine-learning-zoomcamp",
    "sma-zoomcamp": "stock-markets-analytics-zoomcamp",
}


def faq_course_for_family_slug(family_slug: str) -> dict[str, Any] | None:
    """Return the published FAQ course document that covers one course family.

    ``None`` when no FAQ document is published for it -- a real absence, not a
    failure, so a caller degrades gracefully rather than showing nothing to
    parse.
    """

    faq_slug = FAQ_COURSE_SLUG_BY_FAMILY_SLUG.get(family_slug, family_slug)
    return faq_course(faq_slug)


def faq_questions(course: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(question for section in course["sections"] for question in section["questions"])


def faq_asset_path(course_slug: str, asset: str) -> Path | None:
    """Resolve one generated FAQ image or stylesheet without allowing traversal."""

    if not asset or Path(asset).name != asset or Path(asset).is_absolute():
        return None
    if course_slug == "css":
        path = FAQ_ASSET_ROOT / "css" / asset
    elif course_slug in FAQ_COURSE_ORDER:
        path = FAQ_ASSET_ROOT / course_slug / asset
    else:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    try:
        path.resolve().relative_to(FAQ_ASSET_ROOT.resolve())
    except ValueError:
        return None
    return path


def _convert_plain_urls_to_links(text: str) -> str:
    """Match the legacy generator's plain URL behavior outside code spans and fences."""

    chunks = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    result: list[str] = []
    url_pattern = re.compile(r"(?<!\[)(?<!\()(?<!<)(https?://[^\s<>\)]+)(?!\])(?!\))(?!>)")
    for index, chunk in enumerate(chunks):
        if index % 2:
            result.append(chunk)
            continue
        inline = re.split(r"(`[^`]+`)", chunk)
        for inline_index, piece in enumerate(inline):
            if inline_index % 2:
                result.append(piece)
                continue

            def link(match: re.Match[str]) -> str:
                url = match.group(1)
                trailing = ""
                while url and url[-1] in ".,;:!?":
                    trailing = url[-1] + trailing
                    url = url[:-1]
                return f"[{url}]({url}){trailing}"

            result.append(url_pattern.sub(link, piece))
    return "".join(result)


def _faq_declared_image_path(course_slug: str | None, public_path: str) -> str | None:
    """Return a local asset filename for one safe, declared FAQ image path."""

    if not course_slug or not isinstance(public_path, str):
        return None
    try:
        parsed = urlsplit(public_path)
    except ValueError:
        return None
    if (
        parsed.path != public_path
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or ord(character) < 0x20 for character in public_path)
    ):
        return None
    prefix = f"/faq/images/{course_slug}/"
    if not public_path.startswith(prefix):
        return None
    asset = public_path.removeprefix(prefix)
    if not asset or "/" in asset or "\\" in asset:
        return None
    if faq_asset_path(course_slug, asset) is None:
        return None
    return asset


def _faq_image_map(
    course_slug: str | None, images: list[dict[str, Any]]
) -> dict[str, tuple[str, str]]:
    """Build a same-question map of image IDs to checked-in, declared assets."""

    image_map: dict[str, tuple[str, str]] = {}
    for image in images:
        if not isinstance(image, Mapping):
            continue
        image_id = image.get("id")
        description = image.get("description")
        public_path = image.get("public_path")
        if (
            not isinstance(image_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", image_id)
            or not isinstance(description, str)
            or not isinstance(public_path, str)
            or _faq_declared_image_path(course_slug, public_path) is None
        ):
            continue
        image_map[image_id] = (description, public_path)
    return image_map


def _faq_image_token_id(match: re.Match[str]) -> str:
    return match.group("bracket") or match.group("malformed") or match.group("bare")


def _faq_image_markdown(image: tuple[str, str]) -> str:
    description, public_path = image
    # The description is source-derived alt text.  Escape the Markdown delimiters before
    # handing it to Mistune so an unusual description cannot create nested markup.
    alt = description.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"![{alt}]({public_path})"


def _replace_image_tokens_in_text(text: str, image_map: Mapping[str, tuple[str, str]]) -> str:
    """Rewrite supported image-token forms in one non-code Markdown segment."""

    result: list[str] = []
    position = 0
    while position < len(text):
        # Consume a malformed legacy image expression before matching its inner token.  This
        # prevents nested ![...]( ![...](...) ) output and removes unknown wrappers safely.
        wrapper = _FAQ_IMAGE_WRAPPER.match(text, position)
        if wrapper is not None:
            image = image_map.get(_faq_image_token_id(wrapper))
            result.append(_faq_image_markdown(image) if image is not None else "")
            position = wrapper.end()
            continue

        token = _IMAGE_TOKEN.match(text, position)
        if token is not None:
            image = image_map.get(_faq_image_token_id(token))
            result.append(_faq_image_markdown(image) if image is not None else "")
            position = token.end()
            continue

        result.append(text[position])
        position += 1
    return "".join(result)


def _markdown_code_end(text: str, position: int) -> int | None:
    """Return the end of a fenced or inline code span beginning at *position*."""

    character = text[position]
    if character not in "`~":
        return None
    run_end = position + 1
    while run_end < len(text) and text[run_end] == character:
        run_end += 1
    run_length = run_end - position
    line_start = text.rfind("\n", 0, position) + 1
    indentation = text[line_start:position]

    # A backtick/tilde run at the start of a line is a fenced code block.  Keep the entire
    # block byte-for-byte unchanged, including any legacy-looking tokens in the code.
    if run_length >= 3 and len(indentation) <= 3 and not indentation.strip(" "):
        opening_line_end = text.find("\n", run_end)
        if opening_line_end < 0:
            return len(text)
        cursor = opening_line_end + 1
        while cursor < len(text):
            line_end = text.find("\n", cursor)
            if line_end < 0:
                line_end = len(text)
                next_cursor = len(text)
            else:
                next_cursor = line_end + 1
            line = text[cursor:line_end]
            closing = re.match(r" {0,3}(?P<fence>[`~]{3,})[ \t]*$", line)
            if (
                closing is not None
                and closing.group("fence")[0] == character
                and len(closing.group("fence")) >= run_length
            ):
                return next_cursor
            cursor = next_cursor
        return len(text)

    # Inline code uses a matching run of backticks.  Tildes are only fences in the supported
    # Markdown grammar and otherwise remain ordinary text.
    if character != "`":
        return None
    closing_position = text.find(character * run_length, run_end)
    return None if closing_position < 0 else closing_position + run_length


def _replace_image_tokens_outside_code(
    answer: str, image_map: Mapping[str, tuple[str, str]]
) -> str:
    """Apply the compatibility rewrite without touching Markdown code spans or fences."""

    result: list[str] = []
    unprotected_start = 0
    position = 0
    while position < len(answer):
        code_end = _markdown_code_end(answer, position)
        if code_end is None:
            position += 1
            continue
        if unprotected_start < position:
            result.append(
                _replace_image_tokens_in_text(answer[unprotected_start:position], image_map)
            )
        result.append(answer[position:code_end])
        position = code_end
        unprotected_start = position
    if unprotected_start < len(answer):
        result.append(_replace_image_tokens_in_text(answer[unprotected_start:], image_map))
    return "".join(result)


def _replace_image_tokens(
    answer: str,
    images: list[dict[str, Any]],
    *,
    course_slug: str | None = None,
) -> str:
    """Resolve legacy FAQ image tokens using only this question's local declarations."""

    image_map = _faq_image_map(course_slug, images)
    return _replace_image_tokens_outside_code(answer, image_map)


def render_faq_answer(question: dict[str, Any]) -> str:
    course_slug = question.get("course")
    if not isinstance(course_slug, str):
        course_slug = None
    markdown = _replace_image_tokens(
        question["answer"], question.get("images", []), course_slug=course_slug
    )
    # Keep checked-in FAQ source content unchanged while replacing its legacy site link in the
    # rendered answer.  This prevents public pages from sending readers back to the legacy alias.
    markdown = re.sub(
        r"https?://datatalks\.club/slack\.html(?P<fragment>#[^\s)]+)?",
        lambda match: "/slack" + (match.group("fragment") or ""),
        markdown,
    )
    if isinstance(course_slug, str):
        question_links, question_slugs = _faq_question_reference_index(
            faq_sync_stamp(), course_slug
        )
    else:
        course_slug = None
        question_links, question_slugs = {}, {}
    renderer = _FAQRenderer(
        escape=False,
        faq_course_slug=course_slug,
        faq_question_links=question_links,
        faq_question_slugs=question_slugs,
    )
    markdown_renderer = mistune.create_markdown(
        renderer=renderer,
        escape=False,
        plugins=("strikethrough", "table"),
    )
    rendered = str(markdown_renderer(_convert_plain_urls_to_links(markdown)))
    return sanitize_rendered_html("faq", rendered)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def faq_answer_text(question: dict[str, Any]) -> str:
    parser = _TextExtractor()
    parser.feed(render_faq_answer(question))
    return " ".join(" ".join(parser.parts).split())


def faq_asset_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
