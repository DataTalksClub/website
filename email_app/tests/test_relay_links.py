"""The bridge adapter: what it forwards, what it refuses, and what it never says."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, override_settings

from email_app import relay_links
from email_app.relay_links import BridgeOutcome
from email_app.tests.support import FakeRelay, unreachable_relay

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class TokenAndDestinationRulesTests(SimpleTestCase):
    def test_token_shape_matches_relays_url_safe_token(self) -> None:
        self.assertTrue(relay_links.is_well_formed_token(TOKEN))
        for rejected in (
            "",
            "short",
            "../../admin/",
            "token with space",
            "token/with/slash",
            "token?u=x",
            "token\r\nX-Injected: 1",
            "t" * 129,
            None,
            42,
        ):
            with self.subTest(rejected=rejected):
                self.assertFalse(relay_links.is_well_formed_token(rejected))

    def test_click_destination_rule_matches_relays_own_rule(self) -> None:
        self.assertTrue(relay_links.is_safe_click_destination("https://example.com/a?b=c"))
        self.assertTrue(relay_links.is_safe_click_destination("http://example.com"))
        for rejected in (
            "",
            "javascript:alert(1)",
            "data:text/html;base64,AAAA",
            "/relative/path",
            "https://",
            "https://example.com/\r\nX-Injected: 1",
            "https://example.com/" + "a" * 4096,
            None,
        ):
            with self.subTest(rejected=rejected):
                self.assertFalse(relay_links.is_safe_click_destination(rejected))

    def test_fingerprint_is_short_stable_and_not_the_token(self) -> None:
        fingerprint = relay_links.token_fingerprint(TOKEN)
        self.assertEqual(fingerprint, relay_links.token_fingerprint(TOKEN))
        self.assertEqual(len(fingerprint), 12)
        self.assertNotIn(fingerprint, TOKEN)
        self.assertNotEqual(fingerprint, relay_links.token_fingerprint(TOKEN[:-1] + "Z"))
        self.assertEqual(relay_links.token_fingerprint(""), "absent")


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class ForwardedRequestTests(SimpleTestCase):
    def _run(self, relay: FakeRelay, call, *args):  # type: ignore[no-untyped-def]
        with mock.patch.object(relay_links, "_pool", return_value=relay):
            return call(*args)

    def test_open_forwards_relays_exact_pixel_path(self) -> None:
        relay = FakeRelay(status_code=200)
        result = self._run(relay, relay_links.record_open, TOKEN)
        self.assertIs(result.outcome, BridgeOutcome.RECORDED)
        call = relay.calls[0]
        self.assertEqual(call.method, "GET")
        self.assertEqual(call.url, f"{RELAY}/t/o/{TOKEN}.gif")
        self.assertFalse(call.allow_redirects)

    def test_click_forwards_relays_exact_path_and_destination_parameter(self) -> None:
        relay = FakeRelay(status_code=302)
        result = self._run(relay, relay_links.record_click, TOKEN, "https://example.com/post?a=1")
        self.assertIs(result.outcome, BridgeOutcome.RECORDED)
        call = relay.calls[0]
        self.assertEqual(call.url, f"{RELAY}/t/c/{TOKEN}")
        self.assertEqual(call.params, {"u": "https://example.com/post?a=1"})
        # Relay answers a click with a redirect; following it here would leave the
        # website deciding the destination from an upstream header instead of from
        # the value it already validated.
        self.assertFalse(call.allow_redirects)

    def test_unsubscribe_get_and_post_use_relays_exact_path_and_form_field(self) -> None:
        relay = FakeRelay(status_code=200)
        self._run(relay, relay_links.load_unsubscribe, TOKEN)
        self.assertEqual(relay.calls[0].method, "GET")
        self.assertEqual(relay.calls[0].url, f"{RELAY}/unsubscribe/{TOKEN}")

        relay = FakeRelay(status_code=200)
        self._run(relay, relay_links.submit_unsubscribe, TOKEN, "client")
        self.assertEqual(relay.calls[0].method, "POST")
        self.assertEqual(relay.calls[0].url, f"{RELAY}/unsubscribe/{TOKEN}")
        self.assertEqual(relay.calls[0].data, {"scope": "client"})

    def test_each_endpoint_carries_its_own_latency_budget(self) -> None:
        budgets = {}
        for name, call, args in (
            ("open", relay_links.record_open, (TOKEN,)),
            ("click", relay_links.record_click, (TOKEN, "https://example.com/")),
            ("unsubscribe", relay_links.submit_unsubscribe, (TOKEN, "client")),
        ):
            relay = FakeRelay(status_code=200)
            self._run(relay, call, *args)
            budgets[name] = relay.calls[0].timeout
        # The pixel is the highest-volume route and must never park a worker for
        # as long as the low-volume unsubscribe is allowed to.
        self.assertLess(budgets["open"], budgets["unsubscribe"])
        self.assertLess(budgets["click"], budgets["unsubscribe"])

    def test_malformed_input_never_opens_a_socket(self) -> None:
        relay = FakeRelay(status_code=200)
        self.assertIs(
            self._run(relay, relay_links.record_open, "../secret").outcome,
            BridgeOutcome.REJECTED,
        )
        self.assertIs(
            self._run(relay, relay_links.record_click, TOKEN, "javascript:alert(1)").outcome,
            BridgeOutcome.INVALID,
        )
        self.assertIs(
            self._run(relay, relay_links.submit_unsubscribe, TOKEN, "everything").outcome,
            BridgeOutcome.INVALID,
        )
        self.assertFalse(relay.called)

    def test_relay_status_codes_map_to_outcomes(self) -> None:
        cases = {
            200: BridgeOutcome.RECORDED,
            302: BridgeOutcome.RECORDED,
            400: BridgeOutcome.INVALID,
            404: BridgeOutcome.REJECTED,
            500: BridgeOutcome.UNAVAILABLE,
            502: BridgeOutcome.UNAVAILABLE,
            # An authenticating proxy in front of Relay is not a verdict about
            # the recipient's link.
            401: BridgeOutcome.UNAVAILABLE,
            403: BridgeOutcome.UNAVAILABLE,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                relay = FakeRelay(status_code=status)
                self.assertIs(self._run(relay, relay_links.record_open, TOKEN).outcome, expected)

    def test_transport_failure_is_swallowed_rather_than_raised(self) -> None:
        result = self._run(unreachable_relay(), relay_links.record_open, TOKEN)
        self.assertIs(result.outcome, BridgeOutcome.UNAVAILABLE)


class ConfigurationTests(SimpleTestCase):
    @override_settings(RELAY_LINK_BRIDGE_BASE_URL="")
    def test_unset_bridge_is_not_configured(self) -> None:
        self.assertFalse(relay_links.is_configured())
        for result in (
            relay_links.record_open(TOKEN),
            relay_links.record_click(TOKEN, "https://example.com/"),
            relay_links.load_unsubscribe(TOKEN),
            relay_links.submit_unsubscribe(TOKEN, "client"),
        ):
            self.assertIs(result.outcome, BridgeOutcome.NOT_CONFIGURED)

    def test_an_unusable_base_url_is_treated_as_unconfigured(self) -> None:
        for value in ("relay.website.internal", "ftp://relay/", "http:///", "http://r/?x=1"):
            with self.subTest(value=value), override_settings(RELAY_LINK_BRIDGE_BASE_URL=value):
                self.assertFalse(relay_links.is_configured())

    @override_settings(RELAY_LINK_BRIDGE_BASE_URL=f"{RELAY}/")
    def test_trailing_slash_does_not_double_the_separator(self) -> None:
        self.assertEqual(relay_links.bridge_base_url(), RELAY)
