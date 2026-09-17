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
# Unlike `website.settings.development`, the workstation runs no Relay ingress to
# receive durable-job callbacks, so the `relay` backend from `website.settings.base`
# would only ever fail to submit. Match `local_review`/`test` and run jobs inline.
# MAIL_BACKEND has the identical problem one level down: with jobs now running
# inline, a real `cb_mail.deliver` job (e.g. an enrollment confirmation) tries a
# real relay delivery with no `RELAY_BASE_URL` configured and fails outright,
# instead of the harmless queued-but-never-submitted state the old JOBS_BACKEND
# left it in. `memory` matches `website.settings.test`.
COMMUNITY_BASE = {**COMMUNITY_BASE, "JOBS_BACKEND": "sync", "MAIL_BACKEND": "memory"}  # noqa: F405
# D2.2a: the package content source model requires a nonblank webhook
# secret even locally, so local runs fall back to the shared local
# development placeholder; deployed settings keep the env-only boundary
# from ``website.settings.base``.
for _declaration in COMMUNITY_BASE["CONTENT_SOURCES"]:  # noqa: F405
    if not _declaration["webhook_secret"]:
        _declaration["webhook_secret"] = LOCAL_DEVELOPMENT_SECRET_KEY  # noqa: F405
del _declaration
