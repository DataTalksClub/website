from __future__ import annotations

from pathlib import Path

import pytest

from compatibility.extract import (
    ExtractionError,
    extract_html,
    extract_json,
    extract_sitemap,
    extract_text,
)
from compatibility.models import (
    Capture,
    Classification,
    CompatibilityRow,
    ManifestProvenance,
    ObservationOrigin,
    Reference,
    ReferenceKind,
    SitemapEntry,
    StructuredData,
    dumps_jsonl,
)

INTERNAL_HOSTS = frozenset({"datatalks.club", "courses.datatalks.club"})
EXTRACT_FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def test_html_extracts_full_page_contract_and_normalized_fingerprint() -> None:
    html = """
    <!doctype html>
    <html lang="en">
      <head>
        <title>  Legacy   page </title>
        <meta name="description" content=" A useful page ">
        <meta name="robots" content="index, follow">
        <meta property="og:title" content="Legacy social title">
        <meta property="og:image" content="/images/card.png">
        <meta name="twitter:card" content="summary_large_image">
        <link rel="canonical" href="https://datatalks.club/blog/example.html">
        <link rel="alternate" hreflang="es" href="/es/example.html">
        <link rel="stylesheet" href="/assets/site.css">
        <script type="application/ld+json">
          {"@context":"https://schema.org","@type":["BlogPosting","Article"],
           "@id":"https://datatalks.club/blog/example.html#article"}
        </script>
      </head>
      <body>
        <header>Navigation changes do not belong in the fingerprint</header>
        <main id="content">
          <h1 id="welcome"> Welcome   to Café </h1>
          <p>The meaningful body.</p>
          <a href="../articles.html?type=all#top">Articles</a>
          <a href="https://outside.example/path?campaign=kept">Outside</a>
          <img src="/images/photo.jpg" srcset="/images/photo@2x.jpg 2x" alt="Photo">
          <form action="/subscribe" method="post"></form>
        </main>
        <footer>Generated at a changing time</footer>
      </body>
    </html>
    """
    metadata = extract_html(
        html,
        "https://datatalks.club/blog/example.html",
        INTERNAL_HOSTS,
    )

    assert metadata.title == "Legacy page"
    assert metadata.description == "A useful page"
    assert metadata.first_heading == "Welcome to Café"
    assert metadata.language == "en"
    assert metadata.robots == ("follow", "index")
    assert metadata.canonical_url == "https://datatalks.club/blog/example.html"
    assert metadata.alternates == (("es", "https://datatalks.club/es/example.html"),)
    assert metadata.social_metadata == (
        ("og:image", "https://datatalks.club/images/card.png"),
        ("og:title", "Legacy social title"),
        ("twitter:card", "summary_large_image"),
    )
    assert metadata.structured_data == (
        StructuredData(
            type="Article",
            identifier="https://datatalks.club/blog/example.html#article",
        ),
        StructuredData(
            type="BlogPosting",
            identifier="https://datatalks.club/blog/example.html#article",
        ),
    )
    assert metadata.fragments == ("content", "welcome")
    assert metadata.references == (
        Reference(ReferenceKind.ASSET, "https://datatalks.club/assets/site.css"),
        Reference(ReferenceKind.ASSET, "https://datatalks.club/images/card.png"),
        Reference(ReferenceKind.ASSET, "https://datatalks.club/images/photo.jpg"),
        Reference(ReferenceKind.ASSET, "https://datatalks.club/images/photo@2x.jpg"),
        Reference(ReferenceKind.EXTERNAL_LINK, "https://outside.example/path?campaign=kept"),
        Reference(ReferenceKind.FORM_ACTION, "https://datatalks.club/subscribe"),
        Reference(
            ReferenceKind.INTERNAL_LINK,
            "https://datatalks.club/articles.html?type=all#top",
        ),
    )
    assert len(metadata.main_content_fingerprint) == 64
    assert metadata.soft_404 is False

    cosmetic_change = html.replace("The meaningful body.", "  The   meaningful\nbody. ").replace(
        "Generated at a changing time", "Generated at some other time"
    )
    assert (
        extract_html(
            cosmetic_change,
            "https://datatalks.club/blog/example.html",
            INTERNAL_HOSTS,
        ).main_content_fingerprint
        == metadata.main_content_fingerprint
    )


