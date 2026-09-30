"""Robô de LEITURA do chat da Temu e do AliExpress (Mac mini, perfis do AdsPower).

Por que existe: nem a Temu nem o AliExpress têm API de chat (docs/atendimento-
aliexpress-temu.md). O chat só existe no Seller Center aberto num navegador
LOGADO — e os navegadores logados das lojas moram nos perfis do AdsPower deste
Mac. Então o robô mantém uma aba do chat aberta em cada perfil e COPIA para o
DaVinci o que a própria página já recebe. Quem interpreta é o DaVinci
(POST /api/atendimento/robo/eventos, parser por plataforma, testável em
Python); a resposta, por enquanto, a pessoa dá no Seller Center — o DaVinci
mostra a mensagem e a sugestão da IA. Enviar pelo robô fica para depois.

O QUE ELE FAZ, para cada loja de `atendimento_robo_lojas.json` (uma thread
por loja, todas no mesmo processo):
  1. garante o perfil aberto no AdsPower pela API local (abre se estiver
     fechado; com --sem-abrir-perfil, não abre e avisa);
  2. conecta por Selenium no navegador do perfil (debuggerAddress) e usa UMA
     aba PRÓPRIA — o handle fica guardado em disco para o robô reiniciado
     reaproveitar a mesma aba em vez de empilhar abas; as abas da equipe não
     são tocadas;
  3. registra o gancho com `Page.addScriptToEvaluateOnNewDocument` e navega
     NA MESMA sessão: o registro vale só enquanto a sessão CDP que o fez
     estiver viva (se o processo sai, o gancho some) — por isso a sessão
     Selenium de cada perfil fica aberta o tempo todo e, se cair, o robô
     reconecta, registra de novo e recarrega a aba;
  4. o gancho COPIA para uma fila em `window` as respostas de fetch/XHR e os
     quadros de WebSocket que a página JÁ recebe — só as rotas do chat
     (PLATAFORMAS, abaixo). Nada é pedido à plataforma pelo robô;
  5. a cada ~2 s o robô drena a fila e manda ao DaVinci em lotes (até 200
     eventos); a cada 60 s manda um pulso (sem pulso há 5 min, o DaVinci
     mostra a loja como "leitura parada");
  6. recarrega a PRÓPRIA aba a cada 30 min (Temu) / 20 min (AliExpress) —
     SPA aberta por dias acumula memória, e o token do WebSocket do
     AliExpress vence em ~3 h — e religa se a aba, a sessão ou o perfil
     fecharem.

O QUE ELE NUNCA FAZ (a engenharia reversa do JS das duas páginas, 29-30/09,
mostrou o que marca como lido PARA A EQUIPE INTEIRA):
  - clicar em conversa, rolar o painel de mensagens, digitar;
  - abrir URL de conversa específica: na Temu, `chat.html?posn=<pedido>`
    seleciona a conversa no carregamento (enterConv + markRead sem clique);
    no AliExpress, sessão na URL abre a conversa (putRangeRead). A URL de
    cada loja é validada ao carregar o JSON;
  - chamar API da Temu/AliExpress por conta própria (markRead, enterConv,
    markNoNeedReplyMsg, markConversation, putOffset/putRangeRead,
    sendMessage, send.msg, mtop manual...): toda chamada que sai da aba é da
    própria página, no ritmo dela — o servidor não vê nada diferente. A Temu
    ainda assina cada chamada com um token antirrobô gerado no navegador
    (Anti-Content): chamada extra com padrão estranho cai em captcha;
  - logar, resolver captcha ou verificação: sessão caída vira estado
    `sessao_caiu` no pulso e espera uma pessoa. O robô só volta a abrir o
    chat de tempos em tempos para ver se a sessão voltou — e nunca enquanto
    alguém estiver digitando na aba dele.
  VIGIA (conferência disso): o gancho CONTA, só pelo nome da rota, toda
  chamada de marcar lido/enviar que a página fizer na aba do robô (fetch,
  XHR e, pela lista de recursos do navegador, script/JSONP e beacon). Se
  aparecer uma, alguém abriu conversa na aba do robô: vai para o log e o
  pulso, e o robô volta para a lista em 1 min (nunca com alguém digitando).

O QUE VAI PARA O DAVINCI (contrato robô → DaVinci): cada evento é
  {"tipo": "http"|"ws", "url", "metodo", "status", "recebido_em", "corpo"}
  - http: `metodo` é o verbo, `status` o HTTP, `corpo` o texto da resposta;
  - ws: `status` é null e `metodo` diz como ler o `corpo`:
      "texto"   — quadro de texto, como veio (AliExpress/ACCS: JSON com `data`
                  em base64, às vezes GZIP);
      "binario" — quadro binário em BASE64, sem prefixo (Temu/Titan:
                  cabeçalho de 16 bytes + protobuf, gzip opcional);
      "json"    — só na Temu: o `payload` JSON do empurrão da Titan já
                  decodificado PELA PRÓPRIA PÁGINA (o gancho copia o texto que
                  a página passa ao JSON.parse quando ele tem "push_data").
                  Quando a sequência é contínua, a mensagem nova vem SÓ no
                  empurrão (o /sync/message seguinte já parte do seq novo) —
                  sem isto, o DaVinci teria de abrir o protobuf da Titan.
  As URLs saem sem parâmetros sensíveis (token, sign, session...): o token do
  WebSocket do AliExpress vai na URL. Resposta que carrega credencial
  (mtop ...getToken, signUrl da Temu) nem é copiada.

LOG: só contagens, ids, estados e nomes de rota. Nunca corpo, nunca texto de
comprador, nunca URL com parâmetros, nunca o token.

Uso (de apps/api; o robô não importa nada do app — só stdlib + selenium):
    DAVINCI_ROBO_TOKEN=... uv run --no-project --with selenium \\
        python scripts/atendimento_robo_adspower.py
    ... --lojas Barbosa,k1do5vfw   só essas (nome da loja ou perfil_id)
    ... --duracao 300               para sozinho depois de 300 s (teste)
    ... --seco                      não manda nada ao DaVinci (nem pulso): só conta
    ... --sem-abrir-perfil          perfil fechado fica fechado (estado "erro")

Ambiente:
    DAVINCI_ROBO_URL            padrão http://127.0.0.1:8011 (http só para este Mac)
    DAVINCI_ROBO_TOKEN          o mesmo ATENDIMENTO_ROBO_TOKEN do DaVinci
    DAVINCI_ROBO_TOKEN_ARQUIVO  alternativa: arquivo com o token (chmod 600) —
                                o que o launchd usa, para o token não morar no plist
    DAVINCI_ROBO_ESTADO         arquivo dos handles das abas (padrão
                                ~/.davinci/robo_atendimento_abas.json)
    ADSPOWER_URL                padrão http://127.0.0.1:50325
    ADSPOWER_API_KEY            só se a API local do AdsPower exigir chave
Rodar fixo: atendimento_robo_adspower.plist.exemplo (launchd).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import secrets
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSAO = "robo-adspower/1 (2026-09-30)"
LOJAS_PADRAO = Path(__file__).with_name("atendimento_robo_lojas.json")
ESTADO_PADRAO = Path.home() / ".davinci" / "robo_atendimento_abas.json"

# --- Tempos (segundos) -------------------------------------------------------
DRENAR_S = 2.0
PULSO_S = 60.0
LOG_S = 60.0
# Recarga periódica da própria aba. ±10% de folga para as 5 abas não
# recarregarem no mesmo segundo depois de um início junto.
RECARREGAR_S = {"temu": 30 * 60, "aliexpress": 20 * 60}
# A página da Temu chama getConvList/needReplyCount/sync a cada 60 s sozinha:
# 10 min sem NENHUMA resposta HTTP do chat é página travada (ou a Temu mudou o
# bundle) — recarrega. O AliExpress vive do WebSocket e pode ficar mudo por
# horas sem nada de errado: lá não há silêncio que prove problema.
SILENCIO_MAX_S: dict[str, float | None] = {"temu": 10 * 60, "aliexpress": None}
# Tela de login: de quanto em quanto tempo reabrir o chat para ver se alguém
# já entrou (por outra aba — os cookies são do perfil). Verificação/captcha
# usa o intervalo normal de recarga: recarregar mais vezes só pediria mais
# captchas sem ninguém para resolver.
REABRIR_LOGIN_S = 10 * 60
# A URL de login precisa DURAR isto para virar "sessão caiu": no teste real
# (30/09) a Temu abre chat.html → login.html → chat.html sozinha em ~8 s
# quando o cookie ainda vale — sem esta folga, a loja piscava "sessão caiu".
LOGIN_CONFIRMA_S = 25.0
# Aba do robô fora do chat (alguém a usou para outra coisa): dá esse tempo
# antes de voltar, para não arrancar a página da mão de quem está nela.
VOLTAR_AO_CHAT_S = 5 * 60
# Nenhuma navegação automática (fora a recarga agendada) mais perto que isto
# da anterior: numa página de erro de rede, voltar a cada 2 s seria martelar.
NAVEGACAO_MIN_S = 60.0
ESPERA_PERFIL_S = 60.0      # perfil fechado/AdsPower fora: tenta de novo
ESPERA_RELIGAR_S = 15.0     # sessão Selenium caiu: religa depois disto
ESPERA_GANCHO_S = 25.0      # depois de navegar, espera o gancho aparecer
ESPERA_PERFIL_SUBIR_S = 60.0
# Perfil fechado por FORA do robô depois de conectado: alguém fechou a janela
# neste Mac ou abriu o perfil em outro computador (o AdsPower costuma deixar o
# perfil num lugar só). Reabrir na hora derrubaria quem está usando — e os dois
# ficariam se derrubando. Então: espera antes de reabrir, e depois de algumas
# vezes seguidas o robô desiste daquela loja até alguém abrir o perfil aqui.
ESPERA_FECHADO_FORA_S = 30 * 60
MAX_FECHADO_FORA = 3
JANELA_FECHADO_FORA_S = 2 * 3600
# DaVinci fora (rede/5xx) vs. recusando de propósito (token, endpoint desligado).
ESPERA_ENVIO_FALHOU_S = 30.0
ESPERA_ENVIO_RECUSADO_S = 5 * 60
# O MESMO lote levou 5xx tantas vezes seguidas: pode ser um evento que o
# DaVinci nunca consegue gravar (e não o DaVinci fora). Passa a mandar evento
# a evento para achar o culpado — senão ele seguraria a fila da loja inteira.
TENTATIVAS_5XX = 3
# Falhas seguidas de envio a partir das quais o pulso diz "erro" (e não
# "lendo"): a loja não pode aparecer saudável na barra sem nada entrar.
FALHAS_PARA_AVISAR = 2

# --- Tamanhos ------------------------------------------------------------------
MAX_EVENTOS_LOTE = 200                 # contrato
MAX_CORPO = 2 * 1024 * 1024            # contrato: corpo até 2 MB cada
URL_MAX = 8 * 1024                     # contrato aceita 16 KB; folga
MAX_BYTES_LOTE = 6 * 1024 * 1024       # um POST não precisa ser de 400 MB
ENVELOPE_BYTES = 512                   # perfil_id, plataforma, loja e as chaves do POST
MAX_DRENAR_CHARS = 8_000_000           # por chamada ao navegador
MAX_FILA_PAGINA = 500                  # na página: passou disso, perde o mais velho
MAX_PENDENTES = 2000                   # por loja, enquanto o DaVinci não responde
DEDUP_S = 10 * 60                      # mesma resposta de novo: não reenviar

# --- O que copiar -----------------------------------------------------------------
# Regex testadas contra ORIGEM + CAMINHO da URL (sem a busca: um parâmetro não
# pode "puxar" uma URL qualquer para dentro do filtro), sem diferenciar
# maiúsculas. Mesmo texto no Python (url_relevante, 2ª trava antes de enviar)
# e no JS do gancho (1ª trava) — por isso só a sintaxe comum às duas: nada de
# lookbehind nem grupo nomeado.
PLATAFORMAS: dict[str, dict[str, Any]] = {
    "temu": {
        "host": r"(^|\.)temu\.com$",
        "http": [
            r"^https://([a-z0-9-]+\.)*temu\.com/api/plateau/",
            r"^https://([a-z0-9-]+\.)*temu\.com/.*/sync/",
            r"^https://([a-z0-9-]+\.)*temu\.com/.*gethistory",
        ],
        # Nunca deveriam aparecer (só existem se alguém usar a aba do robô),
        # mas se aparecerem não são leitura: marcar lido, entrar na conversa,
        # enviar, e o signUrl devolve URL ASSINADA de mídia (credencial).
        "http_nao": [
            r"/conv/(markread|enterconv|leaveconv|marktop)",
            r"/message/(send|presend|preupload)",
            r"/sign/",
            r"/translation/",
            r"marknoneedreply",
            r"markconversation",
        ],
        # Titan: wss://<host da página>?ws-titan-request-sign=...
        "ws": [r"^wss://([a-z0-9-]+\.)*temu\.com(/|$)"],
        # Marcador do payload do empurrão da Titan (ver "json" na docstring).
        "json": ['"push_data"'],
        # VIGIA: chamadas que MARCAM LIDO ou ENVIAM. Não são copiadas (estão no
        # http_nao); são só CONTADAS, pela rota, em qualquer host e por
        # qualquer meio (fetch, XHR, script/JSONP, beacon). Na aba do robô
        # nunca deveriam acontecer — se acontecem, alguém abriu uma conversa
        # nela, e conversa aberta marca como lida cada mensagem nova do
        # comprador: o robô avisa e volta para a lista.
        "vigiar": [
            r"/conv/(markread|enterconv)",
            r"marknoneedreply",
            r"markconversation",
            r"/message/send",
        ],
        # Rotas ASSINADAS (Anti-Content): só a resposta boa de uma delas prova
        # que a sessão voltou. Com o captcha pendente, as sem assinatura
        # (needReplyCount) continuam respondendo — e fariam a loja piscar
        # "lendo" a cada recarga. Só no Python (o gancho não usa).
        "assinadas": [r"/conv/get(biz)?convlist", r"/sync/message"],
    },
    "aliexpress": {
        "host": r"(^|\.)aliexpress\.(com|us)$",
        # mtop do IM em //seller-acs.aliexpress.com/h5/<api em minúsculas>/1.0/.
        # SÓ as 4 que têm conversa (as que o robo_aliexpress do DaVinci lê):
        # sync, lista de sessões, histórico e contador de não lidas. No teste
        # real de 30/09 a página chamou mais uma dúzia de APIs do IM (perfil
        # do vendedor, grupos de compradores, respostas rápidas, estatística
        # da IA...) que não têm mensagem: copiá-las era mandar dado à toa e
        # inflar o "ignorados" do DaVinci.
        "http": [
            r"^https://seller-acs\.aliexpress\.[a-z.]+/h5/mtop\."
            r"(gsp\.web\.seller\.js\.sync"
            r"|gsp\.im\.use\.web\.seller\.messagebox\.querysessionlist"
            r"|gsp\.im\.web\.seller\.messagebox\.direction\.querybysessionid"
            r"|gsp\.im\.use\.web\.seller\.unreadcount)/",
        ],
        # getToken devolve o token do WebSocket (credencial); putRangeRead é o
        # "marcar lido"; send*, o envio; download.url, link assinado.
        "http_nao": [
            r"token",
            r"putrangeread",
            r"putoffset",
            r"sendimmessage",
            r"send\.msg",
            r"translation",
            r"download\.url",
            r"upload",
        ],
        # ACCS: wss://msgacs.m.aliexpress.com/accs/auth?token=...
        "ws": [r"^wss://(ws-)?msgacs\.[a-z0-9.-]+/accs/"],
        "json": [],
        # putOffset = putRangeRead (marca lido); sendImMessage/send.msg (envio);
        # recall (apaga mensagem enviada). Ver "vigiar" da Temu.
        "vigiar": [
            r"putrangeread",
            r"putoffset",
            r"sendimmessage",
            r"send\.msg",
            r"immessage\.recall",
        ],
        # Toda mtop vai assinada (`sign` + token `_m_h5_tk`): qualquer uma com
        # SUCCESS prova a sessão.
        "assinadas": [r"/h5/mtop\."],
    },
}

# `sess` pega session e o `_x_sessn_id` que a Temu pendura na URL do chat.
_PARAM_SENSIVEL = re.compile(
    r"token|sign|secret|auth|ticket|passw|cookie|sess|^sid$|key", re.I
)
_VERIFICACAO_URL = re.compile(
    r"punish|captcha|_____tmd_____|x5sec|verif|challenge|baxia", re.I
)
_LOGIN_URL = re.compile(
    r"login|signin|sign-in|sign_in|passport|logon|authentication", re.I
)
# Sinais no corpo das respostas, lidos do ENVELOPE (JSON), nunca por busca de
# texto: a mensagem de um comprador mora numa string do mesmo corpo (a prévia
# em sessionData.content, o templateData do js.sync) e pode começar com
# "RGV587_ERROR" — por texto, qualquer comprador apagaria a loja na barra e
# mandaria a equipe atrás de um captcha que não existe (revisão 30/09).
_TEMU_VERIFICACAO = "54001"
_ALI_VERIFICACAO = frozenset({"RGV587_ERROR", "FAIL_SYS_USER_VALIDATE"})
# FAIL_SYS_TOKEN_EXOIRED/EMPTY NÃO estão aqui: é o token da mtop vencendo, a
# página renova e repete sozinha — rotina, não sessão caída.
_ALI_LOGIN = frozenset({"FAIL_SYS_SESSION_EXPIRED", "SESSION_EXPIRED"})
_ALI_LOGIN_PREFIXO = "FAIL_SYS_LOGIN"
_JSONP = re.compile(r"^[\w$.]+\s*\(\s*(?P<corpo>[\[{].*)\)\s*;?$", re.S)

log = logging.getLogger("robo_atendimento")


# ============================================================================
# Partes puras (testadas em tests/test_atendimento_robo_script.py)
# ============================================================================


def _base(url: str) -> str:
    """Origem + caminho, sem busca nem fragmento — o que os filtros olham."""
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path or '/'}"


def url_relevante(plataforma: str, tipo: str, url: str) -> bool:
    """O evento é do chat desta plataforma? (2ª trava; a 1ª é o gancho)."""
    cfg = PLATAFORMAS.get(plataforma)
    if cfg is None or not isinstance(url, str):
        return False
    try:
        base = _base(url)
    except ValueError:
        return False
    if tipo == "ws":
        return any(re.search(r, base, re.I) for r in cfg["ws"])
    if tipo == "http":
        return any(re.search(r, base, re.I) for r in cfg["http"]) and not any(
            re.search(r, base, re.I) for r in cfg["http_nao"]
        )
    return False


def limpar_url(url: str) -> str:
    """URL sem os parâmetros que carregam credencial (token do WebSocket do
    AliExpress, `sign` da mtop, assinatura da Titan...)."""
    try:
        p = urllib.parse.urlsplit(url)
        busca = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            if not _PARAM_SENSIVEL.search(k)
        ]
        return urllib.parse.urlunsplit(
            (p.scheme, p.netloc, p.path, urllib.parse.urlencode(busca), p.fragment)
        )
    except ValueError:
        return re.split(r"[?#]", url, maxsplit=1)[0]


def rotulo_rota(evento: dict) -> str:
    """Nome curto da rota para o log ("conv/getConvList", "ws:binario")."""
    if evento.get("tipo") == "ws":
        return f"ws:{evento.get('metodo') or '?'}"
    try:
        partes = [x for x in urllib.parse.urlsplit(evento.get("url") or "").path.split("/") if x]
    except ValueError:
        return "?"
    mtop = next((x for x in partes if x.lower().startswith("mtop.")), None)
    if mtop:
        return mtop
    return "/".join(partes[-2:]) or "/"


def situacao_da_aba(url_atual: str | None, url_chat: str) -> str:
    """Onde a aba do robô está: "chat", "login", "verificacao", "fora" ou "vazia".

    Olha host + caminho (não a busca: o login costuma levar a URL de volta no
    parâmetro, e ela contém "chat"). Verificação antes de login: a página de
    punição do AliExpress pode morar sob o domínio de login.
    """
    if not url_atual or url_atual.startswith(("about:", "chrome:", "chrome-error:", "data:")):
        return "vazia"
    try:
        a = urllib.parse.urlsplit(url_atual)
        c = urllib.parse.urlsplit(url_chat)
    except ValueError:
        return "fora"
    alvo = f"{a.netloc}{a.path}"
    if _VERIFICACAO_URL.search(alvo):
        return "verificacao"
    if _LOGIN_URL.search(alvo):
        return "login"
    if a.netloc.lower() == c.netloc.lower() and a.path.rstrip("/") == c.path.rstrip("/"):
        return "chat"
    return "fora"


def _envelope(corpo: str) -> dict | None:
    """O objeto JSON de fora da resposta (aceita BOM e JSONP da mtop); None se não for."""
    t = corpo.lstrip("﻿").strip()
    if t[:1] != "{":
        m = _JSONP.match(t)
        if not m:
            return None
        t = m.group("corpo")
    try:
        obj = json.loads(t)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def classificar_corpo(plataforma: str, corpo: str) -> str | None:
    """Sinal de sessão numa resposta HTTP do chat: "verificacao", "login",
    "ok" ou None (nada a dizer). Serve para o caso em que a URL não muda: o
    captcha da Temu (erro 54001) abre por cima do próprio chat.html.

    Só o envelope conta — `success`/`errorCode` na Temu, o código do
    `ret[0]` (antes do "::") no AliExpress, como o leitor do DaVinci faz —,
    nunca um texto solto no meio do corpo."""
    if not isinstance(corpo, str) or not corpo:
        return None
    envelope = _envelope(corpo)
    if envelope is None:
        return None
    if plataforma == "temu":
        sucesso = envelope.get("success")
        codigo = envelope.get("errorCode", envelope.get("error_code"))
        if sucesso is False and str(codigo).strip() == _TEMU_VERIFICACAO:
            return "verificacao"
        return "ok" if sucesso is True else None
    if plataforma == "aliexpress":
        ret = envelope.get("ret")
        if not isinstance(ret, list) or not ret:
            return None
        codigos = [str(r).split("::", 1)[0].strip() for r in ret]
        if any(c.startswith("SUCCESS") for c in codigos):
            return "ok"
        if codigos[0] in _ALI_VERIFICACAO:
            return "verificacao"
        if codigos[0] in _ALI_LOGIN or codigos[0].startswith(_ALI_LOGIN_PREFIXO):
            return "login"
        return None
    return None


def rota_assinada(plataforma: str, url: str) -> bool:
    """A rota é das assinadas (a resposta boa dela prova a sessão)?"""
    cfg = PLATAFORMAS.get(plataforma) or {}
    try:
        base = _base(url)
    except ValueError:
        return False
    return any(re.search(r, base, re.I) for r in cfg.get("assinadas", []))


def _agora_iso() -> str:
    return datetime.now(UTC).isoformat()


def _recebido_em_valido(valor: Any) -> bool:
    if not isinstance(valor, str) or len(valor) > 40:
        return False
    try:
        datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def preparar_evento(plataforma: str, bruto: Any) -> dict | None:
    """Evento drenado da página → evento do contrato (ou None se não serve).

    O que vem da página é dado da página: confere tipo por tipo, repassa só o
    que é do chat (url_relevante), tira credencial da URL e descarta corpo
    acima do limite do contrato — cortar o texto quebraria o JSON e o parser
    do DaVinci registraria lixo.
    """
    if not isinstance(bruto, dict):
        return None
    tipo, url, corpo = bruto.get("tipo"), bruto.get("url"), bruto.get("corpo")
    if tipo not in ("http", "ws") or not isinstance(url, str) or not isinstance(corpo, str):
        return None
    if not url_relevante(plataforma, tipo, url):
        return None
    if len(corpo.encode("utf-8", "surrogatepass")) > MAX_CORPO:
        return None
    metodo = bruto.get("metodo")
    status = bruto.get("status")
    recebido_em = bruto.get("recebido_em")
    url = limpar_url(url)
    if len(url) > URL_MAX:
        # GET da mtop leva o pedido inteiro na busca: sem ela, a rota basta
        # (o contrato recusa URL acima de 16 KB — o lote inteiro cairia).
        url = _base(url)
    return {
        "tipo": tipo,
        "url": url,
        "metodo": metodo[:16] if isinstance(metodo, str) else None,
        "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
        "recebido_em": recebido_em if _recebido_em_valido(recebido_em) else _agora_iso(),
        "corpo": corpo,
    }


def _tamanho(evento: dict) -> int:
    """Bytes do evento DENTRO do POST: o corpo vai como string JSON, com aspas
    e barras escapadas — o js.sync do AliExpress (JSON dentro de string
    dentro de string) cresce ~1,3x. Medir o corpo cru estourava o teto."""
    return len(json.dumps(evento, ensure_ascii=False).encode("utf-8", "surrogatepass")) + 2


def primeiro_lote(
    eventos: list[dict],
    max_eventos: int = MAX_EVENTOS_LOTE,
    max_bytes: int = MAX_BYTES_LOTE,
) -> int:
    """Quantos eventos da FRENTE da fila cabem num POST (pelo menos 1: um
    evento sozinho maior que `max_bytes` vai num lote só dele). Mede só o
    que entra no lote — a fila pode ter 2000 eventos."""
    soma = ENVELOPE_BYTES
    n = 0
    for ev in eventos:
        if n >= max_eventos:
            break
        t = _tamanho(ev)
        if n and soma + t > max_bytes:
            break
        soma += t
        n += 1
    return n


def montar_lotes(
    eventos: list[dict],
    max_eventos: int = MAX_EVENTOS_LOTE,
    max_bytes: int = MAX_BYTES_LOTE,
) -> list[list[dict]]:
    """Fatia a fila em lotes CONSECUTIVOS (a ordem importa: o envio apaga da
    frente da fila o lote que o DaVinci aceitou)."""
    lotes: list[list[dict]] = []
    i = 0
    while i < len(eventos):
        n = primeiro_lote(eventos[i:], max_eventos, max_bytes)
        lotes.append(eventos[i : i + n])
        i += n
    return lotes


def resposta_do_contrato(resposta: Any) -> bool:
    """O DaVinci aceitou o lote? Só com o JSON do contrato ({ok: true,
    gravadas, conversas, ignorados}): um 200 de proxy ou página de erro não
    é entrega — o lote sairia da fila sem ter chegado."""
    if not isinstance(resposta, dict) or resposta.get("ok") is not True:
        return False
    return all(
        isinstance(resposta.get(k), int) and not isinstance(resposta.get(k), bool)
        for k in ("gravadas", "conversas", "ignorados")
    )


class Dedup:
    """A página da Temu pede a MESMA lista de conversas a cada 60 s: mandar
    150 KB iguais por minuto por loja é trabalho à toa para os dois lados. Se
    a mesma resposta (rota + corpo) já foi para a fila de envio há menos de
    `ttl`, não vai de novo. O DaVinci é idempotente de qualquer forma — isto
    é só economia; depois do `ttl` a resposta repetida vai outra vez."""

    def __init__(self, ttl: float, agora: Callable[[], float]):
        self.ttl = ttl
        self.agora = agora
        self._vistos: dict[str, float] = {}

    @staticmethod
    def _chave(ev: dict) -> str:
        h = hashlib.sha1(usedforsecurity=False)
        h.update(f"{ev['tipo']}|{ev.get('metodo')}|{_base(ev['url'])}|".encode())
        h.update(ev["corpo"].encode("utf-8", "surrogatepass"))
        return h.hexdigest()

    def novo(self, ev: dict) -> bool:
        """True (e marca) se não foi visto dentro do `ttl`."""
        agora = self.agora()
        chave = self._chave(ev)
        visto = self._vistos.get(chave)
        if visto is not None and agora - visto < self.ttl:
            return False
        self._vistos[chave] = agora
        if len(self._vistos) > 5000:
            self._vistos = {k: t for k, t in self._vistos.items() if agora - t < self.ttl}
        return True


@dataclass(frozen=True)
class Loja:
    perfil_id: str
    plataforma: str
    loja: str
    url: str

    @property
    def rotulo(self) -> str:
        return f"{self.plataforma}/{self.loja}"


def validar_loja(item: Any) -> Loja:
    """Uma entrada do JSON → Loja, ou ValueError dizendo o que está errado.

    A URL é a trava mais importante: o robô NUNCA abre conversa específica.
    """
    if not isinstance(item, dict):
        raise ValueError("cada loja precisa ser um objeto")
    faltam = [k for k in ("perfil_id", "plataforma", "loja", "url") if not item.get(k)]
    if faltam:
        raise ValueError(f"faltam campos: {', '.join(faltam)}")
    plataforma = str(item["plataforma"]).strip().lower()
    if plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma desconhecida: {plataforma}")
    url = str(item["url"]).strip()
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not re.search(PLATAFORMAS[plataforma]["host"], p.hostname or ""):
        raise ValueError(f"url fora do domínio da {plataforma}")
    # ?posn= (Temu) seleciona a conversa do pedido no carregamento e MARCA
    # LIDO; sessão/conversa na URL (AliExpress) abre a conversa. Recusa
    # qualquer parâmetro — a lista de conversas não precisa de nenhum.
    if p.query or re.search(r"posn|session|conv|[?&]", p.fragment, re.I):
        raise ValueError("url com parâmetro de conversa: o robô só abre a LISTA do chat")
    perfil = str(item["perfil_id"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", perfil):
        raise ValueError("perfil_id inválido")
    return Loja(perfil_id=perfil, plataforma=plataforma, loja=str(item["loja"]).strip(), url=url)


def carregar_lojas(caminho: Path, filtro: str | None = None) -> list[Loja]:
    """Lê o JSON (lista, ou {"lojas": [...]}) e aplica o --lojas (nome da loja
    ou perfil_id, separados por vírgula, sem diferenciar maiúsculas)."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if isinstance(dados, dict):
        dados = dados.get("lojas")
    if not isinstance(dados, list) or not dados:
        raise ValueError(f"{caminho}: esperava uma lista de lojas")
    lojas = [validar_loja(x) for x in dados]
    perfis = [x.perfil_id for x in lojas]
    if len(set(perfis)) != len(perfis):
        # Duas threads no mesmo perfil = duas abas e duas conexões de chat.
        raise ValueError(f"{caminho}: perfil_id repetido")
    if filtro:
        quer = {x.strip().lower() for x in filtro.split(",") if x.strip()}
        lojas = [x for x in lojas if x.loja.lower() in quer or x.perfil_id.lower() in quer]
        if not lojas:
            raise ValueError(f"--lojas {filtro!r} não casou com nenhuma loja do JSON")
    return lojas


