from functools import partial
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from course_management.datamailer.sync.memberships import (
    sync_registration_to_datamailer,
)
from course_management.datamailer.sync.notifications import (
    send_registration_confirmation_email,
)
from course_management.observability import record_event
from courses.models.cohort import CourseRegistration, RegistrationCampaign
from courses.models.homework import Homework
from courses.models.project import Project
from courses.registration import (
    ordered_countries,
    render_markdown,
    youtube_embed_url,
)
from courses.services.registration_counts import public_course_registration_count
from courses.views.url_utils import cohort_url_kwargs

from .registration_form import (
    CourseRegistrationForm,
    authenticated_registration_identity,
    missing_profile_field_names,
)


def registration_is_gated(request: HttpRequest) -> bool:
    """Whether this visitor meets the §8.3 sign-in gate instead of the form.

    Registration is account-owned (`_docs/specs/open-decisions.md` §6), and the
    signed-in-home spec §8.3 makes that visible: an anonymous visitor keeps the
    whole campaign — marketing copy, video, and the already-registered count —
    and is asked to create an account or sign in before the form.  The setting
    is the revert lever the spec asks for.
    """

    if request.user.is_authenticated:
        return False
    return bool(getattr(settings, "REGISTRATION_REQUIRES_ACCOUNT", True))


def campaign_course_is_open(campaign: RegistrationCampaign) -> bool:
    course = campaign.current_course
    if course is None:
        return False

    return (
        Homework.objects.filter(course=course).exists()
        or Project.objects.filter(course=course).exists()
    )


def _active_registration_campaign(
    campaign_slug: str,
) -> RegistrationCampaign:
    return get_object_or_404(
        RegistrationCampaign,
        slug=campaign_slug,
        is_active=True,
    )


def _existing_user_registration(
    request: HttpRequest,
    campaign: RegistrationCampaign,
) -> CourseRegistration | None:
    if not request.user.is_authenticated:
        return None

    email = request.user.email or ""
    email_stripped = email.strip()
    email_normalized = email_stripped.lower()
    return (
        CourseRegistration.objects.filter(campaign=campaign)
        .filter(Q(user=request.user) | Q(email_normalized=email_normalized))
        .first()
    )


def _registration_form(
    request: HttpRequest,
    campaign: RegistrationCampaign,
) -> CourseRegistrationForm:
    if request.method == "POST":
        return CourseRegistrationForm(
            request.POST,
            campaign=campaign,
            user=request.user,
        )

    return CourseRegistrationForm(campaign=campaign, user=request.user)


def _save_registration_if_valid(
    form: CourseRegistrationForm,
) -> CourseRegistration | None:
    if not form.is_valid():
        return None

    registration = form.save()
    sync_callback = partial(
        sync_registration_to_datamailer,
        registration,
    )
    email_callback = partial(
        send_registration_confirmation_email,
        registration,
    )
    transaction.on_commit(sync_callback)
    transaction.on_commit(email_callback)
    return registration


def _start_course_url(campaign: RegistrationCampaign) -> str:
    if not campaign.current_course_id:
        return ""

    return reverse(
        "course",
        kwargs=cohort_url_kwargs(campaign.current_course),
    )


def _registration_context(
    request: HttpRequest,
    campaign: RegistrationCampaign,
    form: CourseRegistrationForm | None,
    registration: CourseRegistration | None,
) -> dict:
    marketing_content = render_markdown(campaign.marketing_markdown)
    marketing_html = mark_safe(marketing_content)
    video_embed_url = youtube_embed_url(campaign.video_url)
    start_course_url = _start_course_url(campaign)
    country_options = ordered_countries()
    course_is_open = campaign_course_is_open(campaign)
    public_count = public_course_registration_count(campaign)
    user = request.user

    return {
        "campaign": campaign,
        "course": campaign.current_course,
        "course_is_open": course_is_open,
        "signup_count": public_count.count if public_count is not None else None,
        "form": form,
        "registration": registration,
        "marketing_html": marketing_html,
        "video_embed_url": video_embed_url,
        "start_course_url": start_course_url,
        "country_options": country_options,
        "registration_gate": registration_is_gated(request),
        "profile_identity": authenticated_registration_identity(user),
        "missing_profile_fields": missing_profile_field_names(user),
    }


def registration_campaign_view(
    request: HttpRequest,
    campaign_slug: str,
) -> HttpResponse:
    campaign = _active_registration_campaign(campaign_slug)

    if registration_is_gated(request):
        # The gate holds both halves of the request, not only the rendered
        # form: an anonymous POST is sent to sign in and comes back to the
        # campaign, so no anonymous ``CourseRegistration`` row is created while
        # the gate is on.
        if request.method == "POST":
            login_url = reverse("login")
            return redirect(f"{login_url}?next={quote(request.path)}")

        context = _registration_context(request, campaign, None, None)
        return render(request, "courses/register.html", context)

    existing_registration = _existing_user_registration(
        request, campaign
    )
    form = _registration_form(request, campaign)

    registration = None
    if request.method == "POST":
        registration = _save_registration_if_valid(form)
        if registration is not None:
            course_slug = ""
            if registration.course_id:
                course_slug = registration.course.slug
            record_event(
                "registration.submitted",
                request=request,
                user=request.user,
                properties={
                    "campaign_slug": campaign.slug,
                    "course_slug": course_slug,
                    "registration_id": registration.id,
                    "accepted_newsletter": registration.accepted_newsletter,
                },
            )

    context = _registration_context(
        request,
        campaign,
        form,
        registration or existing_registration,
    )
    response = render(request, "courses/register.html", context)
    return response
