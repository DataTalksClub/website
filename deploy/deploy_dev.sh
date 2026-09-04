#!/usr/bin/env bash
# Deploy one image digest to the dev website ECS services.
#
# Mirrors course-management-platform's deploy/deploy_dev.sh: describe the
# currently-registered task definition, mutate only the image and VERSION,
# register the new revision, and point the service(s) at it. Cluster,
# service, and task-family names are fixed below rather than configured --
# this only ever deploys to the one reviewed dev environment (main/dev in
# DataTalksClub/aws-infra), so there is nothing to select. Only two values
# can't be hardcoded, because AWS generates them and they're needed to run
# the one-off migration task: the task subnet and security-group ids. They
# come in as DEV_SUBNET_IDS / DEV_SECURITY_GROUP_IDS (comma-separated).
#
# Unlike course-management-platform, this assumes an OIDC role rather than a
# static access key, and the image is addressed by digest, not a mutable tag
# -- the same immutable-release discipline the rest of this repository uses.
#
# Usage: deploy_dev.sh <image-digest-ref>
#   image-digest-ref: the full "repo@sha256:..." pushed by this same workflow run.

set -euo pipefail

IMAGE="${1:?usage: deploy_dev.sh <repo@sha256:digest>}"
VERSION="${GITHUB_SHA:?GITHUB_SHA must be set}"
SUBNET_IDS="${DEV_SUBNET_IDS:?DEV_SUBNET_IDS must be set (comma-separated subnet-... ids)}"
SECURITY_GROUP_IDS="${DEV_SECURITY_GROUP_IDS:?DEV_SECURITY_GROUP_IDS must be set (comma-separated sg-... ids)}"

AWS_REGION="eu-west-1"
CLUSTER="website-production"
WEB_SERVICE="website-dev-web"
WORKER_SERVICE="website-dev-worker"
WEB_FAMILY="website-dev-web"
WORKER_FAMILY="website-dev-worker"
MIGRATION_FAMILY="website-dev-migration"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

register_family() {
  local family="$1"
  local current="$WORKDIR/${family}-current.json"
  local updated="$WORKDIR/${family}-updated.json"

  echo "Describing ${family}"
  aws ecs describe-task-definition --region "$AWS_REGION" \
    --task-definition "$family" > "$current"

  echo "Updating ${family} to ${IMAGE}"
  python3 "$(dirname "$0")/update_task_definition_image.py" \
    "$current" "$IMAGE" "$VERSION" "$updated"

  echo "Registering new ${family} revision"
  # The dev deployer role's RegisterTaskDefinition grant requires exactly
  # these three tags at creation time (main/dev/deployment_iam.tf's
  # website_release_registration_tags) -- omitting or changing any of them
  # fails closed with AccessDenied, on purpose.
  aws ecs register-task-definition --region "$AWS_REGION" \
    --cli-input-json "file://${updated}" \
    --tags \
      key=Project,value=dtc-website \
      key=Environment,value=dev \
      key=ReleaseManager,value=DataTalksClub/website \
    > /dev/null
}

update_service() {
  local service="$1"
  local family="$2"

  echo "Promoting ${service} to the new ${family} revision"
  aws ecs update-service --region "$AWS_REGION" \
    --cluster "$CLUSTER" --service "$service" --task-definition "$family" > /dev/null
}

register_family "$WEB_FAMILY"
register_family "$WORKER_FAMILY"
register_family "$MIGRATION_FAMILY"

echo "Running the migration task and waiting for it to finish, before promoting either service"
network_config="awsvpcConfiguration={subnets=[${SUBNET_IDS}],securityGroups=[${SECURITY_GROUP_IDS}],assignPublicIp=DISABLED}"
task_arn="$(aws ecs run-task --region "$AWS_REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$MIGRATION_FAMILY" \
  --launch-type FARGATE \
  --network-configuration "$network_config" \
  --query 'tasks[0].taskArn' --output text)"
aws ecs wait tasks-stopped --region "$AWS_REGION" --cluster "$CLUSTER" --tasks "$task_arn"
exit_code="$(aws ecs describe-tasks --region "$AWS_REGION" --cluster "$CLUSTER" --tasks "$task_arn" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
if [[ "$exit_code" != "0" ]]; then
  echo "Migration task exited $exit_code; not promoting either service." >&2
  exit 1
fi

update_service "$WEB_SERVICE" "$WEB_FAMILY"
update_service "$WORKER_SERVICE" "$WORKER_FAMILY"

echo "dev deployment completed successfully."
