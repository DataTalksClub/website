from django.db import transaction
from django.db.models import Count

from courses.models.project import Project, ProjectVote

PROJECT_VOTES_PER_PROJECT = 3


def update_project_vote(user, submission, action="vote") -> None:
    # The budget decision must be one atomic unit: the count-then-insert below
    # used to run without a transaction, so two concurrent requests could each
    # observe two votes and both insert a different third (audit BE-12).  The
    # project row is the stable per-voter-project serialization point: every
    # budget decision for one project queues behind its lock, re-counts with a
    # fresh read, and only then inserts.  On the deployed PostgreSQL engine
    # ``select_for_update`` holds that lock to commit; SQLite (tests, local)
    # serializes the write transaction at its own layer.
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=submission.project_id)

        if action == "remove":
            ProjectVote.objects.filter(
                voter=user,
                submission=submission,
            ).delete()
            return

        if ProjectVote.objects.filter(
            voter=user,
            submission=submission,
        ).exists():
            return

        vote_count = ProjectVote.objects.filter(
            voter=user,
            submission__project=submission.project,
        ).count()
        if vote_count >= PROJECT_VOTES_PER_PROJECT:
            return

        # The (submission, voter) unique constraint remains the backstop for a
        # double vote on one submission; the project lock above is what makes
        # the cross-submission budget hold.
        ProjectVote.objects.get_or_create(
            voter=user,
            submission=submission,
        )


def get_voted_submission_ids(user, course) -> set[int]:
    if not user.is_authenticated:
        return set()

    votes = ProjectVote.objects.filter(
        voter=user,
        submission__project__course=course,
    )
    submission_ids = votes.values_list("submission_id", flat=True)
    return set(submission_ids)


def get_project_vote_counts(user, course) -> dict[int, int]:
    if not user.is_authenticated:
        return {}

    project_vote_counts = {}
    vote_count_annotation = Count("id")
    rows = (
        ProjectVote.objects.filter(
            voter=user,
            submission__project__course=course,
        )
        .values("submission__project_id")
        .annotate(count=vote_count_annotation)
    )
    for row in rows:
        project_id = row["submission__project_id"]
        vote_count = row["count"]
        project_vote_counts[project_id] = vote_count
    return project_vote_counts
