#!/bin/sh
# Guarda o login do Claude Code do robô SEM mostrar na tela.
# 1) Num Terminal, rode o setup-token do Claude Code (abre o navegador para você
#    entrar na sua conta) e copie o token que ele mostrar no fim.
# 2) Rode este script e cole o token quando ele pedir (não aparece enquanto cola).
set -eu
RAIZ="${MUDANCAS_RAIZ:-$HOME/DaVinci/mudancas-robo}"
mkdir -p "$RAIZ"
printf "Cole o token do Claude Code e aperte Enter: "
stty -echo
IFS= read -r TOKEN
stty echo
echo
case "$TOKEN" in
  "") echo "Nada colado — nada mudou."; exit 1 ;;
esac
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$TOKEN" > "$RAIZ/.env.claude"
chmod 600 "$RAIZ/.env.claude"
echo "Token guardado em $RAIZ/.env.claude (só o seu usuário lê)."
