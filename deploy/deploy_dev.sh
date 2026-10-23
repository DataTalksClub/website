#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIRECTORY/.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Preflight: the AWS ECS dev path is retired in favor of Coolify
# (.github/workflows/deploy-coolify.yml). Fail fast here with a clear
# message if required AWS resources are gone, instead of failing late or cryptic.
# Hard error (exit 1), never soft-pass. Do NOT recreate ALB/TG/ECS.
DTC_DEPLOYMENT_TARGET="website-development"
export DTC_DEPLOYMENT_TARGET
eval "$(python3 -m deploy.deployment_targets profile)"
AWS_REGION="${AWS_REGION:-eu-west-1}"
CLUSTER="${ECS_CLUSTER_NAME:-$CLUSTER_NAME}"
export ECS_CLUSTER_NAME="$CLUSTER"

if ! SERVICE_DESC=$(aws ecs describe-services \
    --region "$AWS_REGION" \
    --cluster "$CLUSTER" \
    --services "$WEB_SERVICE_NAME" "$WORKER_SERVICE_NAME" 2>&1); then
    echo "Error: ECS services ${WEB_SERVICE_NAME}, ${WORKER_SERVICE_NAME} not found in cluster ${CLUSTER}." >&2
    echo "The AWS ECS Deploy Dev path is retired; live staging CD is Deploy Website Coolify (Contabo)." >&2
    echo "aws error was: ${SERVICE_DESC}" >&2
    exit 1
fi
if [ "$(echo "${SERVICE_DESC}" | jq -r '.failures | length')" != "0" ]; then
    echo "Error: ECS service lookup failed in cluster ${CLUSTER}: $(echo "${SERVICE_DESC}" | jq -c '.failures')" >&2
    echo "The AWS ECS Deploy Dev path is retired; live staging CD is Deploy Website Coolify (Contabo)." >&2
    exit 1
fi
TG_ARNS=$(echo "${SERVICE_DESC}" | jq -r '.services[].loadBalancers[].targetGroupArn // empty')
for TG in ${TG_ARNS}; do
    if ! aws elbv2 describe-target-groups --region "$AWS_REGION" --target-group-arns "${TG}" > /dev/null 2>&1; then
        echo "Error: target group ${TG} attached to dev service does not exist." >&2
        echo "The ELB target group was deleted or is missing; the AWS ECS Deploy Dev path is retired." >&2
        echo "Live staging CD is Deploy Website Coolify (Contabo). Do NOT recreate the ALB/TG/ECS unless AWS ECS staging is explicitly required." >&2
        exit 1
    fi
done

exec bash "$SCRIPT_DIRECTORY/deploy_website.sh" dev "$@"
