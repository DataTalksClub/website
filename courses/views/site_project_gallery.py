from typing import cast
from urllib.parse import unquote, urlencode, urlsplit

from django import forms
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse

from courses.models.cohort import Cohort, Course
from courses.models.project import Project
from courses.views.project_gallery_groups import site_project_submissions
from courses.views.project_submission_listing import (
    project_submissions_page,
    projects_list_context,
)
from courses.views.project_submission_viewer import project_viewer_state
from courses.views.project_submission_votes import project_vote_response

SITE_PROJECT_SUBMISSIONS_PAGE_SIZE = 25


class ProjectGalleryFilters(forms.Form):
    course = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "field-input"}))
    cohort = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "field-input"}))
    project = forms.ChoiceField(
        label="Assignment",
        required=False,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    sort = forms.ChoiceField(
        label="Sort by",
        required=False,
        initial="cohort",
        choices=(
            ("cohort", "Newest cohorts"),
            ("recent", "Recently submitted"),
            ("votes", "Most voted"),
        ),
        widget=forms.Select(attrs={"class": "field-input"}),
    )


def _repository_identity(raw_url: str) -> tuple[str, str]:
    """Return a safe, readable label and destination for a submitted URL."""

    try:
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in raw_url
            or any(character.isspace() or ord(character) < 32 for character in raw_url)
        ):
            return "", ""
    except ValueError:
        return "", ""
    path = unquote(parsed.path).strip("/")
    return f"{parsed.hostname}/{path}".rstrip("/"), raw_url


def optional_all_projects_url() -> str | None:
    """Return the public gallery URL when the active URLConf exposes it."""

    try:
        return reverse("all_projects")
    except NoReverseMatch:
        return None


def _scope_objects(course_slug, cohort_identifier, project_slug):
    family = cohort = project = None
    if course_slug:
        family = get_object_or_404(Course, slug=course_slug, visible=True)
    if cohort_identifier:
        cohort = get_object_or_404(
            Cohort,
            course=family,
            identifier=str(cohort_identifier),
            visible=True,
        )
    if project_slug:
        project = get_object_or_404(Project, course=cohort, slug=project_slug)
    return family, cohort, project


def _choice_rows(submissions):
    return list(
        submissions.order_by()
        .values(
            "project__course__course__slug",
            "project__course__course__title",
            "project__course_id",
            "project__course__identifier",
            "project_id",
            "project__title",
        )
        .annotate(total=Count("pk", distinct=True))
    )


def _filter_choices(rows, family=None, cohort=None, project=None, selected_course="", selected_cohort=""):
    """Compute the Course/Cohort/Assignment option lists, each narrowed by
    what was already picked -- selecting a course should not still offer
    every other course's cohorts, and selecting a cohort (or a course with
    only one) should not still offer every other cohort's assignments.
    ``selected_course``/``selected_cohort`` come from the submitted filter
    values (or the route's own scope, for the family/cohort-scoped pages),
    the same request round trip this filter bar already uses for every
    other filter -- there is no separate client-side cascade to keep in
    sync.
    """

    courses = {
        (row["project__course__course__slug"], row["project__course__course__title"])
        for row in rows
    }
    cohort_rows = rows
    if selected_course:
        cohort_rows = [
            row for row in rows if row["project__course__course__slug"] == selected_course
        ]
    cohorts = {
        (
            str(row["project__course_id"]),
            f"{row['project__course__course__title']} · {row['project__course__identifier']}",
        )
        for row in cohort_rows
    }
    project_rows = cohort_rows
    if selected_cohort:
        project_rows = [
            row for row in cohort_rows if str(row["project__course_id"]) == selected_cohort
        ]
    projects = {
        (
            str(row["project_id"]),
            (
                f"{row['project__title']} · "
                f"{row['project__course__course__title']} "
                f"{row['project__course__identifier']}"
            ),
        )
        for row in project_rows
    }
    if family:
        courses.add((family.slug, family.title))
    if cohort:
        cohorts.add((str(cohort.pk), f"{family.title} · {cohort.identifier}"))
    if project:
        projects.add((str(project.pk), f"{project.title} · {family.title} {cohort.identifier}"))
    return (
        sorted(courses, key=lambda item: (item[1].casefold(), item[0])),
        sorted(cohorts, key=lambda item: item[1].casefold(), reverse=True),
        sorted(projects, key=lambda item: item[1].casefold()),
    )


