from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any, NoReturn, cast
from urllib.parse import quote, urlsplit

import mistune
import yaml
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceStartEvent,
)

from content.inventory import content_route_contracts
from content.podcast_routes import podcast_canonical_path
from content.public_data import public_projection
from content.route_contracts import PublicContract
from content.services import PreparedDocument, sanitize_rendered_html

from .contract import (
    ACCEPTED_BUNDLE_SHA256,
    ACCEPTED_CONTENT_COMMIT,
    ACCEPTED_CONTENT_TREE,
    ACCEPTED_COUNTS,
    ACCEPTED_SOURCE_COUNTS,
    DTC_CONTENT_CONTRACT,
    EDITORIAL_OVERLAY_CREATED,
    EDITORIAL_OVERLAY_ISSUE,
    EDITORIAL_OVERLAY_PATH,
    EDITORIAL_OVERLAY_SHA256,
    EDITORIAL_OVERLAY_TARGETS,
    LEGACY_SOURCE_COMMIT,
    MIGRATION_SHA256,
    ORIGINAL_MIGRATION_COMMIT,
    REPAIR_COMPLETION_REFERENCE,
    REPAIR_MANIFEST_PATH,
    REPAIR_MANIFEST_SHA256,
    REPAIRED_BASELINE_CI_RUN,
    REPAIRED_BASELINE_COMMIT,
    REPAIRED_BASELINE_TREE,
    REPLACEMENT_ATTESTATION_SHA256,
    SOURCE_CI_RUN,
    DtcContentAdapterContract,
)
from .media import validate_media_batch

_ARTICLE_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z0-9][a-z0-9-]*)\.md$")
_SLUG = re.compile(r"^_?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
# Bound by what the database can store, not by the current corpus.
#
# These were bounds read off the shipped projection -- 80 characters for a book
# slug and title -- with a comment saying the one 138-character book slug and
# 143-character title were deliberately excluded as illegitimate. That book is
# published and live at /books/20241118-why-data-science-projects-fail-....html,
# so the guard rejected real content and made the accepted commit unimportable:
# the corpus this adapter exists to ingest cannot pass its own length check.
#
# Whether that title is too long is an editorial question, and the place to
# settle it is the content repository. An importer's job is to refuse what it
# cannot faithfully store, so the bound is ContentDocument.slug (255) and
# ContentDocument.title (512). Anything the column accepts, this accepts.
_BOOK_SLUG_MAX_LENGTH = 255
_BOOK_TITLE_MAX_LENGTH = 512
_PODCAST_SLUG_MAX_LENGTH = 255
_LIQUID_TAG = re.compile(r"{%\s*(.*?)\s*%}")
_INCLUDE = re.compile(r"^include\s+(?P<name>[A-Za-z0-9_./-]+)(?:\s+(?P<args>.*))?$")
_MARKDOWN_URL = re.compile(r"!?\[[^\]]*\]\((?P<url>[^\s)]+)(?:\s+[^)]*)?\)")
_IMAGE_REFERENCE = re.compile(
    r"(?:^|[\"'(=:\s])(?P<path>/?images/(?:posts|podcast|books)/[^\s\"')>]+)"
)
_IMAGE_SOURCE_ATTRIBUTES = frozenset({"src", "data-light-src", "data-dark-src"})
_ALLOWED_IMAGE_ATTRIBUTES = _IMAGE_SOURCE_ATTRIBUTES | frozenset(
    {"alt", "class", "height", "id", "lang", "loading", "style", "title", "width"}
)
_UNSAFE_IMAGE_STYLE = re.compile(
    r"(?:url\s*\(|@import\b|expression\s*\(|-moz-binding\b|javascript\s*:)",
    re.IGNORECASE,
)
_ACCEPTED_REMOTE_IMAGE_OMISSIONS: Mapping[
    str,
    tuple[str, tuple[tuple[str, str], ...]],
] = {
    "articles/2022-10-02-naming-variables-in-machine-learning.md": (
        "c1c13c06e9ccf825bdcb0bf77d0dd32c5da234e19d0238ac1dc24848008c9e17",
        (
            (
                '<img src="https://user-images.githubusercontent.com/34417502/'
                '197341837-5f84a1be-e892-4f5c-92b3-5621bde53cfb.jpg"  />',
                "https://user-images.githubusercontent.com/34417502/"
                "197341837-5f84a1be-e892-4f5c-92b3-5621bde53cfb.jpg",
            ),
            (
                '<img src="https://user-images.githubusercontent.com/34417502/'
                '197341843-73ba78ef-43e5-4ba5-b950-63e380b18bac.jpg"  />',
                "https://user-images.githubusercontent.com/34417502/"
                "197341843-73ba78ef-43e5-4ba5-b950-63e380b18bac.jpg",
            ),
        ),
    ),
    "articles/2025-02-26-building-ai-agent-that-thrives-in-real-world.md": (
        "f7064f7353a88de23568b253af098382f898cbf2f2103d67fec5e1944c07079e",
        (
            (
                '<img src="https://s3.gifyu.com/images/bSoO6.gif"  />',
                "https://s3.gifyu.com/images/bSoO6.gif",
            ),
            (
                '<img src="https://s3.gifyu.com/images/bSoO4.gif"  alt="Dashboard spans" />',
                "https://s3.gifyu.com/images/bSoO4.gif",
            ),
            (
                '<img src="https://s3.gifyu.com/images/bSoOg.gif"  />',
                "https://s3.gifyu.com/images/bSoOg.gif",
            ),
        ),
    ),
}
_SAFE_PERSON_KEY = re.compile(r"^[^/\\?#\x00-\x1f\x7f]{1,255}$")
_ALLOWED_MEDIA_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg"})
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_OVERLAY_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "issue",
        "created",
        "baseline_content_commit",
        "source",
        "migration",
        "field",
        "target_count",
        "targets",
    }
)
_OVERLAY_SOURCE_KEYS = frozenset({"repository", "commit"})
_OVERLAY_MIGRATION_KEYS = frozenset({"manifest", "sha256"})
_OVERLAY_TARGET_KEYS = frozenset({"path", "key", "description_sha256", "target_sha256"})
_EXPECTED_MIGRATION = {
    "schema_version": 1,
    "migration_date": "2026-08-09",
    "source": {
        "repository": "https://github.com/DataTalksClub/datatalksclub.github.io",
        "revision": LEGACY_SOURCE_COMMIT,
    },
    "counts": {
        "articles": 55,
        "podcasts": 205,
        "podcast_transcripts": 203,
        "books": 98,
    },
    "rules": {
        "articles": "copied byte-for-byte with YAML front matter and Markdown body",
        "podcasts": "converted to YAML; transcript arrays moved to separate YAML files",
        "books": "converted to YAML; Markdown body moved to the summary field",
        "templates": "legacy _template.md files excluded",
        "images": "copied byte-for-byte at legacy relative paths",
    },
}
_MARKDOWN = mistune.create_markdown(
    escape=False,
    plugins=("strikethrough", "table"),
)


@dataclass(frozen=True, slots=True)
class DtcContentDiagnostic:
    code: str
    source_path: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "source_path": self.source_path}


class DtcContentValidationError(ValueError):
    """A bounded diagnostic safe for release and operator surfaces."""

    def __init__(self, code: str, source_path: str = ".") -> None:
        safe_path = source_path[:1024] if source_path else "."
        self.diagnostics = (DtcContentDiagnostic(code=code, source_path=safe_path),)
        super().__init__(f"{safe_path}: {code}")


@dataclass(frozen=True, slots=True)
class CandidateRelation:
    source_kind: str
    source_key: str
    relation_type: str
    target_kind: str
    target_key: str
    order: int
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class CandidateAsset:
    source_path: str
    stable_public_path: str
    content_type: str
    size: int
    checksum: str
    contract_id: str | None
    contract_source_id: str | None
    contract_source_revision: str | None


