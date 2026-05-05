#!/usr/bin/env bash
set -euo pipefail

# Pull latest pg_dump from VPS to Mac.
HOST="${DAVINCI_SSH_HOST:-davinci}"
REMOTE_BACKUP_DIR="${DAVINCI_REMOTE_BACKUP_DIR:-/opt/backups}"
LOCAL_DEST="${1:-./data/backups}"

mkdir -p "$LOCAL_DEST"

LATEST=$(ssh "$HOST" "ls -1t $REMOTE_BACKUP_DIR/davinci-*.sql.gz 2>/dev/null | head -n1")
if [[ -z "$LATEST" ]]; then
  echo "no backups found in $REMOTE_BACKUP_DIR"
  exit 1
fi

echo "==> pulling $LATEST -> $LOCAL_DEST/"
scp "$HOST:$LATEST" "$LOCAL_DEST/"
echo "==> done"
