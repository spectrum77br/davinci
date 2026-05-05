#!/usr/bin/env bash
set -euo pipefail

# SSH tunnel: forwards VPS Postgres (5432) -> localhost:15432
#                       VPS Redis    (6379) -> localhost:16379
# Run in dedicated terminal. Ctrl-C to close.
# Mac dev (.env) should use:
#   DATABASE_URL=postgresql+asyncpg://davinci:<pwd>@localhost:15432/davinci
#   REDIS_URL=redis://localhost:16379/0
#   ARQ_REDIS_URL=redis://localhost:16379/1

HOST="${DAVINCI_TUNNEL_HOST:-davinci-tunnel}"

echo "==> opening tunnel via $HOST (Ctrl-C to close)"
echo "    postgres: localhost:15432"
echo "    redis:    localhost:16379"
exec ssh -N "$HOST"
