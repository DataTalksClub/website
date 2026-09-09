#!/usr/bin/env bash
# Deploy one immutable image to the reviewed dev or production website service.
#
# The python3 calls below are deliberately bare of any interpreter pin HERE:
# both deploying workflows start this script through `uv run --frozen`, whose
# locked project environment is first on PATH, so python3 resolves to the
# pinned interpreter (REL-19). ci/tests/test_deploy_release_verification.py
# pins the same seam by symlinking python3 to the test interpreter.
#
# Every physical value -- cluster, repository, services, families, counts,
# tags, URLs -- is read from the reviewed deployment target registry
# (deploy/deployment_targets.py, the single target-definition owner, REL-19);
# this script is only the transport that shells out to AWS. DTC_DEPLOYMENT_
# TARGET selects the reviewed profile before the profile is read, so nothing
# here can deploy to an unreviewed stack.

set -euo pipefail

# Receipts and scratch land in the caller's .tmp/ (the workflows run from the
# checkout root), and the deploy package must import as a module even when the
# caller's cwd is elsewhere, so the checkout root goes on PYTHONPATH.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

TARGET="${1:?usage: deploy_website.sh <dev|production> <repo@sha256:digest> <version> <source-sha>}"
IMAGE="${2:?image digest reference is required}"
VERSION="${3:?version is required}"
SOURCE_SHA="${4:?source SHA is required}"

case "$TARGET" in
  dev) DTC_DEPLOYMENT_TARGET="website-development" ;;
  production) DTC_DEPLOYMENT_TARGET="website-production" ;;
  *)
    echo "target must be dev or production" >&2
    exit 1
    ;;
esac
export DTC_DEPLOYMENT_TARGET

# The reviewed profile. Fails closed on a retired or unknown target; every
# value below is shell-quoted registry output.
eval "$(python3 -m deploy.deployment_targets profile)"

: "${AWS_REGION:?profile did not provide AWS_REGION}" \
  "${CLUSTER_NAME:?profile did not provide CLUSTER_NAME}" \
  "${ECR_REPOSITORY_URI:?profile did not provide ECR_REPOSITORY_URI}" \
  "${WEB_SERVICE_NAME:?profile did not provide WEB_SERVICE_NAME}" \
  "${WORKER_SERVICE_NAME:?profile did not provide WORKER_SERVICE_NAME}" \
  "${WEB_FAMILY:?profile did not provide WEB_FAMILY}" \
  "${WORKER_FAMILY:?profile did not provide WORKER_FAMILY}" \
  "${MIGRATION_FAMILY:?profile did not provide MIGRATION_FAMILY}" \
  "${BASE_URL:?profile did not provide BASE_URL}" \
  "${WEB_DESIRED_COUNT:?profile did not provide WEB_DESIRED_COUNT}" \
  "${WORKER_DESIRED_COUNT:?profile did not provide WORKER_DESIRED_COUNT}" \
  "${PROJECT_TAG:?profile did not provide PROJECT_TAG}" \
  "${ENVIRONMENT_TAG:?profile did not provide ENVIRONMENT_TAG}"

CLUSTER="${ECS_CLUSTER_NAME:?ECS_CLUSTER_NAME must be set}"
if [[ "$CLUSTER" != "$CLUSTER_NAME" ]]; then
  echo "ECS_CLUSTER_NAME must be ${CLUSTER_NAME}" >&2
  exit 1
