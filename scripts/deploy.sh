#!/usr/bin/env bash
set -euo pipefail

# Deploy DaVinci to Hetzner VPS from Mac.
# Requires: ~/.ssh/config with host alias `davinci` (IdentityFile id_ed25519)
# VPS path: /opt/davinci (git clone of this repo)

HOST="${DAVINCI_SSH_HOST:-davinci}"
REMOTE_PATH="${DAVINCI_REMOTE_PATH:-/opt/davinci}"
BRANCH="${DAVINCI_BRANCH:-main}"

echo "==> push branch $BRANCH"
git push origin "$BRANCH"

echo "==> pull + build + up on $HOST:$REMOTE_PATH"
ssh "$HOST" bash -se <<EOF
set -euo pipefail
cd "$REMOTE_PATH"
git fetch origin
git checkout $BRANCH
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
EOF

echo "==> deploy done"
