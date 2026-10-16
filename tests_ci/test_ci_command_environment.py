from __future__ import annotations

from ci.verification import component_environment_fingerprint
from scripts.ci import TEST_ENVIRONMENT_UNSET, TEST_MEDIA_STORE, _test_environment


def test_maintained_test_environment_overrides_ambient_s3(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_MEDIA_STORE_BACKEND", "s3")
    for name in TEST_ENVIRONMENT_UNSET:
        monkeypatch.setenv(name, "ambient-production-value")

    environment = _test_environment(async_unsafe=True)

    assert environment["DJANGO_SETTINGS_MODULE"] == "website.settings.test"
    assert environment["DJANGO_ALLOW_ASYNC_UNSAFE"] == "true"
    assert environment["PUBLIC_MEDIA_STORE_BACKEND"] == TEST_MEDIA_STORE == "memory"
    assert all(name not in environment for name in TEST_ENVIRONMENT_UNSET)


def test_playwright_evidence_environment_records_owned_memory(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_MEDIA_STORE_BACKEND", "s3")

    fingerprint = component_environment_fingerprint("playwright")

    assert fingerprint["allowlisted_config"]["PUBLIC_MEDIA_STORE_BACKEND"] == "memory"
