from django.core.checks import Error, Tags, register

from .tokens import hasher_contract_is_valid


@register(Tags.security)
def check_management_credential_hasher(app_configs: object, **kwargs: object) -> list[Error]:
    del app_configs, kwargs
    if not hasher_contract_is_valid():
        return [
            Error(
                "The management credential hasher contract is unavailable.",
                id="management_auth.E001",
            )
        ]
    return []
