"""Notas Fiscais automáticas — cadastros (admin-only).

Fase 1: cadastro do FATURADOR (emissor da NF). Cada linha é um tipo de
faturador; a lista é extensível. A automação (emissão da NF) é construída
depois — aqui é só o CRUD do cadastro.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import NfEtiqueta, NfFaturador, NfImpressao, User
from app.schemas.nf import (
    NfEtiquetaCreate,
    NfEtiquetaOut,
    NfEtiquetaPatch,
    NfFaturadorCreate,
    NfFaturadorOut,
    NfFaturadorPatch,
    NfFaturamentoRowOut,
    NfImpressaoCreate,
    NfImpressaoOut,
    NfImpressaoPatch,
)
from app.security.cipher import encrypt

logger = structlog.get_logger()
# Prefixo próprio (/api/nf já é usado pelo nf_upload). Umbrella dos cadastros
# do sistema de NF automáticas (faturador; etiqueta/impressão virão depois).
router = APIRouter(prefix="/api/nf-cadastro", tags=["nf_cadastro"])


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _to_out(f: NfFaturador) -> NfFaturadorOut:
    return NfFaturadorOut(
        id=f.id,
        nome=f.nome,
        modo=f.modo,
        nf_cheia=f.nf_cheia,
        percentual=f.percentual,
        sku_fonte=f.sku_fonte,
        nome_fonte=f.nome_fonte,
        ncm=f.ncm,
        ads_power=f.ads_power,
        usuario=f.usuario,
        has_senha=bool(f.senha_enc),
        observacao=f.observacao,
        sort_order=f.sort_order,
        created_by=f.created_by,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


@router.get("/faturadores", response_model=list[NfFaturadorOut])
async def list_faturadores(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[NfFaturadorOut]:
    stmt = select(NfFaturador).order_by(asc(NfFaturador.sort_order), asc(NfFaturador.nome))
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(f) for f in rows]


@router.post("/faturadores", response_model=NfFaturadorOut, status_code=status.HTTP_201_CREATED)
async def create_faturador(
    body: NfFaturadorCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> NfFaturadorOut:
    f = NfFaturador(
        nome=body.nome.strip(),
        modo=body.modo.strip(),
        nf_cheia=body.nf_cheia,
        percentual=body.percentual,
        sku_fonte=_clean(body.sku_fonte),
        nome_fonte=_clean(body.nome_fonte),
        ncm=_clean(body.ncm),
        ads_power=_clean(body.ads_power),
        usuario=_clean(body.usuario),
        senha_enc=encrypt(body.senha) if body.senha else None,
        observacao=_clean(body.observacao),
        sort_order=body.sort_order or 0,
        created_by=admin.id,
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)
    logger.info("nf_faturador_created", id=str(f.id), nome=f.nome)
    return _to_out(f)


@router.patch("/faturadores/{faturador_id}", response_model=NfFaturadorOut)
async def patch_faturador(
    faturador_id: UUID,
    body: NfFaturadorPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> NfFaturadorOut:
    f = (
        await session.execute(select(NfFaturador).where(NfFaturador.id == faturador_id))
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "nf_faturador_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "nome" in data and data["nome"] is not None:
        f.nome = data["nome"].strip()
    if "modo" in data and data["modo"] is not None:
        f.modo = data["modo"].strip()
    if "nf_cheia" in data and data["nf_cheia"] is not None:
        f.nf_cheia = data["nf_cheia"]
    if "percentual" in data:
        f.percentual = data["percentual"]
    if "sku_fonte" in data:
        f.sku_fonte = _clean(data["sku_fonte"])
    if "nome_fonte" in data:
        f.nome_fonte = _clean(data["nome_fonte"])
    if "ncm" in data:
        f.ncm = _clean(data["ncm"])
    if "ads_power" in data:
        f.ads_power = _clean(data["ads_power"])
    if "usuario" in data:
        f.usuario = _clean(data["usuario"])
    if "senha" in data:
        # None não vem aqui (exclude_unset). "" limpa; texto (re)criptografa.
        pwd = data["senha"]
        f.senha_enc = encrypt(pwd) if pwd else None
    if "observacao" in data:
        f.observacao = _clean(data["observacao"])
    if "sort_order" in data and data["sort_order"] is not None:
        f.sort_order = data["sort_order"]

    await session.commit()
    await session.refresh(f)
    return _to_out(f)


@router.delete("/faturadores/{faturador_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faturador(
    faturador_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    f = (
        await session.execute(select(NfFaturador).where(NfFaturador.id == faturador_id))
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "nf_faturador_not_found"})
    await session.delete(f)
    await session.commit()
    logger.info("nf_faturador_deleted", id=str(faturador_id))
    return None


# ---------------------------------------------------------------------------
# ETIQUETA — onde a NF é inserida na plataforma p/ liberar a etiqueta.
# ---------------------------------------------------------------------------


def _etiqueta_out(e: NfEtiqueta) -> NfEtiquetaOut:
    return NfEtiquetaOut(
        id=e.id,
        plataforma=e.plataforma,
        modo=e.modo,
        ads_power=e.ads_power,
        observacao=e.observacao,
        sort_order=e.sort_order,
        created_by=e.created_by,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


@router.get("/etiquetas", response_model=list[NfEtiquetaOut])
async def list_etiquetas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[NfEtiquetaOut]:
    stmt = select(NfEtiqueta).order_by(asc(NfEtiqueta.sort_order), asc(NfEtiqueta.plataforma))
    rows = (await session.execute(stmt)).scalars().all()
    return [_etiqueta_out(e) for e in rows]


@router.post("/etiquetas", response_model=NfEtiquetaOut, status_code=status.HTTP_201_CREATED)
async def create_etiqueta(
    body: NfEtiquetaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> NfEtiquetaOut:
    e = NfEtiqueta(
        plataforma=body.plataforma.strip(),
        modo=_clean(body.modo),
        ads_power=_clean(body.ads_power),
        observacao=_clean(body.observacao),
        sort_order=body.sort_order or 0,
        created_by=admin.id,
    )
    session.add(e)
    await session.commit()
    await session.refresh(e)
    logger.info("nf_etiqueta_created", id=str(e.id), plataforma=e.plataforma)
    return _etiqueta_out(e)


@router.patch("/etiquetas/{etiqueta_id}", response_model=NfEtiquetaOut)
async def patch_etiqueta(
    etiqueta_id: UUID,
    body: NfEtiquetaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> NfEtiquetaOut:
    e = (
        await session.execute(select(NfEtiqueta).where(NfEtiqueta.id == etiqueta_id))
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, detail={"code": "nf_etiqueta_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "plataforma" in data and data["plataforma"] is not None:
        e.plataforma = data["plataforma"].strip()
    if "modo" in data:
        e.modo = _clean(data["modo"])
    if "ads_power" in data:
        e.ads_power = _clean(data["ads_power"])
    if "observacao" in data:
        e.observacao = _clean(data["observacao"])
    if "sort_order" in data and data["sort_order"] is not None:
        e.sort_order = data["sort_order"]

    await session.commit()
    await session.refresh(e)
    return _etiqueta_out(e)


@router.delete("/etiquetas/{etiqueta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_etiqueta(
    etiqueta_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    e = (
        await session.execute(select(NfEtiqueta).where(NfEtiqueta.id == etiqueta_id))
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, detail={"code": "nf_etiqueta_not_found"})
    await session.delete(e)
    await session.commit()
    logger.info("nf_etiqueta_deleted", id=str(etiqueta_id))
    return None


# ---------------------------------------------------------------------------
# IMPRESSÃO — como a etiqueta é impressa depois da NF emitida + inserida.
# ---------------------------------------------------------------------------


def _impressao_out(i: NfImpressao) -> NfImpressaoOut:
    return NfImpressaoOut(
        id=i.id,
        tipo=i.tipo,
        observacao=i.observacao,
        visualizacao=i.visualizacao,
        usa_etiqueta=i.usa_etiqueta,
        usa_declaracao=i.usa_declaracao,
        usa_nota=i.usa_nota,
        sort_order=i.sort_order,
        created_by=i.created_by,
        created_at=i.created_at,
        updated_at=i.updated_at,
    )


@router.get("/impressoes", response_model=list[NfImpressaoOut])
async def list_impressoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[NfImpressaoOut]:
    stmt = select(NfImpressao).order_by(asc(NfImpressao.sort_order), asc(NfImpressao.tipo))
    rows = (await session.execute(stmt)).scalars().all()
    return [_impressao_out(i) for i in rows]


@router.post("/impressoes", response_model=NfImpressaoOut, status_code=status.HTTP_201_CREATED)
async def create_impressao(
    body: NfImpressaoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> NfImpressaoOut:
    i = NfImpressao(
        tipo=body.tipo.strip(),
        observacao=_clean(body.observacao),
        visualizacao=_clean(body.visualizacao),
        usa_etiqueta=body.usa_etiqueta,
        usa_declaracao=body.usa_declaracao,
        usa_nota=body.usa_nota,
        sort_order=body.sort_order or 0,
        created_by=admin.id,
    )
    session.add(i)
    await session.commit()
    await session.refresh(i)
    logger.info("nf_impressao_created", id=str(i.id), tipo=i.tipo)
    return _impressao_out(i)


@router.patch("/impressoes/{impressao_id}", response_model=NfImpressaoOut)
async def patch_impressao(
    impressao_id: UUID,
    body: NfImpressaoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> NfImpressaoOut:
    i = (
        await session.execute(select(NfImpressao).where(NfImpressao.id == impressao_id))
    ).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "nf_impressao_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "tipo" in data and data["tipo"] is not None:
        i.tipo = data["tipo"].strip()
    if "observacao" in data:
        i.observacao = _clean(data["observacao"])
    if "visualizacao" in data:
        i.visualizacao = _clean(data["visualizacao"])
    if "usa_etiqueta" in data and data["usa_etiqueta"] is not None:
        i.usa_etiqueta = data["usa_etiqueta"]
    if "usa_declaracao" in data and data["usa_declaracao"] is not None:
        i.usa_declaracao = data["usa_declaracao"]
    if "usa_nota" in data and data["usa_nota"] is not None:
        i.usa_nota = data["usa_nota"]
    if "sort_order" in data and data["sort_order"] is not None:
        i.sort_order = data["sort_order"]

    await session.commit()
    await session.refresh(i)
    return _impressao_out(i)


@router.delete("/impressoes/{impressao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_impressao(
    impressao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    i = (
        await session.execute(select(NfImpressao).where(NfImpressao.id == impressao_id))
    ).scalar_one_or_none()
    if i is None:
        raise HTTPException(404, detail={"code": "nf_impressao_not_found"})
    await session.delete(i)
    await session.commit()
    logger.info("nf_impressao_deleted", id=str(impressao_id))
    return None


# ---------------------------------------------------------------------------
# PAINEL DE FATURAMENTO (aba NF R37–R39) — read-only.
#
# Uma linha por pedido do Bling (janela de `dias`), só das lojas que têm algum
# cadastro de NF atribuído (Faturador/Etiqueta/Impressão). Os 3 status de etapa
# vêm do LEFT JOIN com nf_faturamento (a automação das fases seguintes grava lá);
# pedido sem linha aparece 'pendente'. Não há escrita aqui — é o painel de
# acompanhamento pra ver onde cada pedido travou.
# ---------------------------------------------------------------------------

_FATURAMENTO_SQL = text(
    """
    SELECT * FROM (
        SELECT DISTINCT ON (bo.numero)
            bo.data::date                         AS data,
            bo.numero                             AS pedido_bling,
            bo.numeroloja                         AS pedido_marketplace,
            COALESCE(pl.label, initcap(si.platform)) AS plataforma,
            si.account_name                       AS conta,
            sb.nome                               AS status_bling,
            COALESCE(nf.status_faturamento, 'pendente') AS status_faturamento,
            nf.erro_faturamento                   AS erro_faturamento,
            COALESCE(nf.status_etiqueta, 'pendente')    AS status_etiqueta,
            nf.erro_etiqueta                      AS erro_etiqueta,
            COALESCE(nf.status_impressao, 'pendente')   AS status_impressao,
            nf.erro_impressao                     AS erro_impressao
        FROM davinci.bling_orders bo
        JOIN davinci.store_info si
            ON si.bling_store_id::text = bo.loja
        LEFT JOIN davinci.situacao_bling sb ON sb.id::text = bo.situacao
        LEFT JOIN davinci.nf_faturamento nf ON nf.pedido_bling = bo.numero
        LEFT JOIN (VALUES
            ('ml', 'Mercado Livre'), ('shopee', 'Shopee'), ('amazon', 'Amazon'),
            ('tiktok', 'TikTok'), ('magalu', 'Magalu'), ('aliexpress', 'AliExpress'),
            ('shein', 'Shein'), ('temu', 'Temu')
        ) AS pl(code, label) ON pl.code = si.platform
        WHERE bo.data >= now() - make_interval(days => :dias)
          AND bo.numero IS NOT NULL
          AND bo.situacao IS DISTINCT FROM 'excluido'
          AND si.archived_at IS NULL
          AND (
              si.nf_faturador_id IS NOT NULL
              OR si.nf_etiqueta_id IS NOT NULL
              OR si.nf_impressao_id IS NOT NULL
          )
        ORDER BY bo.numero, bo.data DESC
    ) t
    ORDER BY t.data DESC, t.pedido_bling
    LIMIT :limit
    """
)


@router.get("/faturamento", response_model=list[NfFaturamentoRowOut])
async def list_faturamento(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[NfFaturamentoRowOut]:
    rows = (
        await session.execute(_FATURAMENTO_SQL, {"dias": dias, "limit": limit})
    ).mappings().all()
    return [NfFaturamentoRowOut(**dict(r)) for r in rows]
