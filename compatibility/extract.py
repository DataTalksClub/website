"""Deterministic metadata extraction for legacy HTML, JSON, and sitemap fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Iterator, Mapping
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from compatibility.models import (
    PageMetadata,
    Reference,
    ReferenceKind,
    SitemapEntry,
    SitemapState,
    StructuredData,
)
from compatibility.redaction import is_url_valued_social_key, redact_fragment, redact_url

MAX_METADATA_ITEMS = 100_000
_SPACE = re.compile(r"\s+")
_ROBOTS_SEPARATOR = re.compile(r"[\s,]+")
_SOFT_404 = re.compile(r"^(?:404(?:\s+error)?|not found|page not found|page unavailable)\b", re.I)
_META_REFRESH = re.compile(r"^(?:\d+(?:\.\d+)?)\s*;\s*url\s*=\s*(.+)$", re.I)
_HIDDEN_ELEMENTS = frozenset({"script", "style", "noscript", "template", "svg", "title"})
_CHROME_ELEMENTS = frozenset({"footer", "header", "nav"})
_ASSET_ELEMENTS = frozenset(
    {"audio", "embed", "iframe", "img", "input", "script", "source", "track", "video"}
)
_ASSET_LINK_RELS = frozenset(
    {
        "apple-touch-icon",
        "dns-prefetch",
        "icon",
        "image_src",
        "manifest",
        "mask-icon",
        "modulepreload",
        "preconnect",
        "prefetch",
        "preload",
        "stylesheet",
    }
)


class ExtractionError(ValueError):
    """A value-free parse failure suitable for an operator log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\ufeff", "")
    return _SPACE.sub(" ", normalized).strip()


def _fingerprint(value: str) -> str:
    normalized = _text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _host(value: str) -> str:
    hostname = urlsplit(value).hostname or ""
    try:
        return hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ExtractionError("invalid_reference_host") from exc


