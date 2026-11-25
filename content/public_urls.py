from django.urls import path, re_path

from event_qna import views as qna_views

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
    path("articles", public_views.permanent_public_redirect, {"target": "/blog"}),
    path("articles/", public_views.permanent_public_redirect, {"target": "/blog"}),
    path("articles.html", public_views.permanent_public_redirect, {"target": "/blog"}),
    path("blog/", public_views.permanent_public_redirect, {"target": "/blog"}),
    path("blog/<path:slug>.html", public_views.article_detail, name="public-article"),
    path(
        "blog/<path:slug>",
        public_views.editorial_detail_alias_redirect,
        {"collection": "article"},
    ),
    path("podcast", public_views.podcast_hub, name="podcast"),
    path("podcast.html", public_views.permanent_public_redirect, {"target": "/podcast"}),
    path("podcast/", public_views.permanent_public_redirect, {"target": "/podcast"}),
    # One season per page; the newest season is the hub itself.  It precedes the
    # editorial alias catch-all, which would otherwise read it as a flat slug.
    re_path(
        r"^podcast/s/(?P<season>[1-9][0-9]{0,3})/$",
        public_views.podcast_hub,
        name="podcast-season",
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
    # Trailing-slash spellings of the episode routes (links with a CDN-added slash exist in
    # the wild) redirect to the canonical in one hop; they precede the editorial alias
    # catch-all, which would otherwise read them as a flat slug and 404.
    re_path(
        r"^podcast/(?P<episode_id>s[0-9]+e[0-9]+)/(?P<slug>[-a-zA-Z0-9_]+)/$",
        public_views.podcast_detail_slash_alias,
        name="public-podcast-by-id-slash-redirect",
    ),
    re_path(
        r"^podcast/(?P<episode_id>s[0-9]+e[0-9]+)/$",
        public_views.podcast_detail_slash_alias,
        name="public-podcast-by-id-without-slug-slash-redirect",
    ),
    path(
        "podcast/<path:slug>",
        public_views.editorial_detail_alias_redirect,
        {"collection": "podcast"},
    ),
    path("books", public_views.collection_hub, {"collection": "books"}, name="books"),
    path("books.html", public_views.permanent_public_redirect, {"target": "/books"}),
    path("books/", public_views.permanent_public_redirect, {"target": "/books"}),
    path(
        "books/20250922-how-software-fails.html",
        public_views.book_detail,
        {"slug": "20250922-how-software-fails"},
        name="book-how-software-fails",
    ),
    path("books/<path:slug>.html", public_views.book_detail, name="public-book"),
    path(
        "books/<path:slug>",
        public_views.editorial_detail_alias_redirect,
        {"collection": "book"},
    ),
    path("people", public_views.collection_hub, {"collection": "people"}, name="people"),
    path("people.html", public_views.permanent_public_redirect, {"target": "/people"}),
    path("people/", public_views.permanent_public_redirect, {"target": "/people"}),
    path(
        "people/aleksandrkim.html",
        public_views.person_detail,
        {"slug": "aleksandrkim"},
        name="person-aleksandr-kim",
    ),
    path("people/<path:slug>.html", public_views.person_detail, name="public-person"),
    path(
        "people/<path:slug>",
        public_views.editorial_detail_alias_redirect,
        {"collection": "person"},
    ),
    path("events", public_views.events, name="events"),
    path("events.html", public_views.legacy_events_redirect),
    path("events/", public_views.legacy_events_redirect),
    path("events/past", public_views.events_past, name="events-past"),
    path("events/past/", public_views.permanent_public_redirect, {"target": "/events/past"}),
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
        r"^events/(?P<event_id>[1-9][0-9]*)/banner$",
        public_views.event_banner,
        name="public-event-banner",
    ),
    path(
        "events/registration/verify",
        public_views.event_registration_verify,
        name="public-event-registration-verify",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/register/resend$",
        public_views.event_register_resend,
        name="public-event-register-resend",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/register$",
        public_views.event_register,
        name="public-event-register",
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
    # The trailing-slash spellings of the two detail routes above are one-hop permanent
    # redirects to the numeric/current-slug canonical (links with a CDN-added slash exist in
    # the wild).  They never render, so the canonical stays the only 200.
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/(?P<slug>[-a-zA-Z0-9_]+)/$",
        public_views.event_detail_slash_alias,
        name="public-event-slash-redirect",
    ),
    re_path(
        r"^events/(?P<event_id>[1-9][0-9]*)/$",
        public_views.event_detail_slash_alias,
        name="public-event-without-slug-slash-redirect",
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
    # One trailing slash on a rendered Wiki page redirects to it in one hop; declared ahead
    # of the editorial detail catch-all, which would otherwise read the slash as a slug.
    re_path(
        r"^wiki/(?P<slug>.+?)/$",
        public_views.wiki_slash_alias,
        name="public-wiki-slash-redirect",
    ),
    path("wiki/<path:slug>", public_views.wiki_detail, name="public-wiki"),
    path("tools", public_views.permanent_public_redirect, {"target": "/wiki"}),
    path("tools/", public_views.permanent_public_redirect, {"target": "/wiki"}),
    path("tools/modelstore.html", public_views.permanent_public_redirect, {"target": "/wiki"}),
    path("tools/obsei.html", public_views.permanent_public_redirect, {"target": "/wiki"}),
    path(
        "conferences/2021-feb.html",
        public_views.permanent_public_redirect,
        {"target": "/events/past"},
    ),
    path(
        "conferences/2021-summer-marathon.html",
        public_views.permanent_public_redirect,
        {"target": "/events/past"},
    ),
    path("conferences", public_views.permanent_public_redirect, {"target": "/events/past"}),
    path("conferences/", public_views.permanent_public_redirect, {"target": "/events/past"}),
    path("support", public_views.permanent_public_redirect, {"target": "/slack"}),
    path("support/", public_views.permanent_public_redirect, {"target": "/slack"}),
    path("podwiki", public_views.podwiki_redirect, name="podwiki-home-redirect"),
    path("podwiki/", public_views.podwiki_redirect, name="podwiki-slash-redirect"),
    path("podwiki/<path:rest>", public_views.podwiki_redirect, name="podwiki-redirect"),
    path("images/<path:media_path>", public_views.media, name="public-media"),
]
