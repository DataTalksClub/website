from django.shortcuts import render

from courses.views.project_gallery_groups import site_project_groups

# The site holds many course families, each of which can hold many cohorts,
# so nothing is auto-expanded here: every family fold opens on demand and
# shows its own project/cohort counts in the closed summary. A cohort fold a
# reader does open still starts on its newest editions, matching the
# family-scoped gallery's own default.
DEFAULT_OPEN_COHORTS = 2


def site_project_gallery_view(request):
    """Every learner project submitted anywhere on the site.

    One level up from ``family_project_gallery_view``: it groups by course
    family first, then by cohort within each family, reusing the same
    cohort-grouping this module's sibling view uses for one family.
    """

    family_groups = site_project_groups()
    context = {
        "family_groups": family_groups,
        "default_open_cohorts": DEFAULT_OPEN_COHORTS,
    }
    return render(request, "projects/site_gallery.html", context)
