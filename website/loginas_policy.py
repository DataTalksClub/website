def can_login_as(request, target_user):
    """
    Determine if the current user can impersonate another user.

    Staff users can impersonate regular users (students) but cannot
    impersonate other staff members or superusers for security reasons.

    Args:
        request: The current HTTP request
        target_user: The user to be impersonated

    Returns:
        bool: True if impersonation is allowed, False otherwise
    """
    return request.user.is_staff and not target_user.is_staff
