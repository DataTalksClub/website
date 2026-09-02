"""The three public routes Relay embeds in mail.

    /t/o/<token>.gif   open-tracking pixel
    /t/c/<token>?u=…   click tracking, then the destination
    /unsubscribe/<token>  the public preference page

They are anonymous by construction: an email client and a mail reader carry no
session, so none of them may require authentication, a session or -- for the two
tracking routes -- a CSRF token.  Nothing here reads or writes the website's
database on the request path except the deliberate durable fallback on an
unsubscribe POST, and none of them ever emits ``Set-Cookie`` for an anonymous
caller, which is the invariant the anonymous edge-caching work depends on.

Every response carries ``private_response_required`` so the shared response
policy marks it ``private, no-store`` and ``noindex``: each URL is scoped to one
recipient and must never be stored by a shared cache or indexed.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from course_management.observability.events import record_event
from email_app import relay_links
from email_app.relay_links import TRANSPARENT_GIF, BridgeOutcome
from email_app.services import accept_unsubscribe_for_replay

BRIDGE_EVENT = "relay_link_bridge"

_SCOPE_LABELS = {
    "client": "Stop marketing email from DataTalks.Club",
    "audience": "Stop email from this list only",
    "global": "Stop every marketing email we send",
}
_SCOPE_DESCRIPTIONS = {
    "client": "You will stop receiving marketing and newsletter email from DataTalks.Club.",
    "audience": "You will stop receiving email from the list this message came from.",
    "global": "You will stop receiving every marketing email managed for DataTalks.Club.",
}


def _record_degradation(route: str, outcome: BridgeOutcome) -> None:
    """Report that a recipient link did not take the happy path.

    Two rules make this safe and affordable.  The request is deliberately not
    passed: the shared event helper copies ``request.path`` into the event, and
    on these routes the path is the recipient's token.  And only the click and
    unsubscribe routes report, never the open pixel -- a send can put 130,000
    pixels through here in one window, and Relay's own open counters and alarms
    already describe that route's health.
    """

    if outcome is BridgeOutcome.RECORDED:
        return
    record_event(
        BRIDGE_EVENT,
        distinct_id="anonymous",
        properties={"route": route, "outcome": outcome.value},
    )


def _mark_recipient_private(request: HttpRequest) -> None:
    """Declare, before anything renders, that this response belongs to one person.

    The shared response policy turns this into ``private, no-store`` and
    ``noindex``, and the account navigation reads it too: a page whose own URL
    carries a recipient token must never have that URL echoed back as a sign-in
    return target.
    """

    request.private_response_required = True  # type: ignore[attr-defined]


def _seal(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    """Keep a bridge response's path out of Django's request log.

    Django logs the request path for any response of 400 or above.  These paths
    contain a per-recipient token, so the response is marked as already logged;
    the bridge reports outcomes without the token instead.
    """

    _mark_recipient_private(request)
    if response.status_code >= 400:
        response._has_been_logged = True  # type: ignore[attr-defined]
    return response


def _gif(request: HttpRequest, *, status: int) -> HttpResponse:
    response = HttpResponse(TRANSPARENT_GIF, status=status, content_type="image/gif")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Content-Length"] = str(len(TRANSPARENT_GIF))
    return _seal(request, response)


@require_GET
def tracking_open(request: HttpRequest, tracking_token: str) -> HttpResponse:
    """Answer the open pixel.

    This is the highest-volume route in the system: a 130,000-recipient send can
    put most of that traffic here in one window.  So it touches no session and no
    website database row, holds a short upstream budget, and always answers with
    a valid GIF -- a slow or absent Relay costs an analytics event, never a
    broken image in someone's mail client.
    """

    result = relay_links.record_open(tracking_token)
    if result.outcome is BridgeOutcome.NOT_CONFIGURED:
        # No bridge in this deployment: the route is not in service.
        return _gif(request, status=404)
    if result.outcome is BridgeOutcome.REJECTED:
        return _gif(request, status=404)
    # Recorded, or no verdict at all.  Both answer 200: a mail client has no use
    # for the difference, and an outage must not look like a broken image.
    return _gif(request, status=200)


@require_GET
def tracking_click(request: HttpRequest, tracking_token: str) -> HttpResponse:
    """Verify one click with Relay and send the reader on.

    An automatic redirect happens only when Relay verifies the link.  That is
    deliberate: the destination arrives in the query string and is entirely
    caller-controlled, so redirecting without Relay's verdict would turn this
    site into an open redirect for anyone who guesses the shape of the URL.
    When Relay cannot or will not verify, the reader still reaches the
    destination -- through a page that names it, in one click, rather than
    silently.
    """

    _mark_recipient_private(request)
    destination = request.GET.get("u", "")
    safe_destination = destination if relay_links.is_safe_click_destination(destination) else ""

    if not relay_links.is_configured():
        _record_degradation("click", BridgeOutcome.NOT_CONFIGURED)
        return _seal(request, _click_notice(request, status=404, destination=""))
    if not safe_destination:
        # Nothing to offer and nothing to verify: the request itself is malformed.
        _record_degradation("click", BridgeOutcome.INVALID)
        return _seal(request, _click_notice(request, status=400, destination=""))

    result = relay_links.record_click(tracking_token, safe_destination)
    _record_degradation("click", result.outcome)
    if result.outcome is BridgeOutcome.RECORDED:
        response = HttpResponseRedirect(safe_destination)
        response["Cache-Control"] = "private, no-store, max-age=0"
        return _seal(request, response)
    if result.answered:
        # Relay looked and said no. The link cannot be attributed to a send, so
        # it is not followed automatically. 400 is the status Relay gives an
        # invalid tracking redirect; only the body differs, because the reader
        # here is a person and not a program.
        return _seal(request, _click_notice(request, status=400, destination=safe_destination))
    # Relay never answered. The page is the correct answer to this request, so it
    # is a 200: a website 5xx would attribute Relay's outage to the website's own
    # error rate and would make a perfectly rendered page look broken. The
    # degradation is reported as an event instead.
    return _seal(
        request,
        _click_notice(request, status=200, destination=safe_destination, unavailable=True),
    )


def _click_notice(
    request: HttpRequest,
    *,
    status: int,
    destination: str = "",
    unavailable: bool = False,
) -> HttpResponse:
    return render(
        request,
        "email/click_notice.html",
        {"destination": destination, "unavailable": unavailable},
        status=status,
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def public_unsubscribe(request: HttpRequest, unsubscribe_token: str) -> HttpResponse:
    """The public preference page for one recipient link.

    Low volume, high stakes.  The GET never mutates anything; the POST applies
    the choice in Relay, and if Relay cannot be reached the request is persisted
    and replayed by a durable job rather than refused.  Nobody who asks to stop
    receiving mail is told no because a backend was down.

    The view is CSRF-exempt because the form is submitted by a recipient
    arriving from a mail client with no prior session, and the token in the path
    is the only thing that authorises the change -- exactly as it is in Relay.
    Being exempt also keeps the page from minting a CSRF cookie for an anonymous
    visitor.
    """

    _mark_recipient_private(request)
    if not relay_links.is_configured():
        _record_degradation("unsubscribe", BridgeOutcome.NOT_CONFIGURED)
        return _render_unsubscribe(request, state="unknown", status=404)

    if request.method == "POST":
        return _apply_unsubscribe(request, unsubscribe_token)

    result = relay_links.load_unsubscribe(unsubscribe_token)
    _record_degradation("unsubscribe", result.outcome)
    if result.outcome is BridgeOutcome.REJECTED:
        return _render_unsubscribe(request, state="unknown", status=404)
    if result.answered:
        return _render_unsubscribe(request, state="form", status=200)
    # Relay is unreachable.  The form is still offered, because the submission
    # path can keep the promise without Relay being up.
    return _render_unsubscribe(request, state="form", status=200, degraded=True)


def _apply_unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    scope = request.POST.get("scope", "")
    if scope not in relay_links.UNSUBSCRIBE_SCOPES:
        return _render_unsubscribe(request, state="form", status=400, invalid_scope=True)

    result = relay_links.submit_unsubscribe(token, scope)
    _record_degradation("unsubscribe", result.outcome)
    if result.outcome is BridgeOutcome.RECORDED:
        return _render_unsubscribe(request, state="confirmed", status=200, scope=scope)
    if result.outcome is BridgeOutcome.REJECTED:
        return _render_unsubscribe(request, state="unknown", status=404)
    if result.outcome is BridgeOutcome.INVALID:
        return _render_unsubscribe(request, state="form", status=400, invalid_scope=True)

    # No verdict from Relay.  Refusing an opt-out is the one failure this seam
    # must not produce, so the request is made durable and confirmed.
    try:
        accept_unsubscribe_for_replay(token=token, scope=scope)
    except ValueError:
        return _render_unsubscribe(request, state="unknown", status=404)
    return _render_unsubscribe(request, state="accepted", status=202, scope=scope)


def _render_unsubscribe(
    request: HttpRequest,
    *,
    state: str,
    status: int,
    scope: str = "",
    degraded: bool = False,
    invalid_scope: bool = False,
) -> HttpResponse:
    response = render(
        request,
        "email/unsubscribe.html",
        {
            "state": state,
            "degraded": degraded,
            "invalid_scope": invalid_scope,
            "chosen_scope": scope,
            "scope_choices": [
                {
                    "value": value,
                    "label": _SCOPE_LABELS[value],
                    "description": _SCOPE_DESCRIPTIONS[value],
                }
                for value in relay_links.UNSUBSCRIBE_SCOPES
            ],
        },
        status=status,
    )
    return _seal(request, response)
