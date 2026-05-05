#!/usr/bin/env bash
set -euo pipefail

HOST="${DAVINCI_SSH_HOST:-davinci}"
REMOTE_PATH="${DAVINCI_REMOTE_PATH:-/opt/davinci}"

ssh "$HOST" bash -se <<EOF
set -euo pipefail
cd "$REMOTE_PATH"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api alembic upgrade head
EOF
