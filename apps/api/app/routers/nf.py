"""Notas Fiscais automáticas — cadastros (recurso `nf_faturador`) e painel de
faturamento (recurso `nf_faturamento`).

Fase 1: cadastro do FATURADOR (emissor da NF). Cada linha é um tipo de
faturador; a lista é extensível. A automação (emissão da NF) é construída
depois — aqui é só o CRUD do cadastro.
"""

import asyncio
import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import asc, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingOrder,
    NfCatalogoMala,
    NfCommand,
    NfEtiqueta,
    NfEtiquetaArquivo,
    NfFaturador,
    NfFaturamento,
    NfImpressao,
    Product,
    User,
)
from app.schemas.nf import (
    ConferirFreteAutoIn,
    ConferirFreteAutoOut,
    ConferirFreteIn,
    ConferirFreteOut,
    ConferirFretePedidoIn,
    ConferirFretePedidoOut,
    ConferirFreteProduto,
    GerarPlanilhaIn,
    NfCatalogoMalaCreate,
    NfCatalogoMalaOut,
    NfCatalogoMalaPatch,
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
from app.security.cipher import decrypt, encrypt
from app.services import (
    melhor_envio,
    nf_emissao_gerar,
    nf_etiqueta_lote,
    nf_frete_auto,
    nf_relatorio,
    nf_upseller,
)
from app.services.melhor_envio import (
    MelhorEnvioApiError,
    MelhorEnvioClient,
    MelhorEnvioConfigError,
)
from app.services.nf_etiqueta_transform import (
    EtiquetaTransformError,
    transformar_etiqueta,
)

# Teto do PDF cru da etiqueta que a marionete sobe pra transformação (item 2).
_ETIQUETA_MAX_BYTES = 8 * 1024 * 1024

# Teto do PDF ÚNICO da impressão em LOTE (N etiquetas + declarações num PDF só).
_ETIQUETA_LOTE_MAX_BYTES = 40 * 1024 * 1024

# Janela do casamento reserva do lote (destinatário + SKU): pedidos mais velhos
# que isso não entram como candidatos — evita casar homônimo antigo.
_ETIQUETA_LOTE_JANELA = timedelta(days=14)

# Pausa entre PATCHes de situação no Bling (rate limit — PATCHes em rajada
# derrubavam parte dos "Enviado Etiqueta").
_BLING_PATCH_PAUSA_S = 0.5

# Situação custom do shop no Bling. É ela que faz o pedido aparecer na aba
# Pedidos do Controle de Estoque pra ser impresso (ver routers/estoque.py).
_SITUACAO_ENVIADO_ETIQUETA = 83965

# Pedido sem estoque não vira etiqueta: vai pra Aguardando Cancelamento e sai
# do fluxo (o humano decide cancelar ou repor). Id da davinci.situacao_bling.
_SITUACAO_AGUARDANDO_CANCELAMENTO = 83955

logger = structlog.get_logger()
_SCHEMA = get_settings().database_schema
# Prefixo próprio (/api/nf já é usado pelo nf_upload). Umbrella dos cadastros
# do sistema de NF automáticas (faturador; etiqueta/impressão virão depois).
router = APIRouter(prefix="/api/nf-cadastro", tags=["nf_cadastro"])

# Cadastros (Faturador/Etiqueta/Impressão) = recurso `nf_faturador`; o painel
# de faturamento = `nf_faturamento`. Antes tudo era admin-only (require_admin).
_cad_view = require_permission("nf_faturador", "view")
_cad_edit = require_permission("nf_faturador", "edit")
_cad_delete = require_permission("nf_faturador", "delete")
_painel_view = require_permission("nf_faturamento", "view")
_painel_edit = require_permission("nf_faturamento", "edit")


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
    _user: Annotated[User, Depends(_cad_view)],
) -> list[NfFaturadorOut]:
    stmt = select(NfFaturador).order_by(asc(NfFaturador.sort_order), asc(NfFaturador.nome))
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(f) for f in rows]


@router.post("/faturadores", response_model=NfFaturadorOut, status_code=status.HTTP_201_CREATED)
async def create_faturador(
    body: NfFaturadorCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(_cad_edit)],
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
    _user: Annotated[User, Depends(_cad_edit)],
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


@router.get("/faturadores/{faturador_id}/senha")
async def reveal_faturador_senha(
    faturador_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_cad_edit)],
) -> dict[str, str]:
    """Devolve a senha descriptografada sob demanda (só quem tem edit).

    O front esvazia o campo ao reabrir (a senha nunca volta no GET da lista);
    este endpoint permite CONFERIR/ver a senha já salva clicando no olho.
    """
    f = (
        await session.execute(select(NfFaturador).where(NfFaturador.id == faturador_id))
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, detail={"code": "nf_faturador_not_found"})
    return {"senha": decrypt(f.senha_enc) if f.senha_enc else ""}


@router.delete("/faturadores/{faturador_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faturador(
    faturador_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_cad_delete)],
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
    _user: Annotated[User, Depends(_cad_view)],
) -> list[NfEtiquetaOut]:
    stmt = select(NfEtiqueta).order_by(asc(NfEtiqueta.sort_order), asc(NfEtiqueta.plataforma))
    rows = (await session.execute(stmt)).scalars().all()
    return [_etiqueta_out(e) for e in rows]


@router.post("/etiquetas", response_model=NfEtiquetaOut, status_code=status.HTTP_201_CREATED)
async def create_etiqueta(
    body: NfEtiquetaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(_cad_edit)],
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
    _user: Annotated[User, Depends(_cad_edit)],
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
    _user: Annotated[User, Depends(_cad_delete)],
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
    _user: Annotated[User, Depends(_cad_view)],
) -> list[NfImpressaoOut]:
    stmt = select(NfImpressao).order_by(asc(NfImpressao.sort_order), asc(NfImpressao.tipo))
    rows = (await session.execute(stmt)).scalars().all()
    return [_impressao_out(i) for i in rows]


@router.post("/impressoes", response_model=NfImpressaoOut, status_code=status.HTTP_201_CREATED)
async def create_impressao(
    body: NfImpressaoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(_cad_edit)],
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
    _user: Annotated[User, Depends(_cad_edit)],
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
    _user: Annotated[User, Depends(_cad_delete)],
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
# CATÁLOGO DE MALA — valor CHEIO da NF de mala por (modelo, tamanho). Usado
# pela emissão quando o faturador é nf_cheia; o casamento é automático — a
# família da mala (M1..M6 → abs, P1..P6 → pp, ME1/ME2) vem do nome do produto.
# ---------------------------------------------------------------------------


def _catalogo_out(c: NfCatalogoMala) -> NfCatalogoMalaOut:
    return NfCatalogoMalaOut(
        id=c.id,
        modelo=c.modelo,
        tamanho=c.tamanho,
        valor=c.valor,
        ncm=c.ncm,
        sort_order=c.sort_order,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("/catalogo-mala", response_model=list[NfCatalogoMalaOut])
async def list_catalogo_mala(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_cad_view)],
) -> list[NfCatalogoMalaOut]:
    stmt = select(NfCatalogoMala).order_by(
        asc(NfCatalogoMala.sort_order),
        asc(NfCatalogoMala.modelo),
        asc(NfCatalogoMala.tamanho),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_catalogo_out(c) for c in rows]


