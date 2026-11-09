#!/bin/sh
set -eu
erp_repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$erp_repo_root"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$erp_repo_root/apps/api"
apps/api/.venv/bin/python -m pytest apps/api/erp_local_tests \
  --confcutdir=apps/api/erp_local_tests \
  -c apps/api/erp_local_tests/pytest.ini -p no:cacheprovider "$@"
exec node --test apps/api/erp_local_tests/ui.cjs
