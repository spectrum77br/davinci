"""Comentários e menções das redes sociais no atendimento (RF7, 02/10/2026).

Instagram e Página do Facebook da CHARLOTS e da URANYX (`MARCAS_REDES`; a
Locagil e a 7buyers não entram — o Direct da 7buyers continua no adaptador
só leitura `instagram.py`, que NÃO é duplicado aqui). Todo comentário de
pessoa numa publicação da marca, e toda menção (IG `/tags`), vira conversa
`canal = 'comentario'` com a etiqueta MÍDIA (a base do canal, em
`etiqueta_fatos`), pendente até a marca responder.

"Menção" AQUI é só a MARCAÇÃO na foto/vídeo (`/{ig}/tags`): a única que a
Graph entrega por consulta. O @ na legenda do post de outra pessoa, a menção
em story e o @ num comentário de outra publicação só chegam por WEBHOOK
(campo `mentions` do Instagram, com `pages_manage_metadata` e o app
assinado) — fora desta versão. A Página do Facebook (`/{page}/tagged`) idem.

A menção nasce SEM a vez da loja (`sem_resposta_necessaria`), a não ser que a
legenda seja pergunta (`constantes.e_pergunta`): a Meta não deixa ler os
comentários do post de OUTRA pessoa, então a resposta que a marca der pelo
app nunca volta para cá — a menção ficaria "esperando resposta" para
sempre. A menção-pergunta fica na fila até alguém marcar como resolvida.

## O token (medido em produção em 02/10/2026, só GET)

O do usuário de sistema do app "DaVinci Publicador" (portfólio
poofy_brasil), em `redes_sociais_tokens` (provedor `facebook`, tipo
SYSTEM_USER, não vence), com DOIS segredos no blob cifrado:
`access_token` (o do sistema) e `page_access_token` (o da Página ligada à
conta). NÃO é um token só: as 5 linhas de publicação têm 3 tokens (as
Páginas Charlots/Uranyx um, os Instagram Charlots/Uranyx outro, a 7buyers
um terceiro), todos com os MESMOS 13 escopos. Cada linha é trocada em
Cadastros › Redes Sociais → a conta → Publicação automática → "trocar
token" (POST /api/redes-sociais/{id}/conectar), que regrava os dois.

O que o token de hoje faz (sondas só GET em produção, 02/10/2026):
  • IG `/{ig}/media` e `/{media}/comments`: LÊ o texto, a hora, o oculto e
    as curtidas, mas SEM o autor — `username`, `from` e `user` são omitidos
    em silêncio (a Meta exige `instagram_manage_comments` desde 27/08/2024).
    Sem o autor não há "uma conversa por pessoa": a leitura para ali e o
    canal fica `sem_escopo` ("comentário sem o autor").
  • IG `/{ig}/tags` (menções): (#10) — falta `instagram_manage_comments`.
  • FB `/{page}/posts` e `/{post}/comments`: SÓ com o token DA PÁGINA (o do
    sistema dá 190/2069032); lê (0 comentários hoje nas duas Páginas — o
    autor `from` não pôde ser conferido). `/{page}/tagged`: (#100) Missing
    Permission (`pages_read_user_content`).

## A leitura (cron `atendimento_redes`, worker a cada 15 min: :09/:24/:39/:54)

Só com `atendimento_leitura_ativa` E `atendimento_redes_ativa`; uma rodada
por vez (trava no Redis); SÓ GET; nunca levanta; log só com ids e
contagens — NUNCA texto, nome ou @ de pessoa, nem token.

  • Canal por conta: `canais_externos.garantir_canal(externo_ref=
    "rede:instagram:<ig_user_id>" | "rede:facebook:<page_id>", canal=
    "comentario", nome="@charlots_br" | "Charlots Brasil", rede_social_id=…)`.
  • Instagram: 1 GET `/{ig}/media` (as `LIMITE_MIDIAS` mais recentes, com
    `comments_count`); os comentários (`/{media}/comments` com `replies`)
    só da publicação cuja contagem mudou desde a última leitura (ou que não
    é relida há `RELER_PUBLICACAO`, se recente) — até
    `MAX_LEITURAS_POR_CONTA` por rodada. Depois `/{ig}/tags` (menções).
  • Facebook: o token da Página (do blob; sem ele, `GET /{page}?fields=
    access_token`); `/{page}/posts` com o total de comentários (`filter
    (stream)`: conta as respostas), e `/{post}/comments?filter=stream`.
  • Uma conversa por (pessoa, publicação): `externo_id = "<media>:<id da
    pessoa ou @username>"`, `comprador_nome` = "@username" (IG) ou o nome
    (FB), `anuncio_id` = a mídia, `anuncio_titulo` = a origem ("Reels
    28/09", "menção · Foto 30/09"), `dados` = {publicacao_id, tipo
    comentario|mencao, eh_pergunta, prazo}. Cada comentário = linha em
    `atendimento_comentarios` + mensagem (`externo_id` = id do comentário).
  • O comentário da PRÓPRIA marca (`da_marca`) é resposta: mensagem da LOJA
    na conversa de quem foi respondido (o pai; ou o @ citado no começo, se
    é outra pessoa do mesmo fio) — nunca conversa nova.
  • Só comentário dos últimos `MIDIA_DIAS_LEITURA` dias vira conversa (a
    história não vira fila); o mais velho fica no cartão da publicação.
  • Pergunta (`constantes.e_pergunta`) ainda sem resposta da marca: prazo de
    `SLA_PERGUNTA_COMENTARIO_HORAS` (`dados[CHAVE_PRAZO_PLATAFORMA]`) e
    `dados.eh_pergunta` — a lista põe a pergunta à frente no filtro Mídia e
    no "Falta responder" (routers/atendimento.py) e mostra o selo; só
    ordena (RF7), não tira nada da fila.
  • Status do canal (aba Lojas): `ok`; `sem_escopo` quando a Meta nega uma
    parte (code 10/3/200–299, "(#100) Missing Permission", ou o comentário
    de pessoa sem autor — todos, ou só alguns), com O QUE falta em
    `ultimo_erro`; `erro` no resto (token recusado 190, rede fora do ar).
    Uma parte negada não impede as outras.

## As ações (routers/atendimento_redes.py)

Responder em PÚBLICO (`POST /{comentário de topo}/replies` no IG,
`/{comentário}/comments` no FB; menção: `POST /{ig}/mentions`), "Responder
no Direct" (resposta privada: `POST /{ig ou page}/messages` com
`recipient.comment_id`, token da Página, UMA mensagem até
`RESPOSTA_PRIVADA_DIAS` dias depois do comentário) e Ocultar (`POST
/{comentário}` com `hide` no IG / `is_hidden` no FB). O caminho existe e é
testado com HTTP falso, mas fica BLOQUEADO enquanto
`atendimento_envio_ativo` for falso (409 `envio_desligado`, ANTES de
qualquer coisa — como as avaliações), pede `confirmar=true` ("Responder em
PÚBLICO?") e só sai com a conta em modo `humano` na aba Lojas. A resposta
nasce `enviando` (o índice de uma em voo por conversa) e vira `enviada`,
`falhou` ou `revisar` (ambíguo) — a régua do `enviar`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.db import is_unique_violation
from app.models import (
    AtendimentoCanal,
    AtendimentoComentario,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPublicacao,
    Marca,
    RedeSocial,
    RedeSocialToken,
    User,
)
from app.redis_client import redis
from app.security.cipher import decrypt_json
from app.services.atendimento import canais_externos, enviar, gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_COMENTARIO,
    CHAVE_PRAZO_PLATAFORMA,
    MIDIA_DIAS_LEITURA,
    MODOS_QUE_ENVIAM,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_HUMANO,
    PLATAFORMA_INSTAGRAM,
    PLATAFORMAS_REDE,
    PUBLICACAO_MENCAO,
    PUBLICACAO_PROPRIA,
    RESPOSTA_PRIVADA_DIAS,
    SLA_PERGUNTA_COMENTARIO_HORAS,
    e_pergunta,
    limite_caracteres,
)
from app.services.atendimento.enviar import EnvioRecusado
from app.services.marketing import meta_client

logger = structlog.get_logger()

# As marcas cujas redes viram conversa (nome — ou slug — da marca no
# cadastro, minúsculo; a Charlots tem slug "poofy").
MARCAS_REDES = ("charlots", "uranyx")
# A trava da rodada no Redis (uma por vez) = o timeout do cron (840 s), abaixo
# do intervalo de 15 min.
CHAVE_TRAVA = "atendimento:redes:rodada"
TRAVA_TTL_S = 14 * 60

BRT = ZoneInfo("America/Sao_Paulo")
# As chamadas são pequenas: a Meta que não responde em 20 s volta na rodada seguinte.
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# ── O que cada rodada lê ──────────────────────────────────────────────────
# Instagram: 1 página do /media (50 = as mais recentes; a Charlots tem 0 nos
# últimos 30 dias e 2 publicações antigas com comentário — medido em 02/10).
LIMITE_MIDIAS = 50
LIMITE_POSTS_FB = 25
LIMITE_TAGS = 25
PAGINA_COMENTARIOS = 50
MAX_PAGINAS_COMENTARIOS = 4
# Publicações com comentário NOVO lidas por conta e rodada (cada uma = 1 GET).
MAX_LEITURAS_POR_CONTA = 15
# A publicação recente é relida mesmo sem contagem nova (o oculto, a curtida).
RELER_PUBLICACAO = timedelta(hours=6)
# A URL da miniatura é de CDN assinada e expira: a tela renova depois disto.
MINIATURA_VALIDADE = timedelta(hours=6)
# A resposta privada é uma mensagem de Direct/Messenger.
LIMITE_DIRECT = 1000
# O cartão da publicação mostra no máximo isto.
MAX_TELA = 200

CAMPOS_MIDIA_IG = (
    "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,"
    "timestamp,username,like_count,comments_count"
)
CAMPOS_COMENTARIO_IG = (
    "id,text,timestamp,username,from,like_count,hidden,parent_id,"
    "replies{id,text,timestamp,username,from,like_count,hidden,parent_id}"
)
CAMPOS_POST_FB = (
    "id,created_time,permalink_url,full_picture,status_type,message,"
    "comments.filter(stream).limit(0).summary(true),reactions.limit(0).summary(true)"
)
CAMPOS_COMENTARIO_FB = "id,created_time,from,message,like_count,is_hidden,parent{id},permalink_url"

# O escopo que a Meta pede para cada parte (o texto do `sem_escopo`).
ESCOPO_COMENTARIOS_IG = "instagram_manage_comments"
ESCOPO_CONTEUDO_FB = "pages_read_user_content"
ESCOPO_ENGAJAMENTO_FB = "pages_manage_engagement"
ESCOPO_MENSAGENS_FB = "pages_messaging"

STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"

# Códigos da Graph que são soluço, não recusa (como `marketing/metricas.py`).
_CODIGOS_PASSAGEIROS = frozenset({1, 2, 4, 17, 32, 341, 613})

ROTULO_FORMATO = {
    "REELS": "Reels",
    "STORY": "Story",
    "CAROUSEL_ALBUM": "Carrossel",
    "VIDEO": "Vídeo",
    "IMAGE": "Foto",
    "POST": "Post",
}
_FORMATO_FB = {"added_video": "VIDEO", "added_photos": "IMAGE"}

# ── Ações ─────────────────────────────────────────────────────────────────
ACAO_PUBLICO = "publico"
ACAO_DIRECT = "direct"
RECUSA_CONFIRMAR = "confirmar_publico"
RECUSA_COMENTARIO_OCULTO = "comentario_oculto"
RECUSA_COMENTARIO_DA_MARCA = "comentario_da_marca"
RECUSA_SEM_RESPOSTA_PRIVADA = "sem_resposta_privada"
RECUSA_PRAZO_PRIVADA = "prazo_resposta_privada"
RECUSA_PRIVADA_JA_ENVIADA = "resposta_privada_ja_enviada"
RECUSA_SEM_OCULTAR = "sem_ocultar"
RECUSA_SEM_TOKEN = "sem_token"  # noqa: S105 — código de recusa, não segredo
RECUSA_REDE_RECUSOU = "rede_recusou"
RECUSA_SEM_CONVERSA = "conversa_nao_encontrada"

MOTIVO_ENVIO_DESLIGADO = (
    "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO): responder em público, "
    "no Direct e ocultar ficam bloqueados — faça pelo app do Instagram/Facebook."
)
MOTIVO_OBSERVAR = (
    "Esta conta está em modo observar na aba Lojas: o DaVinci só lê. Mude para humano "
    "para responder por aqui."
)
MOTIVO_SEM_TOKEN = "A conta não tem token: conecte em Cadastros › Redes Sociais."  # noqa: S105
AVISO_PUBLICO = "A resposta ao comentário é PÚBLICA: aparece na publicação, para qualquer pessoa."
AVISO_DIRECT = (
    f"A resposta no Direct é PRIVADA e única: a Meta deixa mandar UMA mensagem por "
    f"comentário, até {RESPOSTA_PRIVADA_DIAS} dias depois dele."
)


def _agora() -> datetime:
    return datetime.now(UTC)


def novo_cliente() -> httpx.AsyncClient:
    """O cliente HTTP da Graph (os testes trocam por um de mentira)."""
    return httpx.AsyncClient(timeout=TIMEOUT)


def _inteiro(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float | str):
        return None
    try:
        return int(v)
    except (ValueError, OverflowError):
        return None


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _data(bruto: Any) -> datetime | None:
    """'2026-09-28T12:00:00+0000' (IG/FB) → UTC; ilegível → None."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = bruto.strip().replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", texto):
        texto = f"{texto[:-2]}:{texto[-2:]}"
    try:
        return _utc(datetime.fromisoformat(texto))
    except ValueError:
        return None


