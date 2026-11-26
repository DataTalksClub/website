from allauth.account import forms as allauth_forms
from django import forms
from django.conf import settings

from accounts.models import User
from accounts.services.timezones import build_timezone_options, is_valid_timezone

# ``country``/``registration_role`` are accounts-owned compatibility
# projections of values the courses domain defines: the country list and the
# role vocabulary both live in ``courses``, and course registration is the
# surface that writes them back.  Onboarding must therefore offer exactly the
# same choices, or it would store a country registration then rejects and a
# role that renders as its raw stored value.  Importing them here follows the
# precedent already set by ``accounts/views/account_settings.py``, which reads
# ``courses.models.Enrollment`` for the same reason.
from courses.models.cohort import CourseRegistration
from courses.models.learner_profile import (
    YEARS_EXPERIENCE_CHOICES,
    LearnerProfile,
    ensure_learner_profile,
    learner_profile_for,
    profile_field_default,
)
from courses.registration import ordered_countries, region_for_country
from courses.services.learner_profile_gate import (
    REQUIRED_PROFILE_FIELDS,
    mark_profile_confirmed,
    missing_required_fields,
)


def _account_for_email(email: str):
    """The one active account that owns ``email``, by the normalized key.

    allauth's own lookup (``filter_users_by_email``) compares the stored
    ``User.email`` exactly, and imported accounts keep the case they were
    created with, so a lowercase address would miss them.  The durable
    backend's normalized lookup is the site's single answer to "whose address
    is this"; two matches count as none.
    """

    from accounts.backends import DurableAccountBackend

    candidates = DurableAccountBackend()._email_candidates(email)
    return candidates[0] if len(candidates) == 1 else None


class SiteLoginForm(allauth_forms.LoginForm):
    """allauth's email + password login, with the development owner exempt.

    The development owner is provisioned by an operator command with an
    address the operator chose, never through a mailed link, so the
    mandatory-verification stage would only lock that account out of the
    development hosts (where Relay is in dry-run).  The exemption reaches the
    exact development owner only, and only where
    ``DEVELOPMENT_OWNER_LOGIN_ENABLED`` lets staff use a password at all.
    """

    def _login_with_password(self, request, redirect_url, credentials):
        from accounts.views.login import is_exact_development_owner

        if settings.DEVELOPMENT_OWNER_LOGIN_ENABLED and is_exact_development_owner(
            self._login.user
        ):
            self._login.email_verification = "none"
        return super()._login_with_password(request, redirect_url, credentials)


PASSWORD_HELP = "At least 10 characters. Not a common or all-numeric password."


