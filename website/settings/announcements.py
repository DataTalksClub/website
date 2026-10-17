"""Typed bootstrap values. Destinations and bot credentials never enter database rows."""

import json

from django.core.exceptions import ImproperlyConfigured

from core.bootstrap import parse_bool, parse_secret


def announcement_configuration(environment):
    enabled = parse_bool(
        "COURSE_ANNOUNCEMENTS_ENABLED",
        environment.get("COURSE_ANNOUNCEMENTS_ENABLED"),
        default=False,
    )
    raw_token = environment.get("COURSE_TELEGRAM_BOT_TOKEN", "")
    token = ""
    if raw_token:
        token = parse_secret("COURSE_TELEGRAM_BOT_TOKEN", raw_token, minimum_length=20)
    try:
        destinations = json.loads(environment.get("COURSE_TELEGRAM_DESTINATIONS", "{}"))
    except ValueError as error:
        raise ImproperlyConfigured("COURSE_TELEGRAM_DESTINATIONS must be a JSON object") from error
    if not isinstance(destinations, dict):
        raise ImproperlyConfigured("COURSE_TELEGRAM_DESTINATIONS must be a JSON object")
    for key, value in destinations.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise ImproperlyConfigured(
                "Telegram destination references must map to nonempty strings"
            )
    return enabled, token, destinations
