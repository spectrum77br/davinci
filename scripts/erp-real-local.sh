#!/bin/sh
set -eu
erp_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec sh "$erp_repo_root/scripts/erp-local.sh" \
  --database "$erp_repo_root/data/erp-local/real-workspace.sqlite3" \
  --port 8767 --reference-ssh "${DAVINCI_REFERENCE_SSH:-davinci-prod}" \
  --bootstrap-reference \
  --initial-bling-read "$erp_repo_root/data/erp-local/bling-initial-read.json" "$@"
