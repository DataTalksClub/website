from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import APIPrincipal


@receiver(m2m_changed, sender=APIPrincipal.permissions.through)
def lock_principal_permission_changes(
    sender: object,
    instance: APIPrincipal,
    action: str,
    **kwargs: object,
) -> None:
    del sender, kwargs
    if action in {"pre_add", "pre_remove", "pre_clear"} and instance.pk is not None:
        APIPrincipal.objects.select_for_update().get(pk=instance.pk)
