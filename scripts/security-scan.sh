#!/usr/bin/env bash
# Verificação de dependências com falhas conhecidas (Python da api/ERP e Node do site).
# Uso: bash scripts/security-scan.sh   (no Mac mini, toda segunda-feira; resultado em
# docs/seguranca/varreduras/AAAA-MM-DD.txt). Prazo de correção: crítica 7 dias, alta 30 dias.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
out="$root/docs/seguranca/varreduras/$(date +%F).txt"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$(dirname "$out")"
{
  echo "Varredura de dependências — $(date '+%F %T')"
  echo
  echo "== Python (apps/api: api, workers e ERP) =="
  (cd "$root/apps/api" && uv export --frozen --no-dev --no-hashes --no-emit-project) > "$work/req.txt"
  (cd "$work" && uvx --quiet pip-audit -r req.txt --progress-spinner off) || true
  echo
  echo "== Node (site, dependências de produção) =="
  (cd "$root" && pnpm audit --prod 2>&1 | grep -E "vulnerabilities found|Severity:") || true
} > "$out" 2>&1
echo "$out"