@router.post("/catalogo-mala", response_model=NfCatalogoMalaOut, status_code=status.HTTP_201_CREATED)
async def create_catalogo_mala(
    body: NfCatalogoMalaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(_cad_edit)],
) -> NfCatalogoMalaOut:
    c = NfCatalogoMala(
        modelo=body.modelo.strip(),
        tamanho=_clean(body.tamanho),
        valor=body.valor,
        ncm=_clean(body.ncm),
        sort_order=body.sort_order or 0,
        created_by=admin.id,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    logger.info("nf_catalogo_mala_created", id=str(c.id), modelo=c.modelo)
    return _catalogo_out(c)


@router.patch("/catalogo-mala/{catalogo_id}", response_model=NfCatalogoMalaOut)
async def patch_catalogo_mala(
    catalogo_id: UUID,
    body: NfCatalogoMalaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_cad_edit)],
) -> NfCatalogoMalaOut:
    c = (
        await session.execute(select(NfCatalogoMala).where(NfCatalogoMala.id == catalogo_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "nf_catalogo_mala_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "modelo" in data and data["modelo"] is not None:
        c.modelo = data["modelo"].strip()
    if "tamanho" in data:
        c.tamanho = _clean(data["tamanho"])
    if "valor" in data and data["valor"] is not None:
        c.valor = data["valor"]
    if "ncm" in data:
        c.ncm = _clean(data["ncm"])
    if "sort_order" in data and data["sort_order"] is not None:
        c.sort_order = data["sort_order"]

    await session.commit()
    await session.refresh(c)
    return _catalogo_out(c)


@router.delete("/catalogo-mala/{catalogo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalogo_mala(
    catalogo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_cad_delete)],
) -> None:
    c = (
        await session.execute(select(NfCatalogoMala).where(NfCatalogoMala.id == catalogo_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "nf_catalogo_mala_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("nf_catalogo_mala_deleted", id=str(catalogo_id))
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
    f"""
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
        FROM "{_SCHEMA}".bling_orders bo
        JOIN "{_SCHEMA}".store_info si
            ON si.bling_store_id::text = bo.loja
        LEFT JOIN "{_SCHEMA}".situacao_bling sb ON sb.id::text = bo.situacao
        LEFT JOIN "{_SCHEMA}".nf_faturamento nf ON nf.pedido_bling = bo.numero
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
    _user: Annotated[User, Depends(_painel_view)],
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[NfFaturamentoRowOut]:
    rows = (
        await session.execute(_FATURAMENTO_SQL, {"dias": dias, "limit": limit})
    ).mappings().all()
    return [NfFaturamentoRowOut(**dict(r)) for r in rows]


@router.post("/faturamento/gerar-planilha")
async def gerar_planilha_faturamento(
    body: GerarPlanilhaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_painel_edit)],
) -> StreamingResponse:
    """Gera a planilha de importação AVULSA (Fase 3a) dos pedidos escolhidos.

    Lê os itens do Bling principal (já no davinci), aplica a regra do faturador
    da loja de cada pedido e devolve o CSV no layout do relatório de vendas do
    Bling — que se importa no destino como venda avulsa (desacoplada do
    intermediador). Pedidos sem faturador/sem itens não entram no arquivo; o
    total e os pulados (com motivo) vão em headers `X-Pedidos-*`. 422 se
    nenhum pedido pôde ser gerado."""
    res = await nf_emissao_gerar.gerar_planilha(session, body.numeros)
    if not res.pedidos_ok:
        raise HTTPException(
            422,
            detail={
                "code": "nf_nenhum_pedido_gerado",
                "pulados": [{"numero": p.numero, "motivo": p.motivo} for p in res.pulados],
            },
        )
    pulados = [{"numero": p.numero, "motivo": p.motivo} for p in res.pulados]
    # Motivo tem acento → header precisa ser ASCII-safe (base64 do JSON).
    pulados_b64 = base64.b64encode(json.dumps(pulados).encode("utf-8")).decode("ascii")
    fname = nf_emissao_gerar.nome_arquivo()
    return StreamingResponse(
        iter([res.csv]),
        media_type=nf_relatorio.CSV_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Pedidos-Ok": str(len(res.pedidos_ok)),
            "X-Pedidos-Pulados": str(len(res.pulados)),
            "X-Pedidos-Pulados-Detalhe": pulados_b64,
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Pedidos-Ok, X-Pedidos-Pulados, "
                "X-Pedidos-Pulados-Detalhe"
            ),
        },
    )


@router.post("/faturamento/conferir-frete/auto", response_model=ConferirFreteAutoOut)
async def conferir_frete_auto(
    body: ConferirFreteAutoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_painel_edit)],
) -> ConferirFreteAutoOut:
    """Prefill do confere-frete a partir de um pedido do Bling.

    Resolve CEP destino (`bling_orders.cep_destino`), a caixa (dimensões do
    produto no Bling) e o frete projetado (Tabela de Preços da loja) pra o
    operador só revisar e cotar. Não cota nem decide `libera` — isso segue no
    `/conferir-frete`. 400 se o CEP de origem (.env) não estiver configurado;
    404 se o pedido não existir."""
    origem_cep = (get_settings().nf_origem_cep or "").strip()
    if not origem_cep:
        raise HTTPException(400, detail={"code": "nf_origem_cep_missing"})
    try:
        res = await nf_frete_auto.resolver_prefill(session, body.pedido_bling)
    except nf_frete_auto.PedidoNaoEncontrado:
        raise HTTPException(404, detail={"code": "nf_pedido_nao_encontrado"})
    return ConferirFreteAutoOut(
        from_cep=origem_cep,
        to_cep=res.to_cep,
        produtos=[
            ConferirFreteProduto(
                id=p.id,
                width=p.width,
                height=p.height,
                length=p.length,
                weight=p.weight,
                quantity=p.quantity,
            )
            for p in res.produtos
        ],
        frete_projetado=res.frete_projetado,
        plataforma=res.plataforma,
        conta=res.conta,
        nome_destinatario=res.nome_destinatario,
        avisos=res.avisos,
    )


@router.post("/faturamento/conferir-frete", response_model=ConferirFreteOut)
async def conferir_frete_faturamento(
    body: ConferirFreteIn,
    _user: Annotated[User, Depends(_painel_edit)],
) -> ConferirFreteOut:
    """Cota o frete no Melhor Envio e confere contra o frete projetado.

    Fluxo "impressão tipo próprio" (Amazon): depois da NF emitida, cota a
    etiqueta (CEP origem/destino + caixa) e vê se a menor opção cabe no frete
    projetado da Tabela de Preços. Só CONSULTA o ME (não compra); a decisão
    `libera` é o sinal pra gerar a etiqueta. 502 se o ME falhar; 400 se o token
    não estiver configurado."""
    produtos = [p.model_dump(mode="json") for p in body.produtos]
    client = MelhorEnvioClient()
    try:
        cotacoes = await client.calcular_frete(
            from_cep=body.from_cep,
            to_cep=body.to_cep,
            produtos=produtos,
            servicos=body.servicos,
        )
    except MelhorEnvioConfigError:
        raise HTTPException(400, detail={"code": "melhor_envio_token_missing"})
    except MelhorEnvioApiError as exc:
        logger.warning("nf_conferir_frete_api_erro", status=exc.status, body=exc.body[:200])
        raise HTTPException(502, detail={"code": "melhor_envio_erro"})
    res = melhor_envio.conferir_frete(cotacoes, body.frete_projetado)
    return _montar_conferir_out(res, cotacoes)


def _montar_conferir_out(res, cotacoes) -> ConferirFreteOut:
    """Monta o ConferirFreteOut da conferência + lista de cotações."""
    return ConferirFreteOut(
        libera=res.libera,
        motivo=res.motivo,
        menor_frete=res.menor_frete,
        servico_escolhido=res.servico_escolhido,
        empresa_escolhida=res.empresa_escolhida,
        prazo_dias=res.prazo_dias,
        frete_projetado=res.frete_projetado,
        diferenca=res.diferenca,
        cotacoes=[
            {
                "servico_id": c.servico_id,
                "servico_nome": c.servico_nome,
                "empresa": c.empresa,
                "preco": c.preco,
                "prazo_dias": c.prazo_dias,
                "erro": c.erro,
            }
            for c in cotacoes
        ],
    )


@router.post(
    "/faturamento/conferir-frete/pedido", response_model=ConferirFretePedidoOut
)
async def conferir_frete_pedido(
    body: ConferirFretePedidoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_painel_edit)],
) -> ConferirFretePedidoOut:
    """Confere-frete AUTO ponta a ponta a partir do nº do pedido.

    Junta o /auto (resolve CEP/caixa/frete projetado do pedido) com o
    /conferir-frete (cota no Melhor Envio e decide `libera`) numa chamada só —
    é o "fio automático" da impressão tipo próprio (Amazon): um clique cota e
    já mostra se libera. Reusa `resolver_prefill` e o cliente do ME.

    400 nf_origem_cep_missing (CEP origem no .env) / 404 nf_pedido_nao_encontrado
    / 400 melhor_envio_token_missing / 502 melhor_envio_erro."""
    origem_cep = (get_settings().nf_origem_cep or "").strip()
    if not origem_cep:
        raise HTTPException(400, detail={"code": "nf_origem_cep_missing"})
    try:
        pref = await nf_frete_auto.resolver_prefill(session, body.pedido_bling)
    except nf_frete_auto.PedidoNaoEncontrado:
        raise HTTPException(404, detail={"code": "nf_pedido_nao_encontrado"})

    produtos = [
        {
            "id": p.id,
            "width": str(p.width),
            "height": str(p.height),
            "length": str(p.length),
            "weight": str(p.weight),
            "insurance_value": "0",
            "quantity": p.quantity,
        }
        for p in pref.produtos
    ]
    client = MelhorEnvioClient()
    try:
        cotacoes = await client.calcular_frete(
            from_cep=origem_cep,
            to_cep=pref.to_cep,
            produtos=produtos,
            servicos=body.servicos,
        )
    except MelhorEnvioConfigError:
        raise HTTPException(400, detail={"code": "melhor_envio_token_missing"})
    except MelhorEnvioApiError as exc:
        logger.warning(
            "nf_conferir_frete_pedido_api_erro", status=exc.status, body=exc.body[:200]
        )
        raise HTTPException(502, detail={"code": "melhor_envio_erro"})

    res = melhor_envio.conferir_frete(cotacoes, pref.frete_projetado)
    return ConferirFretePedidoOut(
        pedido_bling=body.pedido_bling,
        from_cep=origem_cep,
        to_cep=pref.to_cep,
        plataforma=pref.plataforma,
        conta=pref.conta,
        nome_destinatario=pref.nome_destinatario,
        avisos=pref.avisos,
        prefill_ok=pref.frete_projetado is not None,
        conferencia=_montar_conferir_out(res, cotacoes),
    )


# ---------------------------------------------------------------------------
# ENFILEIRAR IMPORTAÇÃO (Fase 3a-4) — cria o outbox pro executor AdsPower.
#
# Em vez de baixar o CSV pra importar à mão, o admin ENFILEIRA os pedidos: a
# planilha é gerada por FATURADOR (cada login/AdsPower vira um comando com seu
# subconjunto + CSV congelado) e o executor local faz poll de /agent/lease,
# loga no Bling destino e importa. O status de cada pedido vai pra
# nf_faturamento.status_faturamento ('processando' aqui; 'ok'/'erro' no result).
# ---------------------------------------------------------------------------


class EnfileirarOut(BaseModel):
    comandos: int
    pedidos_ok: int
    pulados: list[dict]


async def _marcar_faturamento(
    session: AsyncSession, numeros: list[str], *, status_txt: str, erro: str | None
) -> None:
    """Upsert do status_faturamento de cada pedido em nf_faturamento (chave
    única pedido_bling). Não toca as etapas de etiqueta/impressão."""
    if not numeros:
        return
    existentes = {
        f.pedido_bling: f
        for f in (
            await session.execute(
                select(NfFaturamento).where(NfFaturamento.pedido_bling.in_(numeros))
            )
        ).scalars().all()
    }
    for numero in numeros:
        row = existentes.get(numero)
        if row is None:
            row = NfFaturamento(pedido_bling=numero)
            session.add(row)
        row.status_faturamento = status_txt
        row.erro_faturamento = erro


async def _marcar_etiqueta(
    session: AsyncSession, numeros: list[str], *, status_txt: str, erro: str | None
) -> None:
    """Upsert do status_etiqueta de cada pedido em nf_faturamento (chave única
    pedido_bling). Não toca as etapas de faturamento/impressão."""
    if not numeros:
        return
    existentes = {
        f.pedido_bling: f
        for f in (
            await session.execute(
                select(NfFaturamento).where(NfFaturamento.pedido_bling.in_(numeros))
            )
        ).scalars().all()
    }
    for numero in numeros:
        row = existentes.get(numero)
        if row is None:
            row = NfFaturamento(pedido_bling=numero)
            session.add(row)
        row.status_etiqueta = status_txt
        row.erro_etiqueta = erro


async def _marcar_enviado_etiqueta(
    session: AsyncSession, numeros: list[str]
) -> None:
    """Move os pedidos pra "Enviado Etiqueta" no Bling depois que a etiqueta foi
    capturada — é o gatilho que faz o pedido aparecer no Controle de Estoque pra
    impressão. Espelha a situação na `bling_orders` local pra a tela refletir na
    hora (sem esperar o próximo sync).

    BEST-EFFORT: o comando já fechou e a etiqueta está guardada; falha no Bling
    só loga (a situação pode ser corrigida à mão), nunca derruba o /result.
    """
    if not numeros:
        return
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero.in_(numeros))
            .where(BlingOrder.bling_id.is_not(None))
            .distinct()
        )
    ).all()
    # bling_orders tem uma linha por ITEM — o pedido só precisa de um PATCH.
    pendentes = {
        r.numero: r.bling_id
        for r in rows
        if str(r.situacao or "") != str(_SITUACAO_ENVIADO_ETIQUETA)
    }
    if not pendentes:
        return
    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("nf_enviado_etiqueta_sem_bling", numeros=list(pendentes))
        return
    primeiro = True
    for numero, bling_id in pendentes.items():
        if not primeiro:
            await asyncio.sleep(_BLING_PATCH_PAUSA_S)
        primeiro = False
        try:
            await client.update_order_situacao(
                int(bling_id), _SITUACAO_ENVIADO_ETIQUETA
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "nf_enviado_etiqueta_falhou", numero=numero, erro=str(exc)
            )
            continue
        await session.execute(
            update(BlingOrder)
            .where(BlingOrder.numero == numero)
            .values(situacao=str(_SITUACAO_ENVIADO_ETIQUETA))
        )
        logger.info("nf_enviado_etiqueta", numero=numero)


async def _pedidos_sem_estoque(
    session: AsyncSession, numeros: list[str]
) -> dict[str, list[str]]:
    """Pedidos com algum item de saldo VIRTUAL negativo — conferido AO VIVO.

    A fonte é o Bling (`get_product` → `estoque.saldoVirtualTotal`), nunca o
    `products.stock` local: o sync clampa negativo pra 0 no cache (pro push),
    então o local é estruturalmente cego pra saldo negativo. O virtual já
    desconta a reserva dos pedidos em aberto: 0 ainda é atendível, negativo é
    que a peça não existe. SKU sem evidência de negativo NÃO bloqueia (sem
    integração/id/erro na consulta cai no check local, best-effort). Devolve
    numero → SKUs.
    """
    if not numeros:
        return {}
    itens = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.item_codigo,
                BlingOrder.item_produto_id,
            )
            .where(BlingOrder.numero.in_(numeros))
            .where(BlingOrder.item_codigo.is_not(None))
            .distinct()
        )
    ).all()
    skus = {r.item_codigo for r in itens if r.item_codigo}
    if not skus:
        return {}
    pid_por_sku: dict[str, int] = {}
    for r in itens:
        if r.item_codigo and r.item_produto_id:
            pid_por_sku.setdefault(r.item_codigo, int(r.item_produto_id))

    client = await nf_emissao_gerar._bling_client_opt(session)
    negativos: set[str] = set()
    fallback_local: set[str] = set()
    if client is None:
        logger.warning("nf_sem_estoque_sem_bling_fallback_local", skus=len(skus))
        fallback_local = skus
    else:
        for sku in skus:
            pid = pid_por_sku.get(sku)
            if pid is None:
                fallback_local.add(sku)
                continue
            try:
                data = await client.get_product(pid)
                saldo = (data.get("estoque") or {}).get("saldoVirtualTotal")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "nf_sem_estoque_bling_falhou", sku=sku, erro=str(exc)
                )
                fallback_local.add(sku)
                continue
            if saldo is None:
                fallback_local.add(sku)
            elif float(saldo) < 0:
                negativos.add(sku)
    if fallback_local:
        negativos |= set(
            (
                await session.execute(
                    select(Product.sku).where(
                        Product.sku.in_(fallback_local), Product.stock < 0
                    )
                )
            ).scalars().all()
        )
    faltando: dict[str, list[str]] = {}
    for r in itens:
        if r.item_codigo in negativos and r.item_codigo not in faltando.get(
            r.numero, []
        ):
            faltando.setdefault(r.numero, []).append(r.item_codigo)
    return faltando


async def _marcar_aguardando_cancelamento(
    session: AsyncSession, numeros: list[str]
) -> None:
    """Move os pedidos sem estoque pra "Aguardando Cancelamento" no Bling.

    BEST-EFFORT igual ao `_marcar_enviado_etiqueta`: falha no Bling só loga (o
    pedido fica de fora da fila do mesmo jeito, e a situação pode ser corrigida
    à mão). Espelha na `bling_orders` pra a tela refletir sem esperar o sync.
    """
    if not numeros:
        return
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero.in_(numeros))
            .where(BlingOrder.bling_id.is_not(None))
            .distinct()
        )
    ).all()
    pendentes = {
        r.numero: r.bling_id
        for r in rows
        if str(r.situacao or "") != str(_SITUACAO_AGUARDANDO_CANCELAMENTO)
    }
    if not pendentes:
        return
    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("nf_aguardando_cancelamento_sem_bling", numeros=list(pendentes))
        return
    for numero, bling_id in pendentes.items():
        try:
            await client.update_order_situacao(
                int(bling_id), _SITUACAO_AGUARDANDO_CANCELAMENTO
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "nf_aguardando_cancelamento_falhou", numero=numero, erro=str(exc)
            )
            continue
        await session.execute(
            update(BlingOrder)
            .where(BlingOrder.numero == numero)
            .values(situacao=str(_SITUACAO_AGUARDANDO_CANCELAMENTO))
        )
        logger.info("nf_aguardando_cancelamento", numero=numero)


async def _enfileirar_emissao_upseller(
    session: AsyncSession, faturador_id: UUID | None, numeros: list[str]
) -> bool:
    """Auto-enfileira UM comando `emitir_nf_upseller` por pedido depois que a
    importação avulsa fecha no Upseller.

    Importar o avulso NÃO emite a nota: o pedido cai na fila "Para Emitir" e
    ainda faltam três passos na tela (emitir a NF, exportar o XML e subir esse
    XML no pedido ORIGINAL do marketplace). Só depois disso a etiqueta fica
    imprimível — por isso a captura (`imprimir_etiqueta`) é encadeada quando
    ESTE comando fecha 'ok', não quando o import fecha.

    Só pra faturador `modo='upseller'`. Grão = 1 pedido: um pedido que falhe na
    tela não segura os outros. Dedupe: pula quem já tem um comando de emissão
    ATIVO (pending/claimed) na fila.

    Devolve se o faturador é Upseller — quem chama usa isso pra saber se o
    faturamento fechou (Bling) ou ainda está em curso (Upseller)."""
    if not numeros or faturador_id is None:
        return False
    fat = await session.get(NfFaturador, faturador_id)
    if fat is None or (fat.modo or "").lower() != "upseller":
        return False
    em_fila = await _em_fila(session, "emitir_nf_upseller")
    criar = [n for n in numeros if n not in em_fila]
    for numero in criar:
        session.add(
            NfCommand(
                faturador_id=faturador_id,
                action="emitir_nf_upseller",
                numeros=[numero],
                planilha=b"",
                nome_arquivo="",
                status="pending",
                source="auto",
                ads_power=fat.ads_power,
            )
        )
    return True


async def _enfileirar_emissao_bling(
    session: AsyncSession, faturador_id: UUID | None, numeros: list[str]
) -> bool:
    """Auto-enfileira UM comando `emitir_nf_bling` por pedido depois que a
    importação avulsa fecha no Bling destino.

    Importar a venda avulsa (tela "Importação de Vendas") NÃO emite a nota —
    ela só cria o pedido. A marionete precisa ir na tela de Vendas do Bling
    destino e emitir a NF de cada pedido; só quando ESTE comando fecha 'ok' o
    faturamento encerra e a captura da DANFE (`capturar_nf`) é encadeada.

    Só pra faturador `modo='bling'`. Grão = 1 pedido: um pedido que falhe na
    tela não segura os outros. Dedupe: pula quem já tem um comando de emissão
    ATIVO (pending/claimed) na fila.

    Devolve se o faturador é Bling — quem chama usa isso pra saber se o
    faturamento fechou de vez ou segue em curso (emissão pendente)."""
    if not numeros or faturador_id is None:
        return False
    fat = await session.get(NfFaturador, faturador_id)
    if fat is None or (fat.modo or "").lower() != "bling":
        return False
    em_fila = await _em_fila(session, "emitir_nf_bling")
    criar = [n for n in numeros if n not in em_fila]
    for numero in criar:
        session.add(
            NfCommand(
                faturador_id=faturador_id,
                action="emitir_nf_bling",
                numeros=[numero],
                planilha=b"",
                nome_arquivo="",
                status="pending",
                source="auto",
                ads_power=fat.ads_power,
            )
        )
    return True


async def _enfileirar_captura_nf(
    session: AsyncSession,
    numeros: list[str],
    *,
    faturador_id: UUID | None = None,
    ads_power: str | None = None,
) -> int:
    """Cria UM comando `capturar_nf` por pedido: a marionete baixa a DANFE
    ("Gerar PDF DANFE") do Bling destino e sobe em /agent/nf — no fluxo
    correios/ML a etiqueta sai junto com a NF, não com declaração. Best-effort:
    não marca etapa no painel (capturar_nf não tem coluna própria) e a falha
    dele não derruba o faturamento já emitido. Dedupe via comando ativo."""
    if not numeros:
        return 0
    em_fila = await _em_fila(session, "capturar_nf")
    criar = [n for n in numeros if n not in em_fila]
    for numero in criar:
        session.add(
            NfCommand(
                faturador_id=faturador_id,
                action="capturar_nf",
                numeros=[numero],
                planilha=b"",
                nome_arquivo="",
                status="pending",
                source="auto",
                ads_power=ads_power,
            )
        )
    return len(criar)


async def _em_fila(session: AsyncSession, action: str) -> set[str]:
    """Pedidos que já têm um comando ATIVO (pending/claimed) dessa ação."""
    ativos = (
        await session.execute(
            select(NfCommand.numeros).where(
                NfCommand.action == action,
                NfCommand.status.in_(["pending", "claimed"]),
            )
        )
    ).scalars().all()
    return {n for arr in ativos for n in (arr or [])}


async def _criar_comandos_etiqueta(
    session: AsyncSession,
    numeros: list[str],
    *,
    faturador_id: UUID | None = None,
    ads_power: str | None = None,
) -> int:
    """Cria UM comando `imprimir_etiqueta` por pedido (grão = 1 etiqueta), com o
    AdsPower do comando (perfil do cadastro Etiqueta no fluxo ML). Dedupe: pula
    quem já tem etiqueta 'ok' ou um comando de etiqueta ATIVO (pending/claimed)."""
    if not numeros:
        return 0
    ja_ok = {
        f.pedido_bling
        for f in (
            await session.execute(
                select(NfFaturamento).where(
                    NfFaturamento.pedido_bling.in_(numeros),
                    NfFaturamento.status_etiqueta == "ok",
                )
            )
        ).scalars().all()
    }
    em_fila = await _em_fila(session, "imprimir_etiqueta")
    criar = [n for n in numeros if n not in ja_ok and n not in em_fila]
    for numero in criar:
        session.add(
            NfCommand(
                faturador_id=faturador_id,
                action="imprimir_etiqueta",
                numeros=[numero],
                planilha=b"",
                nome_arquivo="",
                status="pending",
                source="auto",
                ads_power=ads_power,
            )
        )
    if criar:
        await _marcar_etiqueta(session, criar, status_txt="processando", erro=None)
    return len(criar)


async def _resolver_comandos_etiqueta(
    session: AsyncSession, numeros: list[str]
) -> int:
    """Fecha como 'done' os comandos `imprimir_etiqueta` não concluídos cujos
    pedidos tiveram a etiqueta capturada pelo LOTE — o comando individual não
    precisa mais rodar. Só fecha quando TODOS os numeros do comando foram
    cobertos. Inclui os 'failed': o recuperador devolve failed→pending, então
    sem isso o comando voltaria a tentar pra sempre uma etiqueta que já saiu
    da fila do Upseller."""
    if not numeros:
        return 0
    cobertos = set(numeros)
    cmds = (
        await session.execute(
            select(NfCommand).where(
                NfCommand.action == "imprimir_etiqueta",
                NfCommand.status.in_(["pending", "claimed", "failed"]),
            )
        )
    ).scalars().all()
    fechados = 0
    for cmd in cmds:
        if cmd.numeros and set(cmd.numeros) <= cobertos:
            cmd.status = "done"
            cmd.result = "etiqueta capturada pelo lote"
            cmd.completed_at = datetime.now(UTC)
            fechados += 1
    return fechados


async def _enfileirar_etiqueta_ml(
    session: AsyncSession, numeros: list[str]
) -> int:
    """Auto-enfileira a IMPORTAÇÃO da etiqueta no Upseller (fluxo ML): pedidos com
    faturador Bling + cadastro de Etiqueta 'upseller'. A NF já saiu do Bling, então
    o Upseller importa a venda avulsa com NF-e=NÃO só pra puxar a etiqueta. Um
    comando `import_etiqueta` por cadastro de Etiqueta (o AdsPower do cadastro
    Etiqueta viaja no próprio comando). A captura (`imprimir_etiqueta`) é
    encadeada quando este import fechar 'ok'."""
    if not numeros:
        return 0
    res = await nf_emissao_gerar.gerar_etiqueta_upseller(session, numeros)
    for bloco in res.blocos:
        session.add(
            NfCommand(
                faturador_id=None,
                action="import_etiqueta",
                numeros=bloco.numeros,
                planilha=bloco.planilha,
                nome_arquivo=bloco.nome_arquivo,
                status="pending",
                source="auto",
                ads_power=bloco.ads_power,
            )
        )
        await _marcar_etiqueta(
            session, bloco.numeros, status_txt="processando", erro=None
        )
    return len(res.blocos)


@router.post("/faturamento/enfileirar", response_model=EnfileirarOut)
async def enfileirar_importacao(
    body: GerarPlanilhaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_painel_edit)],
) -> EnfileirarOut:
    """Enfileira a importação avulsa dos pedidos escolhidos: gera a planilha por
    faturador, cria um NfCommand por faturador (CSV congelado) e marca cada
    pedido como 'processando'. 422 se nenhum pedido pôde ser gerado.

    Pedido sem estoque (saldo virtual negativo) NÃO entra na fila: vai pra
    Aguardando Cancelamento no Bling e sai como pulado — não faz sentido emitir
    NF/etiqueta de peça que não existe."""
    sem_estoque = await _pedidos_sem_estoque(session, body.numeros)
    pulados_estoque = [
        {
            "numero": numero,
            "motivo": f"sem estoque ({', '.join(skus)}) — Aguardando Cancelamento",
        }
        for numero, skus in sem_estoque.items()
    ]
    if sem_estoque:
        await _marcar_aguardando_cancelamento(session, list(sem_estoque))
        # Erro por pedido com os SKUs negativos — o painel mostra no tooltip
        # do badge "Sem estoque" QUAL peça travou (sem ir caçar no log).
        for numero, skus in sem_estoque.items():
            await _marcar_faturamento(
                session,
                [numero],
                status_txt="sem_estoque",
                erro=f"Aguardando Cancelamento — saldo negativo: {', '.join(skus)}",
            )
        await session.commit()
    numeros = [n for n in body.numeros if n not in sem_estoque]
    res = (
        await nf_emissao_gerar.gerar_por_faturador(session, numeros)
        if numeros
        else nf_emissao_gerar.ResultadoPorFaturador(blocos=[], pulados=[])
    )
    pulados = pulados_estoque + [
        {"numero": p.numero, "motivo": p.motivo} for p in res.pulados
    ]
    if not res.blocos:
        raise HTTPException(
            422,
            detail={"code": "nf_nenhum_pedido_gerado", "pulados": pulados},
        )
    total_ok = 0
    for bloco in res.blocos:
        session.add(
            NfCommand(
                faturador_id=bloco.faturador_id,
                action="import_avulsa",
                numeros=bloco.numeros,
                planilha=bloco.planilha,
                nome_arquivo=bloco.nome_arquivo,
                status="pending",
                source="manual",
                created_by=user.id,
            )
        )
        await _marcar_faturamento(
            session, bloco.numeros, status_txt="processando", erro=None
        )
        total_ok += len(bloco.numeros)
    await session.commit()
    logger.info(
        "nf_importacao_enfileirada",
        comandos=len(res.blocos),
        pedidos_ok=total_ok,
        sem_estoque=len(sem_estoque),
    )
    return EnfileirarOut(
        comandos=len(res.blocos),
        pedidos_ok=total_ok,
        pulados=pulados,
    )


# ---------------------------------------------------------------------------
# AGENTE (executor de importação AdsPower) — superfície M2M gated por token.
#
# O executor local faz poll de /agent/lease, abre o AdsPower do faturador (login
# entregue no lease), importa a planilha no Bling destino e reporta em
# /agent/commands/{id}/result. A planilha crua sai por /agent/commands/{id}/
# planilha. Os três são guardados pelo X-Agent-Token (vazio = fechado/401).
# ---------------------------------------------------------------------------


async def _require_nf_agent_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    expected = get_settings().nf_agent_token
    if (
        not expected
        or not x_agent_token
        or not secrets.compare_digest(x_agent_token, expected)
    ):
        raise HTTPException(401, detail={"code": "nf_agent_unauthorized"})


class NfAgentLeaseIn(BaseModel):
    limit: int = Field(default=5, ge=1, le=50)
    # Filtros opcionais por action: o loop contínuo do executor exclui
    # `imprimir_etiqueta` (impressão roda em passe horário separado) e o passe
    # horário pede SÓ `imprimir_etiqueta`. Vazio/None = sem filtro (compat).
    actions: list[str] | None = None
    exclude_actions: list[str] | None = None


class NfAgentCommandOut(BaseModel):
    id: UUID
    faturador_id: UUID | None
    faturador_nome: str | None
    ads_power: str | None
    usuario: str | None
    senha: str | None
    action: str
    numeros: list[str]
    # Mesma ordem de `numeros`: o número do pedido NA PLATAFORMA (numeroloja do
    # Bling). É por ele que a fila do Upseller lista o pedido; o `numeros` (nº do
    # Bling) segue sendo a chave de gravação da etiqueta. Cai no próprio número
    # quando o pedido não tem numeroloja (avulso digitado à mão).
    numeros_plataforma: list[str]
    nome_arquivo: str
    planilha_b64: str
    ncm: str | None


class NfAgentResultIn(BaseModel):
    status: str  # "done" | "failed"
    result: str | None = None


@router.post("/agent/lease", dependencies=[Depends(_require_nf_agent_token)])
async def nf_agent_lease(
    body: NfAgentLeaseIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reivindica até `limit` comandos pendentes (FOR UPDATE SKIP LOCKED), vira
    pra 'claimed' e devolve cada um com o login do faturador (AdsPower/usuário/
    senha descriptografada) + a planilha em base64. Seguro sob concorrência."""
    stmt = (
        select(NfCommand)
        .where(NfCommand.status == "pending")
        .order_by(NfCommand.created_at.asc())
        .limit(body.limit)
        .with_for_update(skip_locked=True)
    )
    if body.actions:
        stmt = stmt.where(NfCommand.action.in_(body.actions))
    if body.exclude_actions:
        stmt = stmt.where(NfCommand.action.not_in(body.exclude_actions))
    claimed = (await session.execute(stmt)).scalars().all()

    out: list[NfAgentCommandOut] = []
    if claimed:
        now = datetime.now(UTC)
        fat_ids = {c.faturador_id for c in claimed if c.faturador_id}
        faturadores = {
            f.id: f
            for f in (
                await session.execute(
                    select(NfFaturador).where(NfFaturador.id.in_(fat_ids))
                )
            ).scalars().all()
        } if fat_ids else {}
        todos_numeros = {n for c in claimed for n in (c.numeros or [])}
        plataforma_por_numero = {
            r.numero: r.numeroloja
            for r in (
                await session.execute(
                    select(BlingOrder.numero, BlingOrder.numeroloja)
                    .where(BlingOrder.numero.in_(todos_numeros))
                    .where(BlingOrder.numeroloja.is_not(None))
                    .distinct()
                )
            ).all()
            if r.numeroloja
        } if todos_numeros else {}
        for cmd in claimed:
            cmd.status = "claimed"
            cmd.claimed_at = now
            cmd.attempts += 1
            fat = faturadores.get(cmd.faturador_id) if cmd.faturador_id else None
            out.append(
                NfAgentCommandOut(
                    id=cmd.id,
                    faturador_id=cmd.faturador_id,
                    faturador_nome=fat.nome if fat else None,
                    # etiqueta ML carrega o perfil do cadastro Etiqueta no
                    # próprio comando; senão cai no ads_power do faturador.
                    ads_power=cmd.ads_power or (fat.ads_power if fat else None),
                    usuario=fat.usuario if fat else None,
                    senha=(decrypt(fat.senha_enc) if fat and fat.senha_enc else None),
                    action=cmd.action,
                    numeros=list(cmd.numeros or []),
                    numeros_plataforma=[
                        plataforma_por_numero.get(n) or n
                        for n in (cmd.numeros or [])
                    ],
                    nome_arquivo=cmd.nome_arquivo,
                    planilha_b64=base64.b64encode(cmd.planilha).decode("ascii"),
                    ncm=(fat.ncm if fat else None),
                )
            )
        await session.commit()

    logger.info("nf_agent_lease", leased=len(out))
    return {"commands": [o.model_dump(mode="json") for o in out]}


@router.get(
    "/agent/commands/{command_id}/planilha",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_command_planilha(
    command_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Planilha crua do comando (o arquivo que o executor sobe no destino). CSV
    pro Bling (Importar vendas), XLSX pro Upseller — o media type vem da extensão
    do nome."""
    cmd = await session.get(NfCommand, command_id)
    if cmd is None:
        raise HTTPException(404, detail={"code": "nf_command_not_found"})
    nome = cmd.nome_arquivo.lower()
    if nome.endswith(".xlsx"):
        media = nf_upseller.UPSELLER_MEDIA
    else:
        media = nf_relatorio.CSV_MEDIA
    return Response(
        content=cmd.planilha,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{cmd.nome_arquivo}"'
        },
    )


@router.post(
    "/agent/commands/{command_id}/result",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_command_result(
    command_id: UUID,
    body: NfAgentResultIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reporta o resultado de um comando. Marca a etapa do pedido conforme a
    `action`: 'import_avulsa' → status_faturamento (Upseller e Bling seguem
    'processando' e encadeiam a emissão da NF; ML encadeia o import da
    etiqueta); 'emitir_nf_upseller' → fecha o faturamento e encadeia a captura
    da etiqueta; 'emitir_nf_bling' → fecha o faturamento e encadeia a captura
    da DANFE ('capturar_nf', best-effort); 'import_etiqueta' → ao fechar 'ok'
    encadeia a captura; 'imprimir_etiqueta' → status_etiqueta. 'done' vira 'ok',
    'failed' vira 'erro' + guarda a mensagem."""
    cmd = await session.get(NfCommand, command_id)
    if cmd is None:
        raise HTTPException(404, detail={"code": "nf_command_not_found"})
    new_status = "done" if body.status == "done" else "failed"
    cmd.status = new_status
    cmd.result = (body.result or "")[:2000] or None
    cmd.completed_at = datetime.now(UTC)

    numeros = list(cmd.numeros or [])
    if cmd.action == "imprimir_etiqueta":
        if new_status == "done":
            await _marcar_etiqueta(session, numeros, status_txt="ok", erro=None)
            # Etiqueta na mão → o pedido entra na fila de impressão do DaVinci.
            await _marcar_enviado_etiqueta(session, numeros)
        else:
            await _marcar_etiqueta(
                session, numeros, status_txt="erro",
                erro=cmd.result or "falha na etiqueta",
            )
    elif cmd.action == "emitir_nf_upseller":
        # Emitiu a NF, exportou o XML e subiu no pedido ORIGINAL — só AGORA o
        # faturamento fechou de fato e a etiqueta fica imprimível.
        if new_status == "done":
            await _marcar_faturamento(session, numeros, status_txt="ok", erro=None)
            await _criar_comandos_etiqueta(
                session,
                numeros,
                faturador_id=cmd.faturador_id,
                ads_power=cmd.ads_power,
            )
        else:
            await _marcar_faturamento(
                session, numeros, status_txt="erro",
                erro=cmd.result or "falha ao emitir a NF no Upseller",
            )
    elif cmd.action == "emitir_nf_bling":
        # Emitiu a NF na tela de Vendas do Bling destino — o faturamento fechou.
        # A captura da DANFE é best-effort: alimenta a junção etiqueta+NF do
        # fluxo correios/ML, mas a falha dela não desfaz a emissão.
        if new_status == "done":
            await _marcar_faturamento(session, numeros, status_txt="ok", erro=None)
            await _enfileirar_captura_nf(
                session,
                numeros,
                faturador_id=cmd.faturador_id,
                ads_power=cmd.ads_power,
            )
        else:
            await _marcar_faturamento(
                session, numeros, status_txt="erro",
                erro=cmd.result or "falha ao emitir a NF no Bling",
            )
    elif cmd.action == "capturar_nf":
        # Sem etapa própria no painel: a NF já foi emitida; a captura da DANFE
        # só afeta a junção etiqueta+NF. Falha vira log, não erro do pedido.
        if new_status != "done":
            logger.warning(
                "nf_capturar_nf_falhou",
                command_id=str(command_id),
                numeros=numeros,
                erro=cmd.result,
            )
    elif cmd.action == "import_etiqueta":
        # Importou a venda avulsa NF-e=NÃO no Upseller — agora encadeia a captura
        # da etiqueta (imprimir_etiqueta) no mesmo AdsPower do cadastro Etiqueta.
        if new_status == "done":
            await _criar_comandos_etiqueta(
                session, numeros, ads_power=cmd.ads_power
            )
        else:
            await _marcar_etiqueta(
                session, numeros, status_txt="erro",
                erro=cmd.result or "falha ao importar a etiqueta",
            )
    elif new_status == "done":
        # Importar o avulso é só o 1º passo — a NF ainda não saiu, então o
        # pedido segue 'processando' até a emissão fechar (Upseller ou Bling).
        emitindo = await _enfileirar_emissao_upseller(
            session, cmd.faturador_id, numeros
        ) or await _enfileirar_emissao_bling(session, cmd.faturador_id, numeros)
        if not emitindo:
            await _marcar_faturamento(session, numeros, status_txt="ok", erro=None)
        await _enfileirar_etiqueta_ml(session, numeros)
    else:
        await _marcar_faturamento(
            session, numeros, status_txt="erro", erro=cmd.result or "falha na importação"
        )

    await session.commit()
    logger.info("nf_agent_result", command_id=str(command_id), status=new_status)
    return {"ok": True, "status": new_status}


@router.post(
    "/agent/etiqueta",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_etiqueta(
    session: Annotated[AsyncSession, Depends(get_session)],
    pedido_bling: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    destinatario_nome: Annotated[str | None, Form()] = None,
) -> dict:
    """Recebe o PDF CRU da etiqueta (a marionete pega no Upseller/Correios),
    aplica a regra de visualização (item 2: remetente=destinatário, sem bloco NF,
    sem logo do marketplace) e faz UPSERT do PDF transformado em
    `nf_etiqueta_arquivo` por pedido — de onde o Controle de Estoque serve o botão
    "Imprimir Etiqueta". O nome do destinatário é lido da própria etiqueta quando
    `destinatario_nome` não vem.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "nf_etiqueta_vazia"})
    if len(raw) > _ETIQUETA_MAX_BYTES:
        raise HTTPException(413, detail={"code": "nf_etiqueta_grande"})
    try:
        transformado = transformar_etiqueta(raw, (destinatario_nome or "").strip() or None)
    except EtiquetaTransformError as exc:
        raise HTTPException(422, detail={"code": "nf_etiqueta_invalida", "erro": str(exc)}) from exc

    filename = f"etiqueta_{pedido_bling}.pdf"
    row = (
        await session.execute(
            select(NfEtiquetaArquivo).where(
                NfEtiquetaArquivo.pedido_bling == pedido_bling
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = NfEtiquetaArquivo(pedido_bling=pedido_bling)
        session.add(row)
    row.filename = filename
    row.content_type = "application/pdf"
    row.size_bytes = len(transformado)
    row.blob = transformado
    await session.commit()
    logger.info(
        "nf_agent_etiqueta", pedido_bling=pedido_bling, size=len(transformado)
    )
    return {"ok": True, "pedido_bling": pedido_bling, "size_bytes": len(transformado)}


@router.post(
    "/agent/etiqueta-lote",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_etiqueta_lote(
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Recebe o PDF ÚNICO da impressão em LOTE do Upseller (todas as etiquetas
    marcadas de uma vez), fatia em uma etiqueta por pedido, casa cada fatia com
    o pedido do davinci, transforma (mesma regra do /agent/etiqueta) e grava.
    Pros pedidos gravados: status_etiqueta='ok', fecha os comandos
    `imprimir_etiqueta` cobertos e move pra "Enviado Etiqueta" no Bling.

    Casamento em cascata: 1º pelo nº da plataforma impresso na etiqueta
    ("Pedido: X" → bling_orders.numeroloja, Shopee); 2º pelo CPF/CNPJ da
    declaração de conteúdo → bling_orders.documento_destinatario (chave mais
    específica, cobre os layouts sem o nº da plataforma); 3º pelo NOME do
    destinatário + SKU da declaração (TikTok). Todos só quando inequívoco — o
    que não casar volta em `nao_casadas` pra tratamento humano, nunca chuta.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "nf_etiqueta_lote_vazio"})
    if len(raw) > _ETIQUETA_LOTE_MAX_BYTES:
        raise HTTPException(413, detail={"code": "nf_etiqueta_lote_grande"})
    try:
        fatias = nf_etiqueta_lote.fatiar_lote(raw)
    except nf_etiqueta_lote.EtiquetaLoteError as exc:
        raise HTTPException(
            422, detail={"code": "nf_etiqueta_lote_invalido", "erro": str(exc)}
        ) from exc

    # 1) Casamento primário: nº da plataforma na etiqueta → numeroloja. Cada
    #    fatia traz TODOS os candidatos (no layout DANFE o "Pedido:" vem com o
    #    código de triagem e o numeroloja real aparece solto) — quem existir no
    #    banco vence.
    numerolojas = list(
        dict.fromkeys(n for f in fatias for n in f.numerolojas)
    )
    por_loja: dict[str, tuple[str, str | None]] = {}
    if numerolojas:
        rows = (
            await session.execute(
                select(
                    BlingOrder.numeroloja,
                    BlingOrder.numero,
                    BlingOrder.nome_destinatario,
                )
                .where(BlingOrder.numeroloja.in_(numerolojas))
                .where(BlingOrder.numero.is_not(None))
                .distinct()
            )
        ).all()
        por_loja = {r.numeroloja: (r.numero, r.nome_destinatario) for r in rows}

    # 2) Candidatos dos casamentos reserva (CPF/CNPJ e destinatário + SKU),
    #    janela recente.
    cutoff = datetime.now(UTC) - _ETIQUETA_LOTE_JANELA
    cand_rows = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.nome_destinatario,
                BlingOrder.documento_destinatario,
                BlingOrder.item_codigo,
            )
            .where(
                BlingOrder.numero.is_not(None),
                or_(
                    BlingOrder.nome_destinatario.is_not(None),
                    BlingOrder.documento_destinatario.is_not(None),
                ),
                BlingOrder.data >= cutoff,
            )
            .distinct()
        )
    ).all()
    cand_map: dict[str, tuple[str | None, str | None, set[str]]] = {}
    for r in cand_rows:
        _nome, _doc, skus = cand_map.setdefault(
            r.numero, (r.nome_destinatario, r.documento_destinatario, set())
        )
        if r.item_codigo:
            skus.add(r.item_codigo)
    cand_doc = [(n, doc, skus) for n, (_nome, doc, skus) in cand_map.items()]
    cand_nome = [(n, nome, skus) for n, (nome, _doc, skus) in cand_map.items()]

    gravadas: list[str] = []
    nao_casadas: list[dict] = []
    falhas: list[dict] = []
    for i, fatia in enumerate(fatias):
        numero: str | None = None
        nome: str | None = None
        for candidato_loja in fatia.numerolojas:
            if candidato_loja in por_loja:
                numero, nome = por_loja[candidato_loja]
                break
        if numero is None:
            numero = nf_etiqueta_lote.casa_por_documento(
                fatia.documentos, fatia.texto, cand_doc
            )
        if numero is None:
            numero = nf_etiqueta_lote.casa_por_texto(fatia.texto, cand_nome)
        if numero is not None and nome is None:
            nome = cand_map.get(numero, (None, None, set()))[0]
        if numero is None:
            nao_casadas.append(
                {
                    "fatia": i,
                    "numeroloja": fatia.numeroloja,
                    "paginas": [fatia.pagina_ini, fatia.pagina_fim],
                }
            )
            continue
        try:
            transformado = transformar_etiqueta(
                fatia.pdf, (nome or "").strip() or None
            )
        except EtiquetaTransformError as exc:
            falhas.append({"numero": numero, "erro": str(exc)})
            continue
        row = (
            await session.execute(
                select(NfEtiquetaArquivo).where(
                    NfEtiquetaArquivo.pedido_bling == numero
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = NfEtiquetaArquivo(pedido_bling=numero)
            session.add(row)
        row.filename = f"etiqueta_{numero}.pdf"
        row.content_type = "application/pdf"
        row.size_bytes = len(transformado)
        row.blob = transformado
        # Flush pra próxima fatia do mesmo pedido achar a linha no SELECT.
        await session.flush()
        gravadas.append(numero)

    unicos = list(dict.fromkeys(gravadas))
    comandos_resolvidos = 0
    if unicos:
        await _marcar_etiqueta(session, unicos, status_txt="ok", erro=None)
        comandos_resolvidos = await _resolver_comandos_etiqueta(session, unicos)
        await _marcar_enviado_etiqueta(session, unicos)
    await session.commit()
    logger.info(
        "nf_agent_etiqueta_lote",
        total_fatias=len(fatias),
        casadas=len(unicos),
        nao_casadas=len(nao_casadas),
        falhas=len(falhas),
        comandos_resolvidos=comandos_resolvidos,
    )
    return {
        "ok": True,
        "total_fatias": len(fatias),
        "casadas": unicos,
        "nao_casadas": nao_casadas,
        "falhas": falhas,
        "comandos_resolvidos": comandos_resolvidos,
    }


@router.post(
    "/agent/nf",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_nf(
    session: Annotated[AsyncSession, Depends(get_session)],
    pedido_bling: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Recebe o PDF do DANFE do Bling (fluxo correios/ML) por pedido e grava em
    `nf_etiqueta_arquivo.nf_pdf`. A NF vem do Bling ("Gerar PDF DANFE") — a
    marionete captura e sobe aqui. Guardado CRU (sem transformar — a NF vai
    como está). Quando a etiqueta já existe, o botão "Imprimir Etiqueta" passa a
    servir etiqueta + NF juntadas (correios não aceita declaração). A ordem de
    chegada (etiqueta antes ou depois da NF) não importa — a junção é feita na
    hora de servir.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "nf_pdf_vazia"})
    if len(raw) > _ETIQUETA_MAX_BYTES:
        raise HTTPException(413, detail={"code": "nf_pdf_grande"})
    row = (
        await session.execute(
            select(NfEtiquetaArquivo).where(
                NfEtiquetaArquivo.pedido_bling == pedido_bling
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # A NF pode chegar antes da etiqueta; cria a linha só com a NF (a
        # etiqueta preenche blob/filename depois via /agent/etiqueta).
        row = NfEtiquetaArquivo(
            pedido_bling=pedido_bling,
            filename=f"etiqueta_{pedido_bling}.pdf",
            content_type="application/pdf",
            size_bytes=0,
            blob=b"",
        )
        session.add(row)
    row.nf_pdf = raw
    row.nf_size_bytes = len(raw)
    await session.commit()
    logger.info("nf_agent_nf", pedido_bling=pedido_bling, size=len(raw))
    return {"ok": True, "pedido_bling": pedido_bling, "nf_size_bytes": len(raw)}
