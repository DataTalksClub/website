from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse

from courses.views.project_gallery_groups import site_project_submissions

SITE_PROJECT_SUBMISSIONS_PAGE_SIZE = 25


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

    page_range = paginator.get_elided_page_range(submissions_page.number)
    context = {
        "submissions": submissions_page.object_list,
        "submissions_page": submissions_page,
        "page_range": page_range,
    }
    return render(request, "projects/site_gallery.html", context)
