from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from django.test import SimpleTestCase

from deploy.contracts import (
    PLACEHOLDER_DIGEST,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
)
from deploy.release import CompensationError, PromotionConfig, promote, rollback
from deploy.smoke import validate_origin
from deploy.task_definitions import (
    SAFETY_ENVIRONMENT,
    TaskDefinitionConfig,
    build_task_definitions,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
REPOSITORY = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"
TASK_ROLE = "arn:aws:iam::817685572750:role/website-task"
EXECUTION_ROLE = "arn:aws:iam::817685572750:role/website-execution"
FAMILIES = {name: f"website-sandbox-{name}" for name in ("web", "worker", "migration")}
CONTAINERS = {name: name for name in ("web", "worker", "migration")}


def arn(workload: str, revision: int) -> str:
    return f"arn:aws:ecs:eu-west-1:817685572750:task-definition/{FAMILIES[workload]}:{revision}"


def task_document(workload: str, source_sha: str = "bootstrap-disabled") -> dict[str, Any]:
    return {
        "family": FAMILIES[workload],
        "taskRoleArn": TASK_ROLE,
        "executionRoleArn": EXECUTION_ROLE,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [
            {
                "name": CONTAINERS[workload],
                "image": f"{REPOSITORY}@{PLACEHOLDER_DIGEST}",
                "essential": True,
                "environment": [
                    {"name": "APP_VERSION", "value": source_sha},
                    {"name": "DTC_ENVIRONMENT", "value": "sandbox"},
                ],
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": "arn:aws:secretsmanager:eu-west-1:817685572750:secret:db",
                    },
                    {
                        "name": "DJANGO_SECRET_KEY",
                        "valueFrom": "arn:aws:secretsmanager:eu-west-1:817685572750:secret:key",
                    },
                ],
            }
        ],
    }


def successful_record(source_sha: str = SHA_A, digest: str = DIGEST_A) -> ReleaseRecord:
    return ReleaseRecord(
        source_sha=source_sha,
        image_digest=digest,
        web_task_definition_arn=arn("web", 1),
        worker_task_definition_arn=arn("worker", 1),
        migration_task_definition_arn=arn("migration", 1),
        web_desired_count=1,
        worker_desired_count=1,
        rollback_eligible=True,
    )


class FakeGateway:
    def __init__(self, *, bootstrap: bool, fail_once: str | None = None) -> None:
        desired = 0 if bootstrap else 1
        source = None if bootstrap else SHA_A
        digest = PLACEHOLDER_DIGEST if bootstrap else DIGEST_A
        self.snapshots = {
            workload: ServiceSnapshot(
                service_name=f"service-{workload}",
                task_definition_arn=arn(workload, 1),
                desired_count=desired,
                running_count=desired,
                pending_count=0,
                source_sha=source,
                image_digest=digest,
            )
            for workload in ("web", "worker")
        }
        self.fail_once = fail_once
        self.operations: list[str] = []

    def _fail(self, point: str) -> None:
        if self.fail_once == point:
            self.fail_once = None
            raise ReleaseContractError(f"injected {point}")

    def capture_service(self, workload: str) -> ServiceSnapshot:
        self.operations.append(f"capture:{workload}")
        return self.snapshots[workload]

    def source_task_definition(self, workload: str) -> dict[str, Any]:
        self.operations.append(f"source:{workload}")
        return task_document(workload)

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None:
        self.operations.append(f"verify-record:{record.source_sha}:{identity.image_digest}")

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str:
        self.operations.append(f"register:{workload}:{','.join(sorted(tags))}")
        self._fail(f"register:{workload}")
        return arn(workload, 2)

    def run_migration(self, task_definition_arn: str) -> None:
        self.operations.append(f"migrate:{task_definition_arn}")
        self._fail("migration")

    def update_service(self, workload: str, task_definition_arn: str, desired_count: int) -> None:
        self.operations.append(f"update:{workload}:{task_definition_arn}:{desired_count}")
        if self.fail_once == "compensation" and task_definition_arn.endswith(":1"):
            self._fail("compensation")
        self.snapshots[workload] = ServiceSnapshot(
            service_name=f"service-{workload}",
            task_definition_arn=task_definition_arn,
            desired_count=desired_count,
            running_count=desired_count,
            pending_count=0,
            source_sha=SHA_B if task_definition_arn.endswith(":2") else SHA_A,
            image_digest=DIGEST_B if task_definition_arn.endswith(":2") else DIGEST_A,
        )

    def wait_service_stable(self, workload: str, *, worker_singleton: bool = False) -> None:
        self.operations.append(f"wait:{workload}:singleton={worker_singleton}")
        self._fail(f"wait:{workload}")

    def verify_public_web(self, source_sha: str) -> None:
        self.operations.append(f"health:{source_sha}")
        self._fail("health")

    def run_deployed_smoke(self, source_sha: str) -> None:
        self.operations.append(f"smoke:{source_sha}")
        self._fail("smoke")

    def verify_terminal(
        self,
        expected_task_definitions: dict[str, str],
        expected_desired_counts: dict[str, int],
        expected_identity: ReleaseIdentity | None,
    ) -> None:
        del expected_task_definitions, expected_desired_counts, expected_identity
        self.operations.append("terminal")
        self._fail("terminal")


class TaskDefinitionBuilderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.config = TaskDefinitionConfig(
            families=FAMILIES,
            container_names=CONTAINERS,
            task_role_arn=TASK_ROLE,
            execution_role_arn=EXECUTION_ROLE,
        )
        self.identity = ReleaseIdentity(SHA_B, DIGEST_B, REPOSITORY)

    def test_builder_normalizes_all_workloads_from_one_contract(self) -> None:
        tasks = build_task_definitions(
            {workload: task_document(workload) for workload in CONTAINERS},
            self.identity,
            self.config,
        )

        environments = []
        secrets = []
        for workload, task in tasks.items():
            container = task["containerDefinitions"][0]
            self.assertEqual(container["image"], f"{REPOSITORY}@{DIGEST_B}")
            self.assertEqual(container["user"], "10001:10001")
            environment = {item["name"]: item["value"] for item in container["environment"]}
            self.assertEqual(environment["APP_VERSION"], SHA_B)
            self.assertLessEqual(SAFETY_ENVIRONMENT.items(), environment.items())
            environments.append(environment)
            secrets.append(container["secrets"])
            if workload == "migration":
                self.assertEqual(
                    container["entryPoint"],
                    ["uv", "run", "--no-sync", "python", "manage.py"],
                )
                self.assertEqual(container["command"], ["migrate", "--noinput"])
            else:
                self.assertNotIn("entryPoint", container)
                self.assertEqual(container["command"], [workload])
        self.assertEqual(environments[0], environments[1])
        self.assertEqual(environments[1], environments[2])
        self.assertEqual(secrets[0], secrets[1])
        self.assertEqual(secrets[1], secrets[2])

    def test_builder_rejects_role_environment_and_secret_mismatches(self) -> None:
        mutations = ("role", "environment", "secret")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                tasks = {workload: task_document(workload) for workload in CONTAINERS}
                worker = tasks["worker"]
                if mutation == "role":
                    worker["taskRoleArn"] = f"{TASK_ROLE}-other"
                elif mutation == "environment":
                    worker["containerDefinitions"][0]["environment"].append(
                        {"name": "UNSAFE_DIFFERENCE", "value": "1"}
                    )
                else:
                    worker["containerDefinitions"][0]["secrets"][0]["valueFrom"] += "-other"
                with self.assertRaises(ReleaseContractError):
                    build_task_definitions(tasks, self.identity, self.config)

    def test_builder_replaces_prior_release_metadata_before_comparing_sources(self) -> None:
        tasks = {workload: task_document(workload) for workload in CONTAINERS}
        for index, workload in enumerate(CONTAINERS):
            container = tasks[workload]["containerDefinitions"][0]
            container["environment"][0]["value"] = f"{index:x}" * 40
            container["environment"].append(
                {"name": "DATAMAILER_TRANSACTIONAL_DRY_RUN", "value": str(index)}
            )

        normalized = build_task_definitions(tasks, self.identity, self.config)

        for task in normalized.values():
            environment = {
                item["name"]: item["value"]
                for item in task["containerDefinitions"][0]["environment"]
            }
            self.assertEqual(environment["APP_VERSION"], SHA_B)
            self.assertEqual(environment["DATAMAILER_TRANSACTIONAL_DRY_RUN"], "1")

    def test_release_identity_rejects_short_sha_mutable_digest_and_placeholder(self) -> None:
        for source, digest in (
            ("abc123", DIGEST_B),
            (SHA_B, "latest"),
            (SHA_B, PLACEHOLDER_DIGEST),
        ):
            with (
                self.subTest(source=source, digest=digest),
                self.assertRaises(ReleaseContractError),
            ):
                ReleaseIdentity(source, digest, REPOSITORY)


