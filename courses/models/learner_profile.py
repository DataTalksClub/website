from typing import cast

from django.conf import settings
from django.db import models

#: The registration form's years-of-experience vocabulary
#: (``CourseRegistration.YearsExperience``), repeated here because the
#: registration model imports this module's neighbours, not the reverse.
YEARS_EXPERIENCE_CHOICES = (
    ("lt1", "<1"),
    ("1_3", "1–3"),
    ("3_6", "3–6"),
    ("6plus", "6+"),
)


class LearnerProfile(models.Model):
    """The course-platform person fields, one row per account.

    The columns moved verbatim off the auth user model (plan issue D3.1):
    same max lengths, null/blank rules, choices and help texts, so this is a
    data-preserving move with no field-contract change. The "Member profile
    version 1" contract from the platform spec lands later, with the shared
    package adoption; this model deliberately mirrors today's definitions.

    A user may have no row yet (bulk-seeded accounts): readers fall back to
    the field defaults through :func:`learner_profile_for`, and write paths
    create the row through :func:`ensure_learner_profile`.
    """

    ROLE_CHOICES = (
        ("student", "Student"),
        ("instructor", "Instructor"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="learner_profile",
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="student")
    certificate_name = models.CharField(  # noqa: DJ001 - mirrors the nullable moved column
        verbose_name="Certificate name",
        max_length=255,
        blank=True,
        null=True,
        help_text="Your actual name that will appear on your certificates",
    )
    country = models.CharField(
        verbose_name="Country",
        max_length=100,
        blank=True,
    )
    region = models.CharField(
        verbose_name="Region",
        max_length=100,
        blank=True,
    )
    registration_role = models.CharField(
        verbose_name="Registration role",
        max_length=40,
        blank=True,
        help_text="Role last used on a course registration form",
    )
    github_url = models.URLField(  # noqa: DJ001 - mirrors the nullable moved column
        verbose_name="GitHub URL",
        blank=True,
        null=True,
    )
    linkedin_url = models.URLField(  # noqa: DJ001 - mirrors the nullable moved column
        verbose_name="LinkedIn URL",
        blank=True,
        null=True,
    )
    personal_website_url = models.URLField(  # noqa: DJ001 - mirrors the nullable moved column
        verbose_name="Personal website URL",
        blank=True,
        null=True,
    )
    about_me = models.TextField(  # noqa: DJ001 - mirrors the nullable moved column
        verbose_name="About me",
        blank=True,
        null=True,
    )
    company_name = models.CharField(
        verbose_name="Company",
        max_length=255,
        blank=True,
        help_text="Where you work now; leave empty if you are not employed",
    )
    years_experience = models.CharField(
        verbose_name="Years of experience",
        max_length=10,
        blank=True,
        choices=YEARS_EXPERIENCE_CHOICES,
    )
    profile_confirmed_at = models.DateTimeField(
        verbose_name="Profile confirmed at",
        null=True,
        blank=True,
        help_text=(
            "When the learner last saved or confirmed this profile; course "
            "registration asks for a review once it is older than "
            "LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS"
        ),
    )
    dark_mode = models.BooleanField(
        verbose_name="Dark mode", default=False, help_text="Enable dark mode theme"
    )

    class Meta:
        ordering = ("user_id",)

    def __str__(self):
        return f"learner-profile:{self.user_id}"


def learner_profile_for(user) -> LearnerProfile | None:
    """The user's profile row, or ``None`` when it does not exist yet.

    Readers use this and fall back to the moved field's own default, so an
    account without a row reads exactly as it did when the column lived on
    the user model with its default.
    """

    if user is None or getattr(user, "pk", None) is None:
        return None
    try:
        return user.learner_profile
    except LearnerProfile.DoesNotExist:
        return None


def profile_field_default(field_name: str):
    """The moved field's declared default, for readers without a row."""

    field = cast(models.Field, LearnerProfile._meta.get_field(field_name))
    return field.get_default()


def ensure_learner_profile(user) -> LearnerProfile:
    """The user's profile row, created with defaults when missing.

    Write paths use this so a first profile write on a bulk-seeded account
    creates the row rather than failing.
    """

    profile, _created = LearnerProfile.objects.get_or_create(user=user)
    return profile
