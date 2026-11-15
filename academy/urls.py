from django.urls import path

from content.public_views import permanent_public_redirect

from . import views

urlpatterns = [
    path("academy/", views.home, name="academy-home"),
    path(
        "academy",
        permanent_public_redirect,
        {"target": "/academy/"},
        name="academy-home-slash-redirect",
    ),
    path(
        "academy/agentic-engineering/",
        views.agentic_engineering,
        name="academy-agentic-engineering",
    ),
    path(
        "academy/agentic-engineering",
        permanent_public_redirect,
        {"target": "/academy/agentic-engineering/"},
        name="academy-agentic-engineering-slash-redirect",
    ),
    path(
        "academy/ai-engineering/",
        views.ai_engineering,
        name="academy-ai-engineering",
    ),
    path(
        "academy/ai-engineering",
        permanent_public_redirect,
        {"target": "/academy/ai-engineering/"},
        name="academy-ai-engineering-slash-redirect",
    ),
    path("academy/sessions/", views.sessions, name="academy-sessions"),
    path(
        "academy/sessions",
        permanent_public_redirect,
        {"target": "/academy/sessions/"},
        name="academy-sessions-slash-redirect",
    ),
    path(
        "academy/sessions/materials/",
        views.sessions_materials,
        name="academy-sessions-materials",
    ),
    path(
        "academy/sessions/materials",
        permanent_public_redirect,
        {"target": "/academy/sessions/materials/"},
        name="academy-sessions-materials-slash-redirect",
    ),
    path("academy/sessions/<slug:slug>.ics", views.session_ics, name="academy-session-ics"),
    path("academy/confirm/", views.confirm, name="academy-confirm"),
    path(
        "academy/confirm",
        permanent_public_redirect,
        {"target": "/academy/confirm/"},
        name="academy-confirm-slash-redirect",
    ),
    path(
        "academy/layoff-proof/",
        views.layoff_proof_redirect,
        name="academy-layoff-proof-redirect",
    ),
    path(
        "academy/layoff-proof",
        views.layoff_proof_redirect,
        name="academy-layoff-proof-slash-redirect",
    ),
    path(
        "academy/privacy/",
        permanent_public_redirect,
        {"target": "/privacy"},
        name="academy-privacy-redirect",
    ),
    path(
        "academy/privacy",
        permanent_public_redirect,
        {"target": "/privacy"},
        name="academy-privacy-slash-redirect",
    ),
    path(
        "academy/impressum/",
        permanent_public_redirect,
        {"target": "/impressum"},
        name="academy-impressum-redirect",
    ),
    path(
        "academy/impressum",
        permanent_public_redirect,
        {"target": "/impressum"},
        name="academy-impressum-slash-redirect",
    ),
]