def conferir_url_davinci(url: str) -> str:
    """O token vai no cabeçalho: por http, só dentro deste Mac."""
    p = urllib.parse.urlsplit(url.strip())
    if p.scheme == "https" and p.hostname:
        return url.strip().rstrip("/")
    if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1"):
        return url.strip().rstrip("/")
    raise ValueError("DAVINCI_ROBO_URL precisa ser https (http só para 127.0.0.1/localhost)")


def endereco_depuracao(info: dict) -> str:
    """host:porta do navegador do perfil, da resposta do AdsPower."""
    ws = info.get("ws") or {}
    if isinstance(ws, dict) and ws.get("selenium"):
        return str(ws["selenium"])
    porta = info.get("debug_port")
    if porta:
        return f"127.0.0.1:{porta}"
    raise ValueError("AdsPower não devolveu a porta de depuração do perfil")


def tipo_de_falha(exc: BaseException) -> str:
    """Classifica um erro do Selenium sem importar o Selenium (os testes e o
    --help não precisam dele): "aba" (a nossa aba sumiu), "sessao" (o
    navegador ou o chromedriver caíram — religar), "lenta" (tempo esgotado)
    ou "outra"."""
    nome = type(exc).__name__
    texto = str(exc).lower()
    if nome == "NoSuchWindowException" or "no such window" in texto or (
        "target window already closed" in texto
    ):
        return "aba"
    if nome in (
        "InvalidSessionIdException", "MaxRetryError", "ProtocolError", "NewConnectionError"
    ):
        return "sessao"
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return "sessao"
    if "net::err_" in texto:
        # Erro de REDE da página (net::ERR_INTERNET_DISCONNECTED...): o
        # navegador está vivo; religar a sessão não adianta.
        return "outra"
    if any(
        s in texto
        for s in (
            "disconnected:",
            "not connected to devtools",
            "not reachable",
            "invalid session id",
            "no such session",
            "session deleted",
            "connection refused",
            "max retries exceeded",
            "unable to connect",
            "cannot connect to chrome",
            "devtoolsactiveport",
        )
    ):
        return "sessao"
    if nome in ("TimeoutException", "ScriptTimeoutException") or isinstance(exc, TimeoutError):
        return "lenta"
    return "outra"


