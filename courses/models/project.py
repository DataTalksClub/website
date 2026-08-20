import math
import statistics
from enum import Enum

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone

from courses.validators.criteria_validators import (
    validate_review_criteria_options,
)
from courses.validators.custom_url_validators import validate_url_200

from .cohort import Cohort, Enrollment
from .stat_display import build_stat_fields, project_stat_sections

User = get_user_model()


class ProjectState(Enum):
    CLOSED = "CL"
    COLLECTING_SUBMISSIONS = "CS"
    PEER_REVIEWING = "PR"
    COMPLETED = "CO"


def _build_enum_choices(enum_type):
    choices = []
    for state in enum_type:
        # Keep historical migration state stable. Runtime model display methods
        # below provide human-readable labels without changing stored choices.
        choice = (state.value, state.name)
        choices.append(choice)
    return choices


PROJECT_STATE_CHOICES = _build_enum_choices(ProjectState)

PROJECT_STATE_LABELS = {
    ProjectState.CLOSED.value: "Closed",
    ProjectState.COLLECTING_SUBMISSIONS.value: "Collecting submissions",
    ProjectState.PEER_REVIEWING.value: "Peer reviewing",
    ProjectState.COMPLETED.value: "Completed",
}


class Project(models.Model):
    course = models.ForeignKey(Cohort, on_delete=models.CASCADE)
    slug = models.SlugField(blank=False)

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    instructions_url = models.URLField(
        blank=True,
        null=True,
        validators=[URLValidator(schemes=["http", "https"])],
        help_text="Optional link to the project instructions.",
    )

    submission_due_date = models.DateTimeField()

    learning_in_public_cap_project = models.IntegerField(default=14)
    peer_review_due_date = models.DateTimeField()
    time_spent_project_field = models.BooleanField(default=True)

    problems_comments_field = models.BooleanField(default=True)
    faq_contribution_field = models.BooleanField(
        default=True, help_text="Include field for FAQ contributions"
    )

    learning_in_public_cap_review = models.IntegerField(default=2)
    number_of_peers_to_evaluate = models.IntegerField(default=3)
    points_for_peer_review = models.IntegerField(default=3)
    time_spent_evaluation_field = models.BooleanField(default=True)

    state = models.CharField(
        max_length=2,
        choices=PROJECT_STATE_CHOICES,
        default=ProjectState.COLLECTING_SUBMISSIONS.value,
    )

    def __str__(self):
        return self.title

    def get_state_display(self):
        return PROJECT_STATE_LABELS.get(self.state, self.state)

    @property
    def points_to_pass(self):
        """Get the passing score from the course"""
        return self.course.project_passing_score

    def criteria_for_project(self):
        """Return this project's explicitly ordered review criteria."""

        return criteria_for_project(self)

    def get_review_criteria(self):
        """Compatibility-friendly alias for :meth:`criteria_for_project`."""

        return self.criteria_for_project()

    class Meta:
        unique_together = ("course", "slug")


class ProjectSubmission(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    student = models.ForeignKey(User, on_delete=models.CASCADE)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)

    github_link = models.URLField(validators=[URLValidator(), validate_url_200])
    commit_id = models.CharField(max_length=40)

    learning_in_public_links = models.JSONField(blank=True, null=True)
    faq_contribution = models.TextField(blank=True)
    faq_contribution_url = models.URLField(
        blank=True,
        null=True,
        help_text="Pull request or issue URL for the FAQ contribution",
    )

    time_spent = models.FloatField(blank=True, null=True)
    problems_comments = models.TextField(blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)

    project_score = models.IntegerField(default=0)
    project_faq_score = models.IntegerField(default=0)
    project_learning_in_public_score = models.IntegerField(default=0)

    peer_review_score = models.IntegerField(default=0)
    peer_review_learning_in_public_score = models.IntegerField(default=0)

    total_score = models.IntegerField(default=0)

    reviewed_enough_peers = models.BooleanField(default=False)
    passed = models.BooleanField(default=False)
    volunteer_review_only = models.BooleanField(default=False)

    def __str__(self):
        return f"project submission for enrollment {self.enrollment.id}"