def _iso(quando: datetime | None) -> str | None:
    q = _utc(quando)
    return q.isoformat() if q else None


def _https(url: Any) -> str | None:
    texto = url.strip() if isinstance(url, str) else ""
    return texto if texto.startswith("https://") else None


# ── A Graph API (só o braço HTTP; nunca levanta) ──────────────────────────


@dataclass
class Resposta:
    """O que a Graph respondeu. `erro` é texto de operação, SEM token."""

    dados: dict | None = None
    erro: str | None = None
    code: int | None = None
    subcode: int | None = None
    http: int | None = None
    transiente: bool = False
    # Sem resposta (timeout, rede): num POST, pode ter saído.
    ambiguo: bool = False

    @property
    def ok(self) -> bool:
        return self.dados is not None

    @property
    def sem_permissao(self) -> bool:
        if self.code in (3, 10) or (self.code is not None and 200 <= self.code <= 299):
            return True
        return self.code == 100 and "permission" in (self.erro or "").lower()

    @property
    def token_recusado(self) -> bool:
        return self.code == 190

    @property
    def passageiro(self) -> bool:
        return (
            self.ambiguo
            or self.transiente
            or (self.http or 0) >= 500
            or self.code in _CODIGOS_PASSAGEIROS
            or (self.code or 0) >= 80001
        )


def _url(caminho: str) -> str:
    return f"{meta_client.GRAPH_HOST}/{meta_client.graph_version()}/{caminho.lstrip('/')}"


async def _chamar(
    cliente: httpx.AsyncClient,
    metodo: str,
    caminho: str,
    token: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    json: dict | None = None,
) -> Resposta:
    """Uma chamada à Graph com o token no CABEÇALHO (nunca na URL). Nunca levanta."""
    try:
        r = await cliente.request(
            metodo,
            _url(caminho),
            params=params,
            data=data,
            json=json,
            headers=meta_client._headers(token),
        )
    except Exception as exc:  # noqa: BLE001
        return Resposta(erro=f"a Meta não respondeu ({type(exc).__name__})", ambiguo=True)
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    erro = corpo.get("error") if isinstance(corpo, dict) else None
    if r.status_code < 400 and isinstance(corpo, dict) and not erro:
        return Resposta(dados=corpo, http=r.status_code)
    e = erro if isinstance(erro, dict) else {}
    code = _inteiro(e.get("code"))
    subcode = _inteiro(e.get("error_subcode"))
    marcas = [f"code {code}"] if code is not None else [f"HTTP {r.status_code}"]
    if subcode is not None:
        marcas.append(f"subcode {subcode}")
    msg = str(e.get("message") or "a Meta recusou")[:200]
    return Resposta(
        erro=meta_client._redigir(f"{msg} ({', '.join(marcas)})", token)[:300],
        code=code,
        subcode=subcode,
        http=r.status_code,
        transiente=bool(e.get("is_transient")),
    )


async def _listar(
    cliente: httpx.AsyncClient,
    caminho: str,
    token: str,
    params: dict,
    *,
    paginas: int = 1,
) -> tuple[list[dict], Resposta]:
    """GET paginado pelo cursor `after` (nunca pelo `next`, que pode trazer token)."""
    itens: list[dict] = []
    resposta = Resposta()
    depois: str | None = None
    for _ in range(max(1, paginas)):
        p = dict(params)
        if depois:
            p["after"] = depois
        resposta = await _chamar(cliente, "GET", caminho, token, params=p)
        if not resposta.ok:
            return itens, resposta
        dados = resposta.dados or {}
        itens.extend(x for x in dados.get("data") or [] if isinstance(x, dict) and x.get("id"))
        paging = dados.get("paging") if isinstance(dados.get("paging"), dict) else {}
        depois = (
            (paging.get("cursors") or {}) if isinstance(paging.get("cursors"), dict) else {}
        ).get("after")
        if not depois or not paging.get("next"):
            break
    return itens, resposta


# ── As contas ─────────────────────────────────────────────────────────────


@dataclass
class ContaRede:
    """Uma conta da marca numa rede, com os tokens JÁ decifrados (nunca no repr)."""

    rede_social_id: UUID
    marca: str
    plataforma: str
    conta_id: str | None
    nome: str
    username: str | None = None
    token: str | None = field(default=None, repr=False)
    token_pagina: str | None = field(default=None, repr=False)

    @property
    def segredos(self) -> list[str]:
        return [t for t in (self.token, self.token_pagina) if t]


def _conta(rede: RedeSocial, marca: Marca, tok: RedeSocialToken | None) -> ContaRede:
    segredos: dict = {}
    if tok is not None and tok.token_enc:
        try:
            segredos = decrypt_json(tok.token_enc) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "atendimento_redes_token_ilegivel", rede_id=str(rede.id), err=type(exc).__name__
            )
    username = ((tok.external_username if tok else None) or rede.conta or "").strip().lstrip("@")
    if rede.plataforma == PLATAFORMA_INSTAGRAM:
        nome = f"@{username}" if username else "Instagram"
    else:
        nome = username or rede.conta or "Página do Facebook"
    return ContaRede(
        rede_social_id=rede.id,
        marca=(marca.nome or "").strip().lower(),
        plataforma=rede.plataforma,
        conta_id=(tok.external_user_id or "").strip() or None if tok else None,
        nome=nome,
        username=username or None,
        token=segredos.get("access_token") or None,
        token_pagina=segredos.get("page_access_token") or None,
    )


async def contas_das_marcas(session: AsyncSession) -> list[ContaRede]:
    """O Instagram e a Página de cada marca de `MARCAS_REDES` (com o token, se houver)."""
    linhas = (
        await session.execute(
            select(RedeSocial, Marca, RedeSocialToken)
            .join(Marca, Marca.id == RedeSocial.marca_id)
            .outerjoin(RedeSocialToken, RedeSocialToken.rede_social_id == RedeSocial.id)
            .where(
                RedeSocial.plataforma.in_(PLATAFORMAS_REDE),
                or_(
                    func.lower(Marca.nome).in_(MARCAS_REDES),
                    func.lower(Marca.slug).in_(MARCAS_REDES),
                ),
            )
            .order_by(Marca.nome, RedeSocial.plataforma)
        )
    ).all()
    return [_conta(rede, marca, tok) for rede, marca, tok in linhas]


