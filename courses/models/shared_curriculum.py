"""The one current shared curriculum graph per course family.

A schema-2 course repository imports into these rows: one graph of shared
modules and lessons that every delivery references, per-cohort placement rows
that bind optional terminal homework, and the read state of shared lessons.
Stable source content IDs are the identity -- positions and titles may change
only through an explicit import/alias check, and an archive cohort never
creates shared rows.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from courses.curriculum_source_validators import validate_unit_code_sources

from .curriculum_import import SourceProvenanceModel, source_provenance_constraint


class SharedCurriculum(SourceProvenanceModel):
    """The one current graph for one :class:`~courses.models.Course`.

    This is not a version selector: there is no public historical route and no
    release viewer.  A replaced import leaves its previous rows in place for
    rollback/audit until a reviewed retention policy removes them, but no
    route ever selects them.
    """

    course = models.OneToOneField(
        "courses.Course",
        on_delete=models.PROTECT,
        related_name="shared_curriculum",
    )
    parser_version = models.CharField(max_length=128)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            source_provenance_constraint(name="courses_shared_curriculum_source_complete"),
        ]

    def __str__(self) -> str:
        return f"current shared curriculum for {self.course}"


class SharedModule(SourceProvenanceModel):
    """One numbered root module of the current graph, stored once."""

    curriculum = models.ForeignKey(
        SharedCurriculum,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    position = models.PositiveIntegerField()
    slug = models.SlugField(max_length=100)
    title = models.CharField(max_length=200)
    # Optional module README index, imported only into this separate overview
    # field.  It is never a lesson and never a fallback for a missing lesson.
    overview_markdown = models.TextField(blank=True, default="")
    overview_rendered_html = models.TextField(blank=True, default="")
    summary = models.CharField(max_length=500, blank=True, default="")
    published = models.BooleanField(default=True)
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when a source import removes this module from the current "
            "graph. Retired rows are absent from navigation but retained while "
            "learner progress or a route alias requires them."
        ),
    )

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("curriculum", "slug"),
                name="courses_shared_module_curriculum_slug_uq",
            ),
            models.UniqueConstraint(
                fields=("curriculum", "position"),
                name="courses_shared_module_curriculum_position_uq",
            ),
            models.UniqueConstraint(
                fields=("curriculum", "source_content_id"),
                condition=Q(source_content_id__isnull=False),
                name="courses_shared_module_source_content_uq",
            ),
            source_provenance_constraint(name="courses_shared_module_source_complete"),
        ]

    def __str__(self) -> str:
        return self.title


class SharedLesson(SourceProvenanceModel):
    """One numbered lesson of a shared module, stored once per course."""

    module = models.ForeignKey(
        SharedModule,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    position = models.PositiveIntegerField()
    slug = models.SlugField(max_length=100)
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=500, blank=True, default="")
    content_markdown = models.TextField(blank=True)
    rendered_html = models.TextField(blank=True)
    video_url = models.URLField(blank=True)
    code_sources = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_unit_code_sources],
    )
    published = models.BooleanField(default=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("module", "slug"),
                name="courses_shared_lesson_module_slug_uq",
            ),
            models.UniqueConstraint(
                fields=("module", "position"),
                name="courses_shared_lesson_module_position_uq",
            ),
            models.UniqueConstraint(
                fields=("module", "source_content_id"),
                condition=Q(source_content_id__isnull=False),
                name="courses_shared_lesson_source_content_uq",
            ),
            source_provenance_constraint(name="courses_shared_lesson_source_complete"),
        ]

    def __str__(self) -> str:
        return self.title


class CohortSharedModule(models.Model):
    """One cohort's delivery placement of one shared module.

    A placement binds optional terminal homework by stable IDs.  Assessment
    ownership stays cohort-specific: the homework row belongs to the cohort,
    and a shared lesson reaches it only through this explicit mapping.
    """

    cohort = models.ForeignKey(
        "courses.Cohort",
        on_delete=models.CASCADE,
        related_name="shared_module_placements",
    )
    shared_module = models.ForeignKey(
        SharedModule,
        on_delete=models.CASCADE,
        related_name="placements",
    )
    position = models.PositiveIntegerField()
    terminal_homework = models.ForeignKey(
        "courses.Homework",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shared_module_placements",
    )

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "shared_module"),
                name="courses_cohort_shared_module_pair_uq",
            ),
            models.UniqueConstraint(
                fields=("cohort", "position"),
                name="courses_cohort_shared_module_position_uq",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.cohort_id and self.shared_module_id:
            if self.cohort.course_id != self.shared_module.curriculum.course_id:
                errors["shared_module"] = (
                    "A placement must reference a module of the cohort's own course."
                )
            if self.cohort.delivery_mode == "self_paced" and self.terminal_homework_id:
                errors["terminal_homework"] = (
                    "A self-paced placement has no homework in this slice."
                )
        if self.terminal_homework_id:
            if not self.cohort_id:
                errors["terminal_homework"] = "A terminal homework requires a cohort."
            elif self.terminal_homework.course_id != self.cohort_id:
                errors["terminal_homework"] = (
                    "A placement's terminal homework must belong to its cohort."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.cohort} -> {self.shared_module}"


class SharedLessonReadState(models.Model):
    """A learner's persistent read marker for one shared lesson."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_lesson_read_states",
    )
    shared_lesson = models.ForeignKey(
        SharedLesson,
        on_delete=models.CASCADE,
        related_name="read_states",
    )
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "shared_lesson"),
                name="courses_shared_lesson_read_state_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} read {self.shared_lesson}"


