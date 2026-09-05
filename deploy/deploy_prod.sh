#!/usr/bin/env bash

set -euo pipefail

RELEASE_FILE="${1:?usage: deploy_prod.sh <dev-release.json>}"
IMAGE="$(jq -er '.image' "$RELEASE_FILE")"
VERSION="$(jq -er '.version' "$RELEASE_FILE")"
SOURCE_SHA="$(jq -er '.source_sha' "$RELEASE_FILE")"

SCRIPT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIRECTORY/deploy_website.sh" \
  production "$IMAGE" "$VERSION" "$SOURCE_SHA"