async def conta_do_canal(session: AsyncSession, canal: AtendimentoCanal | None) -> ContaRede | None:
    """A conta (com token) por trás do canal de comentários."""
    if canal is None or canal.rede_social_id is None:
        return None
    linha = (
        await session.execute(
            select(RedeSocial, Marca, RedeSocialToken)
            .join(Marca, Marca.id == RedeSocial.marca_id)
            .outerjoin(RedeSocialToken, RedeSocialToken.rede_social_id == RedeSocial.id)
            .where(RedeSocial.id == canal.rede_social_id)
        )
    ).first()
    if linha is None:
        return None
    conta = _conta(*linha)
    p = canais_externos.partes(canal.externo_ref)
    if p and p[0] == "rede" and not conta.conta_id:
        conta.conta_id = p[2]
    return conta


# ── A publicação e o comentário, normalizados ─────────────────────────────


@dataclass
class Comentario:
    externo_id: str
    pai: str | None
    autor_id: str | None
    autor_username: str | None
    texto: str | None
    criado_em: datetime | None
    curtidas: int | None
    oculto: bool
    da_marca: bool

    @property
    def tem_autor(self) -> bool:
        return bool(self.autor_id or self.autor_username)


def _comentario_ig(c: dict, conta: ContaRede, pai: str | None) -> Comentario:
    de = c.get("from") if isinstance(c.get("from"), dict) else {}
    autor_id = str(de.get("id") or "").strip() or None
    username = str(c.get("username") or de.get("username") or "").strip().lstrip("@") or None
    da_marca = bool(
        (autor_id and autor_id == conta.conta_id)
        or (username and conta.username and username.lower() == conta.username.lower())
    )
    return Comentario(
        externo_id=str(c["id"]),
        pai=str(c.get("parent_id") or pai or "") or None,
        autor_id=autor_id,
        autor_username=username,
        texto=c.get("text") if isinstance(c.get("text"), str) else None,
        criado_em=_data(c.get("timestamp")),
        curtidas=_inteiro(c.get("like_count")),
        oculto=bool(c.get("hidden")),
        da_marca=da_marca,
    )


def comentarios_ig(itens: Iterable[dict], conta: ContaRede) -> list[Comentario]:
    """O topo e as respostas (`replies`) do `/{media}/comments` do Instagram."""
    out: list[Comentario] = []
    for c in itens:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        out.append(_comentario_ig(c, conta, None))
        respostas = c.get("replies") if isinstance(c.get("replies"), dict) else {}
        for r in respostas.get("data") or []:
            if isinstance(r, dict) and r.get("id"):
                out.append(_comentario_ig(r, conta, str(c["id"])))
    return out


def comentarios_fb(itens: Iterable[dict], conta: ContaRede) -> list[Comentario]:
    """O `/{post}/comments?filter=stream` da Página (o fio vem achatado, com `parent`)."""
    out: list[Comentario] = []
    for c in itens:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        de = c.get("from") if isinstance(c.get("from"), dict) else {}
        autor_id = str(de.get("id") or "").strip() or None
        pai = c.get("parent") if isinstance(c.get("parent"), dict) else {}
        out.append(
            Comentario(
                externo_id=str(c["id"]),
                pai=str(pai.get("id") or "") or None,
                autor_id=autor_id,
                autor_username=str(de.get("name") or "").strip() or None,
                texto=c.get("message") if isinstance(c.get("message"), str) else None,
                criado_em=_data(c.get("created_time")),
                curtidas=_inteiro(c.get("like_count")),
                oculto=bool(c.get("is_hidden")),
                da_marca=bool(autor_id and autor_id == conta.conta_id),
            )
        )
    return out


def midia_ig(m: dict, *, tipo: str) -> dict:
    """Os campos da `atendimento_publicacoes` a partir de uma mídia do IG."""
    formato = str(m.get("media_product_type") or "").upper()
    if formato not in ("REELS", "STORY"):
        formato = str(m.get("media_type") or "").upper() or None
    return {
        "externo_id": str(m["id"]),
        "tipo": tipo,
        "formato": (formato or None) and formato[:24],
        "autor_username": str(m.get("username") or "").strip().lstrip("@") or None,
        "legenda": m.get("caption") if isinstance(m.get("caption"), str) else None,
        "link": _https(m.get("permalink")),
        # Vídeo/Reels: a capa (`thumbnail_url`); foto e carrossel: a própria imagem.
        "miniatura_url": _https(m.get("thumbnail_url")) or _https(m.get("media_url")),
        "publicada_em": _data(m.get("timestamp")),
        "curtidas": _inteiro(m.get("like_count")),
        "total": _inteiro(m.get("comments_count")) or 0,
    }


def _total(no: Any) -> int | None:
    summary = (no or {}).get("summary") if isinstance(no, dict) else None
    return _inteiro(summary.get("total_count")) if isinstance(summary, dict) else None


def post_fb(p: dict) -> dict:
    """Os campos da `atendimento_publicacoes` a partir de um post da Página."""
    return {
        "externo_id": str(p["id"]),
        "tipo": PUBLICACAO_PROPRIA,
        "formato": _FORMATO_FB.get(str(p.get("status_type") or ""), "POST"),
        "autor_username": None,
        "legenda": p.get("message") if isinstance(p.get("message"), str) else None,
        "link": _https(p.get("permalink_url")),
        "miniatura_url": _https(p.get("full_picture")),
        "publicada_em": _data(p.get("created_time")),
        "curtidas": _total(p.get("reactions")),
        "total": _total(p.get("comments")) or 0,
    }


def origem_da_publicacao(pub: AtendimentoPublicacao) -> str:
    """A origem que a lista mostra: "Reels 28/09", "menção · Foto 30/09"."""
    rotulo = ROTULO_FORMATO.get((pub.formato or "").upper(), "Post")
    quando = _utc(pub.publicada_em)
    base = f"{rotulo} {quando.astimezone(BRT):%d/%m}" if quando else rotulo
    return f"menção · {base}" if pub.tipo == PUBLICACAO_MENCAO else base


def pessoa_de(autor_id: str | None, autor_username: str | None) -> str | None:
    """A chave da pessoa na conversa: o id da rede; sem ele, "@username"."""
    if autor_id:
        return autor_id
    return f"@{autor_username}" if autor_username else None


def nome_na_tela(plataforma: str, autor_username: str | None) -> str | None:
    if not autor_username:
        return None
    return f"@{autor_username}" if plataforma == PLATAFORMA_INSTAGRAM else autor_username


def _e_mencao(c: AtendimentoComentario) -> bool:
    return bool((c.dados or {}).get("mencao")) or c.externo_id.startswith("mencao:")


# ── A gravação ────────────────────────────────────────────────────────────


@dataclass
class Leitura:
    """O que a rodada fez numa conta (contagens) e o que a Meta negou."""

    listou: bool = False
    publicacoes: int = 0
    lidas: int = 0
    comentarios: int = 0
    mensagens: int = 0
    mencoes: int = 0
    sem_autor: int = 0
    faltas: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)

    def recusa(self, parte: str, r: Resposta, *, escopo: str | None = None) -> None:
        if r.sem_permissao:
            falta = f" — falta {escopo}" if escopo else ""
            self.faltas.append(f"{parte}: {r.erro}{falta}")
        elif r.token_recusado:
            self.erros.append(
                f"{parte}: a Meta recusou o token ({r.erro}) — troque o token em "
                "Cadastros › Redes Sociais"
            )
        else:
            self.erros.append(f"{parte}: {r.erro}")

    def resumo(self) -> dict:
        return {
            "publicacoes": self.publicacoes,
            "lidas": self.lidas,
            "comentarios": self.comentarios,
            "mensagens": self.mensagens,
            "mencoes": self.mencoes,
            "sem_autor": self.sem_autor,
            "faltas": len(self.faltas),
            "erros": len(self.erros),
        }


async def _publicacoes(
    session: AsyncSession, plataforma: str, conta_id: str, ids: list[str]
) -> dict[str, AtendimentoPublicacao]:
    if not ids:
        return {}
    linhas = (
        await session.execute(
            select(AtendimentoPublicacao).where(
                AtendimentoPublicacao.plataforma == plataforma,
                AtendimentoPublicacao.conta_id == conta_id,
                AtendimentoPublicacao.externo_id.in_(ids),
            )
        )
    ).scalars()
    return {p.externo_id: p for p in linhas}


