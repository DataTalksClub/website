from django.urls import path

from accounts.views.account_settings import account_settings
from accounts.views.account_toggles import (
    toggle_dark_mode,
    update_account_toggle,
)
from accounts.views.disabled import disabled
from accounts.views.email_preferences import account_email_preferences
from accounts.views.home_dismissals import dismiss_home_item
from accounts.views.impersonation import stop_impersonating
from accounts.views.login import social_login_view
from accounts.views.social_connections import (
    disconnect_social_account,
    social_connections_moved,
)
from accounts.views.timezone import update_timezone_preference
from accounts.views.welcome import welcome

urlpatterns = [
    path('settings/', account_settings, name='account_settings'),
    path('welcome/', welcome, name='account_welcome'),
    path('home/dismiss/', dismiss_home_item, name='dismiss_home_item'),
    path('login/', social_login_view, name='login'),
    path('email/', disabled),
    path('password/reset/', disabled),
    path('toggle-dark-mode/', toggle_dark_mode, name='toggle_dark_mode'),
    path(
        'settings/toggle/',
        update_account_toggle,
        name='update_account_toggle',
    ),
    path(
        'settings/email-preferences/',
        account_email_preferences,
        name='account_email_preferences',
    ),
    path(
        'settings/timezone/',
        update_timezone_preference,
        name='update_timezone_preference',
    ),
    path(
        'settings/sign-in-methods/disconnect/',
        disconnect_social_account,
        name='disconnect_social_account',
    ),
    # website/urls.py includes this module before allauth's, so this entry
    # shadows allauth's own ConnectionsView at the same path.  The name stays
    # allauth's, so `reverse('socialaccount_connections')` — used by allauth
    # after a provider is connected, and by accounts/identity_inventory.py —
    # still resolves to this path, which now leads to account settings.
    path('3rdparty/', social_connections_moved),
    path('stop-impersonating/', stop_impersonating, name='stop_impersonating'),
]
