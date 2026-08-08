from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from core.seo import validated_canonical_url

register = template.Library()


@register.simple_tag
def canonical_link(value: object) -> SafeString:
    canonical = validated_canonical_url(value)
    if not canonical:
        return SafeString("")
    return format_html('<link rel="canonical" href="{}">', canonical)
