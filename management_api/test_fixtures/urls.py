from django.urls import path

from . import views

urlpatterns = [
    path("_fixtures/bulk", views.bulk_fixture, name="fixture-bulk"),
    path(
        "_fixtures/operations/<str:operation_id>",
        views.operation_detail_fixture,
        name="fixture-operation-detail",
    ),
    path(
        "_fixtures/operations/<str:operation_id>/cancel",
        views.operation_cancel_fixture,
        name="fixture-operation-cancel",
    ),
    path("_fixtures/credentials", views.create_credential, name="fixture-credential-create"),
    path(
        "_fixtures/credentials/<str:credential_id>/rotate",
        views.rotate_credential,
        name="fixture-credential-rotate",
    ),
    path(
        "_fixtures/credentials/<str:credential_id>/revoke",
        views.revoke_credential_view,
        name="fixture-credential-revoke",
    ),
]
