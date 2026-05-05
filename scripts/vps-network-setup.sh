#!/usr/bin/env bash
# Bootstrap shared Docker network on VPS so the new davinci stack can talk to
# the existing pg18 + redis7 containers (running from /opt/postgres compose).
# Idempotent: safe to re-run.
set -euo pipefail

NETWORK="${DAVINCI_NETWORK:-davinci_net}"

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "==> creating network $NETWORK"
  docker network create "$NETWORK"
else
  echo "==> network $NETWORK exists"
fi

for c in pg18 redis7; do
  if docker inspect "$c" >/dev/null 2>&1; then
    if docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}' | tr ' ' '\n' | grep -qx "$c"; then
      echo "==> $c already attached"
    else
      echo "==> attaching $c to $NETWORK"
      docker network connect "$NETWORK" "$c"
    fi
  else
    echo "WARN: container $c not running. Skip."
  fi
done

echo "==> resulting attachments:"
docker network inspect "$NETWORK" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}'
echo
