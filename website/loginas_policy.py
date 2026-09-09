def can_login_as(request, target_user):
    """
    Determine if the current user can impersonate another user.

    Only active staff may impersonate, and only active, non-quarantined
    learners: staff, superuser, inactive, and quarantined targets are denied
    so impersonation can never cross a management or identity boundary.

    Args:
        request: The current HTTP request
        target_user: The user to be impersonated

    Returns:
        bool: True if impersonation is allowed, False otherwise
    """
    user = request.user
    if not (getattr(user, "is_authenticated", False) and user.is_active and user.is_staff):
        return False
    if target_user.is_staff or target_user.is_superuser:
        return False
    if not target_user.is_active:
        return False
    if target_user.identity_state == target_user.IdentityState.QUARANTINED:
        return False
    return True
