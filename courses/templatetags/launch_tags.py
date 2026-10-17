import uuid

from django import template

register = template.Library()


@register.simple_tag
def launch_key():
    return str(uuid.uuid4())
