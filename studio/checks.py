"""Django checks for the immutable Studio management registry."""

from django.core.checks import Error, Tags, register

from core.capabilities import validate_capability
from management_registry import CAPABILITY_REGISTRY


@register(Tags.security)
def check_capability_registry(app_configs: object, **kwargs: object) -> list[Error]:
    del app_configs, kwargs
    errors: list[Error] = []
    for capability in CAPABILITY_REGISTRY:
        for problem in validate_capability(capability):
            errors.append(
                Error(
                    problem,
                    id="studio.E001",
                    hint="Fix the code-owned capability declaration; unsafe entries fail closed.",
                )
            )
    return errors