class ProjectVote(models.Model):
    submission = models.ForeignKey(
        ProjectSubmission,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    voter = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="project_votes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("submission", "voter")

    def __str__(self):
        return (
            f"vote by user {self.voter_id} "
            f"for project submission {self.submission_id}"
        )


class ReviewCriteriaTypes(Enum):
    RADIO_BUTTONS = "RB"
    CHECKBOXES = "CB"


class ReviewCriteria(models.Model):
    """An independent criterion definition used through project assignments.

    ``course`` is retained as nullable, deprecated provenance for rows created
    by the legacy cohort-wide rubric.  New integrations must use
    :class:`ProjectCriteriaAssignment`; this field is not the operational
    ownership boundary and may be empty for newly defined criteria.
    """

    course = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=(
            "Deprecated legacy cohort provenance; use project criteria assignments."
        ),
    )
    description = models.CharField(max_length=255)

    options = models.JSONField(validators=[validate_review_criteria_options])
    # example:
    # options=[
    #     {"criteria": "Poor", "score": 0},
    #     {"criteria": "Satisfactory", "score": 1},
    #     {"criteria": "Good", "score": 2},
    #     {"criteria": "Excellent", "score": 3},
    # ]

    REVIEW_CRITERIA_TYPES = (
        (ReviewCriteriaTypes.RADIO_BUTTONS.value, "Radio Buttons"),
        (ReviewCriteriaTypes.CHECKBOXES.value, "Checkboxes"),
    )

    review_criteria_type = models.CharField(
        max_length=2, choices=REVIEW_CRITERIA_TYPES
    )

    def median_score(self) -> int:
        result = 0
        scores = []
        for option in self.options:
            score = option["score"]
            scores.append(score)

        if self.review_criteria_type == ReviewCriteriaTypes.RADIO_BUTTONS.value:
            result = statistics.median(scores)

        if self.review_criteria_type == ReviewCriteriaTypes.CHECKBOXES.value:
            result = sum(scores) / 2  # just give the middle score

        return math.ceil(result)

    @classmethod
    def for_project(cls, project):
        """Return only criteria explicitly assigned to ``project`` in order."""

        return criteria_for_project(project)

    def __str__(self):
        return self.description


class ProjectCriteriaAssignment(models.Model):
    """An ordered assignment of one criterion definition to one project."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="criteria_assignments",
    )
    criteria = models.ForeignKey(
        ReviewCriteria,
        on_delete=models.CASCADE,
        related_name="project_assignments",
    )
    position = models.PositiveIntegerField()

    def clean(self):
        super().clean()
        if not self.project_id or not self.criteria_id:
            return

        project = self.project
        criteria = self.criteria
        errors = {}

        if (
            criteria.course_id is not None
            and criteria.course_id != project.course_id
        ):
            errors["criteria"] = (
                "A criterion's deprecated cohort provenance must match the project cohort."
            )

        other_assignments = type(self).objects.filter(criteria_id=self.criteria_id)
        if self.pk:
            other_assignments = other_assignments.exclude(pk=self.pk)
        if other_assignments.exclude(
            project__course_id=project.course_id
        ).exists():
            errors["criteria"] = (
                "A criterion can only be assigned to projects in one cohort."
            )

        if errors:
            raise ValidationError(errors)

    @classmethod
    def for_project(cls, project):
        return cls.objects.filter(project=project).order_by("position", "id")

    @property
    def criterion(self):
        """Singular compatibility alias for the assigned criterion."""

        return self.criteria

    @property
    def review_criteria(self):
        """Lifecycle terminology alias for the assigned criterion."""

        return self.criteria

    def __str__(self):
        return f"{self.project}: {self.criteria}"

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "position"),
                name="courses_project_criteria_position_unique",
            ),
            models.UniqueConstraint(
                fields=("project", "criteria"),
                name="courses_project_criteria_definition_unique",
            ),
        ]


def criteria_for_project(project):
    """Return a project's explicit rubric without cohort-wide fallback."""

    if not getattr(project, "pk", None):
        return ReviewCriteria.objects.none()

    return ReviewCriteria.objects.filter(
        project_assignments__project_id=project.pk,
    ).order_by(
        "project_assignments__position",
        "project_assignments__id",
    )


class PeerReviewState(Enum):
    TO_REVIEW = "TR"
    SUBMITTED = "SU"


PEER_REVIEW_STATE_CHOICES = _build_enum_choices(PeerReviewState)

PEER_REVIEW_STATE_LABELS = {
    PeerReviewState.TO_REVIEW.value: "To review",
    PeerReviewState.SUBMITTED.value: "Submitted",
}


class PeerReview(models.Model):
    submission_under_evaluation = models.ForeignKey(
        ProjectSubmission,
        related_name="reviews_under_evaluation",
        on_delete=models.CASCADE,
    )
    reviewer = models.ForeignKey(
        ProjectSubmission,
        related_name="reviewers",
        on_delete=models.CASCADE,
    )
    note_to_peer = models.TextField()
    learning_in_public_links = models.JSONField(blank=True, null=True)
    time_spent_reviewing = models.FloatField(blank=True, null=True)
    problems_comments = models.TextField(blank=True)

    optional = models.BooleanField(
        default=False, null=False, blank=False
    )

    submitted_at = models.DateTimeField(null=True, blank=True)

    state = models.CharField(
        max_length=2,
        choices=PEER_REVIEW_STATE_CHOICES,
        default=PeerReviewState.TO_REVIEW.value,
    )

    def __str__(self):
        return f"Peer review {self.id}, state={self.state}"

    def get_state_display(self):
        return PEER_REVIEW_STATE_LABELS.get(self.state, self.state)


