#!/bin/sh
# Instala (ou atualiza) o robô de mudanças neste Mac: copia os arquivos para
# ~/DaVinci/mudancas-robo e carrega o despertador no launchd.
# Os segredos (.env e .env.claude) NÃO vêm do repositório: ver LEIAME.md.
set -eu
AQUI=$(cd "$(dirname "$0")" && pwd)
RAIZ="${MUDANCAS_RAIZ:-$HOME/DaVinci/mudancas-robo}"
PLIST="$HOME/Library/LaunchAgents/com.davinci.mudancas-robo.plist"

mkdir -p "$RAIZ/registros" "$RAIZ/estado" "$RAIZ/claude"
chmod 700 "$RAIZ" "$RAIZ/claude"
cp "$AQUI/rodada.py" "$AQUI/REGRAS.md" "$AQUI/caixa.sb" "$AQUI/testar.sh" \
   "$AQUI/caixa_pytest.py" "$AQUI/conferir_web.cjs" "$RAIZ/"
chmod 755 "$RAIZ/testar.sh"
# permissões do Claude do robô (só arquivos; __RAIZ__ = esta instalação, se aparecer)
sed "s#__RAIZ__#$RAIZ#g" "$AQUI/permissoes.json" > "$RAIZ/permissoes.json"
# (os testes usam um Postgres DESCARTÁVEL criado dentro da caixa a cada
# conferência — o testar.sh cuida; não precisa de banco aqui)
[ -f "$RAIZ/.env" ] || { cp "$AQUI/.env.example" "$RAIZ/.env"; chmod 600 "$RAIZ/.env"; echo "Criei $RAIZ/.env a partir do exemplo — preencha o MUDANCAS_AGENT_TOKEN."; }

sed "s#__RAIZ__#$RAIZ#g" "$AQUI/com.davinci.mudancas-robo.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
if [ "${1:-}" = "--sem-carregar" ]; then
  echo "Arquivos copiados; despertador NÃO carregado (--sem-carregar)."
  exit 0
fi
launchctl load "$PLIST"
echo "Robô de mudanças instalado em $RAIZ e despertador carregado (a cada 1 min)."
