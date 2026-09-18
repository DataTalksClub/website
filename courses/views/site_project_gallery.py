from typing import cast
from urllib.parse import unquote, urlencode, urlsplit

from django import forms
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods

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
GALLERY_SORTS = frozenset({"cohort", "recent", "votes"})


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
            "project__course__identifier",
            "project__slug",
            "project__title",
        )
        .annotate(total=Count("pk", distinct=True))
    )


def _filter_choices(
    rows,
    *,
    selected_course="",
    selected_cohort="",
    family=None,
    cohort=None,
    project=None,
):
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
    cohort_rows = (
        [row for row in rows if row["project__course__course__slug"] == selected_course]
        if selected_course
        else []
    )
    cohorts = {
        (
            row["project__course__identifier"],
            f"{row['project__course__course__title']} · {row['project__course__identifier']}",
        )
        for row in cohort_rows
    }
    project_rows = (
        [row for row in cohort_rows if row["project__course__identifier"] == selected_cohort]
        if selected_cohort
        else []
    )
    projects = {
        (
            row["project__slug"],
            row["project__title"],
        )
        for row in project_rows
    }
    if family:
        courses.add((family.slug, family.title))
    if cohort:
        cohorts.add((cohort.identifier, f"{family.title} · {cohort.identifier}"))
    if project:
        projects.add((project.slug, project.title))
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
        data["cohort"] = cohort.identifier
    if project:
        data["project"] = project.slug
    if "sort" not in data:
        data["sort"] = "cohort"

    filters = ProjectGalleryFilters(data)
    courses, cohorts, projects = _filter_choices(
        rows,
        selected_course=data.get("course", ""),
        selected_cohort=data.get("cohort", ""),
        family=family,
        cohort=cohort,
        project=project,
    )
    cast(forms.ChoiceField, filters.fields["course"]).choices = [("", "All courses"), *courses]
    cast(forms.ChoiceField, filters.fields["cohort"]).choices = [("", "All cohorts"), *cohorts]
    cast(forms.ChoiceField, filters.fields["project"]).choices = [
        ("", "All assignments"),
        *projects,
    ]
    # The dependency is strict, including without JavaScript: Course unlocks
    # Cohort, and a valid Cohort unlocks Assignment.
    if not data.get("course"):
        cast(forms.ChoiceField, filters.fields["cohort"]).widget.attrs.update(
            {"disabled": True, "aria-describedby": "id_cohort-help"}
        )
    if not data.get("cohort"):
        cast(forms.ChoiceField, filters.fields["project"]).widget.attrs.update(
            {"disabled": True, "aria-describedby": "id_project-help"}
        )
    duplicate_fields = [
        name
        for name in ("course", "cohort", "project", "sort")
        if len(request.GET.getlist(name)) > 1
    ]
    if duplicate_fields:
        filters.is_valid()
        for name in duplicate_fields:
            filters.add_error(name, "Choose one value.")
    return filters, courses


def _apply_filters(submissions, filters):
    if not filters.is_valid():
        return submissions.none()
    values = filters.cleaned_data
    if values["course"]:
        submissions = submissions.filter(project__course__course__slug=values["course"])
    if values["cohort"]:
        submissions = submissions.filter(project__course__identifier=values["cohort"])
    if values["project"]:
        submissions = submissions.filter(project__slug=values["project"])
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


def cohort_gallery_url(cohort: Cohort) -> str:
    """The sole public cohort-gallery location, using stable identities."""

    query = urlencode({"course": cohort.course.slug, "cohort": cohort.identifier})
    return f"{reverse('all_projects')}?{query}"


def _one_query_value(request, name: str) -> str:
    values = request.GET.getlist(name)
    if len(values) > 1:
        raise Http404
    return values[0] if values else ""


