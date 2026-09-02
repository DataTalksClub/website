"""The three public routes Relay puts in mail, from the caller's side."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings

from email_app import relay_links
from email_app.models import PendingUnsubscribe
from email_app.relay_links import TRANSPARENT_GIF
from email_app.tests.support import FakeRelay, timing_out_relay, unreachable_relay
from jobs.models import DurableJob

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"
OPEN_PATH = f"/t/o/{TOKEN}.gif"
CLICK_PATH = f"/t/c/{TOKEN}"
UNSUBSCRIBE_PATH = f"/unsubscribe/{TOKEN}"


class BridgeClientMixin:
    def relay(self, relay: FakeRelay):  # type: ignore[no-untyped-def]
        return mock.patch.object(relay_links, "_pool", return_value=relay)


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class OpenPixelTests(BridgeClientMixin, TestCase):
    def test_recorded_open_returns_the_transparent_gif(self) -> None:
        with self.relay(FakeRelay(status_code=200)):
            response = self.client.get(OPEN_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/gif")
        self.assertEqual(response.content, TRANSPARENT_GIF)
        self.assertEqual(response["Content-Length"], str(len(TRANSPARENT_GIF)))

    def test_unknown_token_still_returns_a_valid_gif(self) -> None:
        with self.relay(FakeRelay(status_code=404)):
            response = self.client.get(OPEN_PATH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, TRANSPARENT_GIF)

    def test_unreachable_relay_returns_a_valid_gif_with_a_normal_status(self) -> None:
        # A mail client renders whatever comes back. Losing an analytics event is
        # acceptable; a broken image in 130,000 inboxes is not.
        for relay in (unreachable_relay(), timing_out_relay(), FakeRelay(status_code=503)):
            with self.subTest(relay=relay), self.relay(relay):
                response = self.client.get(OPEN_PATH)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, TRANSPARENT_GIF)

    def test_malformed_token_is_answered_without_calling_relay(self) -> None:
        relay = FakeRelay(status_code=200)
        with self.relay(relay):
            response = self.client.get("/t/o/short.gif")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, TRANSPARENT_GIF)
        self.assertFalse(relay.called)

    def test_the_pixel_touches_no_website_database_row(self) -> None:
        with self.relay(FakeRelay(status_code=200)), self.assertNumQueries(0):
            self.client.get(OPEN_PATH)

    def test_the_pixel_sets_no_cookie_and_is_never_stored(self) -> None:
        with self.relay(FakeRelay(status_code=200)):
            response = self.client.get(OPEN_PATH)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_the_pixel_needs_no_authentication_or_csrf(self) -> None:
        client = self.client_class(enforce_csrf_checks=True)
        with self.relay(FakeRelay(status_code=200)):
            self.assertEqual(client.get(OPEN_PATH).status_code, 200)

    @override_settings(RELAY_LINK_BRIDGE_BASE_URL="")
    def test_an_unconfigured_deployment_does_not_serve_the_route(self) -> None:
        response = self.client.get(OPEN_PATH)
        self.assertEqual(response.status_code, 404)


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class ClickTrackingTests(BridgeClientMixin, TestCase):
    def test_a_verified_click_redirects_to_the_destination(self) -> None:
        with self.relay(FakeRelay(status_code=302)):
            response = self.client.get(CLICK_PATH, {"u": "https://example.com/post"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/post")
        self.assertNotIn("Set-Cookie", response.headers)

    def test_a_click_relay_rejects_is_not_followed_automatically(self) -> None:
        # 400 is the status Relay gives an invalid tracking redirect; only the
        # body differs, because the reader here is a person, not a program.
        with self.relay(FakeRelay(status_code=400)):
            response = self.client.get(CLICK_PATH, {"u": "https://example.com/post"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response.headers)
        self.assertContains(response, "https://example.com/post", status_code=400)

    def test_an_unverifiable_click_shows_the_destination_instead_of_redirecting(self) -> None:
        # An automatic redirect without Relay's verdict would make this site an
        # open redirect for anyone who guesses the URL shape.  The page is the
        # correct answer to the request, so it is a 200: a website 5xx would
        # charge Relay's outage to the website's own error rate.
        with self.relay(unreachable_relay()):
            response = self.client.get(CLICK_PATH, {"u": "https://example.com/post"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertContains(response, "https://example.com/post")
        self.assertContains(response, "could not check this link")

    def test_an_unsafe_destination_is_never_offered(self) -> None:
        relay = FakeRelay(status_code=302)
        for destination in ("javascript:alert(1)", "/local", ""):
            with self.subTest(destination=destination), self.relay(relay):
                response = self.client.get(CLICK_PATH, {"u": destination})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Location", response.headers)
            self.assertNotContains(response, "javascript:", status_code=400)
        self.assertFalse(relay.called)

    @override_settings(RELAY_LINK_BRIDGE_BASE_URL="")
    def test_an_unconfigured_deployment_is_not_an_open_redirect(self) -> None:
        response = self.client.get(CLICK_PATH, {"u": "https://example.com/post"})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class PublicUnsubscribeTests(BridgeClientMixin, TestCase):
    def test_the_initial_get_offers_the_choice_and_mutates_nothing(self) -> None:
        relay = FakeRelay(status_code=200)
        with self.relay(relay):
            response = self.client.get(UNSUBSCRIBE_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose which email to stop")
        self.assertContains(response, 'name="scope"')
        self.assertEqual([call.method for call in relay.calls], ["GET"])
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_an_unknown_link_says_so_and_changes_nothing(self) -> None:
        with self.relay(FakeRelay(status_code=404)):
            response = self.client.get(UNSUBSCRIBE_PATH)
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "no longer valid", status_code=404)

    def test_the_page_is_the_sites_own_surface(self) -> None:
        with self.relay(FakeRelay(status_code=200)):
            response = self.client.get(UNSUBSCRIBE_PATH)
        body = response.content.decode()
        self.assertIn("DataTalks.Club", body)
        # Relay's own page belongs to another product and is never proxied through.
        self.assertNotIn("Datamailer", body)
        self.assertIn('<meta name="robots" content="noindex, nofollow">', body)

    def test_confirming_applies_the_choice_in_relay(self) -> None:
        relay = FakeRelay(status_code=200)
        with self.relay(relay):
            response = self.client.post(UNSUBSCRIBE_PATH, {"scope": "global"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You have been unsubscribed")
        self.assertEqual(relay.calls[-1].data, {"scope": "global"})
        self.assertFalse(PendingUnsubscribe.objects.exists())

    def test_an_unsupported_scope_is_refused_without_reaching_relay(self) -> None:
        relay = FakeRelay(status_code=200)
        with self.relay(relay):
            response = self.client.post(UNSUBSCRIBE_PATH, {"scope": "everything"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose one option", status_code=400)
        self.assertFalse(relay.called)

    def test_an_opt_out_is_never_refused_because_relay_is_down(self) -> None:
        with self.relay(unreachable_relay()):
            response = self.client.post(UNSUBSCRIBE_PATH, {"scope": "client"})
        self.assertEqual(response.status_code, 202)
        self.assertContains(response, "has been recorded", status_code=202)

        pending = PendingUnsubscribe.objects.get()
        self.assertEqual(pending.scope, "client")
        self.assertEqual(pending.status, PendingUnsubscribe.Status.PENDING)
        self.assertEqual(pending.token_fingerprint, relay_links.token_fingerprint(TOKEN))
        self.assertTrue(
            DurableJob.objects.filter(handler="email.unsubscribe-replay").exists(),
            "the accepted opt-out must be handed to a durable job",
        )

    def test_a_degraded_get_still_offers_the_form(self) -> None:
        with self.relay(unreachable_relay()):
            response = self.client.get(UNSUBSCRIBE_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="scope"')
        self.assertContains(response, "could not check this link")

    def test_the_page_needs_no_session_cookie_or_csrf_token(self) -> None:
        client = self.client_class(enforce_csrf_checks=True)
        with self.relay(FakeRelay(status_code=200)):
            get_response = client.get(UNSUBSCRIBE_PATH)
            post_response = client.post(UNSUBSCRIBE_PATH, {"scope": "client"})
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 200)
        self.assertNotIn("Set-Cookie", get_response.headers)
        self.assertNotIn("csrfmiddlewaretoken", get_response.content.decode())

    def test_the_page_is_private_and_unindexed(self) -> None:
        with self.relay(FakeRelay(status_code=200)):
            response = self.client.get(UNSUBSCRIBE_PATH)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    @override_settings(RELAY_LINK_BRIDGE_BASE_URL="")
    def test_an_unconfigured_deployment_does_not_serve_the_route(self) -> None:
        self.assertEqual(self.client.get(UNSUBSCRIBE_PATH).status_code, 404)


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class ForwardingFidelityTests(BridgeClientMixin, TestCase):
    """What Relay receives must be exactly what Relay generated."""

    def test_a_url_safe_token_is_forwarded_without_re_encoding(self) -> None:
        # Relay mints tokens with `secrets.token_urlsafe`, so `-` and `_` are
        # ordinary characters in the path. Percent-encoding either of them would
        # make Relay look up a token it never issued.
        token = "aA0-_" + "b" * 38
        relay = FakeRelay(status_code=200)
        with self.relay(relay):
            self.client.get(f"/t/o/{token}.gif")
        self.assertEqual(relay.calls[0].url, f"{RELAY}/t/o/{token}.gif")
        self.assertNotIn("%", relay.calls[0].url)

    def test_the_destination_survives_the_decode_and_re_encode_round_trip(self) -> None:
        from urllib.parse import urlencode

        from django.utils.encoding import iri_to_uri

        destinations = (
            "https://example.com/post",
            "https://example.com/a b",
            "https://example.com/a+b",
            "https://example.com/search?q=data&sort=new",
            "https://example.com/path#anchor",
            "https://example.com/%C3%BCber",
            "https://example.com/über",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                # Exactly how Relay writes the link into the message body.
                query = urlencode({"u": destination})
                relay = FakeRelay(status_code=302)
                with self.relay(relay):
                    response = self.client.get(f"{CLICK_PATH}?{query}")
                self.assertEqual(response.status_code, 302)
                # Relay is asked to record precisely the destination it generated:
                # the query value is decoded once by Django and encoded once by
                # the bridge, so the value Relay sees is unchanged.
                self.assertEqual(relay.calls[0].params, {"u": destination})
                # `Location` must be a URI, so a non-ASCII or spaced destination
                # is percent-encoded on the way out. That is the same transform
                # Relay's own redirect applies, so both point at one resource.
                self.assertEqual(response["Location"], iri_to_uri(destination))

    def test_a_duplicated_destination_parameter_cannot_split_record_from_redirect(self) -> None:
        relay = FakeRelay(status_code=302)
        with self.relay(relay):
            response = self.client.get(
                f"{CLICK_PATH}?u=https://example.com/one&u=https://example.com/two"
            )
        forwarded = relay.calls[0].params
        assert forwarded is not None
        self.assertEqual(response["Location"], forwarded["u"])
