from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from deploy.aws_gateway import AwsReleaseConfig, AwsReleaseGateway
from deploy.contracts import ReleaseContractError

ROOT = Path(__file__).resolve().parents[2]


class DeploymentWorkflowContractTests(SimpleTestCase):
    def test_workflow_is_serialized_main_only_and_disabled_without_explicit_dispatch(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        self.assertNotIn("pull_request", workflow)
        self.assertIn("group: website-sandbox-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("deploy_sandbox:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertEqual(workflow.count("id-token: write"), 2)
        self.assertIn("name: sandbox", workflow)

    def test_source_selection_is_exact_reachable_and_separate_from_controller(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        self.assertIn("^[0-9a-f]{40}$", workflow)
        self.assertIn('git cat-file -e "${candidate}^{commit}"', workflow)
        self.assertIn("git merge-base --is-ancestor", workflow)
        self.assertGreaterEqual(
            workflow.count("ref: ${{ needs.resolve-release.outputs.release_sha }}"), 4
        )
        self.assertGreaterEqual(workflow.count("path: .tmp/release-source"), 4)
        deploy_section = workflow.split("\n  deploy:\n", maxsplit=1)[1]
        self.assertIn("- uses: actions/checkout@v4", deploy_section)
        self.assertNotIn("path: .tmp/release-source", deploy_section)

    def test_one_image_is_built_tested_and_published_only_after_all_gates(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        self.assertEqual(workflow.count("docker buildx build"), 1)
        self.assertIn("--platform linux/amd64", workflow)
        self.assertIn("org.opencontainers.image.revision=$RELEASE_SHA", workflow)
        self.assertIn("10001:10001", workflow)
        self.assertIn("needs: [resolve-release, quality, django, playwright, container]", workflow)
        self.assertIn("ImageNotFoundException", workflow)
        self.assertIn("Rollback target image is missing; a rollback never republishes it", workflow)
        self.assertNotIn("terraform apply", workflow)

    def test_serving_entrypoint_never_runs_migrations(self) -> None:
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertNotIn("migrate", entrypoint)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("org.opencontainers.image.revision", dockerfile)


class FakeMigrationEcs:
    def __init__(self, run_response, task_response) -> None:  # type: ignore[no-untyped-def]
        self.run_response = run_response
        self.task_response = task_response
        self.stopped: list[str] = []

    def run_task(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return self.run_response

    def describe_tasks(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return self.task_response

    def stop_task(self, **kwargs):  # type: ignore[no-untyped-def]
        self.stopped.append(kwargs["task"])
        return {}


class MigrationTaskContractTests(SimpleTestCase):
    def gateway(self, ecs: FakeMigrationEcs, *, timeout: int = 1) -> AwsReleaseGateway:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = AwsReleaseConfig(
            region="eu-west-1",
            cluster_arn="arn:aws:ecs:eu-west-1:817685572750:cluster/website",
            web_target_group_arn=(
                "arn:aws:elasticloadbalancing:eu-west-1:817685572750:targetgroup/web/abc"
            ),
            service_names={"web": "web", "worker": "worker"},
            task_families={"web": "web", "worker": "worker", "migration": "migration"},
            container_names={"web": "web", "worker": "worker", "migration": "migration"},
            task_role_arn="arn:aws:iam::817685572750:role/task",
            execution_role_arn="arn:aws:iam::817685572750:role/execution",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            assign_public_ip=True,
            base_url="https://web.dtcdev.click",
            screenshot_directory=Path(".tmp/deployed-smoke"),
            timeout_seconds=timeout,
            poll_seconds=1,
        )
        gateway.ecs = ecs
        return gateway

    def test_launch_failure_missing_exit_and_nonzero_exit_fail_closed(self) -> None:
        cases = (
            (
                FakeMigrationEcs({"failures": [{"reason": "denied"}], "tasks": []}, {}),
                "launch failed",
            ),
            (
                FakeMigrationEcs(
                    {"failures": [], "tasks": [{"taskArn": "task-1"}]},
                    {
                        "failures": [],
                        "tasks": [
                            {
                                "lastStatus": "STOPPED",
                                "containers": [{"name": "migration"}],
                            }
                        ],
                    },
                ),
                "without an essential exit code",
            ),
            (
                FakeMigrationEcs(
                    {"failures": [], "tasks": [{"taskArn": "task-1"}]},
                    {
                        "failures": [],
                        "tasks": [
                            {
                                "lastStatus": "STOPPED",
                                "containers": [{"name": "migration", "exitCode": 2}],
                            }
                        ],
                    },
                ),
                "exited nonzero (2)",
            ),
        )
        for ecs, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesMessage(ReleaseContractError, message),
            ):
                self.gateway(ecs).run_migration("migration:2")

    def test_timeout_stops_the_exact_task_and_missing_task_fails(self) -> None:
        timeout_ecs = FakeMigrationEcs(
            {"failures": [], "tasks": [{"taskArn": "task-timeout"}]},
            {"failures": [], "tasks": [{"lastStatus": "RUNNING", "containers": []}]},
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=[0, 2]),
            self.assertRaisesMessage(ReleaseContractError, "timed out and was stopped"),
        ):
            self.gateway(timeout_ecs).run_migration("migration:2")
        self.assertEqual(timeout_ecs.stopped, ["task-timeout"])

        missing_ecs = FakeMigrationEcs(
            {"failures": [], "tasks": [{"taskArn": "task-missing"}]},
            {"failures": [{"reason": "missing"}], "tasks": []},
        )
        with self.assertRaisesMessage(ReleaseContractError, "disappeared"):
            self.gateway(missing_ecs).run_migration("migration:2")

    def test_zero_exit_is_the_only_success(self) -> None:
        ecs = FakeMigrationEcs(
            {"failures": [], "tasks": [{"taskArn": "task-ok"}]},
            {
                "failures": [],
                "tasks": [
                    {
                        "lastStatus": "STOPPED",
                        "containers": [{"name": "migration", "exitCode": 0}],
                    }
                ],
            },
        )
        self.gateway(ecs).run_migration("migration:2")
        self.assertEqual(ecs.stopped, [])

    def test_public_health_polls_until_exact_readiness_or_timeout(self) -> None:
        gateway = self.gateway(FakeMigrationEcs({}, {}))
        gateway._service = Mock(return_value={"desiredCount": 1})  # type: ignore[method-assign]
        gateway.elbv2 = Mock()
        gateway.elbv2.describe_target_health.return_value = {
            "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]
        }
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=[0, 0, 0, 0]),
            patch("deploy.aws_gateway.time.sleep"),
            patch(
                "deploy.aws_gateway.verify_health",
                side_effect=[ReleaseContractError("not ready"), None],
            ) as health,
        ):
            gateway.verify_public_web("a" * 40)
        self.assertEqual(health.call_count, 2)

        gateway.elbv2.describe_target_health.return_value = {
            "TargetHealthDescriptions": [{"TargetHealth": {"State": "initial"}}]
        }
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=[0, 0, 2]),
            patch("deploy.aws_gateway.time.sleep"),
            self.assertRaisesMessage(ReleaseContractError, "ALB target readiness"),
        ):
            gateway.verify_public_web("a" * 40)