fi
IMAGE_REPOSITORY="${IMAGE%@*}"
IMAGE_DIGEST="${IMAGE##*@}"
if [[ "$IMAGE_REPOSITORY" != "$ECR_REPOSITORY_URI" || ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "image must be an immutable digest in ${ECR_REPOSITORY_URI}" >&2
  exit 1
fi
if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 1
fi
if [[ ! "$VERSION" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$ ]] ||
   [[ "${VERSION: -7}" != "${SOURCE_SHA:0:7}" ]]; then
  echo "version must be a UTC release timestamp ending in the source SHA prefix" >&2
  exit 1
fi

mkdir -p .tmp
WORKDIR="$(mktemp -d ".tmp/deploy-${TARGET}.XXXXXX")"
# The receipt lives outside WORKDIR on purpose: WORKDIR's raw API responses are
# scratch, but the receipt is the durable, redacted record of the exact prior
# service state, written before the first mutation and kept after every exit
# (REL-02).
RECEIPT_DIR=".tmp/deploy-receipts"
mkdir -p "$RECEIPT_DIR"
RECEIPT="${RECEIPT_DIR}/${TARGET}-${VERSION}.json"
MUTATION_STARTED=0

recover_or_clean() {
  local status=$?
  if [[ $status -ne 0 && $MUTATION_STARTED -eq 1 && -f "$RECEIPT" ]]; then
    echo "Deployment failed after the first service mutation; attempting bounded recovery to the captured prior state (receipt: ${RECEIPT})" >&2
    if ! python3 "$(dirname "$0")/recovery_receipt.py" recover \
        --receipt "$RECEIPT" --region "$AWS_REGION" --timeout-seconds 900 \
        --cli "${RECOVERY_AWS_CLI:-aws}"; then
      echo "AUTOMATIC RECOVERY FAILED. Restore both services by hand from ${RECEIPT}; it names the exact prior task-definition ARNs and desired counts. The deployment still counts as failed." >&2
    fi
  fi
  rm -rf "$WORKDIR"
}
trap recover_or_clean EXIT

# $1 = workload name, $2 = the describe-task-definition source: the service's
# active task-definition ARN for the long-running services (REL-08: never the
# most recently registered family revision) or the migration family, whose
# tasks are one-off and therefore have no service to read an accepted revision
# from -- that family's latest registered revision is its only possible source,
# so it is validated content-wise like the others instead.
register_family() {
  local workload="$1"
  local source="$2"
  local current="$WORKDIR/${workload}-current.json"
  local updated="$WORKDIR/${workload}-updated.json"
  local registered="$WORKDIR/${workload}-registered.json"

  echo "Describing ${workload} source: ${source}" >&2
  aws ecs describe-task-definition --region "$AWS_REGION" \
    --task-definition "$source" > "$current"
  # This function runs inside $( ) command substitution, where set -e does
  # not apply, so the promotion gate is enforced explicitly: a refused source
  # must stop the deployment before register-task-definition (REL-08).
  if ! python3 -m deploy.update_task_definition_image \
      "$current" "$IMAGE" "$VERSION" "$SOURCE_SHA" "$IMAGE_DIGEST" \
      "$workload" "$updated"; then
    echo "${workload} source was refused; nothing was registered" >&2
    return 1
  fi

  local family
  case "$workload" in
    web) family="$WEB_FAMILY" ;;
    worker) family="$WORKER_FAMILY" ;;
    migration) family="$MIGRATION_FAMILY" ;;
  esac

  echo "Registering ${family} for ${VERSION}" >&2
  aws ecs register-task-definition --region "$AWS_REGION" \
    --cli-input-json "file://${updated}" \
    --tags \
      "key=Project,value=${PROJECT_TAG}" \
      "key=Environment,value=${ENVIRONMENT_TAG}" \
      key=ReleaseManager,value=DataTalksClub/website \
    > "$registered"
  jq -er '.taskDefinition.taskDefinitionArn' "$registered"
}

echo "Discovering the migration network and the current service state"
# Both services are described in one call, requested web-first so
# services[0] below is the web service. Their current taskDefinition ARNs are
# the promotion sources for REL-08.
aws ecs describe-services --region "$AWS_REGION" \
  --cluster "$CLUSTER" --services "$WEB_SERVICE_NAME" "$WORKER_SERVICE_NAME" > "$WORKDIR/service.json"
jq -e '
  (.failures | length) == 0 and
  (.services | length) == 2 and
  (.services[0].taskDefinition | startswith("arn:aws:ecs:")) and
  (.services[1].taskDefinition | startswith("arn:aws:ecs:")) and
  (.services[0].networkConfiguration.awsvpcConfiguration.subnets | length) > 0 and
  (.services[0].networkConfiguration.awsvpcConfiguration.securityGroups | length) > 0
' "$WORKDIR/service.json" > /dev/null
NETWORK_CONFIGURATION="$(jq -c '.services[0].networkConfiguration' "$WORKDIR/service.json")"
WEB_SOURCE="$(jq -er '.services[0].taskDefinition' "$WORKDIR/service.json")"
WORKER_SOURCE="$(jq -er '.services[1].taskDefinition' "$WORKDIR/service.json")"

echo "Capturing the redacted recovery receipt before any mutation: ${RECEIPT}"
python3 "$(dirname "$0")/recovery_receipt.py" capture \
  --target "$TARGET" \
  --cluster "$CLUSTER" \
  --region "$AWS_REGION" \
  --version "$VERSION" \
  --source-sha "$SOURCE_SHA" \
  --image "$IMAGE" \
  --service "$WEB_SERVICE_NAME" \
  --service "$WORKER_SERVICE_NAME" \
  --services-json "$WORKDIR/service.json" \
  --output "$RECEIPT"

WEB_TASK_DEFINITION="$(register_family web "$WEB_SOURCE")"
WORKER_TASK_DEFINITION="$(register_family worker "$WORKER_SOURCE")"
MIGRATION_TASK_DEFINITION="$(register_family migration "$MIGRATION_FAMILY")"

