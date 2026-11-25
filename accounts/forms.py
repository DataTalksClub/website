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
    LearnerProfile,
    ensure_learner_profile,
    learner_profile_for,
    profile_field_default,
)
from courses.registration import ordered_countries, region_for_country


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        if commit:
            profile.save()
        return user

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
            "github_url",
            "linkedin_url",
            "personal_website_url",
            "about_me",
        ]
        labels = {
            "certificate_name": "Certificate name",
            "country": "Country",
            "registration_role": "Role",
            "github_url": "GitHub URL",
            "linkedin_url": "LinkedIn URL",
            "personal_website_url": "Website URL",
            "about_me": "About me",
        }
        help_texts = {
            "certificate_name": ("Used on your certificates, across all your course enrollments."),
            "country": "Used to prefill course registration.",
        }
        widgets = {
            "certificate_name": CERTIFICATE_NAME_WIDGET,
            "country": COUNTRY_COMBOBOX_WIDGET,
            "github_url": GITHUB_URL_WIDGET,
            "linkedin_url": LINKEDIN_URL_WIDGET,
            "personal_website_url": PERSONAL_WEBSITE_URL_WIDGET,
            "about_me": ABOUT_ME_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.fields:
            self.fields[field_name].required = False
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
        return super().save(commit=commit)


def about_you_country_options():
    """The combobox's option list, in the order the registration form uses."""

    return ordered_countries()
