"""Carrinho abandonado dos sites Charlots e Uranyx no atendimento (RF9, 02/10/2026).

O desenho comum (base) e a rodada, a gravação, o fechamento e o cartão da
tela (frente A). O contrato com o site é o de baixo; o LEIA-ME do pacote do
site repete o mesmo.

## O que é

Os sites são atacado: o carrinho é do LOJISTA LOGADO (`adm_carrinho` no
banco do site, uma linha por produto + cor, com `atualizado_em`) e termina
"pelo WhatsApp" (o site monta a mensagem; o lojista confirma que enviou e o
site tira do carrinho o que foi nela — `Carrinho::retirarEnviados`). O
visitante sem login fica só na sessão: fora do escopo (sem contato).

## O contrato com o site (rota de LEITURA no site, servidor a servidor)

    GET {URL do site}/api/davinci/carrinhos?horas=24&dias=30&desde=<ISO UTC>
    Authorization: Bearer <o token do site>
    Accept: application/json

  • O token é o MESMO que o site já usa para chamar o DaVinci (o
    `DAVINCI_ESTOQUE_TOKEN` do .env do site = a entrada "token:site" de
    `SITES_ESTOQUE_TOKENS` no .env do DaVinci). O DaVinci acha o token do
    site invertendo esse mapa (`token_do_site`) — nunca o imprime.
  • Fecha por padrão: sem token no .env do site → 404 (a rota nem existe);
    header ausente, esquema que não é Bearer ou token diferente → 401
    (`hash_equals`). Nada do request escolhe o site: o site é ele mesmo.
  • Hostinger (Apache/LiteSpeed): o site lê `HTTP_AUTHORIZATION` ou
    `REDIRECT_HTTP_AUTHORIZATION` (o .htaccess passa o header com
    `RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]`).
  • Parâmetros (todos opcionais; inválido → 422 `{"erro": "parametro_invalido"}`):
      horas  1–720, padrão 24: carrinho cujo MAX(adm_carrinho.atualizado_em)
             é anterior a agora − horas ("parado");
      dias   1–90, padrão 30: e posterior a agora − dias (o muito velho não
             volta toda rodada);
      desde  ISO 8601 UTC, padrão agora − 8 dias, no máximo 31 dias atrás:
             os EVENTOS com `criado_em >= desde`.
  • Resposta 200, `Content-Type: application/json`, `Cache-Control: no-store`.
    Datas SEMPRE em UTC, ISO 8601 com "Z" (o site converte do fuso do MySQL:
    `UNIX_TIMESTAMP(col)` → `gmdate('Y-m-d\\TH:i:s\\Z')`):

    {
      "site": "charlots",
      "gerado_em": "2026-10-02T12:00:00Z",
      "horas": 24,
      "truncado": false,
      "carrinhos": [
        {
          "lojista": {
            "id": 123,
            "nome": "Fulana", "empresa": "Loja X",
            "email": "x@y.com", "telefone": "11999999999",
            "cidade": "São Paulo", "estado": "SP",
            "status": "aprovado",
            "cnpj": null
          },
          "parado_desde": "2026-09-30T14:03:00Z",
          "quantidade_total": 3,
          "itens": [
            {
              "produto_id": 45,
              "titulo": "Mala ABS M2",
              "cor": "M2|Preto",
              "cor_rotulo": "M2 Preto",
              "quantidade": 3,
              "skus": ["b1001.pi"],
              "url": "https://charlots.com.br/produto/mala-abs",
              "imagem": "https://charlots.com.br/uploads/mala.webp",
              "preco": 199.9
            }
          ]
        }
      ],
      "eventos": [
        {
          "id": 987,
          "tipo": "finalizado",
          "lojista_id": 123,
          "criado_em": "2026-10-01T09:12:00Z",
          "itens": [{"produto_id": 45, "cor": "M2|Preto", "quantidade": 3, "skus": ["b1001.pi"]}],
          "restantes": 0
        }
      ],
      "ativos": [{"lojista_id": 124, "atualizado_em": "2026-10-02T10:40:00Z"}]
    }

    `skus` = os itens de `adm_produtos.skus` da escolha (a mesma regra do
    estoque do site: SKU exato, kit com "+" é um SKU só, ou "base.*" = os
    lotes de venda), em minúsculas; [] quando o produto não tem o mapa. O
    DaVinci casa pelo SKU do recorte do site (o mesmo do GET
    /api/sites/estoque) e mostra o estoque ATUAL (`painel.saldo_do_item`).
    `preco` = o preço de atacado do item (o que o lojista aprovado vê), ou
    null. `tipo` do evento: `finalizado` (confirmou o envio pelo WhatsApp —
    o que foi na mensagem) ou `esvaziado` (apagou o carrinho). `ativos` =
    só id e hora de quem mexeu no carrinho há menos de `horas` (o que sumiu
    da lista de parados porque o lojista voltou a mexer não foi abandonado).
    O site ecoa também `dias` e `desde`, e os itens dos eventos trazem
    `titulo`/`cor_rotulo` (ignorados aqui).
  • Teto: 500 carrinhos e 1.000 eventos por resposta (os mais recentes);
    passou, `truncado: true`.

O site GRAVA o evento (tabela nova `adm_carrinho_eventos`: id, cliente_id,
tipo, itens JSON, restantes, criado_em) no "finalizado" e no "esvaziar" do
lojista logado, em vez de só apagar — é assim que o DaVinci sabe
"recuperado".

## O que o DaVinci faz

  • Cron `atendimento_carrinhos` (worker, :14/:44, `timeout=600`), só com
    `atendimento_leitura_ativa` E `atendimento_carrinhos_ativa`; trava no
    Redis (`atendimento:carrinhos:rodada`). Um GET por site por rodada
    (`desde` = os mesmos 30 dias da janela dos carrinhos: o evento que
    explica um carrinho listado está sempre na resposta). Erro de um site
    não para o outro; nada do site vai para o log (só contagens e códigos).
  • Canal do site: `canais_externos.garantir_canal(externo_ref="site:<site>",
    plataforma="site", canal="carrinho", nome="Charlots")`. Status do canal:
      ok           — leu (truncado = ok com o aviso em `ultimo_erro`);
      sem_escopo   — 401: o token do site não confere, ou (`motivo:
                     sem_token`) a hospedagem cortou o Authorization;
      sem_endpoint — 404 que não é do site (HTML): a rota ainda não está
                     publicada — publicar o pacote do carrinho;
      desligado    — o DaVinci não tem o token do site (nada é chamado), ou
                     o site respondeu 404 `nao_encontrado` (rota desligada lá:
                     sem token no .env do site ou davinci_carrinhos_ativo=0);
      erro         — o resto (403 do WAF, 429 por token errado, 503 sem o SQL
                     do pacote, timeout, 5xx, redirecionamento, resposta fora
                     do contrato, endereço inválido).
  • Uma conversa por lojista por site (`externo_id = "lojista:<id>"`,
    `comprador_id = <id>`, `comprador_nome = empresa ou nome`, `conta =
    "Charlots"`). Cada carrinho parado = um EPISÓDIO em
    `atendimento_carrinhos` (um ABERTO por lojista, índice único parcial) +
    uma mensagem do lojista na conversa (`externo_id = "carrinho:<id>"`,
    autor cliente, na hora em que o DaVinci viu): a vez é da loja enquanto
    o carrinho estiver aberto (`CHAVE_VEZ_DA_LOJA`), com o SLA do carrinho
    (24 h). O fim vira mensagem do sistema (`"carrinho:<id>:fim"`) e a
    conversa sai da fila.
  • Episódio novo só quando o lojista MEXEU depois do último desfecho: o
    `parado_desde` lido tem de ser posterior ao do último episódio, à
    recuperação dele e a todo evento do lojista na resposta. Sem isso, o
    carrinho que passou de 7 dias (ou foi marcado como resolvido) reabriria
    toda semana, e a sobra de um pedido parcial viraria "abandono".
  • Desfecho:
      recuperado     — evento `finalizado` posterior ao `parado_desde` (o
                       evento atrasado — site fora do ar — ainda corrige o
                       "não recuperado" por prazo, se cair dentro dos 7 dias);
      não recuperado — evento `esvaziado` posterior ao `parado_desde`, ou 7
                       dias desde a ÚLTIMA MEXIDA do lojista (`ultima_mexida`:
                       o `parado_desde` ou o `dados.mexido_em`, o mais novo —
                       Eduardo, 02/10/2026; o prazo corre mesmo com o site
                       fora do ar). Carrinho que já chega parado há mais de 7
                       dias não abre episódio (já seria "não recuperado");
      resolvido      — "Marcar como resolvido" na tela.
    O carrinho que SOME da lista sem evento (o lojista voltou a mexer, ou
    saiu da janela de 30 dias) fica aberto com `dados.fora_da_lista_desde`
    (e `dados.mexido_em` quando o site o lista em `ativos`): o evento ou o
    prazo decidem. Sempre `etiqueta.recalcular_etiqueta(...)`
    depois (CARRINHO → PÓS-VENDA no recuperado; → PRÉ-VENDA nos outros).
  • Nada é mandado ao lojista (sem lembrete por Zap/e-mail por enquanto).

## Para a tela

`carrinho_da_conversa`, `estoque_dos_itens` (o estoque ATUAL do DaVinci pela
mesma regra do site: SKU exato ou a soma dos lotes de venda de "base.*";
SKU que o DaVinci não tem = desconhecido, nunca zero), `taxa_do_site`,
`para_tela` e `marcar_resolvido` — usados por `routers/atendimento_carrinhos.py`.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoCarrinho, AtendimentoConversa, User
from app.redis_client import redis
from app.services.atendimento import canais_externos, etiqueta, gravar, painel
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_SISTEMA,
    CANAL_CARRINHO,
    CARRINHO_ABERTO,
    CARRINHO_DIAS_RECUPERACAO,
    CARRINHO_NAO_RECUPERADO,
    CARRINHO_RECUPERADO,
    CARRINHO_RESOLVIDO,
    CHAVE_VEZ_DA_LOJA,
    FIM_CARRINHO_ESVAZIADO,
    FIM_CARRINHO_FINALIZADO,
    FIM_CARRINHO_PRAZO,
    FIM_CARRINHO_RESOLVIDO,
    NOME_SITE,
    PLATAFORMA_SITE,
    ROTA_CARRINHOS_DO_SITE,
    SITES,
    URL_SITE,
)

logger = structlog.get_logger()

# ── A rodada ──────────────────────────────────────────────────────────────
# A trava da rodada no Redis (uma por vez). Menor que o `timeout=600` do
# cron: se o processo morrer, a próxima roda.
CHAVE_TRAVA = "atendimento:carrinhos:rodada"
TRAVA_TTL_S = 9 * 60
# A chamada ao site (Hostinger): conexão curta, leitura folgada.
TEMPO_CONEXAO_S = 5.0
TEMPO_LEITURA_S = 25.0
# A resposta inteira (500 carrinhos + 1.000 eventos cabem em ~3 MB).
RESPOSTA_MAXIMA = 8 * 1024 * 1024
USER_AGENT = "DaVinci-Atendimento/1.0 (carrinho)"
# A janela que o DaVinci pede (`dias`) e os eventos (`desde`): a mesma, para o
# evento que explica um carrinho listado estar sempre na resposta (o site
# aceita até 31 dias de eventos).
DIAS_JANELA = 30
HORAS_MIN, HORAS_MAX = 1, 720

# ── Status do canal (aba Lojas) ───────────────────────────────────────────
STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
# A rota ainda não está no site (404): o pacote não foi publicado.
STATUS_SEM_ENDPOINT = "sem_endpoint"
STATUS_DESLIGADO = "desligado"
STATUS_ERRO = "erro"
# Os textos de `ultimo_erro` (operação; nunca o token, nunca dado de lojista).
ERRO_SITE_SEM_ACESSO = (
    "sem o token do site em SITES_ESTOQUE_TOKENS (o mesmo do estoque): o DaVinci não chama o site"
)
ERRO_URL = "endereço do site inválido em ATENDIMENTO_SITES_URLS (use https://)"
ERRO_SEM_ENDPOINT = (
    f"HTTP 404: a rota {ROTA_CARRINHOS_DO_SITE} ainda não está publicada no site "
    "(publicar o pacote do carrinho na Hostinger)"
)
# As respostas de erro do próprio site (CarrinhosDavinci.php, pacote de
# 02/10): o corpo é um JSON pequeno {"erro", "motivo"?} — só o código é lido,
# e só para escolher o texto abaixo (nada do corpo vai para log nem canal).
ERRO_ROTA_DESLIGADA = (
    "HTTP 404 nao_encontrado: a rota de carrinhos está no site, mas desligada lá "
    "(sem DAVINCI_ESTOQUE_TOKEN no .env do site, ou davinci_carrinhos_ativo=0 em "
    "adm_configuracoes)"
)
ERRO_401_NAO_CONFERE = (
    "HTTP 401 nao_autorizado: o token não confere (o DAVINCI_ESTOQUE_TOKEN do .env do "
    "site e a entrada do site em SITES_ESTOQUE_TOKENS do DaVinci são diferentes)"
)
ERRO_SEM_CABECALHO = (
    "HTTP 401 sem_token: o cabeçalho Authorization não chegou ao PHP do site (a "
    "hospedagem cortou): aplicar a linha do .htaccess descrita no LEIA-ME do pacote"
)
ERRO_BARRADO = (
    "HTTP 403: barrado antes do site (firewall/WAF da Hostinger?): liberar "
    f"{ROTA_CARRINHOS_DO_SITE} para o IP do servidor do DaVinci"
)
ERRO_MUITAS_TENTATIVAS = (
    "HTTP 429 muitas_tentativas: o site fechou a rota para o IP do DaVinci por excesso "
    "de token errado (abre sozinha em 15 min; conferir o token)"
)
ERRO_FALTA_SQL = (
    "HTTP 503 falta_atualizacao_sql: falta rodar no banco do site o SQL do pacote "
    "(tabela adm_carrinho_eventos)"
)
ERRO_BANCO_DO_SITE = "HTTP 503 banco_indisponivel: o banco do site não respondeu"
ERRO_INTERNO_DO_SITE = "HTTP 500 erro_interno: erro no PHP do site (ver o error_log do site)"
# O corpo de erro que se lê (o do site tem dezenas de bytes; a página de
# erro da hospedagem pode ser grande — não interessa).
CORPO_DE_ERRO_MAX = 4096
_RX_CODIGO = re.compile(r"[a-z_]{1,40}")
AVISO_TRUNCADO = (
    "a resposta do site veio truncada (teto de 500 carrinhos / 1.000 eventos): "
    "os mais antigos ficaram de fora"
)

# ── O que se aceita do site (lista branca; o resto é ignorado) ───────────
MAX_CARRINHOS = 500
MAX_EVENTOS = 1000
MAX_ATIVOS = 1000
MAX_ITENS = 200
MAX_SKUS_ITEM = 20
_RX_SKU = re.compile(r"[a-z0-9][a-z0-9.+_-]{0,99}")
# "base.*" = os lotes de venda (a mesma regra do `Estoque::RX_BASE` do site).
_RX_BASE = re.compile(r"[a-z0-9][a-z0-9._-]{0,97}\.\*")
_RX_LOJISTA = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RX_CONTROLE = re.compile(r"[\x00-\x1f\x7f]+")
_CAMPOS_LOJISTA = {
    "nome": 160,
    "empresa": 160,
    "email": 191,
    "telefone": 40,
    "cidade": 120,
    "estado": 40,
    "status": 32,
    "cnpj": 32,
}
TIPOS_EVENTO = ("finalizado", "esvaziado")

# ── A tela ────────────────────────────────────────────────────────────────
MAX_ITENS_TELA = 80
MAX_ANTERIORES = 10
DIAS_TAXA = 30
# O primeiro lote de venda: o nome que `saldo_do_item` precisa para listar os
# irmãos de um "base.*".
_LOTE_REFERENCIA = "ci"
ROTULO_SITUACAO = {
    CARRINHO_ABERTO: "Carrinho parado",
    CARRINHO_RECUPERADO: "Recuperado",
    CARRINHO_NAO_RECUPERADO: "Não recuperado",
    CARRINHO_RESOLVIDO: "Resolvido",
}
ROTULO_FIM = {
    FIM_CARRINHO_FINALIZADO: "o lojista finalizou pelo WhatsApp",
    FIM_CARRINHO_ESVAZIADO: "o lojista esvaziou o carrinho",
    FIM_CARRINHO_PRAZO: f"{CARRINHO_DIAS_RECUPERACAO} dias sem finalizar",
    FIM_CARRINHO_RESOLVIDO: "marcado como resolvido no DaVinci",
}
AVISO_SEM_ENVIO = (
    "Nada é mandado ao lojista daqui (sem lembrete por WhatsApp ou e-mail por enquanto)."
)

# O acontecimento entre parênteses na linha do tempo da etiqueta.
MOTIVO_ETIQUETA_LEITURA = "leitura do carrinho do site"
MOTIVO_ETIQUETA_RECUPERADO = "o lojista finalizou pelo WhatsApp"
MOTIVO_ETIQUETA_ESVAZIADO = "o lojista esvaziou o carrinho"
MOTIVO_ETIQUETA_PRAZO = f"{CARRINHO_DIAS_RECUPERACAO} dias sem finalizar"
MOTIVO_ETIQUETA_RESOLVIDO = "carrinho marcado como resolvido"

_FUSO = ZoneInfo("America/Sao_Paulo")


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def _iso_z(quando: datetime) -> str:
    """O formato que o site aceita em `desde`: 2026-10-02T12:00:00Z."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(quando: datetime | None) -> str:
    """'30/09 14:03' no fuso de São Paulo (texto das mensagens da conversa)."""
    if quando is None:
        return "—"
    return quando.astimezone(_FUSO).strftime("%d/%m %H:%M")


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