echo "Running migrations and loading required code-owned data before either service is promoted"
aws ecs run-task --region "$AWS_REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$MIGRATION_TASK_DEFINITION" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIGURATION" \
  --overrides '{"containerOverrides":[{"name":"migration","command":["prepare_deployment"]}]}' \
  > "$WORKDIR/migration.json"
jq -e '(.failures | length) == 0 and (.tasks | length) == 1' \
  "$WORKDIR/migration.json" > /dev/null
MIGRATION_TASK="$(jq -er '.tasks[0].taskArn' "$WORKDIR/migration.json")"
aws ecs wait tasks-stopped --region "$AWS_REGION" \
  --cluster "$CLUSTER" --tasks "$MIGRATION_TASK"
aws ecs describe-tasks --region "$AWS_REGION" \
  --cluster "$CLUSTER" --tasks "$MIGRATION_TASK" > "$WORKDIR/migration-result.json"
MIGRATION_EXIT_CODE="$(jq -er '.tasks[0].containers[] | select(.name == "migration") | .exitCode' "$WORKDIR/migration-result.json")"
if [[ "$MIGRATION_EXIT_CODE" != "0" ]]; then
  echo "Migration task exited ${MIGRATION_EXIT_CODE}; services were not changed" >&2
  exit 1
fi

# The first service mutation starts here: any failure past this point leaves
# the site on a mixed or new release, so the EXIT trap runs the bounded
# recovery against the captured receipt.
MUTATION_STARTED=1

echo "Promoting ${WEB_SERVICE_NAME}"
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$CLUSTER" --service "$WEB_SERVICE_NAME" \
  --task-definition "$WEB_TASK_DEFINITION" --desired-count "$WEB_DESIRED_COUNT" > /dev/null

echo "Promoting ${WORKER_SERVICE_NAME}"
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$CLUSTER" --service "$WORKER_SERVICE_NAME" \
  --task-definition "$WORKER_TASK_DEFINITION" --desired-count "$WORKER_DESIRED_COUNT" > /dev/null

aws ecs wait services-stable --region "$AWS_REGION" \
  --cluster "$CLUSTER" --services "$WEB_SERVICE_NAME" "$WORKER_SERVICE_NAME"

# REL-06: services-stable alone does not prove a coherent release.  Every
# check below is fail-closed -- a stale worker, a mixed web rollout, a
# database-unready response, or a hanging connection all prevent the success
# record (and the EXIT trap treats any of them as a failed deployment).
echo "Verifying both services are on the promoted definitions"
aws ecs describe-services --region "$AWS_REGION" \
  --cluster "$CLUSTER" --services "$WEB_SERVICE_NAME" "$WORKER_SERVICE_NAME" \
  > "$WORKDIR/promoted.json"
jq -e \
  --arg web "$WEB_SERVICE_NAME" --arg web_arn "$WEB_TASK_DEFINITION" --argjson web_desired "$WEB_DESIRED_COUNT" \
  --arg worker "$WORKER_SERVICE_NAME" --arg worker_arn "$WORKER_TASK_DEFINITION" --argjson worker_desired "$WORKER_DESIRED_COUNT" '
  ([.services[] | select(.serviceName == $web)][0]) as $web_svc |
  ([.services[] | select(.serviceName == $worker)][0]) as $worker_svc |
  ($web_svc.taskDefinition == $web_arn) and
  ($web_svc.desiredCount == $web_desired) and
  ($web_svc.runningCount == $web_desired) and
  (($web_svc.deployments | length) == 1) and
  ($worker_svc.taskDefinition == $worker_arn) and
  ($worker_svc.desiredCount == $worker_desired) and
  ($worker_svc.runningCount == $worker_desired)' \
  "$WORKDIR/promoted.json" > /dev/null

verify_running_tasks() {
  local service="$1" expected_arn="$2"
  # A zero-desired service has nothing running; that is its correct promoted
  # state (dev's worker), recorded here as the documented limitation that dev
  # success cannot exercise a long-lived worker -- the self-check below runs
  # its definition once instead.
  aws ecs list-tasks --region "$AWS_REGION" --cluster "$CLUSTER" \
    --service "$service" --output json > "$WORKDIR/${service}-task-list.json"
  local task_arns
  task_arns="$(jq -r '.taskArns[]' "$WORKDIR/${service}-task-list.json")"
  if [[ -z "$task_arns" ]]; then
    return 0
  fi
  local task_args=()
  while IFS= read -r task_arn; do task_args+=("$task_arn"); done <<< "$task_arns"
  aws ecs describe-tasks --region "$AWS_REGION" --cluster "$CLUSTER" \
    --tasks "${task_args[@]}" > "$WORKDIR/${service}-tasks.json"
  jq -e --arg expected "$expected_arn" \
    '([.tasks[] | select(.lastStatus == "RUNNING")] | length) > 0 and
     ([.tasks[] | select(.lastStatus == "RUNNING")] | all(.taskDefinitionArn == $expected))' \
    "$WORKDIR/${service}-tasks.json" > /dev/null
}