@pytest.mark.parametrize(
    "title,heading",
    [("404 Error", "Missing"), ("Normal", "Page not found")],
)
def test_html_marks_soft_404_signals(title: str, heading: str) -> None:
    metadata = extract_html(
        f"<html><head><title>{title}</title></head><body><h1>{heading}</h1></body></html>",
        "https://datatalks.club/missing",
        INTERNAL_HOSTS,
    )
    assert metadata.soft_404 is True


def test_pinned_docs_missing_head_close_uses_first_document_title_and_visible_main() -> None:
    body = (EXTRACT_FIXTURES / "pinned-doc-missing-head-close.html").read_text(encoding="utf-8")

    metadata = extract_html(
        body,
        "https://datatalks.club/docs/general/brand-assets/",
        INTERNAL_HOSTS,
    )

    assert metadata.title == "Brand Assets | DataTalks.Club Documentation"
    assert metadata.first_heading == "Brand Assets"
    assert metadata.main_content_fingerprint == (
        "f43bee71dce6efd700a762d2f4e1135b892ffd1092e1d186e5b546806e3d7921"
    )
    assert "Link" not in metadata.title
    assert "Menu" not in metadata.title


def test_body_and_svg_titles_never_enter_fallback_body_fingerprint() -> None:
    template = """
    <html><head><title>Document title</title></head><body>
      <p>Visible body contract.</p>
      <title>{body_title}</title>
      <svg><title>{svg_title}</title></svg>
    </body></html>
    """
    first = extract_html(
        template.format(body_title="Browser-hidden one", svg_title="Icon one"),
        "https://datatalks.club/page.html",
        INTERNAL_HOSTS,
    )
    changed = extract_html(
        template.format(body_title="Browser-hidden two", svg_title="Icon two"),
        "https://datatalks.club/page.html",
        INTERNAL_HOSTS,
    )
    removed = extract_html(
        "<html><head><title>Document title</title></head>"
        "<body><p>Visible body contract.</p></body></html>",
        "https://datatalks.club/page.html",
        INTERNAL_HOSTS,
    )

    assert first.title == changed.title == removed.title == "Document title"
    assert first.main_content_fingerprint == changed.main_content_fingerprint
    assert changed.main_content_fingerprint == removed.main_content_fingerprint


def test_browser_ignored_bom_does_not_change_visible_text_fingerprint() -> None:
    clean = extract_html(
        "<body><p>Visible profile biography.</p></body>",
        "https://datatalks.club/people/example.html",
        INTERNAL_HOSTS,
    )
    with_bom = extract_html(
        "<body><p>Visible profile biography.\ufeff</p></body>",
        "https://datatalks.club/people/example.html",
        INTERNAL_HOSTS,
    )

    assert with_bom.main_content_fingerprint == clean.main_content_fingerprint


def test_meta_refresh_target_is_normalized_redacted_and_observational() -> None:
    metadata = extract_html(
        ('<meta http-equiv="refresh" content="0; url=/new path?access_token=supersecret">'),
        "https://datatalks.club/old.html",
        INTERNAL_HOSTS,
    )

    assert metadata.client_redirect_url.startswith(
        "https://datatalks.club/new%20path?access_token=redacted-sha256-"
    )
    assert metadata.references == (
        Reference(ReferenceKind.INTERNAL_LINK, metadata.client_redirect_url),
    )
    assert "supersecret" not in str(metadata)


@pytest.mark.parametrize(
    ("content", "error_code"),
    [
        ("refresh now", "invalid_meta_refresh"),
        ("0; url=javascript:alert(1)", "invalid_metadata_url"),
        ('0; url="/unterminated', "invalid_meta_refresh"),
        (
            "0; url=https://user:password@datatalks.club/private",
            "metadata_url_contains_credentials",
        ),
    ],
)
def test_meta_refresh_rejects_malformed_or_unsafe_targets(
    content: str,
    error_code: str,
) -> None:
    with pytest.raises(ExtractionError, match=f"^{error_code}$"):
        extract_html(
            f'<meta http-equiv="refresh" content="{content}">',
            "https://datatalks.club/old.html",
            INTERNAL_HOSTS,
        )


