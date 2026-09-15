"""Cross-cohort project groupings for the family-wide and site-wide galleries.

``courses/views/course_project_submissions.py`` lists every submission for
one :class:`~courses.models.cohort.Cohort` (issue #179's design-system port).
The two galleries built from this module answer a wider question -- "what did
people build across an entire course family" and "what did people build
anywhere on the site" -- so they read :class:`~courses.models.project.Project`
rows across many cohorts (and, site-wide, many families) instead of scoping to
one cohort.

The site-wide gallery still folds cohorts most people are not looking for
behind a disclosure, grouped by family then cohort (``site_project_groups``);
the family-wide gallery instead flattens every cohort's projects into one
newest-first list (``family_project_list``) -- readers said they just want to
see all of a family's projects, not a wall of per-cohort folds. Either way,
this module only ever returns cohorts (and families) that actually hold a
project: an empty cohort would just be a fold, or a flat list, with nothing
under it.

The two counted totals a caller reads off a group -- ``project_count`` and
``submission_count`` -- are properties rather than stored fields so a group
built in a test from plain constructor calls never needs to keep a derived
number in sync by hand.
"""

from dataclasses import dataclass, field

from django.db.models import Count, Q

from courses.models.cohort import Cohort, Course
from courses.models.project import Project


@dataclass(frozen=True, slots=True)
class CohortProjectGroup:
    """One cohort's projects, each annotated with its counted submission total."""

    cohort: Cohort
    projects: list = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FamilyProjectGroup:
    """One course family's cohort groups, newest edition first."""

    family: Course
    cohort_groups: list = field(default_factory=list)

    @property
    def project_count(self) -> int:
        return sum(len(group.projects) for group in self.cohort_groups)

    @property
    def submission_count(self) -> int:
        return sum(
            project.submissions_count
            for group in self.cohort_groups
            for project in group.projects
        )


def _projects_by_cohort(cohort_ids: list) -> dict:
    """Every project in ``cohort_ids``, keyed by cohort id, each annotated the
    way the single-cohort catalogue already annotates its own projects
    (``course_project_submissions._projects_with_submission_counts``), so the
    two surfaces never count a submission differently.
    """

    if not cohort_ids:
        return {}

    counted_submissions = Q(projectsubmission__volunteer_review_only=False)
    submissions_count = Count("projectsubmission", filter=counted_submissions)
    projects = (
        Project.objects.filter(course_id__in=cohort_ids)
        .annotate(submissions_count=submissions_count)
        .order_by("course_id", "id")
    )

    by_cohort: dict = {}
    for project in projects:
        by_cohort.setdefault(project.course_id, []).append(project)
    return by_cohort


def _build_cohort_groups(
    cohorts: list, projects_by_cohort: dict
) -> list:
    """Pair each of ``cohorts`` with its projects, dropping cohorts with none.

    ``cohorts`` must already be ordered newest first; that order survives into
    the returned groups. This does no querying itself so a caller building
    several families' groups from one bulk projects lookup does not re-query
    per family.
    """

    groups = [
        CohortProjectGroup(
            cohort=cohort, projects=projects_by_cohort.get(cohort.id, [])
        )
        for cohort in cohorts
    ]
    return [group for group in groups if group.projects]


def cohort_project_groups(cohorts: list) -> list:
    """Group ``cohorts``' projects under each cohort, dropping cohorts with none."""

    projects_by_cohort = _projects_by_cohort([cohort.id for cohort in cohorts])
    return _build_cohort_groups(cohorts, projects_by_cohort)


def family_project_groups(family: Course) -> list:
    """Every visible cohort of ``family`` that holds a project, newest first."""

    cohorts = list(
        Cohort.objects.filter(course=family, visible=True).order_by("-year", "-id")
    )
    return cohort_project_groups(cohorts)


def family_project_list(family: Course) -> list:
    """Every project ``family`` holds, across every visible cohort, as one flat
    sequence rather than grouped by cohort.

    The family-wide gallery (``family_project_gallery_view``) shows "every
    project", not "every cohort" -- readers said as much about the old
    per-cohort ``<details>`` folds. This still orders newest cohort first,
    each cohort's own projects in their existing order, by flattening
    :func:`family_project_groups`; the site-wide gallery keeps its own
    grouped-by-family-then-cohort shape via :func:`site_project_groups`; this
    is not a replacement for that. Each returned project carries its cohort
    as ``.cohort`` so a flat list can still tag which edition it came from.
    """

    projects = []
    for group in family_project_groups(family):
        for project in group.projects:
            project.cohort = group.cohort
            projects.append(project)
    return projects


def site_project_groups() -> list:
    """Every visible family's cohort project groups, most active family first.

    "Most active" reads the newest year among that family's cohorts which
    actually hold a project -- the same fact the fold's own summary line
    shows -- so a family with old, popular cohorts does not out-rank one
    that is running right now.

    Building this from two bulk queries (every visible cohort, every project
    in those cohorts) rather than one query per family keeps the page's query
    count flat as the number of course families grows.
    """

    families = list(Course.objects.filter(visible=True))
    families_by_id = {family.id: family for family in families}

    cohorts = list(
        Cohort.objects.filter(course__in=families, visible=True).order_by(
            "course_id", "-year", "-id"
        )
    )
    cohorts_by_family: dict = {}
    for cohort in cohorts:
        cohorts_by_family.setdefault(cohort.course_id, []).append(cohort)

    projects_by_cohort = _projects_by_cohort([cohort.id for cohort in cohorts])

    family_groups = []
    for family_id, family_cohorts in cohorts_by_family.items():
        groups = _build_cohort_groups(family_cohorts, projects_by_cohort)
        if groups:
            family_groups.append(
                FamilyProjectGroup(
                    family=families_by_id[family_id], cohort_groups=groups
                )
            )

    family_groups.sort(
        key=lambda family_group: (
            -max(group.cohort.year for group in family_group.cohort_groups),
            family_group.family.title.casefold(),
        )
    )
    return family_groups
