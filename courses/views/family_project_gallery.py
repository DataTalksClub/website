from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from courses.models.cohort import Course
from courses.views.project_gallery_groups import family_project_submissions
from courses.views.site_project_gallery import optional_all_projects_url

FAMILY_PROJECT_SUBMISSIONS_PAGE_SIZE = 25


def family_project_gallery_view(request, course_slug: str):
    """Every individual learner project submission anywhere in a course family.

    Unlike ``course_project_submissions.list_all_project_submissions_view``
    (one cohort's submissions), this reads every visible cohort of the family
    and lists real submissions flat, newest cohort first -- the aggregate
    view no per-cohort page can answer. A multi-year family can hold
    thousands of submissions (ml-zoomcamp alone has run since 2021), so this
    paginates the same way the per-cohort catalogue does.
    """

    family = get_object_or_404(Course, slug=course_slug, visible=True)
    submissions = family_project_submissions(family)
    paginator = Paginator(submissions, FAMILY_PROJECT_SUBMISSIONS_PAGE_SIZE)
    page_number = request.GET.get("page")
    submissions_page = paginator.get_page(page_number)

    # ``Project.course`` is the submission's cohort (confusingly named; see
    # courses/models/project.py) -- alias it as ``.cohort`` on each row so
    # the flat, multi-cohort list can tag which edition it came from, the
    # same convention the project-type list this view replaces used.
    for submission in submissions_page.object_list:
        submission.cohort = submission.project.course

    page_range = paginator.get_elided_page_range(submissions_page.number)
    context = {
        "course_family": family,
        "submissions": submissions_page.object_list,
        "submissions_page": submissions_page,
        "page_range": page_range,
        "all_projects_url": optional_all_projects_url(),
    }
    return render(request, "projects/family_gallery.html", context)
