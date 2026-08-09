from __future__ import annotations

import hashlib
import json
import re
import subprocess
from argparse import Namespace
from itertools import permutations
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import yaml  # type: ignore[import-untyped]
from django.test import SimpleTestCase

from deploy import cli as deployment_cli
from deploy.aws_gateway import (
    CONTROLLED_MIGRATION_FAILURE_COMMAND,
    MAX_STAGE_TIMEOUT_SECONDS,
    MAX_WEB_STABILIZATION_TIMEOUT_SECONDS,
    MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS,
    WEB_STABILIZATION_TIMEOUT_SECONDS,
    WORKER_STABILIZATION_TIMEOUT_SECONDS,
    AwsReleaseConfig,
    AwsReleaseGateway,
)
from deploy.contracts import (
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ServicePredecessor,
    ServiceSnapshot,
    ServiceTarget,
    ServiceUpdateReceipt,
)
from deploy.task_definitions import (
    FIXED_NONSECRET_ENVIRONMENT,
    TaskDefinitionConfig,
    build_task_definitions,
)

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_WORKFLOW_COMMIT = "91490f0d3f172327a400c9edf5b441265890f897"
HISTORICAL_WORKFLOW_SHA256 = "d6730d36c41866adcfd933ef733132e26ea67d292ddd0334caf42f9b2524850d"
DATABASE_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/database-url-Ab12Cd"
)
DJANGO_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/django-secret-key-Ef34Gh"
)


