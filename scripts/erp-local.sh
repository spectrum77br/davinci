#!/bin/sh
set -eu
erp_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$erp_repo_root/apps/api"
exec .venv/bin/python -m app.erp_local "$@"
