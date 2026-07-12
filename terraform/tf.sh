#!/usr/bin/env bash
# Load repo .env and run terraform with project_id from MONGODB_PROJECT_ID.
# Usage: ./tf.sh init | ./tf.sh apply | ./tf.sh output -raw mongodb_uri
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$(dirname "$0")"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

: "${MONGODB_PROJECT_ID:?Set MONGODB_PROJECT_ID in the repo .env (or the environment)}"
export TF_VAR_project_id="$MONGODB_PROJECT_ID"

exec terraform "$@"