@dataclass(frozen=True, slots=True)
class CandidateBundle:
    source_stable_id: str
    repository: str
    branch: str
    commit_sha: str
    source_tree_sha: str
    adapter_type: str
    schema_version: int
    parser_version: str
    rendering_version: str
    migration_sha256: str
    migration: Mapping[str, Any]
    original_migration_commit: str
    repaired_baseline_commit: str
    repaired_baseline_tree: str
    repaired_baseline_ci_run: str
    repair_manifest_path: str
    repair_manifest_sha256: str
    repair_manifest: Mapping[str, Any] | None
    replacement_attestation_sha256: str
    repair_completion_reference: str
    editorial_overlay_path: str
    editorial_overlay_sha256: str
    editorial_overlay: Mapping[str, Any] | None
    editorial_overlay_issue: str
    source_ci_run: str
    public_contracts_sha256: str
    documents: tuple[PreparedDocument, ...]
    relations: tuple[CandidateRelation, ...]
    assets: tuple[CandidateAsset, ...]
    referenced_asset_paths: tuple[str, ...]
    counts: Mapping[str, int]
    bundle_sha256: str


@dataclass(frozen=True, slots=True)
class _PreflightContent:
    referenced_assets: frozenset[str]
    article_parts: Mapping[str, tuple[dict[str, Any], str]]
    structured_documents: Mapping[str, dict[str, Any]]


class _UnsafeHtmlInspector(HTMLParser):
    _UNSAFE_TAGS = frozenset({"embed", "iframe", "object", "script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.diagnostic_code: str | None = None

    def _reject(self, code: str) -> None:
        if self.diagnostic_code is None:
            self.diagnostic_code = code

    def _inspect_image(self, attrs: list[tuple[str, str | None]]) -> None:
        lowered = [(name.lower(), value) for name, value in attrs]
        names = [name for name, _value in lowered]
        if len(names) != len(set(names)) or any(
            name not in _ALLOWED_IMAGE_ATTRIBUTES for name in names
        ):
            self._reject("unsafe_image_attribute")
            return
        sources = {name: value for name, value in lowered if name in _IMAGE_SOURCE_ATTRIBUTES}
        if "src" not in sources or any(
            value is None or not _safe_local_image_url(value) for value in sources.values()
        ):
            self._reject("unsafe_image_reference")
            return
        style = next((value for name, value in lowered if name == "style"), None)
        if style is not None and (
            any(ord(character) < 0x20 for character in style)
            or "\\" in style
            or "/*" in style
            or "*/" in style
            or _UNSAFE_IMAGE_STYLE.search(style) is not None
        ):
            self._reject("unsafe_image_attribute")

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered_tag = tag.lower()
        if lowered_tag in self._UNSAFE_TAGS:
            self._reject("unsafe_rendered_html")
        if lowered_tag == "img":
            self._inspect_image(attrs)
        for name, value in attrs:
            lowered = name.lower()
            if lowered.startswith("on"):
                self._reject(
                    "unsafe_image_attribute" if lowered_tag == "img" else "unsafe_rendered_html"
                )
            if lowered in {"href", "src", "xlink:href"} and value is not None:
                if not _safe_content_url(value):
                    self._reject("unsafe_rendered_html")


def _fail(code: str, path: str | Path = ".") -> NoReturn:
    normalized = path.as_posix() if isinstance(path, Path) else path
    raise DtcContentValidationError(code, normalized)


def _safe_content_url(value: str) -> bool:
    if not value or any(ord(character) < 0x20 for character in value):
        return False
    if value.startswith("//"):
        return False
    parts = urlsplit(value)
    if parts.scheme:
        return parts.scheme.lower() in {"http", "https", "mailto", "tel"}
    return parts.netloc == ""


def _safe_local_image_url(value: str) -> bool:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() or ord(character) < 0x20 for character in value)
    ):
        return False
    relative = value.removeprefix("/")
    pure = PurePosixPath(relative)
    return (
        relative == pure.as_posix()
        and any(
            relative.startswith(prefix)
            for prefix in ("images/posts/", "images/podcast/", "images/books/")
        )
        and pure.suffix.lower() in _ALLOWED_MEDIA_SUFFIXES
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )


def _accepted_article_render_source(
    raw_body: str,
    *,
    source_path: str,
    source_checksum: str,
    commit_sha: str,
) -> tuple[str, tuple[str, ...]]:
    evidence = _ACCEPTED_REMOTE_IMAGE_OMISSIONS.get(source_path)
    if commit_sha != ACCEPTED_CONTENT_COMMIT or evidence is None:
        return raw_body, ()
    expected_checksum, omissions = evidence
    if source_checksum != expected_checksum:
        _fail("accepted_remote_image_evidence_mismatch", source_path)
    render_source = raw_body
    omitted_urls: list[str] = []
    for exact_tag, remote_url in omissions:
        if render_source.count(exact_tag) != 1:
            _fail("accepted_remote_image_evidence_mismatch", source_path)
        render_source = render_source.replace(exact_tag, "", 1)
        omitted_urls.append(remote_url)
    return render_source, tuple(omitted_urls)


def _json_value(value: Any, *, path: str, depth: int = 0) -> Any:
    if depth > DTC_CONTENT_CONTRACT.max_yaml_depth:
        _fail("structured_depth_exceeded", path)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("non_finite_number", path)
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item, path=path, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                _fail("invalid_mapping_key", path)
            result[key] = _json_value(item, path=path, depth=depth + 1)
        return result
    _fail("unsupported_yaml_value", path)


def _load_yaml_mapping(
    raw_text: str,
    *,
    path: str,
    contract: DtcContentAdapterContract,
) -> dict[str, Any]:
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(raw_text, Loader=yaml.CSafeLoader):
            if isinstance(event, AliasEvent):
                raise yaml.YAMLError("aliases are not supported")
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                nodes += 1
                if depth > contract.max_yaml_depth:
                    raise yaml.YAMLError("maximum depth exceeded")
            elif isinstance(event, ScalarEvent):
                nodes += 1
                if depth + 1 > contract.max_yaml_depth:
                    raise yaml.YAMLError("maximum depth exceeded")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
            if nodes > contract.max_yaml_nodes:
                raise yaml.YAMLError("maximum node count exceeded")
        value = yaml.load(raw_text, Loader=yaml.CSafeLoader)
    except yaml.YAMLError:
        _fail("invalid_or_unsafe_yaml", path)
    if not isinstance(value, dict):
        _fail("yaml_mapping_required", path)
    normalized = _json_value(value, path=path)
    if not isinstance(normalized, dict):
        _fail("yaml_mapping_required", path)
    return normalized


def _read_file(path: Path, *, relative: str, contract: DtcContentAdapterContract) -> bytes:
    try:
        mode = path.lstat().st_mode
    except OSError:
        _fail("file_unreadable", relative)
    if stat.S_ISLNK(mode):
        _fail("symlink_not_allowed", relative)
    if not stat.S_ISREG(mode):
        _fail("regular_file_required", relative)
    try:
        size = path.stat().st_size
    except OSError:
        _fail("file_unreadable", relative)
    if size > contract.max_file_bytes:
        _fail("file_size_limit_exceeded", relative)
    try:
        return path.read_bytes()
    except OSError:
        _fail("file_unreadable", relative)


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        _fail("path_outside_checkout")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in relative
        or any(ord(character) < 0x20 for character in relative)
    ):
        _fail("unsafe_source_path")
    return relative


def _walk_directory(root: Path, directory: Path) -> tuple[Path, ...]:
    relative_directory = _safe_relative(directory, root)
    try:
        mode = directory.lstat().st_mode
    except OSError:
        _fail("required_directory_missing", relative_directory)
    if stat.S_ISLNK(mode):
        _fail("symlink_not_allowed", relative_directory)
    if not stat.S_ISDIR(mode):
        _fail("required_directory_missing", relative_directory)
    result: list[Path] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        _fail("directory_unreadable", relative_directory)
    for entry in entries:
        path = Path(entry.path)
        relative = _safe_relative(path, root)
        if entry.is_symlink():
            _fail("symlink_not_allowed", relative)
        if entry.is_dir(follow_symlinks=False):
            result.extend(_walk_directory(root, path))
        elif entry.is_file(follow_symlinks=False):
            result.append(path)
        else:
            _fail("regular_file_required", relative)
    return tuple(result)


