from django.core.exceptions import ImproperlyConfigured

from core.bootstrap import require_environment
from deploy.development_target import DEVELOPMENT_HOSTNAME, DEVELOPMENT_ORIGIN

from .base import *  # noqa: F403

require_environment(RUNTIME_ENVIRONMENT, RuntimeEnvironment.DEVELOPMENT)  # noqa: F405
DEBUG = False
ENVIRONMENT = "development"
SECRET_KEY = secure_secret_from_environment()  # noqa: F405
ACCOUNT_CANONICAL_ORIGIN = DEVELOPMENT_ORIGIN
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", DEVELOPMENT_HOSTNAME)  # noqa: F405
if ALLOWED_HOSTS != [DEVELOPMENT_HOSTNAME]:
    raise ImproperlyConfigured("Development requires the exact DJANGO_ALLOWED_HOSTS value")
CSRF_TRUSTED_ORIGINS = env_list(  # noqa: F405
    "DJANGO_CSRF_TRUSTED_ORIGINS", DEVELOPMENT_ORIGIN
)
if CSRF_TRUSTED_ORIGINS != [DEVELOPMENT_ORIGIN]:
    raise ImproperlyConfigured("Development requires the exact DJANGO_CSRF_TRUSTED_ORIGINS value")
DATABASES = {
    "default": database_from_environment(  # noqa: F405
        environment=RuntimeEnvironment.DEVELOPMENT,  # noqa: F405
    )
}
NOINDEX = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REDIRECT_EXEMPT = [r"^health/ready$"]