class SharedCurriculumAsset(SourceProvenanceModel):
    """One managed current asset (image or declared code file) of the graph.

    The storage key embeds the source stable ID, the full commit SHA, and the
    checksum, so an import can never overwrite bytes that are currently being
    served.  The stable public path is a database lookup, never a GitHub
    branch URL, and the asset endpoint reads only this row and the managed
    store -- never GitHub during a request.
    """

    lesson = models.ForeignKey(
        SharedLesson,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    # ``source_path`` (the repository-relative origin) comes from the
    # provenance base class and is part of its all-or-nothing provenance set;
    # so does ``source_checksum``.
    public_path = models.CharField(max_length=1024, unique=True)
    storage_key = models.CharField(max_length=1024, unique=True)
    content_type = models.CharField(max_length=128, blank=True, default="")
    byte_size = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("lesson", "source_path"),
                name="courses_shared_curriculum_asset_source_uq",
            ),
            models.CheckConstraint(
                condition=Q(byte_size__gte=0),
                name="courses_shared_curriculum_asset_size_ck",
            ),
        ]

    def __str__(self) -> str:
        return self.public_path


class CurriculumRouteAlias(models.Model):
    """One explicit old-path alias reviewed before any route code moved.

    Its target is exactly one of a shared module, a shared lesson, or a GitHub
    archive destination attached to a cohort.  Nothing infers an alias from
    titles or numeric positions, and an old path may never be reused for
    different content.
    """

    class TargetKind(models.TextChoices):
        SHARED_MODULE = "shared_module", "Shared module"
        SHARED_LESSON = "shared_lesson", "Shared lesson"
        GITHUB_ARCHIVE = "github_archive", "GitHub archive"

    old_path = models.CharField(max_length=1024, unique=True)
    target_kind = models.CharField(max_length=20, choices=TargetKind.choices)
    shared_module = models.ForeignKey(
        SharedModule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="route_aliases",
    )
    shared_lesson = models.ForeignKey(
        SharedLesson,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="route_aliases",
    )
    archive_cohort = models.ForeignKey(
        "courses.Cohort",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="archive_route_aliases",
    )
    # For the archive case only: a validated repository-relative path under
    # that cohort's archive.  The HTTPS GitHub URL is derived from the trusted
    # repository identity and imported commit, never stored as arbitrary
    # external input.
    archive_path = models.CharField(max_length=1024, blank=True, default="")
    reason = models.CharField(max_length=500)
    source_commit_sha = models.CharField(max_length=40)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(target_kind="shared_module", shared_module__isnull=False)
                    | Q(target_kind="shared_lesson", shared_lesson__isnull=False)
                    | Q(
                        target_kind="github_archive",
                        archive_cohort__isnull=False,
                        archive_path__gt="",
                    )
                ),
                name="courses_curriculum_route_alias_target_ck",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        has_module = self.shared_module_id is not None
        has_lesson = self.shared_lesson_id is not None
        has_archive = self.archive_cohort_id is not None
        chosen = sum([has_module, has_lesson, has_archive])
        if chosen > 1:
            errors["target_kind"] = "An alias targets exactly one destination."
        if has_archive and not self.archive_path:
            errors["archive_path"] = "An archive alias needs its repository-relative path."
        if not self.reason:
            errors["reason"] = "An alias records why it exists."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.old_path} -> {self.target_kind}"