# ── Endereço e token do site ──────────────────────────────────────────────


def _url_valida(url: str) -> str | None:
    """https em qualquer host; http só no próprio computador (o ensaio local)."""
    try:
        partes = urlsplit(url.strip())
    except ValueError:
        return None
    host = (partes.hostname or "").lower()
    if partes.scheme == "https" and host:
        return url.strip().rstrip("/")
    if partes.scheme == "http" and host in ("localhost", "127.0.0.1"):
        return url.strip().rstrip("/")
    return None


def url_do_site(site: str) -> str | None:
    """O endereço do site, sem barra no fim: `atendimento_sites_urls` ou o de produção.

    `atendimento_sites_urls` = "charlots=https://...,uranyx=https://..." (o
    ensaio local aponta para o PHP no localhost). Endereço inválido → None
    (o canal fica com erro, nada é chamado).
    """
    nome = (site or "").strip().lower()
    if nome not in SITES:
        return None
    for parte in (get_settings().atendimento_sites_urls or "").split(","):
        chave, _, url = parte.partition("=")
        if chave.strip().lower() == nome and url.strip():
            return _url_valida(url)
    return _url_valida(URL_SITE.get(nome, ""))


def rota_do_site(site: str) -> str | None:
    base = url_do_site(site)
    return f"{base}{ROTA_CARRINHOS_DO_SITE}" if base else None


