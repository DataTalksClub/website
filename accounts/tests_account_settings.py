from pathlib import Path
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount, SocialApp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import SimpleTestCase
from django.urls import resolve, reverse

from accounts.studio_roles import synchronize_studio_roles
from accounts.tests_account_settings_base import AccountSettingsViewTestBase


class AccountSettingsRouteTests(SimpleTestCase):
    def test_account_settings_uses_settings_and_not_the_allauth_connections_route(self):
        self.assertEqual(reverse("account_settings"), "/accounts/settings/")
        self.assertEqual(resolve("/accounts/settings/").url_name, "account_settings")

        # allauth's connections URL is kept — allauth reverses it itself after a
        # provider is connected — but it now leads into the sign-in methods
        # section of settings rather than to a page of its own.
        self.assertEqual(reverse("socialaccount_connections"), "/accounts/3rdparty/")
        self.assertEqual(
            resolve("/accounts/3rdparty/").func.__name__,
            "social_connections_moved",
        )


class AccountSettingsThemeAssetTests(SimpleTestCase):
    def test_settings_toggle_applies_the_shared_theme_state_and_storage_key(self):
        script = (
            Path(settings.BASE_DIR) / "courses" / "static" / "settings_toggles.js"
        ).read_text()

        self.assertIn("window.applyDarkModePreference?.(data.value);", script)
        # The signed-out key is refreshed from the account so signing out does
        # not flip the theme.  It is only ever read back when the body is not
        # authenticated, so it cannot diverge from the account that owns it.
        self.assertIn("localStorage.setItem('darkMode', data.value.toString());", script)

    def test_the_shared_shell_never_reads_the_browser_key_while_signed_in(self):
        shell = (
            Path(settings.BASE_DIR) / "templates" / "core" / "_site_shell_foot.html"
        ).read_text()
        head = (
            Path(settings.BASE_DIR) / "templates" / "core" / "_site_shell_head.html"
        ).read_text()

        # Pre-paint: the stored value is applied only for a signed-out visitor.
        self.assertIn("data-authenticated') !== 'true'", head)
        # The pill, and therefore every localStorage read and write in the shell
        # script, exists only for a signed-out visitor.
        self.assertIn("{% if not user.is_authenticated %}", head)
        self.assertNotIn("data-toggle-url", shell)


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

    def test_the_lede_names_every_section_the_page_actually_owns(self):
        """The page absorbed the theme and sign-in methods; the lede said neither."""

        self.client.force_login(self.user)

        body = self.client.get(reverse("account_settings")).content.decode()

        lede_start = body.index('class="settings-lede"')
        lede = body[lede_start : body.index("</p>", lede_start)]
        for section in (
            "profile details",
            "display preferences",
            "sign-in methods",
            "email subscriptions",
            "course enrollments",
        ):
            with self.subTest(section=section):
                self.assertIn(section, lede)
        self.assertNotIn("certificate name, timezone, theme preference", body)

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

    def test_theme_leads_display_preferences_and_replaces_the_masthead_pill(self):
        """The signed-in theme control lives here, and only here.

        It is grouped with the other display preference (timezone) and comes
        first in that group, because it is what a member arrives looking for now
        that the masthead pill is the signed-out control.
        """

        self.client.force_login(self.user)

        response = self.client.get(reverse("account_settings"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="dark-mode-toggle"', body)

        section = body.index('id="display-preferences-section"')
        theme = body.index('for="id_dark_mode"')
        timezone = body.index('for="id_preferred_timezone"')
        email_subscriptions = body.index('id="email-subscriptions-heading"')
        self.assertLess(section, theme)
        self.assertLess(theme, timezone)
        self.assertLess(timezone, email_subscriptions)
        self.assertIn(
            f'data-toggle-url="{reverse("update_account_toggle")}"',
            body,
        )

    def test_the_settings_page_can_apply_a_theme_without_a_masthead_pill(self):
        """The shared apply hook must not be gated on the pill that page lacks.

        ``settings_toggles.js`` calls ``window.applyDarkModePreference`` so the
        new theme lands on the page the member changed it on.  The shell script
        returns early when there is no ``#dark-mode-toggle`` — as on this page —
        so the assignment has to happen before that guard.
        """

        self.client.force_login(self.user)

        body = self.client.get(reverse("account_settings")).content.decode()

        assignment = body.index("window.applyDarkModePreference = apply;")
        guard = body.index("if (!toggle) {")
        self.assertLess(assignment, guard)

    def test_account_menu_uses_studio_for_authorized_course_operator(self):
        """Studio is the one management entry point the account menu offers.

        The account page joined the design system shell with issue #179, and that
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


class AccountSignInMethodsViewTestCase(AccountSettingsViewTestBase):
    """Sign-in methods live in account settings, not on a page of their own."""

    def setUp(self):
        super().setUp()
        SocialApp.objects.create(
            provider="github",
            name="GitHub",
            client_id="client",
            secret="secret",
        ).sites.add(Site.objects.get_current())

    def link_github(self, user=None, uid="4242"):
        return SocialAccount.objects.create(
            user=user or self.user,
            provider="github",
            uid=uid,
            extra_data={"login": "student"},
        )

    def test_the_old_connections_page_leads_into_the_settings_section(self):
        self.client.force_login(self.user)

        response = self.client.get("/accounts/3rdparty/")

        self.assertRedirects(
            response,
            f"{reverse('account_settings')}#sign-in-methods",
            fetch_redirect_response=False,
        )

    def test_the_old_connections_page_still_requires_a_session(self):
        response = self.client.get("/accounts/3rdparty/")

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_settings_lists_each_linked_provider_with_its_own_disconnect(self):
        self.link_github()
        self.client.force_login(self.user)

        response = self.client.get(reverse("account_settings"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="sign-in-methods"', body)
        self.assertIn("<h2 id=\"sign-in-methods-heading\">Sign-in methods</h2>", body)
        self.assertIn("GitHub", body)
        self.assertIn("student", body)
        self.assertIn(f'action="{reverse("disconnect_social_account")}"', body)
        # Identity is its own section, after the profile form rather than
        # inside it: an HTML form cannot nest, and each disconnect is a POST.
        self.assertLess(body.index("</form>"), body.index('id="sign-in-methods"'))
        self.assertLess(
            body.index('id="sign-in-methods"'),
            body.index('id="email-subscriptions-heading"'),
        )

    def test_settings_explains_the_empty_state_without_offering_a_disconnect(self):
        self.client.force_login(self.user)

        body = self.client.get(reverse("account_settings")).content.decode()

        self.assertIn('id="sign-in-methods"', body)
        self.assertIn("You sign in with your email address.", body)
        self.assertNotIn(f'action="{reverse("disconnect_social_account")}"', body)

    def test_disconnect_removes_the_provider_and_returns_to_the_section(self):
        self.user.set_password("a-usable-password")
        self.user.save(update_fields=["password"])
        connection = self.link_github()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("disconnect_social_account"),
            {"account": connection.pk},
        )

        self.assertRedirects(
            response,
            f"{reverse('account_settings')}#sign-in-methods",
            fetch_redirect_response=False,
        )
        self.assertFalse(SocialAccount.objects.filter(pk=connection.pk).exists())

    def test_disconnect_refuses_to_remove_the_only_way_back_in(self):
        """allauth's own guard decides; this page does not restate the rule."""

        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])
        connection = self.link_github()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("disconnect_social_account"),
            {"account": connection.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(SocialAccount.objects.filter(pk=connection.pk).exists())
        self.assertContains(response, "password")

    def test_disconnect_ignores_a_primary_key_that_is_not_this_account(self):
        other = get_user_model().objects.create_user(
            username="other-member@example.invalid",
            email="other-member@example.invalid",
        )
        connection = self.link_github(user=other, uid="9999")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("disconnect_social_account"),
            {"account": connection.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SocialAccount.objects.filter(pk=connection.pk).exists())

    def test_disconnect_needs_a_session_and_a_post(self):
        disconnect_url = reverse("disconnect_social_account")

        anonymous = self.client.post(disconnect_url, {"account": "1"})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous.url)

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(disconnect_url).status_code, 405)
