"""Private applicant pages and explicit staff moderation, without outgoing mail."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import services
from .forms import VOUCH_WARNING, ApplicationForm, VouchForm
from .models import Application, Membership
from .rate_limits import business_rate


def private(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        # Keep form POSTs same-origin for browser CSRF checks while withholding
        # opaque request/redemption paths from cross-origin destinations.
        response["Referrer-Policy"] = "same-origin"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    return wrapped


def _invite_sponsor(token):
    try:
        pk = signing.loads(token, salt="slack-member-invite", max_age=7 * 86400)
        return Membership.objects.get(pk=pk, banned_at__isnull=True)
    except (signing.BadSignature, Membership.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied("This member invitation is unavailable.") from None


@private
@login_required
@business_rate
@require_http_methods(["GET", "HEAD", "POST"])
def application(request):
    current = Application.objects.filter(user=request.user).first()
    form = ApplicationForm(request.POST or None)
    error = ""
    link = ""
    if request.method == "POST":
        if request.POST.get("action") == "issue":
            try:
                token = services.issue_link(request.user)
                link = reverse("slack-access:redeem", args=[token])
            except ValueError as exc:
                error = str(exc)
        elif request.POST.get("action") == "recheck_course":
            services.refresh_course_approval(request.user)
            return redirect("slack-access:application")
        elif not current and form.is_valid():
            sponsor = _invite_sponsor(request.GET["invite"]) if request.GET.get("invite") else None
            current = services.apply(request.user, sponsor=sponsor, **form.cleaned_data)
            return redirect("slack-access:application")
    return render(
        request,
        "slack_access/application.html",
        {
            "application": current,
            "form": form,
            "error": error,
            "link": link,
            "vouch_url": request.build_absolute_uri(
                reverse("slack-access:vouch", args=[current.vouch_key])
            )
            if current
            else "",
        },
    )


@private
@business_rate
@require_http_methods(["GET", "HEAD", "POST"])
def vouch(request, key):
    current = get_object_or_404(Application, vouch_key=key)
    form = VouchForm(request.POST or None)
    confirmed = False
    if request.method == "POST":
        if not request.user.is_authenticated:
            raise PermissionDenied("Sign in to vouch.")
        if form.is_valid():
            services.vouch(request.user, key)
            confirmed = True
    return render(
        request,
        "slack_access/vouch.html",
        {
            "form": form,
            "warning": VOUCH_WARNING,
            "confirmed": confirmed,
            "pending": current.state == Application.State.PENDING,
            "applicant_id": current.user_id,
            "requested_at": current.created_at,
        },
    )


@private
@login_required
@business_rate
@require_http_methods(["GET", "HEAD", "POST"])
def member_invite(request):
    member = services.active_member(request.user)
    form = VouchForm(request.POST or None)
    invite_url = ""
    if request.method == "POST" and form.is_valid():
        token = signing.dumps(member.pk, salt="slack-member-invite")
        invite_url = (
            request.build_absolute_uri(reverse("slack-access:application")) + "?invite=" + token
        )
    return render(
        request,
        "slack_access/invite.html",
        {
            "form": form,
            "warning": VOUCH_WARNING,
            "invite_url": invite_url,
        },
    )


@private
@login_required
@business_rate
@require_http_methods(["GET", "HEAD", "POST"])
def redeem(request, token):
    if request.method == "POST":
        try:
            return HttpResponseRedirect(services.redeem(request.user, token))
        except ValueError:
            return HttpResponse("Slack invitation is temporarily unavailable.", status=503)
    return render(request, "slack_access/redeem.html")