def token_do_site(site: str) -> str | None:
    """O token do site em `sites_estoque_tokens` ("token:site,token:site"). NUNCA logar.

    O primeiro token do site vale (o mesmo que o site manda no GET
    /api/sites/estoque). Sem token → None: o site não é chamado.
    """
    nome = (site or "").strip().lower()
    for parte in (get_settings().sites_estoque_tokens or "").split(","):
        token, _, dono = parte.partition(":")
        if token.strip() and dono.strip().lower() == nome:
            return token.strip()
    return None


def mesmo_token(a: str | None, b: str | None) -> bool:
    """Comparação em tempo constante (para os testes do contrato e quem precisar)."""
    if not a or not b:
        return False
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _horas() -> int:
    try:
        horas = int(get_settings().atendimento_carrinho_horas)
    except (TypeError, ValueError):
        horas = 24
    return max(HORAS_MIN, min(HORAS_MAX, horas))


# ── O que veio do site (lista branca) ─────────────────────────────────────


def _texto(bruto: Any, limite: int) -> str | None:
    """Texto de uma linha só, sem caractere de controle, cortado; vazio → None."""
    if isinstance(bruto, bool) or bruto is None:
        return None
    if isinstance(bruto, int | float):
        bruto = str(bruto)
    if not isinstance(bruto, str):
        return None
    limpo = " ".join(_RX_CONTROLE.sub(" ", bruto).split())
    return limpo[:limite] or None


def _inteiro(bruto: Any, *, minimo: int = 0, maximo: int = 1_000_000) -> int | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        valor = bruto
    elif isinstance(bruto, float) and bruto.is_integer():
        valor = int(bruto)
    elif isinstance(bruto, str) and bruto.strip().lstrip("-").isdigit():
        valor = int(bruto.strip())
    else:
        return None
    return valor if minimo <= valor <= maximo else None


def _preco(bruto: Any) -> float | None:
    if isinstance(bruto, bool) or bruto is None:
        return None
    try:
        valor = float(bruto)
    except (TypeError, ValueError):
        return None
    if valor != valor or valor < 0 or valor >= 10_000_000:  # NaN, negativo, absurdo
        return None
    return round(valor, 2)


def _id_externo(bruto: Any) -> str | None:
    texto = _texto(bruto, 64)
    return texto if texto and _RX_LOJISTA.fullmatch(texto) else None


