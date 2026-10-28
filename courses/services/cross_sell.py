"""Suggest other courses after a registration (cross-sell).

The relevance rule is documented in ``_docs/runbooks/cross-sell.md``. In
short, a candidate is an active campaign that is not excluded, does not
promote the registered cohort or another cohort of the same course family,
whose cohort (if it has one yet) is visible and not finished, and that the
visitor has not registered for or enrolled in. Curated
``RegistrationCampaign.suggested_campaigns`` come first, then every other
candidate; each group is ordered by cohort start date (soonest first, no
date last) and then by slug, and the first ``CROSS_SELL_MAX_SUGGESTIONS``
win.

``register_suggested`` turns a just-submitted registration into extra
registrations for chosen suggestions without asking for any data again.
"""

from dataclasses import dataclass
from datetime import date
from functools import partial
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from course_management.observability import record_event
from courses.models.cohort import CourseRegistration, Enrollment, RegistrationCampaign
from courses.services.registration_contacts import queue_registration_contacts

SOURCE_ONE_CLICK = "xsell"
SOURCE_LINK = "xsell-link"
SOURCE_EMAIL = "xsell-email"
LINK_SOURCES = {"xsell": SOURCE_LINK, "xsell-email": SOURCE_EMAIL}
SESSION_SOURCE_KEY = "cross_sell_source"
SESSION_FLASH_KEY = "cross_sell_registered"

COPIED_FIELDS = (
    "name",
    "email",
    "company_name",
    "country",
    "region",
    "role",
    "years_experience",
    "accepted_newsletter",
)


@dataclass(frozen=True)
class Suggestion:
    campaign: RegistrationCampaign
    one_click: bool
    url: str
    description: str

    @property
    def cohort(self):
        return self.campaign.current_course

    @property
    def start_date(self):
        cohort = self.cohort
        return cohort.start_date if cohort is not None else None


def max_suggestions() -> int:
    return int(getattr(settings, "CROSS_SELL_MAX_SUGGESTIONS", 3))


def excluded_slugs() -> set[str]:
    return set(getattr(settings, "CROSS_SELL_EXCLUDED_CAMPAIGNS", []) or [])


def needs_application(campaign: RegistrationCampaign) -> bool:
    """Application-style campaigns need answers and consent: link only."""

    cohort = campaign.current_course
    return bool(
        campaign.requires_approval or (cohort is not None and cohort.requires_registration_approval)
    )


def suggestion_url(campaign: RegistrationCampaign, source_campaign, src="xsell") -> str:
    path = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
    query = urlencode({"src": src, "from": source_campaign.slug})
    return f"{path}?{query}"


def _description(campaign: RegistrationCampaign) -> str:
    cohort = campaign.current_course
    if cohort is None:
        return campaign.meta_description or ""
    text = cohort.promo_summary or cohort.course.outcome or cohort.course.description or ""
    return text.strip().splitlines()[0] if text.strip() else ""


def _sort_key(campaign: RegistrationCampaign):
    cohort = campaign.current_course
    start = cohort.start_date if cohort is not None else None
    return (start is None, start or date.max, campaign.slug)


def _taken_campaign_ids(user, email: str) -> tuple[set[int], set[int]]:
    """Campaigns already registered for, and cohorts already enrolled in."""

    identity = Q(pk__in=[])
    email_normalized = (email or "").strip().lower()
    if email_normalized:
        identity |= Q(email_normalized=email_normalized)
    if user is not None and user.is_authenticated:
        identity |= Q(user=user)
        if user.email:
            identity |= Q(email_normalized=user.email.strip().lower())
    registered = set(
        CourseRegistration.objects.filter(identity).values_list("campaign_id", flat=True)
    )
    enrolled = set()
    if user is not None and user.is_authenticated:
        enrolled = set(Enrollment.objects.filter(student=user).values_list("course_id", flat=True))
    return registered, enrolled


def _eligible(campaign, source_campaign, registered, enrolled, excluded) -> bool:
    if campaign.pk == source_campaign.pk or campaign.pk in registered:
        return False
    if campaign.exclude_from_suggestions or campaign.slug in excluded:
        return False
    cohort = campaign.current_course
    source_cohort = source_campaign.current_course
    if cohort is None:
        return True
    if cohort.finished or not cohort.visible or not cohort.course.visible:
        return False
    if cohort.pk in enrolled:
        return False
    if source_cohort is not None and cohort.course_id == source_cohort.course_id:
        return False
    return True