def _gallery_filters(request, rows, family=None, cohort=None, project=None):
    data = request.GET.copy()
    data.pop("page", None)
    if family:
        data["course"] = family.slug
    if cohort:
        data["cohort"] = str(cohort.pk)
    if project:
        data["project"] = str(project.pk)
    if "sort" not in data:
        data["sort"] = "cohort"

    filters = ProjectGalleryFilters(data)
    courses, cohorts, projects = _filter_choices(
        rows,
        family,
        cohort,
        project,
        selected_course=data.get("course", ""),
        selected_cohort=data.get("cohort", ""),
    )
    cast(forms.ChoiceField, filters.fields["course"]).choices = [("", "All courses"), *courses]
    cast(forms.ChoiceField, filters.fields["cohort"]).choices = [("", "All cohorts"), *cohorts]
    cast(forms.ChoiceField, filters.fields["project"]).choices = [
        ("", "All assignments"),
        *projects,
    ]
    # Progressive disclosure: Cohort and Assignment are mostly noise before
    # their prerequisite narrows them (see _filter_choices above), so each
    # stays disabled -- the same real `.field-input[disabled]` treatment
    # every other disabled field on the site already gets -- until a course
    # (for Cohort), or a course or cohort (for Assignment), is chosen. The
    # server still accepts an already-submitted value either way; this only
    # affects what the control offers to *change* next.
    if not data.get("course"):
        cast(forms.ChoiceField, filters.fields["cohort"]).widget.attrs.update(
            {"disabled": True, "aria-describedby": "id_cohort-help"}
        )
    if not (data.get("course") or data.get("cohort")):
        cast(forms.ChoiceField, filters.fields["project"]).widget.attrs.update(
            {"disabled": True, "aria-describedby": "id_project-help"}
        )
    return filters, courses


def _apply_filters(submissions, filters):
    if not filters.is_valid():
        return submissions.none()
    values = filters.cleaned_data
    if values["course"]:
        submissions = submissions.filter(project__course__course__slug=values["course"])
    if values["cohort"]:
        submissions = submissions.filter(project__course_id=int(values["cohort"]))
    if values["project"]:
        submissions = submissions.filter(project_id=int(values["project"]))
    if values["sort"] == "recent":
        return submissions.order_by("-submitted_at", "-pk")
    if values["sort"] == "votes":
        return submissions.order_by("-vote_count", "-submitted_at", "-pk")
    return submissions.order_by(*submissions.query.order_by, "pk")


def _decorate_rows(submissions, family=None, cohort=None, project=None):
    for submission in submissions:
        submission.cohort = cohort or submission.project.course
        submission.family = family or submission.cohort.course
        submission.assignment = project or submission.project
        submission.repository_label, submission.repository_url = _repository_identity(
            submission.github_link
        )


def project_gallery_view(
    request,
    course_slug: str | None = None,
    cohort_identifier: str | int | None = None,
    project_slug: str | None = None,
):
    """Render every project-list route through one filterable gallery template."""

    family, cohort, project = _scope_objects(course_slug, cohort_identifier, project_slug)
    if request.method == "POST":
        if project is None:
            raise ValueError("Only an assignment-scoped gallery accepts votes")
        return project_vote_response(request, cohort, project)

    public_submissions = site_project_submissions()
    if cohort is None:
        # Only the family-wide (``family_projects``) and site-wide
        # (``all_projects``) galleries: don't show a "Passed"/"Not passed"
        # badge, only show submissions that passed at all. The per-cohort
        # listing (``cohort_projects``) was not asked to change, so it keeps
        # showing every submission regardless of grading state.
        public_submissions = public_submissions.filter(passed=True)
    facet_rows = _choice_rows(public_submissions)
    filters, courses = _gallery_filters(request, facet_rows, family, cohort, project)

    context = {}
    if project:
        viewer_state = project_viewer_state(project, cohort, request.user)
        submissions_page = project_submissions_page(request, project, viewer_state)
        context.update(projects_list_context(cohort, project, submissions_page, viewer_state))
    else:
        submissions = _apply_filters(public_submissions, filters)
        paginator = Paginator(submissions, SITE_PROJECT_SUBMISSIONS_PAGE_SIZE)
        submissions_page = paginator.get_page(request.GET.get("page"))
        context.update(
            {
                "submissions": submissions_page.object_list,
                "submissions_page": submissions_page,
                "page_range": paginator.get_elided_page_range(
                    submissions_page.number, on_each_side=1
                ),
            }
        )

    _decorate_rows(submissions_page.object_list, family, cohort, project)
    query = ""
    if filters.is_valid():
        query = urlencode(
            {
                key: value
                for key, value in filters.cleaned_data.items()
                if value and not (key == "sort" and value == "cohort")
            }
        )
    gallery_url = optional_all_projects_url() or request.path
    context.update(
        {
            "course_family": family,
            "course": cohort,
            "project": project,
            "gallery_filters": filters,
            "gallery_total": submissions_page.paginator.count,
            "gallery_course_count": len(courses),
            "gallery_has_filters": bool(family or cohort or project or query or filters.errors),
            "gallery_empty_is_filtered": bool(
                filters.errors
                or {"course", "cohort", "project", "sort"}.intersection(request.GET)
            ),
            "gallery_url": gallery_url,
            "pagination_querystring": f"&{query}" if query else "",
            "is_project_gallery": project is not None,
        }
    )
    return render(request, "projects/site_gallery.html", context)


site_project_gallery_view = project_gallery_view
