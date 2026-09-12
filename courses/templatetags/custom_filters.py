from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import urlize as urlize_impl
from django.utils.safestring import mark_safe

from accounts.services.timezones import format_user_datetime

register = template.Library()


@register.filter(is_safe=True, needs_autoescape=True)
@stringfilter
def urlize_target_blank(value, limit=30, autoescape=None):
    trim_url_limit = int(limit)
    urlized_value = urlize_impl(
        value,
        trim_url_limit=trim_url_limit,
        nofollow=True,
        autoescape=autoescape,
    )
    target_blank_value = urlized_value.replace("<a", '<a target="_blank"')
    return mark_safe(target_blank_value)


@register.filter
def user_datetime(value, user):
    return format_user_datetime(value, user)


@register.filter
def user_date_short(value, user):
    """Render a deadline as the compact "Sep 22" the module rows show beside a title.

    The full deadline is always rendered too, by ``user_datetime``; this is the same
    instant in the same timezone, written short enough to sit on one summary row.
    """
    return format_user_datetime(value, user, fmt="%b %-d")


@register.filter
def is_question_answered(answer):
    """Whether a rendered ``question_answers`` entry already carries an answer.

    A pure rendering concern for the homework progress rail/disc: it only
    reads the shape `courses.views.homework_answers` already builds -- an
    ``options`` list with per-option ``is_selected`` for MC/CB questions, or
    a ``text`` value for FF/FL ones -- so the initial disc/progress state on
    a GET request matches what was actually saved, without waiting on the
    client-side script that keeps it live afterwards.
    """
    if not isinstance(answer, dict):
        return False
    options = answer.get("options")
    if options is not None:
        return any(option.get("is_selected") for option in options)
    text = answer.get("text")
    return bool(text and text.strip())


@register.filter
def thousands(value):
    """Render an integer with comma thousands separators ("8009" -> "8,009")."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value
