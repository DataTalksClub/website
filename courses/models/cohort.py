import re
import uuid

from django.db import models
from django.db.models import Q

from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from accounts.models import CustomUser

from courses.course_family_catalog import canonical_family_slug
from courses.random_names import generate_random_name

from .curriculum_import import (
    SOURCE_STABLE_ID_PATTERN,
    SourceProvenanceModel,
    source_provenance_constraint,
    source_stable_id_validator,
)

User = CustomUser


class CurriculumFormat(models.TextChoices):
    LEGACY = "legacy", "Legacy"
    MODULES = "modules", "Modules"


class Course(SourceProvenanceModel):
    """Reusable course family shared by one or more dated cohorts."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True, blank=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    outcome = models.TextField(blank=True, default="")
    github_repo_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="Optional repository shared by the course family.",
    )
    docs_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="Optional documentation shared by the course family.",
    )
    faq_document_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="Optional FAQ shared by the course family.",
    )
    social_media_hashtag = models.CharField(
        max_length=100,
        blank=True,
        help_text="The hashtag associated with the course family.",
    )
    visible = models.BooleanField(default=True)
    source_stable_id = models.CharField(  # noqa: DJ001 -- null identifies DB-managed rows.
        max_length=128,
        null=True,
        blank=True,
        validators=[source_stable_id_validator],
    )

    SOURCE_IDENTITY_FIELDS = ("source_content_id", "source_stable_id")

    def __str__(self):
        return self.title

    class Meta:
        db_table = "courses_course_family"
        constraints = [
            source_provenance_constraint(
                name="courses_course_source_complete",
                identity_fields=("source_content_id", "source_stable_id"),
            ),
            models.UniqueConstraint(
                fields=("source_content_id",),
                condition=Q(source_content_id__isnull=False),
                name="courses_course_source_content_uq",
            ),
            models.UniqueConstraint(
                fields=("source_stable_id",),
                condition=Q(source_stable_id__isnull=False),
                name="courses_course_source_stable_uq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_stable_id__isnull=True)
                    | Q(source_stable_id__regex=SOURCE_STABLE_ID_PATTERN)
                ),
                name="courses_course_source_stable_ck",
            ),
        ]


class Cohort(SourceProvenanceModel):
    """One dated delivery of a reusable :class:`Course` family."""

    CurriculumFormat = CurriculumFormat

    slug = models.SlugField(unique=True, blank=False)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="cohorts",
    )
    identifier = models.SlugField(
        max_length=80,
        blank=True,
        default="",
        help_text=(
            "Stable public identifier for this cohort, such as '2026' "
            "or 'spring-2026'."
        ),
    )
    year = models.PositiveIntegerField(default=2026)
    title = models.CharField(max_length=200)
    curriculum_format = models.CharField(
        max_length=7,
        choices=CurriculumFormat.choices,
        default=CurriculumFormat.LEGACY,
        db_default=CurriculumFormat.LEGACY,
        help_text="The curriculum presentation used by this cohort.",
    )

    description = models.TextField()
    outcome = models.TextField(blank=True, default="", db_default="")
    # How this cohort is delivered and what it promises, as the pages that
    # advertise it print them. These used to be page-owned constants in
    # core/home_content.py, which is how two generations of copy describing a
    # different cohort shipped on the homepage: nothing tied the words to the
    # cohort they claimed to describe. A cohort that has not been given promo
    # copy has none, and the panel simply omits it.
    delivery_format = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text="How this cohort is delivered, such as 'Online'.",
    )
    promo_summary = models.TextField(
        blank=True,
        default="",
        db_default="",
        help_text="One-sentence summary shown where this cohort is advertised.",
    )
    start_date = models.DateField(
        blank=True,
        null=True,
        help_text="The public start date for the course.",
    )
    end_date = models.DateField(
        blank=True,
        null=True,
        help_text="The public end date for the course.",
    )
    registration_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="Optional external registration page for the course.",
    )
    github_repo_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="Optional GitHub repository URL for the course.",
    )
    students = models.ManyToManyField(
        User, through="Enrollment", related_name="courses_enrolled"
    )

    social_media_hashtag = models.CharField(
        max_length=100,
        blank=True,
        help_text="The hashtag associated with the course for social media use.",
    )

    first_homework_scored = models.BooleanField(
        default=False,
        blank=False,
        help_text="Whether the first homework has been scored. "
        + "We use that for deciding whether to show the leaderboard.",
    )

    finished = models.BooleanField(
        default=False,
        blank=False,
        help_text="Whether the course has finished.",
    )

    faq_document_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
        help_text="The URL of the FAQ document for the course.",
    )

    min_projects_to_pass = models.IntegerField(
        default=1,
        blank=False,
        help_text="The minimum number of projects to pass the course.",
    )

    homework_problems_comments_field = models.BooleanField(
        default=False,
        help_text="Include field for problems and comments in homework",
    )

    project_passing_score = models.IntegerField(
        default=0,
        help_text="Minimum score required to pass any project in this course",
    )

    visible = models.BooleanField(
        default=True,
        blank=False,
        help_text="Whether the course is visible in the course list. "
        + "Non-visible courses are still accessible via direct link.",
    )

    def __str__(self):
        return self.title

    @property
    def canonical_url_path(self) -> str:
        return f"/courses/{self.course.slug}/{self.identifier}"

    def save(self, *args, **kwargs):
        # Existing copied fixtures create Cohort rows directly.  Keep that
        # construction ergonomic while the persisted schema remains a
        # required Course -> Cohort relationship.  The production/local seed
        # uses the reviewed mapping in course_family_catalog.py explicitly.
        if self.course_id is None:
            # Year stripping alone would mint ``ai-dev-tools-zoomcamp`` beside the
            # published ``ai-dev-tools`` family.  Resolve the reviewed slug so no
            # writer can create a second family for one course.
            family_slug = canonical_family_slug(re.sub(r"-\d{4}$", "", self.slug))
            family_title = re.sub(r"\s+\d{4}$", "", self.title).strip()
            family, _ = Course.objects.get_or_create(
                slug=family_slug,
                defaults={"title": family_title or family_slug.replace("-", " ").title()},
            )
            self.course = family
            match = re.search(r"(?:-|\s)(\d{4})$", self.slug or self.title)
            if match and self.year == 2026:
                self.year = int(match.group(1))
        if not self.identifier:
            self.identifier = str(self.year)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()

        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError(
                    {
                        "end_date": (
                            "End date cannot be earlier than start date."
                        )
                    }
                )

    class Meta:
        db_table = "courses_course"
        constraints = [
            models.UniqueConstraint(
                fields=("course", "year"),
                name="courses_cohort_course_year_unique",
            ),
            models.UniqueConstraint(
                fields=("course", "identifier"),
                name="courses_cohort_course_identifier_unique",
            ),
            models.CheckConstraint(
                condition=Q(curriculum_format__in=CurriculumFormat.values),
                name="courses_cohort_curriculum_format_valid",
            ),
            source_provenance_constraint(name="courses_cohort_source_complete"),
            models.UniqueConstraint(
                fields=("course", "source_content_id"),
                condition=Q(source_content_id__isnull=False),
                name="courses_cohort_source_content_uq",
            ),
        ]


class RegistrationCampaign(models.Model):
    slug = models.SlugField(unique=True, blank=False)
    title = models.CharField(max_length=200)
    edition_label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Displayed cohort label, for example '2026 cohort'.",
    )
    current_course = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registration_campaigns",
        help_text="Course edition currently promoted by this form.",
    )
    is_active = models.BooleanField(default=True)

    registration_baseline_cohort = models.ForeignKey(
        Cohort,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=(
            "The cohort the baseline count below was recorded for. A "
            "campaign that has rotated to promote a different cohort since "
            "no longer applies it -- a baseline recorded for a finished "
            "edition never carries onto whatever cohort registers next."
        ),
    )
    registration_baseline_count = models.PositiveIntegerField(
        default=0,
        db_default=0,
        help_text=(
            "Registrations that happened before this campaign's Course"
            "Registration rows existed (a one-time recorded historical "
            "figure, e.g. from a legacy CMP export). Zero for a campaign "
            "whose registrations are all native rows."
        ),
    )
    registration_native_start_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When native CourseRegistration rows became the complete "
            "record for this campaign. Only registrations at or after "
            "this instant are added to the baseline above; unset when "
            "there is no baseline to protect against double-counting."
        ),
    )

    marketing_markdown = models.TextField(blank=True)
    meta_description = models.TextField(blank=True)
    hero_image_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
    )
    video_url = models.URLField(
        blank=True,
        validators=[URLValidator()],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "slug"]

    def __str__(self):
        return self.title


class CourseRegistration(models.Model):
    class Role(models.TextChoices):
        DATA_ENGINEER = "data_engineer", "Data Engineer"
        DATA_SCIENTIST = "data_scientist", "Data Scientist"
        DATA_ANALYST = "data_analyst", "Data Analyst"
        ML_ENGINEER = "ml_engineer", "ML Engineer"
        SOFTWARE_ENGINEER_BACKEND = (
            "software_engineer_backend",
            "Software Engineer (Backend)",
        )
        SOFTWARE_ENGINEER_OTHER = (
            "software_engineer_other",
            "Software Engineer (Frontend, Test, etc)",
        )
        STUDENT_STEM = "student_stem", "Student (STEM)"
        STUDENT_NON_STEM = "student_non_stem", "Student (Non-STEM)"
        OTHER = "other", "Other"

    campaign = models.ForeignKey(
        RegistrationCampaign,
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    course = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registrations",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="course_registrations",
    )

    email = models.EmailField()
    email_normalized = models.EmailField(editable=False)
    name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=100)
    region = models.CharField(max_length=100)
    role = models.CharField(max_length=40, choices=Role.choices)
    comment = models.TextField(blank=True)
    accepted_newsletter = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["campaign", "email_normalized"]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        raw_email = self.email or ""
        email_stripped = raw_email.strip()
        self.email_normalized = email_stripped.lower()
        if self.campaign and self.course_id is None:
            self.course = self.campaign.current_course
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.email_normalized} registered for {self.campaign}"


class Enrollment(models.Model):
    class Meta:
        unique_together = ["student", "course"]

    student = models.ForeignKey(User, on_delete=models.CASCADE)
    course = models.ForeignKey(Cohort, on_delete=models.CASCADE)
    enrollment_date = models.DateTimeField(auto_now_add=True)

    display_name = models.CharField(
        verbose_name="Leaderboard name", max_length=255, blank=True,
        help_text="Name on the leaderboard"
    )
    display_on_leaderboard = models.BooleanField(default=True)
    display_public_profile = models.BooleanField(default=False)

    position_on_leaderboard = models.IntegerField(
        blank=True, null=True, default=None
    )

    certificate_name = models.CharField(
        verbose_name="Certificate name",
        max_length=255,
        blank=True,
        null=True,
        help_text="Your actual name that will appear on your certificate"
    )

    total_score = models.IntegerField(default=0)

    certificate_url = models.CharField(
        max_length=255, null=True, blank=True
    )

    disable_learning_in_public = models.BooleanField(
        default=False,
        verbose_name="Disable learning in public",
        help_text="When enabled, all learning in public scores are removed and future submissions are not counted"
    )

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = generate_random_name()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} enrolled in {self.course}"


class LeaderboardComplaint(models.Model):
    class IssueType(models.TextChoices):
        LEARNING_IN_PUBLIC = (
            "learning_in_public",
            "Incorrect learning in public links",
        )
        HOMEWORK = "homework", "Incorrect homework"
        PROJECT = "project", "Incorrect project"
        OTHER = "other", "Other leaderboard issue"

    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="complaints",
    )
    reporter = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leaderboard_complaints",
    )
    issue_type = models.CharField(
        max_length=32,
        choices=IssueType.choices,
    )
    description = models.TextField()
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_leaderboard_complaints",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["resolved", "-created_at"]

    def __str__(self):
        return (
            f"{self.get_issue_type_display()} for "
            f"{self.enrollment.display_name}"
        )


class CohortBuildItem(models.Model):
    """One thing a learner ends up holding at the end of a cohort module.

    The featured-cohort panel lists these in order under "What you'll build".
    They are rows rather than a list in code because they describe a specific
    cohort's specific modules, and the previous page-owned tuple could not say
    which cohort it belonged to -- so it kept outliving the cohort it described.
    """

    cohort = models.ForeignKey(
        Cohort, on_delete=models.CASCADE, related_name="build_items"
    )
    text = models.CharField(max_length=500)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("cohort_id", "position")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "position"), name="courses_cohort_build_item_position_unique"
            ),
            models.CheckConstraint(
                condition=Q(text__gt=""), name="courses_cohort_build_item_text_nonempty"
            ),
        ]

    def __str__(self) -> str:
        return self.text
