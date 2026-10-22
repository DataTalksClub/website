from django import forms

from courses.models.cohort import CourseRegistration
from courses.registration import region_for_country
from courses.views.registration_profile import (
    update_user_profile_from_registration,
)


ACCEPTED_NEWSLETTER_WIDGET = forms.CheckboxInput(
    attrs={"class": "h-4 w-4"}
)
EMAIL_WIDGET = forms.EmailInput(attrs={"class": "form-control"})
NAME_WIDGET = forms.TextInput(attrs={"class": "form-control"})
COMPANY_NAME_WIDGET = forms.TextInput(
    attrs={"class": "form-control", "autocomplete": "organization"}
)
COUNTRY_SELECT_WIDGET = forms.Select(attrs={"class": "form-control"})
ROLE_WIDGET = forms.Select(attrs={"class": "form-control"})
YEARS_EXPERIENCE_WIDGET = forms.Select(attrs={"class": "form-control"})
EXPERIENCE_WIDGET = forms.Textarea(
    attrs={
        "class": "form-control",
        "rows": 4,
        "placeholder": "Years of experience, relevant projects, tools you use",
    }
)
MOTIVATION_WIDGET = forms.Textarea(
    attrs={
        "class": "form-control",
        "rows": 4,
        "placeholder": "What do you want to build or learn in this course?",
    }
)
PROFILE_URL_WIDGET = forms.URLInput(attrs={"class": "form-control"})
PREREQ_WIDGET = forms.CheckboxInput(attrs={"class": "h-4 w-4"})
COMMENT_WIDGET = forms.Textarea(
    attrs={
        "class": "form-control",
        "rows": 4,
        "placeholder": "Anything you would like to add?",
    }
)
COUNTRY_COMBOBOX_WIDGET_ATTRS = {
    "class": "form-control",
    "autocomplete": "country-name",
    "placeholder": "Start typing your country",
    "data-country-combobox-input": "",
}
OPTIONAL_REGISTRATION_FIELDS = (
    "name",
    "company_name",
    "country",
    "role",
    "comment",
)
#: Application-style campaigns (``RegistrationCampaign.requires_approval``)
#: ask for a name, a role and experience the way Mikhail's course does:
#: limited seats, medium-advanced level, approve targeted applicants after
#: registration. Open campaigns keep the historically optional shape.
#: The Nebius sharing consent is required only there; other campaigns do
#: not see that checkbox at all.
APPLICATION_REQUIRED_FIELDS = (
    "name",
    "role",
    "experience",
    "accepted_nebius_sharing",
)


def campaign_requires_approval(campaign) -> bool:
    return bool(campaign is not None and getattr(campaign, "requires_approval", False))


def configure_registration_form_fields(form, campaign=None):
    for field_name in OPTIONAL_REGISTRATION_FIELDS:
        form.fields[field_name].required = False

    if campaign_requires_approval(campaign):
        for field_name in APPLICATION_REQUIRED_FIELDS:
            form.fields[field_name].required = True
    else:
        form.fields.pop("accepted_nebius_sharing", None)

    country_widget = forms.TextInput(attrs=COUNTRY_COMBOBOX_WIDGET_ATTRS)
    form.fields["country"].widget = country_widget

    role_choices = [("", "Select role")]
    for role_choice in CourseRegistration.Role.choices:
        role_choices.append(role_choice)
    form.fields["role"].choices = role_choices

    if "years_experience" in form.fields:
        years_choices = [("", "Select years of experience")]
        for years_choice in CourseRegistration.YearsExperience.choices:
            years_choices.append(years_choice)
        form.fields["years_experience"].choices = years_choices


def has_authenticated_registration_user(user):
    return user is not None and user.is_authenticated


