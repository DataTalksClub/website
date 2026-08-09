from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.runtime_identity import LOCAL_VERSION, read_runtime_identity
from deploy.aws_gateway import AwsReleaseGateway
from deploy.contracts import (
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
)
from deploy.release_identity import (
    IdentityError,
    PublishedImageRecord,
    SourceIdentity,
    SourceIdentityConstructor,
    validate_schema2_version,
)
from deploy.smoke import Response, verify_health, verify_legacy_health

SHA = "3f6c227" + "a" * 33
DIGEST = f"sha256:{'b' * 64}"
CONFIG_DIGEST = f"sha256:{'c' * 64}"
ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website"
TASK_PREFIX = "arn:aws:ecs:eu-west-1:817685572750:task-definition"


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 8, 9, 14, 32, 5, tzinfo=UTC), "20260809-143205-3f6c227"),
        (datetime(2024, 2, 29, 23, 59, 59, tzinfo=UTC), "20240229-235959-3f6c227"),
        (
            datetime(2027, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=2))),
            "20261231-220000-3f6c227",
        ),
    ],
)
def test_constructor_seals_one_utc_instant(instant: datetime, expected: str) -> None:
    identity = SourceIdentityConstructor(lambda: instant).construct(SHA)

    assert identity == SourceIdentity(
        2, expected, SHA, instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


@pytest.mark.parametrize(
    "version",
    [
        "20261301-143205-3f6c227",
        "20260230-143205-3f6c227",
        "20230229-143205-3f6c227",
        "20260809-240000-3f6c227",
        "20260809-146000-3f6c227",
        "20260809-143260-3f6c227",
        "20261340-999999-3f6c227",
    ],
)
def test_canonical_version_parser_rejects_calendar_and_clock_invalid_stamps(
    version: str,
) -> None:
    with pytest.raises(IdentityError, match="valid UTC calendar instant"):
        validate_schema2_version(version, SHA)


def test_canonical_version_parser_accepts_a_valid_leap_day_boundary() -> None:
    version = "20240229-235959-3f6c227"

    assert validate_schema2_version(version, SHA) == version


def test_constructor_refuses_second_call() -> None:
    constructor = SourceIdentityConstructor(lambda: datetime(2026, 8, 9, 14, 32, 5, tzinfo=UTC))
    constructor.construct(SHA)

    with pytest.raises(IdentityError, match="only once"):
        constructor.construct(SHA)


@pytest.mark.parametrize(
    "clock",
    [lambda: datetime(2026, 8, 9), lambda: "2026-08-09T00:00:00Z"],
)
def test_constructor_rejects_invalid_clock(clock) -> None:
    with pytest.raises(IdentityError, match="aware UTC"):
        SourceIdentityConstructor(clock).construct(SHA)


def test_source_identity_round_trip_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    identity = SourceIdentityConstructor(
        lambda: datetime(2026, 8, 9, 14, 32, 5, tzinfo=UTC)
    ).construct(SHA)
    identity.write(path)

    assert SourceIdentity.read(path) == identity
    payload = json.loads(path.read_text())
    payload["unexpected"] = True
    with pytest.raises(IdentityError, match="fields differ"):
        SourceIdentity.from_dict(payload)


def test_source_and_published_record_parsers_reject_calendar_invalid_version() -> None:
    invalid_version = "20261340-999999-3f6c227"
    source_payload = {
        "identity_schema": 2,
        "version": invalid_version,
        "source_sha": SHA,
        "constructed_at": "2026-08-09T14:32:05Z",
    }
    with pytest.raises(IdentityError, match="valid UTC calendar instant"):
        SourceIdentity.from_dict(source_payload)

    with pytest.raises(IdentityError, match="valid UTC calendar instant"):
        PublishedImageRecord.from_dict(
            source_payload
            | {
                "repository_uri": REPOSITORY,
                "image_digest": DIGEST,
                "image_config_digest": CONFIG_DIGEST,
                "platform": "linux/amd64",
                "user": "10001:10001",
            }
        )


def test_publisher_appends_digest_without_reconstructing_source(tmp_path: Path) -> None:
    source = SourceIdentityConstructor(
        lambda: datetime(2026, 8, 9, 14, 32, 5, tzinfo=UTC)
    ).construct(SHA)
    record = PublishedImageRecord.publish(
        source,
        repository_uri="123456789012.dkr.ecr.eu-west-1.amazonaws.com/website",
        image_digest=DIGEST,
        image_config_digest=CONFIG_DIGEST,
        platform="linux/amd64",
        user="10001:10001",
    )
    path = tmp_path / "published.json"
    record.write(path)

    assert PublishedImageRecord.read(path) == record
    assert record.version == source.version
    assert record.constructed_at == source.constructed_at


def test_legacy_published_record_is_read_only() -> None:
    record = PublishedImageRecord.from_dict(
        {
            "source_sha": SHA,
            "repository_uri": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/website",
            "image_digest": DIGEST,
            "image_config_digest": CONFIG_DIGEST,
            "platform": "linux/amd64",
            "user": "10001:10001",
        },
        allow_legacy=True,
    )

    assert record.identity_schema == 1
    assert record.version == SHA
    assert record.constructed_at is None


def test_legacy_published_record_cannot_pose_as_a_schema2_record() -> None:
    with pytest.raises(IdentityError, match="must declare schema 2"):
        PublishedImageRecord.from_dict(
            {
                "identity_schema": 1,
                "version": SHA,
                "source_sha": SHA,
                "constructed_at": None,
                "repository_uri": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/website",
                "image_digest": DIGEST,
                "image_config_digest": CONFIG_DIGEST,
                "platform": "linux/amd64",
                "user": "10001:10001",
            },
            allow_legacy=True,
        )


def test_runtime_identity_local_fallback_and_sealed_triplet() -> None:
    assert read_runtime_identity({}).payload() == {
        "status": "ok",
        "version": LOCAL_VERSION,
        "source_sha": None,
        "image_digest": None,
    }
    assert read_runtime_identity(
        {"VERSION": "20260809-143205-3f6c227", "SOURCE_SHA": SHA, "IMAGE_DIGEST": DIGEST}
    ).payload() == {
        "status": "ok",
        "version": "20260809-143205-3f6c227",
        "source_sha": SHA,
        "image_digest": DIGEST,
    }


@pytest.mark.parametrize(
    "environment",
    [
        {"DTC_ENVIRONMENT": "development"},
        {"APP_VERSION": "anything"},
        {"VERSION": "dev", "SOURCE_SHA": SHA, "IMAGE_DIGEST": DIGEST},
        {
            "VERSION": "20260809-143205-3f6c227",
            "SOURCE_SHA": "a" * 40,
            "IMAGE_DIGEST": DIGEST,
        },
        {
            "VERSION": "20260809-143205-3f6c227",
            "SOURCE_SHA": SHA,
            "IMAGE_DIGEST": DIGEST,
            "APP_VERSION": "anything",
        },
    ],
)
def test_runtime_identity_rejects_unsealed_or_duplicate_identity(environment) -> None:
    with pytest.raises(RuntimeError):
        read_runtime_identity(environment)


def test_runtime_and_release_identity_reject_calendar_invalid_version() -> None:
    invalid_version = "20261340-999999-3f6c227"

    with pytest.raises(RuntimeError, match="not a sealed release version"):
        read_runtime_identity(
            {
                "DTC_ENVIRONMENT": "development",
                "VERSION": invalid_version,
                "SOURCE_SHA": SHA,
                "IMAGE_DIGEST": DIGEST,
            }
        )
    with pytest.raises(ReleaseContractError, match="sealed UTC timestamp"):
        ReleaseIdentity(SHA, DIGEST, REPOSITORY, invalid_version)


def test_schema2_record_readers_reject_calendar_invalid_version(tmp_path: Path) -> None:
    invalid_version = "20261340-999999-3f6c227"
    release_path = tmp_path / "release.json"
    release_path.write_text(
        json.dumps(
            {
                "identity_schema": 2,
                "version": invalid_version,
                "source_sha": SHA,
                "image_digest": DIGEST,
                "web_task_definition_arn": f"{TASK_PREFIX}/website-web:1",
                "worker_task_definition_arn": f"{TASK_PREFIX}/website-worker:1",
                "migration_task_definition_arn": f"{TASK_PREFIX}/website-migration:1",
                "web_desired_count": 1,
                "worker_desired_count": 1,
                "rollback_eligible": True,
            }
        )
    )
    with pytest.raises(ReleaseContractError, match="sealed UTC timestamp"):
        ReleaseRecord.read(release_path)

    active_path = tmp_path / "active.json"
    active_path.write_text(
        json.dumps(
            {
                "identity_schema": 2,
                "version": invalid_version,
                "source_sha": SHA,
                "image_digest": DIGEST,
                "web_task_definition_arn": f"{TASK_PREFIX}/website-web:1",
                "worker_task_definition_arn": f"{TASK_PREFIX}/website-worker:1",
                "web_desired_count": 1,
                "worker_desired_count": 1,
            }
        )
    )
    with pytest.raises(ReleaseContractError, match="sealed UTC timestamp"):
        ActiveServicePair.read(active_path)

    with pytest.raises(ReleaseContractError, match="sealed UTC timestamp"):
        ServiceSnapshot(
            service_name="web",
            task_definition_arn=f"{TASK_PREFIX}/website-web:1",
            desired_count=1,
            running_count=1,
            pending_count=0,
            source_sha=SHA,
            image_digest=DIGEST,
            primary_deployment_id="ecs-svc/web",
            version=invalid_version,
            identity_schema=2,
        )


def test_task_capture_and_smoke_reject_calendar_invalid_version_before_use() -> None:
    invalid_version = "20261340-999999-3f6c227"
    gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
    gateway.config = SimpleNamespace(container_names={"web": "web"})  # type: ignore[assignment]
    gateway._task_definition = Mock(  # type: ignore[method-assign]
        return_value={
            "containerDefinitions": [
                {
                    "name": "web",
                    "image": f"{REPOSITORY}@{DIGEST}",
                    "environment": [
                        {"name": "VERSION", "value": invalid_version},
                        {"name": "SOURCE_SHA", "value": SHA},
                        {"name": "IMAGE_DIGEST", "value": DIGEST},
                    ],
                }
            ]
        }
    )
    with pytest.raises(ReleaseContractError, match="sealed UTC timestamp"):
        gateway._identity(f"{TASK_PREFIX}/website-web:1", "web")

    with (
        patch("deploy.smoke._request") as request,
        pytest.raises(ReleaseContractError, match="sealed UTC timestamp"),
    ):
        verify_health("https://web.dtcdev.click", invalid_version, SHA, DIGEST)
    request.assert_not_called()


def test_schema1_health_accepts_the_exact_legacy_contract() -> None:
    responses = [
        Response(
            status=200,
            headers={"x-robots-tag": "noindex, nofollow"},
            body=json.dumps({"status": "ok", "version": SHA}).encode(),
        ),
        Response(
            status=200,
            headers={"x-robots-tag": "noindex, nofollow"},
            body=json.dumps(
                {
                    "status": "ready",
                    "checks": {
                        "configuration": {"status": "ok"},
                        "database": {"status": "ok"},
                        "migrations": {"status": "ok"},
                    },
                }
            ).encode(),
        ),
    ]

    with patch("deploy.smoke._request", side_effect=responses) as request:
        verify_legacy_health("https://web.dtcdev.click", SHA)

    assert [call.args[1] for call in request.call_args_list] == [
        "/health/live",
        "/health/ready",
    ]


@pytest.mark.parametrize(
    ("live_payload", "ready_payload", "message"),
    [
        (
            {"status": "ok", "version": "4" * 40},
            {
                "status": "ready",
                "checks": {
                    "configuration": {"status": "ok"},
                    "database": {"status": "ok"},
                    "migrations": {"status": "ok"},
                },
            },
            "exact legacy release identity",
        ),
        (
            {"status": "ok", "version": SHA, "source_sha": SHA},
            {
                "status": "ready",
                "checks": {
                    "configuration": {"status": "ok"},
                    "database": {"status": "ok"},
                    "migrations": {"status": "ok"},
                },
            },
            "exact legacy release identity",
        ),
        (
            {"status": "ok", "version": SHA},
            {
                "status": "ready",
                "source_sha": "4" * 40,
                "checks": {
                    "configuration": {"status": "ok"},
                    "database": {"status": "ok"},
                    "migrations": {"status": "ok"},
                },
            },
            "exact legacy readiness contract",
        ),
    ],
)
def test_schema1_health_rejects_wrong_or_mixed_identity_contracts(
    live_payload: dict[str, object],
    ready_payload: dict[str, object],
    message: str,
) -> None:
    responses = [
        Response(
            status=200,
            headers={"x-robots-tag": "noindex, nofollow"},
            body=json.dumps(live_payload).encode(),
        ),
        Response(
            status=200,
            headers={"x-robots-tag": "noindex, nofollow"},
            body=json.dumps(ready_payload).encode(),
        ),
    ]

    with (
        patch("deploy.smoke._request", side_effect=responses),
        pytest.raises(ReleaseContractError, match=message),
    ):
        verify_legacy_health("https://web.dtcdev.click", SHA)


def test_schema2_health_still_rejects_a_mismatched_exact_triplet() -> None:
    version = f"20260809-143205-{SHA[:7]}"
    live = Response(
        status=200,
        headers={"x-robots-tag": "noindex, nofollow"},
        body=json.dumps(
            {
                "status": "ok",
                "version": version,
                "source_sha": "4" * 40,
                "image_digest": DIGEST,
            }
        ).encode(),
    )

    with (
        patch("deploy.smoke._request", return_value=live),
        pytest.raises(ReleaseContractError, match="exact release identity"),
    ):
        verify_health("https://web.dtcdev.click", version, SHA, DIGEST)


def test_schema2_version_format_has_one_parser_implementation() -> None:
    definitions = []
    for source_root in (ROOT / "core", ROOT / "deploy"):
        for path in source_root.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if "tests" in relative.parts:
                continue
            if "VERSION_PATTERN =" in path.read_text(encoding="utf-8"):
                definitions.append(relative.as_posix())

    assert definitions == ["deploy/release_identity.py"]
    assert "validate_schema2_version" in (ROOT / "deploy/contracts.py").read_text(encoding="utf-8")
    assert "validate_schema2_version" in (ROOT / "core/runtime_identity.py").read_text(
        encoding="utf-8"
    )


def test_local_environment_example_preserves_the_exact_fallback() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "APP_VERSION=" not in example
    assert "VERSION=" not in example
    assert "SOURCE_SHA=" not in example
    assert "IMAGE_DIGEST=" not in example


def test_enabled_service_snapshot_requires_the_complete_identity() -> None:
    with pytest.raises(ReleaseContractError, match="identity is incomplete"):
        ServiceSnapshot(
            service_name="web",
            task_definition_arn=(
                "arn:aws:ecs:eu-west-1:817685572750:task-definition/website-web:1"
            ),
            desired_count=1,
            running_count=1,
            pending_count=0,
            source_sha=None,
            image_digest=None,
            primary_deployment_id="ecs-svc/web",
        )


def test_legacy_success_record_is_strictly_read_only(tmp_path: Path) -> None:
    task_prefix = "arn:aws:ecs:eu-west-1:817685572750:task-definition"
    legacy = {
        "source_sha": SHA,
        "image_digest": DIGEST,
        "web_task_definition_arn": f"{task_prefix}/website-web:1",
        "worker_task_definition_arn": f"{task_prefix}/website-worker:1",
        "migration_task_definition_arn": f"{task_prefix}/website-migration:1",
        "web_desired_count": 1,
        "worker_desired_count": 1,
        "rollback_eligible": True,
    }
    legacy_path = tmp_path / "legacy-release.json"
    legacy_path.write_text(json.dumps(legacy))

    record = ReleaseRecord.read(legacy_path)
    assert record.identity_schema == 1
    assert record.version == SHA
    with pytest.raises(ReleaseContractError, match="must use identity_schema 2"):
        record.write(tmp_path / "new-release.json")

    disguised_path = tmp_path / "disguised-release.json"
    disguised_path.write_text(json.dumps(legacy | {"identity_schema": 1, "version": SHA}))
    with pytest.raises(ReleaseContractError, match="must declare identity_schema 2"):
        ReleaseRecord.read(disguised_path)
