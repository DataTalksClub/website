from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

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
        self.assertIn("Running migrations before either service is promoted", script)
        self.assertLess(script.index("aws ecs run-task"), script.index("aws ecs update-service"))
        self.assertIn("aws ecs wait services-stable", script)


class TaskDefinitionImageUpdateTests(TestCase):
    def test_update_replaces_the_complete_runtime_identity(self) -> None:
        source = {
            "taskDefinition": {
                "family": "website-dev-web",
                "revision": 1,
                "status": "ACTIVE",
                "taskDefinitionArn": "arn:old",
                "containerDefinitions": [
                    {
                        "name": "web",
                        "image": "old:image",
                        "environment": [
                            {"name": "APP_VERSION", "value": "old"},
                            {
                                "name": "CANONICAL_ORIGIN",
                                "value": "https://dev.datatalks.club",
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
            input_path.write_text(json.dumps(source), encoding="utf-8")
            update_task_definition(
                str(input_path),
                "example.invalid/repo@sha256:" + "b" * 64,
                "20260905-120000-aaaaaaa",
                "a" * 40,
                "sha256:" + "b" * 64,
                "development",
                "website.settings.development",
                "dev.datatalks.club",
                str(output_path),
            )
            updated = json.loads(output_path.read_text(encoding="utf-8"))

        container = updated["containerDefinitions"][0]
        environment = {entry["name"]: entry["value"] for entry in container["environment"]}
        self.assertNotIn("APP_VERSION", environment)
        self.assertEqual(environment["VERSION"], "20260905-120000-aaaaaaa")
        self.assertEqual(environment["SOURCE_SHA"], "a" * 40)
        self.assertEqual(environment["IMAGE_DIGEST"], "sha256:" + "b" * 64)
        self.assertEqual(environment["DTC_ENVIRONMENT"], "development")
        self.assertEqual(environment["DJANGO_SETTINGS_MODULE"], "website.settings.development")
        self.assertEqual(environment["DTC_DEVELOPMENT_HOSTNAME"], "dev.datatalks.club")
        self.assertEqual(environment["PUBLIC_MEDIA_STORE_BACKEND"], "s3")
        self.assertEqual(environment["PUBLIC_MEDIA_S3_BUCKET"], "dtc-website-media")
        self.assertEqual(environment["PUBLIC_MEDIA_S3_REGION"], "eu-west-1")
        self.assertEqual(environment["CANONICAL_ORIGIN"], "https://dev.datatalks.club")
        self.assertNotIn("revision", updated)
        self.assertNotIn("taskDefinitionArn", updated)
