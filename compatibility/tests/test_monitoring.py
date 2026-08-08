from __future__ import annotations

import json
from unittest.mock import patch

from django.test import Client, override_settings

from compatibility.monitoring import safe_compatibility_event


def test_safe_event_uses_only_contract_or_low_cardinality_groups() -> None:
    event = safe_compatibility_event(
        host="datatalks.club",
        path="/gone?token=secret#private",
        method="GET",
        status=410,
        referrer="https://datatalks.club/fixture/?email=person@example.com",
        known_contracts={
            "datatalks.club/gone": "contract-gone",
            "datatalks.club/fixture/": "contract-fixture",
        },
    )

    assert event is not None
    assert event.path_group == "contract:contract-gone"
    assert event.referrer_group == "contract:contract-fixture"
    encoded = json.dumps(event.properties())
    assert "token=secret" not in encoded
    assert "person@example.com" not in encoded


def test_unknown_and_external_dimensions_do_not_include_raw_values() -> None:
    event = safe_compatibility_event(
        host="datatalks.club",
        path="/users/person@example.com/private-value?code=credential-canary",
        method="POST",
        status=500,
        referrer="https://external.example/people/person@example.com?token=secret",
    )

    assert event is not None
    assert event.path_group == "unknown"
    assert event.referrer_group == "external"
    assert event.method == "other"
    encoded = json.dumps(event.properties())
    for private in ("person@example.com", "credential-canary", "external.example", "secret"):
        assert private not in encoded


def test_success_records_safe_performance_and_crawler_dimensions() -> None:
    event = safe_compatibility_event(
        host="datatalks.club",
        path="/sitemap.xml",
        method="GET",
        status=200,
        user_agent="Googlebot/2.1 (+https://www.google.com/bot.html)",
        duration_ms=123,
    )

    assert event.event_kind == "request"
    assert event.request_kind == "sitemap"
    assert event.crawler_kind == "googlebot"
    assert event.duration_ms == 123


def test_contract_lookup_preserves_path_case() -> None:
    known = {"datatalks.club/Docs/Exact/": "contract-docs-exact"}

    exact = safe_compatibility_event(
        host="DATATALKS.CLUB",
        path="/Docs/Exact/",
        method="GET",
        status=404,
        known_contracts=known,
    )
    wrong_case = safe_compatibility_event(
        host="datatalks.club",
        path="/docs/exact/",
        method="GET",
        status=404,
        known_contracts=known,
    )

    assert exact.path_group == "contract:contract-docs-exact"
    assert wrong_case.path_group == "unknown"


@override_settings(
    ROOT_URLCONF="compatibility.tests.fixture_urls",
    ALLOWED_HOSTS=["datatalks.club"],
    COMPATIBILITY_CONTRACT_PATHS={"datatalks.club/gone": "contract-gone"},
)
def test_middleware_emits_safe_event_without_passing_request() -> None:
    with patch("compatibility.monitoring.emit_compatibility_event") as emit:
        response = Client().get(
            "/gone?token=secret",
            headers={
                "host": "datatalks.club",
                "referer": "https://evil.example/private?email=person@example.com",
            },
        )

    assert response.status_code == 410
    emitted = emit.call_args.args[0]
    assert emitted.path_group == "contract:contract-gone"
    assert emitted.referrer_group == "external"
    encoded = json.dumps(emitted.properties())
    assert "token=secret" not in encoded
    assert "person@example.com" not in encoded


@override_settings(
    ROOT_URLCONF="compatibility.tests.fixture_urls",
    ALLOWED_HOSTS=["datatalks.club"],
)
def test_local_collector_suppresses_fake_runtime_events() -> None:
    from compatibility.django import DjangoTargetCollector

    with patch("compatibility.monitoring.emit_compatibility_event") as emit:
        observation = DjangoTargetCollector(allowed_hosts={"datatalks.club"}).observe(
            "https://datatalks.club/gone"
        )

    assert observation.status == 410
    emit.assert_not_called()
