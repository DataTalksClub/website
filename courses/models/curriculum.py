from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from courses.curriculum_source_validators import validate_unit_code_sources

from .curriculum_import import SourceProvenanceModel, source_provenance_constraint


class Module(SourceProvenanceModel):
    """An ordered module in a module-format cohort."""

    cohort = models.ForeignKey(
        "courses.Cohort",
        on_delete=models.CASCADE,
        related_name="modules",
    )
    position = models.PositiveIntegerField()
    slug = models.SlugField()
    title = models.CharField(max_length=200)
    link = models.URLField(
        blank=True,
        help_text="Optional destination for the module heading.",
    )
    terminal_homework = models.OneToOneField(
        "courses.Homework",
        on_delete=models.PROTECT,
        related_name="terminal_module",
    )

    @property
    def homework(self):
        """Compatibility name for the module's terminal homework."""

        return self.terminal_homework

    def clean(self):
        super().clean()
        errors = {}
        if self.cohort_id and self.cohort.curriculum_format != "modules":
            errors["cohort"] = "Only module-format cohorts can publish modules."
        if self.cohort_id and self.terminal_homework_id:
            if self.terminal_homework.course_id != self.cohort_id:
                errors["terminal_homework"] = (
                    "A module's terminal homework must belong to its cohort."
                )
        if errors:
            raise ValidationError(errors)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "position"),
                name="courses_module_cohort_position_unique",
            ),
            models.UniqueConstraint(
                fields=("cohort", "slug"),
                name="courses_module_cohort_slug_unique",
            ),
            source_provenance_constraint(name="courses_module_source_complete"),
            models.UniqueConstraint(
                fields=("cohort", "source_content_id"),
                condition=Q(source_content_id__isnull=False),
                name="courses_module_source_content_uq",
            ),
        ]

    def __str__(self):
        return self.title


class Unit(SourceProvenanceModel):
    """Display-only unit metadata for a module flow."""

    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name="units",
    )
    position = models.PositiveIntegerField()
    slug = models.SlugField()
    title = models.CharField(max_length=200)
    content_markdown = models.TextField(blank=True)
    rendered_html = models.TextField(blank=True)
    link = models.URLField(
        blank=True,
        help_text="Optional destination for the unit.",
    )
    video_url = models.URLField(
        blank=True,
        help_text="Lesson video declared by the course repository frontmatter.",
    )
    code_sources = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_unit_code_sources],  # stable module: see the migration
        help_text="Companion code files declared by the course repository frontmatter.",
    )

    def clean(self):
        super().clean()
        if self.module_id and self.module.cohort.curriculum_format != "modules":
            raise ValidationError("Only module-format cohorts can publish units.")

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("module", "position"),
                name="courses_unit_module_position_unique",
            ),
            models.UniqueConstraint(
                fields=("module", "slug"),
                name="courses_unit_module_slug_unique",
            ),
            source_provenance_constraint(name="courses_unit_source_complete"),
            models.UniqueConstraint(
                fields=("module", "source_content_id"),
                condition=Q(source_content_id__isnull=False),
                name="courses_unit_source_content_uq",
            ),
        ]

    def __str__(self):
        return self.title


class UnitReadState(models.Model):
    """A learner's persistent read marker for one curriculum unit."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="unit_read_states",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="read_states",
    )
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "unit"),
                name="courses_unit_read_state_user_unit_uq",
            ),
        ]

    def __str__(self):
        return f"{self.user} read {self.unit}"


class CurriculumFlowItem(models.Model):
    """A top-level module or project entry in a cohort's learning flow."""

    cohort = models.ForeignKey(
        "courses.Cohort",
        on_delete=models.CASCADE,
        related_name="flow_items",
    )
    position = models.PositiveIntegerField()
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="flow_items",
    )
    project = models.ForeignKey(
        "courses.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="flow_items",
    )

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "position"),
                name="courses_flow_item_cohort_position_unique",
            ),
            models.UniqueConstraint(
                fields=("cohort", "module"),
                condition=Q(module__isnull=False),
                name="courses_flow_item_module_unique",
            ),
            models.UniqueConstraint(
                fields=("cohort", "project"),
                condition=Q(project__isnull=False),
                name="courses_flow_item_project_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(module__isnull=False, project__isnull=True)
                    | Q(module__isnull=True, project__isnull=False)
                ),
                name="courses_flow_item_exactly_one_target",
            ),
        ]

    def __str__(self):
        target = self.module or self.project
        return f"{self.position}: {target}"

    def clean(self):
        super().clean()
        has_module = self.module_id is not None
        has_project = self.project_id is not None
        if has_module == has_project:
            raise ValidationError(
                "A curriculum flow item must target exactly one module or project."
            )
        if not self.cohort_id:
            return
        if self.cohort.curriculum_format != "modules":
            raise ValidationError("Only module-format cohorts can publish a flow.")
        target_cohort_id = (
            self.module.cohort_id if has_module else self.project.course_id
        )
        if target_cohort_id != self.cohort_id:
            raise ValidationError(
                "Curriculum flow targets must belong to the same cohort."
            )

# These aliases make the domain vocabulary explicit at call sites while the
# database model remains the stable CurriculumFlowItem contract.
FlowItem = CurriculumFlowItem
LearningFlowItem = CurriculumFlowItem