def _is_draft(path: Path) -> bool:
    """A leading underscore marks an unpublished draft.

    This is the source repository's own convention, inherited from the Jekyll site
    that produced it: a file whose name starts with ``_`` is never rendered, so it
    has no public URL and no legacy route contract -- and cannot acquire one, since
    the crawler that built the route inventory only ever saw published pages.

    ``scripts/build_public_projection.py`` already applies exactly this rule.  The
    adapter used to ingest drafts anyway and then fail closed on the missing
    contract, which is how ``podcasts/_s12e08.yaml`` made a whole checkout
    unimportable.  One rule for drafts, in both builders.
    """

    return path.name.startswith("_")


def _collect_paths(root: Path) -> dict[str, tuple[Path, ...]]:
    articles = _walk_directory(root, root / "articles")
    podcasts_all = _walk_directory(root, root / "podcasts")
    books = _walk_directory(root, root / "books")
    media: list[Path] = []
    for name in ("posts", "podcast", "books"):
        media.extend(_walk_directory(root, root / "images" / name))

    # A draft is still validated for shape and safety -- it must sit where its kind
    # belongs and carry the right suffix -- and is then dropped rather than ingested.
    article_files: list[Path] = []
    for path in articles:
        relative = _safe_relative(path, root)
        if path.parent != root / "articles" or path.suffix != ".md":
            _fail("unsupported_content_path", relative)
        if _is_draft(path):
            continue
        article_files.append(path)

    podcast_files: list[Path] = []
    transcript_files: list[Path] = []
    for path in podcasts_all:
        relative = _safe_relative(path, root)
        if path.suffix != ".yaml":
            _fail("unsupported_content_path", relative)
        if path.parent == root / "podcasts":
            if not _is_draft(path):
                podcast_files.append(path)
        elif path.parent == root / "podcasts" / "transcripts":
            if not _is_draft(path):
                transcript_files.append(path)
        else:
            _fail("unsupported_content_path", relative)

    book_files: list[Path] = []
    for path in books:
        relative = _safe_relative(path, root)
        if path.parent != root / "books" or path.suffix != ".yaml":
            _fail("unsupported_content_path", relative)
        if _is_draft(path):
            continue
        book_files.append(path)

    for path in media:
        relative = _safe_relative(path, root)
        if path.suffix.lower() not in _ALLOWED_MEDIA_SUFFIXES:
            _fail("unsupported_media_type", relative)

    return {
        "articles": tuple(sorted(article_files)),
        "podcasts": tuple(sorted(podcast_files)),
        "podcast_transcripts": tuple(sorted(transcript_files)),
        "books": tuple(sorted(book_files)),
        "media": tuple(sorted(media)),
    }


def _checked_contracts() -> tuple[dict[str, PublicContract], str, frozenset[str], frozenset[str]]:
    contracts = content_route_contracts()
    index = {contract.percent_encoded_public_reference: contract for contract in contracts}
    from content.models import PUBLIC_CONTRACT_DIGEST

    projection = public_projection()
    adopted_paths = {
        quote(str(record["public_path"]), safe="/")
        for collection in ("articles", "podcasts", "books")
        for record in projection[collection]
    }
    # Adoption is a statement about the public path -- "the projection publishes
    # this, so a missing legacy route contract means the crawl never saw it, not
    # that we invented it". It is not a statement about which revision produced
    # the bytes; those are verified per record against provenance.checksum.
    #
    # Pinning it to ACCEPTED_CONTENT_COMMIT coupled two pins that move
    # independently, and they have moved: the checked projection was rebuilt
    # against a later content revision, so this matched nothing and every
    # projection-published asset the legacy crawl missed failed closed. There is
    # one such asset today -- images/books/20241104-llm-engineer-s-handbook/preview.jpg,
    # published and served, whose sibling cover.jpg was crawled and it was not.
    adopted_paths.update(
        quote(str(record["public_path"]), safe="/")
        for record in projection["media"]
        if record.get("provenance", {}).get("repository") == "DataTalksClub/content"
    )
    approved_person_keys = frozenset(str(key) for key in projection["people_by_slug"])
    return index, PUBLIC_CONTRACT_DIGEST, frozenset(adopted_paths), approved_person_keys


def _contract_for(
    public_path: str,
    *,
    path: str,
    index: Mapping[str, PublicContract],
    expect_asset: bool,
    allow_unobserved: bool,
) -> PublicContract | None:
    contract = index.get(public_path)
    if contract is None:
        if allow_unobserved:
            return None
        _fail("legacy_contract_missing", path)
    if (contract.contract_kind == "asset") != expect_asset:
        _fail("legacy_contract_kind_mismatch", path)
    return contract


def _contract_provenance(
    contract: PublicContract | None,
) -> tuple[str | None, str | None, str | None]:
    if contract is None:
        return None, None, None
    return contract.contract_id, contract.source_id, contract.source_revision


def _source_urls(source_path: str, commit_sha: str) -> tuple[str, str]:
    encoded = quote(source_path, safe="/")
    edit = f"https://github.com/DataTalksClub/content/edit/main/{encoded}"
    immutable = f"https://github.com/DataTalksClub/content/blob/{commit_sha}/{encoded}"
    return edit, immutable


def _public_asset_url(source_path: str) -> str:
    return f"https://datatalks.club/{quote(source_path.lstrip('/'), safe='/')}"


def _render_markdown(raw: str, *, path: str) -> tuple[str, tuple[str, ...]]:
    extensions: list[str] = []
    inside_raw = False

    def replace(match: re.Match[str]) -> str:
        nonlocal inside_raw
        value = match.group(1).strip()
        if value == "raw":
            if inside_raw:
                _fail("nested_liquid_raw_block", path)
            inside_raw = True
            return ""
        if value == "endraw":
            if not inside_raw:
                _fail("unmatched_liquid_raw_end", path)
            inside_raw = False
            return ""
        if inside_raw:
            return match.group(0)
        include = _INCLUDE.fullmatch(value)
        if include is None:
            _fail("unsupported_liquid_tag", path)
        name = include.group("name")
        allowed = (
            name in {"youtube.html", "anchor.html", "faq-accordion.html", "related-posts.html"}
            or name.startswith("course-structured-data/")
            or name == "sponsor-structured-data.html"
        )
        if not allowed:
            _fail("unsupported_liquid_include", path)
        extensions.append(value)
        label = html.escape(name.removesuffix(".html").replace("-", " ").replace("/", ": "))
        return f'\n\n<p class="content-extension">{label}</p>\n\n'

    expanded = _LIQUID_TAG.sub(replace, raw)
    if inside_raw:
        _fail("unclosed_liquid_raw_block", path)
    rendered = str(_MARKDOWN(expanded))
    inspector = _UnsafeHtmlInspector()
    inspector.feed(rendered)
    if inspector.diagnostic_code is not None:
        _fail(inspector.diagnostic_code, path)
    sanitized = sanitize_rendered_html("article", rendered)
    return sanitized, tuple(extensions)


def _render_text_fragment(value: str, *, path: str) -> str:
    for match in _MARKDOWN_URL.finditer(value):
        if not _safe_content_url(match.group("url")):
            _fail("unsafe_url", path)
    rendered = str(_MARKDOWN(value))
    inspector = _UnsafeHtmlInspector()
    inspector.feed(rendered)
    if inspector.diagnostic_code is not None:
        _fail(inspector.diagnostic_code, path)
    return rendered


def _render_text(value: str, *, kind: str, path: str) -> str:
    rendered = _render_text_fragment(value, path=path)
    return sanitize_rendered_html(kind, rendered)


