from django.core.exceptions import PermissionDenied, ValidationError

from courses.models import CurriculumFormat, Module, Unit, UnitReadState


def set_unit_read_state(
    *,
    user,
    module: Module,
    unit: Unit,
    is_read: bool,
) -> bool:
    """Persist a unit's read state and return the resulting state.

    Read markers are idempotent and preserve the timestamp of the first mark.
    Unread is the default state, represented by the absence of a marker row.
    """

    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication is required to update unit read state.")
    if (
        module.cohort.curriculum_format != CurriculumFormat.MODULES
        or unit.module_id != module.id
    ):
        raise ValidationError("The unit does not belong to this module curriculum.")

    if is_read:
        UnitReadState.objects.get_or_create(user=user, unit=unit)
        return True

    UnitReadState.objects.filter(user=user, unit=unit).delete()
    return False
