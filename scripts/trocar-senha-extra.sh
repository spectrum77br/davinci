#!/usr/bin/env bash
# Troca a senha extra do Valuation e da tela Empresas (as duas usam a mesma).
#
# Roda neste Mac. A senha é digitada escondida e vai direto para o servidor pela
# entrada padrão do ssh: não aparece na tela, não fica no histórico do terminal,
# não vira argumento de comando (que qualquer processo veria) e não é gravada
# em arquivo nenhum além do .env do servidor.
#
# Por que existe: até 25/09/2026 a senha ficava escrita no código, no GitHub.
# Qualquer pessoa com acesso ao repositório a conhecia.
#
#   ./scripts/trocar-senha-extra.sh
#
# Depois de trocar, o sistema reinicia (~30 s fora do ar) e todo mundo que
# estava com a página aberta precisa digitar a senha nova.
set -euo pipefail

HOST="${DAVINCI_SSH:-root@46.225.188.20}"
CHAVE="${DAVINCI_SSH_KEY:-$HOME/.ssh/coolify}"

read -r -s -p "Senha nova: " A; echo
read -r -s -p "Repita a senha nova: " B; echo

if [ "$A" != "$B" ]; then
  unset A B; echo "As duas não batem. Nada foi trocado."; exit 1
fi
if [ "${#A}" -lt 10 ]; then
  unset A B; echo "Use pelo menos 10 caracteres. Nada foi trocado."; exit 1
fi
# Só caracteres que o arquivo de configuração lê sem surpresa (sem aspas,
# espaço, #, $ nem barra invertida).
if ! printf '%s' "$A" | LC_ALL=C grep -qE '^[A-Za-z0-9!@%&*_.+=:,-]+$'; then
  unset A B
  echo "Use só letras, números e estes símbolos: ! @ % & * _ . + = : , -"
  echo "Nada foi trocado."; exit 1
fi

printf '%s' "$A" | ssh -i "$CHAVE" -o StrictHostKeyChecking=no "$HOST" '
set -e
cd /opt/davinci
cp .env ".env.bak.senha-$(date +%Y%m%d-%H%M%S)"
python3 -c "
import re, sys
nova = sys.stdin.read()
p = \"/opt/davinci/.env\"
s = open(p).read()
linha = \"VALUATION_PASSWORD=\" + nova
if re.search(r\"^VALUATION_PASSWORD=.*$\", s, re.M):
    s = re.sub(r\"^VALUATION_PASSWORD=.*$\", lambda m: linha, s, count=1, flags=re.M)
else:
    s = s.rstrip(chr(10)) + chr(10) + linha + chr(10)
open(p, \"w\").write(s)
"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d >/dev/null 2>&1
echo "senha trocada no servidor; sistema reiniciando"
'
unset A B
echo "Pronto. Em uns 30 segundos o Valuation e a tela Empresas pedem a senha nova."
