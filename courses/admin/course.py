from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget

from courses.models.cohort import (
    Cohort,
    CourseRegistration,
    LeaderboardComplaint,
    RegistrationCampaign,
)
from courses.leaderboard import update_leaderboard
from courses.models.curriculum import CurriculumFlowItem, Module, Unit
from courses.models.homework import Homework, Question
from courses.models.project import (
    Project,
    ProjectCriteriaAssignment,
    ReviewCriteria,
    criteria_for_project,
)
from courses.validators.criteria_validators import (
    validate_review_criteria_options,
)


CRITERIA_DESCRIPTION_WIDGET = UnfoldAdminTextInputWidget(
    attrs={"size": "60"}
)
CRITERIA_OPTIONS_WIDGET = UnfoldAdminTextareaWidget(
    attrs={"cols": 60, "rows": 4}
)


class CriteriaForm(forms.ModelForm):
    class Meta:
        model = ReviewCriteria
        fields = "__all__"
        widgets = {
            "description": CRITERIA_DESCRIPTION_WIDGET,
            "options": CRITERIA_OPTIONS_WIDGET,
        }

    def clean_options(self):
        """Validate the options field to ensure it has the correct structure."""
        options = self.cleaned_data.get("options")

        if options is None:
            raise ValidationError("Options field cannot be empty.")

        try:
            validate_review_criteria_options(options)
        except ValidationError as exc:
            error_messages = getattr(exc, "messages", None)
            if error_messages:
                error_msg = error_messages[0]
            else:
                error_msg = str(exc)

            raise ValidationError(
                f"Invalid options format. {error_msg}\n\n"
                f"Expected format:\n"
                f'[{{"criteria": "Poor", "score": 0}}, '
                f'{{"criteria": "Good", "score": 1}}, ...]'
            ) from exc

        return options


class CriteriaInline(TabularInline):
    model = ReviewCriteria
    form = CriteriaForm
    extra = 0


def update_leaderboard_admin(modeladmin, request, queryset):
    for course in queryset:
        update_leaderboard(course)
        modeladmin.message_user(
            request,
            f"Leaderboard updated for course {course}",
            level=messages.SUCCESS,
        )


update_leaderboard_admin.short_description = "Update leaderboard"


def duplicate_course(modeladmin, request, queryset):
    current_year = timezone.now().year

    for course in queryset:
        new_course = _duplicate_course(course, current_year)
        modeladmin.message_user(
            request,
            f"Course '{course.title}' was duplicated to "
            f"'{new_course.title}'",
            level=messages.SUCCESS,
        )


duplicate_course.short_description = "Duplicate selected courses"


def _duplicate_course(course, current_year):
    target_year = _next_duplicate_year(course, current_year)
    target_identifier = _next_duplicate_identifier(course, str(target_year))
    duplicate_fields = _course_duplicate_fields(
        course,
        target_year,
        target_identifier,
    )
    with transaction.atomic():
        new_course = Cohort.objects.create(**duplicate_fields)
        _copy_curriculum(course, new_course)
    return new_course


def _next_duplicate_year(course, requested_year):
    """Choose the requested year unless this Course already has that edition."""

    used_years = set(
        Cohort.objects.filter(course=course.course).values_list("year", flat=True)
    )
    year = requested_year
    while year in used_years:
        year += 1
    return year


def _next_duplicate_identifier(course, requested_identifier):
    """Choose a unique route identifier within the reusable Course family."""

    used_identifiers = set(
        Cohort.objects.filter(course=course.course).values_list(
            "identifier",
            flat=True,
        )
    )
    identifier = requested_identifier
    suffix = 2
    while identifier in used_identifiers:
        identifier = f"{requested_identifier}-{suffix}"
        suffix += 1
    return identifier


def _course_duplicate_fields(course, current_year, identifier):
    title = _year_rollover_value(course.title, current_year, " ")
    slug = _year_rollover_value(course.slug, current_year, "-")
    return {
        "course": course.course,
        "identifier": identifier,
        "year": current_year,
        "title": title,
        "slug": slug,
        "description": course.description,
        "outcome": course.outcome,
        "start_date": course.start_date,
        "end_date": course.end_date,
        "registration_url": course.registration_url,
        "github_repo_url": course.github_repo_url,
        "social_media_hashtag": course.social_media_hashtag,
        "first_homework_scored": False,
        "finished": False,
        "faq_document_url": course.faq_document_url,
        "project_passing_score": course.project_passing_score,
        "curriculum_format": course.curriculum_format,
        "visible": course.visible,
    }


def _year_rollover_value(value, current_year, separator):
    previous_year = str(current_year - 1)
    current_year_text = str(current_year)
    if previous_year in value:
        return value.replace(previous_year, current_year_text)
    return f"{value}{separator}{current_year}"


