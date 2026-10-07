"""Site adapter for the shared community-base project submission form (#456).

The field contract and its validation live in ``community_base.coursework``
(C5.2n, pinned at v0.5.20); this module is the site-side adoption for
``courses.models.project.ProjectSubmission``, mirroring the AI Shipping Labs
adapter (AI-Shipping-Labs/website#1777).

- ``ProjectSubmissionTarget`` exposes the package project toggles for one site
  ``Project``: every submission on this site captures the commit id, and peer
  review is cohort-based, so the package pooled-review lock never applies;
  the remaining toggles and the state lock come from the model, while the
  package deadline lock never applies (the site's state machine owns closure).
- ``ProjectSubmissionForm`` subclasses the shared form so saving writes the
  site's ``ProjectSubmission``; the package save path writes the package
  coursework models and must not be used for persistence on this site. The
  FAQ contribution field is the documented subclass extra field and keeps the
  site's enforced ``clean_faq_contribution_url`` check.
- Certificate name keeps the pre-adoption behavior: the write goes to the
  learner profile, and the prefill prefers the profile value with the
  enrollment display name as fallback.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from community_base.coursework.project_forms import (
    ProjectSubmissionForm as BaseProjectSubmissionForm,
)

from courses.models.cohort import Enrollment
from courses.models.project import ProjectSubmission
from courses.validators.url_status_transport import clean_faq_contribution_url


class ProjectSubmissionTarget:
    """The package project-toggles contract for one site ``Project``."""

    # Every submission captures the commit id, and peer review is
    # cohort-based: the package pooled-review lock never applies.
    commit_id_field = True
    uses_pooled_review = False

    def __init__(self, project):
        self.project = project

    @property
    def submission_due_date(self):
        """``None``: the state machine owns closure on this site.

        Save, update, and removal stay available for as long as the project
        state permits (issue #456), so the dated project presents as undated
        to the package deadline lock; the page still shows the countdown.
        """
        return None

    def __getattr__(self, name):
        # The remaining toggles and the state lock are the model's own
        # fields: learning_in_public_cap_project, time_spent_project_field,
        # faq_contribution_field, state.
        return getattr(self.project, name)


class ProjectSubmissionForm(BaseProjectSubmissionForm):
    """The shared form persisting to the site's ``ProjectSubmission``."""

    extra_fields_template = "include/project_faq_extra_field.html"

    faq_contribution_url = forms.URLField(
        label="FAQ contribution PR or issue URL",
        required=False,
    )

    def __init__(
        self,
        data=None,
        *,
        project,
        submission=None,
        enrollment=None,
        user=None,
        **kwargs,
    ):
        super().__init__(
            data,
            project=ProjectSubmissionTarget(project),
            submission=submission,
            enrollment=enrollment,
            user=user,
            # Certificates are issued on this site, so the shared certificate
            # name field always renders; the site setting stays unset.
            certificate_name_field=True,
            **kwargs,
        )
        if not project.faq_contribution_field:
            self.fields.pop("faq_contribution_url", None)

    def _initial_from(self, submission, enrollment) -> dict:
        initial = super()._initial_from(submission, enrollment)
        if enrollment is not None:
            initial["certificate_name"] = learner_profile_certificate_name(
                enrollment
            )
        return initial

    def clean_faq_contribution_url(self):
        url = self.cleaned_data.get("faq_contribution_url") or ""
        try:
            return clean_faq_contribution_url(url)
        except ValidationError as error:
            raise ValidationError(error.messages) from None

    def save(self) -> tuple[ProjectSubmission, bool]:
        """Create or update the site submission. Call only after ``is_valid()``.

        Keeps the site's update semantics: ``submitted_at`` is refreshed so
        deadline reminders see the latest activity, and a missing enrollment
        is created only inside the save transaction, so a rejected POST
        leaves nothing behind.
        """
        data = self.cleaned_data
        with transaction.atomic():
            enrollment = self._save_enrollment()
            existing = self.submission
            if existing is not None:
                submission = existing
                created = False
                submission.submitted_at = timezone.now()
            else:
                submission = ProjectSubmission(
                    project=self.project.project,
                    student=self.user,
                    enrollment=enrollment,
                )
                created = True
            submission.github_link = data["github_link"]
            submission.commit_id = data.get("commit_id", "")
            if "learning_in_public_links" in self.fields:
                submission.learning_in_public_links = (
                    data.get("learning_in_public_links") or []
                )
            if "time_spent" in self.fields:
                submission.time_spent = data.get("time_spent")
            if "faq_contribution_url" in self.fields:
                submission.faq_contribution_url = (
                    data.get("faq_contribution_url") or ""
                )
            submission.full_clean()
            submission.save()
            self._save_certificate_name()
        self.submission = submission
        return submission, created

    def _save_enrollment(self) -> Enrollment:
        """The learner's enrollment, created only inside the save transaction."""
        if self.submission is not None:
            return self.submission.enrollment
        enrollment, _ = Enrollment.objects.get_or_create(
            student=self.user,
            course=self.project.project.course,
        )
        self.enrollment = enrollment
        return enrollment

    def _save_certificate_name(self) -> None:
        """Persist the posted certificate name to the learner profile.

        A blank value keeps the current name, matching the pre-adoption
        submit path.
        """
        from courses.models.learner_profile import ensure_learner_profile

        certificate_name = (self.cleaned_data.get("certificate_name") or "").strip()
        if not certificate_name:
            return

        profile = ensure_learner_profile(self.user)
        profile.certificate_name = certificate_name
        profile.save(update_fields=["certificate_name"])


def learner_profile_certificate_name(enrollment: Enrollment) -> str:
    """The site's certificate-name prefill: profile value, else display name."""
    from courses.models.learner_profile import (
        learner_profile_for,
        profile_field_default,
    )

    profile = learner_profile_for(enrollment.student)
    certificate_name = (
        profile.certificate_name
        if profile is not None
        else profile_field_default("certificate_name")
    )
    if certificate_name:
        return certificate_name
    return enrollment.display_name


def build_project_submission_form(project, *, user, data=None, enrollment=None):
    """The adapter form for the viewer's own submission.

    The enrollment is looked up without creating it: the save path creates a
    missing enrollment inside its transaction, so a rejected POST leaves
    nothing behind. Callers that already resolved the enrollment (the page
    context get-or-creates it) pass it through. Without one,
    learning-in-public links stay enabled, which matches a brand-new
    enrollment.
    """
    submission = None
    if user.is_authenticated:
        if enrollment is None:
            enrollment = Enrollment.objects.filter(
                student=user,
                course=project.course,
            ).first()
        submission = ProjectSubmission.objects.filter(
            project=project,
            student=user,
            volunteer_review_only=False,
        ).first()
    return ProjectSubmissionForm(
        data,
        project=project,
        submission=submission,
        enrollment=enrollment,
        user=user,
    )
