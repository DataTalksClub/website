from django.urls import path, re_path

from events.qna import views as qna_views

from . import legal_views, public_views

urlpatterns = [
    path("terms", legal_views.terms, name="terms"),
    path("terms/", public_views.permanent_public_redirect, {"target": "/terms"}),
    path("privacy", legal_views.privacy, name="privacy"),
    path("privacy/", public_views.permanent_public_redirect, {"target": "/privacy"}),
    path("impressum", legal_views.impressum, name="impressum"),
    path(
        "impressum/",
        public_views.permanent_public_redirect,
        {"target": "/impressum"},
    ),
    path("blog", public_views.collection_hub, {"collection": "articles"}, name="articles"),
    path("articles.html", public_views.permanent_public_redirect, {"target": "/blog"}),
    path("blog/", public_views.permanent_public_redirect, {"target": "/blog"}),
    path(
        "blog/<path:slug>/",
        public_views.permanent_detail_redirect,
        {"collection": "blog"},
    ),
    path(
        "blog/ai-dev-tools-zoomcamp.html",
        public_views.article_detail,
        {"slug": "ai-dev-tools-zoomcamp"},
        name="article-ai-dev-tools",
    ),
    path("blog/<path:slug>.html", public_views.article_detail, name="public-article"),
    path(
        "blog/<path:slug>",
        public_views.permanent_detail_redirect,
        {"collection": "blog"},
    ),
    path("podcast", public_views.podcast_hub, name="podcast"),
    path("podcast.html", public_views.permanent_public_redirect, {"target": "/podcast"}),
    path("podcast/", public_views.permanent_public_redirect, {"target": "/podcast"}),
    path(
        "podcast/s24e05-ai-adoption-in-enterprise-beyond-writing-code.html",
        public_views.permanent_public_redirect,
        {"target": "/podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code"},
        name="podcast-ai-adoption-legacy",
    ),
    path(
        "podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code",
        public_views.podcast_detail,
        {"slug": "s24e05-ai-adoption-in-enterprise-beyond-writing-code"},
        name="podcast-ai-adoption",
    ),
    path(
        "podcast/s24e06/how-to-build-ai-that-actually-ships-in-production",
        public_views.podcast_detail_by_id,
        {
            "episode_id": "s24e06",
            "slug": "how-to-build-ai-that-actually-ships-in-production",
        },
        name="podcast-ai-production",
    ),
    re_path(
        r"^podcast/(?P<episode_id>s[0-9]+e[0-9]+)/(?P<slug>[-a-zA-Z0-9_]+)$",
        public_views.podcast_detail_by_id,
        name="public-podcast-by-id",
    ),
    re_path(
        r"^podcast/(?P<episode_id>s[0-9]+e[0-9]+)$",
        public_views.podcast_detail_by_id_without_slug,
        name="public-podcast-by-id-without-slug",
    ),
    path(
        "podcast/<path:slug>/",
        public_views.permanent_detail_redirect,
        {"collection": "podcast"},
    ),
    path(
        "podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html",
        public_views.podcast_legacy_detail,
        {"slug": "s24e06-how-to-build-ai-that-actually-ships-in-production"},
        name="podcast-ai-production-legacy",
    ),
    path(
        "podcast/<path:slug>.html",
        public_views.podcast_legacy_detail,
        name="public-podcast",
    ),
    path(
        "podcast/<path:slug>",
        public_views.permanent_detail_redirect,
        {"collection": "podcast"},
    ),
    path("books", public_views.collection_hub, {"collection": "books"}, name="books"),
    path("books.html", public_views.permanent_public_redirect, {"target": "/books"}),
    path("books/", public_views.permanent_public_redirect, {"target": "/books"}),
    path(
        "books/<path:slug>/",
        public_views.permanent_detail_redirect,
        {"collection": "books"},
    ),
    path(
        "books/20250922-how-software-fails.html",
        public_views.book_detail,
        {"slug": "20250922-how-software-fails"},
        name="book-how-software-fails",
    ),
    path("books/<path:slug>.html", public_views.book_detail, name="public-book"),
    path(
        "books/<path:slug>",
        public_views.permanent_detail_redirect,
        {"collection": "books"},
    ),
    path(
        "people/<path:slug>/",
        public_views.permanent_detail_redirect,
        {"collection": "people"},
    ),
    path(
        "people/aleksandrkim.html",
        public_views.person_detail,
        {"slug": "aleksandrkim"},
        name="person-aleksandr-kim",
    ),
    path("people/<path:slug>.html", public_views.person_detail, name="public-person"),
    path(
        "people/<path:slug>",
        public_views.permanent_detail_redirect,
        {"collection": "people"},
    ),
    path("events", public_views.events, name="events"),
    path("events.html", public_views.legacy_events_redirect),
    path("events/", public_views.legacy_events_redirect),
    path("events/past", public_views.events_past, name="events-past"),
    path("events/past/", public_views.permanent_public_redirect, {"target": "/events/past"}),
    # The reviewed legacy map stores clean one-segment paths.  Keep the historical trailing-slash
    # spelling as an explicit route so redirect behavior never depends on APPEND_SLASH.
    re_path(
        r"^events/(?P<legacy_path>[^/]+)/$",
        public_views.event_legacy_redirect,
        name="public-event-legacy-trailing-slash",
    ),
    # Event-linked Q&A routes must precede the generic event detail route.  The
    # public numeric ID and current title slug remain the only Event lookup
    # inputs; the slug is cosmetic and stale spellings redirect on HTML only.
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/$",
        qna_views.public_qna,
        name="public-event-qna",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/api/questions/$",
        qna_views.qna_questions,
        name="public-event-qna-questions",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/api/questions/(?P<question_id>[-A-Za-z0-9_]+)/$",
        qna_views.qna_question,
        name="public-event-qna-question",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/api/questions/(?P<question_id>[-A-Za-z0-9_]+)/vote/$",
        qna_views.qna_vote,
        name="public-event-qna-vote",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/cohost/(?P<name>[a-zA-Z0-9-]+)/$",
        qna_views.qna_cohost_gate,
        name="public-event-qna-cohost",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/host/$",
        qna_views.qna_host,
        name="public-event-qna-host",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/present/$",
        qna_views.qna_present,
        name="public-event-qna-present",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/qna/qr\.(?P<kind>svg|png)$",
        qna_views.qna_qr,
        name="public-event-qna-qr",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)$",
        public_views.event_detail,
        name="public-event",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)$",
        public_views.event_detail_without_slug,
        name="public-event-without-slug",
    ),
    re_path(
        r"^events/(?P<event_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(?P<slug>[-a-zA-Z0-9_]+)$",
        public_views.event_detail_legacy_uuid,
        name="public-event-legacy-uuid",
    ),
    re_path(
        r"^events/(?P<event_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        public_views.event_detail_legacy_uuid_without_slug,
        name="public-event-legacy-uuid-without-slug",
    ),
    path(
        "events/<path:legacy_path>",
        public_views.event_legacy_redirect,
        name="public-event-legacy",
    ),
    path("wiki", public_views.wiki_hub, name="wiki-home"),
    path("wiki/", public_views.permanent_public_redirect, {"target": "/wiki"}),
    path("wiki/search-corpus.json", public_views.wiki_search_json, name="wiki-search-json"),
    path("wiki/graph", public_views.wiki_graph, name="wiki-graph"),
    path("wiki/graph/graph.json", public_views.wiki_graph_json, name="wiki-graph-json"),
    path("wiki/special-pages", public_views.wiki_special, name="wiki-special"),
    path(
        "wiki/special-pages/<slug:category>",
        public_views.wiki_special,
        name="wiki-special-category",
    ),
    path("wiki/feed.xml", public_views.wiki_feed, name="wiki-feed"),
    path("wiki/sitemap.xml", public_views.wiki_sitemap, name="wiki-sitemap"),
    path("wiki/robots.txt", public_views.wiki_robots, name="wiki-robots"),
    path("wiki/assets/<path:asset>", public_views.wiki_asset, name="wiki-asset"),
    path("wiki/<path:slug>", public_views.wiki_detail, name="public-wiki"),
    path("images/<path:media_path>", public_views.media, name="public-media"),
]
