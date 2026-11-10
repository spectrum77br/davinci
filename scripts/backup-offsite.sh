#!/usr/bin/env bash
# Cópia de segurança cifrada e incremental do DaVinci e do ERP, direto para fora do servidor.
#
# Uso no servidor (agendado uma vez por dia, de madrugada):
#   bash /opt/davinci/scripts/backup-offsite.sh
#
# Configuração em /etc/davinci/backup/backup.env (root, 600), nunca no repositório:
#   RESTIC_REPOSITORY=...        destino fora do servidor (Storage Box em outro local, ou rclone:mega:...)
#   RESTIC_PASSWORD_FILE=/etc/davinci/backup/restic.pass   senha do repositório (guardada também pelo Eduardo)
#   (e as credenciais do destino, se o destino pedir)
#
# O que entra:
#   - banco "davinci" do pg18, em texto (deduplica bem), SEM o esquema "uranyx" (a política do app
#     Uranyx diz que os dados dele não têm cópia) e sem os DADOS dos registros regeneráveis
#     (sync_logs, background_jobs): a estrutura dessas tabelas vai junto;
#   - banco do ERP (SQLite), em cópia consistente.
# Nada grande fica no disco do servidor: o banco do DaVinci vai em fluxo direto para o restic.
# Cifragem: AES-256 do próprio restic. Guarda: 7 diárias + 4 semanais (cerca de 30 dias), para
# respeitar o prazo de 30 dias dos dados de comprador da Amazon.
# Prova de restauração: restic check lendo uma amostra dos dados a cada execução.
set -euo pipefail  # um pg_dump que falhe no meio derruba a cópia, nunca grava pela metade

conf=${BACKUP_CONF:-/etc/davinci/backup/backup.env}
[ -r "$conf" ] || { echo "sem configuração em $conf" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
. "$conf"
set +a
command -v restic >/dev/null || { echo "restic não instalado" >&2; exit 2; }

host=davinci-prod
stamp=$(date -u +%Y%m%dT%H%M%SZ)
log() { echo "$(date -u +%FT%TZ) $*"; }

# Destino. BACKUP_VIA=megacmd: usa a sessão do MEGA que o DaVinci já tem (sidecar MEGAcmd),
# servindo só a pasta das cópias por WebDAV em 127.0.0.1 DENTRO do sidecar; o restic roda na rede
# do sidecar (nsenter), então nada fica aberto para a rede do Docker nem para fora.
rs() { restic "$@"; }
if [ "${BACKUP_VIA:-}" = megacmd ]; then
  mega=${MEGA_CONTAINER:-davinci-megacmd-1}
  folder=${MEGA_FOLDER:-/davinci-backups}
  served() { docker exec "$mega" mega-webdav 2>/dev/null | grep -o "http://127.0.0.1:[0-9]*/[^ ]*${folder#/}" | head -1; }
  url=$(served || true)
  if [ -z "$url" ]; then
    docker exec "$mega" mega-webdav "$folder" --port="${MEGA_WEBDAV_PORT:-4443}" >/dev/null
    url=$(served)
  fi
  [ -n "$url" ] || { echo "pasta das cópias não servida pelo MEGAcmd" >&2; exit 3; }
  export RCLONE_CONFIG_MEGADAV_TYPE=webdav RCLONE_CONFIG_MEGADAV_URL="$url"
  export RCLONE_CONFIG_MEGADAV_VENDOR=other RESTIC_REPOSITORY=rclone:megadav:restic
  pid=$(docker inspect -f '{{.State.Pid}}' "$mega")
  rs() { nsenter -t "$pid" -n -- restic "$@"; }
fi

log "início $stamp"

# 1) DaVinci, em fluxo: pg_dump -> restic (sem arquivo intermediário no disco).
# ONLY=erp pula o DaVinci (teste de dia, sem pesar no banco).
if [ "${ONLY:-}" != erp ]; then
docker exec pg18 pg_dump -U pguser -d davinci --no-owner --no-privileges \
  --exclude-schema=uranyx \
  --exclude-table-data='davinci.sync_logs*' \
  --exclude-table-data='davinci.background_job*' \
  | rs backup --stdin --stdin-filename davinci.sql --tag davinci --host "$host" --quiet
log "davinci ok"
fi

# 2) ERP: cópia consistente pequena (centenas de MB) na própria pasta do ERP, depois apagada.
# Pasta fixa, para o restic ver sempre o mesmo caminho e deduplicar entre os dias.
tmp=/var/lib/davinci-erp/.copia-backup
rm -rf "$tmp"; mkdir -m 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT
docker exec -w /app davinci-erp_worker-1 uv run python -m app.erp_local.release backup \
  /var/lib/davinci-erp/erp.sqlite3 "$tmp/erp.sqlite3" >/dev/null
rs backup "$tmp" --tag erp --host "$host" --quiet
log "erp ok"

# 3) Guarda de cerca de 30 dias.
rs forget --host "$host" --keep-daily 7 --keep-weekly 4 --prune --quiet
log "guarda ok"

# 4) Prova: estrutura do repositório e leitura de uma amostra dos dados.
rs check --read-data-subset=5% --quiet
log "conferência ok"

rs snapshots --host "$host" --latest 1 --compact
log "fim"
