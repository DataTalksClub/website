#!/usr/bin/env bash
# One reviewed, one-shot development website schema recovery path.

dev_reset_current_main() {
  local main_sha
  if ! main_sha="$(gh api "repos/${GITHUB_REPOSITORY:?}/git/ref/heads/main" --jq '.object.sha' 2>/dev/null)"; then
    echo "Cannot verify current main for development schema reset" >&2
    return 1
  fi
  if [[ "$main_sha" != "$SOURCE_SHA" ]]; then
    echo "Development schema reset requires current main" >&2
    return 1
  fi
}

dev_reset_validate() {
  RESET_REQUESTED=0
  if [[ -z "${DTC_DEV_SCHEMA_RESET_CONFIRMATION:-}" ]]; then return 0; fi
  if [[ "$TARGET" != dev || "$DTC_DEV_SCHEMA_RESET_CONFIRMATION" != 'RESET dtc_website_dev.public' ||
        "${GITHUB_EVENT_NAME:-}" != workflow_dispatch || "${GITHUB_REF:-}" != refs/heads/main ||
        "${GITHUB_RUN_ATTEMPT:-}" != 1 || "${GITHUB_SHA:-}" != "$SOURCE_SHA" ||
        "$(git -C "$REPO_ROOT" rev-parse HEAD)" != "$SOURCE_SHA" ]]; then
    echo "Development schema reset dispatch guard refused" >&2
    return 1
  fi
  dev_reset_current_main
  RESET_REQUESTED=1
  python3 -m deploy.dev_reset_ecs preflight
}

dev_reset_drain() {
  dev_reset_current_main
  RESET_STAGE="drain"
  RESET_DRAIN_STARTED=1
  python3 -m deploy.dev_reset_ecs drain
  RESET_STAGE="quiescence"
  python3 -m deploy.dev_reset_ecs quiescent
}

dev_reset_record_failure() {
  local action_succeeded="$1"
  python3 -m deploy.dev_reset_ecs state > "$WORKDIR/reset-observed-state.json" || true
  jq --arg stage "$RESET_STAGE" --argjson may_begin "$RESET_MAY_HAVE_BEGUN" \
    --argjson action_succeeded "$action_succeeded" \
    --slurpfile observed "$WORKDIR/reset-observed-state.json" \
    '.reset = {stage: $stage, mutation_may_have_begun: ($may_begin == 1),
      service_action_succeeded: $action_succeeded,
      observed_services: ($observed[0] // null),
      retry: "Diagnose, then dispatch a fresh normal Deploy Dev run from current main"}' \
    "$RECEIPT" > "$RECEIPT.tmp" && mv "$RECEIPT.tmp" "$RECEIPT"
}

dev_reset_failure() {
  local action_succeeded=false
  if [[ $RESET_MAY_HAVE_BEGUN -eq 1 ]]; then
    if python3 -m deploy.dev_reset_ecs stop; then action_succeeded=true; fi
    if [[ "$action_succeeded" == true ]]; then
      echo "Reset path failed; both development website services are stopped. Diagnose, then use a fresh normal main dispatch." >&2
    else
      echo "STOP NOT VERIFIED. The reset path failed and service state needs manual recovery using the receipt." >&2
    fi
  elif [[ $RESET_DRAIN_STARTED -eq 1 ]]; then
    if python3 "$REPO_ROOT/deploy/recovery_receipt.py" recover \
        --receipt "$RECEIPT" --region "$AWS_REGION" --timeout-seconds 900 \
        --cli "${RECOVERY_AWS_CLI:-aws}"; then
      action_succeeded=true
    fi
    if [[ "$action_succeeded" != true ]]; then
      echo "Service restoration not verified; use the receipt for manual recovery." >&2
    fi
  fi
  dev_reset_record_failure "$action_succeeded"
}
