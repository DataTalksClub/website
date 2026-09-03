"""Studio wiring for the two registration-campaign lifecycle actions.

A ``RegistrationCampaign`` promotes exactly one cohort at a time
(``current_course``), or none while it waits for the next edition.  These two
POST actions are the only writer of that field for an existing campaign --
see ``courses/services/registration_campaigns.py`` for the guarded service
functions and ``studio_courses/forms.py`` for why the general campaign edit
form no longer accepts a raw ``current_course`` change.
"""

from django.contrib import messages

from courses.models.cohort import RegistrationCampaign
from courses.services.registration_campaigns import (
    RegistrationCampaignStateError,
    open_new_cohort,
    stop_registration,
)
from studio_courses.forms import OpenNewCohortForm, RegistrationCampaignForm

from .campaign_forms import CampaignEditPostResult

_STOP_ACTION = "stop_registration"
_OPEN_ACTION = "open_new_cohort"


def _actor_ref(request):
    return f"user:{request.user.pk}"


def _reloaded(campaign):
    return (
        RegistrationCampaign.objects.select_related("current_course").get(pk=campaign.pk)
    )


def _unchanged_result(campaign, *, open_new_cohort_form=None):
    return CampaignEditPostResult(
        response=None,
        form=RegistrationCampaignForm(instance=campaign),
        datamailer_preview=None,
        campaign=None,
        open_new_cohort_form=open_new_cohort_form,
    )


def _handle_stop_registration(request, campaign):
    try:
        stop_registration(
            campaign,
            actor_ref=_actor_ref(request),
            actor_id=request.user.pk,
        )
    except RegistrationCampaignStateError as error:
        messages.error(request, str(error))
        return _unchanged_result(campaign)

    updated = _reloaded(campaign)
    messages.success(request, "Registration stopped for the current cohort.")
    return CampaignEditPostResult(
        response=None,
        form=RegistrationCampaignForm(instance=updated),
        datamailer_preview=None,
        campaign=updated,
        open_new_cohort_form=None,
    )


def _handle_open_new_cohort(request, campaign):
    form = OpenNewCohortForm(request.POST)
    if not form.is_valid():
        return _unchanged_result(campaign, open_new_cohort_form=form)

    cohort = form.cleaned_data["cohort"]
    try:
        open_new_cohort(
            campaign,
            cohort,
            actor_ref=_actor_ref(request),
            actor_id=request.user.pk,
        )
    except RegistrationCampaignStateError as error:
        messages.error(request, str(error))
        return _unchanged_result(campaign, open_new_cohort_form=form)

    updated = _reloaded(campaign)
    messages.success(request, f"Registration opened for {cohort.title}.")
    return CampaignEditPostResult(
        response=None,
        form=RegistrationCampaignForm(instance=updated),
        datamailer_preview=None,
        campaign=updated,
        open_new_cohort_form=None,
    )


def handle_campaign_lifecycle_post(request, campaign):
    """Dispatch a ``campaign_action`` POST and return an edit-page result.

    Neither branch redirects: the caller re-renders the same edit page,
    which is enough to show the campaign's new (or unchanged, on error)
    registration status.
    """

    action = request.POST.get("campaign_action")
    if action == _STOP_ACTION:
        return _handle_stop_registration(request, campaign)
    if action == _OPEN_ACTION:
        return _handle_open_new_cohort(request, campaign)

    messages.error(request, "Unknown registration status action.")
    return _unchanged_result(campaign)
