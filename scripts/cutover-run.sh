#!/usr/bin/env bash
set -euo pipefail

# Phase-14 cutover step 2: run the data migration + validation against the
# side-loaded legacy schema. Assumes ./scripts/cutover-load-legacy.sh ran
# first.
#
# Usage:
#   ./scripts/cutover-run.sh             # migrate + validate (clears creds)
#   ./scripts/cutover-run.sh --reset     # TRUNCATE davinci tables first
#   ./scripts/cutover-run.sh --validate  # only run count validation

HOST="${DAVINCI_SSH_HOST:-davinci}"
REMOTE_PATH="${DAVINCI_REMOTE_PATH:-/opt/davinci}"
MODE="migrate"
RESET=""

while (( "$#" )); do
  case "$1" in
    --reset) RESET="--reset"; shift ;;
    --validate) MODE="validate"; shift ;;
    --keep-credentials) KEEP="--keep-credentials"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
KEEP="${KEEP:-}"

ssh "$HOST" bash -se <<EOF
set -euo pipefail
cd "$REMOTE_PATH"

DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# DATABASE_URL is already set inside the api container; just point the script
# at the legacy schema in the same DB.
LEGACY_URL="\${LEGACY_URL:-\$(\$DC exec -T api printenv DATABASE_URL)}"

if [[ "$MODE" == "validate" ]]; then
  \$DC exec -T -e LEGACY_DATABASE_URL="\$LEGACY_URL" api \\
    python -m app.cutover.cli validate
else
  \$DC exec -T -e LEGACY_DATABASE_URL="\$LEGACY_URL" api \\
    python -m app.cutover.cli migrate $RESET $KEEP
  \$DC exec -T -e LEGACY_DATABASE_URL="\$LEGACY_URL" api \\
    python -m app.cutover.cli validate
fi
EOF