def _absolute_reference(
    raw_url: str,
    document_url: str,
    internal_hosts: frozenset[str],
    kind: ReferenceKind | None = None,
) -> Reference | None:
    value = raw_url.strip()
    if not value:
        return None
    try:
        absolute = redact_url(urljoin(document_url, value))
        parsed = urlsplit(absolute)
    except ValueError as exc:
        if str(exc) == "url_contains_credentials":
            raise ExtractionError("reference_contains_credentials") from exc
        raise ExtractionError("invalid_reference_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        # mailto/tel/data/javascript references are neither fetched nor persisted, which also
        # prevents personal addresses and inline payloads entering baseline artifacts.
        return None
    if parsed.username is not None or parsed.password is not None:
        raise ExtractionError("reference_contains_credentials")
    selected_kind = kind
    if selected_kind is None:
        selected_kind = (
            ReferenceKind.INTERNAL_LINK
            if _host(absolute) in internal_hosts
            else ReferenceKind.EXTERNAL_LINK
        )
    return Reference(kind=selected_kind, url=absolute)


def _absolute_metadata_url(raw_url: str, document_url: str) -> str:
    """Normalize and redact a social/structured URL before durable storage."""

    try:
        absolute = redact_url(urljoin(document_url, raw_url))
        parsed = urlsplit(absolute)
    except ValueError as exc:
        if str(exc) == "url_contains_credentials":
            raise ExtractionError("metadata_url_contains_credentials") from exc
        raise ExtractionError("invalid_metadata_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExtractionError("invalid_metadata_url")
    return absolute


def _structured_value(value: str, document_url: str) -> str:
    """Redact structured-data URL values while preserving non-URL identifiers."""

    parsed = urlsplit(value)
    is_url_value = (
        parsed.scheme in {"http", "https"}
        or value.startswith(("//", "/", "./", "../", "#", "?"))
        or "?" in value
    )
    if not is_url_value:
        return value
    return _absolute_metadata_url(value, document_url)


class _MetadataParser(HTMLParser):
    def __init__(self, document_url: str, internal_hosts: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.document_url = document_url
        self.internal_hosts = internal_hosts
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.main_parts: list[str] = []
        self.body_parts: list[str] = []
        self.description = ""
        self.language = ""
        self.canonical_url = ""
        self.client_redirect_url = ""
        self.alternates: set[tuple[str, str]] = set()
        self.robots: set[str] = set()
        self.social_metadata: dict[str, str] = {}
        self.structured_data: list[StructuredData] = []
        self.fragments: set[str] = set()
        self.references: set[Reference] = set()
        self._hidden_depth = 0
        self._head_open = False
        self._body_started = False
        self._svg_depth = 0
        self._chrome_depth = 0
        self._main_depth = 0
        self._seen_main = False
        self._title_depth = 0
        self._document_title_complete = False
        self._heading_depth = 0
        self._heading_complete = False
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []
        self._item_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        self._item_count += 1
        if self._item_count > MAX_METADATA_ITEMS:
            raise ExtractionError("html_metadata_limit_exceeded")
        if name == "body":
            self._close_implicit_head()
            self._body_started = True
        if name == "html" and not self.language:
            self.language = _text(attributes.get("lang", ""))
        fragment = attributes.get("id")
        if fragment:
            self.fragments.add(redact_fragment(fragment))
        if name == "a" and attributes.get("name"):
            self.fragments.add(redact_fragment(attributes["name"]))
        if name == "head":
            self._head_open = True
            self._hidden_depth += 1
        elif name in _HIDDEN_ELEMENTS:
            self._hidden_depth += 1
        if name == "svg":
            self._svg_depth += 1
        if name in _CHROME_ELEMENTS:
            self._chrome_depth += 1
        if name == "main" and not self._hidden_depth:
            self._main_depth += 1
            self._seen_main = True
        if (
            name == "title"
            and not self._body_started
            and not self._svg_depth
            and not self._document_title_complete
        ):
            self._title_depth = 1
        if name == "h1" and not self._hidden_depth and not self._heading_complete:
            self._heading_depth += 1
        if name == "meta":
            self._handle_meta(attributes)
        elif name == "link":
            self._handle_link(attributes)
        elif name == "a" and "href" in attributes:
            self._add_reference(attributes["href"] or self.document_url)
        elif name == "form":
            self._add_reference(
                attributes.get("action") or self.document_url,
                ReferenceKind.FORM_ACTION,
            )
        if name in _ASSET_ELEMENTS:
            for key in ("src", "poster"):
                if key in attributes:
                    self._add_reference(
                        attributes[key] or self.document_url,
                        ReferenceKind.ASSET,
                    )
            self._add_srcset(attributes.get("srcset", ""))
        if name == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._json_ld_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            if self._json_ld_depth == 0:
                raw = "".join(self._json_ld_parts).strip()
                self._json_ld_parts.clear()
                if raw:
                    self.structured_data.extend(_structured_data_from_json(raw, self.document_url))
        if name == "h1" and self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                self._heading_complete = True
        if name == "title" and self._title_depth:
            self._title_depth = 0
            self._document_title_complete = True
        if name == "main" and self._main_depth:
            self._main_depth -= 1
        if name in _CHROME_ELEMENTS and self._chrome_depth:
            self._chrome_depth -= 1
        if name == "head" and self._head_open:
            self._head_open = False
            self._hidden_depth -= 1
        elif name in _HIDDEN_ELEMENTS and self._hidden_depth:
            self._hidden_depth -= 1
        if name == "svg" and self._svg_depth:
            self._svg_depth -= 1

    def _close_implicit_head(self) -> None:
        if not self._head_open:
            return
        self._head_open = False
        self._hidden_depth -= 1
        if self._title_depth:
            self._title_depth = 0
            self._document_title_complete = True

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_parts.append(data)
            return
        if self._title_depth:
            self.title_parts.append(data)
        if self._heading_depth:
            self.heading_parts.append(data)
        if self._hidden_depth:
            return
        if self._main_depth:
            self.main_parts.append(data)
        if not self._chrome_depth:
            self.body_parts.append(data)

    def _handle_meta(self, attributes: Mapping[str, str]) -> None:
        name = attributes.get("name", "").strip().lower()
        property_name = attributes.get("property", "").strip().lower()
        content = _text(attributes.get("content", ""))
        if attributes.get("http-equiv", "").strip().lower() == "refresh":
            redirect_url = _meta_refresh_url(content, self.document_url)
            if self.client_redirect_url and self.client_redirect_url != redirect_url:
                raise ExtractionError("conflicting_meta_refresh")
            self.client_redirect_url = redirect_url
            self._add_reference(redirect_url)
        if not content:
            return
        if name == "description" and not self.description:
            self.description = content
        elif name in {"robots", "googlebot"}:
            self.robots.update(item for item in _ROBOTS_SEPARATOR.split(content.lower()) if item)
        social_key = property_name or name
        if social_key.startswith(("og:", "twitter:")):
            durable_content = (
                _absolute_metadata_url(content, self.document_url)
                if is_url_valued_social_key(social_key)
                else content
            )
            self.social_metadata[social_key] = durable_content
            if social_key.endswith(("image", "image:url", "player")):
                self._add_reference(content, ReferenceKind.ASSET)

    def _handle_link(self, attributes: Mapping[str, str]) -> None:
        href = attributes.get("href")
        relations = frozenset(attributes.get("rel", "").lower().split())
        reference = (
            _absolute_reference(href or self.document_url, self.document_url, self.internal_hosts)
            if href is not None
            else None
        )
        if "canonical" in relations and reference is not None:
            self.canonical_url = reference.url
        if "alternate" in relations and reference is not None:
            label = attributes.get("hreflang", "") or "alternate"
            self.alternates.add((label, reference.url))
        if relations & _ASSET_LINK_RELS:
            if href is not None:
                self._add_reference(href or self.document_url, ReferenceKind.ASSET)
        elif reference is not None and not relations & {"alternate", "canonical"}:
            self.references.add(reference)

    def _add_reference(self, value: str, kind: ReferenceKind | None = None) -> None:
        reference = _absolute_reference(value, self.document_url, self.internal_hosts, kind)
        if reference is not None:
            self.references.add(reference)

    def _add_srcset(self, value: str) -> None:
        for candidate in value.split(","):
            url = candidate.strip().split(maxsplit=1)[0] if candidate.strip() else ""
            self._add_reference(url, ReferenceKind.ASSET)

    def metadata(self) -> PageMetadata:
        title = _text("".join(self.title_parts))
        heading = _text("".join(self.heading_parts))
        meaningful = " ".join(self.main_parts if self._seen_main else self.body_parts)
        return PageMetadata(
            title=title,
            description=self.description,
            first_heading=heading,
            language=self.language,
            robots=tuple(sorted(self.robots)),
            canonical_url=self.canonical_url,
            client_redirect_url=self.client_redirect_url,
            alternates=tuple(sorted(self.alternates)),
            social_metadata=tuple(sorted(self.social_metadata.items())),
            structured_data=tuple(
                sorted(set(self.structured_data), key=lambda item: (item.type, item.identifier))
            ),
            fragments=tuple(sorted(self.fragments)),
            references=tuple(sorted(self.references, key=lambda item: (item.kind.value, item.url))),
            main_content_fingerprint=_fingerprint(meaningful),
            soft_404=any(bool(_SOFT_404.search(value)) for value in (title, heading)),
        )


def extract_html(body: str, document_url: str, internal_hosts: Iterable[str]) -> PageMetadata:
    parser = _MetadataParser(document_url, frozenset(host.lower() for host in internal_hosts))
    try:
        parser.feed(body)
        parser.close()
    except (RecursionError, ValueError) as exc:
        if isinstance(exc, ExtractionError):
            raise
        raise ExtractionError("invalid_html") from exc
    return parser.metadata()


def _meta_refresh_url(content: str, document_url: str) -> str:
    match = _META_REFRESH.fullmatch(content)
    if match is None:
        raise ExtractionError("invalid_meta_refresh")
    target = match.group(1).strip()
    if len(target) >= 2 and target[0] == target[-1] and target[0] in {'"', "'"}:
        target = target[1:-1].strip()
    elif target.startswith(('"', "'")) or target.endswith(('"', "'")):
        raise ExtractionError("invalid_meta_refresh")
    if not target:
        raise ExtractionError("invalid_meta_refresh")
    return _absolute_metadata_url(target, document_url)


def _walk_json(value: object) -> Iterator[object]:
    pending = [value]
    count = 0
    while pending:
        current = pending.pop()
        count += 1
        if count > MAX_METADATA_ITEMS:
            raise ExtractionError("json_metadata_limit_exceeded")
        yield current
        if isinstance(current, dict):
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def _structured_data(value: object, document_url: str) -> tuple[StructuredData, ...]:
    found: set[StructuredData] = set()
    for current in _walk_json(value):
        if not isinstance(current, dict):
            continue
        raw_types = current.get("@type")
        types = [raw_types] if isinstance(raw_types, str) else raw_types
        if not isinstance(types, list):
            continue
        identifier_value = current.get("@id", current.get("identifier", ""))
        identifier = (
            _structured_value(identifier_value, document_url)
            if isinstance(identifier_value, str)
            else ""
        )
        for type_name in types:
            if isinstance(type_name, str) and type_name:
                found.add(
                    StructuredData(
                        type=_structured_value(type_name, document_url),
                        identifier=identifier,
                    )
                )
    return tuple(sorted(found, key=lambda item: (item.type, item.identifier)))


def _structured_data_from_json(raw: str, document_url: str) -> tuple[StructuredData, ...]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ExtractionError("invalid_json_ld") from exc
    return _structured_data(value, document_url)


def extract_json(body: str, document_url: str, internal_hosts: Iterable[str]) -> PageMetadata:
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ExtractionError("invalid_json") from exc
    # Traverse once for a hard node bound before deterministic canonical serialization.
    tuple(_walk_json(value))
    try:
        canonical = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ExtractionError("invalid_json") from exc
    hosts = frozenset(host.lower() for host in internal_hosts)
    references: set[Reference] = set()
    for current in _walk_json(value):
        if not isinstance(current, str) or not current.startswith(("http://", "https://", "/")):
            continue
        reference = _absolute_reference(current, document_url, hosts)
        if reference is not None:
            references.add(reference)
    return PageMetadata(
        structured_data=_structured_data(value, document_url),
        references=tuple(sorted(references, key=lambda item: (item.kind.value, item.url))),
        main_content_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def extract_text(body: str, document_url: str, internal_hosts: Iterable[str]) -> PageMetadata:
    """Fingerprint text contracts and retain ``Sitemap:`` references from robots.txt."""

    hosts = frozenset(host.lower() for host in internal_hosts)
    references: set[Reference] = set()
    for raw_line in body.splitlines():
        name, separator, value = raw_line.partition(":")
        if not separator or name.strip().lower() != "sitemap":
            continue
        reference = _absolute_reference(value.strip(), document_url, hosts)
        if reference is not None:
            references.add(reference)
    return PageMetadata(
        references=tuple(sorted(references, key=lambda item: (item.kind.value, item.url))),
        main_content_fingerprint=_fingerprint(body),
    )


def extract_sitemap(body: str, document_url: str, internal_hosts: Iterable[str]) -> SitemapState:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.I):
        raise ExtractionError("unsafe_xml_declaration")
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, RecursionError) as exc:
        raise ExtractionError("invalid_xml") from exc
    hosts = frozenset(host.lower() for host in internal_hosts)
    entries: set[SitemapEntry] = set()
    nodes = 0
    for element in root.iter():
        nodes += 1
        if nodes > MAX_METADATA_ITEMS:
            raise ExtractionError("xml_metadata_limit_exceeded")
        if _local_name(element.tag) not in {"url", "sitemap"}:
            continue
        children = {_local_name(child.tag): _text(child.text or "") for child in element}
        location = children.get("loc", "")
        if not location:
            continue
        reference = _absolute_reference(location, document_url, hosts)
        if reference is None:
            raise ExtractionError("sitemap_contains_non_http_url")
        entries.add(SitemapEntry(url=reference.url, lastmod=children.get("lastmod", "")))
    return SitemapState(entries=tuple(sorted(entries, key=lambda item: (item.url, item.lastmod))))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