class PromotionTests(SimpleTestCase):
    def _config(
        self,
        directory: str,
        *,
        prior: ReleaseRecord | None,
    ) -> PromotionConfig:
        return PromotionConfig(
            identity=ReleaseIdentity(SHA_B, DIGEST_B, REPOSITORY),
            task_definitions=TaskDefinitionConfig(
                families=FAMILIES,
                container_names=CONTAINERS,
                task_role_arn=TASK_ROLE,
                execution_role_arn=EXECUTION_ROLE,
            ),
            web_desired_count=1,
            worker_desired_count=1,
            project_tag="website",
            environment_tag="sandbox",
            release_record_path=Path(directory) / "release.json",
            expected_prior_release=prior,
        )

    def _temporary_directory(self):  # type: ignore[no-untyped-def]
        Path(".tmp").mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=".tmp")

    def test_bootstrap_migrates_then_updates_web_then_worker_and_records_success(self) -> None:
        gateway = FakeGateway(bootstrap=True)
        with self._temporary_directory() as directory:
            record = promote(gateway, self._config(directory, prior=None))
            self.assertEqual(ReleaseRecord.read(Path(directory) / "release.json"), record)
        migration = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("migrate:")
        )
        web = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("update:web:")
        )
        worker = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("update:worker:")
        )
        self.assertLess(migration, web)
        self.assertLess(web, worker)
        self.assertIn("wait:worker:singleton=True", gateway.operations)

    def test_nonbootstrap_requires_and_validates_last_successful_record(self) -> None:
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError, "requires the last successful release record"
            ):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=None))
            wrong = successful_record(source_sha="c" * 40, digest=f"sha256:{'c' * 64}")
            with self.assertRaisesMessage(ReleaseContractError, "differ from"):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=wrong))

    def test_migration_failure_never_updates_a_service_or_records_release(self) -> None:
        gateway = FakeGateway(bootstrap=True, fail_once="migration")
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(ReleaseContractError, "injected migration"):
                promote(gateway, self._config(directory, prior=None))
            self.assertFalse((Path(directory) / "release.json").exists())
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_each_postmutation_failure_compensates_both_exact_prior_services(self) -> None:
        for failure in ("wait:web", "health", "wait:worker", "smoke", "terminal"):
            with self.subTest(failure=failure), self._temporary_directory() as directory:
                gateway = FakeGateway(bootstrap=False, fail_once=failure)
                with self.assertRaises(ReleaseContractError):
                    promote(gateway, self._config(directory, prior=successful_record()))
                self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
                self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
                self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
                self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))
                self.assertFalse((Path(directory) / "release.json").exists())

    def test_compensation_failure_is_loud_and_contains_only_recovery_identifiers(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original_wait = gateway.wait_service_stable

        def fail_then_break_compensation(workload: str, *, worker_singleton: bool = False) -> None:
            try:
                original_wait(workload, worker_singleton=worker_singleton)
            except ReleaseContractError:
                gateway.fail_once = "compensation"
                raise

        gateway.wait_service_stable = fail_then_break_compensation  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaises(CompensationError) as caught,
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertIn(arn("web", 1), str(caught.exception))
        self.assertIn(arn("worker", 1), str(caught.exception))

    def test_manual_rollback_uses_no_migration_and_compensates_on_failure(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        current = successful_record()
        target = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
        )
        with self._temporary_directory() as directory:
            rollback(
                gateway,
                target,
                current,
                REPOSITORY,
                Path(directory) / "rolled-back.json",
            )
        self.assertFalse(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertLess(
            gateway.operations.index(f"update:web:{arn('web', 2)}:1"),
            gateway.operations.index(f"update:worker:{arn('worker', 2)}:1"),
        )


class RemoteSmokeSafetyTests(SimpleTestCase):
    def test_remote_smoke_is_restricted_to_the_exact_sandbox_origin(self) -> None:
        self.assertEqual(validate_origin("https://web.dtcdev.click/"), "https://web.dtcdev.click")
        for origin in ("http://web.dtcdev.click", "https://example.com"):
            with self.subTest(origin=origin), self.assertRaises(ReleaseContractError):
                validate_origin(origin)