def test_html_preserves_unicode_percent_case_query_and_fragments() -> None:
    metadata = extract_html(
        '<a href="/FAQ/Caf%C3%A9.html?type=Exact#Question%2DID">Exact</a>',
        "https://datatalks.club/start/",
        INTERNAL_HOSTS,
    )
    assert metadata.references == (
        Reference(
            ReferenceKind.INTERNAL_LINK,
            "https://datatalks.club/FAQ/Caf%C3%A9.html?type=Exact#Question%2DID",
        ),
    )


def test_html_percent_encodes_raw_path_characters_only() -> None:
    metadata = extract_html(
        '<a href="/Files/Café (Final)&notes.html?type=Exact#Question%2DID">Exact</a>',
        "https://datatalks.club/start/",
        INTERNAL_HOSTS,
    )
    assert metadata.references == (
        Reference(
            ReferenceKind.INTERNAL_LINK,
            (
                "https://datatalks.club/Files/"
                "Caf%C3%A9%20%28Final%29%26notes.html?type=Exact#Question%2DID"
            ),
        ),
    )


def test_explicit_empty_url_attributes_resolve_to_document_but_missing_anchor_is_omitted() -> None:
    document_url = "https://datatalks.club/current.html?document_type=page"
    metadata = extract_html(
        """
        <link rel="canonical" href="">
        <a>Missing href</a><a href="">Empty href</a>
        <form></form><form action=""></form>
        <img><img src="">
        """,
        document_url,
        INTERNAL_HOSTS,
    )

    assert metadata.canonical_url == document_url
    assert metadata.references == (
        Reference(ReferenceKind.ASSET, document_url),
        Reference(ReferenceKind.FORM_ACTION, document_url),
        Reference(ReferenceKind.INTERNAL_LINK, document_url),
    )


@pytest.mark.parametrize("relation", ["image_src", "preconnect", "prefetch", "dns-prefetch"])
def test_legacy_image_and_resource_hint_links_are_classified_only_as_assets(
    relation: str,
) -> None:
    image_url = "https://datatalks.club/images/legacy-cover.jpg"
    metadata = extract_html(
        f'<link rel="{relation}" href="{image_url}">',
        "https://datatalks.club/blog/post.html",
        INTERNAL_HOSTS,
    )

    assert metadata.references == (Reference(ReferenceKind.ASSET, image_url),)


def test_html_rejects_credential_bearing_references_without_echoing_value() -> None:
    secret_url = "https://user:password@outside.example/private"
    with pytest.raises(ExtractionError) as raised:
        extract_html(
            f'<a href="{secret_url}">unsafe</a>',
            "https://datatalks.club/",
            INTERNAL_HOSTS,
        )
    assert str(raised.value) == "reference_contains_credentials"
    assert secret_url not in str(raised.value)


def test_html_redacts_real_opaque_and_sensitive_query_values_deterministically() -> None:
    html = """
    <a href="https://amazon.example/item?dib=eyJopaquePayload123456">Amazon</a>
    <a href="https://wiki.c2.com/?GoodCode">Wiki</a>
    <a href="https://offers.example/?code=DATATALKS22">Offer</a>
    """
    first = extract_html(html, "https://datatalks.club/", INTERNAL_HOSTS)
    second = extract_html(html, "https://datatalks.club/", INTERNAL_HOSTS)
    assert first == second
    urls = [reference.url for reference in first.references]
    assert all("redacted-sha256-" in url for url in urls)
    assert not any(value in " ".join(urls) for value in ("eyJopaque", "GoodCode&", "DATATALKS22"))


