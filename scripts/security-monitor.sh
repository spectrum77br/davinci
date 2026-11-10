#!/usr/bin/env bash
# Monitor de atividade suspeita do servidor (não toca no DaVinci). Roda a cada 10 minutos (cron).
#
# Avisa quando:
#   1. alguém entra por SSH com um usuário ou uma chave nunca vistos antes (o IP muda sozinho
#      nas conexões de casa, então a identidade é o par usuário + chave);
#   2. muda algum authorized_keys (chave nova autorizada no servidor);
#   3. muda /etc/passwd, /etc/group ou /etc/sudoers (usuário ou permissão nova);
#   4. há rajada de tentativas: mais de 300 falhas de SSH ou mais de 20 bloqueios novos do
#      fail2ban em 10 minutos;
#   5. aparece uma porta pública nova além de 22, 80 e 443.
# Cada aviso vai para /var/log/davinci-security-alerts.log (uma linha por aviso) e, se configurado
# em /etc/davinci/security/monitor.env, para ALERT_CMD (recebe o texto na entrada).
# Uso: bash scripts/security-monitor.sh [--seed]   (--seed: aprende o normal dos últimos 30 dias)
set -euo pipefail

state=${MONITOR_STATE:-/var/lib/davinci-security}
alerts=${MONITOR_ALERTS:-/var/log/davinci-security-alerts.log}
conf=/etc/davinci/security/monitor.env
[ -r "$conf" ] && { set -a; . "$conf"; set +a; }
mkdir -p -m 700 "$state"
now=$(date -u +%FT%TZ)
since=$(cat "$state/last" 2>/dev/null || echo "-10min")

alert() {
  local line="$now ALERTA $*"
  echo "$line" >> "$alerts"
  if [ -n "${ALERT_CMD:-}" ]; then echo "$line" | bash -c "$ALERT_CMD" || true; fi
}

ssh_log() { journalctl -u ssh --since "$1" --no-pager -q 2>/dev/null || true; }

# 1) Identidades de SSH (usuário + impressão da chave).
identities() { { grep "Accepted publickey" || true; } | sed -nE 's/.*Accepted publickey for ([^ ]+) from .* (SHA256:[A-Za-z0-9+\/=]+).*/\1 \2/p' | sort -u; }
touch "$state/identities"
if [ "${1:-}" = "--seed" ]; then
  ssh_log "-30d" | identities | sort -u "$state/identities" - -o "$state/identities"
else
  ssh_log "$since" | identities | while read -r user key; do
    if ! grep -qxF "$user $key" "$state/identities"; then
      ip=$(ssh_log "$since" | { grep "Accepted publickey for $user " || true; } | { grep -F "$key" || true; } | { grep -oE "from [0-9a-f.:]+" || true; } | tail -1)
      alert "SSH com identidade nova: usuário $user, chave $key, $ip"
      echo "$user $key" >> "$state/identities"
    fi
  done
fi

# 2) e 3) Arquivos de acesso: compara a impressão com a da rodada anterior.
files=(/etc/passwd /etc/group /etc/sudoers /root/.ssh/authorized_keys)
for home in /home/*; do [ -f "$home/.ssh/authorized_keys" ] && files+=("$home/.ssh/authorized_keys"); done
for f in /etc/sudoers.d/*; do [ -f "$f" ] && files+=("$f"); done
{ sha256sum "${files[@]}" 2>/dev/null || true; } | sort -k2 > "$state/files.new"
if [ -s "$state/files" ] && [ "${1:-}" != "--seed" ]; then
  { diff "$state/files" "$state/files.new" || true; } | sed -nE 's/^> [0-9a-f]+  (.*)/\1/p' | while read -r changed; do
    alert "arquivo de acesso mudou: $changed"
  done
fi
mv "$state/files.new" "$state/files"

# 4) Rajadas de tentativas.
fails=$(ssh_log "$since" | grep -cE "Invalid user|authenticating user .* \[preauth\]|Failed password" || true)
[ "${1:-}" != "--seed" ] && [ "${fails:-0}" -gt 300 ] && alert "rajada de SSH: $fails tentativas falhas desde $since"
total=$(fail2ban-client status sshd 2>/dev/null | sed -nE 's/.*Total banned:[[:space:]]*([0-9]+).*/\1/p')
before=$(cat "$state/banned" 2>/dev/null || echo "${total:-0}")
[ "${1:-}" != "--seed" ] && [ $(( ${total:-0} - before )) -gt 20 ] && alert "fail2ban bloqueou $(( ${total:-0} - before )) IPs desde $since"
echo "${total:-0}" > "$state/banned"

# 5) Portas públicas.
ports=$(ss -tlnH | awk '{print $4}' | { grep -vE '^(127\.|\[::1\]|172\.|10\.)' || true; } | sed -E 's/.*:([0-9]+)$/\1/' | sort -un | tr '\n' ' ')
touch "$state/ports"
for p in $ports; do
  case " 22 80 443 " in *" $p "*) continue ;; esac
  if ! grep -qx "$p" "$state/ports"; then
    [ "${1:-}" != "--seed" ] && alert "porta pública nova aberta: $p"
    echo "$p" >> "$state/ports"
  fi
done

date -u '+%F %T UTC' > "$state/last"