def _validate_url(value: Any, *, path: str) -> None:
    if not isinstance(value, str) or not _safe_content_url(value):
        _fail("unsafe_url", path)


def _validate_episode_links(metadata: Mapping[str, Any], *, path: str) -> None:
    links = metadata.get("links", {})
    if not isinstance(links, dict):
        _fail("episode_links_mapping_required", path)
    for value in links.values():
        _validate_url(value, path=path)
    resources = metadata.get("resources", [])
    if not isinstance(resources, list):
        _fail("episode_resources_list_required", path)
    for item in resources:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            _fail("episode_resource_mapping_invalid", path)
        _validate_url(item.get("url"), path=path)
    clips = metadata.get("quotableClips", [])
    if not isinstance(clips, list):
        _fail("episode_clips_list_required", path)
    for item in clips:
        if not isinstance(item, dict):
            _fail("episode_clip_mapping_invalid", path)
        _validate_url(item.get("url"), path=path)


def _validate_book_archive(value: Any, *, path: str, depth: int = 0) -> None:
    if not isinstance(value, list):
        _fail("book_archive_list_required", path)
    if depth > 8:
        _fail("book_archive_depth_exceeded", path)
    for item in value:
        if not isinstance(item, dict):
            _fail("book_archive_entry_invalid", path)
        if not isinstance(item.get("name"), str) or not isinstance(item.get("text"), str):
            _fail("book_archive_entry_invalid", path)
        replies = item.get("replies", [])
        _validate_book_archive(replies, path=path, depth=depth + 1)


def _render_book(metadata: Mapping[str, Any], *, path: str) -> str:
    summary = metadata.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        _fail("book_summary_required", path)
    parts = [_render_text_fragment(summary, path=path)]
    archive = metadata.get("archive", [])
    _validate_book_archive(archive, path=path)
    if archive:
        parts.append('<section class="book-discussion"><h2>Discussion archive</h2>')
        for item in archive:
            parts.append(f"<article><h3>{html.escape(item['name'])}</h3>")
            parts.append(_render_text_fragment(item["text"], path=path))
            replies = item.get("replies", [])
            if replies:
                parts.append('<div class="book-replies">')
                for reply in replies:
                    parts.append(f"<h4>{html.escape(reply['name'])}</h4>")
                    parts.append(_render_text_fragment(reply["text"], path=path))
                parts.append("</div>")
            parts.append("</article>")
        parts.append("</section>")
    return sanitize_rendered_html("book", "".join(parts))


def _render_transcript(segments: Sequence[Mapping[str, Any]], *, path: str) -> str:
    # The content sanitizer historically stripped the outer ``section`` while
    # retaining this fixed safe inner fragment. Build that canonical result
    # directly so large transcripts do not need an HTML5 parse/serialize pass.
    parts = ["<h2>Transcript</h2>"]
    for segment in segments:
        header = segment.get("header")
        line = segment.get("line")
        if header is not None:
            if not isinstance(header, str) or not header.strip():
                _fail("transcript_header_invalid", path)
            parts.append(f"<h3>{html.escape(header)}</h3>")
        elif line is not None:
            if not isinstance(line, str) or not line.strip():
                _fail("transcript_line_invalid", path)
            who = segment.get("who", "")
            if who is not None and not isinstance(who, str):
                _fail("transcript_speaker_invalid", path)
            prefix = f"<strong>{html.escape(who)}:</strong> " if who else ""
            parts.append(f"<p>{prefix}{html.escape(line)}</p>")
        else:
            _fail("transcript_segment_content_required", path)
    # Every source value is escaped above and every element/attribute is code-owned.
    # Unlike Markdown-backed content, this fixed fragment needs no HTML reparsing.
    return "".join(parts)


def _article_parts(text: str, *, path: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        _fail("article_frontmatter_required", path)
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), -1)
    if closing < 0:
        _fail("article_frontmatter_unclosed", path)
    frontmatter = _load_yaml_mapping(
        "".join(lines[1:closing]),
        path=path,
        contract=DTC_CONTENT_CONTRACT,
    )
    body = "".join(lines[closing + 1 :])
    if not body.strip():
        _fail("article_body_empty", path)
    return frontmatter, body


def _source_text(value: Any, *, field: str, path: str, required: bool) -> str:
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    if (
        isinstance(value, dict)
        and len(value) == 1
        and all(isinstance(item, str) for pair in value.items() for item in pair)
    ):
        key, item = next(iter(value.items()))
        value = f"{key}: {item}"
    if not isinstance(value, str):
        if not required and value is None:
            return ""
        _fail(f"{field}_required", path)
    result = value.strip()
    if required and not result:
        _fail(f"{field}_required", path)
    return result


def _required_text(metadata: Mapping[str, Any], key: str, *, path: str) -> str:
    return _source_text(metadata.get(key), field=key, path=path, required=True)


def _optional_text(metadata: Mapping[str, Any], key: str, *, path: str) -> str:
    return _source_text(metadata.get(key), field=key, path=path, required=False)


def _required_string(metadata: Mapping[str, Any], key: str, *, path: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{key}_required", path)
    return value.strip()


def _overlay_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        _fail("editorial_overlay_schema_invalid", EDITORIAL_OVERLAY_PATH)
    return result


def _overlay_exact_keys(value: Mapping[str, Any], expected: frozenset[str]) -> None:
    if set(value) != expected:
        _fail("editorial_overlay_schema_invalid", EDITORIAL_OVERLAY_PATH)


def _overlay_target_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("editorial_overlay_target_path_invalid", EDITORIAL_OVERLAY_PATH)
    pure = PurePosixPath(value)
    if (
        len(pure.parts) != 2
        or pure.parts[0] != "podcasts"
        or pure.parts[1] in {"", ".", ".."}
        or pure.suffix != ".yaml"
        or value != pure.as_posix()
    ):
        _fail("editorial_overlay_target_path_invalid", EDITORIAL_OVERLAY_PATH)
    return value


def _validate_editorial_overlay(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    contract: DtcContentAdapterContract,
) -> Mapping[str, str]:
    _overlay_exact_keys(manifest, _OVERLAY_TOP_LEVEL_KEYS)
    source = _overlay_mapping(manifest, "source")
    migration = _overlay_mapping(manifest, "migration")
    _overlay_exact_keys(source, _OVERLAY_SOURCE_KEYS)
    _overlay_exact_keys(migration, _OVERLAY_MIGRATION_KEYS)
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "podcast_description_editorial_overlay"
        or manifest.get("issue") != EDITORIAL_OVERLAY_ISSUE
        or manifest.get("created") != EDITORIAL_OVERLAY_CREATED
        or manifest.get("baseline_content_commit") != REPAIRED_BASELINE_COMMIT
        or source
        != {
            "repository": "https://github.com/DataTalksClub/datatalksclub.github.io",
            "commit": LEGACY_SOURCE_COMMIT,
        }
        or migration != {"manifest": "migration.yaml", "sha256": MIGRATION_SHA256}
        or manifest.get("field") != "description"
        or type(manifest.get("target_count")) is not int
        or manifest.get("target_count") != len(EDITORIAL_OVERLAY_TARGETS)
    ):
        _fail("editorial_overlay_contract_invalid", EDITORIAL_OVERLAY_PATH)
    targets = manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != len(EDITORIAL_OVERLAY_TARGETS):
        _fail("editorial_overlay_target_count_invalid", EDITORIAL_OVERLAY_PATH)

    descriptions: dict[str, str] = {}
    ordered_paths: list[str] = []
    for row in targets:
        if not isinstance(row, dict):
            _fail("editorial_overlay_target_invalid", EDITORIAL_OVERLAY_PATH)
        _overlay_exact_keys(row, _OVERLAY_TARGET_KEYS)
        relative = _overlay_target_path(row.get("path"))
        if relative in descriptions:
            _fail("editorial_overlay_target_duplicate", EDITORIAL_OVERLAY_PATH)
        if row.get("key") != "description":
            _fail("editorial_overlay_target_field_invalid", relative)
        description_sha256 = row.get("description_sha256")
        target_sha256 = row.get("target_sha256")
        if (
            not isinstance(description_sha256, str)
            or _HEX_64.fullmatch(description_sha256) is None
            or not isinstance(target_sha256, str)
            or _HEX_64.fullmatch(target_sha256) is None
        ):
            _fail("editorial_overlay_target_digest_invalid", relative)
        target_bytes = _read_file(root / relative, relative=relative, contract=contract)
        try:
            target_text = target_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("editorial_overlay_target_utf8_required", relative)
        metadata = _load_yaml_mapping(target_text, path=relative, contract=contract)
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            _fail("editorial_overlay_description_invalid", relative)
        if hashlib.sha256(description.encode("utf-8")).hexdigest() != description_sha256:
            _fail("editorial_overlay_description_digest_mismatch", relative)
        if hashlib.sha256(target_bytes).hexdigest() != target_sha256:
            _fail("editorial_overlay_target_digest_mismatch", relative)
        descriptions[relative] = description
        ordered_paths.append(relative)

    if tuple(ordered_paths) != EDITORIAL_OVERLAY_TARGETS:
        _fail("editorial_overlay_target_order_invalid", EDITORIAL_OVERLAY_PATH)
    return descriptions