class CriteriaResponse(models.Model):
    review = models.ForeignKey(
        PeerReview,
        related_name="criteria_responses",
        on_delete=models.CASCADE,
    )
    criteria = models.ForeignKey(
        ReviewCriteria, on_delete=models.CASCADE
    )
    answer = models.CharField(max_length=255, blank=True, null=True)

    def get_scores(self):
        criteria = self.criteria

        if not self.answer:
            return [0]

        answers = self.answer.split(",")
        answer_indices = []
        for answer in answers:
            answer_index = int(answer) - 1
            answer_indices.append(answer_index)

        scores = []
        for answer_index in answer_indices:
            option = criteria.options[answer_index]
            score = option["score"]
            scores.append(score)

        return scores

    def get_score(self):
        total_score = 0
        scores = self.get_scores()
        for score in scores:
            total_score += score
        return total_score

    def __str__(self):
        return f"{self.criteria.description}: {self.answer}"


class ProjectEvaluationScore(models.Model):
    submission = models.ForeignKey(
        ProjectSubmission, on_delete=models.CASCADE
    )

    review_criteria = models.ForeignKey(
        ReviewCriteria, on_delete=models.CASCADE
    )

    score = models.IntegerField()

    def __str__(self):
        return f"Score: {self.score} for submission by {self.submission.id}"


class ProjectStatistics(models.Model):
    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name="statistics"
    )

    total_submissions = models.IntegerField(default=0)

    # Fields for project_score
    min_project_score = models.IntegerField(null=True, blank=True)
    max_project_score = models.IntegerField(null=True, blank=True)
    avg_project_score = models.FloatField(null=True, blank=True)
    median_project_score = models.FloatField(null=True, blank=True)
    q1_project_score = models.FloatField(null=True, blank=True)
    q3_project_score = models.FloatField(null=True, blank=True)

    # Fields for project_learning_in_public_score
    min_project_learning_in_public_score = models.IntegerField(null=True, blank=True)
    max_project_learning_in_public_score = models.IntegerField(null=True, blank=True)
    avg_project_learning_in_public_score = models.FloatField(null=True, blank=True)
    median_project_learning_in_public_score = models.FloatField(null=True, blank=True)
    q1_project_learning_in_public_score = models.FloatField(null=True, blank=True)
    q3_project_learning_in_public_score = models.FloatField(null=True, blank=True)

    # Fields for peer_review_score
    min_peer_review_score = models.IntegerField(null=True, blank=True)
    max_peer_review_score = models.IntegerField(null=True, blank=True)
    avg_peer_review_score = models.FloatField(null=True, blank=True)
    median_peer_review_score = models.FloatField(null=True, blank=True)
    q1_peer_review_score = models.FloatField(null=True, blank=True)
    q3_peer_review_score = models.FloatField(null=True, blank=True)

    # Fields for peer_review_learning_in_public_score
    min_peer_review_learning_in_public_score = models.IntegerField(null=True, blank=True)
    max_peer_review_learning_in_public_score = models.IntegerField(null=True, blank=True)
    avg_peer_review_learning_in_public_score = models.FloatField(null=True, blank=True)
    median_peer_review_learning_in_public_score = models.FloatField(null=True, blank=True)
    q1_peer_review_learning_in_public_score = models.FloatField(null=True, blank=True)
    q3_peer_review_learning_in_public_score = models.FloatField(null=True, blank=True)

    # Fields for total_score
    min_total_score = models.IntegerField(null=True, blank=True)
    max_total_score = models.IntegerField(null=True, blank=True)
    avg_total_score = models.FloatField(null=True, blank=True)
    median_total_score = models.FloatField(null=True, blank=True)
    q1_total_score = models.FloatField(null=True, blank=True)
    q3_total_score = models.FloatField(null=True, blank=True)

    # Fields for time_spent
    min_time_spent = models.FloatField(null=True, blank=True)
    max_time_spent = models.FloatField(null=True, blank=True)
    avg_time_spent = models.FloatField(null=True, blank=True)
    median_time_spent = models.FloatField(null=True, blank=True)
    q1_time_spent = models.FloatField(null=True, blank=True)
    q3_time_spent = models.FloatField(null=True, blank=True)

    last_calculated = models.DateTimeField(auto_now=True)

    def get_value(self, field_name, stats_type):
        attribute_name = f"{stats_type}_{field_name}"
        return getattr(self, attribute_name)

    def get_stat_fields(self):
        sections = project_stat_sections()
        return build_stat_fields(self, sections)

    def __str__(self):
        return f"Statistics for {self.project.slug}"