def suggestions(source_campaign, user=None, email: str = "", limit: int | None = None):
    """Up to ``limit`` :class:`Suggestion` rows for a registration page."""

    limit = max_suggestions() if limit is None else limit
    if limit <= 0 or source_campaign is None:
        return []
    registered, enrolled = _taken_campaign_ids(user, email)
    excluded = excluded_slugs()
    candidates = list(
        RegistrationCampaign.objects.filter(is_active=True)
        .select_related("current_course", "current_course__course")
        .order_by("slug")
    )
    curated_ids = set(source_campaign.suggested_campaigns.values_list("pk", flat=True))
    eligible = [
        campaign
        for campaign in candidates
        if _eligible(campaign, source_campaign, registered, enrolled, excluded)
    ]
    curated = sorted((c for c in eligible if c.pk in curated_ids), key=_sort_key)
    fallback = sorted((c for c in eligible if c.pk not in curated_ids), key=_sort_key)
    chosen = (curated + fallback)[:limit]
    return [
        Suggestion(
            campaign=campaign,
            one_click=not needs_application(campaign),
            url=suggestion_url(campaign, source_campaign),
            description=_description(campaign),
        )
        for campaign in chosen
    ]


def record_impression(request, source_campaign, items) -> None:
    if not items:
        return
    record_event(
        "cross_sell.impression",
        request=request,
        user=getattr(request, "user", None),
        properties={
            "from_campaign": source_campaign.slug,
            "suggested": ",".join(item.campaign.slug for item in items),
        },
    )


def remember_click(request, campaign) -> None:
    """A visit from a suggestion link: count the click, keep its source."""

    src = request.GET.get("src", "")
    source = LINK_SOURCES.get(src)
    if source is None:
        return
    from_slug = request.GET.get("from", "")[:64]
    pending = request.session.get(SESSION_SOURCE_KEY, {})
    pending[str(campaign.pk)] = source
    request.session[SESSION_SOURCE_KEY] = pending
    record_event(
        "cross_sell.click",
        request=request,
        user=getattr(request, "user", None),
        properties={"campaign": campaign.slug, "from_campaign": from_slug, "source": source},
    )


def pop_link_source(request, campaign) -> str:
    pending = request.session.get(SESSION_SOURCE_KEY, {})
    source = pending.pop(str(campaign.pk), "")
    if source:
        request.session[SESSION_SOURCE_KEY] = pending
    return source


def register_suggested(source_registration, campaigns, request=None):
    """Create registrations for ``campaigns`` copied from the source.

    No confirmation email is sent: a copy of a confirmed registration is
    confirmed at once (the address is already proven), and an unconfirmed
    one is confirmed by the source's own link (``cascade_confirmation``).
    Returns the created registrations; duplicates are skipped.
    """

    created = []
    now = timezone.now()
    for campaign in campaigns:
        values = {field: getattr(source_registration, field) for field in COPIED_FIELDS}
        registration = CourseRegistration(
            campaign=campaign,
            course=campaign.current_course,
            user=source_registration.user,
            source=SOURCE_ONE_CLICK,
            referred_by=source_registration,
            status=CourseRegistration.Status.APPROVED,
            email_confirmed=source_registration.email_confirmed,
            email_confirmation_confirmed_at=now if source_registration.email_confirmed else None,
            **values,
        )
        try:
            with transaction.atomic():
                registration.save()
        except IntegrityError:
            continue
        transaction.on_commit(partial(queue_registration_contacts, registration.pk))
        created.append(registration)
        record_event(
            "cross_sell.registered",
            request=request,
            user=getattr(request, "user", None) if request is not None else None,
            properties={
                "campaign": campaign.slug,
                "from_campaign": source_registration.campaign.slug,
                "registration_id": registration.pk,
                "referred_by_id": source_registration.pk,
            },
        )
    return created


def cascade_confirmation(registration, now=None) -> int:
    """Confirm the one-click registrations a just-confirmed source made."""

    now = now or timezone.now()
    return CourseRegistration.objects.filter(
        referred_by=registration,
        email_normalized=registration.email_normalized,
        email_confirmed=False,
    ).update(email_confirmed=True, email_confirmation_confirmed_at=now)


def email_suggestions_markdown(registration) -> str:
    """The confirmation email's "You might also like" block (may be empty)."""

    from course_management.public_urls import public_url

    items = suggestions(
        registration.campaign,
        user=registration.user,
        email=registration.email_normalized,
    )
    if not items:
        return ""
    lines = ["**You might also like:**", ""]
    for item in items:
        url = public_url(suggestion_url(item.campaign, registration.campaign, src="xsell-email"))
        when = ""
        if item.start_date:
            when = f" (starts {item.start_date:%B} {item.start_date.day}, {item.start_date.year})"
        lines.append(f"- [{item.campaign.title}]({url}){when}")
    return "\n".join(lines)


def follow_email_fix(registration) -> None:
    """Move unconfirmed one-click registrations to a corrected address.

    A copy that would collide with an existing registration for the new
    address is dropped: that address is already registered there.
    """

    children = CourseRegistration.objects.filter(
        referred_by=registration,
        email_confirmed=False,
    ).exclude(email_normalized=registration.email_normalized)
    for child in children:
        child.email = registration.email
        try:
            with transaction.atomic():
                child.save(update_fields=["email", "email_normalized"])
        except IntegrityError:
            child.delete()
