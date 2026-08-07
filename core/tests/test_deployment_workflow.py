from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import yaml  # type: ignore[import-untyped]
from django.test import SimpleTestCase

from deploy.aws_gateway import (
    CONTROLLED_MIGRATION_FAILURE_COMMAND,
    AwsReleaseConfig,
    AwsReleaseGateway,
)
from deploy.contracts import ActiveServicePair, ReleaseContractError, ReleaseIdentity
from deploy.task_definitions import (
    FIXED_NONSECRET_ENVIRONMENT,
    TaskDefinitionConfig,
    build_task_definitions,
)

ROOT = Path(__file__).resolve().parents[2]
DATABASE_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/database-url-Ab12Cd"
)
DJANGO_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/django-secret-key-Ef34Gh"
)


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
        recovery = next(step for step in deploy_steps if step.get("id") == "finalization_recovery")
        self.assertEqual(recovery["timeout-minutes"], 12)
        critical_upload_minutes = sum(
            step.get("timeout-minutes", 0)
            for step in deploy_steps
            if step.get("id") in {"evidence_upload", "smoke_upload", "success_record_upload"}
        )
        self.assertEqual(critical_upload_minutes, 6)

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
        self.assertIn("sandbox-published-image-", publish)
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
        self.assertEqual(workflow.count("vars.SANDBOX_AUTO_DEPLOY == 'true'"), 3)
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
        self.assertIn("probe_sandbox:", dispatch)
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
        self.assertIn("Validate exact wrong-claim probe inputs", wrong_claims)
        self.assertIn(
            "arn:aws:iam::817685572750:role/website-sandbox-github-deployer",
            wrong_claims,
        )
        self.assertNotIn("continue-on-error: true", wrong_claims)
        self.assertIn("deploy.oidc_claim_probe", wrong_claims)
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
                if "deploy.oidc_claim_probe" in step.get("run", "")
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

        exact_pattern = (
            "^arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
            "targetgroup/website-sandbox-web/[0-9a-f]{16}$"
        )
        self.assertIn(exact_pattern, validation)
        self.assertNotIn('test -n "$WEB_TARGET_GROUP_ARN"', validation)

    def test_route53_zone_is_exactly_validated_for_both_probe_roles_only(self) -> None:
        workflow_path = ROOT / ".github/workflows/ci.yml"
        workflow = workflow_path.read_text()
        document = yaml.safe_load(workflow)
        jobs = document["jobs"]
        variable_expression = "${{ vars.SANDBOX_ROUTE53_HOSTED_ZONE_ID }}"

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
            self.assertIn(
                'test "$HOSTED_ZONE_ID" = "Z05963572WVWFHDQZH5NE"',
                validation["run"],
            )

            probe = next(
                step
                for step in steps
                if f"python -m deploy.oidc_probe {role}" in step.get("run", "")
            )
            self.assertEqual(probe["env"]["HOSTED_ZONE_ID"], variable_expression)
            self.assertIn('--hosted-zone-id "$HOSTED_ZONE_ID"', probe["run"])

        self.assertEqual(workflow.count("vars.SANDBOX_ROUTE53_HOSTED_ZONE_ID"), 4)
        for job_name, job in jobs.items():
            if job_name not in {"probe-publisher", "probe-deployer"}:
                self.assertNotIn("SANDBOX_ROUTE53_HOSTED_ZONE_ID", str(job))

    def test_runbook_orders_probe_before_secret_population_and_releases(self) -> None:
        runbook = (ROOT / "_docs/runbooks/sandbox-release.md").read_text()
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
        runbook = (ROOT / "_docs/runbooks/sandbox-release.md").read_text()
        probe = runbook.split("## Post-bootstrap OIDC probe", maxsplit=1)[1].split(
            "## Select and promote a release", maxsplit=1
        )[0]
        normalized_probe = " ".join(probe.split())

        self.assertIn("`SANDBOX_ROUTE53_HOSTED_ZONE_ID`", runbook)
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

    def test_runbook_reconciles_only_after_all_b_exercises_and_final_promotion(self) -> None:
        runbook = (ROOT / "_docs/runbooks/sandbox-release.md").read_text()
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
        self.assertIn("SANDBOX_AUTO_DEPLOY=true", reconciliation)
        self.assertIn(
            "repository-level variable must exist and be exactly `SANDBOX_AUTO_DEPLOY=false`",
            reconciliation,
        )
        self.assertIn(
            "An absent repository variable is not a disabled state",
            " ".join(reconciliation.split()),
        )
        self.assertIn("Never delete this repository variable", reconciliation)
        self.assertNotIn("gh variable delete SANDBOX_AUTO_DEPLOY", runbook)
        self.assertNotIn("After first bootstrap succeeds, set Terraform", runbook)

    def test_runbook_documents_build_once_records_reuse_and_safe_auto_capture(self) -> None:
        runbook = (ROOT / "_docs/runbooks/sandbox-release.md").read_text()

        self.assertIn("sandbox-published-image-e2b93beb1544170b6177ba55ea8fd6530b2e57a3", runbook)
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
