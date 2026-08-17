#!/usr/bin/env python3
"""Recover the legacy article FAQ sections into one checked, provenance-carrying capture.

Ten blog articles ended with a frequently-asked-questions section on the legacy
site.  The questions and answers never lived in the article Markdown: the article
carried only ``{% include faq-accordion.html faqs=site.data.faqs.<key> %}`` and
the pairs themselves lived in ``_data/faqs/<key>.yml`` in the legacy site
repository.  The projected article records therefore stop at the heading, and the
rebuilt page rendered a heading with nothing under it.

This builder joins the two pinned sources back together and writes
``content/article_faq.json``:

* the question/answer pairs are copied verbatim out of the pinned legacy site
  revision, so every word on the page can be diffed against a public commit;
* the insertion point is recomputed with the projection's own block builder from
  the pinned content revision, so the section lands exactly where the legacy page
  put it rather than wherever a reader would guess;
* every input is digested, so a later projection or source change fails loudly at
  load instead of silently moving or stale-rendering a section.

Run it from a clean, pinned pair of checkouts::

    uv run python scripts/build_article_faq.py \\
        --content-root <DataTalksClub/content@pinned> \\
        --legacy-main-root <DataTalksClub/datatalksclub.github.io@pinned>

Nothing here is invented.  An article whose FAQ key has no data file, or whose
data file is empty, is a build failure, never a rendered guess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import django
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# The capture is validated with the same runtime module the page reads it through,
# and that module reaches the shared sanitizer, so the app registry must be up.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.test")
django.setup()

from content.article_faq import (  # noqa: E402
    ARTICLE_FAQ_PATH,
    LEGACY_FAQ_DIRECTORY,
    LEGACY_FAQ_REPOSITORY,
    LEGACY_FAQ_REVISION,
    SCHEMA_VERSION,
    canonical_sha256,
    question_anchor_id,
    validate_article_faq,
)
from scripts.build_public_projection import (  # noqa: E402
    CONTENT_REPOSITORY,
    LEGACY_MAIN_REPOSITORY,
    LEGACY_MAIN_REVISION,
    PREFERRED_CONTENT_REVISION,
    ProjectionBuildError,
    _article_blocks,
    _frontmatter,
    _read_bytes,
    _sha256_bytes,
    _verify_checkout,
)

PROJECTION_ARTICLES_PATH = REPOSITORY_ROOT / "content" / "public_projection" / "articles.json"
PROJECTION_ARTICLES_PUBLIC_PATH = "content/public_projection/articles.json"
DATE_PREFIX = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z0-9][a-z0-9-]*)")
# The one legacy include the article bodies use to place a FAQ accordion.  The
# include names a `_data/faqs/<key>.yml` file; the key is not always the slug.
FAQ_INCLUDE = re.compile(
    r"^\{%\s*include\s+faq-accordion\.html\s+faqs=site\.data\.faqs\.(?P<key>[a-z0-9-]+)\s*%\}$"
)
MAX_QUESTION_CHARACTERS = 500
MAX_ANSWER_CHARACTERS = 5_000


class ArticleFaqBuildError(RuntimeError):
    """One recovered article FAQ input is missing, ambiguous, or unusable."""


def _fail(message: str) -> None:
    raise ArticleFaqBuildError(message)


def _legacy_pairs(path: Path) -> list[dict[str, str]]:
    """Return one legacy FAQ data file as verbatim question/answer pairs."""

    try:
        parsed = yaml.safe_load(_read_bytes(path).decode("utf-8"))
    except (ProjectionBuildError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ArticleFaqBuildError(f"legacy FAQ source unreadable: {path.name}") from error
    if not isinstance(parsed, list) or not parsed:
        _fail(f"legacy FAQ source is not a non-empty list: {path.name}")
    pairs: list[dict[str, str]] = []
    for entry in parsed:
        if not isinstance(entry, dict) or set(entry) != {"question", "answer"}:
            _fail(f"legacy FAQ entry shape rejected: {path.name}")
        question = str(entry["question"]).strip()
        answer = str(entry["answer"]).strip()
        if not question or len(question) > MAX_QUESTION_CHARACTERS:
            _fail(f"legacy FAQ question rejected: {path.name}")
        if not answer or len(answer) > MAX_ANSWER_CHARACTERS:
            _fail(f"legacy FAQ answer rejected: {path.name}")
        pairs.append({"question": question, "answer": answer})
    return pairs


def _article_faq_include(body: str) -> tuple[str, int] | None:
    """Return the FAQ data key and the body line the accordion include sits on."""

    hits = [
        (match.group("key"), index)
        for index, line in enumerate(body.splitlines())
        if (match := FAQ_INCLUDE.match(line.strip())) is not None
    ]
    if not hits:
        return None
    if len(hits) > 1:
        _fail("article carries more than one FAQ accordion include")
    return hits[0]


def _block_index(
    body: str, line_index: int, blocks: list[dict[str, Any]], content_root: Path
) -> int:
    """Return the projected block index the legacy accordion rendered in front of.

    The projection's own article block builder runs over the body above the
    include, so the answer is the projection's count rather than a second
    parser's opinion.  The prefix must be an exact prefix of the projected body,
    otherwise the two sources have drifted apart and the position cannot be
    trusted.
    """

    prefix = "\n".join(body.splitlines()[:line_index])
    prefix_blocks = _article_blocks(prefix, media_root=content_root, counters={})
    index = len(prefix_blocks)
    if blocks[:index] != prefix_blocks:
        _fail("article body prefix does not match the projected blocks")
    if index == 0:
        _fail("article FAQ accordion has no body above it")
    return index


def _heading_id(blocks: list[dict[str, Any]], index: int) -> str:
    """Return the id of the heading the legacy accordion rendered under."""

    for block in reversed(blocks[:index]):
        if block.get("kind") == "heading":
            return str(block["id"])
    _fail("article FAQ accordion has no heading above it")
    raise AssertionError  # pragma: no cover - _fail always raises


def build(arguments: argparse.Namespace) -> dict[str, Any]:
    content_root = arguments.content_root.resolve()
    legacy_main_root = arguments.legacy_main_root.resolve()
    _verify_checkout(
        content_root, CONTENT_REPOSITORY, PREFERRED_CONTENT_REVISION, "preferred content"
    )
    _verify_checkout(legacy_main_root, LEGACY_MAIN_REPOSITORY, LEGACY_MAIN_REVISION, "legacy main")

    projection_bytes = PROJECTION_ARTICLES_PATH.read_bytes()
    records = json.loads(projection_bytes.decode("utf-8"))
    blocks_by_slug = {str(record["slug"]): record["blocks"] for record in records}
    paths_by_slug = {str(record["slug"]): str(record["public_path"]) for record in records}

    articles: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for path in sorted((content_root / "articles").glob("*.md")):
        _, body = _frontmatter(path)
        include = _article_faq_include(body)
        if include is None:
            continue
        key, line_index = include
        match = DATE_PREFIX.fullmatch(path.stem)
        if match is None:
            raise ArticleFaqBuildError(f"article selection key rejected: {path.name}")
        slug = match.group("slug")
        if slug not in blocks_by_slug:
            _fail(f"article is not in the checked projection: {slug}")
        if key in used_keys:
            _fail(f"two articles claim the same FAQ data file: {key}")
        used_keys.add(key)

        source_path = f"{LEGACY_FAQ_DIRECTORY}/{key}.yml"
        legacy_path = legacy_main_root / source_path
        if not legacy_path.is_file():
            _fail(f"legacy FAQ data file is missing: {source_path}")
        pairs = _legacy_pairs(legacy_path)

        blocks = blocks_by_slug[slug]
        index = _block_index(body, line_index, blocks, content_root)
        heading_id = _heading_id(blocks, index)
        heading_ids = {
            str(block["id"])
            for block in blocks
            if block.get("kind") == "heading" and block.get("id")
        }

        questions: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for pair in pairs:
            anchor = question_anchor_id(pair["question"])
            if anchor in seen_ids or anchor in heading_ids:
                _fail(f"article FAQ anchor collides: {slug}#{anchor}")
            seen_ids.add(anchor)
            questions.append({"id": anchor, **pair})

        articles.append(
            {
                "slug": slug,
                "public_path": paths_by_slug[slug],
                "heading_id": heading_id,
                "block_index": index,
                "blocks_sha256": canonical_sha256(blocks),
                "article_source_path": f"articles/{path.name}",
                "article_source_sha256": _sha256_bytes(_read_bytes(path)),
                "source_path": source_path,
                "source_sha256": _sha256_bytes(_read_bytes(legacy_path)),
                "questions": questions,
            }
        )

    articles.sort(key=lambda item: item["slug"])
    capture: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "repository": LEGACY_FAQ_REPOSITORY,
            "revision": LEGACY_FAQ_REVISION,
            "directory": LEGACY_FAQ_DIRECTORY,
        },
        "article_source": {
            "repository": "DataTalksClub/content",
            "revision": PREFERRED_CONTENT_REVISION,
        },
        "projection": {
            "path": PROJECTION_ARTICLES_PUBLIC_PATH,
            "sha256": hashlib.sha256(projection_bytes).hexdigest(),
            "article_count": len(records),
        },
        "counts": {
            "articles": len(articles),
            "questions": sum(len(article["questions"]) for article in articles),
        },
        "articles": articles,
    }
    capture["content_sha256"] = canonical_sha256(
        {key: value for key, value in capture.items() if key != "content_sha256"}
    )
    validate_article_faq(capture)
    return capture


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--content-root", type=Path, required=True)
    result.add_argument("--legacy-main-root", type=Path, required=True)
    result.add_argument("--output", type=Path, default=ARTICLE_FAQ_PATH)
    return result


if __name__ == "__main__":
    parsed = parser().parse_args()
    destination = parsed.output.resolve()
    if not destination.is_relative_to(REPOSITORY_ROOT):
        raise SystemExit("article FAQ capture must stay inside the repository")
    payload = build(parsed)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"{destination.relative_to(REPOSITORY_ROOT)}: "
        f"{payload['counts']['articles']} articles, {payload['counts']['questions']} questions"
    )
