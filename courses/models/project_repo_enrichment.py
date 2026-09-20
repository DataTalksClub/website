"""Repository-side enrichment for the learner-project gallery (issue #416).

One row per distinct GitHub repository submitted to any project, holding the
facts observed about the repository itself: what it does, why it is worth
reading, observed gaps, topics, and whether the repository is still
reachable. Score, cohort, course, and author facts stay on
:class:`~courses.models.project.ProjectSubmission` -- this table only ever
carries what could not be derived from the submissions alone.

Rows are written by an ingest run, never by site visitors; the gallery reads
them to annotate submission rows and falls back to plain rendering when a
submission's repository has no row (unknown is absence).
"""

from django.db import models


class ProjectRepoEnrichment(models.Model):
    repo = models.CharField(
        max_length=300,
        unique=True,
        help_text="Original 'owner/name' slug the submission was keyed by.",
    )
    repo_lower = models.CharField(
        max_length=300,
        unique=True,
        db_index=True,
        help_text="Lowercased 'owner/name' for case-insensitive joins.",
    )
    effective_url = models.URLField(
        blank=True,
        help_text="Current repository URL, following renames. Blank when gone.",
    )
    availability = models.CharField(
        max_length=300,
        blank=True,
        help_text="'live' when reachable, otherwise the unavailability reason.",
    )
    is_unavailable = models.BooleanField(default=False, db_index=True)
    is_coursework = models.BooleanField(
        default=False,
        help_text="Repository is a homework/coursework collection, not a capstone.",
    )
    card_summary = models.CharField(max_length=200, blank=True)
    what_it_is = models.TextField(blank=True)
    interesting = models.TextField(blank=True)
    why_check = models.TextField(blank=True)
    improvements = models.TextField(blank=True)
    topics = models.TextField(
        blank=True,
        help_text="Comma-separated topic labels.",
    )
    confidence = models.CharField(
        max_length=20,
        blank=True,
        help_text="Write-up confidence: high, medium, low, baseline, or blank.",
    )
    structured = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "course-structured-v1 record extracted from the repository README "
            "by the ingest run; null when no structured extraction exists."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "project repo enrichment"
        verbose_name_plural = "project repo enrichments"
        ordering = ("repo",)

    def __str__(self) -> str:
        return self.repo

    @property
    def topic_list(self) -> list[str]:
        return [topic for topic in self.topics.split(",") if topic]
