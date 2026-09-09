from pathlib import Path

from dotenv import load_dotenv

# `.env` is a local-development convenience only. Deployed settings never load it.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from .base import *  # noqa: E402,F403

DEBUG = True
# The workstation follows the CMP's usable local account flow through the
# bounded development-owner form.  This override is local-only; test,
# development, and production settings keep their explicit environment
# boundaries from ``website.settings.base``.
DEVELOPMENT_OWNER_LOGIN_ENABLED = True
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", LOCAL_DEVELOPMENT_SECRET_KEY)  # noqa: F405
ALLOWED_HOSTS = env_list(  # noqa: F405
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver,web.dtcdev.click"
)
CSRF_TRUSTED_ORIGINS = env_list(  # noqa: F405
    "DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,https://web.dtcdev.click"
)
# SQLite cannot create parent directories. These settings also boot inside a bare
# container (the scheduled image smoke test) where the gitignored `.tmp/` does not
# exist, so the default database path below would fail with "unable to open
# database file"; /app is owned by the runtime user, so creating it here works.
(BASE_DIR / ".tmp").mkdir(parents=True, exist_ok=True)
DATABASES = {
    "default": sqlite_database_from_environment(  # noqa: F405
        environment=RuntimeEnvironment.LOCAL,  # noqa: F405
        default_path=BASE_DIR / ".tmp" / "local.sqlite3",  # noqa: F405
    )
}
NOINDEX = True
