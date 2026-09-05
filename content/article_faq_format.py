"""Shared shape rules for article FAQ pairs, free of Django and renderers.

Article FAQ pairs now live in each article's frontmatter ``faq:`` section in
``DataTalksClub/content`` and travel with the article: the projection builder
attaches them to the projected article record and the content-sync adapter
carries them into the article candidate document, both as plain JSON. No
separate model, no second pipeline.

This module holds only what every one of those readers needs and nothing that
would drag Django, mistune, or bleach into a standalone build script: the
question/answer bounds, the stable anchor derivation, and the pair-list
validator. Rendering answers stays in :mod:`content.article_faq`; capture-level
checks (counts, digests, body binding) stay with its explicit caller.
"""

from __future__ import annotations

import re
from typing import Any

#: Bounds carried over from the legacy recovery capture, so a frontmatter pair
#: that would not have survived recovery fails the same way at ingest.
QUESTION_MAX_CHARACTERS = 500
ANSWER_MAX_CHARACTERS = 5_000
ANCHOR_MAX_CHARACTERS = 80

_ANCHOR = re.compile(r"^faq-[a-z0-9][a-z0-9-]*$")
_PAIR_KEYS = frozenset({"question", "answer"})


class ArticleFaqFormatError(ValueError):
    """One frontmatter FAQ pair list is malformed.

    Carries the pair index, never a question or answer value: pairs are
    editorial content and must not leak into logs or reports.
    """


def faq_anchor_id(question: str) -> str:
    """Return the stable, linkable anchor one question keeps.

    Derived from the question the way projected article headings derive
    theirs, with a ``faq-`` prefix so it can never collide with a heading in
    the same document.
    """

    slug = re.sub(r"[^a-z0-9]+", "-", question.casefold()).strip("-")
    if len(slug) > ANCHOR_MAX_CHARACTERS:
        slug = slug[:ANCHOR_MAX_CHARACTERS].rsplit("-", 1)[0]
    return f"faq-{slug or 'question'}"


def validate_faq_pairs(value: Any) -> tuple[dict[str, str], ...]:
    """Validate one frontmatter ``faq:`` list into stable ``{id, question, answer}`` rows.

    Raises :class:`ArticleFaqFormatError` naming only the offending pair index
    when the value is not a non-empty list of ``{question, answer}`` mappings
    within bounds, or when two questions derive the same anchor.
    """

    if not isinstance(value, list) or not value:
        raise ArticleFaqFormatError("faq_must_be_a_non_empty_list")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or set(raw) != _PAIR_KEYS:
            raise ArticleFaqFormatError(f"faq_pair_shape_invalid:{index}")
        question = raw["question"]
        answer = raw["answer"]
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > QUESTION_MAX_CHARACTERS
            or "\x00" in question
        ):
            raise ArticleFaqFormatError(f"faq_question_invalid:{index}")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > ANSWER_MAX_CHARACTERS
            or "\x00" in answer
        ):
            raise ArticleFaqFormatError(f"faq_answer_invalid:{index}")
        anchor = faq_anchor_id(question)
        if _ANCHOR.fullmatch(anchor) is None or anchor in seen:
            raise ArticleFaqFormatError(f"faq_anchor_invalid:{index}")
        seen.add(anchor)
        normalized.append({"id": anchor, "question": question, "answer": answer})
    return tuple(normalized)
