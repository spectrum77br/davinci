#!/bin/sh
# As conferências do robô de mudanças — só DEPOIS do Aprovar (2ª revisão de
# 17/11/2026), numa cópia nova do commit aprovado — sempre DENTRO da caixa
# (caixa.sb): sem rede, sem chaves, sem tokens, escrevendo só na pasta do pedido.
#
#   testar.sh pytest <arquivos/args>   → pytest da API num Postgres DESCARTÁVEL
#                                        criado dentro da caixa (3ª revisão)
#   testar.sh ruff <args>              → ruff check da API
#   testar.sh node <apps/web/tests/x.cjs>
#   testar.sh web <arquivos da tela>   → compila os .vue/.ts/.js mexidos (conferir_web.cjs)
#   testar.sh importar                 → a API carrega (import app.main)
#
# Roda na pasta do pedido (a worktree ~/davinci-mudancas/M-N).
set -eu
RAIZ=$(cd "$(dirname "$0")" && pwd -P)
_cfg() { grep -E "^$1=" "$RAIZ/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed "s#^~#$HOME#"; }
PASTA=$(_cfg PASTA_MUDANCAS); PASTA=${PASTA:-$HOME/davinci-mudancas}
NODEP=$(_cfg NODE_PATH_WEB); NODEP=${NODEP:-$HOME/davinci/apps/web/node_modules}
PGBIN=$(_cfg PG_BIN); PGBIN=${PGBIN:-/opt/homebrew/opt/postgresql@18/bin}

PASTAR=$(cd "$PASTA" && pwd -P)
WT=$(pwd -P)
case "$WT" in
  "$PASTAR"/M-[0-9]*) ;;
  *) echo "testar.sh: só roda dentro da pasta de um pedido ($PASTA/M-N)"; exit 2 ;;
esac
TMPC="$PASTAR/.tmp-caixa/$(basename "$WT")"
# pasta temporária NOVA a cada conferência (o Postgres descartável mora nela)
rm -rf "$TMPC"
mkdir -p "$TMPC/uv"
cp "$RAIZ/caixa_pytest.py" "$RAIZ/conferir_web.cjs" "$TMPC/"
# node_modules da tela: a pasta e o depósito do pnpm para onde os links apontam
NODEM=$(cd "$NODEP" 2>/dev/null && pwd -P || echo /nao-existe)
PNPM=$(cd "$NODEP/vue" 2>/dev/null && pwd -P | sed 's#/\.pnpm/.*#/.pnpm#' || echo /nao-existe)
case "$PNPM" in */.pnpm) ;; *) PNPM=/nao-existe ;; esac
PG="$TMPC/pg"
DB="postgresql+asyncpg://robo@/davinci_robo?host=$PG"

[ $# -ge 1 ] || { echo "uso: testar.sh pytest|ruff|node|web|importar ..."; exit 2; }
QUE=$1; shift
case "$QUE" in
  pytest)
    # Postgres descartável DENTRO da caixa: sobe, roda o pytest, desliga.
    cat > "$TMPC/pytest.sh" <<EOF
LC_ALL=C "$PGBIN/initdb" -D "$PG" -U robo -A trust -E UTF8 --no-locale >/dev/null || exit 3
LC_ALL=C "$PGBIN/pg_ctl" -D "$PG" -l "$TMPC/pg.log" -w \\
  -o "-k $PG -c listen_addresses= -c fsync=off -c full_page_writes=off -c synchronous_commit=off" \\
  start >/dev/null || { tail -5 "$TMPC/pg.log"; exit 3; }
LC_ALL=C "$PGBIN/psql" -h "$PG" -U robo -d postgres -qAtc "create database davinci_robo" >/dev/null || exit 3
/opt/homebrew/bin/uv run --directory apps/api --no-sync pytest -q -p no:cacheprovider -p caixa_pytest "\$@"
rc=\$?
LC_ALL=C "$PGBIN/pg_ctl" -D "$PG" -m fast -w stop >/dev/null 2>&1
exit \$rc
EOF
    set -- /bin/sh "$TMPC/pytest.sh" "$@" ;;
  ruff)     set -- /opt/homebrew/bin/uv run --directory apps/api --no-sync ruff check "$@" ;;
  node)     set -- /opt/homebrew/bin/node "$@" ;;
  web)      set -- /opt/homebrew/bin/node "$TMPC/conferir_web.cjs" "$@" ;;
  importar) set -- /opt/homebrew/bin/uv run --directory apps/api --no-sync python -c "import app.main; print('a API carrega: ok')" ;;
  *) echo "testar.sh: só pytest, ruff, node, web ou importar"; exit 2 ;;
esac

exec env -i \
  HOME="$HOME" PATH=/opt/homebrew/bin:/usr/bin:/bin LANG=pt_BR.UTF-8 \
  TMPDIR="$TMPC" PYTHONPATH="$TMPC" \
  DATABASE_URL="$DB" DAVINCI_TEST_SCHEMA=davinci_test_robo DAVINCI_CAIXA=1 NODE_PATH="$NODEP" \
  UV_CACHE_DIR="$TMPC/uv" UV_OFFLINE=1 UV_NO_SYNC=1 UV_NO_CONFIG=1 \
  /usr/bin/sandbox-exec -f "$RAIZ/caixa.sb" \
    -D CASA="$HOME" -D PASTA="$PASTAR" -D RAIZ="$RAIZ" -D WT="$WT" -D TMP="$TMPC" \
    -D NODEM="$NODEM" -D PNPM="$PNPM" \
    "$@"
