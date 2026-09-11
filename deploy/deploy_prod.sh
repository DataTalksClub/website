#!/usr/bin/env bash

set -euo pipefail

RELEASE_FILE="${1:?usage: deploy_prod.sh <dev-release.json>}"
IMAGE="$(jq -er '.image' "$RELEASE_FILE")"
VERSION="$(jq -er '.version' "$RELEASE_FILE")"
SOURCE_SHA="$(jq -er '.source_sha' "$RELEASE_FILE")"

# Provenance bindings for the production receipt (REL-07): the checkout whose
# deployment code drives this promotion, and the dev run whose verified
# release the file carries.  Both are identifiers; neither is secret.
export CONTROLLER_SHA="${CONTROLLER_SHA:-${GITHUB_SHA:-}}"
export DEV_RUN_ID="$(jq -er '.dev_run_id // ""' "$RELEASE_FILE")"

SCRIPT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIRECTORY/deploy_website.sh" \
  production "$IMAGE" "$VERSION" "$SOURCE_SHA"
