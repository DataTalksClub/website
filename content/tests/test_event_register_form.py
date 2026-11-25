"""The event detail registration form draws the site's shared form primitives.

The page is rendered directly from its template with an in-memory event record,
so every `?registered=` outcome the POST handler redirects to can be asserted
without depending on which events the reference data currently counts as
upcoming.
"""

from __future__ import annotations

import re

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

PUBLIC_PATH = "/events/4242/form-check"


def _event(**overrides: object) -> dict[str, object]:
    return {
        "title": "Form check",
        "type": "webinar",
        "tags": [],
        "starts_at": "2030-01-01T17:00:00+01:00",
        "display_date": "Jan 1, 2030",
        "display_clock": "17:00 CET",
        "registration_count": 0,
        "links": [],
        "banner_url": "",
        "description_html": "",
        "speakers": [],
        "public_path": PUBLIC_PATH,
        "collect_company": False,
        "sponsor_sharing_enabled": False,
        "sponsor_name": "",
        **overrides,
    }


class EventRegisterFormTests(TestCase):
    def render(self, *, state: str = "upcoming", query: str = "", **event: object) -> str:
        request = RequestFactory().get(f"{PUBLIC_PATH}{query}")
        request.user = AnonymousUser()
        return render_to_string(
            "public/event_detail.html",
            {
                "event": _event(**event),
                "event_state": state,
                "event_hero_class": "event-hero-grid event-hero-grid-plain",
                "canonical_url": f"https://datatalks.club{PUBLIC_PATH}",
                "seo_title": "Form check",
                "seo_description": "Webinar.",
                "DARK_MODE": False,
            },
            request=request,
        )

    def form(self, body: str) -> str:
        match = re.search(r'<form\s[^>]*id="event-register-email-form".*?</form>', body, re.S)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(0)

    def test_empty_form_uses_shared_field_primitives_and_keeps_its_contract(self) -> None:
        form = self.form(self.render())

        self.assertIn(f'action="{PUBLIC_PATH}/register"', form)
        self.assertIn('class="cmp-form event-register-form event-register-email"', form)
        self.assertIn("data-register-gate", form)
        self.assertIn('name="csrfmiddlewaretoken"', form)
        self.assertEqual(form.count('class="field"'), 2)
        self.assertIn('<label class="field-label" for="id_reg_email">Email</label>', form)
        self.assertIn('<label class="field-label" for="id_reg_name">Name (optional)</label>', form)
        self.assertEqual(form.count('class="field-input"'), 2)
        self.assertRegex(form, r'id="id_reg_email"\s+type="email"\s+name="email"\s+required')
        self.assertIn('id="id_reg_name" type="text" name="display_name"', form)
        self.assertIn('class="form-check-input"', form)
        self.assertIn('name="newsletter_consent"', form)
        self.assertIn('<label class="form-check-label" for="id_reg_newsletter_consent">', form)
        self.assertIn('class="cmp-form-actions"', form)
        self.assertIn(
            'class="cta cta-primary cta-compact interactive-lift">Register</button>', form
        )
        self.assertNotIn('name="company"', form)
        self.assertNotIn('name="sponsor_sharing_consent"', form)
        self.assertNotIn("aria-invalid", form)

    def test_optional_company_and_sponsor_fields_use_the_same_primitives(self) -> None:
        form = self.form(
            self.render(collect_company=True, sponsor_sharing_enabled=True, sponsor_name="Acme")
        )

        self.assertIn('<label class="field-label" for="id_reg_company">Company</label>', form)
        self.assertIn('name="company"', form)
        self.assertIn('name="sponsor_sharing_consent"', form)
        self.assertIn('for="id_reg_sponsor_sharing_consent"', form)
        self.assertIn("(Acme)", form)

    def test_invalid_email_draws_the_error_summary_and_field_error(self) -> None:
        body = self.render(query="?registered=invalid_email")
        form = self.form(body)

        self.assertIn('class="a11y-error-summary"', body)
        self.assertIn("data-focus-error-summary", body)
        self.assertIn('<a href="#id_reg_email">', body)
        self.assertIn('class="field-input is-invalid"', form)
        self.assertIn('aria-invalid="true"', form)
        self.assertIn('aria-describedby="id_reg_email-error"', form)
        self.assertIn('aria-errormessage="id_reg_email-error"', form)
        self.assertIn('<p id="id_reg_email-error" class="a11y-error">', form)

    def test_registered_outcome_is_a_focused_alert_that_replaces_the_form(self) -> None:
        body = self.render(query="?registered=ok")

        self.assertRegex(
            body,
            r'class="callout callout-info event-register-notice"\s+role="alert"\s+'
            r'tabindex="-1"\s+data-focus-error-summary\s*>\s*<p>\s*'
            r"<strong>You're registered for this event.</strong>",
        )
        self.assertNotIn('class="a11y-error-summary"', body)
        self.assertNotIn('id="event-register-email-form"', body)

    def test_closed_outcome_is_reported_without_a_form(self) -> None:
        for state in ("past", ""):
            with self.subTest(state=state):
                body = self.render(state=state, query="?registered=closed")

                self.assertRegex(
                    body,
                    r'class="callout callout-attention event-register-notice"\s+'
                    r'role="alert"\s+tabindex="-1"\s+data-focus-error-summary',
                )
                self.assertIn("Registration for this event is closed.", body)
                self.assertNotIn('id="event-register-email-form"', body)

    def test_past_event_without_an_outcome_draws_no_registration_band(self) -> None:
        body = self.render(state="past")

        self.assertNotIn('class="band band-lavender event-register-band"', body)
        self.assertNotIn('id="event-register-email-form"', body)
