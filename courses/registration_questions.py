"""Per-course registration questions.

The learner profile (``LearnerProfile``) answers the person-level questions
once for every course.  On top of it a registration campaign may ask its own
questions -- an application-style course wants experience, prerequisites and a
motivation.  Which ones a campaign asks is campaign data
(``RegistrationCampaign.registration_questions``, edited in Studio/admin); the
questions themselves are this fixed vocabulary, because every answer is stored
on a typed ``CourseRegistration`` column that exports, admin and the approval
tooling read.
"""

from __future__ import annotations

#: Every question a campaign may ask, keyed by its ``CourseRegistration``
#: column, in the order the registration card renders them.
REGISTRATION_QUESTION_CHOICES = (
    ("experience", "Experience summary"),
    ("prereq_python", "Prerequisite: Python"),
    ("prereq_docker", "Prerequisite: Docker basics"),
    ("prereq_http_apis", "Prerequisite: HTTP APIs"),
    ("prereq_cli", "Prerequisite: command line"),
    ("motivation", "Why do you want to join?"),
    ("comment", "Anything else (comment)"),
    ("accepted_nebius_sharing", "Consent: share the application with Nebius"),
)
REGISTRATION_QUESTION_KEYS = tuple(key for key, _label in REGISTRATION_QUESTION_CHOICES)

#: What an application-style campaign (``requires_approval``) asks when its
#: editors have not chosen a set: the application questions the course
#: platform has always asked approval courses.
APPLICATION_QUESTIONS = (
    "experience",
    "prereq_python",
    "prereq_docker",
    "prereq_http_apis",
    "prereq_cli",
    "motivation",
    "accepted_nebius_sharing",
)


def validate_registration_questions(value) -> None:
    from django.core.exceptions import ValidationError

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("Registration questions must be a list of question keys.")
    unknown = sorted(set(value) - set(REGISTRATION_QUESTION_KEYS))
    if unknown:
        raise ValidationError(f"Unknown registration questions: {', '.join(unknown)}.")


def campaign_registration_questions(campaign) -> tuple[str, ...]:
    """The question keys ``campaign`` asks, in render order."""

    if campaign is None:
        return ()
    configured = list(getattr(campaign, "registration_questions", None) or [])
    if not configured and getattr(campaign, "requires_approval", False):
        configured = list(APPLICATION_QUESTIONS)
    chosen = set(configured)
    return tuple(key for key in REGISTRATION_QUESTION_KEYS if key in chosen)
