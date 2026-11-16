"""Compare complete Slack roster snapshots with website approvals, without Slack writes."""

from collections import Counter

from allauth.account.models import EmailAddress
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Application, JoinObservation, Membership, MonitorState
from .services import _lock, observe_join
from .slack_api import SlackReadClient


def poll_joins(client=None) -> dict[str, int]:
    if client is None:
        client = SlackReadClient(
            getattr(settings, "SLACK_MONITOR_TOKEN", ""),
            getattr(settings, "SLACK_WORKSPACE_ID", ""),
        )
    # A failed or incomplete fetch never establishes a baseline or advances monitoring state.
    members = client.members()
    counts = {"historical": 0, "website_approved": 0, "outside_flow": 0, "unknown": 0}
    with transaction.atomic():
        MonitorState.objects.get_or_create(key="slack-joins")
        state = MonitorState.objects.select_for_update().get(key="slack-joins")
        _lock()
        historical = state.baseline_at is None
        observations = {row.slack_user_id: row for row in JoinObservation.objects.all()}
        memberships = {row.slack_user_id: row for row in Membership.objects.all()}
        # Resolve case-insensitively in memory; one unbounded SQL IN-list exceeds SQLite's
        # variable limit for a real community roster. Multiple verified claims stay unbound.
        verified: dict[str, list[int]] = {}
        for email, user_id in EmailAddress.objects.filter(verified=True).values_list(
            "email", "user_id"
        ):
            verified.setdefault(email.casefold(), []).append(user_id)
        unique_users = {
            email: user_ids[0] for email, user_ids in verified.items() if len(user_ids) == 1
        }
        roster_claims = Counter(
            unique_users[member.email.casefold()]
            for member in members
            if member.email.casefold() in unique_users
        )
        unique_users = {
            email: user_id for email, user_id in unique_users.items() if roster_claims[user_id] == 1
        }
        application_users = set(Application.objects.values_list("user_id", flat=True))
        if historical:
            assigned_users = {row.user_id for row in memberships.values() if row.user_id}
            additions = []
            baseline = []
            for member in members:
                if member.slack_user_id not in memberships:
                    user_id = unique_users.get(member.email.casefold())
                    if user_id in assigned_users:
                        user_id = None
                    if user_id is not None:
                        assigned_users.add(user_id)
                    membership_row = Membership(slack_user_id=member.slack_user_id, user_id=user_id)
                    additions.append(membership_row)
                    memberships[member.slack_user_id] = membership_row
                if member.slack_user_id not in observations:
                    observation_row = JoinObservation(
                        slack_user_id=member.slack_user_id,
                        inviter_slack_id=member.inviter_slack_id,
                        classification="historical",
                        joined_at=member.joined_at,
                    )
                    baseline.append(observation_row)
                    observations[member.slack_user_id] = observation_row
            Membership.objects.bulk_create(additions, batch_size=500)
            JoinObservation.objects.bulk_create(baseline, batch_size=500)
            counts["historical"] = len(baseline)
        for member in members:
            existing = observations.get(member.slack_user_id)
            membership = memberships.get(member.slack_user_id)
            identity = unique_users.get(member.email.casefold())
            late_identity = identity is not None and (
                membership is None or membership.user_id is None
            )
            application_binding = (
                identity in application_users
                and existing is not None
                and existing.application_id is None
            )
            new_inviter = (
                existing is not None and member.inviter_slack_id and not existing.inviter_slack_id
            )
            if existing is not None and not (late_identity or application_binding or new_inviter):
                continue
            # Preserve observations and their classification on repeated roster polls.
            email_key = member.email.casefold()
            identity_email = member.email
            if email_key in verified and email_key not in unique_users:
                # Do not let the per-member service pick the first of conflicting
                # verified claims that the complete snapshot already proved ambiguous.
                # Withhold identity evidence so the observation remains unknown.
                identity_email = ""
            observation = observe_join(
                member.slack_user_id,
                email=identity_email,
                inviter_slack_id=member.inviter_slack_id,
                joined_at=member.joined_at,
                historical=historical,
            )
            if existing is None:
                counts[observation.classification] += 1
                observations[member.slack_user_id] = observation
        # This is an observed website membership flag, never provider moderation. A
        # failed fetch exits before this transaction and cannot mark anyone absent.
        current_ids = {member.slack_user_id for member in members}
        presence_changes = []
        for membership in memberships.values():
            present = membership.slack_user_id in current_ids
            if membership.present != present:
                membership.present = present
                presence_changes.append(membership)
        Membership.objects.bulk_update(presence_changes, ["present"], batch_size=500)
        now = timezone.now()
        if historical:
            state.baseline_at = now
        state.last_success_at = now
        state.save(update_fields=["baseline_at", "last_success_at"])
    return counts