def resumo_erro(exc: BaseException) -> str:
    """Uma linha curta sobre o erro, sem URL com parâmetros (mensagens do
    Selenium citam a página) e sem pilha."""
    texto = (str(exc).strip().splitlines() or [""])[0]
    texto = re.sub(r"(\b[a-z][a-z0-9+.-]*://[^\s?#]*)[?#]\S*", r"\1", texto, flags=re.I)
    texto = re.sub(r"(?i)(bearer|token)[=: ]+\S+", r"\1 ***", texto)
    return f"{type(exc).__name__}: {texto}"[:200].rstrip(": ")


# ============================================================================
# O gancho (JS que roda DENTRO da aba, antes dos scripts da página)
# ============================================================================
# Decisões:
#  - Proxy em vez de função embrulhada: `fetch.toString()` continua dizendo
#    "[native code]" (os SDKs antirrobô da Temu e do AliExpress olham sinais
#    de automação; embrulho comum mostraria o nosso código). Metadados do XHR
#    num WeakMap, não em propriedade no objeto.
#  - Só ESCUTA: o fetch devolve a MESMA promessa da página (a cópia lê um
#    clone); o XHR ganha um ouvinte 'load' a mais; o WebSocket, um ouvinte
#    'message' a mais. Nada é enviado, nada é pedido.
#  - O acesso do robô é uma propriedade NÃO enumerável de `window` com nome
#    sorteado a cada conexão (não cai numa busca por nomes conhecidos).
#  - `addScriptToEvaluateOnNewDocument` roda o gancho em toda frame da aba;
#    uma frame do mesmo site repassa para a fila da janela de cima.
_GANCHO_JS = r"""
(() => {
  const NOME = __NOME__;
  const CFG = __CFG__;
  try {
    if (Object.prototype.hasOwnProperty.call(window, NOME)) return;
  } catch (e) { return; }
  const re = (l) => (l || []).map((s) => new RegExp(s, 'i'));
  const HTTP = re(CFG.http), HTTP_NAO = re(CFG.http_nao), WS = re(CFG.ws);
  const JSONS = CFG.json || [];
  const VIGIA = re(CFG.vigiar);
  const fila = [];
  const conta = { perdidos: 0, grandes: 0, erros: 0, jsonp: 0 };
  // VIGIA: origem + caminho (sem busca) → quantas vezes a página chamou.
  const vetadas = {};
  const agora = () => new Date().toISOString();
  const absoluta = (u) => {
    try { return new URL(String(u), location.href).href; } catch (e) { return String(u); }
  };
  const base = (u) => {
    try { const x = new URL(u); return x.origin + x.pathname; }
    catch (e) { return String(u).split(/[?#]/)[0]; }
  };
  const casa = (lista, u) => lista.some((r) => r.test(u));
  const querHttp = (u) => { const b = base(u); return casa(HTTP, b) && !casa(HTTP_NAO, b); };
  const querWs = (u) => casa(WS, base(u));
  // Evento do contrato robô → DaVinci.
  const ev = (tipo, url, metodo, status, quando, corpo) =>
    ({ tipo, url, metodo, status, recebido_em: quando, corpo });
  let destino = null;
  try {
    if (window.top !== window && window.top[NOME]) destino = window.top[NOME];
  } catch (e) {}
  const vetar = (b) => {
    try {
      if (destino) { destino.vetar(b); return; }
      if (b in vetadas || Object.keys(vetadas).length < 50) vetadas[b] = (vetadas[b] || 0) + 1;
    } catch (x) {}
  };
  const vigia = (u) => { const b = base(u); if (casa(VIGIA, b)) vetar(b); };
  const guarda = (e) => {
    try {
      if (typeof e.corpo !== 'string') { conta.erros++; return; }
      if (e.corpo.length > CFG.max_corpo) { conta.grandes++; return; }
      if (destino) { destino.receber(e); return; }
      fila.push(e);
      if (fila.length > CFG.max_fila) { fila.shift(); conta.perdidos++; }
    } catch (x) { conta.erros++; }
  };

  const f0 = window.fetch;
  if (typeof f0 === 'function') {
    window.fetch = new Proxy(f0, {
      apply(alvo, isto, args) {
        const p = Reflect.apply(alvo, isto, args);
        try {
          const pedido = args[0];
          const obj = pedido && typeof pedido === 'object';
          const u = absoluta(obj && 'url' in pedido ? pedido.url : pedido);
          vigia(u);
          if (querHttp(u)) {
            const op = args[1] || {};
            const m = String(op.method || (obj && pedido.method) || 'GET').toUpperCase();
            p.then((r) => {
              try {
                const quando = agora();
                r.clone().text().then(
                  (t) => guarda(ev('http', r.url || u, m, r.status, quando, t)),
                  () => { conta.erros++; });
              } catch (x) { conta.erros++; }
            }, () => {});
          }
        } catch (x) { conta.erros++; }
        return p;
      },
    });
  }

  const X = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (X && typeof X.open === 'function' && typeof X.send === 'function') {
    const meta = new WeakMap();
    X.open = new Proxy(X.open, {
      apply(alvo, xhr, args) {
        try {
          meta.set(xhr, { m: String(args[0] || 'GET').toUpperCase(), u: absoluta(args[1]) });
        } catch (x) {}
        return Reflect.apply(alvo, xhr, args);
      },
    });
    X.send = new Proxy(X.send, {
      apply(alvo, xhr, args) {
        try {
          const d = meta.get(xhr);
          if (d) vigia(d.u);
          if (d && querHttp(d.u)) {
            xhr.addEventListener('load', () => {
              try {
                const rt = xhr.responseType;
                let t = null;
                if (rt === '' || rt === 'text') t = xhr.responseText;
                else if (rt === 'json') t = JSON.stringify(xhr.response);
                if (t === null || t === undefined) { conta.erros++; return; }
                guarda(ev('http', xhr.responseURL || d.u, d.m, xhr.status, agora(), t));
              } catch (x) { conta.erros++; }
            });
          }
        } catch (x) { conta.erros++; }
        return Reflect.apply(alvo, xhr, args);
      },
    });
  }

  const W = window.WebSocket;
  if (typeof W === 'function') {
    const b64 = (bytes) => {
      let s = '';
      for (let i = 0; i < bytes.length; i += 0x8000) {
        s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      return btoa(s);
    };
    const quadro = (u, d, quando) => {
      if (typeof d === 'string') { guarda(ev('ws', u, 'texto', null, quando, d)); return; }
      let bytes = null;
      if (d instanceof ArrayBuffer) bytes = new Uint8Array(d);
      else if (ArrayBuffer.isView(d)) bytes = new Uint8Array(d.buffer, d.byteOffset, d.byteLength);
      if (bytes) {
        if (Math.ceil(bytes.length / 3) * 4 > CFG.max_corpo) { conta.grandes++; return; }
        guarda(ev('ws', u, 'binario', null, quando, b64(bytes)));
        return;
      }
      if (typeof Blob !== 'undefined' && d instanceof Blob) {
        d.arrayBuffer().then((b) => quadro(u, b, quando), () => { conta.erros++; });
        return;
      }
      conta.erros++;
    };
    window.WebSocket = new Proxy(W, {
      construct(alvo, args, novo) {
        const ws = Reflect.construct(alvo, args, novo);
        try {
          const u = absoluta(args[0]);
          if (querWs(u)) {
            ws.addEventListener('message', (m) => {
              try { quadro(u, m.data, agora()); } catch (x) { conta.erros++; }
            });
          }
        } catch (x) { conta.erros++; }
        return ws;
      },
    });
  }

  if (JSONS.length) {
    const urlJson = 'wss://' + location.hostname + '/';
    JSON.parse = new Proxy(JSON.parse, {
      apply(alvo, isto, args) {
        const r = Reflect.apply(alvo, isto, args);
        try {
          const s = args[0];
          if (typeof s === 'string' && s.length <= CFG.max_corpo
              && JSONS.some((m) => s.indexOf(m) !== -1)) {
            guarda(ev('ws', urlJson, 'json', null, agora(), s));
          }
        } catch (x) {}
        return r;
      },
    });
  }

  // O que NÃO passa por fetch/XHR (script/JSONP da mtop, sendBeacon, img):
  // a lista de recursos do navegador vê tudo, só pelo endereço. Rota vetada
  // conta na VIGIA; rota do chat carregada assim é uma resposta que o gancho
  // NÃO consegue copiar (conta como jsonp — sinal de que falta ler algo).
  try {
    if (typeof PerformanceObserver === 'function') {
      new PerformanceObserver((lista) => {
        for (const e of lista.getEntries()) {
          try {
            const t = e.initiatorType;
            if (t === 'fetch' || t === 'xmlhttprequest') continue;
            const b = base(e.name);
            if (casa(VIGIA, b)) vetar(b);
            else if (casa(HTTP, b) && !casa(HTTP_NAO, b)) conta.jsonp++;
          } catch (x) {}
        }
      }).observe({ type: 'resource', buffered: true });
    }
  } catch (x) {}

  const api = {
    drenar(max, maxChars) {
      const n = Math.max(1, Math.min(Number(max) || 200, 1000));
      const limite = Number(maxChars) || 8e6;
      const eventos = [];
      let soma = 0;
      while (fila.length && eventos.length < n) {
        const t = fila[0].corpo.length;
        if (eventos.length && soma + t > limite) break;
        eventos.push(fila.shift());
        soma += t;
      }
      return {
        eventos, resta: fila.length, perdidos: conta.perdidos, grandes: conta.grandes,
        erros: conta.erros, jsonp: conta.jsonp, vetadas: Object.assign({}, vetadas),
        href: String(location.href),
      };
    },
    receber(e) { guarda(e); },
    vetar(b) { vetar(b); },
  };
  try {
    Object.defineProperty(window, NOME, {
      value: Object.freeze(api), enumerable: false, configurable: false, writable: false,
    });
  } catch (x) {}
})();
"""

