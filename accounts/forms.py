from django import forms

from accounts.models import CustomUser
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
from courses.registration import ordered_countries, region_for_country


class DevelopmentOwnerLoginForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "username",
                "autocapitalize": "none",
                "class": "form-control",
            }
        ),
    )
    password = forms.CharField(
        max_length=4096,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "form-control",
            }
        ),
    )


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


class AccountSettingsForm(forms.ModelForm):
    preferred_timezone = forms.ChoiceField(
        required=False,
        choices=[],
        widget=PREFERRED_TIMEZONE_WIDGET,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        timezone_choices = [("", "UTC until your browser timezone is detected")]
        for option in build_timezone_options():
            timezone_choice = (option.value, option.label)
            timezone_choices.append(timezone_choice)
        self.fields["preferred_timezone"].choices = timezone_choices

    def clean_preferred_timezone(self):
        timezone_name = self.cleaned_data.get("preferred_timezone", "")
        if timezone_name and not is_valid_timezone(timezone_name):
            raise forms.ValidationError("Choose a valid timezone.")
        return timezone_name

    class Meta:
        model = CustomUser
        fields = [
            "certificate_name",
            "country",
            "registration_role",
            "github_url",
            "linkedin_url",
            "personal_website_url",
            "about_me",
            "preferred_timezone",
            "dark_mode",
        ]
        labels = {
            "certificate_name": "Certificate name",
            "country": "Country",
            "registration_role": "Role",
            "github_url": "GitHub URL",
            "linkedin_url": "LinkedIn URL",
            "personal_website_url": "Website URL",
            "about_me": "About me",
            "preferred_timezone": "Timezone",
            "dark_mode": "Use dark mode",
        }
        help_texts = {
            "certificate_name": ("Used for certificates across your course enrollments."),
            "country": "Used to prefill course registration forms.",
            "registration_role": "Used to prefill course registration forms.",
            "preferred_timezone": (
                "Used to render deadlines and notification emails. We detect "
                "your browser timezone automatically, and you can override it."
            ),
        }
        widgets = {
            "certificate_name": CERTIFICATE_NAME_WIDGET,
            "country": COUNTRY_WIDGET,
            "registration_role": REGISTRATION_ROLE_WIDGET,
            "github_url": GITHUB_URL_WIDGET,
            "linkedin_url": LINKEDIN_URL_WIDGET,
            "personal_website_url": PERSONAL_WEBSITE_URL_WIDGET,
            "about_me": ABOUT_ME_WIDGET,
            "dark_mode": DARK_MODE_WIDGET,
        }


class AboutYouForm(forms.ModelForm):
    """The slim ``/accounts/welcome/`` onboarding form (signed-in-home spec §7.3).

    Owns the person-level fields, not the settings page: three core fields
    (certificate name, country, role) plus a folded-by-default set of links
    and a bio.  Every field saves if present; nothing here is required, so the
    page is safely skippable and trivially resumable.
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
        model = CustomUser
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
            "certificate_name": (
                "Used on your certificates, across all your course enrollments."
            ),
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
