from urllib.parse import urlencode

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def cohort_projects_url(cohort) -> str:
    """Build the canonical gallery URL from public, stable identities."""

    query = urlencode({"course": cohort.course.slug, "cohort": cohort.identifier})
    return f"{reverse('all_projects')}?{query}"


@register.simple_tag
def family_projects_url(family) -> str:
    return f"{reverse('all_projects')}?{urlencode({'course': family.slug})}"
