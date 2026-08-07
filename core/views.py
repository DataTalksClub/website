from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


def home(request: HttpRequest):
    return render(request, "core/home.html")


@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    del request
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})


def _configuration_status() -> tuple[str, list[str]]:
    missing = [
        name for name in settings.REQUIRED_BOOTSTRAP_SETTINGS if not getattr(settings, name, None)
    ]
    return ("ok", []) if not missing else ("error", missing)


def _database_status() -> tuple[str, str | None]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return "error", "database unavailable"
    return "ok", None


def _migration_status() -> tuple[str, str | None]:
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(targets):
            return "error", "unapplied migrations"
    except Exception:
        return "error", "migration state unavailable"
    return "ok", None


@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    del request
    config_status, missing = _configuration_status()
    database_status, database_error = _database_status()
    migration_status, migration_error = (
        _migration_status() if database_status == "ok" else ("error", "database unavailable")
    )

    checks: dict[str, dict[str, Any]] = {
        "configuration": {"status": config_status},
        "database": {"status": database_status},
        "migrations": {"status": migration_status},
    }
    if missing:
        checks["configuration"]["missing"] = missing
    if database_error:
        checks["database"]["message"] = database_error
    if migration_error:
        checks["migrations"]["message"] = migration_error

    ready = all(check["status"] == "ok" for check in checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )
