from __future__ import annotations

from urllib.parse import urlsplit
from xml.etree import ElementTree

PRODUCTION_ORIGIN = "https://datatalks.club"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
SITEMAP_SECTIONS = (
    "main",
    "blog",
    "podcast",
    "books",
    "people",
    "events",
    "courses",
    "wiki",
    "docs",
    "faq",
)
EXPECTED_SITEMAP_LOCATIONS = tuple(
    f"{PRODUCTION_ORIGIN}/sitemaps/{section}.xml" for section in SITEMAP_SECTIONS
)


class SitemapContractError(ValueError):
    """The public sitemap index differs from the accepted release contract."""


def _contains_non_whitespace(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def validate_sitemap_index(body: bytes) -> tuple[str, ...]:
    """Return the exact canonical section locations from a safe sitemap index."""

    if type(body) is not bytes or not body:
        raise SitemapContractError("sitemap index body is empty")
    lowered = body.lower()
    if any(marker in lowered for marker in (b"<!doctype", b"<!entity", b"<?xml-stylesheet")):
        raise SitemapContractError("sitemap index contains an unsafe XML declaration")
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, ValueError) as error:
        raise SitemapContractError("sitemap index is malformed") from error

    sitemap_tag = f"{{{SITEMAP_NAMESPACE}}}sitemap"
    location_tag = f"{{{SITEMAP_NAMESPACE}}}loc"
    if root.tag != f"{{{SITEMAP_NAMESPACE}}}sitemapindex" or root.attrib:
        raise SitemapContractError("sitemap index root differs")
    if _contains_non_whitespace(root.text) or _contains_non_whitespace(root.tail):
        raise SitemapContractError("sitemap index contains unexpected text")

    locations: list[str] = []
    for sitemap in root:
        children = list(sitemap)
        if (
            sitemap.tag != sitemap_tag
            or sitemap.attrib
            or len(children) != 1
            or children[0].tag != location_tag
            or children[0].attrib
            or list(children[0])
            or _contains_non_whitespace(sitemap.text)
            or _contains_non_whitespace(sitemap.tail)
            or _contains_non_whitespace(children[0].tail)
        ):
            raise SitemapContractError("sitemap index entry shape differs")
        location = children[0].text
        if location is None or location != location.strip():
            raise SitemapContractError("sitemap index location is malformed")
        try:
            parsed = urlsplit(location)
            port = parsed.port
        except ValueError as error:
            raise SitemapContractError("sitemap index location is malformed") from error
        if (
            parsed.scheme != "https"
            or parsed.netloc != "datatalks.club"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise SitemapContractError("sitemap index location is not a canonical production URL")
        locations.append(location)

    if not locations:
        raise SitemapContractError("sitemap index is not populated")
    if len(locations) != len(set(locations)):
        raise SitemapContractError("sitemap index contains duplicate locations")
    if tuple(locations) != EXPECTED_SITEMAP_LOCATIONS:
        raise SitemapContractError("sitemap index section locations differ")
    return tuple(locations)
