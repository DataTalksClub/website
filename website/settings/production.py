from django.core.exceptions import ImproperlyConfigured

from core.bootstrap import require_environment

from .base import *  # noqa: F403

require_environment(RUNTIME_ENVIRONMENT, RuntimeEnvironment.PRODUCTION)  # noqa: F405
DEBUG = False
ENVIRONMENT = "production"
SECRET_KEY = secure_secret_from_environment()  # noqa: F405
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")  # noqa: F405
if not ALLOWED_HOSTS:
    missing("DJANGO_ALLOWED_HOSTS")  # noqa: F405
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")  # noqa: F405
if not CSRF_TRUSTED_ORIGINS:
    missing("DJANGO_CSRF_TRUSTED_ORIGINS")  # noqa: F405
if any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Production requires HTTPS DJANGO_CSRF_TRUSTED_ORIGINS values")
# Account links must resolve on the host that served the request.  CANONICAL_ORIGIN
# stays the public apex so canonical tags consolidate there (spec 02), which is not
# where a staging host serves its own sign-in and verification routes.
ACCOUNT_CANONICAL_ORIGIN = CSRF_TRUSTED_ORIGINS[0]
# prod.datatalks.club is staging for its entire life: the apex still serves the
# indexed legacy corpus, so the same pages must not be indexable twice
# (_docs/runbooks/production-hosting-and-dns-migration.md section 9.3).  This is the
# application half of the contract; robots_header_value is the edge half.  Both flip
# together, under review, at the stage-2 apex swap and never before, which is why
# the safe value is written here rather than read from the environment.
NOINDEX = True
DATABASES = {
    "default": database_from_environment(  # noqa: F405
        environment=RuntimeEnvironment.PRODUCTION,  # noqa: F405
    )
}

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REDIRECT_EXEMPT = [r"^health/ready$"]
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
