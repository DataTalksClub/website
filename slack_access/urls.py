from django.urls import path

from . import views

app_name = "slack-access"
urlpatterns = [
    path("", views.application, name="application"),
    path("vouch/<uuid:key>/", views.vouch, name="vouch"),
    path("invite/", views.member_invite, name="invite"),
    path("redeem/<str:token>/", views.redeem, name="redeem"),
]
