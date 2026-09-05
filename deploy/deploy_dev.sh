#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIRECTORY/deploy_website.sh" dev "$@"
