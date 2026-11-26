"""A member who has already passed the learner-profile gate."""

from django.utils import timezone


def give_confirmed_learner_profile(user, **overrides):
    from courses.models.learner_profile import LearnerProfile

    values = {
        "certificate_name": "Test Learner",
        "country": "Germany",
        "region": "Europe",
        "registration_role": "data_engineer",
        "profile_confirmed_at": timezone.now(),
        **overrides,
    }
    return LearnerProfile.objects.update_or_create(user=user, defaults=values)[0]
