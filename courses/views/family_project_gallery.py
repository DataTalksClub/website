from django.shortcuts import get_object_or_404, render

from courses.models.cohort import Course
from courses.views.project_gallery_groups import family_project_list


def family_project_gallery_view(request, course_slug: str):
    """Every learner project submitted anywhere in a course family.

    Unlike ``course_project_submissions.list_all_project_submissions_view``
    (one cohort's submissions), this reads every visible cohort of the family
    and lists its projects flat, newest cohort first -- the aggregate view no
    per-cohort page can answer.
    """

    family = get_object_or_404(Course, slug=course_slug, visible=True)
    projects = family_project_list(family)
    context = {
        "course_family": family,
        "projects": projects,
    }
    return render(request, "projects/family_gallery.html", context)
