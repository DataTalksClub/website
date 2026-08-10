from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.template import Context, Template
from django.template.loader import render_to_string

from course_management.datamailer_templates.definitions.registry import TEMPLATES


@dataclass(frozen=True, slots=True)
class RenderedTransactionalEmail:
    template_key: str
    trigger: str
    subject: str
    text: str
    html: str


class _EmailMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.images: list[tuple[str, str | None]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            self._current_href = values.get("href")
            self._current_text = []
        elif tag == "img":
            self.images.append((values.get("src") or "", values.get("alt")))

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            self.links.append((self._current_href, " ".join(self._current_text).strip()))
            self._current_href = None
            self._current_text = []


def _render(source: str, context: dict[str, object]) -> str:
    return Template(source).render(Context(context)).strip()


def render_current_transactional_email(template_key: str) -> RenderedTransactionalEmail:
    """Render the exact current CMP definition with its maintained example context.

    This does not send or create a new message purpose. It gives accessibility checks a complete
    document around the same subject, HTML fragment, text body, and example context that the
    Datamailer upsert command publishes.
    """

    try:
        definition = TEMPLATES[template_key]
    except KeyError as error:
        raise ValueError(f"unknown transactional email template: {template_key}") from error

    context = dict(definition["example_context"])
    missing = [
        item["name"] for item in definition["required_context"] if item["name"] not in context
    ]
    if missing:
        raise ValueError(f"{template_key} example context is missing: {', '.join(missing)}")

    subject = _render(definition["subject"], context)
    html_body = _render(definition["html_body"], context)
    text_body = _render(definition["text_body"], context)
    if not subject or not html_body or not text_body:
        raise ValueError(f"{template_key} rendered an empty subject or body")

    parser = _EmailMarkupParser()
    parser.feed(html_body)
    for href, label in parser.links:
        if urlparse(href).scheme != "https" or not label:
            raise ValueError(f"{template_key} has an unsafe or unnamed link")
        if href not in text_body:
            raise ValueError(f"{template_key} plain text omits HTML link destination: {href}")
    for source, alternative in parser.images:
        if urlparse(source).scheme != "https" or alternative is None:
            raise ValueError(f"{template_key} image needs HTTPS source and an alt attribute")

    html = render_to_string(
        "accessibility/email.html",
        {
            "subject": subject,
            "template_name": definition["name"],
            "trigger": definition["description"],
            "html_body": html_body,
        },
    )
    return RenderedTransactionalEmail(
        template_key=template_key,
        trigger=definition["description"],
        subject=subject,
        text=f"{subject}\n\n{text_body}\n",
        html=html,
    )