def _compatibility_gallery_query(request, family: Course, cohort: Cohort | None = None) -> str:
    """Keep only validated state on an old gallery path.

    Route identity wins. A conflicting query identity is rejected instead of
    redirecting to a different course or widening the result set.
    """

    supplied_course = _one_query_value(request, "course")
    supplied_cohort = _one_query_value(request, "cohort")
    if supplied_course and supplied_course != family.slug:
        raise Http404
    if cohort and supplied_cohort and supplied_cohort != cohort.identifier:
        raise Http404

    query: dict[str, str] = {"course": family.slug}
    selected_cohort = cohort
    if selected_cohort is None and supplied_cohort:
        selected_cohort = get_object_or_404(
            Cohort,
            course=family,
            identifier=supplied_cohort,
            visible=True,
        )
    if selected_cohort is not None:
        query["cohort"] = selected_cohort.identifier

    project_slug = _one_query_value(request, "project")
    if project_slug:
        if selected_cohort is None:
            raise Http404
        project = get_object_or_404(Project, course=selected_cohort, slug=project_slug)
        query["project"] = project.slug

    sort = _one_query_value(request, "sort")
    if sort:
        if sort not in GALLERY_SORTS:
            raise Http404
        if sort != "cohort":
            query["sort"] = sort

    page = _one_query_value(request, "page")
    if page:
        if not page.isascii() or not page.isdigit() or int(page) < 1:
            raise Http404
        query["page"] = str(int(page))
    return urlencode(query)


@require_http_methods(["GET", "HEAD"])
def cohort_projects_redirect(request, course_slug: str, cohort_identifier: str) -> HttpResponse:
    family, cohort, _project = _scope_objects(course_slug, cohort_identifier, None)
    query = _compatibility_gallery_query(request, family, cohort)
    return HttpResponsePermanentRedirect(f"{reverse('all_projects')}?{query}")


@require_http_methods(["GET", "HEAD"])
def family_projects_redirect(request, course_slug: str) -> HttpResponse:
    family, _cohort, _project = _scope_objects(course_slug, None, None)
    query = _compatibility_gallery_query(request, family)
    return HttpResponsePermanentRedirect(f"{reverse('all_projects')}?{query}")


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

    all_public_submissions = site_project_submissions()
    eligible_submissions = all_public_submissions.filter(passed=True)
    facet_rows = _choice_rows(eligible_submissions)
    requested_course = request.GET.get("course", "")
    requested_cohort = request.GET.get("cohort", "")
    if requested_course and requested_cohort:
        # The canonical course+cohort query replaces the old cohort route and
        # therefore remains valid for a cohort whose only public submissions
        # are not passed. Add only that exact requested scope; never disclose
        # unrelated failed-only facets.
        facet_rows.extend(
            row
            for row in _choice_rows(all_public_submissions)
            if row["project__course__course__slug"] == requested_course
            and row["project__course__identifier"] == requested_cohort
        )
    filters, courses = _gallery_filters(request, facet_rows, family, cohort, project)

    if project is None and filters.is_valid():
        selected = filters.cleaned_data
        if selected["course"]:
            family = Course.objects.filter(slug=selected["course"], visible=True).first()
        if family is not None and selected["cohort"]:
            cohort = Cohort.objects.filter(
                course=family,
                identifier=selected["cohort"],
                visible=True,
            ).first()

    context = {}
    if project:
        viewer_state = project_viewer_state(project, cohort, request.user)
        submissions_page = project_submissions_page(request, project, viewer_state)
        context.update(projects_list_context(cohort, project, submissions_page, viewer_state))
    else:
        # A complete course+cohort selection is the canonical replacement for
        # the old cohort listing, so it retains that listing's passed and
        # not-passed rows. Wider discovery remains passed-only.
        cohort_scoped = filters.is_valid() and bool(filters.cleaned_data["cohort"])
        submissions = _apply_filters(
            all_public_submissions if cohort_scoped else eligible_submissions,
            filters,
        )
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
                filters.errors or {"course", "cohort", "project", "sort"}.intersection(request.GET)
            ),
            "gallery_url": gallery_url,
            "pagination_querystring": f"&{query}" if query else "",
            "is_project_gallery": project is not None,
        }
    )
    return render(request, "projects/site_gallery.html", context)


site_project_gallery_view = project_gallery_view
