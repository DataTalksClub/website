"""Cross-cohort project submissions for the family-wide and site-wide galleries.

The unified gallery uses this query at site, course-family, and cohort scope.
The wider scopes answer "what did people build across an entire course
family" and "what did people build anywhere on the site", so they read
:class:`~courses.models.project.ProjectSubmission` rows across many cohorts
(and, site-wide, many families) instead of scoping to one cohort.

Both galleries once folded real submissions behind something smaller: the
family gallery behind project-type rows with only a submission count, the
site gallery behind per-family/per-cohort ``<details>`` disclosures. Readers
said neither was what they meant by "individual project submissions", so
both now read ``ProjectSubmission`` directly and list real rows, flat and
newest-cohort-first -- ``family_project_submissions`` for one family,
``site_project_submissions`` generalizing that one level up to every visible
family, the same way the module's previous grouping helpers generalized from
one family to the whole site. Both exclude volunteer-review-only submissions
and annotate the same public fields (``vote_count``, ``display_score``) at
every scope.

Both return querysets, not lists, so a caller can paginate without first
materializing every submission the site (or one family) has ever collected --
a multi-year family can hold thousands, and the site holds every family's.
"""

from django.db.models import Case, Count, IntegerField, Value, When

from courses.models.cohort import Cohort, Course
from courses.models.project import ProjectState, ProjectSubmission


def _submission_display_score():
    """The score to show for a submission.

    A value of -1 means the assignment is not completed yet, which the gallery
    renders as ungraded rather than as a real score.
    """

    completed_project_score = When(
        project__state=ProjectState.COMPLETED.value,
        then="project_score",
    )
    unscored_project_score = Value(-1)
    return Case(
        completed_project_score,
        default=unscored_project_score,
        output_field=IntegerField(),
    )


def family_project_submissions(family: Course):
    """Every project submission across every visible cohort of ``family``, as
    one flat, newest-cohort-first queryset of individual submissions -- not
    project-type rows with a count.

    A flat list of project *types* (``Project`` rows, one per assignment,
    annotated with a submission count) still is not "individual project
    submissions" -- readers said so about the list this function replaces.
    This instead reads ``ProjectSubmission`` directly, across every visible
    cohort, ordered newest cohort first, then by project and submission time.
    It excludes
    volunteer-review-only submissions, matching that page's own filter, and
    annotates the same public fields (``vote_count``, ``display_score``) so
    this adds no new visibility beyond what a single cohort's catalogue
    already shows.

    ``select_related`` on ``project__course`` lets a caller read
    ``submission.project.course`` as the submission's cohort without another
    query per row -- ``Project.course`` is the cohort, confusingly named; see
    ``courses/models/project.py``.
    """

    cohort_ids = Cohort.objects.filter(course=family, visible=True).values_list("id", flat=True)
    submissions = ProjectSubmission.objects.filter(
        project__course_id__in=cohort_ids,
        volunteer_review_only=False,
    ).select_related("project", "project__course", "enrollment")
    submissions = submissions.annotate(
        vote_count=Count("votes"),
        display_score=_submission_display_score(),
    )
    return submissions.order_by(
        "-project__course__year",
        "-project__course_id",
        "project_id",
        "submitted_at",
    )


def site_project_submissions():
    """Every project submission anywhere on the site, across every visible
    family and every visible cohort, as one flat, newest-cohort-first
    queryset of individual submissions.

    Generalizes :func:`family_project_submissions` one level up: every
    visible family's every visible cohort, instead of one family's. Within
    the same cohort year, ties break by family title so same-year cohorts
    across different families group together in the reading order rather
    than interleaving arbitrarily.

    ``select_related`` reaches two hops past the submission's own project --
    ``project__course`` is the cohort, ``project__course__course`` is that
    cohort's family (``Cohort.course`` -- confusingly named; see
    ``courses/models/project.py`` and ``courses/models/cohort.py``) -- so a
    caller can read both without another query per row.
    """

    cohort_ids = Cohort.objects.filter(course__visible=True, visible=True).values_list(
        "id", flat=True
    )
    submissions = ProjectSubmission.objects.filter(
        project__course_id__in=cohort_ids,
        volunteer_review_only=False,
    ).select_related("project", "project__course", "project__course__course", "enrollment")
    submissions = submissions.annotate(
        vote_count=Count("votes"),
        display_score=_submission_display_score(),
    )
    return submissions.order_by(
        "-project__course__year",
        "project__course__course__title",
        "-project__course_id",
        "project_id",
        "submitted_at",
    )
