#!/usr/bin/env bash
# Instala (ou reinstala) o serviço que aplica no AdsPower o IP novo das empresas.
# Roda neste Mac, a cada 60 segundos, pelo launchd. Pode rodar de novo à vontade.
set -euo pipefail

ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$HOME/DaVinci/executor-adspower-ip"
TOKEN="$HOME/.davinci/adspower_agent_token"
ROTULO="com.davinci.adspower-ip"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"

if [ ! -s "$TOKEN" ]; then
  echo "Falta o arquivo de token em $TOKEN. Sem ele o DaVinci recusa o serviço." >&2
  exit 1
fi
chmod 600 "$TOKEN"

mkdir -p "$DESTINO/logs"
cp "$ORIGEM/adspower_ip_sync.py" "$DESTINO/adspower_ip_sync.py"

# LaunchAgent (sessão do usuário): é onde o AdsPower roda e responde.
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$ROTULO</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$DESTINO/adspower_ip_sync.py</string>
        <string>--loop</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DESTINO</string>
    <!-- Fica sempre de pé e o próprio programa espera 60 s entre as passadas.
         StartInterval não disparava de forma confiável neste macOS. -->
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$DESTINO/logs/servico.log</string>
    <key>StandardErrorPath</key>
    <string>$DESTINO/logs/servico.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
launchctl kickstart "gui/$(id -u)/$ROTULO" 2>/dev/null || true
echo "Instalado. Log em $DESTINO/logs/servico.log"
