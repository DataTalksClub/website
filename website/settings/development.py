from .base import *  # noqa: F403

DEBUG = False
ENVIRONMENT = "development"
SECRET_KEY = secure_secret_from_environment()  # noqa: F405
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "web.dtcdev.click")  # noqa: F405
CSRF_TRUSTED_ORIGINS = env_list(  # noqa: F405
    "DJANGO_CSRF_TRUSTED_ORIGINS", "https://web.dtcdev.click"
)
DATABASES = {"default": database_from_environment()}  # noqa: F405
NOINDEX = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