class _QuietPasswordFieldsMixin:
    """One short password hint instead of the validators' four-line list.

    The labels name the fields, so allauth's placeholders that repeat them are
    dropped.  The validators themselves are unchanged and still explain a
    refusal in the field's error.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.pop("placeholder", None)
            if name in {"password1", "password"} and "password2" in self.fields:
                field.help_text = PASSWORD_HELP
            elif name == "password2":
                field.label = "Repeat the password"


class SiteSetPasswordForm(_QuietPasswordFieldsMixin, allauth_forms.SetPasswordForm):
    pass


class SiteChangePasswordForm(_QuietPasswordFieldsMixin, allauth_forms.ChangePasswordForm):
    pass


class SiteResetPasswordKeyForm(_QuietPasswordFieldsMixin, allauth_forms.ResetPasswordKeyForm):
    pass


class SiteSignupForm(_QuietPasswordFieldsMixin, allauth_forms.SignupForm):
    """Signup whose "address already taken" check uses the normalized key."""

    def validate_unique_email(self, value):
        email = super().validate_unique_email(value)
        if not self.account_already_exists and _account_for_email(email) is not None:
            self.account_already_exists = True
        return email


class SiteResetPasswordForm(allauth_forms.ResetPasswordForm):
    """Reset request that finds imported, mixed-case addresses too.

    Accounts without a password (social-only, or created by the one-click
    event registration) get the same link: following it is how such an
    account chooses its first password without signing in first.
    """

    def clean_email(self):
        email = super().clean_email()
        if not self.users:
            account = _account_for_email(email)
            self.users = [account] if account is not None else []
        return email


PREFERRED_TIMEZONE_WIDGET = forms.Select(attrs={"class": "form-control"})
CERTIFICATE_NAME_WIDGET = forms.TextInput(
    attrs={
        "class": "form-control",
        "placeholder": "Your name for certificates",
    }
)
COUNTRY_WIDGET = forms.TextInput(
    attrs={
        "class": "form-control",
        "placeholder": "Your country",
    }
)
REGISTRATION_ROLE_WIDGET = forms.TextInput(
    attrs={
        "class": "form-control",
        "placeholder": "Your role",
    }
)
# The registration form's own country combobox, hook for hook
# (`courses/static/country_combobox.js`): a text input the script upgrades into
# a filtered listbox, and which still accepts a typed country with JavaScript
# off.  Spec §7.3 names this widget for the onboarding page.
COUNTRY_COMBOBOX_WIDGET = forms.TextInput(
    attrs={
        "class": "form-control",
        "autocomplete": "country-name",
        "placeholder": "Start typing your country",
        "data-country-combobox-input": "",
    }
)
REGISTRATION_ROLE_SELECT_WIDGET = forms.Select(attrs={"class": "form-control"})
YEARS_EXPERIENCE_SELECT_WIDGET = forms.Select(attrs={"class": "form-control"})
COMPANY_NAME_WIDGET = forms.TextInput(
    attrs={"class": "form-control", "autocomplete": "organization"}
)
REGISTRATION_ROLE_CHOICES = [("", "Select role"), *CourseRegistration.Role.choices]
GITHUB_URL_WIDGET = forms.TextInput(attrs={"class": "form-control"})
LINKEDIN_URL_WIDGET = forms.TextInput(attrs={"class": "form-control"})
PERSONAL_WEBSITE_URL_WIDGET = forms.TextInput(attrs={"class": "form-control"})
ABOUT_ME_WIDGET = forms.Textarea(
    attrs={
        "class": "form-control",
        "rows": 3,
        "style": "height: 100px;",
    }
)
DARK_MODE_WIDGET = forms.CheckboxInput(attrs={"class": "h-4 w-4"})


# The account-level profile fields moved to ``courses.LearnerProfile``
# (plan D3.1).  The settings form spans both models -- ``preferred_timezone``
# stays on the user row -- so the moved fields are declared form fields that
# load from and write back to the profile row, keeping one form and one
# template for the page.
PROFILE_FORM_FIELDS = (
    "certificate_name",
    "country",
    "registration_role",
    "company_name",
    "years_experience",
    "github_url",
    "linkedin_url",
    "personal_website_url",
    "about_me",
    "dark_mode",
)


class AccountSettingsForm(forms.ModelForm):
    preferred_timezone = forms.ChoiceField(
        required=False,
        choices=[],
        widget=PREFERRED_TIMEZONE_WIDGET,
    )
    certificate_name = forms.CharField(
        required=False,
        max_length=255,
        label="Certificate name",
        help_text="Used for certificates across your course enrollments.",
        widget=CERTIFICATE_NAME_WIDGET,
    )
    country = forms.CharField(
        required=False,
        max_length=100,
        label="Country",
        help_text="Used to prefill course registration forms.",
        widget=COUNTRY_WIDGET,
    )
    registration_role = forms.CharField(
        required=False,
        max_length=40,
        label="Role",
        help_text="Used to prefill course registration forms.",
        widget=REGISTRATION_ROLE_WIDGET,
    )
    company_name = forms.CharField(
        required=False,
        max_length=255,
        label="Company",
        help_text="Optional. Leave empty if you are a student or not employed.",
        widget=COMPANY_NAME_WIDGET,
    )
    years_experience = forms.ChoiceField(
        required=False,
        label="Years of professional experience",
        choices=[("", "Select years of experience"), *YEARS_EXPERIENCE_CHOICES],
        widget=YEARS_EXPERIENCE_SELECT_WIDGET,
    )
    github_url = forms.URLField(
        required=False,
        label="GitHub URL",
        widget=GITHUB_URL_WIDGET,
    )
    linkedin_url = forms.URLField(
        required=False,
        label="LinkedIn URL",
        widget=LINKEDIN_URL_WIDGET,
    )
    personal_website_url = forms.URLField(
        required=False,
        label="Website URL",
        widget=PERSONAL_WEBSITE_URL_WIDGET,
    )
    about_me = forms.CharField(
        required=False,
        label="About me",
        widget=ABOUT_ME_WIDGET,
    )
    dark_mode = forms.BooleanField(
        required=False,
        label="Use dark mode",
        widget=DARK_MODE_WIDGET,
    )
    newsletter = forms.BooleanField(
        required=False,
        label="Course and community emails, including the weekly newsletter",
        widget=DARK_MODE_WIDGET,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.services.newsletter_consent import STATE_OFF, newsletter_state

        self.newsletter_state = newsletter_state(self.instance)
        self.fields["newsletter"].initial = self.newsletter_state != STATE_OFF
        timezone_choices = [("", "UTC until your browser timezone is detected")]
        for option in build_timezone_options():
            timezone_choice = (option.value, option.label)
            timezone_choices.append(timezone_choice)
        self.fields["preferred_timezone"].choices = timezone_choices
        profile = learner_profile_for(self.instance)
        for field_name in PROFILE_FORM_FIELDS:
            value = (
                getattr(profile, field_name)
                if profile is not None
                else profile_field_default(field_name)
            )
            self.fields[field_name].initial = value

    def clean_preferred_timezone(self):
        timezone_name = self.cleaned_data.get("preferred_timezone", "")
        if timezone_name and not is_valid_timezone(timezone_name):
            raise forms.ValidationError("Choose a valid timezone.")
        return timezone_name

    def save(self, commit=True):
        user = super().save(commit=commit)
        profile = ensure_learner_profile(user)
        for field_name in PROFILE_FORM_FIELDS:
            setattr(profile, field_name, self.cleaned_data.get(field_name))
        # Saving a complete profile here confirms it as current, exactly as
        # the onboarding form does; an incomplete one stays unconfirmed so
        # registration still asks for the missing fields.
        if not missing_required_fields(profile):
            mark_profile_confirmed(profile)
        if commit:
            profile.save()
            self._save_newsletter(user)
        return user

    def _save_newsletter(self, user) -> None:
        """Unsubscribe at once; turning emails back on goes through the double opt-in."""

        from accounts.services.newsletter_consent import (
            ACCOUNT_SETTINGS_SOURCE,
            STATE_OFF,
            record_signup_email_choice,
            unsubscribe,
        )

        wanted = bool(self.cleaned_data.get("newsletter"))
        if not wanted and self.newsletter_state != STATE_OFF:
            unsubscribe(user)
        elif wanted and self.newsletter_state == STATE_OFF:
            record_signup_email_choice(user, subscribe=True, source=ACCOUNT_SETTINGS_SOURCE)

    class Meta:
        model = User
        fields = ["preferred_timezone"]
        labels = {
            "preferred_timezone": "Timezone",
        }
        help_texts = {
            "preferred_timezone": (
                "Used to render deadlines and notification emails. We detect "
                "your browser timezone automatically, and you can override it."
            ),
        }


class AboutYouForm(forms.ModelForm):
    """The slim ``/accounts/welcome/`` onboarding form (signed-in-home spec §7.3).

    Owns the person-level fields, not the settings page: three core fields
    (certificate name, country, role) plus a folded-by-default set of links
    and a bio.  Every field saves if present; nothing here is required, so the
    page is safely skippable and trivially resumable.  The fields live on the
    account's ``LearnerProfile`` row (plan D3.1), so the form binds to that
    row's instance.
    """

    # Declared rather than left to ``Meta``, so it carries the registration role
    # vocabulary; a declared field takes its own label, not ``Meta.labels``.
    registration_role = forms.ChoiceField(
        label="Role",
        required=False,
        choices=REGISTRATION_ROLE_CHOICES,
        widget=REGISTRATION_ROLE_SELECT_WIDGET,
    )

    class Meta:
        model = LearnerProfile
        fields = [
            "certificate_name",
            "country",
            "registration_role",
            "company_name",
            "years_experience",
            "github_url",
            "linkedin_url",
            "personal_website_url",
            "about_me",
        ]
        labels = {
            "certificate_name": "Full name",
            "country": "Country",
            "registration_role": "Role",
            "company_name": "Company",
            "years_experience": "Years of professional experience",
            "github_url": "GitHub URL",
            "linkedin_url": "LinkedIn URL",
            "personal_website_url": "Website URL",
            "about_me": "About me",
        }
        help_texts = {
            "certificate_name": ("Used on your certificates, across all your course enrollments."),
            "country": "Used for course registration and regional statistics.",
            "company_name": "Optional. Leave empty if you are a student or not employed.",
        }
        widgets = {
            "certificate_name": CERTIFICATE_NAME_WIDGET,
            "country": COUNTRY_COMBOBOX_WIDGET,
            "company_name": COMPANY_NAME_WIDGET,
            "years_experience": YEARS_EXPERIENCE_SELECT_WIDGET,
            "github_url": GITHUB_URL_WIDGET,
            "linkedin_url": LINKEDIN_URL_WIDGET,
            "personal_website_url": PERSONAL_WEBSITE_URL_WIDGET,
            "about_me": ABOUT_ME_WIDGET,
        }

    newsletter = forms.BooleanField(
        required=False,
        initial=True,
        label=(
            "Send me emails about my course and the DataTalks.Club community, "
            "including the weekly newsletter (you can unsubscribe anytime)"
        ),
    )

    def __init__(self, *args, ask_newsletter=False, **kwargs):
        super().__init__(*args, **kwargs)
        # The signup-onboarding email consent: asked only on that step
        # (accounts/services/newsletter_consent.py).
        if not ask_newsletter:
            self.fields.pop("newsletter")
        # The learner-profile gate's required set: registration cannot go
        # ahead without these, so the form that completes the profile asks
        # for them.  Everything else saves if present.
        for field_name in self.fields:
            self.fields[field_name].required = field_name in REQUIRED_PROFILE_FIELDS
        if "newsletter" in self.fields:
            self.fields["newsletter"].initial = True
        self.fields["years_experience"].choices = [
            ("", "Select years of experience"),
            *YEARS_EXPERIENCE_CHOICES,
        ]
        # A role stored before a choice was renamed (or by an import) must not
        # silently disappear from the select the person is looking at.
        stored_role = self.initial.get("registration_role") or ""
        known_roles = {value for value, _label in REGISTRATION_ROLE_CHOICES}
        if stored_role and stored_role not in known_roles:
            self.fields["registration_role"].choices = [
                *REGISTRATION_ROLE_CHOICES,
                (stored_role, stored_role),
            ]

    def clean_country(self):
        # The same rule course registration applies, so onboarding cannot store
        # a country that registration would then reject.
        country = self.cleaned_data.get("country") or ""
        if not country:
            return ""
        if not region_for_country(country):
            raise forms.ValidationError("Select a valid country.")
        return country

    def save(self, commit=True):
        # Region is derived, never asked (spec §7.5), exactly as the
        # registration write-back derives it — so a profile completed through
        # onboarding and a profile completed through registration hold the same
        # record rather than one with a country and no region.
        self.instance.region = region_for_country(self.instance.country or "")
        # Saving the whole profile is a confirmation that it is current.
        mark_profile_confirmed(self.instance)
        return super().save(commit=commit)


def about_you_country_options():
    """The combobox's option list, in the order the registration form uses."""

    return ordered_countries()
