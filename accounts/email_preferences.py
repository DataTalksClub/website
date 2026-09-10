"""Local mail category preferences (D1.2ca).

The three opt-out categories the retired Datamailer store held are fields
on the site user: ``None`` means allowed, ``False`` is an explicit opt-out.
The mail preference resolver reads them directly; the account pages read
and write them through :mod:`accounts.views.email_preferences`.
"""

from dataclasses import dataclass

CATEGORY_FIELDS = {
    "submission-results": "email_submission_confirmations",
    "deadline-reminders": "email_deadline_reminders",
    "course-updates": "email_course_updates",
}

EMAIL_PREFERENCE_FIELDS = frozenset(CATEGORY_FIELDS.values())


@dataclass(frozen=True)
class EmailPreferenceCategory:
    field: str
    label: str
    description: str


EMAIL_PREFERENCE_CATEGORIES = {
    category.field: category
    for category in (
        EmailPreferenceCategory(
            field="email_submission_confirmations",
            label="Homework and project submissions",
            description=(
                "Sends confirmation and score emails after you submit homework or a project."
            ),
        ),
        EmailPreferenceCategory(
            field="email_deadline_reminders",
            label="Deadline reminders",
            description=(
                "Sends reminders when homework or peer review deadlines "
                "are within 24 hours and you have not submitted."
            ),
        ),
        EmailPreferenceCategory(
            field="email_course_updates",
            label="General course-related emails",
            description=(
                "Sends general course and workshop messages, such as course start announcements."
            ),
        ),
    )
}