def authenticated_registration_initial_values(user):
    from courses.models.learner_profile import learner_profile_for, profile_field_default

    profile = learner_profile_for(user)
    if profile is None:
        certificate_name = profile_field_default("certificate_name")
        country = profile_field_default("country")
        registration_role = profile_field_default("registration_role")
    else:
        certificate_name = profile.certificate_name
        country = profile.country
        registration_role = profile.registration_role
    name = certificate_name or user.get_full_name() or ""
    return {
        "name": name,
        "country": country,
        "role": registration_role,
    }


def registration_role_label(role_value):
    """The human label behind a stored ``registration_role`` value.

    ``LearnerProfile.registration_role`` is a plain ``CharField`` compatibility
    projection, so it has no ``get_..._display``; a profile written before a
    choice was renamed (or by an import) can hold a value the enum no longer
    knows, and that is shown verbatim rather than hidden.
    """

    if not role_value:
        return ""
    try:
        return CourseRegistration.Role(role_value).label
    except ValueError:
        return role_value


def missing_profile_field_names(user):
    """The person-level fields (spec §7.5) the account still has not supplied.

    Signed-in-home spec §8.2 asks registration for exactly these and no more:
    everything the profile already knows is shown read-only and correctable,
    and only what is genuinely unknown is an open field.
    """

    if not has_authenticated_registration_user(user):
        return ()

    initial_values = authenticated_registration_initial_values(user)
    missing = []
    for field_name in ("name", "country", "role"):
        if not initial_values.get(field_name):
            missing.append(field_name)
    return tuple(missing)


def authenticated_registration_identity(user):
    """The read-only identity block of the "One final step" card (§8.2).

    A value the profile does not hold stays empty here, because §8.2 renders
    exactly those as open fields above the fold instead of as a blank row.
    """

    if not has_authenticated_registration_user(user):
        return {}

    initial_values = authenticated_registration_initial_values(user)
    return {
        "name": initial_values.get("name") or "",
        "email": user.email or "",
        "country": initial_values.get("country") or "",
        "role": registration_role_label(initial_values.get("role")),
    }


def configure_authenticated_registration_user(form, user):
    if not has_authenticated_registration_user(user):
        return

    form.fields["email"].initial = user.email
    form.fields["email"].disabled = True
    form.fields["email"].help_text = "Using your account email."

    if not form.is_bound:
        initial_values = authenticated_registration_initial_values(user)
        form.initial.update(initial_values)


def normalized_registration_email(email):
    raw_email = email or ""
    email_stripped = raw_email.strip()
    return email_stripped.lower()


def registration_email_taken(campaign, email_normalized):
    return CourseRegistration.objects.filter(
        campaign=campaign,
        email_normalized=email_normalized,
    ).exists()


def save_registration_user_profile(user, registration):
    if not has_authenticated_registration_user(user):
        return

    profile, update_fields = update_user_profile_from_registration(
        user,
        registration,
    )
    if update_fields:
        profile.save(update_fields=update_fields)


