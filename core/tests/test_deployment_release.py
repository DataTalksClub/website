from __future__ import annotations

import argparse
import io
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase

from deploy.cli import main as release_cli_main
from deploy.contracts import (
    PLACEHOLDER_DIGEST,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
)
from deploy.release import (
    RELEASE_A_SHA,
    RELEASE_B_SHA,
    CompensationError,
    PromotionConfig,
    RecoveryContext,
    capture_current_service_pair,
    capture_recovery_context,
    promote,
    restore_after_finalization_failure,
    rollback,
)
from deploy.smoke import ROBOTS_VALUE, Response, run_http_smoke, validate_origin
from deploy.task_definitions import (
    FIXED_NONSECRET_ENVIRONMENT,
    SAFETY_ENVIRONMENT,
    TaskDefinitionConfig,
    build_task_definitions,
)

SHA_A = RELEASE_A_SHA
SHA_B = RELEASE_B_SHA
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
REPOSITORY = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"
TASK_ROLE = "arn:aws:iam::817685572750:role/website-task"
EXECUTION_ROLE = "arn:aws:iam::817685572750:role/website-execution"
FAMILIES = {name: f"website-sandbox-{name}" for name in ("web", "worker", "migration")}
CONTAINERS = {name: name for name in ("web", "worker", "migration")}
DATABASE_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/database-url-Ab12Cd"
)
DJANGO_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/django-secret-key-Ef34Gh"
)


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
                    *[
                        {"name": name, "value": value}
                        for name, value in FIXED_NONSECRET_ENVIRONMENT.items()
                    ],
                ],
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": DATABASE_SECRET_ARN,
                    },
                    {
                        "name": "DJANGO_SECRET_KEY",
                        "valueFrom": DJANGO_SECRET_ARN,
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


def active_pair(source_sha: str = SHA_A, digest: str = DIGEST_A) -> ActiveServicePair:
    return ActiveServicePair(
        source_sha=source_sha,
        image_digest=digest,
        web_task_definition_arn=arn("web", 1),
        worker_task_definition_arn=arn("worker", 1),
        web_desired_count=1,
        worker_desired_count=1,
    )


class FakeGateway:
    worker_stabilization_timeout_seconds = 420

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
        task = task_document(workload)
        task["taskDefinitionArn"] = arn(workload, 1)
        return task

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None:
        self.operations.append(f"verify-record:{record.source_sha}:{identity.image_digest}")

    def verify_active_service_pair(
        self, pair: ActiveServicePair, identity: ReleaseIdentity
    ) -> None:
        self.operations.append(f"verify-pair:{pair.source_sha}:{identity.image_digest}")

    def verify_image_digest_exists(
        self, repository_uri: str, source_sha: str, image_digest: str
    ) -> None:
        self.operations.append(f"verify-image:{repository_uri}:{source_sha}@{image_digest}")
        self._fail("verify-image")

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str:
        self.operations.append(f"register:{workload}:{','.join(sorted(tags))}")
        self._fail(f"register:{workload}")
        return arn(workload, 2)

    def run_migration(
        self, task_definition_arn: str, *, inject_controlled_failure: bool = False
    ) -> None:
        self.operations.append(
            f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
        )
        if inject_controlled_failure:
            raise ReleaseContractError("controlled migration exited nonzero (97)")
        self._fail("migration")

    def update_service(self, workload: str, task_definition_arn: str, desired_count: int) -> None:
        self.operations.append(f"update:{workload}:{task_definition_arn}:{desired_count}")
        if self.fail_once == "compensation" and task_definition_arn.endswith(":1"):
            self._fail("compensation")
        if task_definition_arn.endswith(":2"):
            self._fail(f"update:{workload}")
        self.snapshots[workload] = ServiceSnapshot(
            service_name=f"service-{workload}",
            task_definition_arn=task_definition_arn,
            desired_count=desired_count,
            running_count=desired_count,
            pending_count=0,
            source_sha=SHA_B if task_definition_arn.endswith(":2") else SHA_A,
            image_digest=DIGEST_B if task_definition_arn.endswith(":2") else DIGEST_A,
        )

    def wait_service_stable(
        self,
        workload: str,
        *,
        worker_singleton: bool = False,
        timeout_seconds: int | None = None,
    ) -> None:
        self.operations.append(
            f"wait:{workload}:singleton={worker_singleton}:timeout={timeout_seconds}"
        )
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
        del expected_desired_counts, expected_identity
        self.operations.append("terminal")
        if any(value.endswith(":2") for value in expected_task_definitions.values()):
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

        normalized = build_task_definitions(tasks, self.identity, self.config)

        for task in normalized.values():
            environment = {
                item["name"]: item["value"]
                for item in task["containerDefinitions"][0]["environment"]
            }
            self.assertEqual(environment["APP_VERSION"], SHA_B)
            self.assertEqual(environment["DATAMAILER_TRANSACTIONAL_DRY_RUN"], "1")

    def test_builder_rejects_extra_environment_duplicate_secret_names_and_wrong_arns(self) -> None:
        mutations = ("extra_environment", "duplicate_name", "wrong_arn")
        for mutation in mutations:
            tasks = {workload: task_document(workload) for workload in CONTAINERS}
            for task in tasks.values():
                container = task["containerDefinitions"][0]
                if mutation == "extra_environment":
                    container["environment"].append({"name": "UNREVIEWED", "value": "1"})
                elif mutation == "duplicate_name":
                    container["secrets"][1]["name"] = "DATABASE_URL"
                else:
                    container["secrets"][0]["valueFrom"] = (
                        "arn:aws:secretsmanager:eu-west-1:817685572750:"
                        "secret:another/database-url-Ab12Cd"
                    )
            with self.subTest(mutation=mutation), self.assertRaises(ReleaseContractError):
                build_task_definitions(tasks, self.identity, self.config)

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
        prior: ReleaseRecord | ActiveServicePair | None,
        failure_injection: str = "none",
        source_sha: str = SHA_B,
    ) -> PromotionConfig:
        return PromotionConfig(
            identity=ReleaseIdentity(source_sha, DIGEST_B, REPOSITORY),
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
            failure_injection=failure_injection,
            evidence_path=Path(directory) / "evidence.json",
            recovery_context_path=Path(directory) / "recovery.json",
        )

    def _temporary_directory(self):  # type: ignore[no-untyped-def]
        Path(".tmp").mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=".tmp")

    def test_bootstrap_migrates_then_updates_web_then_worker_and_records_success(self) -> None:
        gateway = FakeGateway(bootstrap=True)
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
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
        pre_mutation_web_capture = max(
            i for i, value in enumerate(gateway.operations) if value == "capture:web"
        )
        pre_mutation_worker_capture = max(
            i for i, value in enumerate(gateway.operations) if value == "capture:worker"
        )
        self.assertLess(migration, web)
        self.assertLess(migration, pre_mutation_web_capture)
        self.assertLess(pre_mutation_web_capture, pre_mutation_worker_capture)
        self.assertLess(pre_mutation_worker_capture, web)
        self.assertLess(web, worker)
        self.assertIn("wait:worker:singleton=True:timeout=420", gateway.operations)

    def test_bootstrap_recovery_context_discards_valid_looking_placeholder_identity(self) -> None:
        gateway = FakeGateway(bootstrap=True)
        for workload in ("web", "worker"):
            snapshot = gateway.snapshots[workload]
            gateway.snapshots[workload] = ServiceSnapshot(
                service_name=snapshot.service_name,
                task_definition_arn=snapshot.task_definition_arn,
                desired_count=0,
                running_count=0,
                pending_count=0,
                source_sha="0" * 40,
                image_digest=PLACEHOLDER_DIGEST,
            )
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=None)
            promote(gateway, config)
            recovery_path = config.recovery_context_path
            assert recovery_path is not None
            context = RecoveryContext.read(recovery_path)
        self.assertIsNone(context.source_sha)
        self.assertIsNone(context.image_digest)
        self.assertEqual((context.web_desired_count, context.worker_desired_count), (0, 0))

    def test_nonbootstrap_requires_and_validates_last_successful_record(self) -> None:
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError, "requires the last successful release record"
            ):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=None))
            wrong = successful_record(source_sha="c" * 40, digest=f"sha256:{'c' * 64}")
            with self.assertRaisesMessage(ReleaseContractError, "differ from"):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=wrong))

    def test_auto_promotion_accepts_a_verified_web_worker_pair_without_migration_state(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            result = promote(gateway, self._config(directory, prior=active_pair()))
        self.assertEqual(result.source_sha, RELEASE_B_SHA)
        self.assertTrue(any(value.startswith("verify-pair:") for value in gateway.operations))

    def test_pre_migration_snapshot_rejects_a_race_without_migration_or_update(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        original_register = gateway.register_task_definition

        def register_then_external_change(
            workload: str, task_definition: dict[str, Any], tags: dict[str, str]
        ) -> str:
            result = original_register(workload, task_definition, tags)
            if workload == "migration":
                gateway.snapshots["worker"] = ServiceSnapshot(
                    service_name="service-worker",
                    task_definition_arn=arn("worker", 2),
                    desired_count=1,
                    running_count=1,
                    pending_count=0,
                    source_sha=SHA_B,
                    image_digest=DIGEST_B,
                )
            return result

        gateway.register_task_definition = register_then_external_change  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(ReleaseContractError, "identities are mixed"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertFalse(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_post_migration_snapshot_rejects_an_active_release_race(self) -> None:
        gateway = FakeGateway(bootstrap=False)

        def migration_then_external_change(
            task_definition_arn: str, *, inject_controlled_failure: bool = False
        ) -> None:
            gateway.operations.append(
                f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
            )
            gateway.snapshots["worker"] = ServiceSnapshot(
                service_name="service-worker",
                task_definition_arn=arn("worker", 2),
                desired_count=1,
                running_count=1,
                pending_count=0,
                source_sha=SHA_B,
                image_digest=DIGEST_B,
            )

        gateway.run_migration = migration_then_external_change  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(ReleaseContractError, "identities are mixed"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertTrue(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_controlled_failure_rejects_bootstrap_unknown_and_wrong_drill_identities(self) -> None:
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires an existing prior release",
            ):
                self._config(directory, prior=None, failure_injection="migration")
            with self.assertRaisesMessage(
                ReleaseContractError, "unsupported release failure injection"
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="unreviewed",
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release B",
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="migration",
                    source_sha=RELEASE_A_SHA,
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release B",
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="migration",
                    source_sha="c" * 40,
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release A as prior",
            ):
                self._config(
                    directory,
                    prior=successful_record(source_sha="c" * 40),
                    failure_injection="migration",
                )

    def test_migration_failure_never_updates_a_service_or_records_release(self) -> None:
        gateway = FakeGateway(bootstrap=True, fail_once="migration")
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(ReleaseContractError, "injected migration"):
                promote(gateway, self._config(directory, prior=None))
            self.assertFalse((Path(directory) / "release.json").exists())
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_controlled_migration_failure_uses_b_task_and_leaves_a_unchanged(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(
                ReleaseContractError, "controlled migration exited nonzero (97)"
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="migration",
                    ),
                )
            self.assertFalse(release_path.exists())
        self.assertIn(f"migrate:{arn('migration', 2)}:injected=True", gateway.operations)
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_controlled_migration_success_still_fails_before_service_mutation(self) -> None:
        gateway = FakeGateway(bootstrap=False)

        def unexpected_success(
            task_definition_arn: str, *, inject_controlled_failure: bool = False
        ) -> None:
            gateway.operations.append(
                f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
            )

        gateway.run_migration = unexpected_success  # type: ignore[method-assign]
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled migration failure unexpectedly returned success",
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="migration",
                    ),
                )
            self.assertFalse((Path(directory) / "release.json").exists())
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_post_mutation_smoke_injection_compensates_both_services_to_a(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError, "controlled post-mutation smoke failure"
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="post_mutation_smoke",
                    ),
                )
            self.assertFalse((Path(directory) / "release.json").exists())

        worker_b_update = gateway.operations.index(f"update:worker:{arn('worker', 2)}:1")
        web_a_restore = gateway.operations.index(f"update:web:{arn('web', 1)}:1")
        self.assertLess(worker_b_update, web_a_restore)
        self.assertNotIn(f"smoke:{SHA_B}", gateway.operations)
        self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_each_postmutation_failure_compensates_both_exact_prior_services(self) -> None:
        for failure in (
            "update:web",
            "wait:web",
            "health",
            "update:worker",
            "wait:worker",
            "smoke",
            "terminal",
        ):
            with self.subTest(failure=failure), self._temporary_directory() as directory:
                gateway = FakeGateway(bootstrap=False, fail_once=failure)
                with self.assertRaises(ReleaseContractError):
                    promote(gateway, self._config(directory, prior=successful_record()))
                self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
                self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
                self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
                self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))
                self.assertFalse((Path(directory) / "release.json").exists())

    def test_worker_timeout_uses_extended_forward_budget_and_default_compensation_budget(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:worker")
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(ReleaseContractError, "injected wait:worker"):
                promote(gateway, self._config(directory, prior=successful_record()))
            self.assertFalse(release_path.exists())

        forward_wait = "wait:worker:singleton=True:timeout=420"
        recovery_wait = "wait:worker:singleton=True:timeout=None"
        self.assertIn(forward_wait, gateway.operations)
        self.assertIn(recovery_wait, gateway.operations)
        self.assertLess(
            gateway.operations.index(forward_wait),
            gateway.operations.index(recovery_wait),
        )
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_release_record_write_failure_compensates_and_records_actual_stage_evidence(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with (
                patch.object(ReleaseRecord, "write", side_effect=OSError("sentinel-secret")),
                self.assertRaises(OSError),
            ):
                promote(gateway, config)
            self.assertFalse(config.release_record_path.exists())
            evidence = config.evidence_path
            assert evidence is not None
            records = __import__("json").loads(evidence.read_text())["stages"]
            self.assertIn(
                ("release_record", "failed"),
                {(item["stage"], item["result"]) for item in records},
            )
            self.assertIn(
                ("compensation", "passed"),
                {(item["stage"], item["result"]) for item in records},
            )
            self.assertNotIn("sentinel-secret", evidence.read_text())
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_manual_prior_image_tag_and_digest_are_proven_before_registration(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="verify-image")
        with self._temporary_directory() as directory, self.assertRaises(ReleaseContractError):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertFalse(any(item.startswith("register:") for item in gateway.operations))
        self.assertFalse(any(item.startswith("migrate:") for item in gateway.operations))
        self.assertFalse(any(item.startswith("update:") for item in gateway.operations))

    def test_compensation_failure_is_loud_and_contains_only_recovery_identifiers(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original_wait = gateway.wait_service_stable

        def fail_then_break_compensation(
            workload: str,
            *,
            worker_singleton: bool = False,
            timeout_seconds: int | None = None,
        ) -> None:
            try:
                original_wait(
                    workload,
                    worker_singleton=worker_singleton,
                    timeout_seconds=timeout_seconds,
                )
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

    def test_compensation_never_leaks_raw_exception_messages(self) -> None:
        def instrument_secret_failure(gateway: FakeGateway, selected_failure: str) -> None:
            restoring = {"value": False}
            original_update = gateway.update_service
            original_wait = gateway.wait_service_stable
            original_terminal = gateway.verify_terminal
            original_health = gateway.verify_public_web

            def update_with_secret_failure(
                workload: str, task_definition_arn: str, desired_count: int
            ) -> None:
                if task_definition_arn.endswith(":1"):
                    restoring["value"] = True
                    if selected_failure == "update":
                        raise RuntimeError("sentinel-secret-must-not-leak")
                original_update(workload, task_definition_arn, desired_count)

            def wait_with_secret_failure(
                workload: str,
                *,
                worker_singleton: bool = False,
                timeout_seconds: int | None = None,
            ) -> None:
                if restoring["value"] and selected_failure == "wait":
                    raise RuntimeError("sentinel-secret-must-not-leak")
                original_wait(
                    workload,
                    worker_singleton=worker_singleton,
                    timeout_seconds=timeout_seconds,
                )

            def terminal_with_secret_failure(*args, **kwargs):  # type: ignore[no-untyped-def]
                if restoring["value"] and selected_failure == "terminal":
                    raise RuntimeError("sentinel-secret-must-not-leak")
                return original_terminal(*args, **kwargs)

            def health_with_secret_failure(source_sha: str) -> None:
                if restoring["value"] and source_sha == SHA_A and selected_failure == "health":
                    raise RuntimeError("sentinel-secret-must-not-leak")
                original_health(source_sha)

            gateway.update_service = update_with_secret_failure  # type: ignore[method-assign]
            gateway.wait_service_stable = wait_with_secret_failure  # type: ignore[method-assign]
            gateway.verify_terminal = terminal_with_secret_failure  # type: ignore[method-assign]
            gateway.verify_public_web = health_with_secret_failure  # type: ignore[method-assign]

        for failure_point in ("update", "wait", "terminal", "health"):
            gateway = FakeGateway(bootstrap=False, fail_once="smoke")
            instrument_secret_failure(gateway, failure_point)
            with (
                self.subTest(failure_point=failure_point),
                self._temporary_directory() as directory,
                self.assertRaises(CompensationError) as caught,
            ):
                promote(gateway, self._config(directory, prior=successful_record()))
            self.assertNotIn("sentinel-secret-must-not-leak", str(caught.exception))
            self.assertIn("RuntimeError", str(caught.exception))

            parser = argparse.ArgumentParser()
            parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
                handler=lambda _arguments: (_ for _ in ()).throw(caught.exception),
                command="promote",
            )
            stderr = io.StringIO()
            with (
                patch("deploy.cli.build_parser", return_value=parser),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                release_cli_main()
            self.assertNotIn("sentinel-secret-must-not-leak", stderr.getvalue())
            self.assertIn("RuntimeError", stderr.getvalue())

    def test_compensation_runs_when_its_evidence_marker_cannot_be_written(self) -> None:
        import deploy.release as release_module

        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original = release_module._record_evidence

        def fail_recovery_marker(path, stage, result, proof=None):  # type: ignore[no-untyped-def]
            if stage == "compensation":
                raise OSError("sentinel-secret-must-not-leak")
            return original(path, stage, result, proof)

        with (
            self._temporary_directory() as directory,
            patch("deploy.release._record_evidence", side_effect=fail_recovery_marker),
            self.assertRaisesMessage(ReleaseContractError, "injected wait:web"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
        self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_auto_deploy_capture_synthesizes_only_a_stable_managed_release(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            path = Path(directory) / "captured.json"
            pair = capture_current_service_pair(
                gateway,
                REPOSITORY,
                path,
                expected_web_count=1,
                expected_worker_count=1,
            )

            self.assertEqual(ActiveServicePair.read(path), pair)
        self.assertEqual(pair.source_sha, RELEASE_A_SHA)
        self.assertFalse(hasattr(pair, "migration_task_definition_arn"))
        self.assertIn(f"verify-pair:{RELEASE_A_SHA}:{DIGEST_A}", gateway.operations)
        self.assertIn("terminal", gateway.operations)

    def test_auto_deploy_capture_rejects_bootstrap_unstable_and_mixed_state(self) -> None:
        bootstrap = FakeGateway(bootstrap=True)
        unstable = FakeGateway(bootstrap=False)
        unstable.snapshots["web"] = ServiceSnapshot(
            service_name="service-web",
            task_definition_arn=arn("web", 1),
            desired_count=1,
            running_count=0,
            pending_count=1,
            source_sha=SHA_A,
            image_digest=DIGEST_A,
        )
        mixed = FakeGateway(bootstrap=False)
        mixed.snapshots["worker"] = ServiceSnapshot(
            service_name="service-worker",
            task_definition_arn=arn("worker", 1),
            desired_count=1,
            running_count=1,
            pending_count=0,
            source_sha="c" * 40,
            image_digest=DIGEST_A,
        )

        cases = (
            (bootstrap, "bootstrap-disabled"),
            (unstable, "stable web"),
            (mixed, "mixed or missing"),
        )
        for gateway, message in cases:
            with (
                self.subTest(message=message),
                self._temporary_directory() as directory,
                self.assertRaisesMessage(ReleaseContractError, message),
            ):
                capture_current_service_pair(
                    gateway,
                    REPOSITORY,
                    Path(directory) / "captured.json",
                    expected_web_count=1,
                    expected_worker_count=1,
                )
            self.assertFalse(any(value.startswith("register:") for value in gateway.operations))
            self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_enabled_recovery_checkpoint_requires_an_accepted_prior(self) -> None:
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(
                ReleaseContractError,
                "enabled recovery checkpoint requires the accepted prior release",
            ),
        ):
            capture_recovery_context(
                FakeGateway(bootstrap=False),
                REPOSITORY,
                Path(directory) / "checkpoint.json",
                None,
            )

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
        self.assertIn("wait:worker:singleton=True:timeout=420", gateway.operations)

    def test_each_rollback_failure_compensates_to_current_exact_pair(self) -> None:
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
        for failure in (
            "update:web",
            "wait:web",
            "health",
            "update:worker",
            "wait:worker",
            "smoke",
            "terminal",
        ):
            with self.subTest(failure=failure), self._temporary_directory() as directory:
                gateway = FakeGateway(bootstrap=False, fail_once=failure)
                with self.assertRaises(ReleaseContractError):
                    rollback(
                        gateway,
                        target,
                        current,
                        REPOSITORY,
                        Path(directory) / "release.json",
                    )
                self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
                self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_finalization_recovery_restores_enabled_and_bootstrap_pairs(self) -> None:
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
        )
        contexts = (
            RecoveryContext(REPOSITORY, SHA_A, DIGEST_A, arn("web", 1), arn("worker", 1), 1, 1),
            RecoveryContext(REPOSITORY, None, None, arn("web", 1), arn("worker", 1), 0, 0),
        )
        for context in contexts:
            gateway = FakeGateway(bootstrap=False)
            gateway.update_service("web", arn("web", 2), 1)
            gateway.update_service("worker", arn("worker", 2), 1)
            with self.subTest(count=context.web_desired_count):
                restore_after_finalization_failure(gateway, context, failed)
                self.assertEqual(
                    gateway.snapshots["web"].task_definition_arn,
                    context.web_task_definition_arn,
                )
                self.assertEqual(
                    gateway.snapshots["worker"].desired_count,
                    context.worker_desired_count,
                )

    def test_finalization_recovery_rejects_a_stale_live_pair_before_update(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        context = RecoveryContext(
            REPOSITORY,
            SHA_A,
            DIGEST_A,
            arn("web", 1),
            arn("worker", 1),
            1,
            1,
        )
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
        )
        with self.assertRaisesMessage(ReleaseContractError, "differ from"):
            restore_after_finalization_failure(gateway, context, failed)
        self.assertFalse(any(operation.startswith("update:") for operation in gateway.operations))

    def test_cli_generic_exception_prints_only_the_exception_class(self) -> None:
        parser = argparse.ArgumentParser()
        parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
            handler=lambda _arguments: (_ for _ in ()).throw(
                RuntimeError("sentinel-secret-must-not-leak")
            ),
            command="promote",
        )
        stderr = io.StringIO()
        with (
            patch("deploy.cli.build_parser", return_value=parser),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            release_cli_main()
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("sentinel-secret-must-not-leak", stderr.getvalue())


class RemoteSmokeSafetyTests(SimpleTestCase):
    def test_remote_smoke_is_restricted_to_the_exact_sandbox_origin(self) -> None:
        self.assertEqual(validate_origin("https://web.dtcdev.click/"), "https://web.dtcdev.click")
        for origin in ("http://web.dtcdev.click", "https://example.com"):
            with self.subTest(origin=origin), self.assertRaises(ReleaseContractError):
                validate_origin(origin)

    def test_http_smoke_checks_safe_404_and_writes_only_redacted_evidence(self) -> None:
        noindex = {"x-robots-tag": ROBOTS_VALUE}
        private = noindex | {"cache-control": "private, no-store"}
        responses = [
            Response(200, noindex, f'{{"status":"ok","version":"{SHA_A}"}}'.encode()),
            Response(
                200,
                noindex,
                b'{"status":"ready","checks":{"configuration":{"status":"ok"},'
                b'"database":{"status":"ok"},"migrations":{"status":"ok"}}}',
            ),
            Response(
                200,
                noindex,
                b"Learn data skills. For free. Together."
                b'<link rel="stylesheet" href="/static/courses.fixture.css">',
            ),
            Response(
                200,
                noindex,
                b'Foundation page<link rel="canonical" href="https://datatalks.club/">',
            ),
            Response(
                302,
                private | {"location": "/accounts/login/?next=%2Fstudio%2F"},
                b"",
            ),
            Response(200, private, b"Sign In"),
            Response(
                401,
                private
                | {
                    "www-authenticate": "Bearer",
                    "x-request-id": "request-smoke",
                },
                b'{"error":{"code":"authentication_required",'
                b'"message":"Valid Bearer authentication is required.",'
                b'"request_id":"request-smoke"}}',
            ),
            Response(404, noindex, b"Page not found"),
            Response(
                200,
                noindex | {"content-type": "text/plain; charset=utf-8"},
                b"User-agent: *\nDisallow: /\n",
            ),
            Response(
                200,
                noindex | {"content-type": "application/xml; charset=utf-8"},
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n',
            ),
            Response(200, noindex | {"content-type": "text/css"}, b"body{}"),
        ]
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory) / "http-evidence.json"
            with patch("deploy.smoke._request", side_effect=responses) as request:
                evidence = run_http_smoke("https://web.dtcdev.click", SHA_A, path)
            self.assertEqual(
                [call.args[1] for call in request.call_args_list][-4:],
                [
                    "/__dtc_deployed_smoke_missing__",
                    "/robots.txt",
                    "/sitemap.xml",
                    "/static/courses.fixture.css",
                ],
            )
            persisted = path.read_text()
            self.assertEqual(__import__("json").loads(persisted), evidence)
            self.assertEqual(evidence["checks"][-1]["runtime_group"], "analytics")
            for forbidden in ("cookie", "authorization", "set-cookie", "response_body"):
                self.assertNotIn(forbidden, persisted.lower())

        invalid_admin_responses = (
            (
                Response(
                    401,
                    private | {"x-request-id": "request-smoke"},
                    b'{"error":{"code":"authentication_required",'
                    b'"message":"Valid Bearer authentication is required.",'
                    b'"request_id":"request-smoke"}}',
                ),
                "lacks the Bearer challenge",
            ),
            (
                Response(
                    401,
                    private
                    | {
                        "www-authenticate": "Bearer",
                        "x-request-id": "request-smoke",
                    },
                    b'{"error":{"code":"authentication_required",'
                    b'"message":"Valid Bearer authentication is required.",'
                    b'"request_id":"different-request"}}',
                ),
                "payload differs",
            ),
        )
        for admin_response, error_message in invalid_admin_responses:
            with self.subTest(error_message=error_message):
                invalid_responses = [*responses]
                invalid_responses[6] = admin_response
                with (
                    patch("deploy.smoke._request", side_effect=invalid_responses),
                    self.assertRaisesMessage(ReleaseContractError, error_message),
                ):
                    run_http_smoke("https://web.dtcdev.click", SHA_A)