def _copy_curriculum(source_course, target_course):
    """Copy curriculum definitions without learner or lifecycle history."""

    homework_map = {}
    for homework in Homework.objects.filter(course=source_course).order_by("id"):
        fields = _copyable_fields(homework, excluded={"id", "course"})
        copied_homework = Homework.objects.create(course=target_course, **fields)
        homework_map[homework.pk] = copied_homework
        for question in Question.objects.filter(homework=homework).order_by("id"):
            question_fields = _copyable_fields(question, excluded={"id", "homework"})
            Question.objects.create(homework=copied_homework, **question_fields)

    project_map = {}
    source_projects = list(
        Project.objects.filter(course=source_course).order_by("id")
    )
    for project in source_projects:
        fields = _copyable_fields(project, excluded={"id", "course"})
        project_map[project.pk] = Project.objects.create(
            course=target_course,
            **fields,
        )

    source_criteria = list(
        ReviewCriteria.objects.filter(course=source_course).order_by("id")
    )
    assignments = list(
        ProjectCriteriaAssignment.objects.filter(project__course=source_course)
        .select_related("criteria")
        .order_by("project_id", "position", "id")
    )
    seen_criteria = {criteria.pk for criteria in source_criteria}
    for assignment in assignments:
        if assignment.criteria_id not in seen_criteria:
            source_criteria.append(assignment.criteria)
            seen_criteria.add(assignment.criteria_id)

    criteria_map = {}
    for criteria in source_criteria:
        criteria_map[criteria.pk] = ReviewCriteria.objects.create(
            course=target_course,
            description=criteria.description,
            options=criteria.options,
            review_criteria_type=criteria.review_criteria_type,
        )

    for project in source_projects:
        project_assignments = list(
            ProjectCriteriaAssignment.objects.filter(project=project)
            .select_related("criteria")
            .order_by("position", "id")
        )
        if not project_assignments:
            project_assignments = [
                (criteria, position)
                for position, criteria in enumerate(criteria_for_project(project))
            ]
        else:
            project_assignments = [
                (assignment.criteria, assignment.position)
                for assignment in project_assignments
            ]
        for criteria, position in project_assignments:
            copied_criteria = criteria_map.get(criteria.pk)
            if copied_criteria is None:
                continue
            ProjectCriteriaAssignment.objects.create(
                project=project_map[project.pk],
                criteria=copied_criteria,
                position=position,
            )

    module_map = {}
    for module in Module.objects.filter(cohort=source_course).order_by("position", "id"):
        copied_module = Module.objects.create(
            cohort=target_course,
            position=module.position,
            slug=module.slug,
            title=module.title,
            link=module.link,
            terminal_homework=homework_map[module.terminal_homework_id],
        )
        module_map[module.pk] = copied_module
        for unit in Unit.objects.filter(module=module).order_by("position", "id"):
            Unit.objects.create(
                module=copied_module,
                position=unit.position,
                slug=unit.slug,
                title=unit.title,
                link=unit.link,
            )

    for flow_item in CurriculumFlowItem.objects.filter(
        cohort=source_course
    ).order_by("position", "id"):
        CurriculumFlowItem.objects.create(
            cohort=target_course,
            position=flow_item.position,
            module=module_map.get(flow_item.module_id),
            project=project_map.get(flow_item.project_id),
        )


def _copyable_fields(instance, *, excluded):
    fields = {}
    for field in instance._meta.concrete_fields:
        if field.name in excluded:
            continue
        fields[field.name] = getattr(instance, field.name)
    return fields


@admin.register(Cohort)
class CourseAdmin(ModelAdmin):
    actions = [update_leaderboard_admin, duplicate_course]
    inlines = [CriteriaInline]
    list_display = [
        "title",
        "identifier",
        "curriculum_format",
        "start_date",
        "end_date",
        "visible",
        "finished",
    ]


admin.site.register(Module, ModelAdmin)
admin.site.register(Unit, ModelAdmin)
admin.site.register(CurriculumFlowItem, ModelAdmin)


@admin.register(RegistrationCampaign)
class RegistrationCampaignAdmin(ModelAdmin):
    prepopulated_fields = {"slug": ("title",)}
    list_display = [
        "title",
        "slug",
        "current_course",
        "is_active",
    ]
    search_fields = ["title", "slug"]
    list_filter = ["is_active", "current_course"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = [
        (
            "Landing page",
            {
                "fields": [
                    "title",
                    "slug",
                    "edition_label",
                    "current_course",
                    "is_active",
                    "hero_image_url",
                    "video_url",
                    "meta_description",
                    "marketing_markdown",
                ]
            },
        ),
        (
            "Timestamps",
            {
                "classes": ["collapse"],
                "fields": ["created_at", "updated_at"],
            },
        ),
    ]


@admin.register(CourseRegistration)
class CourseRegistrationAdmin(ModelAdmin):
    list_display = [
        "email_normalized",
        "campaign",
        "course",
        "company_name",
        "country",
        "region",
        "role",
        "created_at",
    ]
    search_fields = ["email", "email_normalized", "name", "company_name"]
    list_filter = [
        "campaign",
        "course",
        "region",
        "role",
    ]
    readonly_fields = [
        "email_normalized",
        "created_at",
        "updated_at",
    ]
    fieldsets = [
        (
            "Registration",
            {
                "fields": [
                    "campaign",
                    "course",
                    "user",
                    "email",
                    "email_normalized",
                    "name",
                    "company_name",
                    "country",
                    "region",
                    "role",
                    "comment",
                    "accepted_newsletter",
                ]
            },
        ),
        (
            "Timestamps",
            {
                "classes": ["collapse"],
                "fields": ["created_at", "updated_at"],
            },
        ),
    ]


@admin.register(LeaderboardComplaint)
class LeaderboardComplaintAdmin(ModelAdmin):
    list_display = [
        "enrollment",
        "issue_type",
        "resolved",
        "created_at",
        "resolved_at",
    ]
    list_filter = ["resolved", "issue_type", "enrollment__course"]
    search_fields = [
        "description",
        "enrollment__display_name",
        "enrollment__student__email",
        "reporter__email",
    ]
    readonly_fields = ["created_at"]
