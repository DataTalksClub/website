from pathlib import Path

from dotenv import load_dotenv

# `.env` is a local-development convenience only. Deployed settings never load it.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from .base import *  # noqa: E402,F403

DEBUG = True
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", LOCAL_DEVELOPMENT_SECRET_KEY)  # noqa: F405
ALLOWED_HOSTS = env_list(  # noqa: F405
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver,web.dtcdev.click"
)
CSRF_TRUSTED_ORIGINS = env_list(  # noqa: F405
    "DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,https://web.dtcdev.click"
)
DATABASES = {
    "default": database_from_environment(  # noqa: F405
        environment=RuntimeEnvironment.LOCAL,  # noqa: F405
        allow_sqlite=True,
    )
}
NOINDEX = True