_DRENAR_JS = (
    "const a = window[arguments[0]]; return a ? a.drenar(arguments[1], arguments[2]) : null;"
)
_TEM_GANCHO_JS = "return Object.prototype.hasOwnProperty.call(window, arguments[0]);"
# Alguém digitando na aba do robô (o login, por exemplo): não navegar agora.
# Campo com foco E com conteúdo — um campo de busca vazio que a página focou
# sozinha não pode segurar a recarga para sempre. Só o booleano sai da página.
_DIGITANDO_JS = (
    "const a = document.activeElement;"
    "if (!a || !document.hasFocus()) return false;"
    "if (a.isContentEditable) return (a.textContent || '').trim().length > 0;"
    "if (/^(INPUT|TEXTAREA)$/.test(a.tagName)) return String(a.value || '').length > 0;"
    "return false;"
)


def montar_gancho(plataforma: str, nome: str) -> str:
    """O JS do gancho para a plataforma, com o nome sorteado da fila."""
    cfg = PLATAFORMAS[plataforma]
    config = {
        "http": cfg["http"],
        "http_nao": cfg["http_nao"],
        "ws": cfg["ws"],
        "json": cfg["json"],
        "vigiar": cfg["vigiar"],
        "max_corpo": MAX_CORPO,
        "max_fila": MAX_FILA_PAGINA,
    }
    return _GANCHO_JS.replace("__NOME__", json.dumps(nome)).replace(
        "__CFG__", json.dumps(config)
    )