class DeploymentWorkflowContractTests(SimpleTestCase):
    def test_django_gate_fetches_history_for_frozen_evidence_tests(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        checkout = next(
            step
            for step in document["jobs"]["django"]["steps"]
            if step.get("uses") == "actions/checkout@v4"
        )
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        self.assertEqual(checkout["with"]["path"], ".tmp/release-source")

    def test_prior_capture_validates_every_physical_value_before_oidc(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        steps = document["jobs"]["auto-capture-prior"]["steps"]
        validation = next(
            step
            for step in steps
            if step.get("name") == "Validate automatic prior-capture configuration"
        )
        self.assertEqual(
            set(validation["env"]),
            {
                "AWS_REGION",
                "DEPLOYER_ROLE_ARN",
                "ECR_REPOSITORY_URI",
                "ECS_CLUSTER_ARN",
                "WEB_TARGET_GROUP_ARN",
                "WEB_SERVICE_NAME",
                "WORKER_SERVICE_NAME",
                "WEB_FAMILY",
                "WORKER_FAMILY",
                "MIGRATION_FAMILY",
                "CONTAINER_NAMES",
                "TASK_ROLE_ARN",
                "EXECUTION_ROLE_ARN",
                "ECS_SUBNET_IDS",
                "ECS_SECURITY_GROUP_IDS",
                "ASSIGN_PUBLIC_IP",
                "WEB_DESIRED_COUNT",
                "WORKER_DESIRED_COUNT",
            },
        )
        validation_index = steps.index(validation)
        oidc_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
        )
        self.assertLess(validation_index, oidc_index)

    def test_every_compatibility_validator_runs_after_checkout_and_uv_setup(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        validators = 0
        for job_name, job in document["jobs"].items():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if "deploy.legacy_development_compatibility" not in step.get("run", ""):
                    continue
                validators += 1
                prior = steps[:index]
                self.assertTrue(
                    any(item.get("uses") == "actions/checkout@v4" for item in prior),
                    job_name,
                )
                self.assertTrue(
                    any(item.get("uses") == "astral-sh/setup-uv@v6" for item in prior),
                    job_name,
                )
                self.assertTrue(
                    any(item.get("run") == "uv sync --locked" for item in prior),
                    job_name,
                )
        self.assertEqual(validators, 8)

    def test_workflow_reads_the_complete_development_variable_contract_only(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        names = set(re.findall(r"vars\.(DEVELOPMENT_[A-Z0-9_]+)", workflow))
        self.assertEqual(
            names,
            {
                "DEVELOPMENT_AUTO_DEPLOY",
                "DEVELOPMENT_AWS_REGION",
                "DEVELOPMENT_DEPLOYER_ROLE_ARN",
                "DEVELOPMENT_ECR_REPOSITORY_NAME",
                "DEVELOPMENT_ECR_REPOSITORY_URI",
                "DEVELOPMENT_ECS_ASSIGN_PUBLIC_IP",
                "DEVELOPMENT_ECS_CLUSTER_ARN",
                "DEVELOPMENT_ECS_CONTAINER_NAMES",
                "DEVELOPMENT_ECS_EXECUTION_ROLE_ARN",
                "DEVELOPMENT_ECS_MIGRATION_TASK_FAMILY",
                "DEVELOPMENT_ECS_SECURITY_GROUP_IDS",
                "DEVELOPMENT_ECS_SUBNET_IDS",
                "DEVELOPMENT_ECS_TASK_ROLE_ARN",
                "DEVELOPMENT_ECS_WEB_SERVICE_NAME",
                "DEVELOPMENT_ECS_WEB_TASK_FAMILY",
                "DEVELOPMENT_ECS_WORKER_SERVICE_NAME",
                "DEVELOPMENT_ECS_WORKER_TASK_FAMILY",
                "DEVELOPMENT_KMS_KEY_ARN",
                "DEVELOPMENT_PUBLISHER_ROLE_ARN",
                "DEVELOPMENT_RESOURCE_ENVIRONMENT_TAG",
                "DEVELOPMENT_RESOURCE_PROJECT_TAG",
                "DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID",
                "DEVELOPMENT_WEB_RELEASE_DESIRED_COUNT",
                "DEVELOPMENT_WEB_TARGET_GROUP_ARN",
                "DEVELOPMENT_WORKER_RELEASE_DESIRED_COUNT",
            },
        )
        self.assertNotIn("vars." + "SANDBOX_", workflow)

    def test_workflow_is_serialized_main_only_and_disabled_without_explicit_dispatch(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        self.assertNotIn("pull_request", workflow)
        self.assertIn("group: website-development-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("deploy_development:", workflow)
        self.assertIn("probe_development:", workflow)
        self.assertNotIn("deploy_" + "sandbox", workflow)
        self.assertNotIn("probe_" + "sandbox", workflow)
        self.assertNotIn("vars." + "SANDBOX_", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertEqual(workflow.count("id-token: write"), 7)
        self.assertIn("name: sandbox", workflow)

    def test_failure_injection_is_dispatch_only_default_none_and_promotion_only(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        dispatch = workflow.split("workflow_dispatch:", maxsplit=1)[1].split(
            "\npermissions:", maxsplit=1
        )[0]
        self.assertIn("failure_injection:", dispatch)
        self.assertIn("default: none", dispatch)
        self.assertIn("options: [none, migration, post_mutation_smoke]", dispatch)
        self.assertIn("Controlled failure injection is promotion-only", workflow)
        self.assertIn("Controlled failure injection requires exact accepted release B", workflow)
        self.assertIn(
            "Controlled failure injection requires exact accepted release A as prior",
            workflow,
        )
        self.assertIn("RELEASE_A_SHA: 0f0ae208526fa2e76848cf4f5a87bd4aa26687ec", workflow)
        self.assertIn("RELEASE_B_SHA: e2b93beb1544170b6177ba55ea8fd6530b2e57a3", workflow)
        self.assertIn('if [[ "$prior_source" != "$RELEASE_A_SHA" ]]', workflow)
        self.assertEqual(workflow.count('--failure-injection "$FAILURE_INJECTION"'), 1)

    def test_exact_a_b_drill_sequence_and_build_once_reuse_are_fail_closed(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        dispatch = workflow.split("workflow_dispatch:", maxsplit=1)[1].split(
            "\npermissions:", maxsplit=1
        )[0]
        resolve = workflow.split("\n  resolve-release:\n", maxsplit=1)[1].split(
            "\n  quality:\n", maxsplit=1
        )[0]

        self.assertIn("reuse_existing_image:", dispatch)
        self.assertIn("published_image_record:", dispatch)
        self.assertIn("default: false", dispatch)
        self.assertIn("Initial release A requires no prior release", resolve)
        self.assertIn("reuse requires a published-image record", resolve)
        self.assertIn(
            "published_image_record is accepted only when reuse_existing_image=true", resolve
        )
        self.assertIn(
            "The controlled B post-mutation failure must reuse the published B image",
            resolve,
        )
        self.assertIn("Every B run after its migration drill must reuse", resolve)
        self.assertIn("Rollback requires reuse with no failure injection", resolve)
        self.assertIn('keys == ["image_config_digest"', resolve)
        self.assertIn('.platform == "linux/amd64"', resolve)
        self.assertIn('.user == "10001:10001"', resolve)

    def test_automatic_rerun_restores_exact_sha_cache_and_can_never_rebuild(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        steps = document["jobs"]["container"]["steps"]
        cache = next(step for step in steps if step.get("id") == "image-cache")
        self.assertEqual(cache["uses"], "actions/cache@v4")
        self.assertEqual(
            cache["with"]["key"],
            "tested-release-image-${{ needs.resolve-release.outputs.release_sha }}",
        )
        rejection = next(step for step in steps if step.get("name", "").startswith("Reject"))
        self.assertIn("github.run_attempt > 1", rejection["if"])
        self.assertIn("cache-hit != 'true'", rejection["if"])
        build = next(step for step in steps if "Build the production image" in step.get("name", ""))
        save = next(
            step for step in steps if "Preserve the one tested image" in step.get("name", "")
        )
        self.assertIn("cache-hit != 'true'", build["if"])
        self.assertIn("cache-hit != 'true'", save["if"])
        load = next(
            step for step in steps if "Load the immutable tested image" in step.get("name", "")
        )
        self.assertIn("cache-hit == 'true'", load["if"])
        handoff = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")
        self.assertEqual(
            handoff["with"]["name"],
            "release-image-${{ needs.resolve-release.outputs.release_sha }}",
        )
        self.assertTrue(handoff["with"]["overwrite"])
        publish_steps = document["jobs"]["publish"]["steps"]
        download = next(
            step for step in publish_steps if step.get("uses") == "actions/download-artifact@v4"
        )
        self.assertEqual(download["with"]["name"], handoff["with"]["name"])

    def test_terminal_artifacts_are_attempt_qualified_bounded_and_recover_on_failure(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        deploy_steps = document["jobs"]["deploy"]["steps"]
        by_id = {step["id"]: step for step in deploy_steps if "id" in step}
        for step_id in ("evidence_upload", "smoke_upload", "success_record_upload"):
            step = by_id[step_id]
            self.assertTrue(step["continue-on-error"])
            self.assertEqual(step["timeout-minutes"], 2)
            self.assertIn("github.run_attempt", step["with"]["name"])
        success = by_id["success_record_upload"]
        self.assertIn("steps.evidence_upload.outcome == 'success'", success["if"])
        self.assertIn("steps.smoke_upload.outcome == 'success'", success["if"])
        recovery = by_id["finalization_recovery"]
        self.assertIn("steps.release.outcome == 'success'", recovery["if"])
        self.assertIn("restore-finalization", recovery["run"])
        self.assertIn("recovery-context.json", recovery["run"])
        self.assertLess(
            deploy_steps.index(by_id["evidence_upload"]),
            deploy_steps.index(by_id["success_record_upload"]),
        )
        builder = by_id["evidence_builder"]
        self.assertEqual(builder["env"]["CONTROLLER_OUTCOME"], "${{ steps.release.outcome }}")
        self.assertIn(
            'os.environ.get("CONTROLLER_OUTCOME") == "success"',
            builder["run"],
        )
        self.assertIn("deployer_before_checkpoint", builder["run"])
        checkpoint_capture = next(
            step
            for step in deploy_steps
            if step.get("name") == "Capture the exact pre-mutation recovery checkpoint"
        )
        checkpoint_upload = next(
            step
            for step in deploy_steps
            if step.get("name") == "Preserve the exact pre-mutation incident checkpoint"
        )
        release = by_id["release"]
        self.assertIn("deploy.cli capture-recovery", checkpoint_capture["run"])
        self.assertIn("github.run_attempt", checkpoint_upload["with"]["name"])
        self.assertEqual(checkpoint_upload["timeout-minutes"], 2)
        self.assertLess(deploy_steps.index(checkpoint_upload), deploy_steps.index(release))
        self.assertNotIn("continue-on-error", checkpoint_upload)

    def test_workflow_uses_uv_for_every_python_command(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        for line in workflow.splitlines():
            if "python " in line or line.rstrip().endswith("python - <<'PY'"):
                self.assertIn("uv run", line, line)

    def test_deployer_session_preserves_a_bounded_recovery_reserve(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        deploy_steps = document["jobs"]["deploy"]["steps"]
        assume = next(
            step
            for step in deploy_steps
            if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
        )
        self.assertEqual(assume["with"]["role-duration-seconds"], 3600)
        release = next(step for step in deploy_steps if step.get("id") == "release")
        self.assertIn("--timeout-seconds 180", release["run"])
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertEqual(workflow.count("--timeout-seconds 180"), 2)
        self.assertEqual(
            workflow.count("--web-stabilization-timeout-seconds 240"),
            2,
        )
        self.assertEqual(
            workflow.count("--worker-stabilization-timeout-seconds 420"),
            2,
        )
        dispatch = workflow.split("workflow_dispatch:", maxsplit=1)[1].split(
            "\npermissions:", maxsplit=1
        )[0]
        self.assertNotIn("stabilization-timeout", dispatch)
        recovery = next(step for step in deploy_steps if step.get("id") == "finalization_recovery")
        self.assertEqual(recovery["timeout-minutes"], 12)
        critical_upload_minutes = sum(
            step.get("timeout-minutes", 0)
            for step in deploy_steps
            if step.get("id") in {"evidence_upload", "smoke_upload", "success_record_upload"}
        )
        self.assertEqual(critical_upload_minutes, 6)
        worst_case_seconds = (
            180
            + 120
            + WEB_STABILIZATION_TIMEOUT_SECONDS
            + 180
            + WORKER_STABILIZATION_TIMEOUT_SECONDS
            + 180
            + critical_upload_minutes * 60
            + recovery["timeout-minutes"] * 60
        )
        self.assertEqual(worst_case_seconds, 2400)
        self.assertGreaterEqual(3600 - worst_case_seconds, 20 * 60)
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        self.assertIn("3600 - 2400 = 1200", runbook)

    def test_reuse_path_cannot_build_load_login_or_push_and_keeps_publish_artifact(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        container = workflow.split("\n  container:\n", maxsplit=1)[1].split(
            "\n  publish:\n", maxsplit=1
        )[0]
        publish = workflow.split("\n  publish:\n", maxsplit=1)[1].split(
            "\n  deploy:\n", maxsplit=1
        )[0]
        reuse = publish.split(
            "- name: Verify and preserve the recorded immutable image without Docker",
            maxsplit=1,
        )[1].split("- id: image", maxsplit=1)[0]

        build_condition = (
            "if: github.event_name != 'workflow_dispatch' || inputs.reuse_existing_image == false"
        )
        self.assertGreaterEqual(container.count(build_condition), 3)
        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' && inputs.reuse_existing_image == true",
            reuse,
        )
        for forbidden in (
            "docker buildx build",
            "docker image load",
            "docker login",
            "docker image push",
            "docker pull",
        ):
            self.assertNotIn(forbidden, reuse)
        self.assertIn("aws ecr describe-images", reuse)
        self.assertIn("aws ecr batch-get-image", reuse)
        self.assertIn("imageManifest | fromjson | .config.digest", reuse)
        self.assertIn("development-published-image-", publish)
        self.assertIn("retention-days: 90", publish)
        self.assertLess(
            publish.index("Preserve the published-image record independently of deployment"),
            workflow.index("\n  deploy:\n"),
        )

    def test_auto_cd_is_opt_in_stale_safe_and_captures_prior_under_deployer(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        publish = workflow.split("\n  publish:\n", maxsplit=1)[1].split(
            "\n  deploy:\n", maxsplit=1
        )[0]
        deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1].split(
            "\n  probe-contract:\n", maxsplit=1
        )[0]

        capture = workflow.split("\n  auto-capture-prior:\n", maxsplit=1)[1].split(
            "\n  publish:\n", maxsplit=1
        )[0]
        self.assertEqual(workflow.count("vars.DEVELOPMENT_AUTO_DEPLOY == 'true'"), 3)
        self.assertIn("github.event_name == 'push'", publish)
        self.assertIn("github.event_name == 'push'", deploy)
        for section, label in (
            (capture, "Verify current-main controller immediately before prior-capture OIDC"),
            (publish, "Verify current-main controller immediately before publisher OIDC"),
            (deploy, "Verify current-main controller immediately before deployer OIDC"),
        ):
            stale = section.index(label)
            assume = section.index("- uses: aws-actions/configure-aws-credentials@v4")
            self.assertLess(stale, assume)
            between = section[stale:assume]
            self.assertIn("refs/remotes/origin/main", between)
            self.assertIn("$GITHUB_SHA", between)
        self.assertIn("deploy.cli capture-current", capture)
        self.assertNotIn("aws ecr", capture)
        self.assertNotIn("docker image push", capture)
        self.assertIn("needs.auto-capture-prior.result == 'success'", publish)
        self.assertIn("needs.auto-capture-prior.result == 'skipped'", publish)
        self.assertIn("--active-service-pair .tmp/deployment/active-service-pair.json", deploy)
        self.assertNotIn("deploy.cli capture-current", deploy)
        self.assertIn("always()", publish)
        self.assertIn("always()", deploy)

    def test_probe_is_current_main_only_and_skips_every_release_mutation_job(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        dispatch = workflow.split("workflow_dispatch:", maxsplit=1)[1].split(
            "\npermissions:", maxsplit=1
        )[0]
        self.assertIn("probe_development:", dispatch)
        self.assertIn("options: [promote, rollback, probe]", dispatch)
        self.assertIn("Workflow controller is not the current main commit", workflow)
        self.assertIn("OIDC probes run only from the current main source", workflow)
        self.assertIn("probe-contract:", workflow)
        self.assertIn("probe-publisher:", workflow)
        self.assertIn("probe-deployer:", workflow)
        self.assertIn("probe-wrong-main-claims:", workflow)
        self.assertIn("probe-wrong-environment-claim:", workflow)
        self.assertGreaterEqual(workflow.count("inputs.operation != 'probe'"), 6)

        probe_jobs = workflow.split("\n  probe-contract:\n", maxsplit=1)[1]
        for forbidden in (
            "docker buildx build",
            "docker image push",
            "deploy.cli promote",
            "deploy.cli rollback",
            "actions/upload-artifact",
        ):
            self.assertNotIn(forbidden, probe_jobs)
        self.assertIn("core.tests.test_deployment_oidc_probe", probe_jobs)

    def test_wrong_claim_jobs_validate_exact_nonsecret_role_inputs_first(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        wrong_claims = workflow.split("\n  probe-wrong-main-claims:\n", maxsplit=1)[1]
        self.assertIn("Select and validate exact wrong-claim probe inputs", wrong_claims)
        self.assertIn("deploy.legacy_development_compatibility", wrong_claims)
        self.assertNotIn("website-" + "sandbox-github-deployer", wrong_claims)
        self.assertNotIn("continue-on-error: true", wrong_claims)
        self.assertIn("deploy.development_oidc_claim_probe", wrong_claims)
        self.assertIn("--audience dtc.invalid.example", wrong_claims)
        self.assertIn("AWS_EC2_METADATA_DISABLED: true", wrong_claims)

    def test_every_oidc_request_is_immediately_preceded_by_current_main_verification(self) -> None:
        document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        jobs = document["jobs"]
        configured_jobs = (
            "auto-capture-prior",
            "publish",
            "deploy",
            "probe-publisher",
            "probe-deployer",
        )
        for job_name in configured_jobs:
            steps = jobs[job_name]["steps"]
            assume_indexes = [
                index
                for index, step in enumerate(steps)
                if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
            ]
            self.assertEqual(len(assume_indexes), 1)
            previous = steps[assume_indexes[0] - 1]
            self.assertIn("Verify current-main controller immediately before", previous["name"])
            self.assertIn("refs/remotes/origin/main", previous["run"])

        for job_name in ("probe-wrong-main-claims", "probe-wrong-environment-claim"):
            steps = jobs[job_name]["steps"]
            token_indexes = [
                index
                for index, step in enumerate(steps)
                if "deploy.development_oidc_claim_probe" in step.get("run", "")
            ]
            self.assertTrue(token_indexes)
            for index in token_indexes:
                previous = steps[index - 1]
                self.assertIn("Verify current-main controller immediately before", previous["name"])
                self.assertIn("refs/remotes/origin/main", previous["run"])
                self.assertIn('test "$RELEASE_SHA" = "$controller"', previous["run"])

    def test_deployer_probe_rejects_any_nonwebsite_target_group_before_assumption(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        deployer_probe = workflow.split("\n  probe-deployer:\n", maxsplit=1)[1].split(
            "\n  probe-wrong-main-claims:\n", maxsplit=1
        )[0]
        validation = deployer_probe.split(
            "- name: Validate exact deployer probe inputs", maxsplit=1
        )[1].split("- uses: aws-actions/configure-aws-credentials@v4", maxsplit=1)[0]

        self.assertIn(
            "deploy.legacy_development_compatibility deployer-probe",
            validation,
        )
        self.assertNotIn('test -n "$WEB_TARGET_GROUP_ARN"', validation)

    def test_route53_zone_is_exactly_validated_for_both_probe_roles_only(self) -> None:
        workflow_path = ROOT / ".github/workflows/ci.yml"
        workflow = workflow_path.read_text()
        document = yaml.safe_load(workflow)
        jobs = document["jobs"]
        variable_expression = "${{ vars.DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID }}"

        for job_name, role in (
            ("probe-publisher", "publisher"),
            ("probe-deployer", "deployer"),
        ):
            steps = jobs[job_name]["steps"]
            validation = next(
                step for step in steps if step.get("name") == f"Validate exact {role} probe inputs"
            )
            credential_index = next(
                index
                for index, step in enumerate(steps)
                if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
            )
            self.assertLess(steps.index(validation), credential_index)
            self.assertEqual(validation["env"]["HOSTED_ZONE_ID"], variable_expression)
            self.assertIn("deploy.legacy_development_compatibility", validation["run"])

            probe = next(
                step
                for step in steps
                if f"python -m deploy.development_oidc_probe {role}" in step.get("run", "")
            )
            self.assertEqual(probe["env"]["HOSTED_ZONE_ID"], variable_expression)
            self.assertIn('--hosted-zone-id "$HOSTED_ZONE_ID"', probe["run"])

        self.assertEqual(workflow.count("vars.DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID"), 4)
        for job_name, job in jobs.items():
            if job_name not in {"probe-publisher", "probe-deployer"}:
                self.assertNotIn("DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID", str(job))

    def test_kms_key_is_exactly_validated_and_wired_once_for_both_probe_roles_only(self) -> None:
        workflow_path = ROOT / ".github/workflows/ci.yml"
        workflow = workflow_path.read_text()
        document = yaml.safe_load(workflow)
        jobs = document["jobs"]
        variable_expression = "${{ vars.DEVELOPMENT_KMS_KEY_ARN }}"
        for job_name, role in (
            ("probe-publisher", "publisher"),
            ("probe-deployer", "deployer"),
        ):
            with self.subTest(job_name=job_name):
                steps = jobs[job_name]["steps"]
                validation = next(
                    step
                    for step in steps
                    if step.get("name") == f"Validate exact {role} probe inputs"
                )
                credential_index = next(
                    index
                    for index, step in enumerate(steps)
                    if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
                )
                self.assertLess(steps.index(validation), credential_index)
                self.assertEqual(validation["env"]["KMS_KEY_ARN"], variable_expression)
                self.assertIn("deploy.legacy_development_compatibility", validation["run"])

                probe = next(
                    step
                    for step in steps
                    if f"python -m deploy.development_oidc_probe {role}" in step.get("run", "")
                )
                self.assertEqual(probe["env"]["KMS_KEY_ARN"], variable_expression)
                self.assertEqual(probe["run"].count('--kms-key-arn "$KMS_KEY_ARN"'), 1)

        self.assertEqual(workflow.count("vars.DEVELOPMENT_KMS_KEY_ARN"), 4)
        self.assertEqual(workflow.count('--kms-key-arn "$KMS_KEY_ARN"'), 2)
        for job_name, job in jobs.items():
            if job_name not in {"probe-publisher", "probe-deployer"}:
                self.assertNotIn("DEVELOPMENT_KMS_KEY_ARN", str(job))

    def test_runbook_orders_probe_before_secret_population_and_releases(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        bootstrap = runbook.split("## One-time bootstrap", maxsplit=1)[1].split(
            "## Post-bootstrap OIDC probe", maxsplit=1
        )[0]

        apply_position = bootstrap.index("Apply the reviewed")
        outputs_position = bootstrap.index("Configure the non-secret GitHub variables")
        trust_position = bootstrap.index("Read back the\n   publisher/deployer trust policies")
        probe_position = bootstrap.index("Complete the live OIDC probe hold point")
        secrets_position = bootstrap.index("Only after the probe is green, populate")
        release_position = bootstrap.index("Proceed to release A")
        self.assertLess(apply_position, trust_position)
        self.assertLess(trust_position, outputs_position)
        self.assertLess(outputs_position, probe_position)
        self.assertLess(probe_position, secrets_position)
        self.assertLess(secrets_position, release_position)
        self.assertNotIn("Populate the required Secrets Manager values", bootstrap)

    def test_runbook_documents_exact_route53_probe_and_pre_post_evidence(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        probe = runbook.split("## Post-bootstrap OIDC probe", maxsplit=1)[1].split(
            "## Select and promote a release", maxsplit=1
        )[0]
        normalized_probe = " ".join(probe.split())

        self.assertIn("`DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID`", runbook)
        self.assertIn("`Z05963572WVWFHDQZH5NE`", runbook)
        self.assertIn("not a Terraform output", runbook)
        self.assertIn("exactly two byte-for-byte identical `DELETE` changes", normalized_probe)
        self.assertIn("transactional", normalized_probe)
        self.assertIn("`InvalidChangeBatch`", normalized_probe)
        self.assertIn("`NoSuchHostedZone`", normalized_probe)
        self.assertIn("Only an AccessDenied-class response passes", normalized_probe)
        self.assertIn("canonical full record", normalized_probe)
        self.assertIn("exact six", normalized_probe)
        self.assertIn("byte-for-byte unchanged", normalized_probe)

    def test_runbook_has_exact_variable_inventory_and_kms_probe_contract(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        table = runbook.split("| GitHub variable |", maxsplit=1)[1].split(
            "Do not export secret-container ARNs", maxsplit=1
        )[0]
        repository_variables = {
            line.split("`", maxsplit=2)[1] for line in table.splitlines() if "| repository" in line
        }
        environment_variables = {
            line.split("`", maxsplit=2)[1]
            for line in table.splitlines()
            if "| legacy GitHub environment `sandbox` |" in line
        }
        self.assertEqual(
            repository_variables,
            {
                "DEVELOPMENT_AWS_REGION",
                "DEVELOPMENT_ECR_REPOSITORY_URI",
                "DEVELOPMENT_ECR_REPOSITORY_NAME",
                "DEVELOPMENT_PUBLISHER_ROLE_ARN",
                "DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID",
                "DEVELOPMENT_KMS_KEY_ARN",
            },
        )
        self.assertEqual(len(environment_variables), 18)
        self.assertNotIn("DEVELOPMENT_KMS_KEY_ARN", environment_variables)
        self.assertNotIn("DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID", environment_variables)
        self.assertIn("six repository rows", runbook)
        self.assertIn("exact 18 environment rows", runbook)
        self.assertIn("independent fail-closed `DEVELOPMENT_AUTO_DEPLOY=false` switch", runbook)

        probe = runbook.split("## Post-bootstrap OIDC probe", maxsplit=1)[1].split(
            "## Select and promote a release", maxsplit=1
        )[0]
        normalized_probe = " ".join(probe.split())
        exact_arn = "arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887"
        for expected in (
            "`DEVELOPMENT_KMS_KEY_ARN`",
            f"`{exact_arn}`",
            '`Operations=["Decrypt"]`',
            "`DryRun=True`",
            "`DryRunOperationException`",
            "`NotFoundException`",
            "`AccessDeniedException`",
            "grant inventory",
            "before requesting OIDC credentials",
            "pass it exactly once",
        ):
            self.assertIn(expected, normalized_probe)

    def test_sentinel_audit_covers_complete_sequence_and_three_part_proof(self) -> None:
        audit = (ROOT / "_docs/audits/2026-08-07-oidc-denial-sentinels.md").read_text()
        for action in (
            "ecr:DescribeImages",
            "s3:GetObject",
            "iam:UpdateRoleDescription",
            "route53:ChangeResourceRecordSets",
            "cloudfront:CreateInvalidation",
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            "rds:ModifyDBInstance",
            "kms:CreateGrant",
            "secretsmanager:GetSecretValue",
            "ecs:DeregisterTaskDefinition",
            "ecr:BatchDeleteImage",
            "ecs:DescribeServices",
            "ecs:UpdateService",
            "ecs:RunTask",
        ):
            self.assertIn(f"`{action}`", audit)
        for outcome in (
            "ResourceNotFoundException",
            "ClientException",
            "InvalidParameterException",
            "ClusterNotFoundException",
            "MISSING",
            "AccessDenied",
        ):
            self.assertIn(outcome, audit)
        self.assertGreaterEqual(audit.count("**Remove.**"), 5)
        self.assertEqual(audit.count("**Retain.**"), 4)
        self.assertIn("website live-call allowlist", audit)
        self.assertIn("aws-infra", audit)
        self.assertIn("simulate-principal-policy", audit)
        self.assertIn("positive controls", audit)
        self.assertIn("ExpectedBucketOwner=817685572750", audit)
        self.assertIn("exactly four calls, in order", audit)

        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        probe = runbook.split("## Post-bootstrap OIDC probe", maxsplit=1)[1].split(
            "## Select and promote a release", maxsplit=1
        )[0]
        for expected in (
            "ExpectedBucketOwner=817685572750",
            "ecr:DescribeImages` on foreign and production-shaped repository ARNs",
            "iam:UpdateRoleDescription` on each exact publisher/deployer role ARN",
            "cloudfront:CreateInvalidation` on `<cloudfront-distribution-arn>`",
            "elasticloadbalancing:ModifyTargetGroupAttributes` on exact web target-group ARN",
            "rds:ModifyDBInstance` on exact website DB ARN",
            "s3:GetObject` on exact state-object ARN",
            "route53:ChangeResourceRecordSets` on exact hosted-zone ARN",
            "kms:CreateGrant` on exact runtime-key ARN",
            "ecr:BatchDeleteImage` on exact `website-sandbox` repository ARN",
            "simulator covers identity policies only",
            "ECR `website-sandbox` repository policy",
            "S3 state-bucket policy",
        ):
            self.assertIn(expected, probe)

    def test_gate_b_evidence_contract_is_atomic_offline_and_workflow_isolated(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        audit = (ROOT / "_docs/audits/2026-08-07-oidc-denial-sentinels.md").read_text()
        manifest = json.loads((ROOT / "deploy/gate_b_manifest.json").read_text())
        gate = runbook.split("### #81 Gate B — readback and simulator preflight", maxsplit=1)[
            1
        ].split("Before the regional `DescribeTargetHealth`", maxsplit=1)[0]

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["contract_id"], "website-sandbox-gate-b-v1")
        self.assertEqual(
            manifest["source_binding"],
            {
                "website_sha": "07186fc9bf9cf353fa12b74e97018d7f951d0fe6",
                "website_tree": "9621d51fd8952a6c12af5ea62b207aa07c988ac5",
                "infra_sha": "95d93f7e07ded19e482a0c6d6471fbd93fb608d8",
                "infra_tree": "1c38fdf6872a448d92e8191282525bafd3ab3410",
            },
        )
        self.assertEqual(
            manifest["resource_policy_absence"],
            {
                "s3_bucket": "NoSuchBucketPolicy",
                "ecr_repository": "RepositoryPolicyNotFoundException",
                "ecr_registry": "RegistryPolicyNotFoundException",
                "secrets_manager": "success-with-resource-policy-member-absent",
            },
        )
        self.assertEqual(len(manifest["static"]["secret_names"]), 6)
        self.assertEqual(manifest["static"]["kms"]["alias_name"], "alias/website-sandbox-runtime")
        self.assertIn("operator_identity", manifest["dynamic_binding_schema"]["required"])
        self.assertEqual(len(manifest["simulator_rows"]), 90)
        self.assertEqual(
            set(manifest["readback_manifest"]["field_schemas"]),
            {
                "cloudfront",
                "database",
                "ecr",
                "ecs_cluster",
                "ecs_service",
                "kms",
                "operator_identity",
                "role",
                "route53",
                "s3",
                "secret",
                "simulator_context_entry",
                "target_group",
                "task_definition",
                "terraform",
            },
        )
        for role in manifest["static"]["roles"].values():
            self.assertEqual(role["attached_policies"], [])
            self.assertIsNone(role["permissions_boundary"])
            self.assertEqual(role["inline_policy_names"], [role["name"]])

        command_blocks = [part.split("```", maxsplit=1)[0] for part in gate.split("```bash")[1:]]
        commands = "\n".join(command_blocks)
        for required in (
            "aws iam get-role ",
            "aws iam get-role-policy ",
            "aws kms get-key-policy ",
            "aws kms list-grants ",
            "aws s3api get-bucket-ownership-controls ",
            "aws s3api get-bucket-policy ",
            "aws ecr get-repository-policy ",
            "aws ecr get-registry-policy ",
            "aws secretsmanager get-resource-policy ",
            "aws cloudfront get-distribution ",
            "aws route53 list-resource-record-sets ",
            "gh variable get ",
            "gh api --method GET ",
            "aws iam simulate-principal-policy ",
            "sandbox/website/terraform.tfstate.tflock",
        ):
            self.assertIn(required, commands)
        for forbidden in (
            "aws s3api get-object ",
            "aws secretsmanager get-secret-value ",
            "terraform plan",
            "terraform apply",
            "gh workflow run",
            "--debug",
            "set -x",
        ):
            self.assertNotIn(forbidden, commands.lower())
        self.assertFalse(
            any(
                line.lstrip().startswith(("aws ", "gh ", "terraform "))
                for line in commands.splitlines()
            )
        )

        mode_positions = [
            gate.index(f"python -m deploy.gate_b_evidence {mode}")
            for mode in ("manifest", "bindings", "policies", "resources", "simulator", "summary")
        ]
        self.assertEqual(mode_positions, sorted(mode_positions))
        self.assertIn("never falls through", gate)
        self.assertIn("MissingContextValues=[]", gate)
        self.assertIn("BucketOwnerEnforced", gate)
        self.assertIn("registry-v2", gate)
        self.assertIn("ResourcePolicy` member absent", gate)
        self.assertIn("origin custom headers", gate)
        self.assertIn("one resource or one context key", gate)
        self.assertIn("payload_sha256", gate)
        self.assertIn('chmod 0700 -- ".tmp"', gate)
        self.assertIn("chmod 0600", gate)
        self.assertIn("ContextKeyName", gate)
        self.assertIn("grant_inventory_truncated", gate)
        self.assertIn("null permissions", gate)
        self.assertIn("code-pinned canonical SHA-256", gate)
        self.assertIn("superseded", audit)
        self.assertIn("application role as `GranteePrincipal` or `RetiringPrincipal`", audit)

        rows = manifest["simulator_rows"]
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(len(by_id), len(rows))
        for row in rows:
            self.assertIsInstance(row["action"], str)
            self.assertIsInstance(row["resource"], str)
            self.assertIn(row["expected"], {"allowed", "implicitDeny"})
            parent_id = row["negative_of"]
            if parent_id is None:
                self.assertIsNone(row["changed_axis"])
                continue
            parent = by_id[parent_id]
            self.assertEqual(parent["expected"], "allowed")
            self.assertEqual(parent["principal"], row["principal"])
            self.assertEqual(parent["action"], row["action"])
            if row["changed_axis"] == "resource":
                self.assertNotEqual(parent["resource"], row["resource"])
                self.assertEqual(parent["context"], row["context"])
            else:
                self.assertEqual(parent["resource"], row["resource"])

        unchanged_hashes = {
            "deploy/oidc_probe.py": (
                "10f38b3c3df04c763f0e09ffe6128fc9d9fe174c4f3f7f161600992fcd84e2ff"
            ),
            "deploy/oidc_claim_probe.py": (
                "693b1844e5e1c40709ce368ecb6bef22814a5912ceaa635288f0d0204a75ba28"
            ),
            "core/tests/test_deployment_oidc_probe.py": (
                "58b447bc72ddfd11359f801087ba5a2f896f0d56bff7a832c6a2768d34f74b92"
            ),
            "pyproject.toml": ("892b36023b1f984616bf1424d8bdeeeef38984b58570ecf422689814d2504be9"),
            "uv.lock": "2f429a2e0edad55dc14b3a70c0697304ce11a832c93431558073fbdca514cb10",
        }
        for relative_path, expected_hash in unchanged_hashes.items():
            actual_hash = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
            self.assertEqual(actual_hash, expected_hash)
        historical_workflow = subprocess.run(
            [
                "git",
                "show",
                f"{HISTORICAL_WORKFLOW_COMMIT}:.github/workflows/ci.yml",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(
            hashlib.sha256(historical_workflow).hexdigest(), HISTORICAL_WORKFLOW_SHA256
        )
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertNotIn("gate_b_evidence", workflow)

    def test_gate_b_operator_contract_is_exact_and_workflow_isolated(self) -> None:
        seed_path = ROOT / "deploy/gate_b_binding_seed.json"
        contract_path = ROOT / "deploy/gate_b_execution_contract.json"
        seed = json.loads(seed_path.read_text())
        contract = json.loads(contract_path.read_text())

        self.assertEqual(seed["schema_version"], 1)
        self.assertEqual(seed["seed_id"], "website-sandbox-gate-b-binding-v1")
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["contract_id"], "website-sandbox-gate-b-execution-v1")
        self.assertEqual(seed["source_binding"], contract["source_binding"])
        self.assertEqual(
            hashlib.sha256(seed_path.read_bytes()).hexdigest(),
            contract["bindings"]["seed_file_sha256"],
        )
        canonical_seed = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(
            hashlib.sha256(canonical_seed).hexdigest(),
            contract["bindings"]["seed_canonical_sha256"],
        )
        canonical_dns = json.dumps(
            seed["dns_records"], sort_keys=True, separators=(",", ":")
        ).encode()
        self.assertEqual(hashlib.sha256(canonical_dns).hexdigest(), seed["dns_records_sha256"])
        canonical_full_dns = json.dumps(
            seed["normalized_dns_full_records"], sort_keys=True, separators=(",", ":")
        ).encode()
        self.assertEqual(
            hashlib.sha256(canonical_full_dns).hexdigest(),
            seed["normalized_dns_full_records_sha256"],
        )
        self.assertEqual(seed["source_dns_full_records_bytes"], 1242)
        self.assertEqual(
            seed["source_dns_full_records_sha256"],
            "4cadb0505d61e04a7e652b7f2c2e303bfa573407a65dffc30d9fbf6d2708b0e7",
        )
        self.assertEqual(len(seed["secret_arns"]), 6)
        self.assertEqual(len(seed["dns_records"]), 6)
        self.assertEqual(len(seed["normalized_dns_full_records"]), 6)
        self.assertEqual(len(seed["github_repository_variables"]), 7)
        self.assertEqual(len(seed["github_environment_variables"]), 18)
        self.assertEqual(seed["github_repository_variables"]["SANDBOX_AUTO_DEPLOY"], "false")
        self.assertEqual(
            seed["operator_parent"],
            {
                "account_id": "817685572750",
                "forbidden_role_names": [
                    "website-sandbox-github-publisher",
                    "website-sandbox-github-deployer",
                    "website-sandbox-task-application",
                    "website-sandbox-task-execution",
                ],
                "role_arn": "arn:aws:iam::817685572750:role/phone-aws-sandbox-role",
                "role_id": "AROA34YO3VSHI2OCVBKTW",
                "session_name_pattern": "^phone-sandbox-[0-9a-f]{8}$",
            },
        )

        graph = contract["graph"]
        operations = graph["operations"]
        self.assertEqual(graph["provider_operation_count"], 174)
        self.assertEqual(graph["non_simulator_operation_count"], 84)
        self.assertEqual(graph["aws_readback_operation_count"], 58)
        self.assertEqual(graph["github_operation_count"], 26)
        self.assertEqual(graph["simulator_operation_count"], 90)
        self.assertEqual(graph["expected_nonzero_count"], 5)
        self.assertEqual(len(operations), 84)
        self.assertEqual([item["sequence"] for item in operations], list(range(1, 85)))
        self.assertEqual(len({item["id"] for item in operations}), 84)
        self.assertEqual(operations[0]["id"], "sts-caller")
        self.assertEqual(operations[0]["phase"], "identity")
        self.assertEqual(
            operations[0]["argv"][1:],
            ["sts", "get-caller-identity", "--no-cli-pager", "--output", "json"],
        )
        self.assertEqual(sum(item["provider"] == "aws" for item in operations), 58)
        self.assertEqual(sum(item["provider"] == "github" for item in operations), 26)
        self.assertEqual(
            sum(item["expected"]["exit_code"] == "nonzero" for item in operations),
            5,
        )
        expected_absences = {
            "s3-policy": "NoSuchBucketPolicy",
            "s3-lock-object": "404",
            "ecr-zero-digest": "ImageNotFoundException",
            "ecr-repository-policy": "RepositoryPolicyNotFoundException",
            "ecr-registry-policy": "RegistryPolicyNotFoundException",
        }
        self.assertEqual(
            {
                item["id"]: item["expected"]["error_code"]
                for item in operations
                if item["expected"]["exit_code"] == "nonzero"
            },
            expected_absences,
        )
        self.assertEqual(
            graph["simulator_recipe"]["source_sha256"],
            "838fa6daca8b0760350e13a60e5e42fa059cbf51f5526749098b5a6aeafd9ad1",
        )
        self.assertEqual(graph["simulator_recipe"]["count"], 90)
        self.assertEqual(contract["credential_process"]["resolve_count"], 1)
        self.assertEqual(contract["credential_process"]["minimum_ttl_seconds_at_start"], 840)
        self.assertEqual(contract["credential_process"]["hard_reserve_seconds"], 120)
        self.assertFalse(contract["credential_process"]["refresh_allowed"])
        self.assertEqual(
            contract["child_environments"]["aws_fixed_values"]["AWS_CONFIG_FILE"],
            "/dev/null",
        )
        self.assertEqual(
            contract["child_environments"]["aws_fixed_values"]["AWS_SHARED_CREDENTIALS_FILE"],
            "/dev/null",
        )
        self.assertFalse(contract["limits"]["resume_allowed"])
        self.assertEqual(contract["limits"]["retry_count"], 0)
        self.assertTrue(contract["limits"]["require_owner_euid"])
        self.assertTrue(contract["limits"]["require_single_link"])
        self.assertEqual(contract["limits"]["success_stderr"], "empty")
        self.assertEqual(contract["limits"]["accepted_error_stdout"], "empty")

        commands = "\n".join(" ".join(item["argv"]) for item in operations).lower()
        for forbidden in (
            " s3api get-object ",
            " secretsmanager get-secret-value ",
            "terraform ",
            "workflow run",
            "--debug",
            "--policy-input-list",
            "--resource-policy",
            "--caller-arn",
        ):
            self.assertNotIn(forbidden, commands)

        frozen_hashes = {
            "_docs/audits/2026-08-07-oidc-denial-sentinels.md": (
                "8c4e9b6701d670bac87853254d66b2399e379e79b072409690c68acb3a3e9696"
            ),
            "deploy/gate_b_evidence.py": (
                "52d93e9b2757c75c4ec633ac2d903fdce658b107481382025c22bf0f9d276b68"
            ),
            "deploy/gate_b_manifest.json": (
                "c96f710091adfc0e9c85ed02329238f374118766f46f630ea956794015987985"
            ),
            "core/tests/test_gate_b_evidence.py": (
                "4dd65a576f3bd3d3bd2dff41170ee161f45ffe5362a4e4e1f8af0feabc081027"
            ),
            "deploy/oidc_probe.py": (
                "10f38b3c3df04c763f0e09ffe6128fc9d9fe174c4f3f7f161600992fcd84e2ff"
            ),
            "deploy/oidc_claim_probe.py": (
                "693b1844e5e1c40709ce368ecb6bef22814a5912ceaa635288f0d0204a75ba28"
            ),
            "core/tests/test_deployment_oidc_probe.py": (
                "58b447bc72ddfd11359f801087ba5a2f896f0d56bff7a832c6a2768d34f74b92"
            ),
            "pyproject.toml": ("892b36023b1f984616bf1424d8bdeeeef38984b58570ecf422689814d2504be9"),
            "uv.lock": "2f429a2e0edad55dc14b3a70c0697304ce11a832c93431558073fbdca514cb10",
        }
        for relative_path, expected_hash in frozen_hashes.items():
            self.assertEqual(
                hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest(),
                expected_hash,
            )
        historical_workflow = subprocess.run(
            [
                "git",
                "show",
                f"{HISTORICAL_WORKFLOW_COMMIT}:.github/workflows/ci.yml",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(
            hashlib.sha256(historical_workflow).hexdigest(), HISTORICAL_WORKFLOW_SHA256
        )

        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        audit = (ROOT / "_docs/audits/2026-08-08-gate-b-operator-execution.md").read_text()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        for required in (
            "#85 sealed operator procedure",
            "exactly 174 evidence operations",
            "resolved exactly once",
            "execution-attestation.json",
            "does not authorize",
        ):
            self.assertIn(required, f"{runbook}\n{audit}")
        self.assertNotIn("gate_b_operator", workflow)
        self.assertNotIn("gate_b_assembler", workflow)

    def test_runbook_reconciles_only_after_all_b_exercises_and_final_promotion(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()
        drills_position = runbook.index("## Controlled failure drills")
        rollback_position = runbook.index("## Manual immutable rollback")
        reconciliation_position = runbook.index("## Final release-B Terraform reconciliation")

        self.assertLess(drills_position, rollback_position)
        self.assertLess(rollback_position, reconciliation_position)
        reconciliation = runbook[reconciliation_position:]
        self.assertIn("Never reconcile release A or an intermediate release state", reconciliation)
        self.assertIn("post-mutation B smoke", reconciliation)
        self.assertIn("clean promotion of the already-built B digest", reconciliation)
        self.assertIn("second and final plan", reconciliation)
        self.assertIn("e2b93beb1544170b6177ba55ea8fd6530b2e57a3", reconciliation)
        self.assertIn("DEVELOPMENT_AUTO_DEPLOY=true", reconciliation)
        self.assertIn(
            "repository-level variable must exist and be exactly `DEVELOPMENT_AUTO_DEPLOY=false`",
            reconciliation,
        )
        self.assertIn(
            "An absent repository variable is not a disabled state",
            " ".join(reconciliation.split()),
        )
        self.assertIn("Never delete this repository variable", reconciliation)
        self.assertNotIn("gh variable delete DEVELOPMENT_AUTO_DEPLOY", runbook)
        self.assertNotIn("After first bootstrap succeeds, set Terraform", runbook)

    def test_runbook_documents_build_once_records_reuse_and_safe_auto_capture(self) -> None:
        runbook = (ROOT / "_docs/runbooks/development-release.md").read_text()

        self.assertIn(
            "development-published-image-e2b93beb1544170b6177ba55ea8fd6530b2e57a3", runbook
        )
        self.assertIn("only release-B build/push run", runbook)
        self.assertIn("performs no Docker build, load, login, pull, or push", runbook)
        self.assertIn("is **not** a successful or rollback-eligible release record", runbook)
        self.assertIn("## Automatic deployment after bootstrap", runbook)
        self.assertIn("queued push therefore makes no AWS call", runbook)
        self.assertIn("distinct active-service-pair schema", runbook)
        self.assertIn("never guesses a cross-family revision", runbook)
        self.assertIn("prevents publication as well as deployment", runbook)
        self.assertIn("before migration", runbook)
        self.assertIn("again after migration", runbook)

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
        self.assertIn("Rollback requires reuse with no failure injection", workflow)
        self.assertIn("published-image record independently of deployment", workflow)
        self.assertNotIn("terraform apply", workflow)

    def test_release_image_builds_and_verifies_the_runtime_static_manifest(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        manifest_gate = workflow.split(
            "- name: Verify the built runtime static manifest", maxsplit=1
        )[1].split("- name: Smoke-test liveness without publishing", maxsplit=1)[0]

        self.assertIn("DJANGO_SETTINGS_MODULE=website.settings.collectstatic", dockerfile)
        self.assertNotIn(
            "DJANGO_SETTINGS_MODULE=website.settings.test "
            "uv run --no-sync python manage.py collectstatic",
            dockerfile,
        )
        self.assertIn("python -m scripts.verify_static_manifest", manifest_gate)
        self.assertIn("website.settings.collectstatic", manifest_gate)
        self.assertIn("website.settings.test", manifest_gate)
        self.assertIn("malformed.json", manifest_gate)
        self.assertIn("missing-entry.json", manifest_gate)
        self.assertIn("target=/app/staticfiles,readonly", manifest_gate)
        self.assertLess(
            workflow.index("- name: Verify the built runtime static manifest"),
            workflow.index("- name: Preserve the one tested image"),
        )
        self.assertIn(
            "curl --fail --silent --output /dev/null http://127.0.0.1:8000/unified/",
            workflow,
        )

    def test_serving_entrypoint_never_runs_migrations(self) -> None:
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertNotIn("migrate", entrypoint)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("org.opencontainers.image.revision", dockerfile)


class FakeWorkerClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current += seconds


class FakeWorkerServiceEcs:
    def __init__(
        self,
        clock: FakeWorkerClock,
        service: dict[str, object],
        *,
        complete_at: float | None = None,
    ) -> None:
        self.clock = clock
        self.service = service
        self.complete_at = complete_at
        self.describe_calls = 0

    def describe_services(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.describe_calls += 1
        service = json.loads(json.dumps(self.service))
        if self.complete_at is not None and self.clock.current >= self.complete_at:
            deployments = self.service["deployments"]
            assert isinstance(deployments, list)
            first_deployment = deployments[0]
            assert isinstance(first_deployment, dict)
            service.update({"desiredCount": 1, "runningCount": 1, "pendingCount": 0})
            service["deployments"] = [
                {
                    "status": "PRIMARY",
                    "id": first_deployment["id"],
                    **(
                        {"taskDefinition": service["taskDefinition"]}
                        if "taskDefinition" in service
                        else {}
                    ),
                    "desiredCount": 1,
                    "runningCount": 1,
                    "pendingCount": 0,
                    "failedTasks": 0,
                    "rolloutState": "COMPLETED",
                }
            ]
        return {"failures": [], "services": [service]}


class FakeServiceSequenceEcs:
    def __init__(
        self,
        services: list[dict[str, object]],
        *,
        update_response: dict[str, object] | None = None,
        clock: FakeWorkerClock | None = None,
        update_delay: float = 0,
        describe_delays: list[float] | None = None,
    ) -> None:
        self.services = services
        self.update_response = update_response
        self.describe_calls = 0
        self.update_calls = 0
        self.clock = clock
        self.update_delay = update_delay
        self.describe_delays = describe_delays or []

    def describe_services(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        call_index = self.describe_calls
        index = min(call_index, len(self.services) - 1)
        self.describe_calls += 1
        if self.clock is not None and call_index < len(self.describe_delays):
            self.clock.current += self.describe_delays[call_index]
        return {"failures": [], "services": [json.loads(json.dumps(self.services[index]))]}

    def update_service(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.update_calls += 1
        if self.clock is not None:
            self.clock.current += self.update_delay
        return json.loads(json.dumps(self.update_response))


class WorkerStabilizationContractTests(SimpleTestCase):
    WEB_A = "arn:aws:ecs:eu-west-1:817685572750:task-definition/web:1"
    WEB_B = "arn:aws:ecs:eu-west-1:817685572750:task-definition/web:2"
    WEB_C = "arn:aws:ecs:eu-west-1:817685572750:task-definition/web:3"
    WORKER_A = "arn:aws:ecs:eu-west-1:817685572750:task-definition/worker:1"
    WORKER_B = "arn:aws:ecs:eu-west-1:817685572750:task-definition/worker:2"

    @staticmethod
    def in_progress_service() -> dict[str, object]:
        return {
            "serviceName": "worker",
            "taskDefinition": WorkerStabilizationContractTests.WORKER_B,
            "desiredCount": 1,
            "runningCount": 1,
            "pendingCount": 0,
            "deployments": [
                {
                    "status": "PRIMARY",
                    "id": "ecs-svc/worker-b",
                    "taskDefinition": WorkerStabilizationContractTests.WORKER_B,
                    "desiredCount": 1,
                    "runningCount": 1,
                    "pendingCount": 0,
                    "failedTasks": 0,
                    "rolloutState": "IN_PROGRESS",
                }
            ],
        }

    def gateway(
        self,
        ecs: FakeWorkerServiceEcs | FakeServiceSequenceEcs,
    ) -> AwsReleaseGateway:
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
            timeout_seconds=MAX_STAGE_TIMEOUT_SECONDS,
            web_stabilization_timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS,
            worker_stabilization_timeout_seconds=WORKER_STABILIZATION_TIMEOUT_SECONDS,
            poll_seconds=10,
        )
        gateway.ecs = ecs
        return gateway

    def wait(self, gateway: AwsReleaseGateway, clock: FakeWorkerClock) -> None:
        receipt = ServiceUpdateReceipt(
            "worker",
            "worker",
            ServiceTarget(self.WORKER_B, 1),
            "ecs-svc/worker-b",
            (
                ServicePredecessor(
                    ServiceTarget(self.WORKER_A, 1),
                    "ecs-svc/worker-a",
                    "terminal",
                ),
            ),
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            gateway.wait_service_stable(
                receipt,
                worker_singleton=True,
                timeout_seconds=WORKER_STABILIZATION_TIMEOUT_SECONDS,
            )

    def test_slow_singleton_completes_after_180_seconds_inside_worker_budget(self) -> None:
        clock = FakeWorkerClock()
        ecs = FakeWorkerServiceEcs(clock, self.in_progress_service(), complete_at=190)

        self.wait(self.gateway(ecs), clock)

        self.assertEqual(clock.current, 190)
        self.assertGreater(clock.current, MAX_STAGE_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 20)

    def test_singleton_in_progress_through_worker_max_times_out_exactly(self) -> None:
        clock = FakeWorkerClock()
        ecs = FakeWorkerServiceEcs(clock, self.in_progress_service())
        with self.assertRaisesMessage(ReleaseContractError, "deadline expired"):
            self.wait(self.gateway(ecs), clock)

        self.assertEqual(clock.current, WORKER_STABILIZATION_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 43)

    def wait_web(self, gateway: AwsReleaseGateway, clock: FakeWorkerClock) -> None:
        receipt = ServiceUpdateReceipt(
            "web",
            "web",
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            (
                ServicePredecessor(
                    ServiceTarget(self.WEB_A, 1),
                    "ecs-svc/web-a",
                    "terminal",
                ),
            ),
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            gateway.wait_service_stable(
                receipt,
                timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS,
            )

    @classmethod
    def web_in_progress_service(cls) -> dict[str, object]:
        service = cls.in_progress_service()
        service["serviceName"] = "web"
        service["taskDefinition"] = cls.WEB_B
        deployments = service["deployments"]
        assert isinstance(deployments, list)
        deployments[0]["id"] = "ecs-svc/web-b"
        deployments[0]["taskDefinition"] = cls.WEB_B
        return service

    def test_web_completion_on_inclusive_final_observation_succeeds_without_later_poll(
        self,
    ) -> None:
        clock = FakeWorkerClock()
        terminal = self.web_in_progress_service()
        terminal.update({"desiredCount": 1, "runningCount": 1, "pendingCount": 0})
        deployments = terminal["deployments"]
        assert isinstance(deployments, list)
        deployments[0].update(
            {
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "failedTasks": 0,
                "rolloutState": "COMPLETED",
            }
        )
        ecs = FakeServiceSequenceEcs(
            [terminal],
            clock=clock,
            describe_delays=[WEB_STABILIZATION_TIMEOUT_SECONDS],
        )

        self.wait_web(self.gateway(ecs), clock)

        self.assertEqual(clock.current, WEB_STABILIZATION_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_web_in_progress_on_final_observation_fails_without_another_poll(self) -> None:
        clock = FakeWorkerClock()
        ecs = FakeWorkerServiceEcs(clock, self.web_in_progress_service())

        with self.assertRaisesMessage(ReleaseContractError, "deadline expired"):
            self.wait_web(self.gateway(ecs), clock)

        self.assertEqual(clock.current, WEB_STABILIZATION_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 25)
        self.assertEqual(len(clock.sleep_calls), 24)

    def test_known_bad_web_states_fail_immediately(self) -> None:
        base = self.web_in_progress_service()
        primary = base["deployments"]
        assert isinstance(primary, list)
        cases = {
            "missing primary": base | {"deployments": []},
            "duplicate primary": base | {"deployments": primary * 2},
            "failed rollout": base | {"deployments": [primary[0] | {"rolloutState": "FAILED"}]},
            "crashing task": base | {"deployments": [primary[0] | {"failedTasks": 1}]},
            "wrong service task definition": base | {"taskDefinition": self.WEB_C},
            "mixed primary task definition": base
            | {"deployments": [primary[0] | {"taskDefinition": self.WEB_A}]},
            "wrong desired count": base | {"desiredCount": 2},
            "completed with inexact counts": base
            | {"deployments": [primary[0] | {"rolloutState": "COMPLETED", "runningCount": 0}]},
        }
        for name, service in cases.items():
            clock = FakeWorkerClock()
            ecs = FakeWorkerServiceEcs(clock, service)
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                self.wait_web(self.gateway(ecs), clock)
            self.assertEqual(clock.current, 0)
            self.assertEqual(ecs.describe_calls, 1)

    def test_missing_or_malformed_required_web_counts_fail_immediately(self) -> None:
        invalid_values = {
            "missing": None,
            "boolean": True,
            "string": "1",
            "negative": -1,
        }
        for location in ("service", "primary"):
            for field in ("desiredCount", "runningCount", "pendingCount"):
                for invalid_name, invalid_value in invalid_values.items():
                    service = json.loads(json.dumps(self.web_in_progress_service()))
                    document = service
                    if location == "primary":
                        deployments = service["deployments"]
                        assert isinstance(deployments, list)
                        document = deployments[0]
                    assert isinstance(document, dict)
                    if invalid_name == "missing":
                        document.pop(field)
                    else:
                        document[field] = invalid_value
                    clock = FakeWorkerClock()
                    ecs = FakeWorkerServiceEcs(clock, service)
                    with (
                        self.subTest(location=location, field=field, invalid=invalid_name),
                        self.assertRaisesMessage(ReleaseContractError, field),
                    ):
                        self.wait_web(self.gateway(ecs), clock)
                    self.assertEqual(clock.current, 0)
                    self.assertEqual(ecs.describe_calls, 1)

    def test_malformed_optional_failed_task_count_fails_immediately(self) -> None:
        for invalid in (True, "1", -1):
            service = json.loads(json.dumps(self.web_in_progress_service()))
            deployments = service["deployments"]
            assert isinstance(deployments, list)
            deployments[0]["failedTasks"] = invalid
            clock = FakeWorkerClock()
            ecs = FakeWorkerServiceEcs(clock, service)
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesMessage(ReleaseContractError, "failedTasks"),
            ):
                self.wait_web(self.gateway(ecs), clock)
            self.assertEqual(clock.current, 0)
            self.assertEqual(ecs.describe_calls, 1)

    def test_known_bad_worker_states_fail_immediately(self) -> None:
        base = self.in_progress_service()
        primary = base["deployments"]
        assert isinstance(primary, list)
        cases = {
            "missing primary": base | {"deployments": []},
            "duplicate primary": base | {"deployments": primary * 2},
            "failed primary": base | {"deployments": [primary[0] | {"rolloutState": "FAILED"}]},
            "singleton breach": base | {"runningCount": 1, "pendingCount": 1},
        }
        for name, service in cases.items():
            clock = FakeWorkerClock()
            ecs = FakeWorkerServiceEcs(clock, service)
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                self.wait(self.gateway(ecs), clock)
            self.assertEqual(clock.current, 0)
            self.assertEqual(ecs.describe_calls, 1)

    def test_worker_override_is_bounded_and_restricted_to_singleton_waits(self) -> None:
        clock = FakeWorkerClock()
        gateway = self.gateway(FakeWorkerServiceEcs(clock, self.in_progress_service()))
        cases = (
            ("web", True, 420),
            ("worker", False, 420),
            ("worker", True, 421),
            ("worker", True, 0),
        )
        for workload, singleton, timeout in cases:
            target_arn = self.WORKER_B if workload == "worker" else self.WEB_B
            prior_arn = self.WORKER_A if workload == "worker" else self.WEB_A
            receipt = ServiceUpdateReceipt(
                workload,
                workload,
                ServiceTarget(target_arn, 1),
                f"ecs-svc/{workload}-b",
                (
                    ServicePredecessor(
                        ServiceTarget(prior_arn, 1),
                        f"ecs-svc/{workload}-a",
                        "terminal",
                    ),
                ),
            )
            with (
                self.subTest(
                    workload=workload,
                    singleton=singleton,
                    timeout=timeout,
                ),
                self.assertRaises(ReleaseContractError),
            ):
                gateway.wait_service_stable(
                    receipt,
                    worker_singleton=singleton,
                    timeout_seconds=timeout,
                )


class ServiceReceiptAdoptionContractTests(SimpleTestCase):
    WEB_A = WorkerStabilizationContractTests.WEB_A
    WEB_B = WorkerStabilizationContractTests.WEB_B
    WEB_C = WorkerStabilizationContractTests.WEB_C

    @staticmethod
    def deployment(
        task_definition: str,
        desired_count: int,
        deployment_id: str,
        *,
        status: str = "PRIMARY",
        rollout_state: str = "IN_PROGRESS",
        running_count: int = 0,
        pending_count: int = 1,
        failed_tasks: int = 0,
    ) -> dict[str, object]:
        return {
            "id": deployment_id,
            "status": status,
            "taskDefinition": task_definition,
            "desiredCount": desired_count,
            "runningCount": running_count,
            "pendingCount": pending_count,
            "failedTasks": failed_tasks,
            "rolloutState": rollout_state,
        }

    @classmethod
    def service(
        cls,
        service_target: ServiceTarget,
        primary_target: ServiceTarget,
        primary_id: str,
        *,
        service_name: object = "web",
        service_running: int = 0,
        service_pending: int = 1,
        primary_running: int = 0,
        primary_pending: int = 1,
        rollout_state: str = "IN_PROGRESS",
        failed_tasks: int = 0,
        deployments: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "serviceName": service_name,
            "taskDefinition": service_target.task_definition_arn,
            "desiredCount": service_target.desired_count,
            "runningCount": service_running,
            "pendingCount": service_pending,
            "deployments": deployments
            if deployments is not None
            else [
                cls.deployment(
                    primary_target.task_definition_arn,
                    primary_target.desired_count,
                    primary_id,
                    rollout_state=rollout_state,
                    running_count=primary_running,
                    pending_count=primary_pending,
                    failed_tasks=failed_tasks,
                )
            ],
        }

    @classmethod
    def receipt(
        cls,
        *,
        target: ServiceTarget | None = None,
        target_id: str = "ecs-svc/web-b",
        predecessor: ServicePredecessor | None = None,
        attempted: ServicePredecessor | None = None,
    ) -> ServiceUpdateReceipt:
        target = target or ServiceTarget(cls.WEB_B, 1)
        predecessor = predecessor or ServicePredecessor(
            ServiceTarget(cls.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        predecessors = (predecessor,) + ((attempted,) if attempted is not None else ())
        return ServiceUpdateReceipt("web", "web", target, target_id, predecessors)

    @staticmethod
    def gateway(ecs: FakeServiceSequenceEcs, *, poll_seconds: int = 10) -> AwsReleaseGateway:
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
            poll_seconds=poll_seconds,
        )
        gateway.ecs = ecs
        return gateway

    def draining_rejection_cases(
        self,
        candidate: dict[str, object],
        predecessor: dict[str, object],
        *,
        include_missing_id: bool = True,
    ) -> dict[str, list[dict[str, object]]]:
        candidate_id = candidate["id"]
        idless = predecessor.copy()
        idless.pop("id")
        cases = {
            "candidate": [candidate | {"status": "DRAINING"}, predecessor],
            "candidate ID reused": [candidate, predecessor | {"id": candidate_id}],
            "unknown ID": [candidate, predecessor | {"id": "ecs-svc/unknown"}],
            "cross-paired task definition": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_B},
            ],
            "alien task definition": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_C},
            ],
            "nonzero desired": [candidate, predecessor | {"desiredCount": 1}],
            "nonzero running": [candidate, predecessor | {"runningCount": 1}],
            "nonzero pending": [candidate, predecessor | {"pendingCount": 1}],
            "failed task": [candidate, predecessor | {"failedTasks": 1}],
            "failed rollout": [candidate, predecessor | {"rolloutState": "FAILED"}],
            "duplicate projection": [candidate, predecessor, predecessor.copy()],
        }
        if include_missing_id:
            cases["missing ID"] = [candidate, idless]
        return cases

    def verify_terminal_web_service(
        self,
        web_service: dict[str, object],
        web_predecessors: tuple[ServicePredecessor, ...],
        *,
        worker_deployments: list[dict[str, object]] | None = None,
    ) -> None:
        worker_target = ServiceTarget(WorkerStabilizationContractTests.WORKER_B, 1)
        worker_service = self.service(
            worker_target,
            worker_target,
            "ecs-svc/worker-b",
            service_name="worker",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
            deployments=worker_deployments,
        )
        snapshots = {
            "web": ServiceSnapshot(
                "web",
                self.WEB_B,
                1,
                1,
                0,
                None,
                None,
                "ecs-svc/web-b",
            ),
            "worker": ServiceSnapshot(
                "worker",
                worker_target.task_definition_arn,
                1,
                1,
                0,
                None,
                None,
                "ecs-svc/worker-b",
            ),
        }
        services = {"web": web_service, "worker": worker_service}
        task_definitions = {
            "web": self.WEB_B,
            "worker": worker_target.task_definition_arn,
        }
        gateway = self.gateway(FakeServiceSequenceEcs([]))
        with (
            patch.object(
                gateway,
                "capture_service",
                side_effect=lambda workload: snapshots[workload],
            ),
            patch.object(
                gateway,
                "_service",
                side_effect=lambda workload: services[workload],
            ),
            patch.object(
                gateway,
                "_active_tasks",
                side_effect=lambda workload: [{"taskDefinitionArn": task_definitions[workload]}],
            ),
        ):
            gateway.verify_terminal(
                task_definitions,
                {"web": 1, "worker": 1},
                None,
                {"web": "ecs-svc/web-b", "worker": "ecs-svc/worker-b"},
                {"web": web_predecessors, "worker": ()},
            )

    def test_immutable_receipt_types_and_phase_shape_fail_closed(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(ServiceTarget(self.WEB_A, 1), "ecs-svc/web-a", "terminal")
        attempted = ServicePredecessor(target, "ecs-svc/web-attempted", "attempted")
        invalid_receipts: tuple[tuple[Any, Any, Any], ...] = (
            (True, target, (terminal,)),
            ("   ", target, (terminal,)),
            ("web", object(), (terminal,)),
            ("web", target, [terminal]),
            ("web", target, (object(),)),
            ("web", target, ()),
            ("web", target, (attempted,)),
            ("web", target, (terminal, terminal)),
            ("web", target, (terminal, attempted, attempted)),
        )
        for identity, selected_target, predecessors in invalid_receipts:
            with self.subTest(identity=identity, predecessors=predecessors):
                with self.assertRaises(ReleaseContractError):
                    ServiceUpdateReceipt(
                        "web",
                        identity,
                        selected_target,
                        "ecs-svc/web-b",
                        predecessors,
                    )
        with self.assertRaises(ReleaseContractError):
            ServicePredecessor(object(), "ecs-svc/web-a", "terminal")  # type: ignore[arg-type]
        with self.assertRaises(ReleaseContractError):
            ServiceUpdateReceipt(
                "web",
                "web",
                target,
                "ecs-svc/web-b",
                (terminal,),
                "raw-provider-payload",  # type: ignore[arg-type]
            )

    def test_update_response_returns_exact_receipt_and_rejects_invalid_shapes(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(ServiceTarget(self.WEB_A, 1), "ecs-svc/web-a", "terminal")
        candidate = self.deployment(self.WEB_B, 1, "ecs-svc/web-b")
        old = self.deployment(
            self.WEB_A,
            1,
            "ecs-svc/web-a",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        valid_service = self.service(
            target,
            target,
            "ecs-svc/web-b",
            deployments=[candidate, old],
        )
        ecs = FakeServiceSequenceEcs([valid_service], update_response={"service": valid_service})
        receipt = self.gateway(ecs).update_service("web", target, (terminal,))
        self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-b")
        self.assertEqual(receipt.target, target)
        self.assertEqual(receipt.binding_reason, "complete_receipt")
        self.assertEqual(ecs.describe_calls, 0)

        invalid_services: dict[str, dict[str, object]] = {
            "wrong service": valid_service | {"serviceName": "other"},
            "boolean service count": valid_service | {"desiredCount": True},
            "no primary": valid_service | {"deployments": [candidate | {"status": "ACTIVE"}, old]},
            "multiple primary": valid_service
            | {"deployments": [candidate, old | {"status": "PRIMARY"}]},
            "empty target id": valid_service | {"deployments": [candidate | {"id": ""}, old]},
            "reused old id": valid_service | {"deployments": [candidate | {"id": "ecs-svc/web-a"}]},
            "third deployment": valid_service
            | {
                "deployments": [
                    candidate,
                    old,
                    self.deployment(
                        self.WEB_C,
                        1,
                        "ecs-svc/web-c",
                        status="ACTIVE",
                    ),
                ]
            },
            "multiple new candidates": valid_service
            | {
                "deployments": [
                    candidate,
                    old,
                    self.deployment(
                        self.WEB_B,
                        1,
                        "ecs-svc/web-b-other",
                        status="ACTIVE",
                    ),
                ]
            },
            "malformed old count": valid_service
            | {"deployments": [candidate, old | {"pendingCount": True}]},
            "target failed": valid_service
            | {"deployments": [candidate | {"rolloutState": "FAILED"}, old]},
            "target failed tasks": valid_service
            | {"deployments": [candidate | {"failedTasks": 1}, old]},
            "target completed inexact": valid_service
            | {"deployments": [candidate | {"rolloutState": "COMPLETED"}, old]},
        }
        for name, service in invalid_services.items():
            candidate_ecs = FakeServiceSequenceEcs([service], update_response={"service": service})
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                self.gateway(candidate_ecs).update_service("web", target, (terminal,))

        same_target = terminal.target
        old_completed = self.service(
            same_target,
            same_target,
            terminal.primary_deployment_id,
            service_running=1,
            service_pending=1,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        same_target_ecs = FakeServiceSequenceEcs(
            [old_completed], update_response={"service": old_completed}
        )
        clock = FakeWorkerClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaisesMessage(ReleaseContractError, "deadline expired"),
        ):
            self.gateway(same_target_ecs).update_service(
                "web",
                same_target,
                (terminal,),
                deadline=1.0,
            )
        self.assertEqual(same_target_ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [1])

    def test_update_rejects_unsafe_predecessor_shape_before_side_effect(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        service = self.service(target, target, "ecs-svc/web-b")
        for predecessors in ((),):
            ecs = FakeServiceSequenceEcs([service], update_response={"service": service})
            with self.assertRaises(ReleaseContractError):
                self.gateway(ecs).update_service("web", target, predecessors)
            self.assertEqual(ecs.update_calls, 0)

    def test_exact_zero_count_initialization_binds_and_only_terminal_state_succeeds(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        old = self.deployment(
            self.WEB_A,
            1,
            "ecs-svc/web-a",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        initialization = self.service(
            target,
            ServiceTarget(self.WEB_B, 0),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=0,
            primary_pending=0,
            deployments=[candidate, old],
        )
        terminal_service = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [initialization, terminal_service],
            update_response={"service": initialization},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            deadline = gateway.service_stabilization_deadline(WEB_STABILIZATION_TIMEOUT_SECONDS)
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=deadline,
                timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS,
            )
            self.assertEqual(receipt.target, target)
            self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-b")
            self.assertEqual(receipt.binding_reason, "zero_count_initialization")
            self.assertEqual(ecs.describe_calls, 0)
            gateway.wait_service_stable(
                receipt,
                timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS,
                deadline=deadline,
            )
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [10])

    def test_partial_acknowledgement_reconciles_immediately_on_the_original_deadline(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        old = self.service(
            terminal.target,
            terminal.target,
            terminal.primary_deployment_id,
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        crossed = self.service(
            terminal.target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [old, crossed],
            update_response={"service": {"serviceName": "web"}},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            deadline = 30.0
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=deadline,
                timeout_seconds=30,
            )
        self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-b")
        self.assertEqual(receipt.binding_reason, "partial_acknowledgement_reconciled")
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [10])
        self.assertEqual(clock.current, 10)

    def test_bound_zero_count_initialization_times_out_without_becoming_success(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        initialization = self.service(
            target,
            ServiceTarget(self.WEB_B, 0),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=0,
            primary_pending=0,
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [initialization],
            update_response={"service": initialization},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=20.0,
                timeout_seconds=20,
            )
            with self.assertRaisesMessage(ReleaseContractError, "deadline expired"):
                gateway.wait_service_stable(
                    receipt,
                    timeout_seconds=20,
                    deadline=20.0,
                )
        self.assertEqual(clock.current, 20)
        self.assertEqual(ecs.describe_calls, 3)
        self.assertEqual(clock.sleep_calls, [10, 10])

    def test_partial_acknowledgement_expires_on_inclusive_deadline_without_identity(
        self,
    ) -> None:
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        stale = self.service(
            terminal.target,
            terminal.target,
            terminal.primary_deployment_id,
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([stale], update_response={})
        gateway = self.gateway(ecs, poll_seconds=17)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaisesMessage(ReleaseContractError, "deadline expired") as caught,
        ):
            gateway.update_service(
                "web",
                ServiceTarget(self.WEB_B, 1),
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 3)
        self.assertEqual(clock.sleep_calls, [17, 13])
        self.assertEqual(clock.current, 30)

    def test_zero_count_candidate_without_id_is_never_synthesized_from_the_request(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        candidate.pop("id")
        old = self.deployment(
            self.WEB_A,
            1,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        service = self.service(
            target,
            ServiceTarget(self.WEB_B, 0),
            "unused",
            service_running=1,
            service_pending=0,
            deployments=[candidate, old],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([service], update_response={"service": service})
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaisesMessage(ReleaseContractError, "deadline expired"),
        ):
            self.gateway(ecs).update_service(
                "web",
                target,
                (terminal,),
                deadline=1.0,
            )
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [1])

    def test_zero_count_candidate_contradictions_fail_on_the_first_response(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        old = self.deployment(
            self.WEB_A,
            1,
            "ecs-svc/web-a",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        base_candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        cases = {
            "service zero": self.service(
                ServiceTarget(self.WEB_B, 0),
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=0,
                service_pending=0,
                primary_running=0,
                primary_pending=0,
                deployments=[base_candidate, old],
            ),
            "candidate running": self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[base_candidate | {"runningCount": 1}, old],
            ),
            "candidate pending": self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[base_candidate | {"pendingCount": 1}, old],
            ),
            "candidate failed": self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[base_candidate | {"failedTasks": 1}, old],
            ),
            "candidate completed": self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[base_candidate | {"rolloutState": "COMPLETED"}, old],
            ),
        }
        for name, service in cases.items():
            ecs = FakeServiceSequenceEcs([], update_response={"service": service})
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                self.gateway(ecs).update_service("web", target, (terminal,))
            self.assertEqual(ecs.describe_calls, 0)

    def test_present_malformed_acknowledgement_counts_fail_without_reconciliation(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        valid = self.service(target, target, "ecs-svc/web-b")
        for location, fields in (
            ("service", ("desiredCount", "runningCount", "pendingCount")),
            (
                "candidate",
                ("desiredCount", "runningCount", "pendingCount", "failedTasks"),
            ),
        ):
            for field in fields:
                for malformed in (True, "1", -1):
                    service = json.loads(json.dumps(valid))
                    document = service
                    if location == "candidate":
                        deployments = service["deployments"]
                        assert isinstance(deployments, list)
                        document = deployments[0]
                    assert isinstance(document, dict)
                    document[field] = malformed
                    ecs = FakeServiceSequenceEcs([], update_response={"service": service})
                    with (
                        self.subTest(
                            location=location,
                            field=field,
                            malformed=malformed,
                        ),
                        self.assertRaises(ReleaseContractError),
                    ):
                        self.gateway(ecs).update_service("web", target, (terminal,))
                    self.assertEqual(ecs.describe_calls, 0)

    def test_bootstrap_and_same_target_force_new_accept_only_new_initialization_id(
        self,
    ) -> None:
        for name, predecessor_target, target in (
            (
                "bootstrap",
                ServiceTarget(self.WEB_A, 0),
                ServiceTarget(self.WEB_B, 1),
            ),
            (
                "same-target",
                ServiceTarget(self.WEB_A, 1),
                ServiceTarget(self.WEB_A, 1),
            ),
        ):
            terminal = ServicePredecessor(
                predecessor_target,
                f"ecs-svc/{name}-old",
                "terminal",
            )
            candidate_id = f"ecs-svc/{name}-new"
            candidate = self.deployment(
                target.task_definition_arn,
                0,
                candidate_id,
                running_count=0,
                pending_count=0,
            )
            old = self.deployment(
                predecessor_target.task_definition_arn,
                predecessor_target.desired_count,
                terminal.primary_deployment_id,
                status="ACTIVE",
                rollout_state="COMPLETED",
                running_count=predecessor_target.desired_count,
                pending_count=0,
            )
            initialization = self.service(
                target,
                ServiceTarget(target.task_definition_arn, 0),
                candidate_id,
                service_running=predecessor_target.desired_count,
                service_pending=0,
                primary_running=0,
                primary_pending=0,
                deployments=[candidate, old],
            )
            ecs = FakeServiceSequenceEcs([], update_response={"service": initialization})
            with self.subTest(name=name):
                receipt = self.gateway(ecs).update_service("web", target, (terminal,))
                self.assertEqual(receipt.primary_deployment_id, candidate_id)
                self.assertNotEqual(receipt.primary_deployment_id, terminal.primary_deployment_id)
                self.assertEqual(receipt.binding_reason, "zero_count_initialization")

    def test_restorative_and_worker_initialization_use_the_same_strict_receipt_rules(
        self,
    ) -> None:
        restore = ServiceTarget(self.WEB_A, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a-old",
            "terminal",
        )
        attempted = ServicePredecessor(
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            "attempted",
        )
        candidate = self.deployment(
            self.WEB_A,
            0,
            "ecs-svc/web-a-restore",
            running_count=0,
            pending_count=0,
        )
        failed_attempt = self.deployment(
            self.WEB_B,
            1,
            attempted.primary_deployment_id,
            status="ACTIVE",
            rollout_state="FAILED",
            failed_tasks=1,
            running_count=0,
            pending_count=0,
        )
        service = self.service(
            restore,
            ServiceTarget(self.WEB_A, 0),
            "ecs-svc/web-a-restore",
            service_running=0,
            service_pending=1,
            primary_running=0,
            primary_pending=0,
            deployments=[candidate, failed_attempt],
        )
        receipt = self.gateway(
            FakeServiceSequenceEcs([], update_response={"service": service})
        ).update_service("web", restore, (terminal, attempted))
        self.assertEqual(receipt.binding_reason, "zero_count_initialization")

        worker_a = ServiceTarget(WorkerStabilizationContractTests.WORKER_A, 1)
        worker_b = ServiceTarget(WorkerStabilizationContractTests.WORKER_B, 1)
        worker_terminal = ServicePredecessor(worker_a, "ecs-svc/worker-a", "terminal")
        worker_candidate = self.deployment(
            worker_b.task_definition_arn,
            0,
            "ecs-svc/worker-b",
            running_count=1,
            pending_count=0,
        )
        worker_service = self.service(
            worker_b,
            ServiceTarget(worker_b.task_definition_arn, 0),
            "ecs-svc/worker-b",
            service_name="worker",
            service_running=1,
            service_pending=0,
            deployments=[worker_candidate],
        )
        ecs = FakeServiceSequenceEcs([], update_response={"service": worker_service})
        with self.assertRaises(ReleaseContractError):
            self.gateway(ecs).update_service("worker", worker_b, (worker_terminal,))
        self.assertEqual(ecs.describe_calls, 0)

    def test_forward_zero_initialization_allows_bounded_predecessor_retirement(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/4046860192810903819",
            "terminal",
        )
        candidate_id = "ecs-svc/3371700095810170170"
        initialization = self.deployment(
            self.WEB_B,
            0,
            candidate_id,
            running_count=0,
            pending_count=0,
        )
        captured_predecessor = self.deployment(
            self.WEB_A,
            1,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        acknowledgement = self.service(
            target,
            ServiceTarget(self.WEB_B, 0),
            candidate_id,
            service_running=1,
            service_pending=0,
            deployments=[initialization, captured_predecessor],
        )
        draining_predecessor = captured_predecessor | {
            "desiredCount": 0,
            "runningCount": 1,
        }
        candidate_in_progress = self.deployment(
            self.WEB_B,
            1,
            candidate_id,
            running_count=0,
            pending_count=1,
        )
        draining = self.service(
            target,
            target,
            candidate_id,
            service_running=1,
            service_pending=1,
            deployments=[candidate_in_progress, draining_predecessor],
        )
        retired_predecessor = draining_predecessor | {
            "status": "DRAINING",
            "runningCount": 0,
            "pendingCount": 0,
        }
        candidate_terminal = self.deployment(
            self.WEB_B,
            1,
            candidate_id,
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        completed_with_overlap = self.service(
            target,
            target,
            candidate_id,
            service_running=2,
            service_pending=0,
            deployments=[candidate_terminal, retired_predecessor],
        )
        terminal_service = self.service(
            target,
            target,
            candidate_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate_terminal],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [draining, completed_with_overlap, terminal_service],
            update_response={"service": acknowledgement},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertEqual(receipt.binding_reason, "zero_count_initialization")
            gateway.wait_service_stable(receipt, deadline=30.0, timeout_seconds=30)
        self.assertEqual(receipt.primary_deployment_id, candidate_id)
        self.assertEqual(ecs.describe_calls, 3)
        self.assertEqual(clock.sleep_calls, [10, 10])

    def test_restorative_same_definition_receipt_allows_old_predecessor_retirement(
        self,
    ) -> None:
        restore = ServiceTarget(self.WEB_A, 1)
        terminal = ServicePredecessor(
            restore,
            "ecs-svc/4046860192810903819",
            "terminal",
        )
        attempted = ServicePredecessor(
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/3371700095810170170",
            "attempted",
        )
        candidate_id = "ecs-svc/4738995105852396099"
        candidate_acknowledgement = self.deployment(
            self.WEB_A,
            1,
            candidate_id,
            running_count=0,
            pending_count=0,
        )
        draining_old = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        attempted_active = self.deployment(
            self.WEB_B,
            1,
            attempted.primary_deployment_id,
            status="ACTIVE",
            running_count=1,
            pending_count=0,
        )
        acknowledgement = self.service(
            restore,
            restore,
            candidate_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate_acknowledgement, attempted_active, draining_old],
        )
        candidate_in_progress = self.deployment(
            self.WEB_A,
            1,
            candidate_id,
            running_count=1,
            pending_count=0,
        )
        attempted_draining = self.deployment(
            self.WEB_B,
            0,
            attempted.primary_deployment_id,
            status="DRAINING",
            running_count=0,
            pending_count=0,
        )
        restoring = self.service(
            restore,
            restore,
            candidate_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate_in_progress, attempted_draining],
        )
        candidate_terminal = self.deployment(
            self.WEB_A,
            1,
            candidate_id,
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        terminal_service = self.service(
            restore,
            restore,
            candidate_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate_terminal],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [restoring, terminal_service],
            update_response={"service": acknowledgement},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                restore,
                (terminal, attempted),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertEqual(receipt.binding_reason, "complete_receipt")
            gateway.wait_service_stable(receipt, deadline=30.0, timeout_seconds=30)
        self.assertEqual(receipt.primary_deployment_id, candidate_id)
        self.assertNotEqual(receipt.primary_deployment_id, terminal.primary_deployment_id)
        self.assertNotEqual(receipt.primary_deployment_id, attempted.primary_deployment_id)
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [10])

    def test_recovery_capture_allows_the_terminal_predecessor_to_retire(self) -> None:
        attempted = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/4046860192810903819",
            "terminal",
        )
        attempted_initialization = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/3371700095810170170",
            running_count=0,
            pending_count=0,
        )
        draining_terminal = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        service = self.service(
            attempted,
            ServiceTarget(self.WEB_B, 0),
            "ecs-svc/3371700095810170170",
            service_running=1,
            service_pending=0,
            deployments=[attempted_initialization, draining_terminal],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([service])
        with patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic):
            captured = self.gateway(ecs).capture_attempted_predecessor(
                "web",
                attempted,
                terminal,
                30.0,
            )
        self.assertEqual(captured.target, attempted)
        self.assertEqual(captured.primary_deployment_id, "ecs-svc/3371700095810170170")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_acknowledgement_rejects_unsafe_draining_deployments(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        for name, deployments in self.draining_rejection_cases(
            candidate,
            predecessor,
            include_missing_id=False,
        ).items():
            acknowledgement = self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=0,
                service_pending=0,
                deployments=deployments,
            )
            ecs = FakeServiceSequenceEcs(
                [],
                update_response={"service": acknowledgement},
            )
            with self.subTest(name=name), self.assertRaises(ReleaseContractError) as caught:
                self.gateway(ecs).update_service("web", target, (terminal,))
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.update_calls, 1)
            self.assertEqual(ecs.describe_calls, 0)

    def test_partial_draining_acknowledgements_require_exact_reconciliation(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        known_id_missing_member = predecessor.copy()
        known_id_missing_member.pop("pendingCount")
        idless = predecessor.copy()
        idless.pop("id")
        exact = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=0,
            service_pending=0,
            deployments=[candidate, predecessor],
        )
        for name, projection in (
            ("known ID missing member", known_id_missing_member),
            ("ID-less unambiguous predecessor", idless),
        ):
            acknowledgement = self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=0,
                service_pending=0,
                deployments=[candidate, projection],
            )
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs(
                [exact],
                update_response={"service": acknowledgement},
            )
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            ):
                receipt = self.gateway(ecs).update_service(
                    "web",
                    target,
                    (terminal,),
                    deadline=30.0,
                )
            self.assertEqual(
                receipt.binding_reason,
                "partial_acknowledgement_zero_count_initialization",
            )
            self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-b")
            self.assertEqual(ecs.describe_calls, 1)
            self.assertEqual(clock.sleep_calls, [])

    def test_idless_ambiguous_draining_projection_reconciles_injectively(self) -> None:
        target = ServiceTarget(self.WEB_A, 1)
        terminal = ServicePredecessor(
            target,
            "ecs-svc/web-a-old",
            "terminal",
        )
        attempted = ServicePredecessor(
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            "attempted",
        )
        candidate = self.deployment(
            self.WEB_A,
            0,
            "ecs-svc/web-a-restore",
            running_count=0,
            pending_count=0,
        )
        ambiguous_draining = {
            "status": "DRAINING",
            "desiredCount": 0,
            "runningCount": 0,
            "pendingCount": 0,
            "failedTasks": 0,
            "rolloutState": "IN_PROGRESS",
        }
        acknowledgement = self.service(
            target,
            target,
            "ecs-svc/web-a-restore",
            service_running=0,
            service_pending=0,
            deployments=[candidate, ambiguous_draining],
        )
        exact_attempted = self.deployment(
            self.WEB_B,
            0,
            attempted.primary_deployment_id,
            status="DRAINING",
            running_count=0,
            pending_count=0,
        )
        reconciled = self.service(
            target,
            target,
            "ecs-svc/web-a-restore",
            service_running=0,
            service_pending=0,
            deployments=[candidate, exact_attempted],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [reconciled],
            update_response={"service": acknowledgement},
        )
        with patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic):
            receipt = self.gateway(ecs).update_service(
                "web",
                target,
                (terminal, attempted),
                deadline=30.0,
            )
        self.assertEqual(
            receipt.binding_reason,
            "partial_acknowledgement_zero_count_initialization",
        )
        self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-a-restore")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_reconciliation_cannot_carry_terminal_success_with_predecessor_work(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor_with_work = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        crossed_terminal = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[candidate, predecessor_with_work],
        )
        draining_predecessor = predecessor_with_work | {
            "status": "DRAINING",
            "runningCount": 0,
        }
        exact_terminal = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[candidate, draining_predecessor],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [crossed_terminal, crossed_terminal, exact_terminal],
            update_response={"service": {"serviceName": "web"}},
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertFalse(receipt.terminal_observed)
            gateway.wait_service_stable(
                receipt,
                deadline=30.0,
                timeout_seconds=30,
            )
        self.assertEqual(receipt.binding_reason, "partial_acknowledgement_reconciled")
        self.assertEqual(ecs.describe_calls, 3)
        self.assertEqual(clock.sleep_calls, [10])

    def test_reconciled_predecessor_work_at_deadline_cannot_advance(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor_with_work = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        crossed_terminal = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[candidate, predecessor_with_work],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [crossed_terminal, crossed_terminal],
            update_response={"service": {"serviceName": "web"}},
            clock=clock,
            describe_delays=[0, 30],
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertFalse(receipt.terminal_observed)
            with self.assertRaises(ReleaseContractError) as caught:
                gateway.wait_service_stable(
                    receipt,
                    deadline=30.0,
                    timeout_seconds=30,
                )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [])

    def test_reconciliation_rejects_unsafe_draining_deployment(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        unknown = self.deployment(
            self.WEB_A,
            0,
            "ecs-svc/unknown",
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        reconciled = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=0,
            service_pending=0,
            deployments=[candidate, unknown],
        )
        ecs = FakeServiceSequenceEcs(
            [reconciled],
            update_response={"service": {"serviceName": "web"}},
        )
        with self.assertRaises(ReleaseContractError) as caught:
            self.gateway(ecs).update_service("web", target, (terminal,))
        self.assertEqual(caught.exception.reason_code, "contract_contradiction")
        self.assertEqual(ecs.update_calls, 1)
        self.assertEqual(ecs.describe_calls, 1)

    def test_bound_receipt_rejects_unsafe_draining_deployments(self) -> None:
        receipt = self.receipt()
        candidate = self.deployment(
            self.WEB_B,
            1,
            receipt.primary_deployment_id,
            running_count=0,
            pending_count=1,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            receipt.predecessors[0].primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        for name, deployments in self.draining_rejection_cases(
            candidate,
            predecessor,
        ).items():
            service = self.service(
                receipt.target,
                receipt.target,
                receipt.primary_deployment_id,
                service_running=0,
                service_pending=1,
                deployments=deployments,
            )
            ecs = FakeServiceSequenceEcs([service])
            clock = FakeWorkerClock()
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).wait_service_stable(receipt)
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 1)

    def test_recovery_capture_rejects_unsafe_draining_deployments(self) -> None:
        attempted = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        for name, deployments in self.draining_rejection_cases(
            candidate,
            predecessor,
        ).items():
            service = self.service(
                attempted,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=0,
                service_pending=0,
                deployments=deployments,
            )
            ecs = FakeServiceSequenceEcs([service])
            clock = FakeWorkerClock()
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).capture_attempted_predecessor(
                    "web",
                    attempted,
                    terminal,
                    30.0,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 1)

    def test_completed_candidate_rejects_aggregate_outside_phase_envelope(self) -> None:
        receipt = self.receipt()
        candidate = self.deployment(
            self.WEB_B,
            1,
            receipt.primary_deployment_id,
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            receipt.predecessors[0].primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        for name, running, pending in (
            ("below completed candidate", 0, 1),
            ("above captured phase", 3, 0),
        ):
            service = self.service(
                receipt.target,
                receipt.target,
                receipt.primary_deployment_id,
                service_running=running,
                service_pending=pending,
                deployments=[candidate, predecessor],
            )
            ecs = FakeServiceSequenceEcs([service])
            with (
                self.subTest(name=name),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).wait_service_stable(receipt)
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 1)

    def test_completed_candidate_waits_for_listed_predecessor_work_to_stop(self) -> None:
        receipt = self.receipt()
        candidate = self.deployment(
            self.WEB_B,
            1,
            receipt.primary_deployment_id,
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor_with_work = self.deployment(
            self.WEB_A,
            0,
            receipt.predecessors[0].primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        crossed_exact_aggregate = self.service(
            receipt.target,
            receipt.target,
            receipt.primary_deployment_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate, predecessor_with_work],
        )
        predecessor_draining = predecessor_with_work | {
            "status": "DRAINING",
            "runningCount": 0,
        }
        exact = self.service(
            receipt.target,
            receipt.target,
            receipt.primary_deployment_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate, predecessor_draining],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([crossed_exact_aggregate, exact])
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            self.gateway(ecs).wait_service_stable(
                receipt,
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(ecs.describe_calls, 2)
        self.assertEqual(clock.sleep_calls, [10])

    def test_listed_predecessor_work_at_deadline_times_out_without_extra_read(self) -> None:
        receipt = self.receipt()
        candidate = self.deployment(
            self.WEB_B,
            1,
            receipt.primary_deployment_id,
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            receipt.predecessors[0].primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        overlap = self.service(
            receipt.target,
            receipt.target,
            receipt.primary_deployment_id,
            service_running=1,
            service_pending=0,
            deployments=[candidate, predecessor],
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [overlap],
            clock=clock,
            describe_delays=[30],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            self.gateway(ecs).wait_service_stable(
                receipt,
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_terminal_verification_allows_exact_zero_work_draining_predecessor(self) -> None:
        predecessor = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        draining = self.deployment(
            self.WEB_A,
            0,
            predecessor.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        service = self.service(
            ServiceTarget(self.WEB_B, 1),
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[primary, draining],
        )

        self.verify_terminal_web_service(service, (predecessor,))

    def test_reconciled_active_zero_work_remnant_carries_through_terminal_verification(
        self,
    ) -> None:
        predecessor = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        target = ServiceTarget(self.WEB_B, 1)
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        active = self.deployment(
            self.WEB_A,
            0,
            predecessor.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        service = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[primary, active],
        )
        clock = FakeWorkerClock()
        gateway = self.gateway(
            FakeServiceSequenceEcs(
                [service],
                update_response={"service": {"serviceName": "web"}},
            )
        )

        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (predecessor,),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertTrue(receipt.terminal_observed)
            gateway.wait_service_stable(
                receipt,
                timeout_seconds=30,
                deadline=30.0,
            )

        self.assertEqual(gateway.ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])
        self.verify_terminal_web_service(service, (predecessor,))

    def test_terminal_verification_allows_recognized_predecessor_to_be_absent(self) -> None:
        predecessor = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        service = self.service(
            ServiceTarget(self.WEB_B, 1),
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[primary],
        )

        self.verify_terminal_web_service(service, (predecessor,))

    def test_terminal_verification_rejects_working_or_unknown_extra_deployments(
        self,
    ) -> None:
        predecessor_model = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        recognized_active = self.deployment(
            self.WEB_A,
            0,
            predecessor_model.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        cases = {
            "recognized ACTIVE with work": [
                primary,
                recognized_active | {"runningCount": 1},
            ],
            "unknown ACTIVE alien task": [
                primary,
                self.deployment(
                    self.WEB_C,
                    0,
                    "ecs-svc/alien",
                    status="ACTIVE",
                    rollout_state="COMPLETED",
                    running_count=0,
                    pending_count=0,
                ),
            ],
            "unknown ACTIVE same predecessor task": [
                primary,
                recognized_active | {"id": "ecs-svc/web-a-other"},
            ],
            "unknown ACTIVE same candidate task": [
                primary,
                self.deployment(
                    self.WEB_B,
                    0,
                    "ecs-svc/web-b-other",
                    status="ACTIVE",
                    rollout_state="COMPLETED",
                    running_count=0,
                    pending_count=0,
                ),
            ],
        }
        for name, deployments in cases.items():
            service = self.service(
                ServiceTarget(self.WEB_B, 1),
                ServiceTarget(self.WEB_B, 1),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=deployments,
            )
            with self.subTest(name=name), self.assertRaises(ReleaseContractError) as caught:
                self.verify_terminal_web_service(service, (predecessor_model,))
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")

    def test_terminal_verification_keeps_untouched_worker_allowlist_empty(self) -> None:
        predecessor = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        web_primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        web_service = self.service(
            ServiceTarget(self.WEB_B, 1),
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[web_primary],
        )
        worker_primary = self.deployment(
            WorkerStabilizationContractTests.WORKER_B,
            1,
            "ecs-svc/worker-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        worker_unknown = self.deployment(
            WorkerStabilizationContractTests.WORKER_A,
            0,
            "ecs-svc/worker-unknown",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )

        with self.assertRaises(ReleaseContractError) as caught:
            self.verify_terminal_web_service(
                web_service,
                (predecessor,),
                worker_deployments=[worker_primary, worker_unknown],
            )
        self.assertEqual(caught.exception.reason_code, "contract_contradiction")

    def test_terminal_verification_revalidates_service_target_and_aggregate(self) -> None:
        predecessor = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        base = self.service(
            ServiceTarget(self.WEB_B, 1),
            ServiceTarget(self.WEB_B, 1),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            deployments=[primary],
        )
        cases = {
            "changed service target": base | {"taskDefinition": self.WEB_A},
            "excess aggregate running": base | {"runningCount": 2},
            "aggregate pending work": base | {"pendingCount": 1},
        }
        for name, service in cases.items():
            with self.subTest(name=name), self.assertRaises(ReleaseContractError) as caught:
                self.verify_terminal_web_service(service, (predecessor,))
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")

    def test_terminal_verification_rejects_unsafe_draining_deployments(self) -> None:
        predecessor_model = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            predecessor_model.primary_deployment_id,
            status="DRAINING",
            rollout_state="COMPLETED",
            running_count=0,
            pending_count=0,
        )
        for name, deployments in self.draining_rejection_cases(
            primary,
            predecessor,
        ).items():
            if name == "candidate":
                deployments = [
                    primary | {"id": "ecs-svc/web-other"},
                    primary
                    | {
                        "status": "DRAINING",
                        "desiredCount": 0,
                        "runningCount": 0,
                        "pendingCount": 0,
                        "rolloutState": "IN_PROGRESS",
                    },
                ]
            service = self.service(
                ServiceTarget(self.WEB_B, 1),
                ServiceTarget(self.WEB_B, 1),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=deployments,
            )
            with self.subTest(name=name), self.assertRaises(ReleaseContractError) as caught:
                self.verify_terminal_web_service(service, (predecessor_model,))
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")

    def test_predecessor_retirement_contradictions_fail_before_reconciliation(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        cases = {
            "desired above captured": [candidate, predecessor | {"desiredCount": 2}],
            "running above captured": [candidate, predecessor | {"runningCount": 2}],
            "pending above captured": [candidate, predecessor | {"pendingCount": 2}],
            "task total above captured": [candidate, predecessor | {"pendingCount": 1}],
            "terminal failed task": [candidate, predecessor | {"failedTasks": 1}],
            "terminal failed rollout": [candidate, predecessor | {"rolloutState": "FAILED"}],
            "cross-paired predecessor": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_B},
            ],
            "unknown predecessor ID": [
                candidate,
                predecessor | {"id": "ecs-svc/web-unknown"},
            ],
            "alien predecessor task": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_C},
            ],
            "candidate drain misuse": [
                candidate | {"runningCount": 1},
                predecessor,
            ],
            "duplicate predecessor projection": [candidate, predecessor, predecessor],
        }
        for name, deployments in cases.items():
            acknowledgement = self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=deployments,
            )
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs([], update_response={"service": acknowledgement})
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).update_service(
                    "web",
                    target,
                    (terminal,),
                    deadline=30.0,
                    timeout_seconds=30,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.update_calls, 1)
            self.assertEqual(ecs.describe_calls, 0)
            self.assertEqual(clock.sleep_calls, [])

    def test_bound_receipt_rejects_unsafe_predecessor_retirement(self) -> None:
        receipt = self.receipt()
        candidate = self.deployment(
            self.WEB_B,
            1,
            receipt.primary_deployment_id,
            running_count=0,
            pending_count=1,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            receipt.predecessors[0].primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        cases = {
            "desired above captured": [candidate, predecessor | {"desiredCount": 2}],
            "running above captured": [candidate, predecessor | {"runningCount": 2}],
            "pending above captured": [candidate, predecessor | {"pendingCount": 2}],
            "task total above captured": [candidate, predecessor | {"pendingCount": 1}],
            "terminal failed task": [candidate, predecessor | {"failedTasks": 1}],
            "terminal failed rollout": [candidate, predecessor | {"rolloutState": "FAILED"}],
            "cross-paired predecessor": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_B},
            ],
            "unknown predecessor ID": [
                candidate,
                predecessor | {"id": "ecs-svc/web-unknown"},
            ],
            "alien predecessor task": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_C},
            ],
            "candidate drain misuse": [
                candidate | {"desiredCount": 0, "runningCount": 1, "pendingCount": 0},
                predecessor,
            ],
            "duplicate predecessor": [candidate, predecessor, predecessor],
        }
        for name, deployments in cases.items():
            service = self.service(
                receipt.target,
                receipt.target,
                receipt.primary_deployment_id,
                service_running=1,
                service_pending=1,
                deployments=deployments,
            )
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs([service])
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).wait_service_stable(
                    receipt,
                    timeout_seconds=30,
                    deadline=30.0,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 1)
            self.assertEqual(clock.sleep_calls, [])

    def test_recovery_capture_rejects_unsafe_terminal_retirement(self) -> None:
        attempted = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        predecessor = self.deployment(
            self.WEB_A,
            0,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        cases = {
            "desired above captured": [candidate, predecessor | {"desiredCount": 2}],
            "task total above captured": [candidate, predecessor | {"pendingCount": 1}],
            "terminal failed task": [candidate, predecessor | {"failedTasks": 1}],
            "terminal failed rollout": [candidate, predecessor | {"rolloutState": "FAILED"}],
            "cross-paired predecessor": [
                candidate,
                predecessor | {"taskDefinition": self.WEB_B},
            ],
            "unknown predecessor ID": [
                candidate,
                predecessor | {"id": "ecs-svc/web-unknown"},
            ],
            "candidate drain misuse": [
                candidate | {"runningCount": 1},
                predecessor,
            ],
            "duplicate predecessor": [candidate, predecessor, predecessor],
        }
        for name, deployments in cases.items():
            service = self.service(
                attempted,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=deployments,
            )
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs([service])
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).capture_attempted_predecessor(
                    "web",
                    attempted,
                    terminal,
                    30.0,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 1)
            self.assertEqual(clock.sleep_calls, [])

    def test_retiring_predecessor_does_not_weaken_deadline_or_worker_singleton(self) -> None:
        web_receipt = self.receipt()
        web_candidate = self.deployment(
            self.WEB_B,
            1,
            web_receipt.primary_deployment_id,
            running_count=0,
            pending_count=1,
        )
        web_predecessor = self.deployment(
            self.WEB_A,
            0,
            web_receipt.predecessors[0].primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        web_draining = self.service(
            web_receipt.target,
            web_receipt.target,
            web_receipt.primary_deployment_id,
            service_running=1,
            service_pending=1,
            deployments=[web_candidate, web_predecessor],
        )
        web_clock = FakeWorkerClock()
        web_ecs = FakeServiceSequenceEcs(
            [web_draining],
            clock=web_clock,
            describe_delays=[30],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=web_clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=web_clock.sleep),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            self.gateway(web_ecs).wait_service_stable(
                web_receipt,
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(web_ecs.describe_calls, 1)
        self.assertEqual(web_clock.sleep_calls, [])

        worker_a = ServiceTarget(WorkerStabilizationContractTests.WORKER_A, 1)
        worker_b = ServiceTarget(WorkerStabilizationContractTests.WORKER_B, 1)
        worker_receipt = ServiceUpdateReceipt(
            "worker",
            "worker",
            worker_b,
            "ecs-svc/worker-b",
            (ServicePredecessor(worker_a, "ecs-svc/worker-a", "terminal"),),
        )
        worker_candidate = self.deployment(
            worker_b.task_definition_arn,
            1,
            worker_receipt.primary_deployment_id,
            running_count=0,
            pending_count=1,
        )
        worker_predecessor = self.deployment(
            worker_a.task_definition_arn,
            0,
            worker_receipt.predecessors[0].primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        worker_overlap = self.service(
            worker_b,
            worker_b,
            worker_receipt.primary_deployment_id,
            service_name="worker",
            service_running=1,
            service_pending=0,
            deployments=[worker_candidate, worker_predecessor],
        )
        worker_clock = FakeWorkerClock()
        worker_ecs = FakeServiceSequenceEcs([worker_overlap])
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=worker_clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=worker_clock.sleep),
            self.assertRaises(ReleaseContractError) as worker_caught,
        ):
            self.gateway(worker_ecs).wait_service_stable(
                worker_receipt,
                worker_singleton=True,
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(worker_caught.exception.reason_code, "contract_contradiction")
        self.assertEqual(worker_ecs.describe_calls, 1)
        self.assertEqual(worker_clock.sleep_calls, [])

    def test_partial_worker_counts_enforce_per_deployment_and_aggregate_singleton(
        self,
    ) -> None:
        worker_a = ServiceTarget(WorkerStabilizationContractTests.WORKER_A, 1)
        worker_b = ServiceTarget(WorkerStabilizationContractTests.WORKER_B, 1)
        terminal = ServicePredecessor(worker_a, "ecs-svc/worker-a", "terminal")
        candidate = self.deployment(
            worker_b.task_definition_arn,
            1,
            "ecs-svc/worker-b",
            running_count=1,
            pending_count=0,
        )
        candidate.pop("pendingCount")
        old = self.deployment(
            worker_a.task_definition_arn,
            1,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        cases = {
            "partial per-deployment lower bound": [candidate | {"runningCount": 2}],
            "partial aggregate lower bound": [candidate, old],
        }
        for name, deployments in cases.items():
            service = self.service(
                worker_b,
                worker_b,
                "ecs-svc/worker-b",
                service_name="worker",
                service_running=1,
                service_pending=0,
                deployments=deployments,
            )
            ecs = FakeServiceSequenceEcs([], update_response={"service": service})
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                self.gateway(ecs).update_service("worker", worker_b, (terminal,))
            self.assertEqual(ecs.describe_calls, 0)

    def test_recovery_captures_and_replaces_an_attempted_zero_count_initialization(
        self,
    ) -> None:
        restore = ServiceTarget(self.WEB_A, 1)
        attempted_target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(restore, "ecs-svc/web-a", "terminal")
        attempted_initialization = self.deployment(
            self.WEB_B,
            0,
            "ecs-svc/web-b",
            running_count=0,
            pending_count=0,
        )
        observed_attempt = self.service(
            attempted_target,
            ServiceTarget(self.WEB_B, 0),
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=0,
            primary_pending=0,
            deployments=[attempted_initialization],
        )
        gateway = self.gateway(FakeServiceSequenceEcs([observed_attempt]))
        captured = gateway.capture_attempted_predecessor(
            "web",
            attempted_target,
            terminal,
            gateway.service_stabilization_deadline(),
        )
        self.assertEqual(captured.target, attempted_target)
        self.assertEqual(captured.primary_deployment_id, "ecs-svc/web-b")

        restorative_candidate = self.deployment(
            self.WEB_A,
            0,
            "ecs-svc/web-a-restore",
            running_count=0,
            pending_count=0,
        )
        attempted_active = attempted_initialization | {"status": "ACTIVE"}
        restorative_service = self.service(
            restore,
            ServiceTarget(self.WEB_A, 0),
            "ecs-svc/web-a-restore",
            service_running=0,
            service_pending=1,
            primary_running=0,
            primary_pending=0,
            deployments=[restorative_candidate, attempted_active],
        )
        receipt = self.gateway(
            FakeServiceSequenceEcs([], update_response={"service": restorative_service})
        ).update_service("web", restore, (terminal, captured))
        self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-a-restore")
        self.assertEqual(receipt.binding_reason, "zero_count_initialization")

    def test_worker_receipt_rejects_service_and_cross_deployment_overlap(self) -> None:
        worker_a = ServiceTarget(WorkerStabilizationContractTests.WORKER_A, 1)
        worker_b = ServiceTarget(WorkerStabilizationContractTests.WORKER_B, 1)
        terminal = ServicePredecessor(worker_a, "ecs-svc/worker-a", "terminal")
        target_deployment = self.deployment(
            worker_b.task_definition_arn,
            1,
            "ecs-svc/worker-b",
            running_count=1,
            pending_count=0,
        )
        old_deployment = self.deployment(
            worker_a.task_definition_arn,
            1,
            "ecs-svc/worker-a",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        overlap_cases = (
            self.service(
                worker_b,
                worker_b,
                "ecs-svc/worker-b",
                service_name="worker",
                service_running=1,
                service_pending=1,
                deployments=[target_deployment],
            ),
            self.service(
                worker_b,
                worker_b,
                "ecs-svc/worker-b",
                service_name="worker",
                service_running=1,
                service_pending=0,
                deployments=[target_deployment, old_deployment],
            ),
        )
        for service in overlap_cases:
            ecs = FakeServiceSequenceEcs([service], update_response={"service": service})
            with self.assertRaises(ReleaseContractError):
                self.gateway(ecs).update_service("worker", worker_b, (terminal,))

    def test_crossed_and_reordered_stale_reads_converge_only_on_receipt_id(self) -> None:
        old = ServiceTarget(self.WEB_A, 1)
        target = ServiceTarget(self.WEB_B, 1)
        states = [
            self.service(
                old,
                old,
                "ecs-svc/web-a",
                service_running=1,
                service_pending=0,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
            self.service(old, target, "ecs-svc/web-b"),
            self.service(
                target,
                old,
                "ecs-svc/web-a",
                service_running=0,
                service_pending=1,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
            self.service(
                old,
                old,
                "ecs-svc/web-a",
                service_running=1,
                service_pending=0,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
            self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
        ]
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(states)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            self.gateway(ecs).wait_service_stable(
                self.receipt(), timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS
            )
        self.assertEqual(ecs.describe_calls, 5)
        self.assertEqual(clock.sleep_calls, [10, 10, 10, 10])

    def test_stale_reads_do_not_reset_a_nondivisor_inclusive_deadline(self) -> None:
        old = ServiceTarget(self.WEB_A, 1)
        stale = self.service(
            old,
            old,
            "ecs-svc/web-a",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([stale])
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaisesMessage(ReleaseContractError, "deadline expired"),
        ):
            self.gateway(ecs, poll_seconds=17).wait_service_stable(
                self.receipt(), timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS
            )
        self.assertEqual(clock.current, WEB_STABILIZATION_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 16)
        self.assertEqual(len(clock.sleep_calls), 15)
        self.assertEqual(clock.sleep_calls[-1], 2)

    def test_bootstrap_tuples_reject_cross_pairs_alien_and_same_target_id(self) -> None:
        old = ServiceTarget(self.WEB_A, 0)
        target = ServiceTarget(self.WEB_B, 1)
        receipt = self.receipt(
            target=target,
            predecessor=ServicePredecessor(old, "ecs-svc/web-a", "terminal"),
        )
        cases = {
            "old arn target count": self.service(
                ServiceTarget(self.WEB_A, 1), target, "ecs-svc/web-b"
            ),
            "target arn old count": self.service(
                ServiceTarget(self.WEB_B, 0), target, "ecs-svc/web-b"
            ),
            "alien arn": self.service(ServiceTarget(self.WEB_C, 1), target, "ecs-svc/web-b"),
            "unrecognized same target id": self.service(target, target, "ecs-svc/web-other"),
            "unrecognized zero-count target id": self.service(
                target,
                ServiceTarget(self.WEB_B, 0),
                "ecs-svc/web-other",
                service_running=0,
                service_pending=1,
                primary_running=0,
                primary_pending=0,
            ),
        }
        for name, service in cases.items():
            clock = FakeWorkerClock()
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError),
            ):
                self.gateway(FakeServiceSequenceEcs([service])).wait_service_stable(
                    receipt, timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS
                )
            self.assertEqual(clock.current, 0)

    def test_bootstrap_zero_count_stale_and_both_crossed_orientations_converge(self) -> None:
        old = ServiceTarget(self.WEB_A, 0)
        target = ServiceTarget(self.WEB_B, 1)
        receipt = self.receipt(
            target=target,
            predecessor=ServicePredecessor(old, "ecs-svc/web-a", "terminal"),
        )
        states = [
            self.service(
                old,
                old,
                "ecs-svc/web-a",
                service_running=0,
                service_pending=0,
                primary_running=0,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
            self.service(
                old,
                target,
                "ecs-svc/web-b",
                service_running=0,
                service_pending=0,
            ),
            self.service(
                target,
                old,
                "ecs-svc/web-a",
                service_running=0,
                service_pending=1,
                primary_running=0,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
            self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            ),
        ]
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(states)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            self.gateway(ecs).wait_service_stable(
                receipt, timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS
            )
        self.assertEqual(ecs.describe_calls, 4)
        self.assertEqual(clock.sleep_calls, [10, 10, 10])

    def test_fresh_general_wait_has_inclusive_nineteen_observations(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        in_progress = self.service(target, target, "ecs-svc/web-b")
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([in_progress])
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaisesMessage(ReleaseContractError, "deadline expired"),
        ):
            self.gateway(ecs).wait_service_stable(self.receipt())
        self.assertEqual(clock.current, MAX_STAGE_TIMEOUT_SECONDS)
        self.assertEqual(ecs.describe_calls, 19)
        self.assertEqual(len(clock.sleep_calls), 18)

    def test_failed_attempted_predecessor_is_pollable_only_during_recovery(self) -> None:
        restore = ServiceTarget(self.WEB_A, 1)
        attempted_target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(restore, "ecs-svc/web-a", "terminal")
        attempted = ServicePredecessor(attempted_target, "ecs-svc/web-b", "attempted")
        receipt = self.receipt(
            target=restore,
            target_id="ecs-svc/web-recovery",
            predecessor=terminal,
            attempted=attempted,
        )
        failed_attempt = self.service(
            attempted_target,
            attempted_target,
            "ecs-svc/web-b",
            rollout_state="FAILED",
            failed_tasks=1,
        )
        recovered = self.service(
            restore,
            restore,
            "ecs-svc/web-recovery",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            self.gateway(FakeServiceSequenceEcs([failed_attempt, recovered])).wait_service_stable(
                receipt
            )
        forward_receipt = self.receipt()
        with self.assertRaises(ReleaseContractError):
            self.gateway(FakeServiceSequenceEcs([failed_attempt])).wait_service_stable(
                forward_receipt, timeout_seconds=WEB_STABILIZATION_TIMEOUT_SECONDS
            )

    def test_target_failure_is_rejected_even_when_receipt_deployment_is_not_primary(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        attempted = ServicePredecessor(target, "ecs-svc/web-b", "attempted")
        receipt = self.receipt(
            target=ServiceTarget(self.WEB_A, 1),
            target_id="ecs-svc/web-recovery",
            attempted=attempted,
        )
        service = self.service(
            target,
            target,
            "ecs-svc/web-b",
            deployments=[
                self.deployment(
                    target.task_definition_arn,
                    1,
                    "ecs-svc/web-b",
                    rollout_state="FAILED",
                    failed_tasks=1,
                ),
                self.deployment(
                    self.WEB_A,
                    1,
                    "ecs-svc/web-recovery",
                    status="ACTIVE",
                    rollout_state="FAILED",
                    failed_tasks=1,
                ),
            ],
        )
        with self.assertRaises(ReleaseContractError):
            self.gateway(FakeServiceSequenceEcs([service])).wait_service_stable(receipt)

    def test_ambiguous_update_capture_shares_one_inclusive_recovery_deadline(self) -> None:
        terminal = ServicePredecessor(ServiceTarget(self.WEB_A, 1), "ecs-svc/web-a", "terminal")
        attempted = ServiceTarget(self.WEB_B, 1)
        stale = self.service(
            terminal.target,
            terminal.target,
            terminal.primary_deployment_id,
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        adopted = self.service(attempted, attempted, "ecs-svc/web-b")
        clock = FakeWorkerClock()
        gateway = self.gateway(FakeServiceSequenceEcs([stale, adopted]))
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            deadline = gateway.service_stabilization_deadline()
            predecessor = gateway.capture_attempted_predecessor(
                "web", attempted, terminal, deadline
            )
            self.assertEqual(predecessor.primary_deployment_id, "ecs-svc/web-b")
            self.assertEqual(clock.current, 10)
            remaining_states = [
                self.service(
                    terminal.target,
                    terminal.target,
                    terminal.primary_deployment_id,
                    service_running=1,
                    service_pending=0,
                    primary_running=1,
                    primary_pending=0,
                    rollout_state="COMPLETED",
                )
            ]
            gateway.ecs = FakeServiceSequenceEcs(remaining_states)
            recovery_receipt = self.receipt(
                target=terminal.target,
                target_id="ecs-svc/web-recovery",
                attempted=predecessor,
            )
            with self.assertRaisesMessage(ReleaseContractError, "deadline expired"):
                gateway.wait_service_stable(recovery_receipt, deadline=deadline)
        self.assertEqual(clock.current, MAX_STAGE_TIMEOUT_SECONDS)
        self.assertEqual(len(clock.sleep_calls), 18)

    def test_recovery_capture_polls_nonprimary_candidate_and_rejects_third_identity(self) -> None:
        terminal = ServicePredecessor(ServiceTarget(self.WEB_A, 1), "ecs-svc/web-a", "terminal")
        attempted = ServiceTarget(self.WEB_B, 1)
        old_primary = self.deployment(
            self.WEB_A,
            1,
            "ecs-svc/web-a",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        candidate_active = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            status="ACTIVE",
        )
        candidate_primary = self.service(attempted, attempted, "ecs-svc/web-b")
        first = self.service(
            terminal.target,
            terminal.target,
            terminal.primary_deployment_id,
            service_running=1,
            service_pending=1,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
            deployments=[old_primary, candidate_active],
        )
        clock = FakeWorkerClock()
        gateway = self.gateway(FakeServiceSequenceEcs([first, candidate_primary]))
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            captured = gateway.capture_attempted_predecessor(
                "web", attempted, terminal, gateway.service_stabilization_deadline()
            )
        self.assertEqual(captured.primary_deployment_id, "ecs-svc/web-b")
        self.assertEqual(clock.current, 10)

        third = first | {
            "deployments": [
                old_primary,
                self.deployment(
                    self.WEB_C,
                    1,
                    "ecs-svc/web-c",
                    status="ACTIVE",
                ),
            ]
        }
        third_clock = FakeWorkerClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=third_clock.monotonic),
            self.assertRaisesMessage(ReleaseContractError, "third"),
        ):
            self.gateway(FakeServiceSequenceEcs([third])).capture_attempted_predecessor(
                "web", attempted, terminal, 180.0
            )

    def test_raw_recovery_deadlines_cannot_bypass_general_maximum(self) -> None:
        clock = FakeWorkerClock()
        stale = self.service(
            ServiceTarget(self.WEB_A, 1),
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        gateway = self.gateway(FakeServiceSequenceEcs([stale]))
        terminal = ServicePredecessor(ServiceTarget(self.WEB_A, 1), "ecs-svc/web-a", "terminal")
        with patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic):
            for invalid in (True, float("nan"), float("inf"), 181.0):
                with self.subTest(invalid=invalid), self.assertRaises(ReleaseContractError):
                    gateway.capture_attempted_predecessor(
                        "web", ServiceTarget(self.WEB_B, 1), terminal, invalid
                    )
                with self.subTest(wait=invalid), self.assertRaises(ReleaseContractError):
                    gateway.wait_service_stable(self.receipt(), deadline=invalid)

    def test_partial_alien_task_definitions_fail_before_reconciliation(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        responses = {
            "service task definition without count": {
                "serviceName": "web",
                "taskDefinition": self.WEB_C,
            },
            "deployment task definition without id": {
                "serviceName": "web",
                "deployments": [{"taskDefinition": self.WEB_C}],
            },
            "service count without task definition": {
                "serviceName": "web",
                "desiredCount": 2,
            },
            "deployment count without identity": {
                "serviceName": "web",
                "deployments": [{"desiredCount": 2}],
            },
            "present null service identity": {
                "serviceName": None,
            },
            "present null service task definition": {
                "serviceName": "web",
                "taskDefinition": None,
            },
            "present null deployment status": {
                "serviceName": "web",
                "deployments": [{"status": None}],
            },
            "present null deployment task definition": {
                "serviceName": "web",
                "deployments": [{"taskDefinition": None}],
            },
            "present null deployment rollout state": {
                "serviceName": "web",
                "deployments": [{"rolloutState": None}],
            },
        }
        for name, service in responses.items():
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs([], update_response={"service": service})
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).update_service(
                    "web",
                    target,
                    (terminal,),
                    deadline=30.0,
                    timeout_seconds=30,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 0)
            self.assertEqual(clock.sleep_calls, [])

    def test_idless_partial_deployment_must_extend_to_an_allowed_phase_shape(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 0),
            "ecs-svc/web-a",
            "terminal",
        )
        cases = {
            "predecessor arn target count cross-pair": {
                "taskDefinition": self.WEB_A,
                "desiredCount": 1,
            },
            "initialization running count": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 0,
                "runningCount": 1,
                "rolloutState": "IN_PROGRESS",
            },
            "initialization pending count": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 0,
                "pendingCount": 1,
                "rolloutState": "IN_PROGRESS",
            },
            "initialization failed count": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 0,
                "failedTasks": 1,
                "rolloutState": "IN_PROGRESS",
            },
            "completed initialization": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 0,
                "runningCount": 0,
                "pendingCount": 0,
                "failedTasks": 0,
                "rolloutState": "COMPLETED",
            },
            "failed target": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 1,
                "rolloutState": "FAILED",
            },
            "positive target failed count": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 1,
                "failedTasks": 1,
            },
            "completed target running count": {
                "taskDefinition": self.WEB_B,
                "desiredCount": 1,
                "runningCount": 0,
                "pendingCount": 0,
                "failedTasks": 0,
                "rolloutState": "COMPLETED",
            },
        }
        for name, deployment in cases.items():
            clock = FakeWorkerClock()
            ecs = FakeServiceSequenceEcs(
                [],
                update_response={
                    "service": {
                        "serviceName": "web",
                        "deployments": [deployment],
                    }
                },
            )
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError) as caught,
            ):
                self.gateway(ecs).update_service(
                    "web",
                    target,
                    (terminal,),
                    deadline=1.0,
                )
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertEqual(ecs.describe_calls, 0)
            self.assertEqual(clock.sleep_calls, [])

    def test_partial_candidate_projections_fit_one_candidate_slot_in_every_order(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(self.WEB_B, 1, "ecs-svc/web-b")
        idless_target = self.deployment(
            self.WEB_B,
            1,
            "unused",
            status="ACTIVE",
        )
        idless_target.pop("id")
        second_idless_target = idless_target | {"pendingCount": 0}
        predecessor = self.deployment(
            self.WEB_A,
            1,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        cases = {
            "explicit and ID-less target-only": (candidate, idless_target, predecessor),
            "two ID-less target-only": (
                idless_target,
                second_idless_target,
                predecessor | {"status": "PRIMARY"},
            ),
        }
        for name, deployments in cases.items():
            for order in permutations(deployments):
                clock = FakeWorkerClock()
                partial = {
                    "serviceName": "web",
                    "deployments": list(order),
                }
                ecs = FakeServiceSequenceEcs([], update_response={"service": partial})
                with (
                    self.subTest(name=name, order=tuple(item.get("id") for item in order)),
                    patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                    patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                    self.assertRaises(ReleaseContractError) as caught,
                ):
                    self.gateway(ecs).update_service(
                        "web",
                        target,
                        (terminal,),
                        deadline=30.0,
                        timeout_seconds=30,
                    )
                self.assertEqual(caught.exception.reason_code, "contract_contradiction")
                self.assertEqual(ecs.update_calls, 1)
                self.assertEqual(ecs.describe_calls, 0)
                self.assertEqual(clock.sleep_calls, [])

    def test_one_ambiguous_idless_projection_still_reconciles_to_an_exact_receipt(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        candidate = self.deployment(self.WEB_B, 1, "ecs-svc/web-b")
        predecessor = self.deployment(
            self.WEB_A,
            1,
            terminal.primary_deployment_id,
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        complete = self.service(
            target,
            target,
            "ecs-svc/web-b",
            deployments=[candidate, predecessor],
        )
        partial = {
            "serviceName": "web",
            "deployments": [{"desiredCount": 1}],
        }
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs([complete], update_response={"service": partial})
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = self.gateway(ecs).update_service(
                "web",
                target,
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
        self.assertEqual(receipt.primary_deployment_id, "ecs-svc/web-b")
        self.assertEqual(receipt.binding_reason, "partial_acknowledgement_reconciled")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_update_service_duration_consumes_deadline_and_equality_gets_one_final_read(
        self,
    ) -> None:
        cases = (
            (
                "replacement",
                ServiceTarget(self.WEB_B, 1),
                ServicePredecessor(
                    ServiceTarget(self.WEB_A, 1),
                    "ecs-svc/web-a",
                    "terminal",
                ),
                "ecs-svc/web-b",
            ),
            (
                "same-target",
                ServiceTarget(self.WEB_A, 1),
                ServicePredecessor(
                    ServiceTarget(self.WEB_A, 1),
                    "ecs-svc/web-a",
                    "terminal",
                ),
                "ecs-svc/web-a-new",
            ),
        )
        for case, target, terminal, candidate_id in cases:
            complete = self.service(
                target,
                target,
                candidate_id,
                service_running=1,
                service_pending=0,
                primary_running=1,
                primary_pending=0,
                rollout_state="COMPLETED",
            )
            for timing, delay in (("after", 31), ("equality", 30)):
                clock = FakeWorkerClock()
                ecs = FakeServiceSequenceEcs(
                    [complete],
                    update_response={"service": complete},
                    clock=clock,
                    update_delay=delay,
                )
                gateway = self.gateway(ecs)
                with (
                    self.subTest(case=case, timing=timing),
                    patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                    patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                ):
                    if timing == "after":
                        with self.assertRaises(ReleaseContractError) as caught:
                            gateway.update_service(
                                "web",
                                target,
                                (terminal,),
                                deadline=30.0,
                                timeout_seconds=30,
                            )
                        self.assertEqual(
                            caught.exception.reason_code,
                            "receipt_deadline_expired",
                        )
                    else:
                        receipt = gateway.update_service(
                            "web",
                            target,
                            (terminal,),
                            deadline=30.0,
                            timeout_seconds=30,
                        )
                        self.assertFalse(receipt.terminal_observed)
                        gateway.wait_service_stable(
                            receipt,
                            timeout_seconds=30,
                            deadline=30.0,
                        )
                self.assertEqual(ecs.describe_calls, 0 if timing == "after" else 1)

    def test_reconciliation_terminal_at_equality_is_carried_without_second_describe(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        terminal_service = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [terminal_service],
            update_response={"service": {"serviceName": "web"}},
            clock=clock,
            describe_delays=[30],
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            receipt = gateway.update_service(
                "web",
                target,
                (terminal,),
                deadline=30.0,
                timeout_seconds=30,
            )
            self.assertTrue(receipt.terminal_observed)
            gateway.wait_service_stable(
                receipt,
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(clock.current, 30)
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_reconciliation_in_progress_at_equality_cannot_start_second_describe(
        self,
    ) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        in_progress = self.service(target, target, "ecs-svc/web-b")
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [in_progress],
            update_response={"service": {"serviceName": "web"}},
            clock=clock,
            describe_delays=[30],
        )
        gateway = self.gateway(ecs)
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            with self.assertRaises(ReleaseContractError) as caught:
                gateway.update_service(
                    "web",
                    target,
                    (terminal,),
                    deadline=30.0,
                    timeout_seconds=30,
                )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 1)

    def test_invalid_final_read_fails_without_another_call_or_sleep(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        failed = self.service(
            target,
            target,
            "ecs-svc/web-b",
            rollout_state="FAILED",
            failed_tasks=1,
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [failed],
            clock=clock,
            describe_delays=[30],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            self.gateway(ecs).wait_service_stable(
                self.receipt(),
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(caught.exception.reason_code, "contract_contradiction")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_read_returning_after_deadline_is_discarded(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        complete = self.service(
            target,
            target,
            "ecs-svc/web-b",
            service_running=1,
            service_pending=0,
            primary_running=1,
            primary_pending=0,
            rollout_state="COMPLETED",
        )
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [complete],
            clock=clock,
            describe_delays=[31],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            self.gateway(ecs).wait_service_stable(
                self.receipt(),
                timeout_seconds=30,
                deadline=30.0,
            )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(clock.sleep_calls, [])

    def test_recovery_capture_at_equality_cannot_issue_restorative_update(self) -> None:
        attempted = ServiceTarget(self.WEB_B, 1)
        terminal = ServicePredecessor(
            ServiceTarget(self.WEB_A, 1),
            "ecs-svc/web-a",
            "terminal",
        )
        observed = self.service(attempted, attempted, "ecs-svc/web-b")
        clock = FakeWorkerClock()
        ecs = FakeServiceSequenceEcs(
            [observed],
            update_response={"service": observed},
            clock=clock,
            describe_delays=[30],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            self.gateway(ecs).capture_attempted_predecessor(
                "web",
                attempted,
                terminal,
                30.0,
            )
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(ecs.describe_calls, 1)
        self.assertEqual(ecs.update_calls, 0)

    def test_terminal_requires_explicit_failed_tasks_and_valid_extra_status(self) -> None:
        target = ServiceTarget(self.WEB_B, 1)
        primary = self.deployment(
            self.WEB_B,
            1,
            "ecs-svc/web-b",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        missing_failed = primary.copy()
        missing_failed.pop("failedTasks")
        invalid_extra = self.deployment(
            self.WEB_A,
            1,
            "ecs-svc/web-a",
            status="ACTIVE",
            rollout_state="COMPLETED",
            running_count=1,
            pending_count=0,
        )
        invalid_extra.pop("status")
        services = {
            "missing failedTasks": self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[missing_failed],
            ),
            "missing extra status": self.service(
                target,
                target,
                "ecs-svc/web-b",
                service_running=1,
                service_pending=0,
                deployments=[primary, invalid_extra],
            ),
        }
        for name, service in services.items():
            clock = FakeWorkerClock()
            with (
                self.subTest(name=name),
                patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
                patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
                self.assertRaises(ReleaseContractError),
            ):
                self.gateway(FakeServiceSequenceEcs([service])).wait_service_stable(
                    self.receipt(),
                    timeout_seconds=30,
                    deadline=30.0,
                )
            self.assertEqual(clock.current, 0)


class WorkerTimeoutCliContractTests(SimpleTestCase):
    @staticmethod
    def namespace(**overrides: object) -> Namespace:
        values: dict[str, object] = {
            "region": "eu-west-1",
            "cluster_arn": "arn:aws:ecs:eu-west-1:817685572750:cluster/website",
            "web_target_group_arn": (
                "arn:aws:elasticloadbalancing:eu-west-1:817685572750:targetgroup/web/abc"
            ),
            "web_service_name": "web",
            "worker_service_name": "worker",
            "web_family": "web",
            "worker_family": "worker",
            "migration_family": "migration",
            "web_container_name": "web",
            "worker_container_name": "worker",
            "migration_container_name": "migration",
            "task_role_arn": "arn:aws:iam::817685572750:role/task",
            "execution_role_arn": "arn:aws:iam::817685572750:role/execution",
            "subnet_id": ["subnet-1"],
            "security_group_id": ["sg-1"],
            "assign_public_ip": True,
            "base_url": "https://web.dtcdev.click",
            "screenshot_directory": Path(".tmp/deployed-smoke"),
            "timeout_seconds": MAX_STAGE_TIMEOUT_SECONDS,
            "web_stabilization_timeout_seconds": WEB_STABILIZATION_TIMEOUT_SECONDS,
            "worker_stabilization_timeout_seconds": WORKER_STABILIZATION_TIMEOUT_SECONDS,
            "poll_seconds": 10,
        }
        return Namespace(**(values | overrides))

    def build_config(self, **overrides: object) -> AwsReleaseConfig:
        with patch("deploy.cli.AwsReleaseGateway", side_effect=lambda config: config):
            return deployment_cli._gateway(  # type: ignore[return-value]
                self.namespace(**overrides)
            )

    def test_cli_defaults_preserve_three_distinct_reviewed_budgets(self) -> None:
        config = self.build_config()
        self.assertEqual(config.timeout_seconds, 180)
        self.assertEqual(config.web_stabilization_timeout_seconds, 240)
        self.assertEqual(config.worker_stabilization_timeout_seconds, 420)
        self.assertEqual(WEB_STABILIZATION_TIMEOUT_SECONDS, 240)
        self.assertEqual(MAX_WEB_STABILIZATION_TIMEOUT_SECONDS, 240)
        self.assertEqual(WORKER_STABILIZATION_TIMEOUT_SECONDS, 420)
        self.assertEqual(MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS, 420)

    def test_cli_rejects_invalid_worker_timeout_values(self) -> None:
        cases = (
            ({"web_stabilization_timeout_seconds": True}, "positive integers"),
            ({"web_stabilization_timeout_seconds": 0}, "positive integers"),
            ({"web_stabilization_timeout_seconds": -1}, "positive integers"),
            ({"web_stabilization_timeout_seconds": "bad"}, "positive integers"),
            ({"web_stabilization_timeout_seconds": 241}, "recovery-safe maximum"),
            (
                {"web_stabilization_timeout_seconds": 5, "poll_seconds": 10},
                "poll interval must not exceed the web",
            ),
            ({"worker_stabilization_timeout_seconds": 0}, "positive integers"),
            ({"worker_stabilization_timeout_seconds": -1}, "positive integers"),
            ({"worker_stabilization_timeout_seconds": "bad"}, "positive integers"),
            ({"worker_stabilization_timeout_seconds": 421}, "recovery-safe maximum"),
            (
                {"worker_stabilization_timeout_seconds": 5, "poll_seconds": 10},
                "poll interval must not exceed the worker",
            ),
            ({"timeout_seconds": 181}, "stage timeout"),
        )
        for overrides, message in cases:
            with (
                self.subTest(overrides=overrides),
                self.assertRaisesMessage(ReleaseContractError, message),
            ):
                self.build_config(**overrides)

    def test_typed_config_rejects_invalid_web_timeout_values(self) -> None:
        values = self.namespace().__dict__
        config_values = {
            "region": values["region"],
            "cluster_arn": values["cluster_arn"],
            "web_target_group_arn": values["web_target_group_arn"],
            "service_names": {"web": "web", "worker": "worker"},
            "task_families": {"web": "web", "worker": "worker", "migration": "migration"},
            "container_names": {
                "web": "web",
                "worker": "worker",
                "migration": "migration",
            },
            "task_role_arn": values["task_role_arn"],
            "execution_role_arn": values["execution_role_arn"],
            "subnet_ids": values["subnet_id"],
            "security_group_ids": values["security_group_id"],
            "assign_public_ip": values["assign_public_ip"],
            "base_url": values["base_url"],
            "screenshot_directory": values["screenshot_directory"],
        }
        cases = (
            ({"web_stabilization_timeout_seconds": True}, "positive integers"),
            ({"web_stabilization_timeout_seconds": "bad"}, "positive integers"),
            ({"web_stabilization_timeout_seconds": 0}, "positive integers"),
            ({"web_stabilization_timeout_seconds": -1}, "positive integers"),
            ({"web_stabilization_timeout_seconds": 241}, "recovery-safe maximum"),
            (
                {"web_stabilization_timeout_seconds": 5, "poll_seconds": 10},
                "poll interval must not exceed the web",
            ),
        )
        for overrides, message in cases:
            with (
                self.subTest(overrides=overrides),
                self.assertRaisesMessage(ReleaseContractError, message),
            ):
                AwsReleaseConfig(**(config_values | overrides))  # type: ignore[arg-type]

    def test_real_cli_parser_rejects_malformed_web_timeout(self) -> None:
        runtime_arguments = [
            "--region",
            "eu-west-1",
            "--cluster-arn",
            "cluster",
            "--web-target-group-arn",
            "target-group",
            "--web-service-name",
            "web",
            "--worker-service-name",
            "worker",
            "--web-family",
            "web",
            "--worker-family",
            "worker",
            "--migration-family",
            "migration",
            "--web-container-name",
            "web",
            "--worker-container-name",
            "worker",
            "--migration-container-name",
            "migration",
            "--task-role-arn",
            "task-role",
            "--execution-role-arn",
            "execution-role",
            "--subnet-id",
            "subnet-1",
            "--security-group-id",
            "sg-1",
            "--assign-public-ip",
            "true",
            "--web-stabilization-timeout-seconds",
            "malformed",
            "--repository-uri",
            "repository",
            "--expected-web-count",
            "1",
            "--expected-worker-count",
            "1",
            "--release-record-path",
            ".tmp/release.json",
        ]
        with self.assertRaises(SystemExit):
            deployment_cli.build_parser().parse_args(["capture-current", *runtime_arguments])


class FakeMigrationEcs:
    def __init__(self, run_response, task_response) -> None:  # type: ignore[no-untyped-def]
        self.run_response = run_response
        self.task_response = task_response
        self.stopped: list[str] = []
        self.run_arguments: list[dict[str, object]] = []
        self.describe_index = 0

    def run_task(self, **kwargs):  # type: ignore[no-untyped-def]
        self.run_arguments.append(kwargs)
        return self.run_response

    def describe_tasks(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        if isinstance(self.task_response, list):
            response = self.task_response[min(self.describe_index, len(self.task_response) - 1)]
            self.describe_index += 1
            if isinstance(response, Exception):
                raise response
            return response
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
            [
                {
                    "failures": [],
                    "tasks": [
                        {"taskArn": "task-timeout", "lastStatus": "STOPPED", "containers": []}
                    ],
                }
            ],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=[0, 2, 2, 2]),
            self.assertRaisesMessage(ReleaseContractError, "timed out; exact task is STOPPED"),
        ):
            self.gateway(timeout_ecs).run_migration("migration:2")
        self.assertEqual(timeout_ecs.stopped, ["task-timeout"])

        missing_ecs = FakeMigrationEcs(
            {"failures": [], "tasks": [{"taskArn": "task-missing"}]},
            [
                {"failures": [{"reason": "missing"}], "tasks": []},
                {
                    "failures": [],
                    "tasks": [
                        {"taskArn": "task-missing", "lastStatus": "STOPPED", "containers": []}
                    ],
                },
            ],
        )
        with self.assertRaisesMessage(ReleaseContractError, "observation failed"):
            self.gateway(missing_ecs).run_migration("migration:2")

    def test_timeout_and_observation_error_wait_for_exact_task_to_stop(self) -> None:
        stopped = {
            "failures": [],
            "tasks": [{"taskArn": "task-slow", "lastStatus": "STOPPED", "containers": []}],
        }
        initial_observations: tuple[object, ...] = (
            {"failures": [], "tasks": []},
            RuntimeError("sentinel-secret-must-not-leak"),
        )
        for initial in initial_observations:
            ecs = FakeMigrationEcs(
                {"failures": [], "tasks": [{"taskArn": "task-slow"}]},
                [
                    initial,
                    {
                        "failures": [],
                        "tasks": [
                            {
                                "taskArn": "task-slow",
                                "lastStatus": "RUNNING",
                                "containers": [],
                            }
                        ],
                    },
                    stopped,
                ],
            )
            with (
                self.subTest(initial=type(initial).__name__),
                patch("deploy.aws_gateway.time.sleep"),
                self.assertRaisesMessage(ReleaseContractError, "exact task is STOPPED") as caught,
            ):
                self.gateway(ecs, timeout=10).run_migration("migration:2")
            self.assertEqual(ecs.stopped, ["task-slow"])
            self.assertEqual(ecs.describe_index, 3)
            self.assertNotIn("sentinel-secret-must-not-leak", str(caught.exception))

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
        self.assertNotIn("overrides", ecs.run_arguments[0])

    def test_controlled_failure_uses_only_the_exact_nonsecret_command_override(self) -> None:
        self.assertEqual(
            CONTROLLED_MIGRATION_FAILURE_COMMAND,
            ["__dtc_controlled_migration_failure__"],
        )
        ecs = FakeMigrationEcs(
            {"failures": [], "tasks": [{"taskArn": "task-injected"}]},
            {
                "failures": [],
                "tasks": [
                    {
                        "lastStatus": "STOPPED",
                        "containers": [{"name": "migration", "exitCode": 97}],
                    }
                ],
            },
        )

        with self.assertRaisesMessage(ReleaseContractError, "exited nonzero (97)"):
            self.gateway(ecs).run_migration(
                "arn:aws:ecs:eu-west-1:817685572750:task-definition/migration:2",
                inject_controlled_failure=True,
            )

        self.assertEqual(
            ecs.run_arguments[0]["taskDefinition"],
            "arn:aws:ecs:eu-west-1:817685572750:task-definition/migration:2",
        )
        self.assertEqual(
            ecs.run_arguments[0]["overrides"],
            {
                "containerOverrides": [
                    {
                        "name": "migration",
                        "command": CONTROLLED_MIGRATION_FAILURE_COMMAND,
                    }
                ]
            },
        )
        self.assertNotIn("environment", str(ecs.run_arguments[0]["overrides"]))

    def test_managed_prior_requires_exact_tags_and_normalized_service_pair(self) -> None:
        gateway = self.gateway(FakeMigrationEcs({}, {}))
        source_sha = "a" * 40
        image_digest = f"sha256:{'a' * 64}"
        repository = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"
        identity = ReleaseIdentity(source_sha, image_digest, repository)
        config = TaskDefinitionConfig(
            families=gateway.config.task_families,
            container_names=gateway.config.container_names,
            task_role_arn=gateway.config.task_role_arn,
            execution_role_arn=gateway.config.execution_role_arn,
        )
        source_tasks = {}
        for workload in ("web", "worker", "migration"):
            source_tasks[workload] = {
                "family": workload,
                "taskRoleArn": gateway.config.task_role_arn,
                "executionRoleArn": gateway.config.execution_role_arn,
                "networkMode": "awsvpc",
                "requiresCompatibilities": ["FARGATE"],
                "cpu": "256",
                "memory": "512",
                "containerDefinitions": [
                    {
                        "name": workload,
                        "image": f"{repository}@{image_digest}",
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
        normalized = build_task_definitions(source_tasks, identity, config)
        tasks = {workload: normalized[workload] for workload in ("web", "worker")}
        for task in tasks.values():
            task["status"] = "ACTIVE"
        prefix = "arn:aws:ecs:eu-west-1:817685572750:task-definition"
        references = {workload: f"{prefix}/{workload}:4" for workload in ("web", "worker")}
        pair = ActiveServicePair(
            source_sha=source_sha,
            image_digest=image_digest,
            web_task_definition_arn=references["web"],
            worker_task_definition_arn=references["worker"],
            web_desired_count=1,
            worker_desired_count=1,
        )
        exact_tags = [
            {"key": "ReleaseManager", "value": "DataTalksClub/website"},
            {"key": "Project", "value": "website"},
            {"key": "Environment", "value": "sandbox"},
        ]
        responses = {
            reference: (tasks[workload], exact_tags) for workload, reference in references.items()
        }
        gateway._task_definition_with_tags = Mock(  # type: ignore[method-assign]
            side_effect=lambda reference: responses[reference]
        )
        gateway.verify_active_service_pair(pair, identity)

        responses[references["worker"]] = (
            tasks["worker"],
            [{"key": "ReleaseManager", "value": "another-controller"}],
        )
        with self.assertRaisesMessage(ReleaseContractError, "management tags differ"):
            gateway.verify_active_service_pair(pair, identity)

        responses[references["worker"]] = (tasks["worker"] | {"status": "INACTIVE"}, exact_tags)
        with self.assertRaisesMessage(ReleaseContractError, "not ACTIVE"):
            gateway.verify_active_service_pair(pair, identity)

    def test_active_image_proof_binds_full_sha_tag_to_exact_digest(self) -> None:
        gateway = self.gateway(FakeMigrationEcs({}, {}))
        gateway.ecr = Mock()
        source_sha = "a" * 40
        image_digest = f"sha256:{'a' * 64}"
        gateway.ecr.describe_images.side_effect = [
            {"imageDetails": [{"imageDigest": image_digest}]},
            {"imageDetails": [{"imageDigest": image_digest}]},
        ]
        gateway.ecr.batch_get_image.return_value = {
            "failures": [],
            "images": [
                {
                    "imageId": {"imageDigest": image_digest},
                    "imageManifest": "{}",
                }
            ],
        }
        gateway.verify_image_digest_exists(
            "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox",
            source_sha,
            image_digest,
        )
        self.assertEqual(
            gateway.ecr.describe_images.call_args_list[0].kwargs["imageIds"],
            [{"imageTag": source_sha}],
        )

        gateway.ecr.describe_images.reset_mock(side_effect=True)
        gateway.ecr.describe_images.return_value = {
            "imageDetails": [{"imageDigest": f"sha256:{'b' * 64}"}]
        }
        with self.assertRaisesMessage(ReleaseContractError, "source SHA tag"):
            gateway.verify_image_digest_exists(
                "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox",
                source_sha,
                image_digest,
            )

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
