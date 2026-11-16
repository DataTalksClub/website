"""Transactional application policy, independent of HTML and Slack transport."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from allauth.account.models import EmailAddress
from community_base.curriculum.models import Enrollment
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.identity_resolution import identity_state_eligible
from accounts.services.email_verification import is_account_email_verified
from accounts.studio_authorization import has_explicit_permission
from management_auth.models import APIPrincipal
from management_auth.services import principal_has_permission

from .models import Application, Decision, JoinObservation, Membership, MonitorState


# One lock serializes approval/sponsorship/ban/redemption decisions across web workers.
def _lock():
    MonitorState.objects.get_or_create(key="graph")
    MonitorState.objects.select_for_update().get(key="graph")


def _verified(user):
    if (
        not user.is_active
        or not identity_state_eligible(user)
        or not is_account_email_verified(user, user.email)
    ):
        raise PermissionDenied("A verified active account is required.")


def _unbanned(user):
    if Membership.objects.filter(user=user, banned_at__isnull=False).exists():
        raise PermissionDenied("Slack access has been revoked.")
    if Application.objects.filter(user=user, state=Application.State.BANNED).exists():
        raise PermissionDenied("Slack access has been revoked.")


def reviewer(user, *, ban=False):
    permission = "slack_access.ban_members" if ban else "slack_access.review_applications"
    if isinstance(user, APIPrincipal):
        if (
            not user.is_active
            or not principal_has_permission(user, permission)
            or (
                user.kind == APIPrincipal.Kind.HUMAN
                and (user.user is None or not user.user.is_staff)
            )
        ):
            raise PermissionDenied("Slack review permission is required.")
        return
    if (
        not user.is_active
        or not user.is_staff
        or not identity_state_eligible(user)
        or not has_explicit_permission(user, permission)
    ):
        raise PermissionDenied("Slack review permission is required.")


def _actor_user(actor):
    return actor.user if isinstance(actor, APIPrincipal) else actor


def active_member(user):
    _verified(user)
    _unbanned(user)
    member = Membership.objects.filter(user=user, banned_at__isnull=True, present=True).first()
    if member is None:
        raise PermissionDenied("Verified Slack membership is required.")
    return member


def _approve(application, source, actor=None):
    _unbanned(application.user)
    if application.state != Application.State.PENDING:
        raise PermissionDenied("This application is no longer pending.")
    if application.sponsor_id and (
        application.sponsor.banned_at or not application.sponsor.present
    ):
        raise PermissionDenied("Sponsor access has been revoked.")
    application.state = Application.State.APPROVED
    application.approval_source = source
    application.approved_by = _actor_user(actor)
    application.approved_at = timezone.now()
    application.save()
    Decision.objects.create(
        application=application, actor=_actor_user(actor), action=f"approve_{source}"
    )
    return application


@transaction.atomic
def apply(user, *, who, work, what, sponsor=None):
    _lock()
    _verified(user)
    _unbanned(user)
    if any(not isinstance(v, str) or not v.strip() for v in (who, work, what)):
        raise ValueError("All questionnaire answers are required.")
    if len(who) > 500 or len(work) > 500 or len(what) > 2000:
        raise ValueError("Questionnaire answer too long.")
    if sponsor is not None:
        sponsor = Membership.objects.select_for_update().get(pk=sponsor.pk)
        if sponsor.banned_at or not sponsor.present or sponsor.user_id == user.pk:
            raise PermissionDenied("Invalid sponsor.")
    application, created = Application.objects.get_or_create(
        user=user,
        defaults={
            "who": who.strip(),
            "work": work.strip(),
            "what": what.strip(),
            "sponsor": sponsor,
        },
    )
    if created:
        Decision.objects.create(application=application, actor=user, action="apply")
    # Active current-domain enrollment is authoritative. A pending course application is not.
    if (
        application.state == Application.State.PENDING
        and Enrollment.objects.filter(user=user, unenrolled_at__isnull=True).exists()
    ):
        return _approve(application, "course")
    return application


@transaction.atomic
def refresh_course_approval(user):
    """Reconcile a pending request with current enrollment, including later enrollment."""
    _lock()
    _verified(user)
    _unbanned(user)
    application = Application.objects.select_for_update().filter(user=user).first()
    if (
        application
        and application.state == Application.State.PENDING
        and Enrollment.objects.filter(user=user, unenrolled_at__isnull=True).exists()
    ):
        return _approve(application, "course")
    return application


@transaction.atomic
def vouch(user, key):
    _lock()
    member = active_member(user)
    application = Application.objects.select_for_update().get(vouch_key=key)
    if application.user_id == user.pk or application.state != Application.State.PENDING:
        raise PermissionDenied("This request cannot be vouched for.")
    if application.sponsor_id and application.sponsor_id != member.pk:
        raise PermissionDenied("An accountable sponsor is already recorded.")
    if application.sponsor_id == member.pk:
        return application
    application.sponsor = member
    application.save(update_fields=["sponsor"])
    Decision.objects.create(application=application, actor=user, action="vouch")
    return application


@transaction.atomic
def review(user, application_id, *, approve):
    _lock()
    reviewer(user)
    application = (
        Application.objects.select_for_update().select_related("user").get(pk=application_id)
    )
    _verified(application.user)
    if approve:
        if not application.sponsor_id:
            raise PermissionDenied("A member invitation or vouch is required.")
        return _approve(application, "staff", user)
    if application.state != Application.State.PENDING:
        raise PermissionDenied("This application is no longer pending.")
    application.state = Application.State.REJECTED
    application.redirect_digest = ""
    application.save()
    Decision.objects.create(application=application, actor=_actor_user(user), action="reject")
    return application


@transaction.atomic
def issue_link(user):
    _lock()
    _verified(user)
    _unbanned(user)
    application = Application.objects.select_for_update().filter(user=user).first()
    if application is None:
        raise PermissionDenied("No Slack application exists.")
    if application.state != Application.State.APPROVED or application.redirect_consumed_at:
        raise PermissionDenied("No invitation is available.")
    destination()  # Fail closed before minting when integration is not configured.
    token = secrets.token_urlsafe(32)
    application.redirect_digest = hashlib.sha256(token.encode()).hexdigest()
    application.redirect_issued_at = timezone.now()
    application.save(update_fields=["redirect_digest", "redirect_issued_at"])
    return token


def destination():
    value = getattr(settings, "SLACK_INVITE_URL", "")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "join.slack.com"
            and parsed.path.startswith("/t/")
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.port in (None, 443)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Slack invitation is not configured. Please contact the community team.")
    return value


@transaction.atomic
def redeem(user, token):
    _lock()
    _verified(user)
    _unbanned(user)
    application = Application.objects.select_for_update().filter(user=user).first()
    if application is None:
        raise PermissionDenied("No Slack application exists.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        application.state != Application.State.APPROVED
        or application.redirect_consumed_at
        or not application.redirect_digest
        or not secrets.compare_digest(digest, application.redirect_digest)
        or not application.redirect_issued_at
        or application.redirect_issued_at < timezone.now() - timedelta(days=7)
    ):
        raise PermissionDenied("This invitation is unavailable or already used.")
    target = destination()
    application.redirect_consumed_at = timezone.now()
    application.redirect_digest = ""
    application.save(update_fields=["redirect_consumed_at", "redirect_digest"])
    Decision.objects.create(application=application, actor=user, action="redeem")
    return target


def ban_scope(member_id):
    root = Membership.objects.get(pk=member_id)
    affected = {root.pk}
    frontier = affected
    while frontier:
        # The application edge already records responsibility before a later roster poll
        # binds the invitee's Membership. Traverse both durable edges in both directions.
        parents = set(
            Membership.objects.filter(pk__in=frontier)
            .exclude(sponsor_id=None)
            .values_list("sponsor_id", flat=True)
        )
        users = Membership.objects.filter(pk__in=frontier).values_list("user_id", flat=True)
        parents.update(
            Application.objects.filter(user_id__in=users)
            .exclude(sponsor_id=None)
            .values_list("sponsor_id", flat=True)
        )
        children = set(
            Membership.objects.filter(sponsor_id__in=frontier).values_list("pk", flat=True)
        )
        application_users = Application.objects.filter(sponsor_id__in=frontier).values_list(
            "user_id", flat=True
        )
        children.update(
            Membership.objects.filter(user_id__in=application_users).values_list("pk", flat=True)
        )
        frontier = (parents | children) - affected
        affected.update(frontier)
    return affected


@transaction.atomic
def ban(user, member_id):
    """Explicit site moderation: ban accountable ancestors and all their descendants.

    This does not call Slack. Monitoring never invokes this service.
    """
    _lock()
    reviewer(user, ban=True)
    affected = ban_scope(member_id)
    now = timezone.now()
    Membership.objects.filter(pk__in=affected, banned_at__isnull=True).update(banned_at=now)
    users = Membership.objects.filter(pk__in=affected).values_list("user_id", flat=True)
    applications = Application.objects.filter(user_id__in=users) | Application.objects.filter(
        sponsor_id__in=affected
    )
    for application in applications.distinct():
        application.state = Application.State.BANNED
        application.redirect_digest = ""
        application.save(update_fields=["state", "redirect_digest"])
        Decision.objects.create(
            application=application, actor=_actor_user(user), action="ban_cascade"
        )
    return len(affected)


@transaction.atomic
def observe_join(slack_user_id, email="", inviter_slack_id="", joined_at=None, historical=False):
    _lock()
    existing = JoinObservation.objects.filter(slack_user_id=slack_user_id).first()
    if existing:
        if inviter_slack_id and not existing.inviter_slack_id:
            existing.inviter_slack_id = inviter_slack_id
            existing.save(update_fields=["inviter_slack_id"])
    # Email ambiguity never grants membership or invalidates another account's link.
    addresses = (
        list(
            EmailAddress.objects.filter(email__iexact=email, verified=True).select_related("user")[
                :2
            ]
        )
        if email
        else []
    )
    person = addresses[0].user if len(addresses) == 1 else None
    ambiguous = len(addresses) > 1
    if (
        person
        and Membership.objects.filter(user=person).exclude(slack_user_id=slack_user_id).exists()
    ):
        person = None
        ambiguous = True
    application = Application.objects.filter(user=person).first() if person else None
    classification = "historical" if historical else ("outside_flow" if email else "unknown")
    if not historical and ambiguous:
        classification = "unknown"
    if not historical and application and application.state == Application.State.APPROVED:
        if joined_at is not None:
            classification = (
                "website_approved" if application.approved_at <= joined_at else "outside_flow"
            )
        else:
            # Polling alone cannot prove whether approval preceded the actual Slack join.
            classification = "unknown"
    member, _ = Membership.objects.get_or_create(slack_user_id=slack_user_id)
    if person and not Membership.objects.filter(user=person).exclude(pk=member.pk).exists():
        member.user = person
        if (
            application
            and application.state in {Application.State.APPROVED, Application.State.JOINED}
            and member.sponsor_id is None
        ):
            member.sponsor = application.sponsor
        member.save()
    if application:
        application.joined_at = application.joined_at or joined_at or timezone.now()
        application.redirect_digest = ""
        if application.state == Application.State.APPROVED:
            application.state = Application.State.JOINED
        application.save(update_fields=["joined_at", "redirect_digest", "state"])
    if existing:
        if application and existing.application_id is None:
            existing.application = application
            existing.save(update_fields=["application"])
        return existing
    return JoinObservation.objects.create(
        slack_user_id=slack_user_id,
        inviter_slack_id=inviter_slack_id,
        classification=classification,
        application=application,
        joined_at=joined_at,
    )
