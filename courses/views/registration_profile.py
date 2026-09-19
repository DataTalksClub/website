PROFILE_FIELD_NAMES = (
    "certificate_name",
    "country",
    "region",
    "registration_role",
)


def update_user_profile_from_registration(user, registration):
    from courses.models.learner_profile import ensure_learner_profile

    profile = ensure_learner_profile(user)
    update_fields = []
    profile_values = registration_profile_values(registration)
    for field_name, value in profile_values:
        update_user_profile_field(
            profile,
            update_fields,
            field_name,
            value,
        )
    return profile, update_fields


def registration_profile_values(registration):
    profile_values = []
    certificate_name_value = registration.name.strip()
    certificate_name = (
        "certificate_name",
        certificate_name_value,
    )
    country = ("country", registration.country)
    region = ("region", registration.region)
    registration_role = (
        "registration_role",
        registration.role,
    )
    profile_values.append(certificate_name)
    profile_values.append(country)
    profile_values.append(region)
    profile_values.append(registration_role)
    return profile_values


def update_user_profile_field(profile, update_fields, field_name, value):
    if not value or getattr(profile, field_name) == value:
        return

    setattr(profile, field_name, value)
    update_fields.append(field_name)