class CourseRegistrationForm(forms.ModelForm):
    accepted_newsletter = forms.BooleanField(
        label=(
            "I agree to be added to the DataTalks.Club newsletter and "
            "receive course updates."
        ),
        required=True,
        widget=ACCEPTED_NEWSLETTER_WIDGET,
    )
    experience = forms.CharField(
        label="Experience",
        required=False,
        widget=EXPERIENCE_WIDGET,
        help_text=(
            "Your background: years of experience, relevant projects, "
            "tools you use."
        ),
    )
    motivation = forms.CharField(
        label="Why do you want to join?",
        required=False,
        widget=MOTIVATION_WIDGET,
    )
    years_experience = forms.ChoiceField(
        label="Years of professional software or ML experience",
        required=False,
        widget=YEARS_EXPERIENCE_WIDGET,
        choices=[],
        help_text="Optional.",
    )
    linkedin_url = forms.URLField(
        label="LinkedIn profile",
        required=False,
        widget=PROFILE_URL_WIDGET,
        help_text="Optional. A link to your LinkedIn profile, starting with https://.",
    )
    github_url = forms.URLField(
        label="GitHub profile",
        required=False,
        widget=PROFILE_URL_WIDGET,
        help_text="Optional. A link to your GitHub profile, starting with https://.",
    )
    prereq_python = forms.BooleanField(
        label="I am comfortable with Python",
        required=False,
        widget=PREREQ_WIDGET,
    )
    prereq_docker = forms.BooleanField(
        label="I know Docker basics",
        required=False,
        widget=PREREQ_WIDGET,
    )
    prereq_http_apis = forms.BooleanField(
        label="I have worked with HTTP APIs",
        required=False,
        widget=PREREQ_WIDGET,
    )
    prereq_cli = forms.BooleanField(
        label="I am comfortable with the command line",
        required=False,
        widget=PREREQ_WIDGET,
    )
    accepted_nebius_sharing = forms.BooleanField(
        label=(
            "Share my application with Nebius and Nebius Academy "
            "for review, credits and course operation"
        ),
        required=False,
        widget=ACCEPTED_NEWSLETTER_WIDGET,
        help_text="Required for application-review courses.",
    )

    class Meta:
        model = CourseRegistration
        fields = [
            "email",
            "name",
            "company_name",
            "country",
            "role",
            "experience",
            "years_experience",
            "prereq_python",
            "prereq_docker",
            "prereq_http_apis",
            "prereq_cli",
            "linkedin_url",
            "github_url",
            "motivation",
            "comment",
            "accepted_newsletter",
            "accepted_nebius_sharing",
        ]
        widgets = {
            "email": EMAIL_WIDGET,
            "name": NAME_WIDGET,
            "company_name": COMPANY_NAME_WIDGET,
            "country": COUNTRY_SELECT_WIDGET,
            "role": ROLE_WIDGET,
            "experience": EXPERIENCE_WIDGET,
            "linkedin_url": PROFILE_URL_WIDGET,
            "github_url": PROFILE_URL_WIDGET,
            "motivation": MOTIVATION_WIDGET,
            "comment": COMMENT_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        self.campaign = kwargs.pop("campaign", None)
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        configure_registration_form_fields(self, self.campaign)
        configure_authenticated_registration_user(self, self.user)

    def clean_email(self):
        if has_authenticated_registration_user(self.user):
            email = self.user.email
        else:
            email = self.cleaned_data["email"]

        email_normalized = normalized_registration_email(email)
        if registration_email_taken(self.campaign, email_normalized):
            raise forms.ValidationError(
                "You have already registered for this course."
            )
        return email_normalized

    def clean_country(self):
        country = self.cleaned_data["country"]
        if not country:
            return ""
        if not region_for_country(country):
            raise forms.ValidationError("Select a valid country.")
        return country

    def clean_accepted_newsletter(self):
        accepted_newsletter = self.cleaned_data["accepted_newsletter"]
        if not accepted_newsletter:
            raise forms.ValidationError("This field is required.")
        return accepted_newsletter

    def clean_accepted_nebius_sharing(self):
        accepted_nebius_sharing = self.cleaned_data.get("accepted_nebius_sharing")
        if (
            "accepted_nebius_sharing" in self.fields
            and self.fields["accepted_nebius_sharing"].required
            and not accepted_nebius_sharing
        ):
            raise forms.ValidationError("This field is required.")
        return bool(accepted_nebius_sharing)

    def save(self, commit=True):
        registration = super().save(commit=False)
        registration.campaign = self.campaign
        registration.course = self.campaign.current_course
        registration.region = region_for_country(registration.country)
        if campaign_requires_approval(self.campaign):
            registration.status = CourseRegistration.Status.PENDING
        else:
            registration.status = CourseRegistration.Status.APPROVED
        if has_authenticated_registration_user(self.user):
            registration.user = self.user
            registration.email = self.user.email
        if commit:
            registration.save()
            save_registration_user_profile(self.user, registration)
        return registration
