"""Newsletter signup form on home and /newsletter, wired to Relay dtc-newsletter."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from core.newsletter import MESSAGES

RELAY_SETTINGS = {
    "SITE_KEY": "dtc",
    "JOBS_BACKEND": "sync",
    "MAIL_BACKEND": "memory",
    "RELAY_BASE_URL": "https://relay.example.invalid",
    "RELAY_API_KEY": "",
}


@override_settings(COMMUNITY_BASE=RELAY_SETTINGS)
class NewsletterSignupTests(TestCase):
    def test_home_renders_newsletter_signup_form_and_stays_cacheable(self) -> None:
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('id="newsletter-heading"', body)
        self.assertIn("data-relay-signup", body)
        self.assertIn('action="/newsletter"', body)
        self.assertIn("core/newsletter_signup.js", body)
        self.assertFalse(response.cookies)
        self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))

    def test_newsletter_page_renders_canonical_csp_and_aliases(self) -> None:
        response = self.client.get(reverse("newsletter"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('<link rel="canonical" href="https://datatalks.club/newsletter">', body)
        self.assertIn("data-relay-signup", body)
        self.assertIn("core/newsletter_signup.js", body)
        self.assertIn("form-action 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", response.headers["Content-Security-Policy"])
        self.assertFalse(response.cookies)

        for alias in ("/newsletter/", "/newsletter.html"):
            with self.subTest(alias=alias):
                redirect_response = self.client.get(alias, follow=False)
                self.assertEqual(redirect_response.status_code, 301)
                self.assertEqual(redirect_response.headers["Location"], "/newsletter")

    def test_newsletter_page_is_its_synced_row_with_one_form_pairing(self) -> None:
        from content.models import SyncedDocument

        record = SyncedDocument.objects.get(content_kind="newsletter_page").record
        body = self.client.get(reverse("newsletter")).content.decode()
        for text in (record["title"], record["lead"], record["proof"], record["close_title"]):
            self.assertIn(escape(text), body)
        for part in record["issue_parts"]:
            self.assertIn(escape(part["title"]), body)
        # The hero and the closing panel carry the same form, input and button
        # on one row, and Subscribe is the same button in both.
        self.assertEqual(body.count('class="newsletter-form-row"'), 2)
        self.assertEqual(body.count(">Subscribe</button>"), 2)

    def test_newsletter_page_is_absent_without_its_row(self) -> None:
        from content.models import SyncedDocument

        SyncedDocument.objects.filter(content_kind="newsletter_page").delete()
        self.assertEqual(self.client.get(reverse("newsletter")).status_code, 404)

    def test_main_sitemap_includes_newsletter(self) -> None:
        response = self.client.get("/sitemaps/main.xml")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<loc>https://datatalks.club/newsletter</loc>")

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_json_subscribe_posts_to_relay_public_list_and_maps_outcomes(
        self, mock_request: Mock
    ) -> None:
        cases = (
            ("subscribed", MESSAGES["subscribed"]),
            ("already_subscribed", MESSAGES["already_subscribed"]),
            ("not_resubscribed", MESSAGES["not_resubscribed"]),
        )
        for relay_status, expected_message in cases:
            with self.subTest(relay_status=relay_status):
                mock_request.reset_mock()
                mock_request.return_value = Mock(status_code=200)
                mock_request.return_value.json.return_value = {"status": relay_status}

                response = self.client.post(
                    reverse("newsletter"),
                    data=json.dumps(
                        {
                            "email": "subscriber@example.invalid",
                            "company": "",
                            "page": "https://datatalks.club/",
                        }
                    ),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"ok": True, "status": relay_status, "message": expected_message},
                )
                self.assertIn("no-store", response.headers["Cache-Control"])
                mock_request.assert_called_once_with(
                    "POST",
                    "https://relay.example.invalid/api/public/lists/dtc-newsletter/subscribe",
                    json={
                        "email": "subscriber@example.invalid",
                        "company": "",
                        "page": "https://datatalks.club/",
                    },
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=10,
                )

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_html_form_post_works_without_javascript(self, mock_request: Mock) -> None:
        mock_request.return_value = Mock(status_code=200)
        mock_request.return_value.json.return_value = {"status": "subscribed"}

        response = self.client.post(
            reverse("newsletter"),
            data={"email": "subscriber@example.invalid", "company": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, escape(MESSAGES["subscribed"]))
        self.assertIn("no-store", response.headers["Cache-Control"])
        mock_request.assert_called_once()

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_honeypot_short_circuits_without_calling_relay(self, mock_request: Mock) -> None:
        response = self.client.post(
            reverse("newsletter"),
            data=json.dumps({"email": "bot@example.invalid", "company": "Acme Corp"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "status": "subscribed", "message": MESSAGES["subscribed"]},
        )
        mock_request.assert_not_called()

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_invalid_email_rejected_locally_without_calling_relay(self, mock_request: Mock) -> None:
        for bad_email in ("", "   ", "not-an-email"):
            with self.subTest(bad_email=bad_email):
                response = self.client.post(
                    reverse("newsletter"),
                    data=json.dumps({"email": bad_email, "company": ""}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        "ok": False,
                        "status": "invalid_email",
                        "message": MESSAGES["invalid_email"],
                    },
                )
        mock_request.assert_not_called()

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_relay_errors_and_timeouts_map_cleanly(self, mock_request: Mock) -> None:
        mock_request.return_value = Mock(status_code=429)
        mock_request.return_value.json.return_value = {}
        rate_limited = self.client.post(
            reverse("newsletter"),
            data=json.dumps({"email": "subscriber@example.invalid", "company": ""}),
            content_type="application/json",
        )
        self.assertEqual(rate_limited.status_code, 429)
        self.assertEqual(rate_limited.json()["status"], "rate_limited")

        mock_request.side_effect = requests.Timeout("timed out")
        timed_out = self.client.post(
            reverse("newsletter"),
            data=json.dumps({"email": "subscriber@example.invalid", "company": ""}),
            content_type="application/json",
        )
        self.assertEqual(timed_out.status_code, 504)
        self.assertEqual(timed_out.json()["status"], "timeout")

        mock_request.side_effect = requests.ConnectionError("unreachable")
        failed = self.client.post(
            reverse("newsletter"),
            data=json.dumps({"email": "subscriber@example.invalid", "company": ""}),
            content_type="application/json",
        )
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["status"], "failed")

    @override_settings(COMMUNITY_BASE={**RELAY_SETTINGS, "RELAY_BASE_URL": ""})
    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_missing_relay_base_url_fails_closed(self, mock_request: Mock) -> None:
        response = self.client.post(
            reverse("newsletter"),
            data=json.dumps({"email": "subscriber@example.invalid", "company": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "failed")
        mock_request.assert_not_called()

    def test_unsupported_method_returns_405(self) -> None:
        response = self.client.put(reverse("newsletter"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("no-store", response.headers["Cache-Control"])
