"""Exercise the real promotion CLI without AWS or secret values."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

from core.tests.deployment_fixtures import RELAY_URL, secret_references, source_document
from deploy.deployment_targets import registered_target
from deploy.task_definitions import COMMANDS

ROOT = Path(__file__).resolve().parents[2]
DEV = registered_target("website-development")
PROD = registered_target("website-production")


def promotion_command(source, output, target, workload):
    return [
        sys.executable,
        "-m",
        "deploy.update_task_definition_image",
        str(source),
        target.ecr_repository_uri + "@sha256:" + "c" * 64,
        "20260929-120000-bbbbbbb",
        "b" * 40,
        "sha256:" + "c" * 64,
        workload,
        str(output),
    ]


class RelaySecretPromotionTests(TestCase):
    def promote(self, document, target, workload="web"):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "output.json"
            source.write_text(json.dumps(document))
            result = subprocess.run(  # noqa: S603
                promotion_command(source, output, target, workload),
                cwd=ROOT,
                env={"DTC_DEPLOYMENT_TARGET": target.name, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                timeout=30,
            )
            updated = None
            if output.exists():
                updated = json.loads(output.read_text())
            return result, updated

    def test_all_workloads_preserve_exact_references_and_configured_url(self):
        for target in (DEV, PROD):
            for workload in ("web", "worker", "migration"):
                with self.subTest(target=target.name, workload=workload):
                    result, task = self.promote(source_document(target, workload), target, workload)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    container = task["containerDefinitions"][0]
                    self.assertEqual(container["secrets"], secret_references(target))
                    environment = {item["name"]: item["value"] for item in container["environment"]}
                    self.assertEqual(environment["RELAY_BASE_URL"], RELAY_URL)
                    self.assertEqual(environment["DTC_ENVIRONMENT"], target.dtc_environment)
                    self.assertEqual(environment["DJANGO_SETTINGS_MODULE"], target.settings_module)
                    self.assertEqual(environment["VERSION"], "20260929-120000-bbbbbbb")
                    self.assertEqual(
                        container["image"], target.ecr_repository_uri + "@sha256:" + "c" * 64
                    )
                    self.assertEqual(container["command"], COMMANDS[workload]["command"])
                    self.assertNotIn("taskDefinitionArn", task)
                    if workload == "migration":
                        self.assertEqual(container["entryPoint"], COMMANDS[workload]["entryPoint"])
                    if target == DEV:
                        self.assertEqual(environment["DTC_DEVELOPMENT_HOSTNAME"], DEV.hostname)

    def reject(self, secrets, target=DEV):
        document = source_document(target, "web")
        document["taskDefinition"]["containerDefinitions"][0]["secrets"] = secrets
        result, updated = self.promote(document, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(updated)
        self.assertNotIn("arn:aws:secretsmanager:", result.stderr + result.stdout)
        self.assertNotIn("sensitive-sentinel", result.stderr + result.stdout)

    def test_missing_duplicate_extra_and_malformed_entries_are_refused(self):
        references = secret_references(DEV)
        invalid: list[Any] = [
            None,
            {},
            [None],
            [{"name": "sensitive-sentinel", "value": "sensitive-sentinel"}],
        ]
        for index in range(len(references)):
            invalid.append(references[:index] + references[index + 1 :])
        invalid.extend(
            [
                references + [references[0]],
                references + [{"name": "EXTRA", "valueFrom": "sensitive-sentinel"}],
            ]
        )
        for value in invalid:
            with self.subTest(value=value):
                self.reject(value)

    def test_wrong_relay_boundaries_selectors_and_containers_are_refused(self):
        reference = secret_references(DEV)[2]["valueFrom"]
        invalid: list[str] = [
            reference.replace("eu-west-1", "us-east-1"),
            reference.replace("387546586013", "817685572750"),
            reference.replace("website-dev/", "website-production/"),
            reference.replace("integrations-", "other-"),
            reference.replace("abc123", "short"),
            reference.replace(":RELAY_API_KEY::", ""),
            reference.replace(":RELAY_API_KEY::", ":RELAY_WEBHOOK_SECRET::"),
            reference.replace(":RELAY_API_KEY::", ":OTHER::"),
            reference.replace(":RELAY_API_KEY::", ":RELAY_API_KEY:AWSCURRENT:"),
            reference.replace(":RELAY_API_KEY::", ":RELAY_API_KEY::version"),
            reference.replace("abc123", "abc456"),
            "sensitive-sentinel",
            "",
        ]
        for value in invalid:
            with self.subTest(value=value):
                references = secret_references(DEV)
                references[2]["valueFrom"] = value
                self.reject(references)

    def test_production_rejects_relay_and_development_references(self):
        self.reject(secret_references(PROD) + secret_references(DEV)[2:], PROD)
        self.reject(secret_references(DEV)[:2], PROD)
