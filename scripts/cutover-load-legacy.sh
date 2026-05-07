#!/usr/bin/env bash
set -euo pipefail

# Phase-14 cutover step 1: load the legacy `stocksync` Postgres dump from the
# old (Manus AI) host into the new VPS Postgres as a side schema named
# `stocksync_legacy`. Run this from the Mac (it ssh's to davinci).
#
# Usage:
#   ./scripts/cutover-load-legacy.sh path/to/legacy-stocksync.sql.gz
#
# The dump may be plain SQL or a custom-format pg_dump. If it was taken with
# `pg_dump -Fp -n stocksync ...`, the schema name inside is already
# `stocksync` — we rename it to `stocksync_legacy` after restore so the live
# `davinci` schema is never touched.

DUMP="${1:?usage: $0 <dump-file>}"
HOST="${DAVINCI_SSH_HOST:-davinci}"
REMOTE_PATH="${DAVINCI_REMOTE_PATH:-/opt/davinci}"
DB="${DAVINCI_DB:-davinci}"
USER="${DAVINCI_DB_USER:-davinci}"

if [[ ! -f "$DUMP" ]]; then
  echo "dump not found: $DUMP" >&2
  exit 1
fi

REMOTE_TMP="/tmp/$(basename "$DUMP")"

echo "==> uploading dump to $HOST:$REMOTE_TMP"
scp "$DUMP" "$HOST:$REMOTE_TMP"

echo "==> restoring + renaming schema to stocksync_legacy on $HOST"
ssh "$HOST" bash -se <<EOF
set -euo pipefail
cd "$REMOTE_PATH"

PSQL="docker compose exec -T postgres psql -U $USER -d $DB"

# Make sure no stale legacy schema is in the way.
\$PSQL -c 'DROP SCHEMA IF EXISTS stocksync_legacy CASCADE'
\$PSQL -c 'DROP SCHEMA IF EXISTS stocksync CASCADE'

case "$REMOTE_TMP" in
  *.sql.gz) gunzip -c "$REMOTE_TMP" | \$PSQL ;;
  *.sql)    \$PSQL -f "$REMOTE_TMP" ;;
  *.dump|*.custom)
    docker compose cp "$REMOTE_TMP" postgres:/tmp/legacy.dump
    docker compose exec -T postgres pg_restore -U $USER -d $DB -n stocksync /tmp/legacy.dump
    docker compose exec -T postgres rm -f /tmp/legacy.dump
    ;;
  *) echo "unknown dump format: $REMOTE_TMP" >&2; exit 1 ;;
esac

\$PSQL -c 'ALTER SCHEMA stocksync RENAME TO stocksync_legacy'
\$PSQL -c "SELECT 'loaded ' || count(*) || ' tables' FROM information_schema.tables WHERE table_schema = 'stocksync_legacy'"

rm -f "$REMOTE_TMP"
EOF

echo "==> legacy schema available at stocksync_legacy"