def test_html_redacts_url_metadata_and_query_shaped_fragments_before_serialization() -> None:
    secret = "supersecret"
    document_url = "https://datatalks.club/blog/private.html"
    html = f"""
    <meta property="og:image" content="/image.png?token={secret}">
    <meta property="og:url" content="{document_url}#access_token={secret}">
    <meta http-equiv="refresh" content="0; url=/new?access_token={secret}">
    <script type="application/ld+json">
      {{"@type":"Article","@id":"{document_url}?access_token={secret}"}}
    </script>
    <div id="token={secret}"></div>
    <a name="access_token={secret}"></a>
    <a href="#access_token={secret}">Private fragment</a>
    """

    metadata = extract_html(html, document_url, INTERNAL_HOSTS)
    capture = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=document_url,
        status=200,
        metadata=metadata,
    )
    manifest = dumps_jsonl(
        ManifestProvenance.create(
            generated_at="2026-08-08T06:30:00Z",
            tool_version="legacy-manifest/1",
        ),
        (CompatibilityRow(Classification.preserve(), production_capture=capture),),
    )

    assert secret not in manifest
    assert manifest.count("redacted-sha256-") >= 5
    assert metadata.social_metadata[0][1].startswith("https://datatalks.club/image.png?")
    assert metadata.structured_data[0].identifier.startswith(document_url + "?access_token=")
    assert metadata.client_redirect_url.startswith(
        "https://datatalks.club/new?access_token=redacted-sha256-"
    )
    assert all(fragment.startswith("redacted-sha256-") for fragment in metadata.fragments)
    assert any(
        reference.url.rsplit("#", 1)[-1] in metadata.fragments for reference in metadata.references
    )


def test_json_is_order_independent_and_collects_contract_references() -> None:
    first = extract_json(
        '{"next":"/faq/json/courses.json","@type":"FAQPage","items":[2,1]}',
        "https://datatalks.club/faq/json/index.json",
        INTERNAL_HOSTS,
    )
    second = extract_json(
        '{"items":[2,1],"@type":"FAQPage","next":"/faq/json/courses.json"}',
        "https://datatalks.club/faq/json/index.json",
        INTERNAL_HOSTS,
    )
    assert first == second
    assert first.structured_data == (StructuredData(type="FAQPage"),)
    assert first.references == (
        Reference(
            ReferenceKind.INTERNAL_LINK,
            "https://datatalks.club/faq/json/courses.json",
        ),
    )


def test_json_rejects_invalid_and_nonfinite_documents() -> None:
    with pytest.raises(ExtractionError, match="^invalid_json$"):
        extract_json("{", "https://datatalks.club/feed.json", INTERNAL_HOSTS)
    with pytest.raises(ExtractionError, match="^invalid_json$"):
        extract_json('{"score": NaN}', "https://datatalks.club/feed.json", INTERNAL_HOSTS)


def test_sitemap_extracts_lastmod_and_rejects_entity_declarations() -> None:
    sitemap = extract_sitemap(
        """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://datatalks.club/</loc><lastmod>2026-08-08</lastmod></url>
          <url><loc>/docs/</loc><lastmod>2026-08-07T10:11:12Z</lastmod></url>
        </urlset>
        """,
        "https://datatalks.club/sitemap.xml",
        INTERNAL_HOSTS,
    )
    assert sitemap.entries == (
        SitemapEntry(url="https://datatalks.club/", lastmod="2026-08-08"),
        SitemapEntry(url="https://datatalks.club/docs/", lastmod="2026-08-07T10:11:12Z"),
    )

    with pytest.raises(ExtractionError, match="^unsafe_xml_declaration$"):
        extract_sitemap(
            '<!DOCTYPE x [<!ENTITY x "unsafe">]><urlset/>',
            "https://datatalks.club/sitemap.xml",
            INTERNAL_HOSTS,
        )


def test_text_contract_has_fingerprint_and_robots_sitemap_reference() -> None:
    metadata = extract_text(
        "User-agent: *\nDisallow:\nSitemap: https://datatalks.club/sitemap.xml\n",
        "https://datatalks.club/robots.txt",
        INTERNAL_HOSTS,
    )
    assert len(metadata.main_content_fingerprint) == 64
    assert metadata.references == (
        Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/sitemap.xml"),
    )
