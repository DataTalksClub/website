"""Reading and removing the sign-in methods linked to one account.

The account settings page owns this surface; allauth's separate
``/accounts/3rdparty/`` page is a redirect into it.  The rule that stops a
member disconnecting the provider that is their only way back in is *not*
restated here: ``allauth.socialaccount.forms.DisconnectForm`` is used
verbatim, so ``flows.connect.validate_disconnect`` (last remaining account,
no usable password, no verified email) still decides, and
``flows.connect.disconnect`` still deletes the row, fires
``social_account_removed`` and sends allauth's notification mail.
"""

from __future__ import annotations

from dataclasses import dataclass

from allauth.socialaccount.forms import DisconnectForm
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers import registry


@dataclass(frozen=True, slots=True)
class SocialConnection:
    """One linked provider, as the settings page needs to show it."""

    pk: int
    provider_id: str
    provider_name: str
    identifier: str


@dataclass(frozen=True, slots=True)
class DisconnectOutcome:
    removed: bool
    message: str
    provider_id: str = ""


def list_social_connections(user) -> list[SocialConnection]:
    """The providers this account can sign in with, oldest link first."""

    accounts = SocialAccount.objects.filter(user=user).order_by("provider", "pk")
    return [
        SocialConnection(
            pk=account.pk,
            provider_id=account.provider,
            provider_name=provider_name(account.provider),
            identifier=_identifier(account),
        )
        for account in accounts
    ]


def provider_name(provider_id: str) -> str:
    """The provider's display name, without resolving its configured app.

    ``SocialAccount.get_provider()`` needs a ``SocialApp``, and raises when one
    is missing.  Listing a member's own connections must not depend on that, so
    the name comes from the registered provider class instead.
    """

    provider_class = registry.get_class(provider_id)
    if provider_class is not None:
        return provider_class.name
    return provider_id.replace("_", " ").title()


def _identifier(account: SocialAccount) -> str:
    """How the provider knows this member, for telling two links apart."""

    extra_data = account.extra_data if isinstance(account.extra_data, dict) else {}
    for key in ("email", "login", "name", "preferred_username"):
        value = extra_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return account.uid


def disconnect_social_connection(request, account_pk: str) -> DisconnectOutcome:
    """Remove one linked provider from the signed-in account.

    ``DisconnectForm`` limits its queryset to ``request.user``'s own accounts,
    so an unknown or someone else's primary key is rejected as invalid rather
    than acted on.
    """

    form = DisconnectForm(data={"account": account_pk}, request=request)
    if not form.is_valid():
        return DisconnectOutcome(removed=False, message=_first_error(form))

    account = form.cleaned_data["account"]
    provider_id = account.provider
    name = provider_name(provider_id)
    form.save()
    return DisconnectOutcome(
        removed=True,
        message=f"{name} was disconnected from your account.",
        provider_id=provider_id,
    )


def _first_error(form: DisconnectForm) -> str:
    for errors in form.errors.values():
        for error in errors:
            return str(error)
    return "That sign-in method could not be disconnected."
