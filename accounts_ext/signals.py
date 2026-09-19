"""The save-path invariant for the moved identity reconciliation columns.

``User.save`` used to write
``normalized_email == normalize_account_email(user.email)`` on the user row.
The column now lives on ``accounts_ext.IdentityState``, so the same invariant
is kept here, on every path that persists a user email write:

- a full ``save()`` (``update_fields`` is ``None``) syncs the row, exactly as
  the old override wrote the column;
- an ``update_fields`` save that names ``email`` syncs the row, as the old
  override appended ``normalized_email`` to the persisted fields;
- any other ``update_fields`` save did not persist ``normalized_email``
  before and does not touch the row now;
- ``queryset.update()`` and ``bulk_update()`` bypassed the old override and
  bypass this receiver, unchanged;
- fixture loading (``raw=True``) never syncs.
"""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.identity_values import normalize_account_email
from accounts_ext.models import IdentityState


def _sync_identity_state(user, *, created: bool) -> None:
    normalized_email = normalize_account_email(user.email)
    if created:
        IdentityState.objects.create(
            user=user,
            normalized_email=normalized_email,
        )
        return
    # ``update_or_create`` rather than ``filter().update()``: a user created
    # by a path that predates the row (bulk-created accounts) gets its row
    # here on the first ORM save, not a silent zero-row UPDATE.
    IdentityState.objects.update_or_create(
        user=user,
        defaults={"normalized_email": normalized_email},
    )


@receiver(post_save, sender=get_user_model())
def sync_identity_state(sender, instance, created, **kwargs):
    if kwargs.get("raw"):
        return
    update_fields = kwargs.get("update_fields")
    if not created and update_fields is not None and "email" not in update_fields:
        # Mirror of the old ``User.save`` persistence rule: a partial
        # save that does not carry the email never persisted the normalized
        # column either.
        return
    _sync_identity_state(instance, created=created)