# ============================================================================
# Clientes: AdsPower (API local) e DaVinci
# ============================================================================


class AdsPowerFora(RuntimeError):  # noqa: N818 — português, como `ErroCaixa`
    """A API local do AdsPower não respondeu (app fechado?)."""


class AdsPowerErro(RuntimeError):  # noqa: N818 — português, como `ErroCaixa`
    """O AdsPower respondeu com erro (perfil inexistente, aberto em outro Mac...)."""


class AdsPower:
    """API local do AdsPower. Ela aguenta ~1 chamada por segundo: todas as
    threads passam por uma trava que espaça as chamadas."""

    def __init__(self, base: str, chave: str = ""):
        self.base = base.rstrip("/")
        self.chave = chave
        self._trava = threading.Lock()
        self._ultima = 0.0

    def _get(self, caminho: str, params: dict, timeout: float = 20) -> dict:
        url = f"{self.base}{caminho}?{urllib.parse.urlencode(params)}"
        cab = {"Authorization": f"Bearer {self.chave}"} if self.chave else {}
        with self._trava:
            espera = 1.1 - (time.monotonic() - self._ultima)
            if espera > 0:
                time.sleep(espera)
            try:
                req = urllib.request.Request(url, headers=cab)  # noqa: S310 — URL local fixa
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                    dados = json.loads(r.read().decode("utf-8") or "{}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise AdsPowerFora(type(e).__name__) from None
            finally:
                self._ultima = time.monotonic()
        if dados.get("code") != 0:
            raise AdsPowerErro(str(dados.get("msg") or "erro sem mensagem")[:160])
        return dados.get("data") or {}

    def ativo(self, perfil: str) -> dict | None:
        """Dados do perfil aberto (endereço de depuração, chromedriver) ou None."""
        dados = self._get("/api/v1/browser/active", {"user_id": perfil})
        return dados if dados.get("status") == "Active" else None

    def abrir(self, perfil: str) -> dict:
        # open_tabs=1: não reabre as abas salvas do perfil (cada uma seria mais
        # uma página do Seller Center — e mais uma conexão de chat); ip_tab=0:
        # sem a aba de checagem de IP. Só o robô abre a aba dele depois.
        return self._get(
            "/api/v1/browser/start",
            {"user_id": perfil, "open_tabs": 1, "ip_tab": 0},
            timeout=120,
        )


class DaVinciFora(RuntimeError):  # noqa: N818 — português, como `ErroCaixa`
    """Rede, tempo esgotado, 5xx. `status` é o HTTP quando o DaVinci RESPONDEU
    (5xx): aí o problema pode ser o próprio lote, não o DaVinci fora."""

    def __init__(self, motivo: str, status: int | None = None):
        super().__init__(motivo)
        self.status = status


class DaVinciRecusou(RuntimeError):  # noqa: N818 — como `EnvioRecusado`
    def __init__(self, status: int, motivo: str):
        super().__init__(f"HTTP {status}: {motivo}")
        self.status = status
        self.motivo = motivo


def _motivo_http(e: urllib.error.HTTPError) -> str:
    """Motivo legível SEM ecoar o que enviamos: o 422 do FastAPI devolve o
    `input` de cada erro — que pode ser o corpo com texto de comprador. Fica
    só `loc` e `msg`."""
    if 300 <= e.code < 400:
        return (
            "o DaVinci respondeu com redirecionamento e o robô não segue (o token iria "
            "junto para outro endereço): confira o DAVINCI_ROBO_URL"
        )
    if e.code in (401, 403):
        return "token recusado (DAVINCI_ROBO_TOKEN ≠ ATENDIMENTO_ROBO_TOKEN do DaVinci?)"
    if e.code in (404, 503):
        return "endpoints do robô desligados no DaVinci (ATENDIMENTO_ROBO_TOKEN vazio?)"
    if e.code == 422:
        try:
            detalhe = json.loads(e.read().decode("utf-8")).get("detail")
            partes = [
                f"{'.'.join(str(x) for x in d.get('loc', []))}: {d.get('msg', '')}"
                for d in detalhe
                if isinstance(d, dict)
            ]
            return "; ".join(partes)[:300] or "payload recusado"
        except (ValueError, AttributeError, TypeError, OSError):
            return "payload recusado"
    return "erro no DaVinci"


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """O opener padrão do urllib segue 301/302/303 repetindo o pedido como
    GET e COPIANDO os cabeçalhos — o Authorization iria para qualquer host e
    esquema (a trava do http-só-local vale só para a URL inicial) e o lote
    seria dado como entregue sem ter chegado. Sem seguir, o 3xx vira HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913
        return None


class DaVinci:
    def __init__(self, base: str, token: str):
        self.base = conferir_url_davinci(base)
        self._token = token
        self._abrir = urllib.request.build_opener(_SemRedirecionar).open

    def post(self, caminho: str, corpo: dict, timeout: float = 60) -> dict:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8", "surrogatepass")
        req = urllib.request.Request(  # noqa: S310 — https (ou http local), conferido
            self.base + caminho,
            data=dados,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": VERSAO,
            },
        )
        try:
            with self._abrir(req, timeout=timeout) as r:
                texto = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code >= 500 and e.code != 503:
                raise DaVinciFora(f"HTTP {e.code}", status=e.code) from None
            raise DaVinciRecusou(e.code, _motivo_http(e)) from None
        except (urllib.error.URLError, OSError) as e:
            raise DaVinciFora(type(e).__name__) from None
        try:
            resposta = json.loads(texto or "{}")
        except ValueError:
            return {}
        return resposta if isinstance(resposta, dict) else {}


class EstadoAbas:
    """perfil_id → handle da aba do robô, em disco. Sem isto, cada reinício do
    robô (launchd KeepAlive) abriria mais uma aba no perfil — e cada aba do
    chat é mais uma conexão de chat da loja (a Temu derruba conexão demais:
    'too many connection' / 'multi terminal login')."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._trava = threading.Lock()

    def _ler(self) -> dict[str, str]:
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in dados.items()} if isinstance(dados, dict) else {}

    def handle(self, perfil: str) -> str | None:
        with self._trava:
            return self._ler().get(perfil)

    def guardar(self, perfil: str, handle: str | None) -> None:
        with self._trava:
            dados = self._ler()
            if handle:
                dados[perfil] = handle
            else:
                dados.pop(perfil, None)
            try:
                self.caminho.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.caminho.with_suffix(".tmp")
                tmp.write_text(json.dumps(dados, indent=1), encoding="utf-8")
                tmp.replace(self.caminho)
            except OSError as e:
                log.warning("não consegui gravar %s: %s", self.caminho, type(e).__name__)


