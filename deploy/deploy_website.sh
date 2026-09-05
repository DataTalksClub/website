#!/usr/bin/env bash
# Deploy one immutable image to the reviewed dev or production website service.

set -euo pipefail

TARGET="${1:?usage: deploy_website.sh <dev|production> <repo@sha256:digest> <version> <source-sha>}"
IMAGE="${2:?image digest reference is required}"
VERSION="${3:?version is required}"
SOURCE_SHA="${4:?source SHA is required}"
CLUSTER="${ECS_CLUSTER_NAME:?ECS_CLUSTER_NAME must be set}"

AWS_REGION="eu-west-1"
EXPECTED_CLUSTER="website-production"
REPOSITORY="387546586013.dkr.ecr.eu-west-1.amazonaws.com/website-production"

if [[ "$CLUSTER" != "$EXPECTED_CLUSTER" ]]; then
  echo "ECS_CLUSTER_NAME must be ${EXPECTED_CLUSTER}" >&2
  exit 1
fi
IMAGE_REPOSITORY="${IMAGE%@*}"
IMAGE_DIGEST="${IMAGE##*@}"
if [[ "$IMAGE_REPOSITORY" != "$REPOSITORY" || ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "image must be an immutable digest in ${REPOSITORY}" >&2
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

case "$TARGET" in
  dev)
    NAMESPACE="website-dev"
    RUNTIME_ENVIRONMENT="development"
    ENVIRONMENT_TAG="dev"
    PROJECT_TAG="dtc-website"
    BASE_URL="https://dev.datatalks.club"
    WEB_DESIRED_COUNT=1
    WORKER_DESIRED_COUNT=0
    ;;
  production)
    NAMESPACE="website-production"
    RUNTIME_ENVIRONMENT="production"
    ENVIRONMENT_TAG="production"
    PROJECT_TAG="website"
    BASE_URL="https://prod.datatalks.club"
    WEB_DESIRED_COUNT=2
    WORKER_DESIRED_COUNT=1
    ;;
  *)
    echo "target must be dev or production" >&2
    exit 1
    ;;
esac

WEB_SERVICE="${NAMESPACE}-web"
WORKER_SERVICE="${NAMESPACE}-worker"
WEB_FAMILY="${NAMESPACE}-web"
WORKER_FAMILY="${NAMESPACE}-worker"
MIGRATION_FAMILY="${NAMESPACE}-migration"

mkdir -p .tmp
WORKDIR="$(mktemp -d ".tmp/deploy-${TARGET}.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

register_family() {
  local family="$1"
  local current="$WORKDIR/${family}-current.json"
  local updated="$WORKDIR/${family}-updated.json"
  local registered="$WORKDIR/${family}-registered.json"

  echo "Describing ${family}" >&2
  aws ecs describe-task-definition --region "$AWS_REGION" \
    --task-definition "$family" > "$current"
  python3 "$(dirname "$0")/update_task_definition_image.py" \
    "$current" "$IMAGE" "$VERSION" "$SOURCE_SHA" "$IMAGE_DIGEST" \
    "$RUNTIME_ENVIRONMENT" "$updated"

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

echo "Discovering the migration network from ${WEB_SERVICE}"
aws ecs describe-services --region "$AWS_REGION" \
  --cluster "$CLUSTER" --services "$WEB_SERVICE" > "$WORKDIR/service.json"
jq -e '
  (.failures | length) == 0 and
  (.services | length) == 1 and
  (.services[0].networkConfiguration.awsvpcConfiguration.subnets | length) > 0 and
  (.services[0].networkConfiguration.awsvpcConfiguration.securityGroups | length) > 0
' "$WORKDIR/service.json" > /dev/null
NETWORK_CONFIGURATION="$(jq -c '.services[0].networkConfiguration' "$WORKDIR/service.json")"

WEB_TASK_DEFINITION="$(register_family "$WEB_FAMILY")"
WORKER_TASK_DEFINITION="$(register_family "$WORKER_FAMILY")"
MIGRATION_TASK_DEFINITION="$(register_family "$MIGRATION_FAMILY")"

echo "Running migrations before either service is promoted"
aws ecs run-task --region "$AWS_REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$MIGRATION_TASK_DEFINITION" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIGURATION" \
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

echo "Promoting ${WEB_SERVICE}"
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$CLUSTER" --service "$WEB_SERVICE" \
  --task-definition "$WEB_TASK_DEFINITION" --desired-count "$WEB_DESIRED_COUNT" > /dev/null

echo "Promoting ${WORKER_SERVICE}"
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$CLUSTER" --service "$WORKER_SERVICE" \
  --task-definition "$WORKER_TASK_DEFINITION" --desired-count "$WORKER_DESIRED_COUNT" > /dev/null

aws ecs wait services-stable --region "$AWS_REGION" \
  --cluster "$CLUSTER" --services "$WEB_SERVICE" "$WORKER_SERVICE"

echo "Verifying ${BASE_URL} reports the promoted release"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "${BASE_URL}/api/health/" \
      --output "$WORKDIR/health.json" &&
     jq -e \
       --arg version "$VERSION" \
       --arg source_sha "$SOURCE_SHA" \
       --arg image_digest "$IMAGE_DIGEST" \
       '.status == "ok" and .version == $version and .source_sha == $source_sha and .image_digest == $image_digest' \
       "$WORKDIR/health.json" > /dev/null; then
    echo "${TARGET} deployment completed successfully"
    exit 0
  fi
  sleep 10
done

echo "${BASE_URL} did not report the promoted release" >&2
exit 1
