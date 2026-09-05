from __future__ import annotations

import re
from collections.abc import Callable

# Kramdown's inline target metadata is intentionally treated as a small, closed grammar here.
# The source form uses an ASCII double quote; the second form is what can appear in a value that
# has already passed through HTML escaping.  No other Kramdown attribute spellings are accepted.
_TARGET_ATTRIBUTE = r"\{:[ \t]*target[ \t]*=[ \t]*\"blank\"[ \t]*\}"
_ESCAPED_TARGET_ATTRIBUTE = r"\{:[ \t]*target[ \t]*=[ \t]*&quot;blank&quot;[ \t]*\}"
_TARGET_ATTRIBUTE_RE = re.compile(rf"(?:{_TARGET_ATTRIBUTE}|{_ESCAPED_TARGET_ATTRIBUTE})")
_LEAKED_TARGET_ATTRIBUTE_RE = _TARGET_ATTRIBUTE_RE

# A source Markdown link is kept intact by this helper.  ``_plain_inline`` performs the existing
# label-only projection in the next step; this boundary only removes the presentation metadata.
_MARKDOWN_LINK_WITH_TARGET_ATTRIBUTE_RE = re.compile(
    rf"(\[[^\]\n]+\]\([^\)\n]*\))(?:{_TARGET_ATTRIBUTE}|{_ESCAPED_TARGET_ATTRIBUTE})"
)


def _replace_outside_code_spans(
    value: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
) -> str:
    """Apply a narrow replacement to prose while leaving backtick code spans untouched."""

    # Published bodies are plain text today, but keeping this boundary code-span
    # aware prevents the helper from interpreting a literal example as editorial metadata when it
    # is used by the projection builder in the future.
    pieces: list[str] = []
    position = 0
    while position < len(value):
        opening = value.find("`", position)
        if opening == -1:
            pieces.append(pattern.sub(replacement, value[position:]))
            break
        pieces.append(pattern.sub(replacement, value[position:opening]))
        run_end = opening + 1
        while run_end < len(value) and value[run_end] == "`":
            run_end += 1
        delimiter = value[opening:run_end]
        closing = value.find(delimiter, run_end)
        if closing == -1:
            pieces.append(value[opening:])
            break
        closing_end = closing + len(delimiter)
        pieces.append(value[opening:closing_end])
        position = closing_end
    return "".join(pieces)


def strip_target_attributes_from_links(value: str) -> str:
    """Remove only supported target metadata attached to a Markdown link.

    The link label, destination, surrounding text, and all unrelated attributes remain unchanged.
    This helper is used while building a new projection, before the existing label-only inline
    normalization runs.
    """

    return _replace_outside_code_spans(
        value,
        _MARKDOWN_LINK_WITH_TARGET_ATTRIBUTE_RE,
        r"\1",
    )


def strip_leaked_target_attributes(
    value: str,
    *,
    published_body: bool = False,
) -> str:
    """Remove exact leaked target metadata from an already published body block.

    Article and person body blocks are stored as plain text, so the preceding Markdown link has
    already been reduced to its visible label.  Callers must opt in by declaring the value is one
    of those published blocks; without it, attached literal prose is returned unchanged.  Even
    then, only the exact metadata token is removed; braces, code spans, and unrelated attribute
    forms are preserved.
    """

    if not published_body:
        return value
    return _replace_outside_code_spans(value, _LEAKED_TARGET_ATTRIBUTE_RE, "")


def target_attribute_count(value: str) -> int:
    """Count supported target metadata outside code spans, for the published-body canary."""

    count = 0

    def count_match(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return match.group(0)

    _replace_outside_code_spans(value, _TARGET_ATTRIBUTE_RE, count_match)
    return count
