"""Marketing › Lives — cadastro das lives e a porta do gravador do Mac (03/11/2026).

Eduardo, 03/11/2026: alguém cria a live no DaVinci e cola o link de compartilhar
(Shopee ou TikTok); um robô no Mac mini pega as cadastradas, grava a live
inteira e, com as gravações, a gente tira o padrão de uma boa live.

  Tela (permissão `marketing_criativos`, a mesma de Roteiros e Desempenho):
    GET    /api/marketing/lives            — a lista, mais nova primeiro
    GET    /api/marketing/lives/lojas      — lojas Shopee/TikTok pro select
    POST   /api/marketing/lives            — cadastra (plataforma sai do link)
    PATCH  /api/marketing/lives/{id}       — corrige o cadastro
    POST   /api/marketing/lives/{id}/regravar — erro → aguardando de novo
    DELETE /api/marketing/lives/{id}       — só o que não está gravando/gravado

  Robô (`X-Agent-Token` = MARKETING_AGENT_TOKEN, o mesmo do executor do TikTok):
    GET  /api/marketing/lives/robo/pendentes    — aguardando + gravando
    POST /api/marketing/lives/robo/{id}/estado  — status, arquivo e métricas

Só o robô muda o status. O vídeo fica no Mac; aqui vai só o caminho.
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Integration, MarketingLive, User
from app.models.enums import IntegrationPlatform
from app.models.marketing_live import (
    STATUS_AGUARDANDO,
    STATUS_ERRO,
    STATUS_GRAVADA,
    STATUS_GRAVANDO,
    STATUS_LIVE,
)
from app.routers.marketing import _require_agent_token

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/lives", tags=["marketing_lives"])

_view = require_permission("marketing_criativos", "view")
_edit = require_permission("marketing_criativos", "edit")

# Hosts aceitos por plataforma. O link curto (s.shopee.com.br, shp.ee,
# vm.tiktok.com) o robô resolve na hora de gravar.
_HOSTS: dict[str, tuple[str, ...]] = {
    "shopee": ("shopee.com.br", "shp.ee"),
    "tiktok": ("tiktok.com",),
}

# O robô pode ir de um status para estes. `gravada` é final; `erro` só sai
# pela tela (regravar).
_TRANSICOES: dict[str, tuple[str, ...]] = {
    STATUS_AGUARDANDO: (STATUS_AGUARDANDO, STATUS_GRAVANDO, STATUS_GRAVADA, STATUS_ERRO),
    STATUS_GRAVANDO: (STATUS_GRAVANDO, STATUS_GRAVADA, STATUS_ERRO),
}


def plataforma_do_link(link: str) -> str | None:
    """'shopee' | 'tiktok' pelo host do link; None se não for de nenhuma."""
    try:
        u = urlparse(link.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https"):
        return None
    host = (u.hostname or "").lower()
    for plataforma, hosts in _HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return plataforma
    return None


class LiveIn(BaseModel):
    link: str = Field(min_length=10, max_length=2000)
    apresentador: str | None = Field(default=None, max_length=200)
    integration_id: UUID | None = None
    produtos: str | None = Field(default=None, max_length=4000)
    observacao: str | None = Field(default=None, max_length=4000)


class LivePatch(BaseModel):
    link: str | None = Field(default=None, min_length=10, max_length=2000)
    apresentador: str | None = Field(default=None, max_length=200)
    integration_id: UUID | None = None
    produtos: str | None = Field(default=None, max_length=4000)
    observacao: str | None = Field(default=None, max_length=4000)


class LiveOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    plataforma: str
    link: str
    apresentador: str | None
    integration_id: UUID | None
    loja: str | None
    produtos: str | None
    observacao: str | None
    status: str
    titulo: str | None
    canal: str | None
    sala_id: str | None
    inicio_em: datetime | None
    fim_em: datetime | None
    duracao_s: int | None
    arquivo: str | None
    tamanho_bytes: int | None
    metricas: dict[str, Any] | None
    erro: str | None
    robo_visto_em: datetime | None
    created_at: datetime
    created_by_nome: str | None = None


class EstadoIn(BaseModel):
    status: str
    titulo: str | None = Field(default=None, max_length=500)
    canal: str | None = Field(default=None, max_length=200)
    sala_id: str | None = Field(default=None, max_length=100)
    inicio_em: datetime | None = None
    fim_em: datetime | None = None
    duracao_s: int | None = Field(default=None, ge=0)
    arquivo: str | None = Field(default=None, max_length=1000)
    tamanho_bytes: int | None = Field(default=None, ge=0)
    metricas: dict[str, Any] | None = None
    erro: str | None = Field(default=None, max_length=2000)


async def _live_or_404(session: AsyncSession, live_id: UUID) -> MarketingLive:
    live = await session.get(MarketingLive, live_id)
    if live is None:
        raise HTTPException(404, detail={"code": "live_nao_encontrada"})
    return live


async def _loja(session: AsyncSession, integration_id: UUID | None, plataforma: str) -> str | None:
    """Nome da loja (guardado junto: a integração pode sumir e o nome fica)."""
    if integration_id is None:
        return None
    integ = await session.get(Integration, integration_id)
    if integ is None:
        raise HTTPException(422, detail={"code": "loja_nao_encontrada"})
    if str(integ.platform) != plataforma:
        raise HTTPException(422, detail={"code": "loja_de_outra_plataforma"})
    return integ.name


def _link_ok(link: str) -> tuple[str, str]:
    link = link.strip()
    plataforma = plataforma_do_link(link)
    if plataforma is None:
        raise HTTPException(422, detail={"code": "link_invalido"})
    return link, plataforma


def _limpo(texto: str | None) -> str | None:
    return (texto or "").strip() or None


@router.get("", response_model=list[LiveOut])
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LiveOut]:
    q = (
        select(MarketingLive, User.name)
        .outerjoin(User, User.id == MarketingLive.created_by)
        .order_by(MarketingLive.created_at.desc())
        .limit(limit)
    )
    if status:
        q = q.where(MarketingLive.status == status)
    saida: list[LiveOut] = []
    for live, nome in (await session.execute(q)).all():
        out = LiveOut.model_validate(live)
        out.created_by_nome = nome
        saida.append(out)
    return saida


@router.get("/lojas")
async def lojas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> list[dict[str, str]]:
    rows = (
        await session.execute(
            select(Integration.id, Integration.name, Integration.platform)
            .where(
                Integration.platform.in_((IntegrationPlatform.SHOPEE, IntegrationPlatform.TIKTOK)),
                Integration.status == "active",
                Integration.archived_at.is_(None),
            )
            .order_by(Integration.name)
        )
    ).all()
    return [{"id": str(i), "nome": n, "plataforma": str(p)} for i, n, p in rows]


@router.post("", response_model=LiveOut, status_code=201)
async def criar(
    body: LiveIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> LiveOut:
    link, plataforma = _link_ok(body.link)
    live = MarketingLive(
        plataforma=plataforma,
        link=link,
        apresentador=_limpo(body.apresentador),
        integration_id=body.integration_id,
        loja=await _loja(session, body.integration_id, plataforma),
        produtos=_limpo(body.produtos),
        observacao=_limpo(body.observacao),
        status=STATUS_AGUARDANDO,
        created_by=user.id,
    )
    session.add(live)
    await session.commit()
    await session.refresh(live)
    logger.info("live_cadastrada", live_id=str(live.id), plataforma=plataforma)
    out = LiveOut.model_validate(live)
    out.created_by_nome = user.name
    return out


@router.patch("/{live_id}", response_model=LiveOut)
async def editar(
    live_id: UUID,
    body: LivePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> LiveOut:
    live = await _live_or_404(session, live_id)
    campos = body.model_fields_set
    if "link" in campos and body.link is not None:
        if live.status != STATUS_AGUARDANDO and body.link.strip() != live.link:
            raise HTTPException(409, detail={"code": "live_ja_comecou"})
        live.link, live.plataforma = _link_ok(body.link)
    if "apresentador" in campos:
        live.apresentador = _limpo(body.apresentador)
    if "produtos" in campos:
        live.produtos = _limpo(body.produtos)
    if "observacao" in campos:
        live.observacao = _limpo(body.observacao)
    if "integration_id" in campos or "link" in campos:
        integ = body.integration_id if "integration_id" in campos else live.integration_id
        live.loja = await _loja(session, integ, live.plataforma)
        live.integration_id = integ
    await session.commit()
    await session.refresh(live)
    return LiveOut.model_validate(live)


@router.post("/{live_id}/regravar", response_model=LiveOut)
async def regravar(
    live_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> LiveOut:
    """Live que deu erro volta pra fila (ex.: o link estava errado e foi corrigido)."""
    live = await _live_or_404(session, live_id)
    if live.status != STATUS_ERRO:
        raise HTTPException(409, detail={"code": "live_nao_deu_erro"})
    live.status = STATUS_AGUARDANDO
    live.erro = None
    await session.commit()
    await session.refresh(live)
    return LiveOut.model_validate(live)


@router.delete("/{live_id}", status_code=204)
async def apagar(
    live_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> None:
    live = await _live_or_404(session, live_id)
    if live.status not in (STATUS_AGUARDANDO, STATUS_ERRO):
        # Gravando: o robô ainda está nela. Gravada: tem arquivo no Mac e
        # métricas — apagar o cadastro perderia o elo entre os dois.
        raise HTTPException(409, detail={"code": "live_nao_apagavel"})
    await session.delete(live)
    await session.commit()


# ── Robô do Mac ───────────────────────────────────────────────────────────


@router.get("/robo/pendentes", dependencies=[Depends(_require_agent_token)])
async def robo_pendentes(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """O que o gravador deve olhar: aguardando e gravando.

    `gravando` volta também: se o Mac reiniciou no meio, o robô retoma a
    gravação num arquivo novo (parte 2) em vez de largar a live.
    """
    rows = (
        (
            await session.execute(
                select(MarketingLive)
                .where(MarketingLive.status.in_((STATUS_AGUARDANDO, STATUS_GRAVANDO)))
                .order_by(MarketingLive.created_at.asc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "plataforma": r.plataforma,
            "link": r.link,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "inicio_em": r.inicio_em.isoformat() if r.inicio_em else None,
            "arquivo": r.arquivo,
        }
        for r in rows
    ]


@router.post("/robo/{live_id}/estado", dependencies=[Depends(_require_agent_token)])
async def robo_estado(
    live_id: UUID,
    body: EstadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """O robô conta como está a live. Campo ausente (None) não apaga o que já tem."""
    if body.status not in STATUS_LIVE:
        raise HTTPException(400, detail={"code": "status_invalido"})
    live = await _live_or_404(session, live_id)
    if body.status not in _TRANSICOES.get(live.status, ()):
        raise HTTPException(409, detail={"code": "transicao_invalida", "atual": live.status})
    live.status = body.status
    for campo in (
        "titulo",
        "canal",
        "sala_id",
        "inicio_em",
        "fim_em",
        "duracao_s",
        "arquivo",
        "tamanho_bytes",
        "metricas",
    ):
        valor = getattr(body, campo)
        if valor is not None:
            setattr(live, campo, valor)
    if body.status == STATUS_ERRO:
        live.erro = (body.erro or "erro sem detalhe")[:2000]
    elif body.erro is not None:
        live.erro = body.erro[:2000] or None
    elif body.status == STATUS_GRAVADA:
        live.erro = None
    live.robo_visto_em = datetime.now(UTC)
    await session.commit()
    if body.status != STATUS_GRAVANDO or live.inicio_em is None:
        logger.info("live_estado", live_id=str(live_id), status=body.status)
    return {"status": "ok"}
