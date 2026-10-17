"""Operator drafts, immutable publications, and durable launch schedules."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models


class AnnouncementTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    channel = models.CharField(
        max_length=10, choices=(("email", "Email"), ("telegram", "Telegram"))
    )
    kind = models.CharField(
        max_length=12, choices=(("cohort", "Cohort"), ("module", "Module"), ("project", "Project"))
    )
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    offset_minutes = models.IntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)
    archived = models.BooleanField(default=False)
    seed_key = models.CharField(max_length=100, null=True, unique=True, blank=True)

    def __str__(self):
        return f"AnnouncementTemplate:{self.pk}"


class AnnouncementTemplateVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        AnnouncementTemplate, on_delete=models.PROTECT, related_name="versions"
    )
    number = models.PositiveIntegerField()
    channel = models.CharField(max_length=10)
    kind = models.CharField(max_length=12)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    offset_minutes = models.IntegerField()
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("template", "number"), name="announcement_version_uq")
        ]

    def __str__(self):
        return f"AnnouncementTemplateVersion:{self.pk}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Published announcement versions are immutable.")
        return super().save(*args, **kwargs)


class AnnouncementAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.OneToOneField(
        "courses.Course", on_delete=models.CASCADE, related_name="announcement_assignment"
    )
    versions = models.ManyToManyField(AnnouncementTemplateVersion, blank=True)
    revision = models.PositiveIntegerField(default=1)
    # Persist only a reference. Destination and credential resolve at send time.
    telegram_destination_key = models.SlugField(blank=True)

    def __str__(self):
        return f"AnnouncementAssignment:{self.pk}"


class CohortAnnouncementPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cohort = models.OneToOneField(
        "courses.Cohort", on_delete=models.CASCADE, related_name="announcement_plan"
    )
    versions = models.ManyToManyField(AnnouncementTemplateVersion, blank=True)
    override_defaults = models.BooleanField(default=False)
    launched = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"CohortAnnouncementPlan:{self.pk}"


class Announcement(models.Model):
    _telegram_destination_key: str | None = None
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cohort = models.ForeignKey(
        "courses.Cohort", on_delete=models.PROTECT, related_name="announcements"
    )
    version = models.ForeignKey(AnnouncementTemplateVersion, null=True, on_delete=models.PROTECT)
    channel = models.CharField(max_length=10)
    target_kind = models.CharField(max_length=24, default="cohort")
    target_id = models.CharField(max_length=40)
    generated_key = models.CharField(max_length=180, null=True, unique=True, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    context = models.JSONField(default=dict)
    context_overrides = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="draft")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    anchor_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    approved_revision = models.PositiveIntegerField(null=True)
    reason_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = (
            ("read_course_launch", "Read course launch"),
            ("manage_course_launch", "Manage course launch"),
            ("send_course_launch", "Send course launch"),
        )

    def __str__(self):
        return f"Announcement:{self.pk}"


class AnnouncementDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(
        Announcement, on_delete=models.PROTECT, related_name="deliveries"
    )
    recipient_key = models.CharField(max_length=100)
    state = models.CharField(max_length=20, default="pending")
    external_id = models.CharField(max_length=200, blank=True)
    email_delivery = models.OneToOneField(
        "cb_mail.EmailDelivery", null=True, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("announcement", "recipient_key"), name="announcement_delivery_uq"
            )
        ]

    def __str__(self):
        return f"AnnouncementDelivery:{self.pk}"
