"""Controlled tests are always simulated; they never enqueue a real recipient."""

from django.core.validators import validate_email

from courses.models import Announcement
from courses.services.launch_announcements import serialize_announcement
from courses.services.launch_validation import LaunchError, live_cohort, load_row


def test_send(payload):
    destination = payload.get("destination")
    if not isinstance(destination, str):
        raise LaunchError("Enter an explicit operator test destination.")
    validate_email(destination)
    row = load_row(Announcement, payload["id"])
    live_cohort(row.cohort)
    if payload.get("confirmed") is not True:
        raise LaunchError("Confirm the operator test destination.")
    result = serialize_announcement(row)
    return {
        "id": result["id"],
        "mode": "simulation",
        "test": True,
        "preview": result["preview"],
        "state": "simulated",
        "guidance": "No message was sent. Production delivery is a separate confirmed action.",
    }