echo "Verifying every running web task carries the promoted definition"
verify_running_tasks "$WEB_SERVICE_NAME" "$WEB_TASK_DEFINITION"
echo "Verifying every running worker task carries the promoted definition"
verify_running_tasks "$WORKER_SERVICE_NAME" "$WORKER_TASK_DEFINITION"

echo "Running the bounded worker self-check on the promoted worker definition"
# One-off task on the exact definition the worker service runs: a system.noop
# durable intent round-trips the signed job ingress and must reach SUCCEEDED
# within this task's budget.  It contacts no real provider and sends no email;
# it proves the promoted worker image, configuration, database access and
# durable-job execution path -- which a zero-worker dev service otherwise
# never exercises.
aws ecs run-task --region "$AWS_REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$WORKER_TASK_DEFINITION" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIGURATION" \
  --overrides '{"containerOverrides":[{"name":"worker","command":["python","manage.py","jobs_ingress_selftest"]}]}' \
  > "$WORKDIR/worker-selfcheck.json"
jq -e '(.failures | length) == 0 and (.tasks | length) == 1' \
  "$WORKDIR/worker-selfcheck.json" > /dev/null
WORKER_SELFCHECK_TASK="$(jq -er '.tasks[0].taskArn' "$WORKDIR/worker-selfcheck.json")"
aws ecs wait tasks-stopped --region "$AWS_REGION" \
  --cluster "$CLUSTER" --tasks "$WORKER_SELFCHECK_TASK"
aws ecs describe-tasks --region "$AWS_REGION" \
  --cluster "$CLUSTER" --tasks "$WORKER_SELFCHECK_TASK" \
  > "$WORKDIR/worker-selfcheck-result.json"
WORKER_SELFCHECK_EXIT="$(jq -er '.tasks[0].containers[] | select(.name == "worker") | .exitCode' "$WORKDIR/worker-selfcheck-result.json")"
if [[ "$WORKER_SELFCHECK_EXIT" != "0" ]]; then
  echo "Worker self-check exited ${WORKER_SELFCHECK_EXIT}; the promoted worker cannot execute durable jobs" >&2
  exit 1
fi

echo "Verifying ${BASE_URL} reports the promoted release"
# HEALTH_POLL_SECONDS exists so tests can shorten the retry loop; production
# always uses the default.  The connect/max-time deadlines keep a hanging
# connection inside the retry budget instead of exceeding it, and
# /health/ready is the bounded database-and-migrations check: its 503 is a
# failed attempt, never a success record.
HEALTH_POLL_SECONDS="${HEALTH_POLL_SECONDS:-10}"
HEALTH_CONNECT_TIMEOUT="${HEALTH_CONNECT_TIMEOUT:-5}"
HEALTH_MAX_TIME="${HEALTH_MAX_TIME:-20}"
HEALTH_MAX_ATTEMPTS="${HEALTH_MAX_ATTEMPTS:-30}"
for attempt in $(seq 1 "$HEALTH_MAX_ATTEMPTS"); do
  if curl --fail --silent --show-error \
      --connect-timeout "$HEALTH_CONNECT_TIMEOUT" --max-time "$HEALTH_MAX_TIME" \
      "${BASE_URL}/api/health/" \
      --output "$WORKDIR/health.json" &&
     jq -e \
       --arg version "$VERSION" \
       --arg source_sha "$SOURCE_SHA" \
       --arg image_digest "$IMAGE_DIGEST" \
       '.status == "ok" and .version == $version and .source_sha == $source_sha and .image_digest == $image_digest' \
       "$WORKDIR/health.json" > /dev/null &&
     curl --fail --silent --show-error \
       --connect-timeout "$HEALTH_CONNECT_TIMEOUT" --max-time "$HEALTH_MAX_TIME" \
       "${BASE_URL}/health/ready" \
       --output "$WORKDIR/ready.json" &&
     curl --fail --silent --show-error \
       --connect-timeout "$HEALTH_CONNECT_TIMEOUT" --max-time "$HEALTH_MAX_TIME" \
       "${BASE_URL}/" \
       --output "$WORKDIR/home.html"; then
    python3 "$(dirname "$0")/recovery_receipt.py" mark-promoted "$RECEIPT"
    echo "${TARGET} deployment completed successfully"
    exit 0
  fi
  sleep "$HEALTH_POLL_SECONDS"
done

echo "${BASE_URL} did not report the promoted release" >&2
exit 1
