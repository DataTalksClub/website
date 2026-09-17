from django.conf import settings
from loginas.utils import is_impersonated_session


def export_settings(request):
    from courses.models.learner_profile import learner_profile_for, profile_field_default

    dark_mode = False
    if request.user.is_authenticated:
        profile = learner_profile_for(request.user)
        dark_mode = (
            profile.dark_mode if profile is not None else profile_field_default("dark_mode")
        )
    is_impersonating = is_impersonated_session(request)
    return {
        "VERSION": settings.VERSION,
        "DARK_MODE": dark_mode,
        "SHOW_WRAPPED": settings.SHOW_WRAPPED,
        "IS_IMPERSONATING": is_impersonating,
    }
