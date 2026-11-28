from django.urls import path, re_path

from content.public_views import permanent_public_redirect

from . import views

app_name = "academy"
urlpatterns = [
    path("", views.page, name="home"),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("layoff-proof/", views.layoff_proof, name="layoff_proof"),
    # The name search answers give the comparison of German AI bootcamps.
    path(
        "ai-courses-germany/",
        permanent_public_redirect,
        {"target": "/academy/guides/ai-bootcamps-germany-experienced-developers/"},
    ),
    re_path(
        r"^(?P<name>(?:og|qr)/[a-z0-9-]+\.(?:png|svg))$", views.legacy_image, name="legacy_image"
    ),
    re_path(r"^sessions/(?P<name>[a-z0-9][a-z0-9-]*)\.ics$", views.calendar, name="calendar"),
    re_path(r"^(?P<path>(?:[a-z0-9][a-z0-9-]*/){1,2})$", views.page, name="page"),
]
