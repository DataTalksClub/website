from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

from deploy.contracts import ReleaseContractError
from deploy.deployment_targets import registered_target
from deploy.update_task_definition_image import update_task_definition

ROOT = Path(__file__).resolve().parents[2]


class CmpStyleDeploymentWorkflowTests(TestCase):
    def test_dev_and_prod_use_only_the_cluster_variable(self) -> None:
        workflows = "\n".join(
            (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for name in ("deploy-dev.yml", "deploy-prod.yml")
        )
        self.assertEqual(workflows.count("vars.ECS_CLUSTER_NAME"), 2)
        self.assertNotIn("DEV_SUBNET_IDS", workflows)
        self.assertNotIn("DEV_SECURITY_GROUP_IDS", workflows)
        self.assertNotIn("vars.DEVELOPMENT_", workflows)

    def test_push_deploys_dev_and_production_is_dispatch_only(self) -> None:
        dev = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")
        prod = (ROOT / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [main]", dev)
        self.assertIn("name: development", dev)
        self.assertIn("https://dev.datatalks.club", dev)
        self.assertIn("Test the deployment contract", dev)
        self.assertIn("--platform linux/arm64", dev)
        self.assertNotIn("push:", prod)
        self.assertIn("confirm_production", prod)
        self.assertIn("name: production", prod)
        self.assertIn("https://prod.datatalks.club", prod)
        self.assertIn("dev-release-*", prod)

    def test_legacy_ci_cannot_deploy_on_push(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        capture_job = workflow[workflow.index("  auto-capture-prior:") :]
        self.assertIn("    if: ${{ false }}", capture_job[:500])

    def test_runtime_network_is_discovered_from_the_service(self) -> None:
        script = (ROOT / "deploy" / "deploy_website.sh").read_text(encoding="utf-8")
        self.assertIn("aws ecs describe-services", script)
        self.assertIn(".services[0].networkConfiguration", script)
        self.assertIn("Running migrations and loading required code-owned data", script)
        self.assertIn('["prepare_deployment"]', script)
        self.assertLess(script.index("aws ecs run-task"), script.index("aws ecs update-service"))
        self.assertIn("aws ecs wait services-stable", script)
        # REL-06 pins the promoted-release verification to three bounded curls
        # inside one retry loop. The script wraps them across lines, so match
        # against a whitespace-normalized copy with continuations flattened.
        flattened = " ".join(script.replace("\\\n", " ").split())
        self.assertIn(
            'curl --fail --silent --show-error --connect-timeout "$HEALTH_CONNECT_TIMEOUT"'
            ' --max-time "$HEALTH_MAX_TIME" "${BASE_URL}/api/health/"'
            ' --output "$WORKDIR/health.json"',
            flattened,
        )
        self.assertIn(
            'curl --fail --silent --show-error --connect-timeout "$HEALTH_CONNECT_TIMEOUT"'
            ' --max-time "$HEALTH_MAX_TIME" "${BASE_URL}/health/ready"'
            ' --output "$WORKDIR/ready.json"',
            flattened,
        )
        self.assertIn(
            'curl --fail --silent --show-error --connect-timeout "$HEALTH_CONNECT_TIMEOUT"'
            ' --max-time "$HEALTH_MAX_TIME" "${BASE_URL}/"'
            ' --output "$WORKDIR/home.html"',
            flattened,
        )
        self.assertIn(
            '.status == "ok" and .version == $version'
            " and .source_sha == $source_sha and .image_digest == $image_digest",
            script,
        )


class PromotionProvenanceTests(TestCase):
    """REL-07: production promotion is pinned to main and carries full identity.

    A production run must be traceable to the controller checkout whose
    deployment code drove it, the dev run whose verification proved the
    release, and the observed service pair it actually left behind -- and a
    rollback must name its release explicitly rather than inferring one.
    """

    def test_production_promotion_runs_only_from_main(self) -> None:
        prod = (ROOT / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
        self.assertIn(
            "if: inputs.confirm_production == true && github.ref == 'refs/heads/main'",
            prod,
        )

    def test_the_dev_release_record_binds_the_proving_run(self) -> None:
        dev = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")
        self.assertIn("dev_run_id", dev)
        self.assertIn("constructed_at", dev)
        # The record is written after the deploy step has succeeded.
        record = dev[dev.index("Record the release proven in dev") :]
        self.assertIn("github.run_id", record)

    def test_the_promotion_validates_the_record_against_the_selected_run(
        self,
    ) -> None:
        prod = (ROOT / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
        self.assertIn(
            'keys == ["constructed_at", "dev_run_id", "image", "source_sha", "version"]',
            prod,
        )
        # The artifact's run binding must be the run the promotion selected.
        self.assertIn(".dev_run_id == $run_id", prod)

    def test_an_explicit_older_release_is_selected_verbatim(self) -> None:
        prod = (ROOT / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
        selection = prod[prod.index("Select the dev release to promote") :]
        self.assertIn('[[ -n "$DEV_RUN_ID_INPUT" ]]', selection)
        # A named run is accepted only as a completed, successful main deploy.
        self.assertIn('.path == ".github/workflows/deploy-dev.yml"', selection)
        self.assertIn('.head_branch == "main"', selection)
        self.assertIn('.conclusion == "success"', selection)

    def test_the_receipt_carries_the_provenance_and_observed_pair(self) -> None:
        module = (ROOT / "deploy" / "recovery_receipt.py").read_text(encoding="utf-8")
        self.assertIn("controller_sha", module)
        self.assertIn("dev_run_id", module)
        self.assertIn("promoted_at", module)
        script = (ROOT / "deploy" / "deploy_website.sh").read_text(encoding="utf-8")
        self.assertIn("--controller-sha", script)
        self.assertIn("--web-task-definition", script)
        self.assertIn("--worker-task-definition", script)
        wrapper = (ROOT / "deploy" / "deploy_prod.sh").read_text(encoding="utf-8")
        self.assertIn("CONTROLLER_SHA", wrapper)
        self.assertIn("dev_run_id", wrapper)


class TargetRegistryProfileTests(TestCase):
    """The registry is the single target-definition owner (REL-19)."""

    def test_the_development_target_shares_the_production_cluster_and_repository(self) -> None:
        development = registered_target("website-development")
        production = registered_target("website-production")
        self.assertFalse(development.retired)
        self.assertEqual(development.ecs_cluster_name, production.ecs_cluster_name)
        self.assertEqual(development.ecr_repository_uri, production.ecr_repository_uri)
        self.assertEqual(development.resource_namespace, "website-dev")
        self.assertEqual((development.web_desired_count, development.worker_desired_count), (1, 0))
        self.assertEqual((production.web_desired_count, production.worker_desired_count), (2, 1))

    def test_profile_output_carries_everything_the_orchestrator_needs(self) -> None:
        from deploy.deployment_targets import profile_fields

        development = profile_fields(registered_target("website-development"))
        self.assertEqual(development["CLUSTER_NAME"], "website-production")
        self.assertEqual(development["BASE_URL"], "https://dev.datatalks.club")
        self.assertEqual(development["DEVELOPMENT_HOSTNAME"], "dev.datatalks.club")
        self.assertEqual(development["WEB_DESIRED_COUNT"], "1")
        self.assertEqual(development["WORKER_DESIRED_COUNT"], "0")
        self.assertEqual(development["PROJECT_TAG"], "dtc-website")
        self.assertEqual(development["ENVIRONMENT_TAG"], "dev")
        production = profile_fields(registered_target("website-production"))
        self.assertEqual(production["BASE_URL"], "https://prod.datatalks.club")
        # Production tasks must not carry the development hostname variable.
        self.assertEqual(production["DEVELOPMENT_HOSTNAME"], "")
        self.assertEqual(
            sorted(development),
            sorted(production),
            "both profiles must expose the same keys",
        )

    def test_the_orchestrator_reads_its_profile_from_the_registry(self) -> None:
        script = (ROOT / "deploy" / "deploy_website.sh").read_text(encoding="utf-8")
        self.assertIn("python3 -m deploy.deployment_targets profile", script)
        self.assertIn('DTC_DEPLOYMENT_TARGET="website-development"', script)
        # No target literals of its own: the physical values come from the
        # registry profile.
        self.assertNotIn("NAMESPACE=", script)
        self.assertNotIn('BASE_URL="https://', script)
        self.assertNotIn("WEB_DESIRED_COUNT=1", script)


class TaskDefinitionImageUpdateTests(TestCase):
    def production_document(self, **overrides: object) -> dict:
        target = registered_target("website-production")
        document = {
            "taskDefinition": {
                "family": "website-production-web",
                "revision": 41,
                "status": "ACTIVE",
                "taskDefinitionArn": (
                    target.task_definition_arn_prefix("website-production-web") + "41"
                ),
                "taskRoleArn": target.task_role_arn,
                "executionRoleArn": target.execution_role_arn,
                "runtimePlatform": {
                    "cpuArchitecture": "ARM64",
                    "operatingSystemFamily": "LINUX",
                },
                "containerDefinitions": [
                    {
                        "name": "web",
                        "image": "old-image:tag",
                        "command": ["web"],
                        "environment": [
                            {"name": "APP_VERSION", "value": "old"},
                            {
                                "name": "TERRAFORM_OWNED",
                                "value": "carry-me-over",
                            },
                        ],
                        "secrets": [
                            {
                                "name": "DATABASE_URL",
                                "valueFrom": (
                                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                                    ":secret:website-production/database-url-abc123"
                                ),
                            },
                            {
                                "name": "DJANGO_SECRET_KEY",
                                "valueFrom": (
                                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                                    ":secret:website-production/django-secret-key-abc123"
                                ),
                            },
                        ],
                    }
                ],
            }
        }
        document["taskDefinition"].update(overrides)
        return document

    def update(self, document: dict, workload: str = "web") -> dict:
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            input_path = Path(directory) / "input.json"
            output_path = Path(directory) / "output.json"
            input_path.write_text(json.dumps(document), encoding="utf-8")
            update_task_definition(
                str(input_path),
                f"{registered_target('website-production').ecr_repository_uri}@sha256:{'b' * 64}",
                "20260905-120000-aaaaaaa",
                "a" * 40,
                "sha256:" + "b" * 64,
                workload,
                str(output_path),
            )
            return json.loads(output_path.read_text(encoding="utf-8"))

    def test_update_replaces_the_complete_runtime_identity(self) -> None:
        updated = self.update(self.production_document())

        container = updated["containerDefinitions"][0]
        environment = {entry["name"]: entry["value"] for entry in container["environment"]}
        self.assertNotIn("APP_VERSION", environment)
        self.assertEqual(environment["VERSION"], "20260905-120000-aaaaaaa")
        self.assertEqual(environment["SOURCE_SHA"], "a" * 40)
        self.assertEqual(environment["IMAGE_DIGEST"], "sha256:" + "b" * 64)
        self.assertEqual(environment["DTC_ENVIRONMENT"], "production")
        self.assertEqual(environment["DJANGO_SETTINGS_MODULE"], "website.settings.production")
        self.assertNotIn("DTC_DEVELOPMENT_HOSTNAME", environment)
        self.assertEqual(environment["PUBLIC_MEDIA_STORE_BACKEND"], "s3")
        self.assertEqual(environment["PUBLIC_MEDIA_S3_BUCKET"], "dtc-website-media")
        self.assertEqual(environment["PUBLIC_MEDIA_S3_REGION"], "eu-west-1")
        # Everything the release pipeline does not own is carried over.
        self.assertEqual(environment["TERRAFORM_OWNED"], "carry-me-over")
        self.assertEqual(
            container["image"],
            f"{registered_target('website-production').ecr_repository_uri}@sha256:{'b' * 64}",
        )
        self.assertNotIn("revision", updated)
        self.assertNotIn("taskDefinitionArn", updated)

    def test_the_module_cli_promotes_against_the_development_target(self) -> None:
        development = registered_target("website-development")
        document = {
            "taskDefinition": {
                "family": "website-dev-web",
                "revision": 7,
                "status": "ACTIVE",
                "taskDefinitionArn": (
                    development.task_definition_arn_prefix("website-dev-web") + "7"
                ),
                "taskRoleArn": development.task_role_arn,
                "executionRoleArn": development.execution_role_arn,
                "runtimePlatform": {
                    "cpuArchitecture": "ARM64",
                    "operatingSystemFamily": "LINUX",
                },
                "containerDefinitions": [
                    {
                        "name": "web",
                        "image": "old-image:tag",
                        "command": ["web"],
                        "environment": [],
                        "secrets": [
                            {
                                "name": "DATABASE_URL",
                                "valueFrom": (
                                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                                    ":secret:website-dev/database-url-abc123"
                                ),
                            },
                            {
                                "name": "DJANGO_SECRET_KEY",
                                "valueFrom": (
                                    "arn:aws:secretsmanager:eu-west-1:387546586013"
                                    ":secret:website-dev/django-secret-key-abc123"
                                ),
                            },
                        ],
                    }
                ],
            }
        }
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            input_path = Path(directory) / "input.json"
            output_path = Path(directory) / "output.json"
            input_path.write_text(json.dumps(document), encoding="utf-8")
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "deploy.update_task_definition_image",
                    str(input_path),
                    f"{development.ecr_repository_uri}@sha256:{'c' * 64}",
                    "20260905-130000-bbbbbbb",
                    "b" * 40,
                    "sha256:" + "c" * 64,
                    "web",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env={"DTC_DEPLOYMENT_TARGET": "website-development", "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            updated = json.loads(output_path.read_text(encoding="utf-8"))

        environment = {
            entry["name"]: entry["value"]
            for entry in updated["containerDefinitions"][0]["environment"]
        }
        self.assertEqual(environment["DTC_ENVIRONMENT"], "development")
        self.assertEqual(environment["DJANGO_SETTINGS_MODULE"], "website.settings.development")
        self.assertEqual(environment["DTC_DEVELOPMENT_HOSTNAME"], "dev.datatalks.club")
        self.assertEqual(environment["VERSION"], "20260905-130000-bbbbbbb")

    def test_a_sidecar_container_is_refused_before_registration(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["containerDefinitions"].append(
            {"name": "log-router", "image": "log-agent:1"}
        )
        with self.assertRaisesRegex(ReleaseContractError, "exactly one container"):
            self.update(document)

    def test_a_wrong_application_container_name_is_refused(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["containerDefinitions"][0]["name"] = "main"
        with self.assertRaisesRegex(ReleaseContractError, "expected container 'web'"):
            self.update(document)

    def test_a_foreign_task_role_is_refused(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["taskRoleArn"] = (
            "arn:aws:iam::387546586013:role/website-dev-task-application"
        )
        with self.assertRaisesRegex(ReleaseContractError, "task role"):
            self.update(document)

    def test_another_architecture_is_refused(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["runtimePlatform"] = {
            "cpuArchitecture": "X86_64",
            "operatingSystemFamily": "LINUX",
        }
        with self.assertRaisesRegex(ReleaseContractError, "runtime platform"):
            self.update(document)

    def test_an_out_of_target_secret_reference_is_refused(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["containerDefinitions"][0]["secrets"][0]["valueFrom"] = (
            "arn:aws:secretsmanager:eu-west-1:387546586013:secret:website-dev/database-url-abc123"
        )
        with self.assertRaisesRegex(ReleaseContractError, "secret reference"):
            self.update(document)

    def test_a_web_task_without_the_reviewed_command_is_refused(self) -> None:
        document = self.production_document()
        document["taskDefinition"]["containerDefinitions"][0]["command"] = ["serve"]
        with self.assertRaisesRegex(ReleaseContractError, "command mismatch"):
            self.update(document)

    def test_the_migration_command_is_not_validated_because_it_is_overridden(self) -> None:
        document = self.production_document()
        task = document["taskDefinition"]
        target = registered_target("website-production")
        task["family"] = "website-production-migration"
        task["taskDefinitionArn"] = (
            target.task_definition_arn_prefix("website-production-migration") + "3"
        )
        task["containerDefinitions"][0] = {
            "name": "migration",
            "image": "old-image:tag",
            "entryPoint": ["/bin/sh", "-lc"],
            "command": [
                "uv run --no-sync python manage.py migrate --noinput"
                " && uv run --no-sync python manage.py sync_relay_schedules"
            ],
            "environment": [],
            "secrets": document["taskDefinition"]["containerDefinitions"][0]["secrets"],
        }
        updated = self.update(document, workload="migration")
        self.assertEqual(
            updated["containerDefinitions"][0]["name"],
            "migration",
        )

    def test_a_service_source_from_another_family_is_refused(self) -> None:
        document = self.production_document()
        target = registered_target("website-production")
        document["taskDefinition"]["taskDefinitionArn"] = (
            target.task_definition_arn_prefix("website-production-worker") + "41"
        )
        with self.assertRaisesRegex(ReleaseContractError, "outside the"):
            self.update(document)

    def test_an_image_outside_the_target_repository_is_refused(self) -> None:
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            input_path = Path(directory) / "input.json"
            output_path = Path(directory) / "output.json"
            input_path.write_text(json.dumps(self.production_document()), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseContractError, "repository"):
                update_task_definition(
                    str(input_path),
                    f"other-account.dkr.ecr.eu-west-1.amazonaws.com/website@sha256:{'b' * 64}",
                    "20260905-120000-aaaaaaa",
                    "a" * 40,
                    "sha256:" + "b" * 64,
                    "web",
                    str(output_path),
                )
