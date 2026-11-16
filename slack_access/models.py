"""Website approval and accountability; no provider secrets or raw events."""

import uuid

from django.conf import settings
from django.db import models


class Membership(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slack_membership",
    )
    slack_user_id = models.CharField(max_length=32, unique=True)
    sponsor = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    present = models.BooleanField(default=True)
    banned_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"slack-member:{self.pk}"


class Application(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class State(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved"
        JOINED = "joined", "Joined Slack"
        REJECTED = "rejected", "Declined"
        BANNED = "banned", "Access revoked"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    who = models.CharField(max_length=500)
    work = models.CharField(max_length=500)
    what = models.TextField(max_length=2000)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    sponsor = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.PROTECT)
    vouch_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    approval_source = models.CharField(max_length=16, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slack_approvals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    redirect_digest = models.CharField(max_length=64, blank=True)
    redirect_issued_at = models.DateTimeField(null=True, blank=True)
    redirect_consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("review_applications", "Review Slack applications and join reports"),
            ("ban_members", "Revoke Slack website access with accountability cascade"),
        ]

    def __str__(self):
        return f"slack-application:{self.pk}"


class JoinObservation(models.Model):
    slack_user_id = models.CharField(max_length=32, unique=True)
    inviter_slack_id = models.CharField(max_length=32, blank=True)
    classification = models.CharField(
        max_length=24,
        choices=[
            ("website_approved", "Website approved"),
            ("outside_flow", "Outside website approval"),
            ("historical", "Existing at monitoring baseline"),
            ("unknown", "Unresolved identity or join time"),
        ],
    )
    application = models.ForeignKey(Application, null=True, blank=True, on_delete=models.SET_NULL)
    observed_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"slack-join:{self.pk}"


class MonitorState(models.Model):
    key = models.CharField(max_length=32, primary_key=True)
    baseline_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.key


class Decision(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"slack-decision:{self.pk}"


class ActionBucket(models.Model):
    subject_hash = models.CharField(max_length=64, primary_key=True)
    window_started_at = models.DateTimeField(db_index=True)
    hits = models.PositiveIntegerField(default=0)
    revision = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return "slack-action-counter"