def _required_list(metadata: Mapping[str, Any], key: str, *, path: str) -> list[Any]:
    value = metadata.get(key)
    if not isinstance(value, list):
        _fail(f"{key}_list_required", path)
    return value


def _source_datetime(value: Any, *, path: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            _fail("source_datetime_invalid", path)
    else:
        _fail("source_datetime_invalid", path)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _person_relations(
    *,
    source_kind: str,
    source_key: str,
    relation_type: str,
    values: Sequence[Any],
    path: str,
) -> list[CandidateRelation]:
    result: list[CandidateRelation] = []
    for order, value in enumerate(values):
        if not isinstance(value, str) or _SAFE_PERSON_KEY.fullmatch(value) is None:
            _fail("person_key_invalid", path)
        result.append(
            CandidateRelation(
                source_kind=source_kind,
                source_key=source_key,
                relation_type=relation_type,
                target_kind="person",
                target_key=value,
                order=order,
            )
        )
    return result


def _asset_references(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str):
        scalar = value.strip().lstrip("/")
        if (
            scalar.startswith(("images/posts/", "images/podcast/", "images/books/"))
            and PurePosixPath(scalar).suffix.lower() in _ALLOWED_MEDIA_SUFFIXES
        ):
            pure = PurePosixPath(scalar)
            if any(part in {"", ".", ".."} for part in pure.parts):
                _fail("asset_reference_traversal")
            return {scalar}
        for match in _IMAGE_REFERENCE.finditer(value):
            candidate = match.group("path").lstrip("/").rstrip(".,;:")
            pure = PurePosixPath(candidate)
            if any(part in {"", ".", ".."} for part in pure.parts):
                _fail("asset_reference_traversal")
            result.add(candidate)
    elif isinstance(value, list):
        for item in value:
            result.update(_asset_references(item))
    elif isinstance(value, dict):
        for item in value.values():
            result.update(_asset_references(item))
    return result


def _preflight_asset_references(
    paths: Mapping[str, Sequence[Path]],
    raw_files: Mapping[str, bytes],
    *,
    root: Path,
    contract: DtcContentAdapterContract,
) -> _PreflightContent:
    """Reject incomplete source media before document-field readiness checks."""

    references: set[str] = set()
    article_parts: dict[str, tuple[dict[str, Any], str]] = {}
    structured_documents: dict[str, dict[str, Any]] = {}
    for path in paths["articles"]:
        source_path = _safe_relative(path, root)
        try:
            source_text = raw_files[source_path].decode("utf-8")
        except UnicodeDecodeError:
            _fail("content_utf8_required", source_path)
        metadata, body = _article_parts(source_text, path=source_path)
        article_parts[source_path] = (metadata, body)
        references.update(_asset_references(metadata))
        references.update(_asset_references(body))
    for collection in ("podcasts", "books"):
        for path in paths[collection]:
            source_path = _safe_relative(path, root)
            try:
                source_text = raw_files[source_path].decode("utf-8")
            except UnicodeDecodeError:
                _fail("content_utf8_required", source_path)
            metadata = _load_yaml_mapping(source_text, path=source_path, contract=contract)
            structured_documents[source_path] = metadata
            references.update(_asset_references(metadata))

    media_paths = {_safe_relative(path, root) for path in paths["media"]}
    missing = sorted(references - media_paths)
    if missing:
        _fail("referenced_asset_missing", missing[0])
    return _PreflightContent(
        referenced_assets=frozenset(references),
        article_parts=article_parts,
        structured_documents=structured_documents,
    )


def _document_metadata(
    *,
    immutable_url: str,
    legacy_path: str | None,
    migration_sha256: str,
    extensions: Sequence[str] = (),
    omitted_remote_images: Sequence[str] = (),
    publication_state: str = "published",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "adapter_schema_version": DTC_CONTENT_CONTRACT.schema_version,
        "immutable_source_url": immutable_url,
        "legacy_path": legacy_path,
        "migration_sha256": migration_sha256,
        "publication_state": publication_state,
        "render_extensions": list(extensions),
        "source_repository": DTC_CONTENT_CONTRACT.repository_https_url,
    }
    if omitted_remote_images:
        result["omitted_remote_images"] = list(omitted_remote_images)
    return result


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _bundle_digest(
    *,
    commit_sha: str,
    source_evidence: Mapping[str, Any],
    documents: Sequence[PreparedDocument],
    relations: Sequence[CandidateRelation],
    assets: Sequence[CandidateAsset],
) -> str:
    payload = {
        "assets": [asdict(asset) for asset in assets],
        "commit_sha": commit_sha,
        "documents": [asdict(document) for document in documents],
        "relations": [asdict(relation) for relation in relations],
        "schema_version": DTC_CONTENT_CONTRACT.schema_version,
        "source_evidence": source_evidence,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def adapt_dtc_content_checkout(
    checkout_root: Path,
    *,
    commit_sha: str,
    source_tree_sha: str = "",
    contract: DtcContentAdapterContract = DTC_CONTENT_CONTRACT,
    clock: Callable[[], float] = monotonic,
) -> CandidateBundle:
    """Parse an already verified immutable checkout without network or database work."""

    try:
        contract.validate_commit(commit_sha)
    except ValueError:
        _fail("source_commit_invalid")
    root = Path(checkout_root)
    try:
        root_mode = root.lstat().st_mode
    except OSError:
        _fail("checkout_unreadable")
    if stat.S_ISLNK(root_mode):
        _fail("checkout_symlink_not_allowed")
    if not stat.S_ISDIR(root_mode):
        _fail("checkout_directory_required")
    root = root.absolute()
    started_at = clock()

    if source_tree_sha and re.fullmatch(r"[0-9a-f]{40}", source_tree_sha) is None:
        _fail("source_tree_invalid")
    if commit_sha == ACCEPTED_CONTENT_COMMIT and source_tree_sha != ACCEPTED_CONTENT_TREE:
        _fail("accepted_source_tree_mismatch")

    migration_path = root / "migration.yaml"
    migration_bytes = _read_file(
        migration_path,
        relative="migration.yaml",
        contract=contract,
    )
    if hashlib.sha256(migration_bytes).hexdigest() != MIGRATION_SHA256:
        _fail("migration_provenance_tampered", "migration.yaml")
    try:
        migration_text = migration_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _fail("migration_provenance_invalid", "migration.yaml")
    migration = _load_yaml_mapping(
        migration_text,
        path="migration.yaml",
        contract=contract,
    )
    if migration != _EXPECTED_MIGRATION:
        _fail("migration_provenance_invalid", "migration.yaml")

    repair_manifest: Mapping[str, Any] | None = None
    repaired_baseline_commit = ""
    repaired_baseline_tree = ""
    repaired_baseline_ci_run = ""
    repair_manifest_path = ""
    repair_manifest_sha256 = ""
    replacement_attestation_sha256 = ""
    repair_completion_reference = ""
    editorial_overlay: Mapping[str, Any] | None = None
    editorial_overlay_path = ""
    editorial_overlay_sha256 = ""
    editorial_overlay_issue = ""
    source_ci_run = ""
    if commit_sha == ACCEPTED_CONTENT_COMMIT:
        repair_path = root / REPAIR_MANIFEST_PATH
        repair_bytes = _read_file(
            repair_path,
            relative=REPAIR_MANIFEST_PATH,
            contract=contract,
        )
        if hashlib.sha256(repair_bytes).hexdigest() != REPAIR_MANIFEST_SHA256:
            _fail("repair_manifest_tampered", REPAIR_MANIFEST_PATH)
        try:
            repair_text = repair_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("repair_manifest_invalid", REPAIR_MANIFEST_PATH)
        repair_manifest = _load_yaml_mapping(
            repair_text,
            path=REPAIR_MANIFEST_PATH,
            contract=contract,
        )
        repair_baseline = repair_manifest.get("baseline")
        repair_rows = repair_manifest.get("repairs")
        if (
            type(repair_manifest.get("schema_version")) is not int
            or repair_manifest.get("schema_version") != 1
            or repair_manifest.get("issue") != "https://github.com/DataTalksClub/content/issues/2"
            or not isinstance(repair_baseline, dict)
            or repair_baseline.get("repository") != "https://github.com/DataTalksClub/content"
            or repair_baseline.get("commit") != ORIGINAL_MIGRATION_COMMIT
            or repair_baseline.get("migration_manifest_sha256") != MIGRATION_SHA256
            or repair_baseline.get("legacy_repository")
            != "https://github.com/DataTalksClub/datatalksclub.github.io"
            or repair_baseline.get("legacy_commit") != LEGACY_SOURCE_COMMIT
            or repair_manifest.get("expected_delta")
            != {
                "articles": 0,
                "podcasts": 0,
                "podcast_transcripts": 0,
                "books": 0,
                "media": 8,
            }
            or repair_manifest.get("current_counts") != ACCEPTED_SOURCE_COUNTS
            or not isinstance(repair_rows, list)
            or any(not isinstance(item, dict) for item in repair_rows)
            or [item.get("ordinal") for item in repair_rows] != list(range(1, 11))
        ):
            _fail("repair_manifest_invalid", REPAIR_MANIFEST_PATH)
        repair_manifest_path = REPAIR_MANIFEST_PATH
        repair_manifest_sha256 = REPAIR_MANIFEST_SHA256
        replacement_attestation_sha256 = REPLACEMENT_ATTESTATION_SHA256
        repair_completion_reference = REPAIR_COMPLETION_REFERENCE
        repaired_baseline_commit = REPAIRED_BASELINE_COMMIT
        repaired_baseline_tree = REPAIRED_BASELINE_TREE
        repaired_baseline_ci_run = REPAIRED_BASELINE_CI_RUN

        overlay_bytes = _read_file(
            root / EDITORIAL_OVERLAY_PATH,
            relative=EDITORIAL_OVERLAY_PATH,
            contract=contract,
        )
        if hashlib.sha256(overlay_bytes).hexdigest() != EDITORIAL_OVERLAY_SHA256:
            _fail("editorial_overlay_tampered", EDITORIAL_OVERLAY_PATH)
        try:
            overlay_text = overlay_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("editorial_overlay_utf8_required", EDITORIAL_OVERLAY_PATH)
        editorial_overlay = _load_yaml_mapping(
            overlay_text,
            path=EDITORIAL_OVERLAY_PATH,
            contract=contract,
        )
        _validate_editorial_overlay(root, editorial_overlay, contract=contract)
        editorial_overlay_path = EDITORIAL_OVERLAY_PATH
        editorial_overlay_sha256 = EDITORIAL_OVERLAY_SHA256
        editorial_overlay_issue = EDITORIAL_OVERLAY_ISSUE
        source_ci_run = SOURCE_CI_RUN

    paths = _collect_paths(root)
    all_paths = tuple(path for group in paths.values() for path in group)
    if len(all_paths) > contract.max_files:
        _fail("source_file_count_limit_exceeded")
    source_bytes = 0
    raw_files: dict[str, bytes] = {}
    for path in sorted(all_paths):
        relative = _safe_relative(path, root)
        data = _read_file(path, relative=relative, contract=contract)
        raw_files[relative] = data
        source_bytes += len(data)
        if source_bytes > contract.max_source_bytes:
            _fail("source_byte_limit_exceeded")
        if clock() - started_at > contract.max_validation_seconds:
            _fail("source_validation_time_limit_exceeded")

    preflight = _preflight_asset_references(
        paths,
        raw_files,
        root=root,
        contract=contract,
    )

    (
        contracts,
        public_contracts_sha256,
        adopted_public_paths,
        approved_person_keys,
    ) = _checked_contracts()
    # #105 is the current public authority and contains a bounded set of adopted
    # paths that were never observed by #34. Parity below must prove those paths
    # against the checked projection; do not fabricate legacy provenance for them.
    documents: list[PreparedDocument] = []
    relations: list[CandidateRelation] = []
    assets: list[CandidateAsset] = []
    referenced_assets: set[str] = set(preflight.referenced_assets)
    route_owners: dict[str, str] = {}

    def register_route(public_path: str, source_path: str) -> None:
        previous = route_owners.setdefault(public_path, source_path)
        if previous != source_path:
            _fail("duplicate_legacy_path", source_path)

    for path in paths["articles"]:
        source_path = _safe_relative(path, root)
        match = _ARTICLE_NAME.fullmatch(path.name)
        if match is None:
            _fail("article_filename_invalid", source_path)
        slug = match.group("slug")
        metadata, raw_body = preflight.article_parts[source_path]
        title = _required_text(metadata, "title", path=source_path)
        authors = _required_list(metadata, "authors", path=source_path)
        public_path = f"/blog/{slug}.html"
        permalink = metadata.get("permalink")
        if permalink is not None and permalink != public_path:
            _fail("article_permalink_mismatch", source_path)
        register_route(public_path, source_path)
        legacy_contract = _contract_for(
            public_path,
            path=source_path,
            index=contracts,
            expect_asset=False,
            allow_unobserved=public_path in adopted_public_paths,
        )
        contract_id, contract_source_id, contract_source_revision = _contract_provenance(
            legacy_contract
        )
        source_checksum = hashlib.sha256(raw_files[source_path]).hexdigest()
        render_source, omitted_remote_images = _accepted_article_render_source(
            raw_body,
            source_path=source_path,
            source_checksum=source_checksum,
            commit_sha=commit_sha,
        )
        rendered, extensions = _render_markdown(render_source, path=source_path)
        edit_url, immutable_url = _source_urls(source_path, commit_sha)
        image_path = _required_text(metadata, "image", path=source_path).lstrip("/")
        referenced_assets.add(image_path)
        referenced_assets.update(_asset_references(raw_body))
        subtitle = _optional_text(metadata, "subtitle", path=source_path)
        description = _optional_text(metadata, "description", path=source_path)
        summary = subtitle or description
        documents.append(
            PreparedDocument(
                content_kind="article",
                stable_key=slug,
                source_path=source_path,
                checksum=source_checksum,
                source_created_at=_source_datetime(
                    metadata.get("date") or metadata.get("datepublished") or match.group("date"),
                    path=source_path,
                ),
                exact_public_path=public_path,
                slug=slug,
                title=title,
                summary=summary,
                canonical_url=f"https://datatalks.club{public_path}",
                seo_title=f"{title} — DataTalks.Club",
                seo_description=description,
                seo_image_url=_public_asset_url(image_path),
                raw_frontmatter=metadata,
                raw_body=raw_body,
                rendered_html=rendered,
                adapter_metadata=_document_metadata(
                    immutable_url=immutable_url,
                    legacy_path=public_path,
                    migration_sha256=MIGRATION_SHA256,
                    extensions=extensions,
                    omitted_remote_images=omitted_remote_images,
                ),
                is_published=True,
                noindex=False,
                edit_url=edit_url,
                contract_id=contract_id,
                contract_source_id=contract_source_id,
                contract_source_revision=contract_source_revision,
            )
        )
        relations.extend(
            _person_relations(
                source_kind="article",
                source_key=slug,
                relation_type="author",
                values=authors,
                path=source_path,
            )
        )

    episode_metadata: dict[str, dict[str, Any]] = {}
    transcript_references: dict[str, str] = {}
    for path in paths["podcasts"]:
        source_path = _safe_relative(path, root)
        try:
            raw_text = raw_files[source_path].decode("utf-8")
        except UnicodeDecodeError:
            _fail("content_utf8_required", source_path)
        metadata = preflight.structured_documents[source_path]
        slug = _required_text(metadata, "slug", path=source_path)
        if _SLUG.fullmatch(slug) is None or path.name != f"{slug}.yaml":
            _fail("podcast_slug_filename_mismatch", source_path)
        if len(slug) > _PODCAST_SLUG_MAX_LENGTH:
            _fail("podcast_slug_too_long", source_path)
        if slug in episode_metadata:
            _fail("duplicate_podcast_slug", source_path)
        episode_metadata[slug] = metadata
        title = _required_text(metadata, "title", path=source_path)
        legacy_path = _required_text(metadata, "legacy_path", path=source_path)
        if legacy_path != f"/podcast/{slug}.html":
            _fail("podcast_legacy_path_mismatch", source_path)
        # The source YAML keeps its historical `legacy_path` field for provenance, while
        # the prepared public document follows the code-owned canonical route registry.
        public_path = podcast_canonical_path(slug)
        guests = _required_list(metadata, "guests", path=source_path)
        for required_number in ("season", "episode"):
            value = metadata.get(required_number)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                _fail(f"podcast_{required_number}_invalid", source_path)
        _validate_episode_links(metadata, path=source_path)
        transcript = metadata.get("transcript")
        if isinstance(transcript, list):
            _fail("inline_transcript_not_allowed", source_path)
        if transcript is not None:
            if not isinstance(transcript, str):
                _fail("transcript_reference_invalid", source_path)
            pure = PurePosixPath(transcript)
            if (
                pure.is_absolute()
                or pure.parent != PurePosixPath("transcripts")
                or pure.suffix != ".yaml"
                or any(part in {"", ".", ".."} for part in pure.parts)
                or "\\" in transcript
            ):
                _fail("transcript_reference_outside_directory", source_path)
            target = f"podcasts/{transcript}"
            if target in transcript_references:
                _fail("duplicate_transcript_reference", source_path)
            transcript_references[target] = slug
        register_route(public_path, source_path)
        podcast_contract = _contract_for(
            public_path,
            path=source_path,
            index=contracts,
            expect_asset=False,
            allow_unobserved=public_path in adopted_public_paths,
        )
        contract_id, contract_source_id, contract_source_revision = _contract_provenance(
            podcast_contract
        )
        image_path = _required_text(metadata, "image", path=source_path).lstrip("/")
        referenced_assets.add(image_path)
        referenced_assets.update(_asset_references(metadata))
        description = _required_string(metadata, "description", path=source_path)
        summary = description
        rendered = _render_text(
            "\n\n".join(
                value
                for value in (metadata.get("intro"), metadata.get("notes"))
                if isinstance(value, str) and value.strip()
            )
            or summary,
            kind="podcast",
            path=source_path,
        )
        edit_url, immutable_url = _source_urls(source_path, commit_sha)
        documents.append(
            PreparedDocument(
                content_kind="podcast",
                stable_key=slug,
                source_path=source_path,
                checksum=hashlib.sha256(raw_files[source_path]).hexdigest(),
                source_created_at=_source_datetime(metadata.get("dateadded"), path=source_path),
                exact_public_path=public_path,
                slug=slug,
                title=title,
                summary=summary,
                canonical_url=f"https://datatalks.club{public_path}",
                seo_title=f"{title} — DataTalks.Club Podcast",
                seo_description=summary,
                seo_image_url=_public_asset_url(image_path),
                raw_body=raw_text,
                raw_structured_data=json.dumps(
                    metadata,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                rendered_html=rendered,
                adapter_metadata=_document_metadata(
                    immutable_url=immutable_url,
                    legacy_path=public_path,
                    migration_sha256=MIGRATION_SHA256,
                    publication_state="published",
                ),
                is_published=True,
                noindex=False,
                edit_url=edit_url,
                contract_id=contract_id,
                contract_source_id=contract_source_id,
                contract_source_revision=contract_source_revision,
            )
        )
        relations.extend(
            _person_relations(
                source_kind="podcast",
                source_key=slug,
                relation_type="guest",
                values=guests,
                path=source_path,
            )
        )
        if transcript is not None:
            relations.append(
                CandidateRelation(
                    source_kind="podcast",
                    source_key=slug,
                    relation_type="transcript",
                    target_kind="podcast_transcript",
                    target_key=slug,
                    order=0,
                )
            )

    actual_transcripts = {_safe_relative(path, root): path for path in paths["podcast_transcripts"]}
    missing = sorted(set(transcript_references) - set(actual_transcripts))
    if missing:
        _fail("referenced_transcript_missing", missing[0])
    orphaned = sorted(set(actual_transcripts) - set(transcript_references))
    if orphaned:
        _fail("orphan_transcript", orphaned[0])
    for source_path in sorted(actual_transcripts):
        try:
            raw_text = raw_files[source_path].decode("utf-8")
        except UnicodeDecodeError:
            _fail("content_utf8_required", source_path)
        metadata = _load_yaml_mapping(raw_text, path=source_path, contract=contract)
        slug = transcript_references[source_path]
        if metadata.get("podcast") != slug:
            _fail("transcript_podcast_mismatch", source_path)
        segments = metadata.get("segments")
        if not isinstance(segments, list):
            _fail("transcript_segments_list_required", source_path)
        if any(not isinstance(segment, dict) for segment in segments):
            _fail("transcript_segment_mapping_required", source_path)
        rendered = _render_transcript(
            cast(list[Mapping[str, Any]], segments),
            path=source_path,
        )
        edit_url, immutable_url = _source_urls(source_path, commit_sha)
        documents.append(
            PreparedDocument(
                content_kind="podcast_transcript",
                stable_key=slug,
                source_path=source_path,
                checksum=hashlib.sha256(raw_files[source_path]).hexdigest(),
                title=f"Transcript: {episode_metadata[slug]['title']}",
                raw_body=raw_text,
                raw_structured_data=json.dumps(
                    metadata,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                rendered_html=rendered,
                adapter_metadata=_document_metadata(
                    immutable_url=immutable_url,
                    legacy_path=None,
                    migration_sha256=MIGRATION_SHA256,
                    publication_state="embedded_only",
                ),
                is_published=False,
                noindex=True,
                edit_url=edit_url,
            )
        )

    book_slugs: set[str] = set()
    for path in paths["books"]:
        source_path = _safe_relative(path, root)
        try:
            raw_text = raw_files[source_path].decode("utf-8")
        except UnicodeDecodeError:
            _fail("content_utf8_required", source_path)
        metadata = preflight.structured_documents[source_path]
        slug = _required_text(metadata, "slug", path=source_path)
        if _SLUG.fullmatch(slug) is None or path.name != f"{slug}.yaml":
            _fail("book_slug_filename_mismatch", source_path)
        if len(slug) > _BOOK_SLUG_MAX_LENGTH:
            _fail("book_slug_too_long", source_path)
        if slug in book_slugs:
            _fail("duplicate_book_slug", source_path)
        book_slugs.add(slug)
        title = _required_text(metadata, "title", path=source_path)
        if len(title) > _BOOK_TITLE_MAX_LENGTH:
            _fail("book_title_too_long", source_path)
        public_path = _required_text(metadata, "legacy_path", path=source_path)
        if public_path != f"/books/{slug}.html":
            _fail("book_legacy_path_mismatch", source_path)
        authors = _required_list(metadata, "authors", path=source_path)
        links = _required_list(metadata, "links", path=source_path)
        for item in links:
            if not isinstance(item, dict):
                _fail("book_link_mapping_invalid", source_path)
            _validate_url(
                item.get("link", item.get("list", item.get("url"))),
                path=source_path,
            )
        register_route(public_path, source_path)
        legacy_contract = _contract_for(
            public_path,
            path=source_path,
            index=contracts,
            expect_asset=False,
            allow_unobserved=public_path in adopted_public_paths,
        )
        contract_id, contract_source_id, contract_source_revision = _contract_provenance(
            legacy_contract
        )
        image_path = _required_text(metadata, "image", path=source_path).lstrip("/")
        cover_path = _required_text(metadata, "cover", path=source_path).lstrip("/")
        referenced_assets.update({image_path, cover_path})
        referenced_assets.update(_asset_references(metadata))
        rendered = _render_book(metadata, path=source_path)
        summary = _required_text(metadata, "description", path=source_path)
        edit_url, immutable_url = _source_urls(source_path, commit_sha)
        documents.append(
            PreparedDocument(
                content_kind="book",
                stable_key=slug,
                source_path=source_path,
                checksum=hashlib.sha256(raw_files[source_path]).hexdigest(),
                source_created_at=_source_datetime(metadata.get("start"), path=source_path),
                exact_public_path=public_path,
                slug=slug,
                title=title,
                summary=summary,
                canonical_url=f"https://datatalks.club{public_path}",
                seo_title=f"{title} — DataTalks.Club Books",
                seo_description=summary,
                seo_image_url=_public_asset_url(image_path),
                raw_body=raw_text,
                raw_structured_data=json.dumps(
                    metadata,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                rendered_html=rendered,
                adapter_metadata=_document_metadata(
                    immutable_url=immutable_url,
                    legacy_path=public_path,
                    migration_sha256=MIGRATION_SHA256,
                ),
                is_published=True,
                noindex=False,
                edit_url=edit_url,
                contract_id=contract_id,
                contract_source_id=contract_source_id,
                contract_source_revision=contract_source_revision,
            )
        )
        relations.extend(
            _person_relations(
                source_kind="book",
                source_key=slug,
                relation_type="book_author",
                values=authors,
                path=source_path,
            )
        )

    media_sources = tuple(_safe_relative(path, root) for path in paths["media"])
    media_validation = validate_media_batch(
        tuple((source_path, raw_files[source_path]) for source_path in media_sources)
    )
    validated_media_types: dict[str, str] = {}
    for source_path, content_type, error_code in media_validation:
        if error_code:
            _fail(error_code, source_path)
        validated_media_types[source_path] = content_type

    media_paths: set[str] = set()
    for source_path in media_sources:
        public_path = f"/{quote(source_path, safe='/')}"
        if source_path in media_paths:
            _fail("duplicate_media_path", source_path)
        media_paths.add(source_path)
        register_route(public_path, source_path)
        legacy_contract = _contract_for(
            public_path,
            path=source_path,
            index=contracts,
            expect_asset=True,
            allow_unobserved=public_path in adopted_public_paths,
        )
        contract_id, contract_source_id, contract_source_revision = _contract_provenance(
            legacy_contract
        )
        data = raw_files[source_path]
        assets.append(
            CandidateAsset(
                source_path=source_path,
                stable_public_path=public_path,
                content_type=validated_media_types[source_path],
                size=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
                contract_id=contract_id,
                contract_source_id=contract_source_id,
                contract_source_revision=contract_source_revision,
            )
        )
    missing_assets = sorted(referenced_assets - media_paths)
    if missing_assets:
        _fail("referenced_asset_missing", missing_assets[0])

    if commit_sha == ACCEPTED_CONTENT_COMMIT:
        relations = [
            replace(
                relation,
                is_required=(
                    relation.target_kind != "person" or relation.target_key in approved_person_keys
                ),
            )
            for relation in relations
        ]

    counts = {name: len(group) for name, group in paths.items()}
    if commit_sha == ACCEPTED_CONTENT_COMMIT and counts != ACCEPTED_COUNTS:
        _fail("accepted_baseline_count_mismatch")
    documents.sort(key=lambda item: (item.content_kind, item.stable_key, item.source_path))
    relations.sort(
        key=lambda item: (
            item.source_kind,
            item.source_key,
            item.relation_type,
            item.order,
            item.target_kind,
            item.target_key,
        )
    )
    assets.sort(key=lambda item: item.stable_public_path)
    if len({(item.content_kind, item.stable_key) for item in documents}) != len(documents):
        _fail("duplicate_document_identity")
    if clock() - started_at > contract.max_validation_seconds:
        _fail("source_validation_time_limit_exceeded")
    bundle_sha256 = _bundle_digest(
        commit_sha=commit_sha,
        source_evidence={
            "source_tree_sha": source_tree_sha,
            "original_migration_commit": ORIGINAL_MIGRATION_COMMIT,
            "repaired_baseline_commit": repaired_baseline_commit,
            "repaired_baseline_tree": repaired_baseline_tree,
            "repaired_baseline_ci_run": repaired_baseline_ci_run,
            "migration_sha256": MIGRATION_SHA256,
            "repair_manifest_path": repair_manifest_path,
            "repair_manifest_sha256": repair_manifest_sha256,
            "replacement_attestation_sha256": replacement_attestation_sha256,
            "repair_completion_reference": repair_completion_reference,
            "editorial_overlay_path": editorial_overlay_path,
            "editorial_overlay_sha256": editorial_overlay_sha256,
            "editorial_overlay_issue": editorial_overlay_issue,
            "source_ci_run": source_ci_run,
        },
        documents=documents,
        relations=relations,
        assets=assets,
    )
    if commit_sha == ACCEPTED_CONTENT_COMMIT and bundle_sha256 != ACCEPTED_BUNDLE_SHA256:
        _fail("accepted_bundle_digest_mismatch")
    return CandidateBundle(
        source_stable_id=contract.stable_id,
        repository=contract.repository_https_url,
        branch=contract.branch,
        commit_sha=commit_sha,
        source_tree_sha=source_tree_sha,
        adapter_type=contract.adapter_type,
        schema_version=contract.schema_version,
        parser_version=contract.parser_version,
        rendering_version=contract.rendering_version,
        migration_sha256=MIGRATION_SHA256,
        migration=migration,
        original_migration_commit=ORIGINAL_MIGRATION_COMMIT,
        repaired_baseline_commit=repaired_baseline_commit,
        repaired_baseline_tree=repaired_baseline_tree,
        repaired_baseline_ci_run=repaired_baseline_ci_run,
        repair_manifest_path=repair_manifest_path,
        repair_manifest_sha256=repair_manifest_sha256,
        repair_manifest=repair_manifest,
        replacement_attestation_sha256=replacement_attestation_sha256,
        repair_completion_reference=repair_completion_reference,
        editorial_overlay_path=editorial_overlay_path,
        editorial_overlay_sha256=editorial_overlay_sha256,
        editorial_overlay=editorial_overlay,
        editorial_overlay_issue=editorial_overlay_issue,
        source_ci_run=source_ci_run,
        public_contracts_sha256=public_contracts_sha256,
        documents=tuple(documents),
        relations=tuple(relations),
        assets=tuple(assets),
        referenced_asset_paths=tuple(sorted(referenced_assets)),
        counts=counts,
        bundle_sha256=bundle_sha256,
    )
