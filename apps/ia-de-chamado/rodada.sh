#!/bin/sh
# Rodada da IA de Chamado (Claude Code) — o despertador do Mac Santiago chama a
# cada 10 minutos (com.davinci.ia-de-chamado.plist).
#
# 1. Pergunta ao DaVinci se há trabalho (sem IA, segundos, não gasta o plano).
# 2. Nada → sai. Tem → chama o Claude com o manual e os casos:
#      forte   (instrução de pessoa, fala presa, tela) → Opus 5.5, esforço alto
#      simples (a plataforma respondeu)                → Sonnet 5.5, esforço médio
# 3. Uma rodada por vez (trava); no fim apaga tmp/. Perfil do AdsPower quem fecha
#    é a IA (fica aberto de propósito quando para em captcha/login pra uma pessoa).
#
# Vinicius, 29/09/2026: "não quero mais usar o hermes, quero usar o claude code".

AQUI="$HOME/DaVinci/ia-de-chamado"
cd "$AQUI" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export DAVINCI_CEREBRO_DIR="$AQUI"          # .env (acesso ao DaVinci) e estado/
export CLAUDE_CONFIG_DIR="$AQUI/claude"     # login e conversas do Claude ficam aqui
[ -f "$AQUI/.env.claude" ] && . "$AQUI/.env.claude"   # CLAUDE_CODE_OAUTH_TOKEN (vale 1 ano)
export CLAUDE_CODE_OAUTH_TOKEN

mkdir -p registros tmp estado
LOG="registros/$(date +%Y-%m-%d).log"
TRAVA="estado/rodada.trava"
agora() { date '+%H:%M:%S'; }

if [ -f "$TRAVA" ] && kill -0 "$(cat "$TRAVA")" 2>/dev/null; then
  exit 0  # a rodada anterior ainda está trabalhando (ex.: tarefa de tela)
fi
echo $$ > "$TRAVA"
trap 'rm -f "$TRAVA"' EXIT INT TERM
date '+%Y-%m-%d %H:%M:%S' > estado/ultima-rodada   # prova de vida do despertador

PEDIDO="Rodada da IA de Chamado. Abaixo: o MANUAL do Vinicius, as correções e confirmações \
dele e os CASOS que esperam por você. Decida CADA caso seguindo o CLAUDE.md (instrução de \
pessoa > manual > prudência) e registre cada decisão com python3 ferramentas/davinci_chamados.py \
decidir --json. Comandos UM POR VEZ, no formato exato do CLAUDE.md (sem cd, &&, |, ; ou >). \
Se vier a seção ABRIR NO ML, siga a seção dela no CLAUDE.md (não usa decidir). \
Na dúvida, humano. No fim, feche os perfis que abriu e escreva só a lista curta: \
pedido → ação → por quê."

for tipo in forte simples; do
  casos=$(/usr/bin/python3 ferramentas/davinci_chamados.py precheck --tipo "$tipo" 2>>"$LOG")
  if [ $? -ne 0 ]; then echo "$(agora) [$tipo] checagem falhou" >> "$LOG"; continue; fi
  case "$casos" in ""|*'"wakeAgent": false'*) continue ;; esac

  if [ "$tipo" = forte ]; then MODELO=claude-opus-5-5; ESFORCO=high
  else MODELO=claude-sonnet-5-5; ESFORCO=medium; fi
  # Evidência pedida na tela (Upload Evidence) não é caso simples: escolher as
  # fotos e mexer no Seller Center é trabalho do Opus (290730, 29/09).
  case "$casos" in
    *"Upload Evidence"*|*"vidência até"*|*"vidências até"*) MODELO=claude-opus-5-5; ESFORCO=high ;;
  esac
  echo "$(agora) [$tipo] começou ($MODELO, esforço $ESFORCO)" >> "$LOG"
  printf '%s\n' "$casos" | claude -p "$PEDIDO" \
    --model "$MODELO" --effort "$ESFORCO" \
    --permission-mode dontAsk --permission-prompts none \
    --output-format text >> "$LOG" 2>&1
  echo "$(agora) [$tipo] terminou (saída $?)" >> "$LOG"
done

# Faxina: nada baixado fica no Mac (regra do Vinicius).
rm -rf "$AQUI/tmp" && mkdir -p "$AQUI/tmp"