def criar_driver_selenium(info: dict) -> Any:
    """Selenium preso ao navegador JÁ aberto do perfil (não abre navegador).

    `quit()` numa sessão presa por debuggerAddress só encerra o chromedriver —
    o navegador do perfil continua aberto.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    op = Options()
    op.add_experimental_option("debuggerAddress", endereco_depuracao(info))
    # eager: volta no DOMContentLoaded. O gancho já está instalado desde a
    # criação do documento; esperar pixel de rastreio não ajuda em nada.
    op.page_load_strategy = "eager"
    servico = Service(executable_path=info["webdriver"], log_output=os.devnull)
    drv = webdriver.Chrome(service=servico, options=op)
    drv.set_page_load_timeout(60)
    drv.set_script_timeout(20)
    return drv


# ============================================================================
# Leitor: uma loja (uma thread, uma sessão Selenium, uma aba)
# ============================================================================


@dataclass
class Contexto:
    adspower: Any                       # AdsPower (ou falso, nos testes)
    davinci: Any | None                 # None = --seco
    abrir_perfil: bool
    estado: EstadoAbas
    criar_driver: Callable[[dict], Any] = criar_driver_selenium
    parar: threading.Event = field(default_factory=threading.Event)
    agora: Callable[[], float] = time.monotonic


class _LogLoja(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})["loja"] = self.extra["loja"]
        return msg, kwargs


def espera_reabrir(fechamentos: list[float], agora: float) -> float | None:
    """Segundos até poder reabrir um perfil que foi fechado por fora do robô.

    `fechamentos` = quando o robô viu o perfil sumir depois de conectado.
    None = fechou vezes demais na janela: não reabrir sozinho (se alguém abrir o
    perfil neste Mac, o robô conecta nele normalmente).
    """
    recentes = [t for t in fechamentos if agora - t <= JANELA_FECHADO_FORA_S]
    if len(recentes) >= MAX_FECHADO_FORA:
        return None
    if not recentes:
        return 0.0
    return max(0.0, recentes[-1] + ESPERA_FECHADO_FORA_S - agora)


class Leitor(threading.Thread):
    """Lê o chat de UMA loja. `passo()` é uma rodada (os testes chamam direto);
    `run()` repete até o `parar`."""

    def __init__(self, loja: Loja, ctx: Contexto):
        super().__init__(name=f"robo-{loja.loja}", daemon=True)
        self.loja = loja
        self.ctx = ctx
        self.log = _LogLoja(log, {"loja": loja.rotulo})
        self.drv: Any = None
        self.handle: str | None = None
        self.nome_gancho: str | None = None
        self.estado = "iniciando"
        self.detalhe: str | None = None
        self._pulso_ja = True           # o 1º pulso sai logo
        self.url_aba: str | None = None
        self.pendentes: list[dict] = []
        self.dedup = Dedup(DEDUP_S, ctx.agora)
        self.conta: Counter = Counter()
        self.rotas: Counter = Counter()
        self._pagina = {"perdidos": 0, "grandes": 0, "erros": 0, "jsonp": 0}
        # VIGIA: rota vetada → contagem já vista NESTE documento (o gancho
        # conta por documento; a diferença é o que a página chamou de novo).
        self._vetadas_doc: dict[str, int] = {}
        self.vetadas: Counter = Counter()      # rótulo da rota → total da execução
        self.rotas_total: Counter = Counter()  # rótulo da rota → total da execução
        # Quando a VIGIA pegou uma conversa aberta na aba do robô (None = não).
        self.conversa_aberta_em: float | None = None
        # Sinal de sessão POR ROTA ("verificacao"/"login"). Por rota porque,
        # com o captcha da Temu pendente, só as chamadas assinadas (Anti-
        # Content: getConvList, sync) falham — needReplyCount continua "ok" e,
        # num sinal único, o estado piscaria entre lendo e sessao_caiu.
        self.sinais: dict[str, str] = {}
        self.pronto = False             # aba própria + gancho + chat: tudo feito
        self.fora_desde: float | None = None
        self.login_desde: float | None = None   # URL de login/verificação desde
        # Última resposta boa de uma rota ASSINADA (só ela prova a sessão).
        self.assinada_ok_em: float | None = None
        self.ultimo_http_em: float | None = None
        self.ultima_navegacao = float("-inf")
        self.proximo_recarregar = float("inf")
        self.proxima_conexao = 0.0
        # Perfil sumido depois de conectado (fechado aqui ou aberto em outro lugar).
        self.ja_conectou = False
        self.fechamentos_fora: list[float] = []
        self.proximo_envio = 0.0
        self.proximo_pulso = 0.0
        self.proximo_log = ctx.agora() + LOG_S
        self.falhas_seguidas = 0
        self._ultima_falha_envio: str | None = None
        self._ultima_falha_pulso: str | None = None
        # Envio: tentativas seguidas que falharam (qualquer motivo), 5xx
        # seguidos no lote da frente, quantos eventos ainda vão um a um
        # (procurando o que o DaVinci nunca grava) e o teto de eventos por
        # lote (cai à metade num 413; volta quando a fila esvazia).
        self.falhas_envio = 0
        self._5xx_seguidos = 0
        self._isolar = 0
        self._teto_lote = MAX_EVENTOS_LOTE

    # --- laço -------------------------------------------------------------------

    def run(self) -> None:
        try:
            while not self.ctx.parar.is_set():
                espera = self.passo()
                self.ctx.parar.wait(espera)
        except Exception as e:  # noqa: BLE001 — a thread não pode morrer calada
            self.log.error("leitor parou por erro inesperado: %s", resumo_erro(e))
        finally:
            self._encerrar()

    def passo(self) -> float:
        """Uma rodada: conecta se preciso, drena, decide recarga; envia e pulsa."""
        espera = DRENAR_S
        try:
            if self.drv is None:
                if self.ctx.agora() < self.proxima_conexao or not self._conectar():
                    espera = min(10.0, max(1.0, self.proxima_conexao - self.ctx.agora()))
            if self.drv is not None:
                self._ler()
                self.falhas_seguidas = 0
        except Exception as e:  # noqa: BLE001 — toda falha vira estado + religação
            self._tratar_falha(e)
        for etapa in (self._enviar, self._pulsar, self._logar):
            try:
                etapa()
            except Exception as e:  # noqa: BLE001 — um bug aqui não pode parar a loja
                self.log.error("falha em %s: %s", etapa.__name__, resumo_erro(e))
        return espera

    # --- estado e pulso ------------------------------------------------------------

    def _mudar_estado(self, estado: str, detalhe: str | None = None) -> None:
        if estado != self.estado:
            self.log.info(
                "estado: %s → %s%s", self.estado, estado, f" ({detalhe})" if detalhe else ""
            )
            self._pulso_ja = True     # mudou: o DaVinci fica sabendo já, não em 60 s
        self.estado, self.detalhe = estado, detalhe

    def _resumo(self) -> str:
        ultimo = (
            f"{int(self.ctx.agora() - self.ultimo_http_em)}s"
            if self.ultimo_http_em is not None
            else "—"
        )
        return (
            f"capturados {self.conta['capturados']}, enviados {self.conta['enviados']}, "
            f"pendentes {len(self.pendentes)}, última resposta do chat há {ultimo}"
            + (f", VETADAS {sum(self.vetadas.values())}" if self.vetadas else "")
        )

    def _envio_parado(self) -> bool:
        return bool(self.pendentes) and self.falhas_envio >= FALHAS_PARA_AVISAR

    def _pulsar(self) -> None:
        agora = self.ctx.agora()
        if not self._pulso_ja and agora < self.proximo_pulso:
            return
        self._pulso_ja = False
        self.proximo_pulso = agora + PULSO_S
        self._postar_pulso()

    def _postar_pulso(self) -> bool:
        """Manda o pulso agora; True = o DaVinci aceitou."""
        if self.ctx.davinci is None:
            return False
        estado = self.estado
        detalhe = self._resumo() if estado == "lendo" else self.detalhe
        if estado == "lendo" and self._envio_parado():
            # A página está sendo lida, mas nada ENTRA no DaVinci: "lendo"
            # deixaria a loja saudável na barra enquanto a fila cresce.
            estado = "erro"
            detalhe = (
                f"envio ao DaVinci parado: {self._ultima_falha_envio or 'falhou'} "
                f"({len(self.pendentes)} pendentes)"
            )
        corpo = {
            "perfil_id": self.loja.perfil_id,
            "plataforma": self.loja.plataforma,
            "loja": self.loja.loja,
            "estado": estado,
            "url": limpar_url(self.url_aba or self.loja.url),
            "detalhe": detalhe[:300] if detalhe else None,
            "versao": VERSAO,
        }
        try:
            self.ctx.davinci.post("/api/atendimento/robo/pulso", corpo, timeout=20)
        except (DaVinciFora, DaVinciRecusou) as e:
            if str(e) != self._ultima_falha_pulso:
                self.log.warning("pulso não chegou ao DaVinci: %s", e)
                self._ultima_falha_pulso = str(e)
            return False
        self._ultima_falha_pulso = None
        return True

    def _logar(self) -> None:
        agora = self.ctx.agora()
        if agora < self.proximo_log:
            return
        self.proximo_log = agora + LOG_S
        rotas = ", ".join(f"{k} {v}" for k, v in self.rotas.most_common(8)) or "nada"
        extras = ", ".join(
            f"{k} {self.conta[k]}"
            for k in ("repetidos", "recusados", "rejeitados", "descartados",
                      "pagina_perdidos", "pagina_grandes", "pagina_erros", "pagina_jsonp",
                      "vetadas", "erros_davinci", "seco")
            if self.conta[k]
        )
        self.log.info(
            "%s | último minuto: %s | total: capturados %d, enviados %d, gravadas %d, "
            "conversas %d, ignorados %d, pendentes %d%s",
            self.estado, rotas, self.conta["capturados"], self.conta["enviados"],
            self.conta["gravadas"], self.conta["conversas"], self.conta["ignorados"],
            len(self.pendentes), f" | {extras}" if extras else "",
        )
        self.rotas.clear()

    # --- conexão --------------------------------------------------------------------

    def _conectar(self) -> bool:
        """AdsPower → Selenium → aba própria → gancho → chat. False = esperar."""
        self._mudar_estado("iniciando", "conectando ao perfil do AdsPower")
        self._pulsar()      # abrir perfil pode levar minuto: o DaVinci já sabe que começou
        perfil = self.loja.perfil_id
        try:
            info = self.ctx.adspower.ativo(perfil)
            if info is None:
                agora = self.ctx.agora()
                if self.ja_conectou:
                    self.ja_conectou = False
                    self.fechamentos_fora.append(agora)
                espera = espera_reabrir(self.fechamentos_fora, agora)
                if espera is None:
                    self._mudar_estado(
                        "erro",
                        "perfil fechado fora do robô várias vezes: não reabro sozinho "
                        "(abra o perfil no AdsPower deste Mac que o robô volta a ler)",
                    )
                    self.proxima_conexao = agora + ESPERA_PERFIL_S
                    return False
                if espera > 0:
                    self._mudar_estado(
                        "erro",
                        "perfil fechado ou em uso em outro computador: tento de novo em "
                        f"{int(espera // 60) + 1} min, sem forçar",
                    )
                    # Checa antes do prazo: se alguém reabrir o perfil aqui, conecta já.
                    self.proxima_conexao = agora + min(espera, ESPERA_PERFIL_S)
                    return False
                if not self.ctx.abrir_perfil:
                    self._mudar_estado("erro", "perfil fechado no AdsPower (--sem-abrir-perfil)")
                    self.proxima_conexao = self.ctx.agora() + ESPERA_PERFIL_S
                    return False
                self.log.info("perfil %s fechado: abrindo pelo AdsPower", perfil)
                self.ctx.adspower.abrir(perfil)
                info = self._esperar_perfil()
                if info is None:
                    self._mudar_estado("erro", "o AdsPower não terminou de abrir o perfil")
                    self.proxima_conexao = self.ctx.agora() + ESPERA_PERFIL_S
                    return False
        except AdsPowerFora:
            self._mudar_estado("erro", "AdsPower não responde (o app e a API local estão ligados?)")
            self.proxima_conexao = self.ctx.agora() + ESPERA_PERFIL_S
            return False
        except AdsPowerErro as e:
            self._mudar_estado("erro", f"AdsPower: {e}")
            self.proxima_conexao = self.ctx.agora() + ESPERA_PERFIL_S
            return False
        self.pronto = False
        self.drv = self.ctx.criar_driver(info)
        self._abrir_aba()
        self._instalar_gancho()
        self._ir_ao_chat("início")
        self.pronto = True
        self.ja_conectou = True
        self.log.info("conectado ao perfil %s (aba própria pronta)", perfil)
        return True

    def _esperar_perfil(self) -> dict | None:
        # O AdsPower responde ao start antes de o navegador estar de pé.
        fim = self.ctx.agora() + ESPERA_PERFIL_SUBIR_S
        while not self.ctx.parar.is_set():
            info = self.ctx.adspower.ativo(self.loja.perfil_id)
            if info is not None and info.get("webdriver"):
                return info
            if self.ctx.agora() >= fim:
                return None
            self.ctx.parar.wait(3)
        return None

    def _abrir_aba(self) -> None:
        """Reaproveita a aba do robô (handle salvo) ou abre UMA nova."""
        abas = list(self.drv.window_handles)
        salvo = self.ctx.estado.handle(self.loja.perfil_id)
        if salvo and salvo in abas:
            self.drv.switch_to.window(salvo)
            self.handle = salvo
            return
        if abas:
            # O chromedriver preso ao navegador pode estar "olhando" um alvo
            # que já não existe; a aba nova nasce a partir de uma que existe.
            # (Trocar de alvo aqui não muda a aba que a equipe vê.)
            self.drv.switch_to.window(abas[0])
        self.drv.switch_to.new_window("tab")
        self.handle = self.drv.current_window_handle
        self.ctx.estado.guardar(self.loja.perfil_id, self.handle)
        self.conta["abas_abertas"] += 1

    def _na_nossa_aba(self) -> None:
        """Trava antes de registrar gancho ou navegar: o comando CDP e o get()
        vão para a aba ATUAL da sessão — que tem de ser a do robô, nunca uma
        da equipe. Aba sumida → NoSuchWindowException → "aba" (reabre)."""
        if not self.handle:
            raise RuntimeError("o robô ainda não tem aba própria neste perfil")
        if self.drv.current_window_handle != self.handle:
            self.drv.switch_to.window(self.handle)

    def _instalar_gancho(self) -> None:
        """Registra o gancho NA NOSSA ABA (o comando vai ao alvo atual, que é
        ela). Vale para os próximos documentos da aba enquanto esta sessão
        viver — por isso sempre seguido de uma navegação."""
        self._na_nossa_aba()
        self.nome_gancho = "__" + secrets.token_hex(5)
        self.drv.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": montar_gancho(self.loja.plataforma, self.nome_gancho)},
        )

    def _ir_ao_chat(self, motivo: str) -> None:
        """Abre a LISTA do chat num documento NOVO (o gancho só entra em
        documento novo). about:blank antes: se a aba já estiver na mesma URL e
        só o #fragmento mudar, o navegador não recarregaria — e um refresh()
        recarregaria a URL ATUAL, que pode ser uma conversa aberta."""
        self._na_nossa_aba()
        if self.nome_gancho:
            try:
                self._drenar()        # o que já chegou não se perde na troca
            except Exception:  # noqa: BLE001, S110 — página velha: segue a navegação
                pass
        # Marca ANTES de tentar: se o get() falhar (rede fora), a próxima
        # tentativa automática respeita o NAVEGACAO_MIN_S em vez de vir em 2 s.
        self.ultima_navegacao = self.ctx.agora()
        self.drv.get("about:blank")
        try:
            self.drv.get(self.loja.url)
        except Exception as e:  # noqa: BLE001
            if tipo_de_falha(e) != "lenta":
                raise
            # Carga lenta (tempo esgotado): o documento já existe, o gancho
            # também. Segue — a leitura diz onde a aba está.
        agora = self.ctx.agora()
        folga = random.uniform(0.9, 1.1)  # noqa: S311 — espalhar recargas, não segurança
        self.proximo_recarregar = agora + RECARREGAR_S[self.loja.plataforma] * folga
        self.ultimo_http_em = agora
        self.fora_desde = None
        self.sinais.clear()
        self._pagina = {"perdidos": 0, "grandes": 0, "erros": 0, "jsonp": 0}
        self._vetadas_doc = {}
        self.conversa_aberta_em = None
        self.conta["navegacoes"] += 1
        self.log.info("aba no chat (%s)", motivo)
        if not self._esperar_gancho():
            raise RuntimeError("o gancho não apareceu na aba depois de navegar")

    def _esperar_gancho(self) -> bool:
        fim = self.ctx.agora() + ESPERA_GANCHO_S
        while True:
            if self.drv.execute_script(_TEM_GANCHO_JS, self.nome_gancho):
                return True
            if self.ctx.agora() >= fim or self.ctx.parar.wait(1):
                return False

    def _alguem_digitando(self) -> bool:
        try:
            return bool(self.drv.execute_script(_DIGITANDO_JS))
        except Exception:  # noqa: BLE001 — na dúvida, não navega agora
            return True

    # --- leitura ----------------------------------------------------------------------

    def _drenar(self) -> dict | None:
        """Esvazia a fila da página (até 5 idas por rodada). None = sem gancho."""
        ultimo = None
        for _ in range(5):
            r = self.drv.execute_script(
                _DRENAR_JS, self.nome_gancho, MAX_EVENTOS_LOTE, MAX_DRENAR_CHARS
            )
            if not isinstance(r, dict):
                return ultimo
            ultimo = r
            self._receber(r.get("eventos") or [])
            for k in ("perdidos", "grandes", "erros", "jsonp"):
                v = r.get(k) if isinstance(r.get(k), int) else 0
                if v > self._pagina[k]:
                    self.conta[f"pagina_{k}"] += v - self._pagina[k]
                self._pagina[k] = v
            self._vigiar(r.get("vetadas"))
            if not r.get("resta"):
                break
        return ultimo

    def _receber(self, brutos: list) -> None:
        agora = self.ctx.agora()
        for bruto in brutos:
            self.conta["capturados"] += 1
            ev = preparar_evento(self.loja.plataforma, bruto)
            if ev is None:
                self.conta["recusados"] += 1
                continue
            self.rotas[rotulo_rota(ev)] += 1
            self.rotas_total[rotulo_rota(ev)] += 1
            if ev["tipo"] == "http":
                self.ultimo_http_em = agora
                sinal = classificar_corpo(self.loja.plataforma, ev["corpo"])
                rota = rotulo_rota(ev)
                if sinal in ("login", "verificacao"):
                    self.sinais[rota] = sinal
                elif sinal == "ok":
                    self.sinais.pop(rota, None)
                    if rota_assinada(self.loja.plataforma, ev["url"]):
                        self.assinada_ok_em = agora
            if not self.dedup.novo(ev):
                self.conta["repetidos"] += 1
                continue
            self.pendentes.append(ev)
        excesso = len(self.pendentes) - MAX_PENDENTES
        if excesso > 0:
            # DaVinci fora há muito tempo: perde o mais velho (a lista de
            # conversas mais nova repõe o estado quando ele voltar).
            del self.pendentes[:excesso]
            self.conta["descartados"] += excesso

    def _vigiar(self, vetadas: Any) -> None:
        """Chamadas de marcar lido/enviar que a PÁGINA fez na aba do robô.

        Só o nome da rota vai para o log. Qualquer uma é sinal de conversa
        aberta na aba do robô (alguém clicou nela): o `_ler` volta para a lista.
        """
        if not isinstance(vetadas, dict):
            return
        for url, n in vetadas.items():
            if not isinstance(url, str) or not isinstance(n, int) or isinstance(n, bool):
                continue
            novas = n - self._vetadas_doc.get(url, 0)
            self._vetadas_doc[url] = n
            if novas <= 0:
                continue
            rota = rotulo_rota({"tipo": "http", "url": url})
            self.vetadas[rota] += novas
            self.conta["vetadas"] += novas
            self.log.warning(
                "VIGIA: a página chamou %s (%d) na aba do robô — conversa aberta nela?",
                rota, novas,
            )
            if self.conversa_aberta_em is None:
                self.conversa_aberta_em = self.ctx.agora()

    def _ler(self) -> None:
        r = self._drenar()
        if r is None:
            # Documento sem o gancho na nossa aba: a sessão CDP que o
            # registrou não é mais esta (ou alguém trocou a aba de lugar).
            self.log.warning("gancho ausente na aba: registrando de novo")
            self._instalar_gancho()
            self._ir_ao_chat("gancho ausente")
            return
        href = r.get("href") if isinstance(r.get("href"), str) else None
        self.url_aba = href
        situacao = situacao_da_aba(href, self.loja.url)
        agora = self.ctx.agora()
        plat = self.loja.plataforma
        sinais = set(self.sinais.values())
        sinal = "verificacao" if "verificacao" in sinais else ("login" if sinais else None)

        if situacao in ("login", "verificacao"):
            self.login_desde = self.login_desde if self.login_desde is not None else agora
            if agora - self.login_desde < LOGIN_CONFIRMA_S:
                return      # pode ser o vaivém do login automático: espera confirmar
        else:
            self.login_desde = None

        # A conversa aberta vem ANTES da sessão caída: com a loja em "sessão
        # caiu" alguém vai mexer justamente na aba do robô — e a volta à lista
        # em 1 min tem de valer também aí (revisão 30/09).
        if situacao == "chat" and self.conversa_aberta_em is not None:
            # Conversa aberta na aba do robô: cada mensagem nova do comprador
            # seria marcada como lida PARA A EQUIPE. Dá um minuto (alguém pode
            # estar no meio de algo) e volta para a lista — nunca digitando.
            self._mudar_estado(
                "erro", "conversa aberta na aba do robô (marca como lida): voltando para a lista"
            )
            if (
                agora - self.conversa_aberta_em >= NAVEGACAO_MIN_S
                and agora - self.ultima_navegacao >= NAVEGACAO_MIN_S
                and not self._alguem_digitando()
            ):
                self._ir_ao_chat("conversa aberta na aba do robô")
            return

        if situacao in ("login", "verificacao") or (situacao == "chat" and sinal):
            motivo = situacao if situacao != "chat" else sinal
            self._mudar_estado(
                "sessao_caiu",
                "tela de login: uma pessoa precisa entrar (o robô não loga)"
                if motivo == "login"
                else "verificação/captcha: uma pessoa precisa resolver na aba do robô",
            )
            intervalo = REABRIR_LOGIN_S if motivo == "login" else RECARREGAR_S[plat]
            if agora - self.ultima_navegacao >= intervalo and not self._alguem_digitando():
                self._ir_ao_chat("conferir se a sessão voltou")
            return

        if situacao == "chat":
            self.fora_desde = None
            if (
                self.estado == "sessao_caiu"
                and (self.assinada_ok_em is None or self.assinada_ok_em < self.ultima_navegacao)
                and agora - self.ultima_navegacao < LOGIN_CONFIRMA_S
            ):
                # Reabriu o chat para ver se a sessão voltou: o chat.html
                # carrega ANTES de a página mandar para o login, e com o
                # captcha pendente as rotas SEM assinatura (needReplyCount)
                # continuam respondendo. Só volta a "lendo" com a resposta boa
                # de uma rota assinada (ou passada a folga) — senão a loja
                # piscaria "lendo" a cada recarga.
                return
            silencio = SILENCIO_MAX_S.get(plat)
            calado = (
                silencio is not None
                and self.ultimo_http_em is not None
                and agora - self.ultimo_http_em > silencio
            )
            if calado:
                self._mudar_estado(
                    "erro", f"a página não chama o chat há {int(silencio // 60)} min: recarregando"
                )
                pode = agora - self.ultima_navegacao >= NAVEGACAO_MIN_S
                if pode and not self._alguem_digitando():
                    self._ir_ao_chat("página calada")
                return
            self._mudar_estado("lendo")
            if agora >= self.proximo_recarregar and not self._alguem_digitando():
                self._ir_ao_chat("recarga periódica")
            return

        # "fora" (alguém usou a aba para outra coisa) ou "vazia" (erro de rede,
        # aba em branco): volta ao chat — já, se vazia; com folga, se fora.
        self.fora_desde = self.fora_desde if self.fora_desde is not None else agora
        self._mudar_estado("erro", "a aba do robô saiu do chat")
        limite = 0.0 if situacao == "vazia" else VOLTAR_AO_CHAT_S
        if (
            agora - self.fora_desde >= limite
            and agora - self.ultima_navegacao >= NAVEGACAO_MIN_S
            and not self._alguem_digitando()
        ):
            self._ir_ao_chat("aba fora do chat")

    # --- falhas -------------------------------------------------------------------------

    def _tratar_falha(self, exc: BaseException) -> None:
        tipo = tipo_de_falha(exc)
        self.falhas_seguidas += 1
        texto = resumo_erro(exc)
        if tipo == "aba" and self.drv is not None and self.pronto:
            self.log.warning("a aba do robô fechou: abrindo outra")
            self.ctx.estado.guardar(self.loja.perfil_id, None)
            self.handle = None
            try:
                self.pronto = False
                self._abrir_aba()
                self._instalar_gancho()
                self._ir_ao_chat("aba fechada")
                self.pronto = True
                return
            except Exception as e:  # noqa: BLE001 — não deu na mesma sessão: religa
                tipo, texto = "sessao", resumo_erro(e)
        # Falha no meio da conexão (sem aba própria + gancho + chat completos):
        # nunca seguir com essa sessão pela metade — solta e religa do zero.
        if tipo == "sessao" or self.falhas_seguidas >= 5 or not self.pronto:
            self.log.warning("sessão com o navegador perdida (%s): religando", texto)
            self._soltar_driver()
            self._mudar_estado("erro", f"sessão com o navegador caiu: {texto}")
            self.proxima_conexao = self.ctx.agora() + ESPERA_RELIGAR_S
            return
        self.log.warning("falha na leitura (%d seguida): %s", self.falhas_seguidas, texto)
        self._mudar_estado("erro", texto)

    def _soltar_driver(self) -> None:
        drv, self.drv = self.drv, None
        self.nome_gancho = None
        self.pronto = False
        if drv is not None:
            try:
                drv.quit()      # só encerra o chromedriver (sessão presa)
            except Exception:  # noqa: BLE001, S110 — já estava morto
                pass

    def _encerrar(self) -> None:
        """Saída limpa: drena, fecha a PRÓPRIA aba (ou a deixa em branco, se
        for a última do perfil — fechar a última fecharia o navegador) e
        entrega o que ficou pendente."""
        if self.drv is not None:
            try:
                if self.nome_gancho:
                    self._drenar()
                abas = list(self.drv.window_handles)
                if self.handle in abas:
                    self.drv.switch_to.window(self.handle)
                    if len(abas) > 1:
                        self.drv.close()
                        self.ctx.estado.guardar(self.loja.perfil_id, None)
                    else:
                        self.drv.get("about:blank")
            except Exception as e:  # noqa: BLE001
                self.log.warning("não consegui fechar a aba do robô: %s", resumo_erro(e))
            self._soltar_driver()
        self.proximo_envio = 0.0
        self._enviar()
        self.log.info("encerrado | %s", self._resumo())
        self.log.info(
            "rotas na execução: %s | vetadas: %s",
            ", ".join(f"{k} {v}" for k, v in self.rotas_total.most_common(12)) or "nada",
            ", ".join(f"{k} {v}" for k, v in self.vetadas.most_common()) or "nenhuma",
        )

    # --- envio ----------------------------------------------------------------------------

    def _enviar(self) -> None:
        if not self.pendentes:
            return
        if self.ctx.davinci is None:
            self.conta["seco"] += len(self.pendentes)
            self.pendentes.clear()
            return
        if self.ctx.agora() < self.proximo_envio:
            return
        while self.pendentes:
            isolando = self._isolar > 0
            n = 1 if isolando else primeiro_lote(self.pendentes, self._teto_lote)
            lote = self.pendentes[:n]
            corpo = {
                "perfil_id": self.loja.perfil_id,
                "plataforma": self.loja.plataforma,
                "loja": self.loja.loja,
                "eventos": lote,
            }
            try:
                resp = self.ctx.davinci.post("/api/atendimento/robo/eventos", corpo)
            except DaVinciRecusou as e:
                if e.status == 413 and len(lote) > 1:
                    # Grande demais para o DaVinci (ou o nginx na frente):
                    # parte ao meio e manda de novo — descartar perderia até
                    # 200 eventos por causa do tamanho.
                    self._teto_lote = max(1, len(lote) // 2)
                    continue
                if e.status in (400, 413, 422):
                    # O lote em si é o problema: reenviar daria o mesmo erro para
                    # sempre e travaria a fila. Sai da fila, conta e avisa.
                    self._descartar(len(lote), f"DaVinci rejeitou: {e}")
                    continue
                self._falha_envio(str(e), ESPERA_ENVIO_RECUSADO_S)
                return
            except DaVinciFora as e:
                if e.status is not None and isolando and self._postar_pulso():
                    # Sozinho, este evento levou 5xx — e o DaVinci aceitou um
                    # pulso logo depois: não é o DaVinci fora, é o evento.
                    self._descartar(1, f"o DaVinci nunca grava este evento ({e})")
                    self._isolar -= 1
                    continue
                if e.status is not None:
                    self._5xx_seguidos += 1
                    if self._5xx_seguidos >= TENTATIVAS_5XX and not isolando:
                        self._isolar = len(lote)
                self._falha_envio(f"DaVinci fora ({e})", ESPERA_ENVIO_FALHOU_S)
                return
            if not resposta_do_contrato(resp):
                self._falha_envio("resposta do DaVinci fora do contrato", ESPERA_ENVIO_FALHOU_S)
                return
            del self.pendentes[: len(lote)]
            if isolando:
                self._isolar -= 1
            self._5xx_seguidos = 0
            self.conta["enviados"] += len(lote)
            for k in ("gravadas", "conversas", "ignorados"):
                self.conta[k] += resp[k]
            erros = resp.get("erros")
            if isinstance(erros, int) and not isinstance(erros, bool) and erros > 0:
                # Conversas que o DaVinci não conseguiu gravar (erro de dado):
                # o lote foi aceito, mas elas ficaram de fora — à vista no log.
                self.conta["erros_davinci"] += erros
            if self._ultima_falha_envio:
                self.log.info("DaVinci voltou a aceitar eventos")
                self._ultima_falha_envio = None
            if self.falhas_envio >= FALHAS_PARA_AVISAR:
                self._pulso_ja = True       # a barra sai do "erro" já, não em 60 s
            self.falhas_envio = 0
        # Fila vazia: o próximo lote volta ao tamanho normal.
        self._teto_lote = MAX_EVENTOS_LOTE
        self._isolar = 0

    def _descartar(self, n: int, motivo: str) -> None:
        """Tira da FRENTE da fila `n` eventos que nunca vão entrar (só a rota vai ao log)."""
        rotas = Counter(rotulo_rota(ev) for ev in self.pendentes[:n])
        del self.pendentes[:n]
        self.conta["rejeitados"] += n
        self.log.warning(
            "%d evento(s) fora da fila (%s): %s", n,
            ", ".join(f"{k} {v}" for k, v in rotas.most_common(5)), motivo,
        )

    def _falha_envio(self, motivo: str, espera: float) -> None:
        self.proximo_envio = self.ctx.agora() + espera
        self.falhas_envio += 1
        if self.falhas_envio == FALHAS_PARA_AVISAR:
            self._pulso_ja = True           # a barra mostra o "erro" já
        if motivo != self._ultima_falha_envio:
            self.log.warning(
                "envio parado: %s (%d pendentes; tento de novo em %d s)",
                motivo, len(self.pendentes), int(espera),
            )
            self._ultima_falha_envio = motivo


# ============================================================================
# Principal
# ============================================================================


class _LojaPadrao(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "loja"):
            record.loja = "robô"
        return True


def _ler_token() -> str:
    token = os.environ.get("DAVINCI_ROBO_TOKEN", "").strip()
    arquivo = os.environ.get("DAVINCI_ROBO_TOKEN_ARQUIVO", "").strip()
    if not token and arquivo:
        try:
            token = Path(arquivo).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise SystemExit(
                f"não consegui ler DAVINCI_ROBO_TOKEN_ARQUIVO: {type(e).__name__}"
            ) from e
    return token


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Robô de leitura do chat da Temu/AliExpress (AdsPower) para o DaVinci."
    )
    ap.add_argument("--lojas", help="só estas (nome da loja ou perfil_id, separados por vírgula)")
    ap.add_argument("--duracao", type=float, help="segundos até parar sozinho (teste)")
    ap.add_argument("--seco", action="store_true", help="não manda nada ao DaVinci; só conta")
    ap.add_argument(
        "--sem-abrir-perfil", action="store_true", help="não abre perfil fechado no AdsPower"
    )
    ap.add_argument("--config", type=Path, default=LOJAS_PADRAO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    manipulador = logging.StreamHandler()
    manipulador.addFilter(_LojaPadrao())
    manipulador.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(loja)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logging.basicConfig(level=logging.INFO, handlers=[manipulador], force=True)

    try:
        lojas = carregar_lojas(a.config, a.lojas)
    except (OSError, ValueError) as e:
        raise SystemExit(f"lojas: {e}") from e
    try:
        import selenium  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "falta o selenium: rode com `uv run --no-project --with selenium python "
            "scripts/atendimento_robo_adspower.py`"
        ) from e

    davinci = None
    if not a.seco:
        token = _ler_token()
        if not token:
            raise SystemExit(
                "falta DAVINCI_ROBO_TOKEN (ou DAVINCI_ROBO_TOKEN_ARQUIVO) — o mesmo "
                "ATENDIMENTO_ROBO_TOKEN do DaVinci. Para só testar a leitura: --seco"
            )
        try:
            davinci = DaVinci(os.environ.get("DAVINCI_ROBO_URL", "http://127.0.0.1:8011"), token)
        except ValueError as e:
            raise SystemExit(str(e)) from e

    ctx = Contexto(
        adspower=AdsPower(
            os.environ.get("ADSPOWER_URL", "http://127.0.0.1:50325"),
            os.environ.get("ADSPOWER_API_KEY", ""),
        ),
        davinci=davinci,
        abrir_perfil=not a.sem_abrir_perfil,
        estado=EstadoAbas(
            Path(os.environ.get("DAVINCI_ROBO_ESTADO") or ESTADO_PADRAO).expanduser()
        ),
    )

    def _parar(sinal, _frame):
        log.info("sinal %s: encerrando", signal.Signals(sinal).name)
        ctx.parar.set()

    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)

    log.info(
        "%s | %d loja(s): %s | %s%s",
        VERSAO,
        len(lojas),
        ", ".join(f"{x.rotulo}={x.perfil_id}" for x in lojas),
        "SECO (nada vai ao DaVinci)" if davinci is None else f"DaVinci em {davinci.base}",
        " | não abre perfil fechado" if a.sem_abrir_perfil else "",
    )
    fim = time.monotonic() + a.duracao if a.duracao else None
    leitores = [Leitor(x, ctx) for x in lojas]
    for leitor in leitores:
        if ctx.parar.is_set():
            break
        leitor.start()
        ctx.parar.wait(1.5)     # espaça as primeiras chamadas ao AdsPower

    while not ctx.parar.wait(1):
        if fim is not None and time.monotonic() >= fim:
            log.info("--duracao %.0f s: encerrando", a.duracao)
            ctx.parar.set()
    for leitor in leitores:
        if leitor.is_alive():
            leitor.join(timeout=45)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
