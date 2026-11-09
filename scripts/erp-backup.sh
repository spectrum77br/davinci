#!/bin/sh
# Cópia verificada do ERP próprio. Uso no servidor, por agendamento:
#   sh scripts/erp-backup.sh /var/lib/davinci-erp/erp.sqlite3 /var/lib/davinci-erp/backups
# Faz a cópia, restaura em arquivo novo para provar que abre e guarda os 30 dias mais recentes.
set -eu
source_db=$1
target_dir=$2
stamp=$(date -u +%Y%m%dT%H%M%SZ)
folder="$target_dir/$stamp"
mkdir -p "$folder"
python -m app.erp_local.release backup "$source_db" "$folder/erp.sqlite3" > "$folder/backup.json"
python -m app.erp_local.release restore "$folder/erp.sqlite3" "$folder/restored.sqlite3" > "$folder/restore.json"
rm -f "$folder/restored.sqlite3"
find "$target_dir" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
echo "$folder"
