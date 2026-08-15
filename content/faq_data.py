"""Validated, source-backed FAQ data and rendering helpers.

The FAQ source is intentionally frozen into a projection.  Views never import the source
checkout or evaluate answer text as a template; Markdown is rendered only after image tokens are
resolved and the shared HTML allow-list has sanitized the result.
"""

from __future__ import annotations

import json
import mimetypes
import re
from collections.abc import Mapping
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import mistune
from django.core.exceptions import ImproperlyConfigured

from .services import sanitize_rendered_html

FAQ_PROJECTION_PATH = Path(__file__).with_name("faq_projection.json")
FAQ_ASSET_ROOT = Path(__file__).with_name("faq_assets")
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


@lru_cache(maxsize=len(FAQ_COURSE_ORDER))
def _faq_question_reference_index(
    course_slug: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build immutable-source, same-course lookup maps for one FAQ render."""

    course = faq_course(course_slug)
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


def _load_projection() -> dict[str, Any]:
    try:
        projection = json.loads(FAQ_PROJECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("FAQ content projection cannot be loaded.") from exc
    _validate_projection(projection)
    return projection


def _validate_projection(projection: dict[str, Any]) -> None:
    if projection.get("schema_version") != 1:
        raise ImproperlyConfigured("Unsupported FAQ content projection schema.")
    source = projection.get("source")
    if source != {
        "repository": FAQ_SOURCE_REPOSITORY,
        "revision": FAQ_SOURCE_REVISION,
        "branch": "main",
        "path": "_questions",
    }:
        raise ImproperlyConfigured(
            "FAQ content projection source does not match the pinned revision."
        )
    courses = projection.get("courses")
    if (
        not isinstance(courses, list)
        or tuple(course.get("slug") for course in courses) != FAQ_COURSE_ORDER
    ):
        raise ImproperlyConfigured("FAQ courses are missing or out of source order.")

    expected_counts = projection.get("counts")
    if not isinstance(expected_counts, dict):
        raise ImproperlyConfigured("FAQ content projection counts are missing.")
    seen_ids: set[str] = set()
    actual_sections = actual_questions = 0
    actual_asset_paths: set[str] = set()
    for course in courses:
        slug = course.get("slug")
        if not isinstance(slug, str) or course.get("public_path") != f"/faq/{slug}.html":
            raise ImproperlyConfigured("FAQ course has an invalid public path.")
        sections = course.get("sections")
        if not isinstance(sections, list):
            raise ImproperlyConfigured("FAQ course sections are missing.")
        section_ids: set[str] = set()
        for section in sections:
            section_id = section.get("id")
            if not isinstance(section_id, str) or section_id in section_ids:
                raise ImproperlyConfigured("FAQ section IDs must be unique within a course.")
            section_ids.add(section_id)
            actual_sections += 1
            questions = section.get("questions")
            if not isinstance(questions, list):
                raise ImproperlyConfigured("FAQ section questions are missing.")
            for question in questions:
                question_id = question.get("id")
                if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
                    raise ImproperlyConfigured("FAQ question IDs must be ten-character strings.")
                if question_id in seen_ids:
                    raise ImproperlyConfigured("FAQ question IDs must be globally unique.")
                seen_ids.add(question_id)
                if question.get("course") != slug or question.get("section_id") != section_id:
                    raise ImproperlyConfigured("FAQ question relationship is inconsistent.")
                if not isinstance(question.get("question"), str) or not isinstance(
                    question.get("answer"), str
                ):
                    raise ImproperlyConfigured("FAQ question and answer must be strings.")
                source_path = question.get("source_path")
                edit_url = question.get("edit_url")
                if not isinstance(source_path, str) or not source_path.startswith(
                    f"_questions/{slug}/"
                ):
                    raise ImproperlyConfigured("FAQ source path is invalid.")
                if not isinstance(edit_url, str) or not edit_url.endswith(source_path):
                    raise ImproperlyConfigured("FAQ edit URL is invalid.")
                actual_questions += 1
                image_ids: set[str] = set()
                for image in question.get("images", []):
                    image_id = image.get("id")
                    image_path = image.get("public_path")
                    if not isinstance(image_id, str) or image_id in image_ids:
                        raise ImproperlyConfigured("FAQ image IDs must be unique per question.")
                    image_ids.add(image_id)
                    if not isinstance(image_path, str) or not image_path.startswith(
                        f"/faq/images/{slug}/"
                    ):
                        raise ImproperlyConfigured("FAQ image public path is invalid.")
                    actual_asset_paths.add(image_path)
    if expected_counts != {
        "courses": len(courses),
        "sections": actual_sections,
        "questions": actual_questions,
        "assets": len(actual_asset_paths),
    }:
        raise ImproperlyConfigured("FAQ content projection counts do not match its records.")


@lru_cache(maxsize=1)
def faq_projection() -> dict[str, Any]:
    return _load_projection()


def faq_courses() -> tuple[dict[str, Any], ...]:
    return tuple(faq_projection()["courses"])


def faq_course(course_slug: str) -> dict[str, Any] | None:
    return next((course for course in faq_courses() if course["slug"] == course_slug), None)


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
        question_links, question_slugs = _faq_question_reference_index(course_slug)
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
