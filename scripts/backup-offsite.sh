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

log "início $stamp"

# 1) DaVinci, em fluxo: pg_dump -> restic (sem arquivo intermediário no disco).
docker exec pg18 pg_dump -U pguser -d davinci --no-owner --no-privileges \
  --exclude-schema=uranyx \
  --exclude-table-data='davinci.sync_logs*' \
  --exclude-table-data='davinci.background_job*' \
  | restic backup --stdin --stdin-filename davinci.sql --tag davinci --host "$host" --quiet
log "davinci ok"

# 2) ERP: cópia consistente pequena (centenas de MB) na própria pasta do ERP, depois apagada.
# Pasta fixa, para o restic ver sempre o mesmo caminho e deduplicar entre os dias.
tmp=/var/lib/davinci-erp/.copia-backup
rm -rf "$tmp"; mkdir -m 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT
docker exec -w /app davinci-erp_worker-1 uv run python -m app.erp_local.release backup \
  /var/lib/davinci-erp/erp.sqlite3 "$tmp/erp.sqlite3" >/dev/null
restic backup "$tmp" --tag erp --host "$host" --quiet
log "erp ok"

# 3) Guarda de cerca de 30 dias.
restic forget --host "$host" --keep-daily 7 --keep-weekly 4 --prune --quiet
log "guarda ok"

# 4) Prova: estrutura do repositório e leitura de uma amostra dos dados.
restic check --read-data-subset=5% --quiet
log "conferência ok"

restic snapshots --host "$host" --latest 1 --compact
log "fim"
