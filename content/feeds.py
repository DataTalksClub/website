"""The site's ten most recent blog posts, matching the public Atom subscription."""

from datetime import UTC, datetime

from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils.feedgenerator import Atom1Feed
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe

from core.runtime_config import get_str_setting

from . import catalogue
from .article_content import article_public_path, prose_sections


class ArticleAtomFeed(Atom1Feed):
    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, {**item, "description": None})
        handler.addQuickElement("content", item["body"], {"type": "html", "xml:base": item["link"]})


@require_safe
@cache_control(max_age=0, must_revalidate=True)
def site_feed(request: HttpRequest) -> HttpResponse:
    origin = get_str_setting("site.origin.canonical").rstrip("/")
    feed = ArticleAtomFeed(
        title="DataTalks.Club",
        link=f"{origin}/",
        description="The latest DataTalks.Club blog posts",
        feed_url=f"{origin}/feed.xml",
        feed_guid=f"{origin}/feed.xml",
        author_name="DataTalks.Club",
    )
    for article in catalogue.articles()[:10]:
        link = f"{origin}{article_public_path(article)}"
        published = datetime.fromisoformat(article["published"][:10]).replace(tzinfo=UTC)
        feed.add_item(
            title=article["title"],
            link=link,
            # Retain existing subscriber identities (the static feed omits .html).
            unique_id=link.removesuffix(".html"),
            description="",
            pubdate=published,
            updateddate=published,
            body=render_to_string(
                "public/_prose_blocks.html",
                {"sections": prose_sections(article.get("blocks", ()))},
            ),
        )
    return HttpResponse(
        feed.writeString("utf-8"), content_type="application/atom+xml; charset=utf-8"
    )
