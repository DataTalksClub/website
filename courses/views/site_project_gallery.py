from typing import cast
from urllib.parse import unquote, urlencode, urlsplit

from django import forms
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse

from courses.views.project_gallery_groups import site_project_submissions

SITE_PROJECT_SUBMISSIONS_PAGE_SIZE = 25


class ProjectGalleryFilters(forms.Form):
    q = forms.CharField(
        label="Repository or assignment",
        required=False,
        max_length=120,
        widget=forms.TextInput(attrs={"type": "search", "class": "field-input"}),
    )
    course = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "field-input"}))
    year = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "field-input"}))
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
    """A readable address, not an inferred project title or a repository fetch.

    Keep the submitted destination (including a subdirectory or fragment). Unsafe
    or missing addresses render without a link; never expose URL credentials.
    """

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
    """Resolve the site-wide project gallery link without failing when it is absent.

    ``all_projects`` is registered by the full site's ``website.urls``, which
    ``course_management.urls`` (the reduced, courses-app-only URLConf the
    studio/course-management deployment target runs) never includes. Templates
    shared between both surfaces (``courses/course_list.html``,
    ``projects/family_gallery.html``) call this instead of ``{% url
    'all_projects' %}`` directly, so the link is simply left off rather than
    raising ``NoReverseMatch`` when rendered under the reduced URLConf.
    """

    try:
        return reverse("all_projects")
    except NoReverseMatch:
        return None


def site_project_gallery_view(request):
    """Every individual learner project submission anywhere on the site.

    One level up from ``family_project_gallery_view``: it reads every
    visible family's every visible cohort instead of one family's, reusing
    the same submission-level query and row shape (submitter, repository
    link, project + cohort tag, votes, score/pass state) so the two galleries
    stay visually and structurally consistent. The site can hold many
    thousands of submissions across every course family, so like the family
    gallery this paginates rather than rendering one unbounded list.
    """

    submissions = site_project_submissions()
    # Facets come from exactly the same public rows as the results. Group in SQL,
    # rather than materializing thousands of submissions or querying per card.
    facets = list(
        submissions.order_by()
        .values(
            "project__course__course__slug",
            "project__course__course__title",
            "project__course__year",
        )
        .annotate(total=Count("pk", distinct=True))
    )
    courses = sorted(
        {
            (row["project__course__course__slug"], row["project__course__course__title"])
            for row in facets
        },
        key=lambda course: (course[1].casefold(), course[0]),
    )
    years = sorted({row["project__course__year"] for row in facets}, reverse=True)
    filters = ProjectGalleryFilters(request.GET or {"sort": "cohort"})
    cast(forms.ChoiceField, filters.fields["course"]).choices = [("", "All courses"), *courses]
    cast(forms.ChoiceField, filters.fields["year"]).choices = [
        ("", "All years"),
        *((str(year), str(year)) for year in years),
    ]
    if filters.is_valid():
        values = filters.cleaned_data
        if values["course"]:
            submissions = submissions.filter(project__course__course__slug=values["course"])
        if values["year"]:
            submissions = submissions.filter(project__course__year=int(values["year"]))
        if values["q"]:
            submissions = submissions.filter(
                Q(github_link__icontains=values["q"]) | Q(project__title__icontains=values["q"])
            )
        if values["sort"] == "recent":
            submissions = submissions.order_by("-submitted_at", "-pk")
        elif values["sort"] == "votes":
            submissions = submissions.order_by("-vote_count", "-submitted_at", "-pk")
        else:
            submissions = submissions.order_by(*submissions.query.order_by, "pk")
    else:
        # Invalid/retired facet values must not silently widen a shared filter link.
        submissions = submissions.none()
    paginator = Paginator(submissions, SITE_PROJECT_SUBMISSIONS_PAGE_SIZE)
    page_number = request.GET.get("page")
    submissions_page = paginator.get_page(page_number)

    # ``Project.course`` is the submission's cohort, and that cohort's own
    # ``.course`` is its family (both confusingly named; see
    # courses/models/project.py and courses/models/cohort.py) -- alias both
    # as ``.cohort``/``.family`` on each row so a flat, site-wide list can
    # still tag which course and edition a submission came from.
    for submission in submissions_page.object_list:
        submission.cohort = submission.project.course
        submission.family = submission.project.course.course
        submission.repository_label, submission.repository_url = _repository_identity(
            submission.github_link
        )

    page_range = paginator.get_elided_page_range(submissions_page.number, on_each_side=1)
    query = urlencode(
        {
            key: value
            for key, value in filters.cleaned_data.items()
            if value and not (key == "sort" and value == "cohort")
        }
    )
    context = {
        "submissions": submissions_page.object_list,
        "submissions_page": submissions_page,
        "page_range": page_range,
        "gallery_filters": filters,
        "gallery_total": sum(row["total"] for row in facets),
        "gallery_course_count": len(courses),
        "gallery_has_filters": bool(query) or bool(filters.errors),
        "pagination_querystring": f"&{query}" if query else "",
    }
    return render(request, "projects/site_gallery.html", context)
