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
    #: Placeholder names the send may leave empty. Relay rejects an empty
    #: string for any name in ``required_context``, so a key that is
    #: legitimately empty (no start date announced yet, no verify prompt
    #: due) must be declared optional in the template frontmatter instead
    #: of being passed as "". Never sent to Relay: optionality is a
    #: property of the source file, the catalog only stores what is required.
    optional_context: tuple[str, ...] = ()

    def draft(self) -> dict:
        """The Relay catalog draft document for this template.

        Relay's transactional template API stores ``markdown_body`` (it is
        what a send renders) and has no ``body`` key: sending ``body``
        would persist an empty ``markdown_body`` and the stored draft
        would differ forever, republishing on every import run.
        """

        return {
            "name": self.name,
            "subject": self.subject,
            "markdown_body": self.body,
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
        placeholders = _placeholders(subject, body)
        optional = optional_context(post.metadata.get("optional_context", ()), placeholders, path)
        templates.append(
            MailTemplate(
                key=path.stem,
                name=str(post.metadata.get("name", path.stem)),
                subject=subject,
                body=body,
                footer_note=str(post.metadata.get("footer_note", "")),
                required_context=tuple(
                    sorted(placeholders - set(BUILT_IN_CONTEXT) - set(optional))
                ),
                optional_context=optional,
            )
        )
    return templates


def _placeholders(subject: str, body: str) -> set[str]:
    """Every ``{{ name }}`` the subject and body contain, built-ins included."""

    return {match.group(1) for match in PLACEHOLDER.finditer(f"{subject}\n{body}")}


def optional_context(declared, placeholders: set[str], path) -> tuple[str, ...]:
    """The frontmatter ``optional_context`` list, validated against the file.

    An optional name that the template never uses is a typo that would
    silently weaken the catalog, so it fails closed.
    """

    if declared is None:
        return ()
    if isinstance(declared, str):
        declared = [declared]
    try:
        names = [str(name).strip() for name in declared]
    except TypeError as exc:
        raise ValueError(f"{path.name}: optional_context must be a list of names") from exc
    optional = tuple(sorted(name for name in names if name))
    unknown = [name for name in optional if name not in placeholders]
    if unknown:
        raise ValueError(f"{path.name}: optional_context names no placeholder: {unknown}")
    return optional


def required_context(subject: str, body: str) -> tuple[str, ...]:
    """Template variables the purpose must receive from its send call.

    The package's built-in context (user, site) is excluded: a send never
    passes those, the package always does. ``load_templates`` additionally
    subtracts the file's ``optional_context``; this helper reports the raw
    placeholder set so the catalog derivation stays in one place.
    """

    return tuple(sorted(_placeholders(subject, body) - set(BUILT_IN_CONTEXT)))


def published_version(template_key: str) -> int:
    """``MAIL_TEMPLATE_VERSION_RESOLVER``: the catalog version to pin.

    ``import_mail_templates`` records what Relay answered per purpose
    (``core.MailTemplatePublication``); an unknown key keeps the package's
    unresolved default 1 instead of guessing.
    """

    from core.models import MailTemplatePublication

    row = MailTemplatePublication.objects.filter(key=template_key).only("version").first()
    return row.version if row is not None else 1
