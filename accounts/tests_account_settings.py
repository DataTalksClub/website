from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import resolve, reverse

from accounts.studio_roles import synchronize_studio_roles
from accounts.tests_account_settings_base import AccountSettingsViewTestBase


class AccountSettingsRouteTests(SimpleTestCase):
    def test_account_settings_uses_settings_and_not_the_allauth_connections_route(self):
        self.assertEqual(reverse("account_settings"), "/accounts/settings/")
        self.assertEqual(resolve("/accounts/settings/").url_name, "account_settings")

        # allauth intentionally owns this separate URL for managing linked
        # social accounts; it must not become a backwards alias for settings.
        self.assertEqual(
            resolve("/accounts/3rdparty/").url_name,
            "socialaccount_connections",
        )


class AccountSettingsThemeAssetTests(SimpleTestCase):
    def test_settings_toggle_applies_the_shared_theme_state_and_storage_key(self):
        script = (
            Path(settings.BASE_DIR) / "courses" / "static" / "settings_toggles.js"
        ).read_text()

        self.assertIn("window.applyDarkModePreference?.(data.value);", script)
        self.assertIn("localStorage.setItem('darkMode', data.value.toString());", script)


class AccountSettingsAuthViewTestCase(AccountSettingsViewTestBase):
    def test_account_settings_requires_login(self):
        account_settings_url = reverse("account_settings")
        login_url = reverse("login")
        expected_redirect_url = f"{login_url}?next={account_settings_url}"

        response = self.client.get(account_settings_url)

        self.assertEqual(response.status_code, 302)
        is_expected_redirect = response.url.startswith(expected_redirect_url)
        self.assertTrue(is_expected_redirect)


class AccountSettingsOverviewViewTestCase(AccountSettingsViewTestBase):
    def test_account_settings_shows_user_and_enrolled_courses(self):
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")
        courses_studio_url = reverse("studio_courses_course_list")

        response = self.client.get(account_settings_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "student@example.com")
        self.assertContains(response, "Data Course")
        self.assertContains(response, "Student One")
        self.assertNotContains(response, courses_studio_url)
        self.assertNotContains(
            response,
            'class="nav-link user-menu-item" href="/courses">Courses</a>',
        )

    def test_account_settings_uses_lavender_content_without_an_eyebrow(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("account_settings"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            body,
            r'<section\s+class="band band-lavender"\s+id="account-settings-content"',
        )
        self.assertIn('<h1 id="account-settings-heading">Account settings</h1>', body)
        self.assertNotIn('class="mono-label mono-label-indigo">Account</p>', body)

    def test_account_theme_toggle_updates_shared_state_and_persists_on_reload(self):
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")

        light_response = self.client.get(account_settings_url)
        self.assertContains(light_response, 'data-dark-mode="false"')
        self.assertContains(light_response, "window.applyDarkModePreference = apply;")
        self.assertContains(light_response, 'src="/static/settings_toggles.js"')

        toggle_response = self.client.post(
            reverse("update_account_toggle"),
            {"field": "dark_mode", "value": "true"},
        )

        self.assertEqual(toggle_response.status_code, 200)
        self.assertEqual(toggle_response.json()["dark_mode"], True)
        dark_response = self.client.get(account_settings_url)
        self.assertContains(dark_response, 'class="dark dark-mode"')
        self.assertContains(dark_response, 'data-dark-mode="true"')

    def test_account_menu_uses_studio_for_authorized_course_operator(self):
        """Studio is the one management entry point the account menu offers.

        The account page joined the design 5a shell with issue #179, and that
        shell's account menu carries a single ``Studio`` destination instead of
        the adopted shell's deeper ``Studio Courses`` row.  The characterized
        rule is unchanged: an explicitly authorized course operator reaches
        management through Studio, and through nothing else.
        """

        self.user.is_staff = True
        self.user.save()
        groups = {group.name: group for group in synchronize_studio_roles()}
        self.user.groups.add(groups["course_operator"])
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")
        studio_url = reverse("studio:home")
        courses_studio_url = reverse("studio_courses_course_list")

        response = self.client.get(account_settings_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{studio_url}">Studio</a>')
        self.assertNotContains(response, courses_studio_url)

    @patch("accounts.views.email_preferences.get_email_preferences_for_user")
    def test_account_settings_does_not_block_on_datamailer_preferences(
        self,
        get_email_preferences,
    ):
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")

        response = self.client.get(account_settings_url)

        self.assertEqual(response.status_code, 200)
        get_email_preferences.assert_not_called()
        self.assertNotIn(
            "email_submission_confirmations",
            response.context["form"].fields,
        )


class AccountSettingsProfileViewTestCase(AccountSettingsViewTestBase):
    def test_account_settings_updates_profile(self):
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")
        payload = self.account_settings_profile_payload()

        response = self.client.post(account_settings_url, payload)

        self.assertRedirects(response, account_settings_url)
        self.assert_profile_update_saved()

    def test_account_settings_profile_save_preserves_dark_mode_toggle(self):
        self.user.dark_mode = True
        self.user.save(update_fields=["dark_mode"])
        self.client.force_login(self.user)
        account_settings_url = reverse("account_settings")
        payload = {
            "certificate_name": "Student Certificate",
            "github_url": "",
            "linkedin_url": "",
            "personal_website_url": "",
            "about_me": "",
        }

        response = self.client.post(account_settings_url, payload)

        self.assertRedirects(response, account_settings_url)
        self.user.refresh_from_db()
        self.assertTrue(self.user.dark_mode)
