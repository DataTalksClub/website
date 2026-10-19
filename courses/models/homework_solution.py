"""Private-source solutions, published independently of submission/scoring state."""

from django.db import models
from django.db.models import Q
from django.utils import timezone


class HomeworkSolutionQuerySet(models.QuerySet):
    def published(self, at=None):
        return self.filter(Q(published=True) | Q(publish_at__lte=at or timezone.now()))


class HomeworkSolution(models.Model):
    homework = models.OneToOneField(
        "courses.Homework", on_delete=models.CASCADE, related_name="solution"
    )
    markdown = models.TextField()
    answers = models.JSONField(default=list)
    published = models.BooleanField(default=False)
    publish_at = models.DateTimeField(null=True, blank=True)
    source_path = models.CharField(max_length=512, unique=True)
    source_commit = models.CharField(max_length=40)

    objects = HomeworkSolutionQuerySet.as_manager()

    def __str__(self):
        return f"Solution for homework {self.homework_id}"