async def gravar_publicacao(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    d: dict,
    agora: datetime,
) -> AtendimentoPublicacao:
    """Acha ou cria a publicação e atualiza o retrato (sem a contagem lida). Não commita."""
    chave = {
        "plataforma": conta.plataforma,
        "conta_id": str(conta.conta_id),
        "externo_id": d["externo_id"][:128],
    }
    await session.execute(
        pg_insert(AtendimentoPublicacao)
        .values(id=uuid4(), canal_id=canal.id, tipo=d["tipo"], dados={}, **chave)
        .on_conflict_do_nothing(index_elements=["plataforma", "conta_id", "externo_id"])
    )
    pub = (
        await session.execute(
            select(AtendimentoPublicacao)
            .where(*(getattr(AtendimentoPublicacao, k) == v for k, v in chave.items()))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    pub.canal_id = canal.id
    pub.tipo = d["tipo"]
    pub.formato = d.get("formato")
    for campo in ("autor_username", "legenda", "link", "publicada_em", "curtidas"):
        if d.get(campo) is not None:
            setattr(pub, campo, d[campo])
    if d.get("miniatura_url"):
        pub.miniatura_url = d["miniatura_url"]
        pub.miniatura_lida_em = agora
    pub.dados = {**(pub.dados or {}), "comentarios_na_rede": d.get("total", 0)}
    await session.flush()
    return pub


def _precisa_ler(pub: AtendimentoPublicacao, total: int, agora: datetime) -> bool:
    """Ler os comentários desta publicação agora? (cada leitura = 1 GET ou mais)"""
    if total <= 0:
        return False
    if pub.comentarios is None or pub.comentarios != total:
        return True
    lida = gravar._iso_utc((pub.dados or {}).get("comentarios_lidos_em"))
    recente = (_utc(pub.publicada_em) or agora) >= agora - timedelta(days=MIDIA_DIAS_LEITURA)
    return recente and (lida is None or agora - lida >= RELER_PUBLICACAO)


async def _upsert_comentario(
    session: AsyncSession,
    pub: AtendimentoPublicacao,
    plataforma: str,
    c: Comentario,
    *,
    dados: dict | None = None,
) -> AtendimentoComentario:
    await session.execute(
        pg_insert(AtendimentoComentario)
        .values(
            id=uuid4(),
            publicacao_id=pub.id,
            plataforma=plataforma,
            externo_id=c.externo_id[:128],
            dados=dados or {},
        )
        .on_conflict_do_nothing(index_elements=["plataforma", "externo_id"])
    )
    linha = (
        await session.execute(
            select(AtendimentoComentario)
            .where(
                AtendimentoComentario.plataforma == plataforma,
                AtendimentoComentario.externo_id == c.externo_id[:128],
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    linha.publicacao_id = pub.id
    linha.pai_externo_id = (c.pai or None) and c.pai[:128]
    linha.autor_id = (c.autor_id or None) and c.autor_id[:128]
    linha.autor_username = c.autor_username
    linha.da_marca = c.da_marca
    linha.texto = gravar.sem_nul(c.texto)
    linha.criado_em = c.criado_em
    linha.curtidas = c.curtidas
    linha.oculto = c.oculto
    linha.eh_pergunta = (not c.da_marca) and e_pergunta(c.texto)
    if dados:
        linha.dados = {**(linha.dados or {}), **dados}
    return linha


async def conversa_da_pessoa(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    pub: AtendimentoPublicacao,
    autor_id: str | None,
    autor_username: str | None,
) -> AtendimentoConversa:
    """A conversa (pessoa × publicação); cria se não há. Não commita."""
    pessoa = pessoa_de(autor_id, autor_username)
    if pessoa is None:
        raise ValueError("comentário sem autor não tem conversa")
    conversa, _ = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=None,
        plataforma=conta.plataforma,
        canal_nome=CANAL_COMENTARIO,
        externo_id=f"{pub.externo_id}:{pessoa}",
        conta=conta.nome,
        comprador_id=pessoa,
        comprador_nome=nome_na_tela(conta.plataforma, autor_username),
        anuncio_id=pub.externo_id,
        anuncio_titulo=origem_da_publicacao(pub),
        dados={
            "publicacao_id": str(pub.id),
            "tipo": "mencao" if pub.tipo == PUBLICACAO_MENCAO else "comentario",
        },
    )
    return conversa


def _destinatario(
    resposta: AtendimentoComentario, fio: dict[str, AtendimentoComentario]
) -> AtendimentoComentario | None:
    """De quem é a resposta da marca: do @ citado no começo (se está no mesmo fio), senão do pai."""
    pai = fio.get(resposta.pai_externo_id or "")
    citado = re.match(r"\s*@([\w.]+)", resposta.texto or "")
    if citado and pai is not None:
        nome = citado.group(1).lower()
        raiz = pai.pai_externo_id or pai.externo_id
        for c in fio.values():
            mesmo_fio = c.externo_id == raiz or c.pai_externo_id == raiz
            if (
                mesmo_fio
                and not c.da_marca
                and (c.autor_username or "").lower() == nome
                and (c.criado_em or datetime.min.replace(tzinfo=UTC))
                <= (resposta.criado_em or datetime.max.replace(tzinfo=UTC))
            ):
                return c
    return pai


def _texto_do_comentario(c: AtendimentoComentario) -> str:
    return c.texto or ("(menção sem legenda)" if _e_mencao(c) else "(comentário sem texto)")


async def atualizar_pergunta(session: AsyncSession, conversa: AtendimentoConversa) -> None:
    """A pergunta ainda sem resposta da marca dá o prazo curto (RF7: só ordena)."""
    linhas = (
        (
            await session.execute(
                select(AtendimentoComentario)
                .where(AtendimentoComentario.conversa_id == conversa.id)
                .order_by(AtendimentoComentario.criado_em.asc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    ultima_marca = max(
        (_utc(c.criado_em) for c in linhas if c.da_marca and c.criado_em is not None), default=None
    )
    pergunta = next(
        (
            c
            for c in linhas
            if not c.da_marca
            and c.eh_pergunta
            and c.criado_em is not None
            and (ultima_marca is None or _utc(c.criado_em) > ultima_marca)
        ),
        None,
    )
    prazo = (
        _iso(_utc(pergunta.criado_em) + timedelta(hours=SLA_PERGUNTA_COMENTARIO_HORAS))
        if pergunta is not None
        else None
    )
    dados = dict(conversa.dados or {})
    eh = pergunta is not None
    if dados.get("eh_pergunta") != eh or dados.get(CHAVE_PRAZO_PLATAFORMA) != prazo:
        conversa.dados = {**dados, "eh_pergunta": eh, CHAVE_PRAZO_PLATAFORMA: prazo}
    gravar.recalcular(conversa)


async def gravar_comentarios(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    pub: AtendimentoPublicacao,
    comentarios: list[Comentario],
    agora: datetime,
    leitura: Leitura,
) -> None:
    """Os comentários de UMA publicação: linhas, conversas e mensagens. Idempotente; não commita."""
    janela = agora - timedelta(days=MIDIA_DIAS_LEITURA)
    lidas: list[AtendimentoComentario] = []
    for c in comentarios:
        if not c.tem_autor:
            # Sem o autor não dá para saber de quem é (nem se é da marca).
            leitura.sem_autor += 1
            continue
        lidas.append(await _upsert_comentario(session, pub, conta.plataforma, c))
    await session.flush()
    leitura.comentarios += len(lidas)
    fio = {
        c.externo_id: c
        for c in (
            await session.execute(
                select(AtendimentoComentario).where(AtendimentoComentario.publicacao_id == pub.id)
            )
        ).scalars()
    }
    tocadas: dict[UUID, AtendimentoConversa] = {}
    ordem = sorted(lidas, key=lambda c: _utc(c.criado_em) or agora)
    # 1) A pessoa: a conversa dela nesta publicação (só o que é da janela).
    for c in ordem:
        if c.da_marca:
            continue
        if c.conversa_id is None and (_utc(c.criado_em) or agora) < janela:
            continue
        conversa = (
            tocadas.get(c.conversa_id) if c.conversa_id else None
        ) or await conversa_da_pessoa(session, canal, conta, pub, c.autor_id, c.autor_username)
        tocadas[conversa.id] = conversa
        c.conversa_id = conversa.id
        _, criada = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=c.externo_id,
            autor=AUTOR_CLIENTE,
            texto=_texto_do_comentario(c),
            enviada_em=c.criado_em,
            tipo="texto",
            anexos=[],
            payload={
                "comentario": True,
                "oculto": c.oculto,
                **({"pai": c.pai_externo_id} if c.pai_externo_id else {}),
            },
            origem=None,
        )
        leitura.mensagens += int(criada)
    # 2) A marca: resposta na conversa de quem foi respondido (nunca conversa nova).
    for c in ordem:
        if not c.da_marca:
            continue
        alvo = _destinatario(c, fio) if c.conversa_id is None else None
        conversa_id = c.conversa_id or (alvo.conversa_id if alvo is not None else None)
        if conversa_id is None:
            continue  # comentário solto da marca, ou resposta a comentário fora da janela
        conversa = tocadas.get(conversa_id) or await session.get(AtendimentoConversa, conversa_id)
        if conversa is None:
            continue
        tocadas[conversa.id] = conversa
        c.conversa_id = conversa.id
        _, criada = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=c.externo_id,
            autor=AUTOR_LOJA,
            texto=c.texto,
            enviada_em=c.criado_em,
            tipo="texto",
            anexos=[],
            payload={"comentario": True, "resposta_publica": True},
            origem=None,
        )
        leitura.mensagens += int(criada)
    await session.flush()
    for conversa in tocadas.values():
        await atualizar_pergunta(session, conversa)
    await session.flush()


async def gravar_mencao(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    pub: AtendimentoPublicacao,
    leitura: Leitura,
) -> None:
    """A menção (o post de outra pessoa que marcou a marca) vira a conversa dela. Não commita."""
    if not pub.autor_username:
        leitura.sem_autor += 1
        return
    c = Comentario(
        # A conta entra no id: a mesma mídia pode marcar duas marcas nossas
        # (o UNIQUE do comentário é por plataforma + id).
        externo_id=f"mencao:{conta.conta_id}:{pub.externo_id}",
        pai=None,
        autor_id=None,
        autor_username=pub.autor_username,
        texto=pub.legenda,
        criado_em=pub.publicada_em,
        curtidas=pub.curtidas,
        oculto=False,
        da_marca=False,
    )
    linha = await _upsert_comentario(session, pub, conta.plataforma, c, dados={"mencao": True})
    conversa = await conversa_da_pessoa(session, canal, conta, pub, None, pub.autor_username)
    linha.conversa_id = conversa.id
    _, criada = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=c.externo_id,
        autor=AUTOR_CLIENTE,
        texto=_texto_do_comentario(linha),
        enviada_em=pub.publicada_em,
        tipo="texto",
        anexos=[],
        payload={"mencao": True},
        origem=None,
    )
    if criada and not linha.eh_pergunta:
        # A resposta da marca à menção (no post de OUTRA pessoa) nunca volta
        # para cá — a Meta não deixa ler aqueles comentários: sem isto a
        # menção ficaria esperando para sempre. Só na criação: quem desfizer
        # à mão ("precisa de resposta") não é desfeito pela leitura seguinte.
        conversa.sem_resposta_necessaria = True
    leitura.mencoes += 1
    leitura.mensagens += int(criada)
    await session.flush()
    await atualizar_pergunta(session, conversa)


def _comentarios_sem_escopo(comentarios: list[Comentario]) -> bool:
    """A Meta omitiu o autor de TODA pessoa: é o escopo que falta, não dado.

    Sem `instagram_manage_comments` o IG manda o texto sem `username`/`from`
    (medido em 02/10) — mas a resposta da PRÓPRIA marca pode vir com o autor
    (é conteúdo dela). Então: há comentário sem autor e nenhum de pessoa com.
    """
    return any(not c.tem_autor for c in comentarios) and not any(
        c.tem_autor and not c.da_marca for c in comentarios
    )


# ── A leitura de cada rede ────────────────────────────────────────────────


async def _ler_comentarios_das(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    cliente: httpx.AsyncClient,
    itens: list[dict],
    agora: datetime,
    leitura: Leitura,
    *,
    token: str,
    para_publicacao,
    caminho_comentarios: str,
    params_comentarios: dict,
    normalizar,
    escopo_autor: str,
) -> None:
    existentes = await _publicacoes(
        session, conta.plataforma, str(conta.conta_id), [str(i["id"]) for i in itens]
    )
    leituras = 0
    for item in itens:
        d = para_publicacao(item)
        pub = existentes.get(d["externo_id"])
        if d["total"] <= 0 and pub is None:
            continue
        pub = await gravar_publicacao(session, canal, conta, d, agora)
        leitura.publicacoes += 1
        if d["total"] <= 0:
            pub.comentarios = 0
        if not _precisa_ler(pub, d["total"], agora) or leituras >= MAX_LEITURAS_POR_CONTA:
            await session.commit()
            continue
        leituras += 1
        brutos, r = await _listar(
            cliente,
            caminho_comentarios.format(id=d["externo_id"]),
            token,
            params_comentarios,
            paginas=MAX_PAGINAS_COMENTARIOS,
        )
        if not r.ok:
            leitura.recusa("comentários", r, escopo=escopo_autor)
            await session.commit()
            if r.sem_permissao or r.token_recusado:
                return
            continue
        comentarios = normalizar(brutos, conta)
        sem_autor = sum(1 for c in comentarios if not c.tem_autor)
        if _comentarios_sem_escopo(comentarios):
            # Medido em 02/10: com o token de hoje o IG devolve o texto SEM
            # `username`/`from`. Nada é gravado (a publicação fica sem a
            # contagem: relê quando o token novo chegar) e a conta para
            # aqui (as outras publicações dariam o mesmo).
            leitura.sem_autor += sem_autor
            leitura.faltas.append(
                f"comentários vêm sem o autor (username/from) — falta {escopo_autor}"
            )
            await session.commit()
            return
        await gravar_comentarios(session, canal, conta, pub, comentarios, agora, leitura)
        if sem_autor:
            # Só PARTE veio sem o autor: o resto entra, mas os sem autor
            # sumiriam calados com o canal "ok". Vira `sem_escopo` com a
            # contagem, e a publicação fica SEM a contagem lida: é relida na
            # rodada seguinte (e quando o token novo chegar).
            leitura.faltas.append(
                f"{sem_autor} comentário(s) vieram sem o autor (username/from) e ficaram "
                f"de fora — se não foram apagados, falta {escopo_autor}"
            )
            pub.comentarios = None
        else:
            pub.comentarios = d["total"]
            pub.dados = {**(pub.dados or {}), "comentarios_lidos_em": _iso(agora)}
        leitura.lidas += 1
        await session.commit()


async def _ler_instagram(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    cliente: httpx.AsyncClient,
    agora: datetime,
    leitura: Leitura,
) -> None:
    token = str(conta.token)
    midias, r = await _listar(
        cliente,
        f"{conta.conta_id}/media",
        token,
        {"fields": CAMPOS_MIDIA_IG, "limit": LIMITE_MIDIAS},
    )
    if not r.ok:
        leitura.recusa("publicações (/media)", r, escopo="instagram_basic")
        return
    leitura.listou = True
    await _ler_comentarios_das(
        session,
        canal,
        conta,
        cliente,
        midias,
        agora,
        leitura,
        token=token,
        para_publicacao=lambda m: midia_ig(m, tipo=PUBLICACAO_PROPRIA),
        caminho_comentarios="{id}/comments",
        params_comentarios={"fields": CAMPOS_COMENTARIO_IG, "limit": PAGINA_COMENTARIOS},
        normalizar=comentarios_ig,
        escopo_autor=ESCOPO_COMENTARIOS_IG,
    )
    # As menções: o post de outra pessoa que marcou a conta.
    tags, r = await _listar(
        cliente, f"{conta.conta_id}/tags", token, {"fields": CAMPOS_MIDIA_IG, "limit": LIMITE_TAGS}
    )
    if not r.ok:
        leitura.recusa("menções (/tags)", r, escopo=ESCOPO_COMENTARIOS_IG)
        return
    janela = agora - timedelta(days=MIDIA_DIAS_LEITURA)
    for m in tags:
        d = midia_ig(m, tipo=PUBLICACAO_MENCAO)
        if (d["publicada_em"] or agora) < janela:
            continue
        pub = await gravar_publicacao(session, canal, conta, d, agora)
        pub.comentarios = d["total"]
        leitura.publicacoes += 1
        await gravar_mencao(session, canal, conta, pub, leitura)
        await session.commit()


async def _token_da_pagina(
    conta: ContaRede, cliente: httpx.AsyncClient, leitura: Leitura | None = None
) -> str | None:
    """O token DA PÁGINA (o do sistema dá 190/2069032 nos posts — medido em 02/10)."""
    if conta.token_pagina:
        return conta.token_pagina
    if not conta.token:
        return None
    r = await _chamar(
        cliente, "GET", str(conta.conta_id), conta.token, params={"fields": "access_token"}
    )
    token = (r.dados or {}).get("access_token") if r.ok else None
    if not token and leitura is not None:
        leitura.recusa("token da Página", r, escopo="pages_show_list")
    if token:
        conta.token_pagina = str(token)
    return conta.token_pagina


async def _ler_facebook(
    session: AsyncSession,
    canal: AtendimentoCanal,
    conta: ContaRede,
    cliente: httpx.AsyncClient,
    agora: datetime,
    leitura: Leitura,
) -> None:
    token = await _token_da_pagina(conta, cliente, leitura)
    if not token:
        if not leitura.faltas and not leitura.erros:
            leitura.erros.append(SEM_TOKEN_PAGINA)
        return
    posts, r = await _listar(
        cliente,
        f"{conta.conta_id}/posts",
        token,
        {"fields": CAMPOS_POST_FB, "limit": LIMITE_POSTS_FB},
    )
    if not r.ok:
        leitura.recusa("posts da Página (/posts)", r, escopo="pages_read_engagement")
        return
    leitura.listou = True
    await _ler_comentarios_das(
        session,
        canal,
        conta,
        cliente,
        posts,
        agora,
        leitura,
        token=token,
        para_publicacao=post_fb,
        caminho_comentarios="{id}/comments",
        params_comentarios={
            "fields": CAMPOS_COMENTARIO_FB,
            "filter": "stream",
            "order": "chronological",
            "limit": PAGINA_COMENTARIOS,
        },
        normalizar=comentarios_fb,
        escopo_autor=ESCOPO_CONTEUDO_FB,
    )


def _juntar(partes: list[str]) -> str:
    return "; ".join(dict.fromkeys(partes))[:900]


def aplicar_status(canal: AtendimentoCanal, leitura: Leitura, agora: datetime) -> None:
    """O status que a aba Lojas mostra (o `sem_escopo` diz o que falta)."""
    if leitura.listou:
        canal.ultimo_ok_em = agora
    if leitura.faltas:
        canal.status = STATUS_SEM_ESCOPO
        canal.ultimo_erro = _juntar(leitura.faltas + leitura.erros)
        canal.ultimo_erro_em = agora
    elif leitura.erros:
        canal.status = STATUS_ERRO
        canal.ultimo_erro = _juntar(leitura.erros)
        canal.ultimo_erro_em = agora
    else:
        canal.status = STATUS_OK
        canal.ultimo_erro = None
    canais_externos.com_externo(canal, leitura={"em": _iso(agora), **leitura.resumo()})


async def ler_conta(conta: ContaRede, *, cliente: httpx.AsyncClient, agora: datetime) -> dict:
    """Uma conta, na SUA sessão. Nunca levanta: a falha vira o status do canal."""
    if not conta.conta_id:
        logger.info("atendimento_redes_conta_sem_id", rede_id=str(conta.rede_social_id))
        return {"pulada": "sem_conta"}
    ref = canais_externos.ref_da_rede(conta.plataforma, conta.conta_id)
    leitura = Leitura()
    async with _db.SessionLocal() as session:
        canal_id: UUID | None = None
        try:
            canal = await canais_externos.garantir_canal(
                session,
                externo_ref=ref,
                plataforma=conta.plataforma,
                canal=CANAL_COMENTARIO,
                nome=conta.nome,
                rede_social_id=conta.rede_social_id,
            )
            canal_id = canal.id
            await session.commit()
            if not conta.token:
                leitura.erros.append("conta sem token: conecte em Cadastros › Redes Sociais")
            elif conta.plataforma == PLATAFORMA_INSTAGRAM:
                await _ler_instagram(session, canal, conta, cliente, agora, leitura)
            else:
                await _ler_facebook(session, canal, conta, cliente, agora, leitura)
            canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
            aplicar_status(canal, leitura, agora)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.error(
                "atendimento_redes_conta_falhou",
                rede_id=str(conta.rede_social_id),
                plataforma=conta.plataforma,
                err=type(exc).__name__,
            )
            if canal_id is not None:
                try:
                    canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
                    if canal is not None:
                        canal.status = STATUS_ERRO
                        canal.ultimo_erro = f"a leitura falhou no DaVinci ({type(exc).__name__})"
                        canal.ultimo_erro_em = agora
                        await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
            return {"erro": type(exc).__name__}
    logger.info(
        "atendimento_redes_conta_lida",
        rede_id=str(conta.rede_social_id),
        plataforma=conta.plataforma,
        **leitura.resumo(),
    )
    return {"status": canal.status, **leitura.resumo()}


async def rodada(
    *, cliente: httpx.AsyncClient | None = None, agora: datetime | None = None
) -> dict:
    """As contas das marcas, uma por uma (a falha de uma não para as outras)."""
    agora = agora or _agora()
    async with _db.SessionLocal() as session:
        contas = await contas_das_marcas(session)
    proprio = cliente is None
    http = cliente or novo_cliente()
    resumo: dict[str, Any] = {"contas": len(contas), "por_conta": {}}
    try:
        for conta in contas:
            chave = f"{conta.plataforma}:{conta.conta_id or conta.rede_social_id}"
            resumo["por_conta"][chave] = await ler_conta(conta, cliente=http, agora=agora)
    finally:
        if proprio:
            await http.aclose()
    return resumo


async def _pegar_trava() -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        # Sem Redis, roda sem trava: a gravação é idempotente (UNIQUE + ids).
        logger.warning("atendimento_redes_trava_indisponivel", err=type(exc).__name__)
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
        logger.warning("atendimento_redes_trava_soltar_falhou")


async def atendimento_redes(
    ctx: Any = None,
    *,
    cliente: httpx.AsyncClient | None = None,
    agora: datetime | None = None,
) -> dict | None:
    """O CRON (worker `atendimento_redes`, :09/:24/:39/:54, `timeout=840`).

    Só com `atendimento_leitura_ativa` E `atendimento_redes_ativa` (os dois
    nascem desligados aqui; o worker confere também). Uma rodada por vez.
    SÓ GET. Nunca levanta: o erro vira log (só o tipo) e `None`.
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_redes_ativa):
        return None
    pegou, token = await _pegar_trava()
    if not pegou:
        logger.info("atendimento_redes_ocupado")
        return {"pulado": "ocupado"}
    try:
        resumo = await rodada(cliente=cliente, agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.error("atendimento_redes_falhou", err=type(exc).__name__)
        return None
    finally:
        await _soltar_trava(token)
    logger.info("atendimento_redes_tick", contas=resumo.get("contas"))
    return resumo


# ── A miniatura (a URL expira) ────────────────────────────────────────────


async def renovar_miniatura(
    session: AsyncSession,
    pub: AtendimentoPublicacao,
    *,
    cliente: httpx.AsyncClient | None = None,
    agora: datetime | None = None,
) -> bool:
    """Relê a miniatura vencida (1 GET), só com a leitura ligada. Nunca levanta.

    A referência (o id da mídia) é o que vale; a URL da CDN é um retrato que
    expira — a tela a pede de novo quando é velha. Não commita.

    Depende só de `atendimento_leitura_ativa` (ligada em produção), NÃO de
    `atendimento_redes_ativa`: este GET roda no processo da API (ao abrir o
    cartão), e ligar as redes recriando só o worker deixaria a API com o
    interruptor velho (o `get_settings` é lido na subida) — a miniatura nunca
    mais se renovaria. Só existe publicação para renovar depois que o cron
    das redes rodou.
    """
    s = get_settings()
    agora = agora or _agora()
    lida = _utc(pub.miniatura_lida_em)
    if not s.atendimento_leitura_ativa:
        return False
    if lida is not None and agora - lida < MINIATURA_VALIDADE:
        return False
    canal = await session.get(AtendimentoCanal, pub.canal_id) if pub.canal_id else None
    conta = await conta_do_canal(session, canal)
    if conta is None or not conta.token:
        return False
    proprio = cliente is None
    http = cliente or novo_cliente()
    try:
        if pub.plataforma == PLATAFORMA_INSTAGRAM:
            r = await _chamar(
                http,
                "GET",
                pub.externo_id,
                conta.token,
                params={"fields": "media_url,thumbnail_url"},
            )
            d = r.dados or {}
            url = _https(d.get("thumbnail_url")) or _https(d.get("media_url"))
        else:
            token = await _token_da_pagina(conta, http)
            r = (
                await _chamar(http, "GET", pub.externo_id, token, params={"fields": "full_picture"})
                if token
                else Resposta()
            )
            url = _https((r.dados or {}).get("full_picture"))
    finally:
        if proprio:
            await http.aclose()
    if not url:
        return False
    pub.miniatura_url = url
    pub.miniatura_lida_em = agora
    return True


# ── O que a tela mostra e o que dá para fazer ─────────────────────────────


def motivo_resposta(
    c: AtendimentoComentario,
    canal: AtendimentoCanal | None,
    *,
    envio_ativo: bool,
    privado: bool,
    agora: datetime,
) -> tuple[str, str] | None:
    """Por que NÃO dá para responder este comentário agora (code, texto); None = dá.

    A ordem é a da explicação: primeiro o sistema (envio desligado), depois a
    conta (modo), depois o comentário. É a MESMA régua do POST e da tela.
    """
    if not envio_ativo:
        return enviar.RECUSA_ENVIO_DESLIGADO, MOTIVO_ENVIO_DESLIGADO
    if canal is None or canal.modo not in MODOS_QUE_ENVIAM:
        return enviar.RECUSA_CANAL_EM_OBSERVACAO, MOTIVO_OBSERVAR
    if c.da_marca:
        return RECUSA_COMENTARIO_DA_MARCA, "Este comentário é da própria marca."
    if privado:
        if _e_mencao(c):
            return (
                RECUSA_SEM_RESPOSTA_PRIVADA,
                "Menção não tem resposta privada pela API: responda em público ou pelo app.",
            )
        criado = _utc(c.criado_em)
        if criado is None or agora - criado > timedelta(days=RESPOSTA_PRIVADA_DIAS):
            return (
                RECUSA_PRAZO_PRIVADA,
                f"A resposta privada só sai até {RESPOSTA_PRIVADA_DIAS} dias depois do "
                "comentário (regra da Meta).",
            )
        if (c.dados or {}).get("resposta_privada_em"):
            return (
                RECUSA_PRIVADA_JA_ENVIADA,
                "A Meta deixa mandar UMA resposta privada por comentário — esta já foi.",
            )
    elif c.oculto:
        return (
            RECUSA_COMENTARIO_OCULTO,
            "Comentário oculto não recebe resposta pública (regra da Meta): mostre-o antes.",
        )
    return None


def motivo_ocultar(
    c: AtendimentoComentario,
    canal: AtendimentoCanal | None,
    *,
    envio_ativo: bool,
    esconder: bool = True,
) -> tuple[str, str] | None:
    """Por que NÃO dá para ocultar (ou mostrar de novo) agora; None = dá."""
    if not envio_ativo:
        return enviar.RECUSA_ENVIO_DESLIGADO, MOTIVO_ENVIO_DESLIGADO
    if canal is None or canal.modo not in MODOS_QUE_ENVIAM:
        return enviar.RECUSA_CANAL_EM_OBSERVACAO, MOTIVO_OBSERVAR
    if _e_mencao(c):
        return RECUSA_SEM_OCULTAR, "A menção é publicação de outra pessoa: não dá para ocultar."
    if c.da_marca:
        return RECUSA_SEM_OCULTAR, "Comentário da própria marca não se oculta (fica visível)."
    if esconder and c.oculto:
        return RECUSA_SEM_OCULTAR, "Este comentário já está oculto."
    if not esconder and not c.oculto:
        return RECUSA_SEM_OCULTAR, "Este comentário não está oculto."
    return None


def comentario_para_tela(
    c: AtendimentoComentario,
    *,
    conversa_id: UUID | None,
    canal: AtendimentoCanal | None,
    envio_ativo: bool,
    agora: datetime,
    respostas: list[dict] | None = None,
) -> dict:
    publico = motivo_resposta(c, canal, envio_ativo=envio_ativo, privado=False, agora=agora)
    direct = motivo_resposta(c, canal, envio_ativo=envio_ativo, privado=True, agora=agora)
    oculta = motivo_ocultar(c, canal, envio_ativo=envio_ativo, esconder=not c.oculto)
    dados = c.dados or {}
    respostas = respostas or []
    return {
        "id": c.id,
        "externo_id": c.externo_id,
        "pai_externo_id": c.pai_externo_id,
        "autor_username": c.autor_username,
        "da_marca": c.da_marca,
        "texto": c.texto,
        "criado_em": _utc(c.criado_em),
        "curtidas": c.curtidas,
        "oculto": c.oculto,
        "eh_pergunta": c.eh_pergunta,
        "mencao": _e_mencao(c),
        "desta_conversa": conversa_id is not None and c.conversa_id == conversa_id,
        "respondido": any(r.get("da_marca") for r in respostas)
        or bool(dados.get("resposta_privada_em")),
        "resposta_privada_em": gravar._iso_utc(dados.get("resposta_privada_em")),
        "pode_responder": publico is None,
        "motivo_responder": publico[1] if publico else None,
        "pode_direct": direct is None,
        "motivo_direct": direct[1] if direct else None,
        "pode_ocultar": oculta is None,
        "motivo_ocultar": oculta[1] if oculta else None,
        "respostas": respostas,
    }


async def publicacao_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoPublicacao | None:
    bruto = (conversa.dados or {}).get("publicacao_id")
    try:
        pub = await session.get(AtendimentoPublicacao, UUID(str(bruto))) if bruto else None
    except ValueError:
        pub = None
    if pub is None and conversa.anuncio_id:
        pub = (
            await session.execute(
                select(AtendimentoPublicacao)
                .where(
                    AtendimentoPublicacao.plataforma == conversa.plataforma,
                    AtendimentoPublicacao.externo_id == conversa.anuncio_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return pub


async def painel(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    envio_ativo: bool,
    agora: datetime | None = None,
) -> dict:
    """O painel da direita: a publicação, os comentários (em fio) e o que dá para fazer."""
    agora = agora or _agora()
    canal = await session.get(AtendimentoCanal, conversa.canal_id) if conversa.canal_id else None
    pub = await publicacao_da_conversa(session, conversa)
    interacoes = 0
    if conversa.comprador_id:
        mesma_pessoa = [AtendimentoConversa.comprador_id == conversa.comprador_id]
        nome = (conversa.comprador_nome or "").strip()
        if conversa.plataforma == PLATAFORMA_INSTAGRAM and nome.startswith("@"):
            # A menção guarda a pessoa pelo "@username" (o /tags não traz o
            # id) e o comentário pelo id: o @ na tela liga as duas.
            mesma_pessoa.append(func.lower(AtendimentoConversa.comprador_nome) == nome.lower())
        interacoes = int(
            await session.scalar(
                select(func.count(AtendimentoConversa.id)).where(
                    AtendimentoConversa.plataforma == conversa.plataforma,
                    or_(*mesma_pessoa),
                    AtendimentoConversa.id != conversa.id,
                )
            )
            or 0
        )
    base: dict[str, Any] = {
        "publicacao": None,
        "comentarios": [],
        "total_comentarios": 0,
        "truncado": False,
        "interacoes_anteriores": interacoes,
        "canal_status": canal.status if canal else None,
        "canal_erro": (canal.ultimo_erro or None) if canal and canal.status != STATUS_OK else None,
        "envio": {
            "ativo": envio_ativo,
            "modo_canal": canal.modo if canal else None,
            "motivo": MOTIVO_ENVIO_DESLIGADO
            if not envio_ativo
            else (MOTIVO_OBSERVAR if canal is None or canal.modo not in MODOS_QUE_ENVIAM else None),
            "aviso_publico": AVISO_PUBLICO,
            "aviso_direct": AVISO_DIRECT,
            "limite_publico": limite_caracteres(conversa.plataforma, CANAL_COMENTARIO),
            "limite_direct": LIMITE_DIRECT,
            "resposta_privada_dias": RESPOSTA_PRIVADA_DIAS,
        },
    }
    if pub is None:
        return base
    total = int(
        await session.scalar(
            select(func.count(AtendimentoComentario.id)).where(
                AtendimentoComentario.publicacao_id == pub.id
            )
        )
        or 0
    )
    linhas = (
        (
            await session.execute(
                select(AtendimentoComentario)
                .where(AtendimentoComentario.publicacao_id == pub.id)
                .order_by(AtendimentoComentario.criado_em.desc().nulls_last())
                .limit(MAX_TELA)
            )
        )
        .scalars()
        .all()
    )
    ids = {c.externo_id for c in linhas}
    filhos: dict[str, list[AtendimentoComentario]] = {}
    for c in linhas:
        if c.pai_externo_id and c.pai_externo_id in ids:
            filhos.setdefault(c.pai_externo_id, []).append(c)

    def item(c: AtendimentoComentario, respostas: list[dict] | None = None) -> dict:
        return comentario_para_tela(
            c,
            conversa_id=conversa.id,
            canal=canal,
            envio_ativo=envio_ativo,
            agora=agora,
            respostas=respostas,
        )

    topo = [c for c in linhas if not (c.pai_externo_id and c.pai_externo_id in ids)]
    comentarios = []
    for c in topo:
        respostas = sorted(filhos.get(c.externo_id, []), key=lambda r: _utc(r.criado_em) or agora)
        comentarios.append(item(c, [item(r) for r in respostas]))
    lida = _utc(pub.miniatura_lida_em)
    base.update(
        publicacao={
            "id": pub.id,
            "plataforma": pub.plataforma,
            "conta_id": pub.conta_id,
            "conta_nome": conversa.conta,
            "externo_id": pub.externo_id,
            "tipo": pub.tipo,
            "formato": pub.formato,
            "origem": origem_da_publicacao(pub),
            "autor_username": pub.autor_username,
            "legenda": pub.legenda,
            "link": _https(pub.link),
            "miniatura_url": _https(pub.miniatura_url),
            "miniatura_lida_em": lida,
            "miniatura_vencida": lida is None or agora - lida >= MINIATURA_VALIDADE,
            "publicada_em": _utc(pub.publicada_em),
            "curtidas": pub.curtidas,
            "comentarios": pub.comentarios
            if pub.comentarios is not None
            else _inteiro((pub.dados or {}).get("comentarios_na_rede")),
        },
        comentarios=comentarios,
        total_comentarios=total,
        truncado=total > len(linhas),
    )
    return base


# ── As ações (bloqueadas com o envio desligado) ───────────────────────────


def preparar_texto(texto: str | None, *, plataforma: str, privado: bool) -> str:
    """Normaliza e confere o tamanho. Rede social da própria marca não tem a régua
    de "contato fora da loja" dos marketplaces (link do site e @ são normais aqui)."""
    from app.services.atendimento import validador

    normalizado = validador.normalizar(texto or "", plataforma=plataforma, canal=CANAL_COMENTARIO)
    if not normalizado.strip():
        raise EnvioRecusado(enviar.RECUSA_TEXTO_INVALIDO, ["A resposta está vazia."])
    limite = LIMITE_DIRECT if privado else limite_caracteres(plataforma, CANAL_COMENTARIO)
    if len(normalizado) > limite:
        raise EnvioRecusado(
            enviar.RECUSA_TEXTO_INVALIDO,
            [f"passa do limite de {limite} caracteres ({len(normalizado)})"],
        )
    return normalizado


def _antes_de_tudo() -> None:
    """As travas do sistema, ANTES de qualquer leitura ou escrita."""
    s = get_settings()
    if not s.atendimento_envio_ativo:
        raise EnvioRecusado(enviar.RECUSA_ENVIO_DESLIGADO, MOTIVO_ENVIO_DESLIGADO)
    if s.atendimento_simulador and s.is_prod:
        logger.error("atendimento_redes_simulador_em_producao")
        raise EnvioRecusado(
            enviar.RECUSA_SIMULADOR_EM_PRODUCAO,
            "O simulador de envio (só para teste local) está ligado em produção: nada foi "
            "enviado. Desligue ATENDIMENTO_SIMULADOR.",
        )


def bloqueio_do_envio() -> EnvioRecusado | None:
    """A recusa do sistema (envio desligado / simulador em produção), ou None."""
    try:
        _antes_de_tudo()
    except EnvioRecusado as e:
        return e
    return None


async def _contexto_da_acao(
    session: AsyncSession, comentario: AtendimentoComentario
) -> tuple[AtendimentoPublicacao, AtendimentoCanal | None, ContaRede | None]:
    pub = await session.get(AtendimentoPublicacao, comentario.publicacao_id)
    if pub is None:  # pragma: no cover — FK com CASCADE
        raise EnvioRecusado(RECUSA_SEM_TOKEN, "A publicação deste comentário não existe mais.")
    canal = (
        await session.get(AtendimentoCanal, pub.canal_id, populate_existing=True)
        if pub.canal_id
        else None
    )
    return pub, canal, await conta_do_canal(session, canal)


SEM_TOKEN_PAGINA = "sem o token da Página (troque o token em Cadastros › Redes Sociais)"  # noqa: S105


async def _chamar_resposta(
    conta: ContaRede,
    pub: AtendimentoPublicacao,
    c: AtendimentoComentario,
    texto: str,
    *,
    privado: bool,
    cliente: httpx.AsyncClient,
) -> tuple[Resposta, str | None]:
    """O POST na rede → (resposta, id externo do que saiu). Nunca levanta."""
    if privado:
        # Resposta privada: Messenger Platform, token DA PÁGINA, no id da conta
        # (ig_user_id no Instagram, page_id no Facebook).
        token = await _token_da_pagina(conta, cliente)
        if not token:
            return Resposta(erro=SEM_TOKEN_PAGINA), None
        r = await _chamar(
            cliente,
            "POST",
            f"{conta.conta_id}/messages",
            token,
            json={"recipient": {"comment_id": c.externo_id}, "message": {"text": texto}},
        )
        mid = (r.dados or {}).get("message_id") if r.ok else None
        return r, f"direct:{mid}" if mid else None
    if _e_mencao(c):
        r = await _chamar(
            cliente,
            "POST",
            f"{conta.conta_id}/mentions",
            str(conta.token),
            data={"media_id": pub.externo_id, "message": texto},
        )
    elif conta.plataforma == PLATAFORMA_INSTAGRAM:
        # Resposta a uma resposta vai para o comentário de topo (regra do IG).
        alvo = c.pai_externo_id or c.externo_id
        r = await _chamar(
            cliente, "POST", f"{alvo}/replies", str(conta.token), data={"message": texto}
        )
    else:
        token = await _token_da_pagina(conta, cliente)
        if not token:
            return Resposta(erro=SEM_TOKEN_PAGINA), None
        alvo = c.pai_externo_id or c.externo_id
        r = await _chamar(cliente, "POST", f"{alvo}/comments", token, data={"message": texto})
    rid = (r.dados or {}).get("id") if r.ok else None
    return r, str(rid) if rid else None


async def _com_cliente(cliente: httpx.AsyncClient | None, chamada):
    """Roda `chamada(http)` com o cliente dado ou um novo (fechado no fim)."""
    if cliente is not None:
        return await chamada(cliente)
    async with novo_cliente() as http:
        return await chamada(http)


async def responder(
    session: AsyncSession,
    comentario: AtendimentoComentario,
    texto: str,
    *,
    user: User | None,
    confirmar: bool,
    privado: bool = False,
    cliente: httpx.AsyncClient | None = None,
    agora: datetime | None = None,
) -> AtendimentoMensagem:
    """Responde o comentário em PÚBLICO ou no Direct; devolve a mensagem gravada.

    Levanta `EnvioRecusado` quando uma trava impede (nada saiu): envio
    desligado (ANTES de tudo), sem confirmar, conta em observar, comentário
    oculto/da marca, Direct fora do prazo ou repetido, texto inválido, uma
    em voo. Erro da REDE não levanta: a mensagem fica `falhou` (não saiu)
    ou `revisar` (sem resposta: pode ter saído — não retentar às cegas).
    COMMITA antes de chamar a rede (a linha em voo) e depois (o resultado).
    """
    _antes_de_tudo()
    agora = agora or _agora()
    if not confirmar:
        raise EnvioRecusado(
            RECUSA_CONFIRMAR,
            AVISO_DIRECT if privado else f"Responder em PÚBLICO? {AVISO_PUBLICO}",
        )
    pub, canal, conta = await _contexto_da_acao(session, comentario)
    motivo = motivo_resposta(comentario, canal, envio_ativo=True, privado=privado, agora=agora)
    if motivo:
        raise EnvioRecusado(*motivo)
    if conta is None or not conta.token or not conta.conta_id:
        raise EnvioRecusado(RECUSA_SEM_TOKEN, MOTIVO_SEM_TOKEN)
    normalizado = preparar_texto(texto, plataforma=pub.plataforma, privado=privado)
    acao = ACAO_DIRECT if privado else ACAO_PUBLICO

    # ── 0/1. a conversa travada e a linha em voo (num SAVEPOINT) ──────────
    ponto = await session.begin_nested()
    try:
        conversa: AtendimentoConversa | None
        if comentario.conversa_id is None and canal is not None:
            # Comentário de fora da janela (só no cartão): ganha a conversa agora.
            conversa = await conversa_da_pessoa(
                session, canal, conta, pub, comentario.autor_id, comentario.autor_username
            )
            comentario.conversa_id = conversa.id
            # O comentário entra na conversa antes da resposta (o contexto).
            await gravar.gravar_mensagem(
                session,
                conversa,
                externo_id=comentario.externo_id,
                autor=AUTOR_CLIENTE,
                texto=_texto_do_comentario(comentario),
                enviada_em=comentario.criado_em,
                tipo="texto",
                anexos=[],
                payload={"comentario": True, "oculto": comentario.oculto},
                origem=None,
            )
        else:
            conversa = (
                await session.get(AtendimentoConversa, comentario.conversa_id)
                if comentario.conversa_id
                else None
            )
        if conversa is None:
            raise EnvioRecusado(RECUSA_SEM_CONVERSA, "A conversa deste comentário não existe mais.")
        await enviar.travar_conversa(session, conversa)
        await enviar.aposentar_envios_presos(session, conversa_id=conversa.id)
        mensagem = AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=None,
            autor=AUTOR_LOJA,
            origem=ORIGEM_HUMANO,
            autor_user_id=user.id if user is not None else None,
            tipo="texto",
            texto=normalizado,
            anexos=[],
            enviada_em=None,
            status=MSG_ENVIANDO,
            payload={"comentario": {"id": comentario.externo_id, "acao": acao}},
        )
        await session.flush()
        try:
            async with session.begin_nested():
                session.add(mensagem)
                await session.flush()
        except IntegrityError as e:
            if not is_unique_violation(e):
                raise
            raise EnvioRecusado(
                enviar.RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
            ) from e
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_redes_resposta_iniciada",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=pub.plataforma,
        acao=acao,
    )

    # ── 2. a rede (ou o simulador, só local) ──────────────────────────────
    simulado = enviar.vai_para_o_simulador(pub.plataforma)
    if simulado:
        r, externo = Resposta(dados={"id": "sim"}), f"sim:{uuid4()}"
    else:
        r, externo = await _com_cliente(
            cliente,
            lambda http: _chamar_resposta(
                conta, pub, comentario, normalizado, privado=privado, cliente=http
            ),
        )

    # ── 3. o resultado ────────────────────────────────────────────────────
    await session.refresh(mensagem)
    payload = {**(mensagem.payload or {}), "envio": {"simulador": True} if simulado else {}}
    if r.ok:
        mensagem.status = MSG_ENVIADA
        mensagem.erro = None
        mensagem.enviada_em = agora
        if externo:
            dono = await session.scalar(
                select(AtendimentoMensagem.id).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.externo_id == externo,
                    AtendimentoMensagem.id != mensagem.id,
                )
            )
            if dono is None:
                mensagem.externo_id = externo
        if privado:
            comentario.dados = {
                **(comentario.dados or {}),
                "resposta_privada_em": _iso(agora),
                "resposta_privada_por": str(user.id) if user else None,
            }
        elif externo:
            # A resposta entra no cartão já (a leitura seguinte acha o mesmo id).
            linha = await _upsert_comentario(
                session,
                pub,
                pub.plataforma,
                Comentario(
                    externo_id=externo,
                    pai=(
                        None
                        if _e_mencao(comentario)
                        else (comentario.pai_externo_id or comentario.externo_id)
                    ),
                    autor_id=conta.conta_id,
                    autor_username=conta.username,
                    texto=normalizado,
                    criado_em=agora,
                    curtidas=None,
                    oculto=False,
                    da_marca=True,
                ),
                dados={"pelo_davinci": True},
            )
            linha.conversa_id = conversa.id
    elif r.ambiguo:
        mensagem.status = MSG_REVISAR
        mensagem.erro = (r.erro or "envio_ambiguo")[:500]
        if privado:
            # Pode ter saído, e a Meta só aceita UMA: não deixa tentar às cegas.
            comentario.dados = {
                **(comentario.dados or {}),
                "resposta_privada_em": _iso(agora),
                "resposta_privada_incerta": True,
            }
    else:
        mensagem.status = MSG_FALHOU
        mensagem.erro = (r.erro or "envio_falhou")[:500]
    mensagem.payload = payload
    await session.flush()
    await gravar.recalcular_conversa(session, conversa)
    await atualizar_pergunta(session, conversa)
    await session.commit()
    logger.info(
        "atendimento_redes_resposta_concluida",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=pub.plataforma,
        acao=acao,
        status=mensagem.status,
        code=r.code,
    )
    return mensagem


async def ocultar(
    session: AsyncSession,
    comentario: AtendimentoComentario,
    *,
    user: User | None,
    confirmar: bool,
    esconder: bool = True,
    cliente: httpx.AsyncClient | None = None,
    agora: datetime | None = None,
) -> AtendimentoComentario:
    """Oculta (ou mostra de novo) o comentário na rede e registra quem fez.

    As mesmas travas da resposta (envio desligado ANTES de tudo, confirmar,
    modo humano). A recusa da rede vira `EnvioRecusado(rede_recusou)`; sem
    resposta da rede, idem — e a pessoa confere no app antes de repetir.
    """
    _antes_de_tudo()
    agora = agora or _agora()
    if not confirmar:
        raise EnvioRecusado(
            RECUSA_CONFIRMAR,
            "Ocultar o comentário na rede? Ele some para os outros (a pessoa ainda o vê).",
        )
    pub, canal, conta = await _contexto_da_acao(session, comentario)
    motivo = motivo_ocultar(comentario, canal, envio_ativo=True, esconder=esconder)
    if motivo:
        raise EnvioRecusado(*motivo)
    if conta is None or not conta.token:
        raise EnvioRecusado(RECUSA_SEM_TOKEN, MOTIVO_SEM_TOKEN)
    valor = "true" if esconder else "false"

    async def _na_rede(http: httpx.AsyncClient) -> Resposta:
        if pub.plataforma == PLATAFORMA_INSTAGRAM:
            return await _chamar(
                http, "POST", comentario.externo_id, str(conta.token), data={"hide": valor}
            )
        token = await _token_da_pagina(conta, http)
        if not token:
            return Resposta(erro=SEM_TOKEN_PAGINA)
        return await _chamar(http, "POST", comentario.externo_id, token, data={"is_hidden": valor})

    if enviar.vai_para_o_simulador(pub.plataforma):
        r = Resposta(dados={"success": True})
    else:
        r = await _com_cliente(cliente, _na_rede)
    logger.info(
        "atendimento_redes_ocultar",
        comentario_id=str(comentario.id),
        plataforma=pub.plataforma,
        esconder=esconder,
        ok=r.ok,
        code=r.code,
    )
    if not r.ok:
        detalhe = r.erro or "a rede não confirmou"
        if r.ambiguo:
            detalhe = f"{detalhe} — confira no app antes de tentar de novo"
        raise EnvioRecusado(RECUSA_REDE_RECUSOU, detalhe)
    comentario.oculto = esconder
    comentario.dados = {
        **(comentario.dados or {}),
        "oculto_em" if esconder else "mostrado_em": _iso(agora),
        "oculto_por" if esconder else "mostrado_por": str(user.id) if user else None,
    }
    if comentario.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, comentario.conversa_id)
        if conversa is not None:
            rede = "Instagram" if pub.plataforma == PLATAFORMA_INSTAGRAM else "Facebook"
            quem = ((user.name or user.email or "").strip() if user else "") or "alguém"
            acao = "ocultado" if esconder else "mostrado"
            await gravar.gravar_mensagem(
                session,
                conversa,
                externo_id=f"{acao}:{comentario.externo_id}:{_iso(agora)}",
                autor=AUTOR_SISTEMA,
                texto=(
                    f"Comentário {'ocultado' if esconder else 'mostrado de novo'} no {rede} "
                    f"pelo DaVinci ({quem})."
                ),
                enviada_em=agora,
                tipo="texto",
                anexos=[],
                payload={"ocultar": esconder, "comentario": comentario.externo_id},
                origem=None,
            )
    await session.commit()
    return comentario
