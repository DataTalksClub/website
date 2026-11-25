"""Model-level picture rule for the shared ``events.Event`` row.

``community_base`` owns the ``Event`` model, so the site cannot add a column
constraint or override ``save()``.  This ``pre_save`` receiver refuses saving
an *existing* event that has no picture -- the same rule the PostgreSQL
constraint triggers enforce at commit, here also on SQLite (local development
and the test suite).  A new row is exempt: ``create_event_identity`` writes its
picture right after inserting it, inside the same transaction, before anything
saves the row again.
"""

from __future__ import annotations

from typing import Any

from community_base.events.models import Event
from django.db.models.signals import pre_save
from django.dispatch import receiver

from events.pictures import EventPictureRequired


@receiver(pre_save, sender=Event, dispatch_uid="event_registrants.event_requires_picture")
def event_requires_picture(
    sender: type[Event], instance: Event, raw: bool = False, **kwargs: Any
) -> None:
    del sender, kwargs
    if raw or instance._state.adding or instance.pk is None:
        return
    from .models import EventOperatorConfig

    has_picture = (
        EventOperatorConfig.objects.using(instance._state.db or "default")
        .filter(event_id=instance.pk, banner_image__isnull=False)
        .exclude(banner_image=b"")
        .exists()
    )
    if not has_picture:
        raise EventPictureRequired()
