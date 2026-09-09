"""Loading of the repository's ``email_templates/`` source of truth (D1.2a).

Every DTC mail purpose is one markdown file with frontmatter: ``name``,
``subject``, optional ``footer_note`` and a markdown body that is a Django
template. The file name minus ``.md`` is the template key the Relay catalog
knows. The ``import_mail_templates`` deploy step mirrors this directory into
Relay, and the template tests render every purpose from it, so both read
through this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from django.conf import settings

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|[^}]*)?\}\}")

#: Context the package mail app provides to every template on top of the
#: purpose-specific values a send call passes.
BUILT_IN_CONTEXT = ("user_email", "user_name", "site_name", "site_url")


@dataclass(frozen=True)
class MailTemplate:
    key: str
    name: str
    subject: str
    body: str
    footer_note: str
    required_context: tuple[str, ...]

    def draft(self) -> dict:
        """The Relay catalog draft document for this template."""

        return {
            "name": self.name,
            "subject": self.subject,
            "body": self.body,
            "required_context": list(self.required_context),
        }


def template_dir() -> Path:
    return Path(settings.COMMUNITY_BASE["MAIL_TEMPLATE_DIR"])


def load_templates() -> list[MailTemplate]:
    """Every purpose template, ordered by key for stable output."""

    templates = []
    for path in sorted(template_dir().glob("*.md")):
        if path.name == "README.md":
            continue
        post = frontmatter.load(path)
        subject = str(post.metadata.get("subject", ""))
        body = post.content
        templates.append(
            MailTemplate(
                key=path.stem,
                name=str(post.metadata.get("name", path.stem)),
                subject=subject,
                body=body,
                footer_note=str(post.metadata.get("footer_note", "")),
                required_context=required_context(subject, body),
            )
        )
    return templates


def required_context(subject: str, body: str) -> tuple[str, ...]:
    """Template variables the purpose must receive from its send call.

    The package's built-in context (user, site) is excluded: a send never
    passes those, the package always does.
    """

    found = {match.group(1) for match in PLACEHOLDER.finditer(f"{subject}\n{body}")}
    return tuple(sorted(found - set(BUILT_IN_CONTEXT)))