def _data(bruto: Any) -> datetime | None:
    """ISO 8601 do site ("…Z" ou com fuso); sem fuso = UTC; ilegível → None."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    try:
        quando = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(quando)


def url_publica(bruto: Any) -> str | None:
    """Endereço que a tela pode usar em link/imagem: https; http só no localhost."""
    texto = _texto(bruto, 1000)
    if not texto or any(c in texto for c in " \"'<>\\`"):
        return None
    try:
        partes = urlsplit(texto)
    except ValueError:
        return None
    host = (partes.hostname or "").lower()
    if partes.scheme == "https" and host:
        return texto
    if partes.scheme == "http" and host in ("localhost", "127.0.0.1"):
        return texto
    return None


def sku_valido(sku: str) -> bool:
    return bool(_RX_SKU.fullmatch(sku) or _RX_BASE.fullmatch(sku))


def _skus(bruto: Any) -> list[str]:
    """Os SKUs da escolha, em minúsculas, sem repetição; item torto some."""
    if not isinstance(bruto, list):
        return []
    saida: list[str] = []
    for s in bruto:
        if not isinstance(s, str):
            continue
        limpo = s.strip().lower()
        if sku_valido(limpo) and limpo not in saida:
            saida.append(limpo)
        if len(saida) >= MAX_SKUS_ITEM:
            break
    return saida


def lojista_do_site(bruto: Any) -> tuple[str, dict] | None:
    """(id, retrato) do lojista — só os campos do contrato; sem id válido → None."""
    if not isinstance(bruto, dict):
        return None
    lojista_id = _id_externo(bruto.get("id"))
    if lojista_id is None:
        return None
    retrato = {campo: _texto(bruto.get(campo), limite) for campo, limite in _CAMPOS_LOJISTA.items()}
    if retrato["email"] and "@" not in retrato["email"]:
        retrato["email"] = None
    return lojista_id, retrato


def item_do_site(bruto: Any) -> dict | None:
    """Um item do carrinho no formato guardado; sem quantidade válida → None."""
    if not isinstance(bruto, dict):
        return None
    quantidade = _inteiro(bruto.get("quantidade"), minimo=1, maximo=100_000)
    if quantidade is None:
        return None
    return {
        "produto_id": _id_externo(bruto.get("produto_id")),
        "titulo": _texto(bruto.get("titulo"), 200),
        "cor": _texto(bruto.get("cor"), 120),
        "cor_rotulo": _texto(bruto.get("cor_rotulo"), 120),
        "quantidade": quantidade,
        "skus": _skus(bruto.get("skus")),
        "url": url_publica(bruto.get("url")),
        "imagem": url_publica(bruto.get("imagem")),
        "preco": _preco(bruto.get("preco")),
    }


def _item_do_evento(bruto: Any) -> dict | None:
    if not isinstance(bruto, dict):
        return None
    quantidade = _inteiro(bruto.get("quantidade"), minimo=1, maximo=100_000)
    if quantidade is None:
        return None
    return {
        "produto_id": _id_externo(bruto.get("produto_id")),
        "cor": _texto(bruto.get("cor"), 120),
        "quantidade": quantidade,
        "skus": _skus(bruto.get("skus")),
    }


@dataclass
class CarrinhoLido:
    lojista_id: str
    lojista: dict
    parado_desde: datetime
    itens: list[dict]
    quantidade_total: int


@dataclass
class EventoLido:
    id: str | None
    tipo: str
    lojista_id: str
    criado_em: datetime
    itens: list[dict]
    restantes: int | None


@dataclass
class Leitura:
    carrinhos: list[CarrinhoLido] = field(default_factory=list)
    eventos: list[EventoLido] = field(default_factory=list)
    # Quem mexeu no carrinho há menos de `horas` (lojista → a hora): só id e hora.
    ativos: dict[str, datetime] = field(default_factory=dict)
    truncado: bool = False
    gerado_em: datetime | None = None
    descartados: int = 0


def carrinho_do_site(bruto: Any, agora: datetime) -> CarrinhoLido | None:
    if not isinstance(bruto, dict):
        return None
    lojista = lojista_do_site(bruto.get("lojista"))
    parado = _data(bruto.get("parado_desde"))
    if lojista is None or parado is None:
        return None
    itens_brutos = bruto.get("itens") if isinstance(bruto.get("itens"), list) else []
    itens = [i for i in (item_do_site(x) for x in itens_brutos[:MAX_ITENS]) if i is not None]
    if not itens:
        return None
    total = _inteiro(bruto.get("quantidade_total"), minimo=1, maximo=10_000_000)
    return CarrinhoLido(
        lojista_id=lojista[0],
        lojista=lojista[1],
        # Relógio do site adiantado não joga o carrinho para o futuro.
        parado_desde=min(parado, agora),
        itens=itens,
        quantidade_total=total or sum(i["quantidade"] for i in itens),
    )


def evento_do_site(bruto: Any) -> EventoLido | None:
    if not isinstance(bruto, dict):
        return None
    tipo = _texto(bruto.get("tipo"), 20)
    lojista_id = _id_externo(bruto.get("lojista_id"))
    criado = _data(bruto.get("criado_em"))
    if tipo not in TIPOS_EVENTO or lojista_id is None or criado is None:
        return None
    itens_brutos = bruto.get("itens") if isinstance(bruto.get("itens"), list) else []
    return EventoLido(
        id=_id_externo(bruto.get("id")),
        tipo=tipo,
        lojista_id=lojista_id,
        criado_em=criado,
        itens=[i for i in (_item_do_evento(x) for x in itens_brutos[:MAX_ITENS]) if i is not None],
        restantes=_inteiro(bruto.get("restantes"), minimo=0, maximo=1_000_000),
    )


class FalhaDoSite(Exception):  # noqa: N818 — nome do domínio
    """O site não deu a leitura: o status do canal e o texto de operação (sem dado)."""

    def __init__(self, status: str, erro: str) -> None:
        super().__init__(erro)
        self.status = status
        self.erro = erro


def ler_resposta(corpo: bytes, site: str, agora: datetime) -> Leitura:
    """O JSON do site → `Leitura`. Fora do contrato → `FalhaDoSite` (erro)."""
    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FalhaDoSite(STATUS_ERRO, "resposta do site fora do contrato (não é JSON)") from None
    if not isinstance(dados, dict) or not isinstance(dados.get("carrinhos"), list):
        raise FalhaDoSite(
            STATUS_ERRO, "resposta do site fora do contrato (sem a lista de carrinhos)"
        )
    quem = _texto(dados.get("site"), 32)
    if quem is not None and quem.lower() != site:
        # O token é de um site e o endereço é de outro: nada é gravado.
        raise FalhaDoSite(STATUS_ERRO, f"o endereço respondeu como outro site ({quem[:20]})")
    eventos_brutos = dados.get("eventos") if isinstance(dados.get("eventos"), list) else []
    leitura = Leitura(truncado=bool(dados.get("truncado")), gerado_em=_data(dados.get("gerado_em")))
    vistos: set[str] = set()
    for bruto in dados["carrinhos"][:MAX_CARRINHOS]:
        lido = carrinho_do_site(bruto, agora)
        if lido is None or lido.lojista_id in vistos:
            leitura.descartados += 1
            continue
        vistos.add(lido.lojista_id)
        leitura.carrinhos.append(lido)
    for bruto in eventos_brutos[:MAX_EVENTOS]:
        ev = evento_do_site(bruto)
        if ev is None:
            leitura.descartados += 1
            continue
        leitura.eventos.append(ev)
    ativos_brutos = dados.get("ativos") if isinstance(dados.get("ativos"), list) else []
    for bruto in ativos_brutos[:MAX_ATIVOS]:
        if not isinstance(bruto, dict):
            continue
        lojista_id = _id_externo(bruto.get("lojista_id"))
        mexido = _data(bruto.get("atualizado_em"))
        if lojista_id is not None and mexido is not None:
            leitura.ativos[lojista_id] = min(mexido, agora)
    if (
        len(dados["carrinhos"]) > MAX_CARRINHOS
        or len(eventos_brutos) > MAX_EVENTOS
        or len(ativos_brutos) > MAX_ATIVOS
    ):
        leitura.truncado = True
    leitura.eventos.sort(key=lambda e: (e.criado_em, e.id or ""))
    return leitura


# ── A chamada ao site ─────────────────────────────────────────────────────


def _codigos_do_erro(corpo: bytes) -> tuple[str | None, str | None]:
    """("erro", "motivo") do JSON de erro do site — só códigos curtos; o resto, None."""
    try:
        dados = json.loads(corpo[:CORPO_DE_ERRO_MAX].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(dados, dict):
        return None, None

    def _codigo(chave: str) -> str | None:
        v = dados.get(chave)
        return v if isinstance(v, str) and _RX_CODIGO.fullmatch(v) else None

    return _codigo("erro"), _codigo("motivo") or _codigo("parametro")


def _falha_http(status: int, corpo: bytes = b"") -> FalhaDoSite:
    """O status HTTP do site (e o código do JSON de erro dele) → status do canal + texto.

    O site (CarrinhosDavinci.php) responde JSON nos erros dele: 401 com
    `motivo=sem_token` quando nenhum Authorization chegou ao PHP (a
    hospedagem cortou), 404 `nao_encontrado` com a rota desligada lá, 429
    depois de 60 tokens errados, 503 `falta_atualizacao_sql` sem a tabela
    nova. O 404 em HTML é a rota que ainda não existe (pacote não publicado);
    o 403 nunca sai do site — é a hospedagem barrando antes.
    """
    erro, motivo = _codigos_do_erro(corpo)
    if status == 401:
        return FalhaDoSite(
            STATUS_SEM_ESCOPO, ERRO_SEM_CABECALHO if motivo == "sem_token" else ERRO_401_NAO_CONFERE
        )
    if status == 403:
        return FalhaDoSite(STATUS_ERRO, ERRO_BARRADO)
    if status == 404:
        if erro == "nao_encontrado":
            return FalhaDoSite(STATUS_DESLIGADO, ERRO_ROTA_DESLIGADA)
        return FalhaDoSite(STATUS_SEM_ENDPOINT, ERRO_SEM_ENDPOINT)
    if 300 <= status < 400:
        return FalhaDoSite(
            STATUS_ERRO,
            f"HTTP {status}: o site redirecionou (o token não segue redirecionamento): "
            "confira o endereço em ATENDIMENTO_SITES_URLS",
        )
    if status == 422:
        qual = f" ({motivo})" if motivo else ""
        return FalhaDoSite(
            STATUS_ERRO, f"HTTP 422: o site recusou os parâmetros (horas/dias/desde){qual}"
        )
    if status == 429:
        if erro == "muitas_tentativas":
            return FalhaDoSite(STATUS_ERRO, ERRO_MUITAS_TENTATIVAS)
        return FalhaDoSite(STATUS_ERRO, "HTTP 429: limite de chamadas do site")
    if status == 503 and erro == "falta_atualizacao_sql":
        return FalhaDoSite(STATUS_ERRO, ERRO_FALTA_SQL)
    if status == 503 and erro == "banco_indisponivel":
        return FalhaDoSite(STATUS_ERRO, ERRO_BANCO_DO_SITE)
    if status == 500 and erro == "erro_interno":
        return FalhaDoSite(STATUS_ERRO, ERRO_INTERNO_DO_SITE)
    return FalhaDoSite(STATUS_ERRO, f"HTTP {status}")


async def buscar(
    cliente: httpx.AsyncClient,
    site: str,
    *,
    url: str,
    token: str,
    desde: datetime,
    agora: datetime,
) -> Leitura:
    """Um GET na rota do site → `Leitura`. Falha → `FalhaDoSite` (nunca com o token).

    Sem seguir redirecionamento (o token iria junto para outro endereço) e
    com teto no tamanho da resposta.
    """
    params = {"horas": str(_horas()), "dias": str(DIAS_JANELA), "desde": _iso_z(desde)}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    try:
        async with cliente.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            follow_redirects=False,
            timeout=httpx.Timeout(TEMPO_LEITURA_S, connect=TEMPO_CONEXAO_S),
        ) as resposta:
            if resposta.status_code != 200:
                # Só o começo do corpo (o JSON de erro do site é pequeno).
                corpo_erro = bytearray()
                async for pedaco in resposta.aiter_bytes():
                    corpo_erro.extend(pedaco)
                    if len(corpo_erro) >= CORPO_DE_ERRO_MAX:
                        break
                raise _falha_http(resposta.status_code, bytes(corpo_erro))
            corpo = bytearray()
            async for pedaco in resposta.aiter_bytes():
                corpo.extend(pedaco)
                if len(corpo) > RESPOSTA_MAXIMA:
                    raise FalhaDoSite(STATUS_ERRO, "resposta do site maior que 8 MB (recusada)")
    except FalhaDoSite:
        raise
    except httpx.TimeoutException:
        raise FalhaDoSite(STATUS_ERRO, "timeout ao chamar o site") from None
    except httpx.HTTPError as exc:
        raise FalhaDoSite(STATUS_ERRO, f"sem conexão com o site ({type(exc).__name__})") from None
    return ler_resposta(bytes(corpo), site, agora)


# ── Gravação ──────────────────────────────────────────────────────────────


def texto_do_carrinho(c: AtendimentoCarrinho) -> str:
    """A mensagem do lojista na conversa: o que ficou parado (sem dado pessoal)."""
    itens = [i for i in (c.itens or []) if isinstance(i, dict)]
    pecas = int(c.quantidade_total or 0)
    site = NOME_SITE.get(c.site, c.site)
    linhas = [
        f"Carrinho parado no site {site} desde {_fmt(_utc(c.parado_desde))}: "
        f"{_plural(pecas, 'peça', 'peças')} em {_plural(len(itens), 'produto', 'produtos')}."
    ]
    for it in itens[:10]:
        nome = it.get("titulo") or "Produto"
        cor = it.get("cor_rotulo") or it.get("cor")
        linhas.append(f"• {nome}{f' ({cor})' if cor else ''} × {it.get('quantidade') or 0}")
    if len(itens) > 10:
        linhas.append(f"• e mais {_plural(len(itens) - 10, 'produto', 'produtos')}")
    return "\n".join(linhas)


def texto_do_fim(c: AtendimentoCarrinho) -> str:
    """A mensagem do sistema no desfecho."""
    dados = c.dados if isinstance(c.dados, dict) else {}
    if c.situacao == CARRINHO_RECUPERADO:
        enviados = dados.get("itens_enviados")
        enviados = enviados if isinstance(enviados, list) else []
        pecas = sum(int(i.get("quantidade") or 0) for i in enviados if isinstance(i, dict))
        texto = (
            "Carrinho recuperado: o lojista finalizou o pedido pelo WhatsApp em "
            f"{_fmt(_utc(c.recuperado_em))}"
        )
        texto += f" ({_plural(pecas, 'peça', 'peças')} na mensagem)." if pecas else "."
        restantes = dados.get("restantes")
        if isinstance(restantes, int) and restantes > 0:
            ficou = "ficou" if restantes == 1 else "ficaram"
            texto += f" Ainda {ficou} {_plural(restantes, 'item', 'itens')} no carrinho."
        return texto
    if c.situacao == CARRINHO_NAO_RECUPERADO and c.motivo_fim == FIM_CARRINHO_ESVAZIADO:
        quando = _data(dados.get("evento_em"))
        return f"Carrinho não recuperado: o lojista esvaziou o carrinho em {_fmt(quando)}."
    if c.situacao == CARRINHO_NAO_RECUPERADO:
        return (
            f"Carrinho não recuperado: {CARRINHO_DIAS_RECUPERACAO} dias sem finalizar "
            "pelo WhatsApp."
        )
    return "Carrinho marcado como resolvido no DaVinci (nada foi mandado ao lojista)."


def _motivo_etiqueta(c: AtendimentoCarrinho) -> str:
    if c.situacao == CARRINHO_RECUPERADO:
        return MOTIVO_ETIQUETA_RECUPERADO
    if c.situacao == CARRINHO_RESOLVIDO:
        return MOTIVO_ETIQUETA_RESOLVIDO
    if c.motivo_fim == FIM_CARRINHO_ESVAZIADO:
        return MOTIVO_ETIQUETA_ESVAZIADO
    return MOTIVO_ETIQUETA_PRAZO


async def _garantir_conversa(
    session: AsyncSession, canal: AtendimentoCanal, c: AtendimentoCarrinho, *, vez_desde: datetime
) -> tuple[AtendimentoConversa, bool]:
    """A conversa do lojista no canal do site (cria se não há). Não commita."""
    retrato = c.lojista if isinstance(c.lojista, dict) else {}
    nome = retrato.get("empresa") or retrato.get("nome") or f"Lojista {c.lojista_id}"
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=None,
        plataforma=PLATAFORMA_SITE,
        canal_nome=CANAL_CARRINHO,
        externo_id=f"lojista:{c.lojista_id}",
        conta=NOME_SITE.get(c.site, c.site),
        comprador_id=c.lojista_id,
        comprador_nome=nome,
        dados={
            "site": c.site,
            "carrinho_id": str(c.id),
            # A vez é da loja enquanto o carrinho estiver aberto (o SLA do
            # carrinho conta da hora em que o DaVinci o viu).
            CHAVE_VEZ_DA_LOJA: _iso(vez_desde),
        },
    )
    if c.conversa_id != conversa.id:
        c.conversa_id = conversa.id
    if c.canal_id != canal.id:
        c.canal_id = canal.id
    return conversa, criada


def ultima_mexida(c: AtendimentoCarrinho) -> datetime | None:
    """A última vez que o lojista mexeu no carrinho: o `parado_desde` (o site o
    atualiza a cada mexida) ou o `dados.mexido_em` (o site o lista em `ativos`),
    o mais novo. É daí que correm os 7 dias do "não recuperado" (02/10/2026)."""
    marcos = [m for m in (_utc(c.parado_desde), _data((c.dados or {}).get("mexido_em"))) if m]
    return max(marcos) if marcos else _utc(c.detectado_em)


def vence_em(c: AtendimentoCarrinho) -> datetime | None:
    """Quando o carrinho aberto vira "não recuperado" (7 dias da última mexida)."""
    base = ultima_mexida(c)
    return base + timedelta(days=CARRINHO_DIAS_RECUPERACAO) if base else None


async def _abrir(
    session: AsyncSession, canal: AtendimentoCanal, site: str, lido: CarrinhoLido, agora: datetime
) -> AtendimentoCarrinho:
    """Episódio novo: a linha, a conversa do lojista, a mensagem e a etiqueta CARRINHO."""
    c = AtendimentoCarrinho(
        id=uuid4(),
        site=site,
        canal_id=canal.id,
        lojista_id=lido.lojista_id,
        lojista=lido.lojista,
        itens=lido.itens,
        quantidade_total=lido.quantidade_total,
        parado_desde=lido.parado_desde,
        detectado_em=agora,
        visto_em=agora,
        situacao=CARRINHO_ABERTO,
        dados={"parado_desde_inicial": _iso(lido.parado_desde)},
    )
    session.add(c)
    await session.flush()
    conversa, criada = await _garantir_conversa(session, canal, c, vez_desde=agora)
    await session.flush()
    await _mensagem_do_carrinho(session, conversa, c, criada=criada, agora=agora)
    return c


async def _mensagem_do_carrinho(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    c: AtendimentoCarrinho,
    *,
    criada: bool,
    agora: datetime,
) -> None:
    """A mensagem do lojista (idempotente pelo id), a fila e a etiqueta CARRINHO."""
    if criada:
        # A conversa acabou de nascer (o gravar já a classificou sem o
        # carrinho, como Pré-venda): a primeira classificação de verdade é o
        # Carrinho, sem uma troca "Pré-venda → Carrinho" na linha do tempo.
        conversa.etiqueta = None
        conversa.etiqueta_automatica = None
    await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"carrinho:{c.id}",
        autor=AUTOR_CLIENTE,
        texto=texto_do_carrinho(c),
        enviada_em=_utc(c.detectado_em) or agora,
        tipo="texto",
        anexos=[],
        payload={"carrinho_id": str(c.id), "carrinho": True},
        origem=None,
    )
    gravar.recalcular(conversa)
    await etiqueta.recalcular_etiqueta(
        session, conversa, motivo=MOTIVO_ETIQUETA_LEITURA, agora=agora
    )
    await session.flush()


async def _atualizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    c: AtendimentoCarrinho,
    lido: CarrinhoLido,
    agora: datetime,
) -> bool:
    """O retrato do aberto (o site é a verdade). True = o que a tela mostra mudou."""
    mudou = False
    if c.lojista != lido.lojista:
        c.lojista = lido.lojista
        mudou = True
    if c.itens != lido.itens or int(c.quantidade_total or 0) != lido.quantidade_total:
        c.itens = lido.itens
        c.quantidade_total = lido.quantidade_total
        mudou = True
    if _utc(c.parado_desde) != lido.parado_desde:
        # O lojista mexeu e parou de novo: o mesmo episódio (um aberto por
        # lojista), com o "parado desde" novo.
        c.parado_desde = lido.parado_desde
        mudou = True
    c.visto_em = agora
    dados = dict(c.dados or {})
    if "fora_da_lista_desde" in dados or "mexido_em" in dados:
        dados.pop("fora_da_lista_desde", None)
        dados.pop("mexido_em", None)
        c.dados = dados
    vez = _utc(c.detectado_em) or agora
    conversa, criada = await _garantir_conversa(session, canal, c, vez_desde=vez)
    await session.flush()
    if criada:
        # A conversa tinha sido apagada (o carrinho ficou sem ela): renasce
        # com a mensagem e a etiqueta.
        await _mensagem_do_carrinho(session, conversa, c, criada=True, agora=agora)
    elif mudou:
        await etiqueta.recalcular_etiqueta(
            session, conversa, motivo=MOTIVO_ETIQUETA_LEITURA, agora=agora
        )
    await session.flush()
    return mudou


async def _tem_aberto(session: AsyncSession, conversa_id: UUID) -> bool:
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(AtendimentoCarrinho)
            .where(
                AtendimentoCarrinho.conversa_id == conversa_id,
                AtendimentoCarrinho.situacao == CARRINHO_ABERTO,
            )
        )
    )


async def encerrar(
    session: AsyncSession,
    c: AtendimentoCarrinho,
    *,
    situacao: str,
    motivo_fim: str,
    agora: datetime,
    evento: EventoLido | None = None,
    user: User | None = None,
    motivo: str | None = None,
) -> None:
    """O desfecho: a linha, a mensagem do sistema, a fila e a etiqueta. Não commita.

    Também corrige o "não recuperado por prazo" quando o evento de
    finalização chega atrasado (a mensagem do fim ganha outro id).
    """
    estava_aberto = c.situacao == CARRINHO_ABERTO
    c.situacao = situacao
    c.motivo_fim = motivo_fim
    if c.encerrado_em is None:
        c.encerrado_em = agora
    dados = dict(c.dados or {})
    if evento is not None:
        dados.update(
            {
                "evento_id": evento.id,
                "evento_tipo": evento.tipo,
                "evento_em": _iso(evento.criado_em),
                "itens_enviados": evento.itens,
                "restantes": evento.restantes,
            }
        )
        if evento.tipo == "finalizado":
            c.recuperado_em = evento.criado_em
    if user is not None:
        c.tratado_por = user.id
        c.tratado_em = agora
    if motivo:
        dados["resolvido_motivo"] = motivo
    c.dados = dados
    await session.flush()
    if c.conversa_id is None:
        return
    conversa = await session.get(AtendimentoConversa, c.conversa_id)
    if conversa is None:
        return
    if not await _tem_aberto(session, conversa.id):
        # Sem carrinho aberto, a vez não é mais da loja: sai da fila.
        conversa.dados = {**(conversa.dados or {}), CHAVE_VEZ_DA_LOJA: None}
        await session.flush()
    sufixo = "fim" if estava_aberto else f"fim:{situacao}"
    await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"carrinho:{c.id}:{sufixo}",
        autor=AUTOR_SISTEMA,
        texto=texto_do_fim(c),
        # A hora do evento (relógio do site), nunca antes da mensagem do
        # carrinho: a conversa fica na ordem em que as coisas aconteceram aqui.
        enviada_em=(
            max(evento.criado_em, _utc(c.detectado_em) or evento.criado_em)
            if evento is not None
            else agora
        ),
        tipo="texto",
        anexos=[],
        payload={"carrinho_id": str(c.id), "carrinho_fim": situacao},
        origem=None,
    )
    gravar.recalcular(conversa)
    await etiqueta.recalcular_etiqueta(session, conversa, motivo=_motivo_etiqueta(c), agora=agora)
    await session.flush()


async def _travado(session: AsyncSession, cid: UUID | None) -> AtendimentoCarrinho | None:
    """O carrinho relido do banco DEPOIS de travar a linha (a tela pode tê-lo resolvido)."""
    if cid is None:
        return None
    c = await session.get(AtendimentoCarrinho, cid, populate_existing=True)
    if c is not None:
        await gravar.travar_linha(session, c)
    return c


async def _ids(session: AsyncSession, *filtros) -> list[UUID]:
    return list(
        (
            await session.execute(
                select(AtendimentoCarrinho.id)
                .where(*filtros)
                .order_by(AtendimentoCarrinho.detectado_em, AtendimentoCarrinho.id)
            )
        )
        .scalars()
        .all()
    )


async def _abertos_do_site(session: AsyncSession, site: str) -> dict[str, UUID]:
    linhas = (
        await session.execute(
            select(AtendimentoCarrinho.lojista_id, AtendimentoCarrinho.id).where(
                AtendimentoCarrinho.site == site,
                AtendimentoCarrinho.situacao == CARRINHO_ABERTO,
            )
        )
    ).all()
    return {str(lojista): cid for lojista, cid in linhas}


async def _por_prazo_recentes(session: AsyncSession, site: str, agora: datetime) -> dict[str, UUID]:
    """O "não recuperado por prazo" mais recente de cada lojista (o evento atrasado corrige)."""
    linhas = (
        await session.execute(
            select(AtendimentoCarrinho.lojista_id, AtendimentoCarrinho.id)
            .where(
                AtendimentoCarrinho.site == site,
                AtendimentoCarrinho.situacao == CARRINHO_NAO_RECUPERADO,
                AtendimentoCarrinho.motivo_fim == FIM_CARRINHO_PRAZO,
                AtendimentoCarrinho.encerrado_em >= agora - timedelta(days=DIAS_JANELA + 1),
            )
            .order_by(AtendimentoCarrinho.detectado_em)
        )
    ).all()
    return {str(lojista): cid for lojista, cid in linhas}


async def _marcos(session: AsyncSession, site: str, lojistas: list[str]) -> dict[str, datetime]:
    """Por lojista: o último "parado desde" ou recuperação já tratados (episódios antigos)."""
    if not lojistas:
        return {}
    linhas = (
        await session.execute(
            select(
                AtendimentoCarrinho.lojista_id,
                func.max(AtendimentoCarrinho.parado_desde),
                func.max(AtendimentoCarrinho.recuperado_em),
            )
            .where(
                AtendimentoCarrinho.site == site,
                AtendimentoCarrinho.lojista_id.in_(sorted(set(lojistas))),
            )
            .group_by(AtendimentoCarrinho.lojista_id)
        )
    ).all()
    saida: dict[str, datetime] = {}
    for lojista, parado, recuperado in linhas:
        candidatos = [q for q in (_utc(parado), _utc(recuperado)) if q is not None]
        if candidatos:
            saida[lojista] = max(candidatos)
    return saida


async def _uma(session: AsyncSession, resumo: dict, rotulo: str, fn, *args) -> None:
    """Uma unidade de trabalho (evento ou carrinho): commita sozinha; erro não para as outras.

    `fn` devolve a chave do resumo que a unidade conta (ou None): só conta
    depois do commit.
    """
    try:
        chave = await fn(*args)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — um carrinho não para os outros
        await session.rollback()
        resumo["erros"] += 1
        logger.warning("atendimento_carrinho_falhou", etapa=rotulo, erro=type(exc).__name__)
        return
    if chave:
        resumo[chave] = resumo.get(chave, 0) + 1


async def processar(
    session: AsyncSession, canal_id: UUID, site: str, leitura: Leitura, agora: datetime
) -> dict:
    """Grava a leitura de um site: eventos (desfecho), carrinhos (abre/atualiza), sumidos.

    Commita por unidade. Os eventos vêm ANTES: o evento posterior ao "parado
    desde" fecha o aberto, e o carrinho listado com evento depois dele é
    sobra de pedido, não abandono novo.
    """
    resumo = {
        "lidos": len(leitura.carrinhos),
        "eventos": len(leitura.eventos),
        "novos": 0,
        "atualizados": 0,
        "recuperados": 0,
        "esvaziados": 0,
        "corrigidos": 0,
        "ignorados": 0,
        "fora_da_lista": 0,
        "voltou_a_mexer": 0,
        "erros": 0,
    }
    prazo = timedelta(days=CARRINHO_DIAS_RECUPERACAO)

    # 1. Eventos, do mais antigo ao mais novo.
    abertos = await _abertos_do_site(session, site)
    por_prazo = await _por_prazo_recentes(session, site, agora)

    async def _evento(ev: EventoLido) -> str | None:
        cid = abertos.get(ev.lojista_id)
        c = await _travado(session, cid)
        if c is not None and c.situacao == CARRINHO_ABERTO and ev.criado_em > _utc(c.parado_desde):
            finalizou = ev.tipo == "finalizado"
            await encerrar(
                session,
                c,
                situacao=CARRINHO_RECUPERADO if finalizou else CARRINHO_NAO_RECUPERADO,
                motivo_fim=FIM_CARRINHO_FINALIZADO if finalizou else FIM_CARRINHO_ESVAZIADO,
                agora=agora,
                evento=ev,
            )
            abertos.pop(ev.lojista_id, None)
            return "recuperados" if finalizou else "esvaziados"
        # Evento de antes de parar não é desfecho do aberto — mas pode ser o
        # de um episódio fechado por prazo, chegando atrasado (o site ficou
        # fora do ar): finalizou dentro dos 7 dias, foi recuperado, sim.
        cid = por_prazo.get(ev.lojista_id)
        if ev.tipo != "finalizado" or cid is None:
            return None
        c = await _travado(session, cid)
        if c is None or c.situacao != CARRINHO_NAO_RECUPERADO or c.motivo_fim != FIM_CARRINHO_PRAZO:
            return None
        limite = vence_em(c) or agora
        if not (_utc(c.parado_desde) < ev.criado_em <= limite):
            return None
        await encerrar(
            session,
            c,
            situacao=CARRINHO_RECUPERADO,
            motivo_fim=FIM_CARRINHO_FINALIZADO,
            agora=agora,
            evento=ev,
        )
        por_prazo.pop(ev.lojista_id, None)
        return "corrigidos"

    for ev in leitura.eventos:
        # A janela traz 30 dias de eventos: só o do lojista com episódio em
        # jogo vira trabalho (o resto nem abre transação).
        if ev.lojista_id in abertos or ev.lojista_id in por_prazo:
            await _uma(session, resumo, "evento", _evento, ev)

    # 2. Carrinhos listados: atualiza o aberto ou abre um episódio novo.
    abertos = await _abertos_do_site(session, site)
    ultimo_evento: dict[str, datetime] = {}
    for ev in leitura.eventos:
        anterior = ultimo_evento.get(ev.lojista_id)
        if anterior is None or ev.criado_em > anterior:
            ultimo_evento[ev.lojista_id] = ev.criado_em
    marcos = await _marcos(
        session, site, [x.lojista_id for x in leitura.carrinhos if x.lojista_id not in abertos]
    )

    async def _carrinho(lido: CarrinhoLido) -> str | None:
        canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
        cid = abertos.get(lido.lojista_id)
        c = await _travado(session, cid)
        if c is not None and c.situacao == CARRINHO_ABERTO:
            await _atualizar(session, canal, c, lido, agora)
            return "atualizados"
        marco = max(
            [q for q in (marcos.get(lido.lojista_id), ultimo_evento.get(lido.lojista_id)) if q],
            default=None,
        )
        if marco is not None and lido.parado_desde <= marco:
            # Nada de novo desde o último desfecho (ou é a sobra de um pedido).
            return "ignorados"
        if lido.parado_desde + prazo <= agora:
            # Parado há mais de 7 dias desde a última mexida: já nasceria
            # "não recuperado" (a 1ª leitura olha 30 dias para trás).
            return "ignorados"
        try:
            async with session.begin_nested():
                c = await _abrir(session, canal, site, lido, agora)
        except IntegrityError:
            # Outra rodada abriu o mesmo no meio (o índice "um aberto"): atualiza.
            achado = await session.scalar(
                select(AtendimentoCarrinho).where(
                    AtendimentoCarrinho.site == site,
                    AtendimentoCarrinho.lojista_id == lido.lojista_id,
                    AtendimentoCarrinho.situacao == CARRINHO_ABERTO,
                )
            )
            if achado is None:
                raise
            await _atualizar(session, canal, achado, lido, agora)
            return "atualizados"
        abertos[lido.lojista_id] = c.id
        return "novos"

    vistos: set[str] = set()
    for lido in leitura.carrinhos:
        vistos.add(lido.lojista_id)
        await _uma(session, resumo, "carrinho", _carrinho, lido)

    # 3. O aberto que sumiu da lista sem evento: fica aberto (o evento ou o
    # prazo decidem), com a marca para a tela. Lista truncada não diz nada.
    # O site lista em `ativos` quem voltou a mexer (há menos de `horas`): a
    # tela diz "voltou a mexer em …" — não foi abandonado nem esvaziado.

    async def _sumiu(cid: UUID, mexido: datetime | None) -> str | None:
        c = await session.get(AtendimentoCarrinho, cid, populate_existing=True)
        if c is None or c.situacao != CARRINHO_ABERTO:
            return None
        dados = dict(c.dados or {})
        chave = None
        if not dados.get("fora_da_lista_desde"):
            dados["fora_da_lista_desde"] = _iso(agora)
            chave = "fora_da_lista"
        if mexido is not None and dados.get("mexido_em") != _iso(mexido):
            dados["mexido_em"] = _iso(mexido)
            chave = chave or "voltou_a_mexer"
        if chave is None:
            return None
        c.dados = dados
        await session.flush()
        return chave

    for lojista, cid in (await _abertos_do_site(session, site)).items():
        if lojista in vistos:
            continue
        mexido = leitura.ativos.get(lojista)
        if leitura.truncado and mexido is None:
            continue
        await _uma(session, resumo, "sumiu", _sumiu, cid, mexido)
    return resumo


async def fechar_vencidos(*, agora: datetime | None = None) -> dict:
    """O prazo (7 dias desde a última mexida do lojista): aberto vira não recuperado. Só banco.

    Roda mesmo com o site fora do ar (o prazo é de relógio); o evento que
    chegar atrasado ainda corrige para recuperado (`processar`).
    """
    agora = agora or _agora()
    resumo = {"vencidos": 0, "erros": 0}
    async with _db.SessionLocal() as session:
        ids = await _ids(
            session,
            AtendimentoCarrinho.situacao == CARRINHO_ABERTO,
            # Pré-filtro: a última mexida nunca é anterior ao `parado_desde`;
            # a conta exata (com o `mexido_em`) é feita na linha travada.
            AtendimentoCarrinho.parado_desde <= agora - timedelta(days=CARRINHO_DIAS_RECUPERACAO),
        )

        async def _vencer(cid: UUID) -> str | None:
            c = await _travado(session, cid)
            if c is None or c.situacao != CARRINHO_ABERTO:
                return None
            limite = vence_em(c)
            if limite is not None and limite > agora:
                return None  # o lojista mexeu depois: o prazo recomeçou
            await encerrar(
                session,
                c,
                situacao=CARRINHO_NAO_RECUPERADO,
                motivo_fim=FIM_CARRINHO_PRAZO,
                agora=agora,
            )
            return "vencidos"

        for cid in ids:
            await _uma(session, resumo, "prazo", _vencer, cid)
    return resumo


async def _registrar(
    session: AsyncSession,
    canal_id: UUID,
    status: str,
    erro: str | None,
    agora: datetime,
    **cursor: Any,
) -> None:
    """A saúde do canal do site (aba Lojas): status, carimbos e o texto de operação."""
    try:
        canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
        if canal is None:
            return
        canal.status = status
        if status == STATUS_OK:
            canal.ultimo_ok_em = agora
        if erro:
            canal.ultimo_erro_em = agora
            canal.ultimo_erro = erro[:300]
        if cursor:
            canais_externos.com_externo(canal, **cursor)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — a saúde do canal não derruba a rodada
        await session.rollback()
        logger.warning("atendimento_carrinhos_status_falhou", erro=type(exc).__name__)


async def ler_site(site: str, *, cliente: httpx.AsyncClient, agora: datetime | None = None) -> dict:
    """Um site: garante o canal, chama a rota, grava e registra a saúde. Nunca levanta."""
    agora = agora or _agora()
    async with _db.SessionLocal() as session:
        try:
            canal = await canais_externos.garantir_canal(
                session,
                externo_ref=canais_externos.ref_do_site(site),
                plataforma=PLATAFORMA_SITE,
                canal=CANAL_CARRINHO,
                nome=NOME_SITE.get(site, site),
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning("atendimento_carrinhos_canal_falhou", site=site, erro=type(exc).__name__)
            return {"status": STATUS_ERRO}
        canal_id = canal.id
        url = rota_do_site(site)
        if url is None:
            await _registrar(session, canal_id, STATUS_ERRO, ERRO_URL, agora)
            return {"status": STATUS_ERRO}
        token = token_do_site(site)
        if token is None:
            await _registrar(session, canal_id, STATUS_DESLIGADO, ERRO_SITE_SEM_ACESSO, agora)
            return {"status": STATUS_DESLIGADO}
        try:
            leitura = await buscar(
                cliente,
                site,
                url=url,
                token=token,
                desde=agora - timedelta(days=DIAS_JANELA),
                agora=agora,
            )
        except FalhaDoSite as f:
            await _registrar(session, canal_id, f.status, f.erro, agora)
            logger.info("atendimento_carrinhos_site_falhou", site=site, status=f.status)
            return {"status": f.status}
        try:
            resumo = await processar(session, canal_id, site, leitura, agora)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            erro = f"falha ao gravar a leitura ({type(exc).__name__})"
            await _registrar(session, canal_id, STATUS_ERRO, erro, agora)
            logger.warning(
                "atendimento_carrinhos_gravar_falhou", site=site, erro=type(exc).__name__
            )
            return {"status": STATUS_ERRO}
        aviso = AVISO_TRUNCADO if leitura.truncado else None
        if resumo["erros"] and not aviso:
            total = resumo["lidos"] + resumo["eventos"]
            aviso = f"{resumo['erros']} de {total} itens da leitura falharam ao gravar"
        await _registrar(
            session,
            canal_id,
            STATUS_OK,
            aviso,
            agora,
            ultima_leitura_em=_iso(agora),
            gerado_em=_iso(leitura.gerado_em),
            carrinhos_lidos=len(leitura.carrinhos),
            eventos_lidos=len(leitura.eventos),
            ativos_lidos=len(leitura.ativos),
            truncado=leitura.truncado,
        )
        return {"status": STATUS_OK, **resumo, "descartados": leitura.descartados}


# ── O cron ────────────────────────────────────────────────────────────────


async def _pegar_trava() -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        # Sem Redis, roda sem trava: a gravação é idempotente (índice "um
        # aberto por lojista" + ids das mensagens).
        logger.warning("atendimento_carrinhos_trava_indisponivel", err=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_trava(token: str | None) -> None:
    if token is None:
        return
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_carrinhos_trava_soltar_falhou")


async def atendimento_carrinhos(
    ctx: Any = None,
    *,
    cliente_http: httpx.AsyncClient | None = None,
    agora: datetime | None = None,
    **_: Any,
) -> dict | None:
    """O CRON (worker `atendimento_carrinhos`, :14/:44, `timeout=600`).

    Só roda com `ATENDIMENTO_CARRINHOS_ATIVA` E `ATENDIMENTO_LEITURA_ATIVA`;
    uma rodada por vez. Um GET por site (erro de um não para o outro) e,
    depois, o prazo de 7 dias (só banco). Nunca levanta: o erro vira log (só
    o tipo) e `None`. `cliente_http` = o cliente HTTP (os testes passam um
    falso, com o site de mentira).
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_carrinhos_ativa):
        return None
    pegou, token = await _pegar_trava()
    if not pegou:
        logger.info("atendimento_carrinhos_ocupado")
        return {"pulado": True}
    agora = agora or _agora()
    resumo: dict[str, Any] = {}
    try:
        if cliente_http is None:
            async with httpx.AsyncClient(follow_redirects=False) as cliente:
                for site in SITES:
                    resumo[site] = await ler_site(site, cliente=cliente, agora=agora)
        else:
            for site in SITES:
                resumo[site] = await ler_site(site, cliente=cliente_http, agora=agora)
        resumo["prazo"] = await fechar_vencidos(agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.error("atendimento_carrinhos_falhou", err=type(exc).__name__)
        return None
    finally:
        await _soltar_trava(token)
    logger.info("atendimento_carrinhos_tick", **resumo)
    return resumo


# ── Para a tela (o cartão do carrinho) ────────────────────────────────────


class AcaoRecusada(Exception):  # noqa: N818 — nome do domínio, como o das avaliações
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


async def carrinho_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> tuple[AtendimentoCarrinho | None, list[AtendimentoCarrinho]]:
    """(o aberto — ou o mais recente — da conversa, os anteriores do lojista no site)."""
    linhas = list(
        (
            await session.execute(
                select(AtendimentoCarrinho)
                .where(AtendimentoCarrinho.conversa_id == conversa.id)
                .order_by(
                    (AtendimentoCarrinho.situacao == CARRINHO_ABERTO).desc(),
                    AtendimentoCarrinho.detectado_em.desc(),
                    AtendimentoCarrinho.created_at.desc(),
                )
                .limit(MAX_ANTERIORES + 1)
            )
        )
        .scalars()
        .all()
    )
    if not linhas:
        return None, []
    return linhas[0], linhas[1:]


async def _saldo_seguro(session: AsyncSession, sku: str) -> dict | None:
    """`painel.saldo_do_item` num SAVEPOINT: uma consulta que falhe não leva o cartão."""
    await session.flush()
    try:
        async with session.begin_nested():
            return await painel.saldo_do_item(session, sku)
    except Exception as exc:  # noqa: BLE001
        logger.warning("atendimento_carrinho_saldo_falhou", erro=type(exc).__name__)
        return None


async def estoque_dos_itens(session: AsyncSession, itens: list[dict]) -> list[dict]:
    """O estoque ATUAL do DaVinci de cada item, pela regra do estoque do site.

    SKU exato = o saldo dele; "base.*" = a soma dos lotes de venda ativos
    (ci/pi/ra/sa/sp); vários itens na escolha = a soma. Negativo conta como
    0. SKU que o DaVinci não tem (ou inativo) deixa o item DESCONHECIDO —
    nunca zero por falta de dado. A demanda de cada linha soma as linhas que
    dividem algum SKU (duas cores no mesmo SKU), como o carrinho do site.
    """
    memo: dict[str, dict | None] = {}

    async def _saldo(sku: str) -> dict | None:
        if sku not in memo:
            memo[sku] = await _saldo_seguro(session, sku)
        return memo[sku]

    linhas: list[dict] = []
    for item in itens[:MAX_ITENS_TELA]:
        skus = [s for s in (item.get("skus") or []) if isinstance(s, str) and sku_valido(s)]
        quantidade = int(item.get("quantidade") or 0)
        concretos: dict[str, int] = {}
        detalhes: list[dict] = []
        conhecido = bool(skus)
        falhou = False
        outros = 0
        for s in skus:
            if s.endswith(".*"):
                base = s[:-2]
                r = await _saldo(f"{base}.{_LOTE_REFERENCIA}")
                if r is None:
                    falhou = True
                    conhecido = False
                    continue
                lotes = [
                    lote
                    for lote in r.get("lotes") or []
                    if lote.get("de_venda") and lote.get("ativo") and lote.get("saldo") is not None
                ]
                if not lotes:
                    conhecido = False
                    detalhes.append({"sku": s, "existe": False, "saldo": None})
                    continue
                for lote in lotes:
                    chave = str(lote["sku"]).lower()
                    concretos[chave] = max(0, int(lote["saldo"]))
                    detalhes.append(
                        {
                            "sku": lote["sku"],
                            "existe": True,
                            "saldo": int(lote["saldo"]),
                            "lote": lote.get("lote"),
                            "atualizado_em": lote.get("atualizado_em"),
                        }
                    )
                continue
            r = await _saldo(s)
            if r is None:
                falhou = True
                conhecido = False
                continue
            if not r.get("existe") or not r.get("ativo") or r.get("saldo") is None:
                conhecido = False
                detalhes.append(
                    {
                        "sku": s,
                        "existe": bool(r.get("existe")),
                        "saldo": r.get("saldo"),
                        "nome": r.get("nome"),
                    }
                )
                continue
            concretos[s] = max(0, int(r["saldo"]))
            outros += int(r.get("saldo_outros_lotes") or 0)
            detalhes.append(
                {
                    "sku": r.get("sku") or s,
                    "existe": True,
                    "saldo": int(r["saldo"]),
                    "nome": r.get("nome"),
                    "kit": bool(r.get("kit")),
                    "lote": r.get("lote"),
                    "atualizado_em": r.get("atualizado_em"),
                }
            )
        linhas.append(
            {
                "concretos": set(concretos),
                "disponivel": sum(concretos.values()) if conhecido and concretos else None,
                "quantidade": quantidade,
                "skus": detalhes,
                "outros_lotes": outros,
                "falhou": falhou,
                "sem_mapa": not skus,
            }
        )

    saida: list[dict] = []
    for linha in linhas:
        disponivel = linha["disponivel"]
        demanda = (
            sum(o["quantidade"] for o in linhas if o["concretos"] & linha["concretos"])
            or linha["quantidade"]
        )
        if linha["sem_mapa"]:
            status, texto = "sem_mapa", "Produto sem SKU ligado no site: estoque desconhecido"
        elif disponivel is None:
            status = "desconhecido"
            texto = (
                "Não consegui ler o estoque agora"
                if linha["falhou"]
                else "SKU fora do catálogo do DaVinci (ou inativo): estoque desconhecido"
            )
        elif disponivel == 0:
            status, texto = "zero", "Sem estoque no DaVinci"
        elif demanda > disponivel:
            status = "acima"
            texto = f"Acima do estoque ({_plural(disponivel, 'disponível', 'disponíveis')})"
        else:
            status, texto = "ok", f"{disponivel} em estoque"
        saida.append(
            {
                "disponivel": disponivel,
                "demanda": demanda,
                "status": status,
                "texto": texto,
                "outros_lotes": linha["outros_lotes"],
                "falhou": linha["falhou"],
                "skus": linha["skus"],
            }
        )
    return saida


async def taxa_do_site(session: AsyncSession, site: str, *, agora: datetime | None = None) -> dict:
    """Os episódios dos últimos 30 dias do site por situação e a taxa de recuperação."""
    agora = agora or _agora()
    linhas = (
        await session.execute(
            select(AtendimentoCarrinho.situacao, func.count())
            .where(
                AtendimentoCarrinho.site == site,
                AtendimentoCarrinho.detectado_em >= agora - timedelta(days=DIAS_TAXA),
            )
            .group_by(AtendimentoCarrinho.situacao)
        )
    ).all()
    contagem = {situacao: int(n) for situacao, n in linhas}
    encerrados = sum(n for s, n in contagem.items() if s != CARRINHO_ABERTO)
    recuperados = contagem.get(CARRINHO_RECUPERADO, 0)
    return {
        "dias": DIAS_TAXA,
        "detectados": sum(contagem.values()),
        "abertos": contagem.get(CARRINHO_ABERTO, 0),
        "recuperados": recuperados,
        "nao_recuperados": contagem.get(CARRINHO_NAO_RECUPERADO, 0),
        "resolvidos": contagem.get(CARRINHO_RESOLVIDO, 0),
        "encerrados": encerrados,
        "taxa": round(recuperados / encerrados, 4) if encerrados else None,
    }


def _valor_total(itens: list[dict]) -> float | None:
    if not itens or any(i.get("preco") is None for i in itens):
        return None
    return round(sum(float(i["preco"]) * int(i.get("quantidade") or 0) for i in itens), 2)


def para_tela(
    c: AtendimentoCarrinho,
    *,
    estoque: list[dict] | None,
    nomes: dict[UUID, str | None] | None = None,
) -> dict:
    """O carrinho no formato do `CarrinhoOut` (routers/atendimento_carrinhos.py)."""
    dados = c.dados if isinstance(c.dados, dict) else {}
    itens = [i for i in (c.itens or []) if isinstance(i, dict)]
    lojista = c.lojista if isinstance(c.lojista, dict) else {}
    saida_itens = []
    for n, it in enumerate(itens[:MAX_ITENS_TELA]):
        saida_itens.append(
            {
                "produto_id": it.get("produto_id"),
                "titulo": it.get("titulo"),
                "cor": it.get("cor"),
                "cor_rotulo": it.get("cor_rotulo"),
                "quantidade": int(it.get("quantidade") or 0),
                "skus": [s for s in it.get("skus") or [] if isinstance(s, str)],
                # Releitura do que foi guardado: só o que a tela pode usar.
                "url": url_publica(it.get("url")),
                "imagem": url_publica(it.get("imagem")),
                "preco": _preco(it.get("preco")),
                "estoque": estoque[n] if estoque is not None and n < len(estoque) else None,
            }
        )
    sem_estoque = sum(
        1 for i in saida_itens if i["estoque"] and i["estoque"]["status"] in ("zero", "acima")
    )
    detectado = _utc(c.detectado_em)
    restantes = dados.get("restantes")
    return {
        "id": c.id,
        "site": c.site,
        "site_nome": NOME_SITE.get(c.site, c.site),
        "site_url": url_do_site(c.site),
        "conversa_id": c.conversa_id,
        "situacao": c.situacao,
        "situacao_rotulo": ROTULO_SITUACAO.get(c.situacao, c.situacao),
        "motivo_fim": c.motivo_fim,
        "motivo_fim_rotulo": ROTULO_FIM.get(c.motivo_fim or "") if c.motivo_fim else None,
        "lojista_id": c.lojista_id,
        "lojista": {campo: lojista.get(campo) for campo in _CAMPOS_LOJISTA},
        "itens": saida_itens,
        "itens_total": len(itens),
        "quantidade_total": int(c.quantidade_total or 0),
        "valor_total": _valor_total(itens),
        "itens_sem_estoque": sem_estoque,
        "parado_desde": _utc(c.parado_desde),
        "detectado_em": detectado,
        "visto_em": _utc(c.visto_em),
        "fora_da_lista_desde": _data(dados.get("fora_da_lista_desde")),
        "mexido_em": _data(dados.get("mexido_em")),
        "prazo_em": vence_em(c),
        "encerrado_em": _utc(c.encerrado_em),
        "recuperado_em": _utc(c.recuperado_em),
        "itens_enviados": [i for i in (dados.get("itens_enviados") or []) if isinstance(i, dict)][
            :MAX_ITENS_TELA
        ],
        "restantes": restantes if isinstance(restantes, int) else None,
        "tratado_em": _utc(c.tratado_em),
        "tratado_por_nome": (nomes or {}).get(c.tratado_por) if c.tratado_por else None,
        "resolvido_motivo": dados.get("resolvido_motivo")
        if isinstance(dados.get("resolvido_motivo"), str)
        else None,
        "pode_resolver": c.situacao == CARRINHO_ABERTO,
    }


async def marcar_resolvido(
    session: AsyncSession,
    c: AtendimentoCarrinho,
    *,
    user: User,
    motivo: str | None = None,
    agora: datetime | None = None,
) -> bool:
    """Marcar como resolvido: sai de aberto e a etiqueta volta. Nada vai ao lojista. Não commita.

    Idempotente (o já resolvido → False). O já recuperado/não recuperado →
    `AcaoRecusada("carrinho_encerrado")`. Trava a linha antes de decidir (a
    leitura do site pode estar fechando o mesmo carrinho); ocupada por mais
    de 5 s → `AcaoRecusada("carrinho_ocupado")`.
    """
    if not await gravar.travar_linha(session, c, espera="5s"):
        raise AcaoRecusada(
            "carrinho_ocupado",
            "A leitura do site está atualizando este carrinho agora; tente de novo em instantes.",
        )
    if c.situacao == CARRINHO_RESOLVIDO:
        return False
    if c.situacao != CARRINHO_ABERTO:
        rotulo = ROTULO_SITUACAO.get(c.situacao, c.situacao).lower()
        raise AcaoRecusada("carrinho_encerrado", f"Este carrinho já foi encerrado ({rotulo}).")
    texto = " ".join((motivo or "").split())[:300] or None
    await encerrar(
        session,
        c,
        situacao=CARRINHO_RESOLVIDO,
        motivo_fim=FIM_CARRINHO_RESOLVIDO,
        agora=agora or _agora(),
        user=user,
        motivo=texto,
    )
    return True
