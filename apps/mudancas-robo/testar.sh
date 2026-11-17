#!/bin/sh
# O ÚNICO jeito de o Claude do robô (e o próprio robô) rodar código da mudança
# ainda não aprovada: sempre DENTRO da caixa (caixa.sb) — sem rede, sem chaves,
# sem tokens, escrevendo só na pasta do pedido. Revisão de 17/11/2026.
#
#   testar.sh pytest <arquivos/args>   → pytest da API (banco de teste local, pelo socket)
#   testar.sh ruff <arquivos .py>      → ruff check da API
#   testar.sh node <apps/web/tests/x.cjs>
#   testar.sh importar                 → a API carrega (import app.main)
#
# Roda na pasta do pedido (a worktree ~/davinci-mudancas/M-N).
set -eu
RAIZ=$(cd "$(dirname "$0")" && pwd)
_cfg() { grep -E "^$1=" "$RAIZ/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed "s#^~#$HOME#"; }
PASTA=$(_cfg PASTA_MUDANCAS); PASTA=${PASTA:-$HOME/davinci-mudancas}
DB=$(_cfg DATABASE_URL_TESTES); DB=${DB:-postgresql+asyncpg://admmarketing@/davinci_robo?host=/tmp}
NODEP=$(_cfg NODE_PATH_WEB); NODEP=${NODEP:-$HOME/davinci/apps/web/node_modules}

WT=$(pwd -P)
case "$WT" in
  "$(cd "$PASTA" && pwd -P)"/M-[0-9]*) ;;
  *) echo "testar.sh: só roda dentro da pasta de um pedido ($PASTA/M-N)"; exit 2 ;;
esac
TMPC="$(cd "$PASTA" && pwd -P)/.tmp-caixa/$(basename "$WT")"
mkdir -p "$TMPC"

[ $# -ge 1 ] || { echo "uso: testar.sh pytest|ruff|node|importar ..."; exit 2; }
QUE=$1; shift
case "$QUE" in
  pytest)   set -- /opt/homebrew/bin/uv run --directory apps/api --no-sync pytest -q -p no:cacheprovider "$@" ;;
  ruff)     set -- /opt/homebrew/bin/uv run --directory apps/api --no-sync ruff check "$@" ;;
  node)     set -- /opt/homebrew/bin/node "$@" ;;
  importar) set -- /opt/homebrew/bin/uv run --directory apps/api --no-sync python -c "import app.main; print('a API carrega: ok')" ;;
  *) echo "testar.sh: só pytest, ruff, node ou importar"; exit 2 ;;
esac

exec env -i \
  HOME="$HOME" PATH=/opt/homebrew/bin:/usr/bin:/bin LANG=pt_BR.UTF-8 \
  TMPDIR="$TMPC" \
  DATABASE_URL="$DB" DAVINCI_TEST_SCHEMA=davinci_test_robo NODE_PATH="$NODEP" \
  UV_CACHE_DIR="$HOME/.cache/uv" UV_OFFLINE=1 \
  /usr/bin/sandbox-exec -f "$RAIZ/caixa.sb" \
    -D CASA="$HOME" -D RAIZ="$RAIZ" -D WT="$WT" \
    -D UVCACHE="$HOME/.cache/uv" -D TMP="$TMPC" \
    "$@"
