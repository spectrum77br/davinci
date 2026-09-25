#!/usr/bin/env bash
# Para o serviço. Nenhum perfil do AdsPower é alterado por parar.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.davinci.adspower-ip.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Serviço parado. Os IPs novos passam a ficar só no DaVinci até reinstalar."
