from allauth.account.signals import password_reset as allauth_password_reset
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import User
from course_management.observability import record_event


@receiver(user_logged_in)
def record_user_logged_in(sender, request, user, **kwargs):
    path = getattr(request, "path", "")
    event_name = "auth.login_success"
    if path.startswith("/admin/login/user/"):
        event_name = "auth.impersonation_started"

    record_event(
        event_name,
        request=request,
        user=user,
        properties={
            "user_id": user.id,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        },
    )


@receiver(user_logged_out)
def record_user_logged_out(sender, request, user, **kwargs):
    record_event(
        "auth.logout",
        request=request,
        user=user,
        properties={
            "user_id": user.id if user is not None else None,
        },
    )


@receiver(user_login_failed)
def record_user_login_failed(sender, credentials, request, **kwargs):
    record_event(
        "auth.login_failed",
        request=request,
        properties={
            "credential_keys": sorted(credentials.keys()),
        },
    )


@receiver(post_save, sender=User)
def record_user_created(sender, instance, created, **kwargs):
    if not created:
        return

    record_event(
        "auth.user_created",
        user=instance,
        properties={
            "user_id": instance.id,
            "is_staff": instance.is_staff,
            "is_superuser": instance.is_superuser,
        },
    )


@receiver(allauth_password_reset)
def verify_address_after_password_reset(sender, request, user, **kwargs):
    """A completed reset proves the account address, so stop asking again.

    The reset link was mailed to an address on this account; following it is
    the same proof a verification link is.  Without this an imported or
    one-click account would reset its password and then be sent a second,
    verification email at its first sign-in.  The account address is marked
    only when no unverified address of another spelling is on the account, so
    the reset cannot have gone anywhere else.
    """

    from allauth.account.models import EmailAddress

    from accounts.identity_values import normalize_account_email

    account_email = normalize_account_email(user.email)
    if account_email is None:
        return
    addresses = list(EmailAddress.objects.filter(user=user))
    if any(
        not address.verified and normalize_account_email(address.email) != account_email
        for address in addresses
    ):
        return
    matching = [a for a in addresses if normalize_account_email(a.email) == account_email]
    if not matching:
        EmailAddress.objects.create(
            user=user,
            email=account_email,
            verified=True,
            primary=not any(a.primary for a in addresses),
        )
        return
    for address in matching:
        if not address.verified:
            address.verified = True
            address.save(update_fields=["verified"])
