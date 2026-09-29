#!/usr/bin/env bash
# EXIT handling for normal promotion and the one-shot dev schema reset. The
# redacted receipt lives outside the scratch directory so failures retain it.
# A post-mutation normal failure restores the prior task definitions; a reset
# failure may require leaving services stopped because the schema may differ.

recover_or_clean() {
  local status=$?
  if [[ $status -ne 0 && $RESET_REQUESTED -eq 1 && -f "$RECEIPT" ]]; then
    dev_reset_failure
  elif [[ $status -ne 0 && $MUTATION_STARTED -eq 1 && -f "$RECEIPT" ]]; then
    echo "Deployment failed after the first service mutation; attempting bounded recovery to the captured prior state (receipt: ${RECEIPT})" >&2
    if ! python3 "$REPO_ROOT/deploy/recovery_receipt.py" recover \
        --receipt "$RECEIPT" --region "$AWS_REGION" --timeout-seconds 900 \
        --cli "${RECOVERY_AWS_CLI:-aws}"; then
      echo "AUTOMATIC RECOVERY FAILED. Restore both services by hand from ${RECEIPT}; it names the exact prior task-definition ARNs and desired counts. The deployment still counts as failed." >&2
    fi
  fi
  rm -rf "$WORKDIR"
}
