"""Notas Fiscais automáticas — cadastros (recurso `nf_faturador`) e painel de
faturamento (recurso `nf_faturamento`).

Fase 1: cadastro do FATURADOR (emissor da NF). Cada linha é um tipo de
faturador; a lista é extensível. A automação (emissão da NF) é construída
depois — aqui é só o CRUD do cadastro.
"""

import asyncio
import base64
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo

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
from sqlalchemy import and_, asc, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingKitComponent,
    BlingOrder,
    NfCatalogoMala,
    NfCommand,
    NfEtiqueta,
    NfEtiquetaArquivo,
    NfFaturador,
    NfFaturamento,
    NfImpressao,
    NfNota,
    Product,
    StoreInfo,
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
    nf_xml,
)
from app.services.bling_situacoes import SITUACAO_ENVIADO_ETIQUETA as _SITUACAO_ENVIADO_ETIQUETA
from app.services.bling_situacoes import (
    SITUACOES_ENVIADO_ETIQUETA_STR as _SITUACOES_ENVIADO_ETIQUETA_STR,
)
from app.services.estoque_fifo import PedidoPecas, conferir_fifo
from app.services.melhor_envio import (
    MelhorEnvioApiError,
    MelhorEnvioClient,
    MelhorEnvioConfigError,
)
from app.services.nf_etiqueta_transform import (
    EtiquetaTransformError,
    transformar_etiqueta,
)
from app.services.prioridade_estoque import aplicar_prioridade_estoque

# Teto do PDF cru da etiqueta que a marionete sobe pra transformação (item 2).
_ETIQUETA_MAX_BYTES = 8 * 1024 * 1024

# Teto do PDF ÚNICO da impressão em LOTE (N etiquetas + declarações num PDF só).
_ETIQUETA_LOTE_MAX_BYTES = 40 * 1024 * 1024

# Janela do casamento reserva do lote (destinatário + SKU): pedidos mais velhos
# que isso não entram como candidatos — evita casar homônimo antigo.
_ETIQUETA_LOTE_JANELA = timedelta(days=14)

# Pausa entre PATCHes de situação no Bling (rate limit — PATCHes em rajada
# derrubavam parte dos PATCHes pra Em digitação (21; antes 83965 Enviado
# Etiqueta)).
_BLING_PATCH_PAUSA_S = 0.5

# Situação de "etiqueta enviada" no Bling — importada acima de
# services/bling_situacoes: _SITUACAO_ENVIADO_ETIQUETA = 21 (Em digitação,
# nativa; canônica desde 03/09/2026) é pra onde o pedido VAI quando a etiqueta
# sobe, e é ela que o faz aparecer na aba Pedidos do Controle de Estoque pra
# ser impresso (ver routers/estoque.py). _SITUACOES_ENVIADO_ETIQUETA_STR
# também inclui a custom 83965 (Enviado Etiqueta, legado) pra não re-PATCHar
# pedido antigo que já está lá.

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


def _clean_ncm_fonte(v: str | None) -> str | None:
    """Normaliza a fonte do NCM: NULL/''/'padrao' = NCM padrão (fixo);
    'importacao' = NCM do produto de importação (padrão como reserva)."""
    v = _clean(v)
    if v == "padrao":
        v = None
    if v not in (None, "importacao"):
        raise HTTPException(422, detail={"code": "invalid_ncm_fonte"})
    return v


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
        ncm_fonte=f.ncm_fonte,
        observacao_duimp=f.observacao_duimp,
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
        ncm_fonte=_clean_ncm_fonte(body.ncm_fonte),
        observacao_duimp=body.observacao_duimp,
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
    if "ncm_fonte" in data:
        f.ncm_fonte = _clean_ncm_fonte(data["ncm_fonte"])
    if "observacao_duimp" in data and data["observacao_duimp"] is not None:
        f.observacao_duimp = data["observacao_duimp"]
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
    """Move os pedidos pra "Em digitação" (situação 21, nativa do Bling — decisão
    do Eduardo 03/09/2026; antes era a custom 83965 "Enviado Etiqueta", que
    segue válida como legado nos pedidos antigos) depois que a etiqueta foi
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
    # Quem já está em QUALQUER situação de etiqueta enviada (21 ou o legado
    # 83965) fica como está — não re-PATCHa legado pra 21.
    pendentes = {
        r.numero: r.bling_id
        for r in rows
        if str(r.situacao or "") not in _SITUACOES_ENVIADO_ETIQUETA_STR
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


# Situações que SEGURAM peça no Bling — o saldo virtual já desconta a reserva
# delas (medido em prod 30/10: reserva = unidades em 6 + 21 em 97,7% dos
# componentes; a 83955 solta a reserva na hora em que o pedido entra nela). A
# 83965 (Enviado Etiqueta, legado) vai junto da 21 por segurança.
_SITUACOES_QUE_RESERVAM = ("6", *_SITUACOES_ENVIADO_ETIQUETA_STR)
# Pedido Em aberto que já entrou na fila da NF (passou no check antes): a peça
# é dele — nunca é o "negativo" de um pedido conferido depois.
_STATUS_PECA_GARANTIDA = ("processando", "ok")
# Quem segura a peça no espelho: só pedido dos últimos 30 dias e nunca o
# excluído. O espelho tem fantasma (21 de 2025, 6 de maio que nunca saíram) —
# sem a janela ele entraria na fila FIFO e absorveria o déficit.
_JANELA_SEGURAM = timedelta(days=30)


def _peca(produto_id: int | None, sku: str | None) -> str:
    """Chave da peça no FIFO: o id do produto no Bling (o mesmo do componente
    do kit); sem id, o SKU."""
    return f"id:{int(produto_id)}" if produto_id else f"sku:{sku}"


def _segura_peca(corte: datetime) -> tuple:
    """Filtros do espelho pra quem segura peça no Bling: situação que reserva,
    dentro da janela e sem linha 'excluido' do MESMO pedido no Bling (mesmo
    `bling_id`). Pelo número não: pedido excluído e reimportado ganha outro
    `bling_id` com o mesmo número, e a versão viva continua segurando a peça."""
    excluido = aliased(BlingOrder)
    return (
        BlingOrder.situacao.in_(_SITUACOES_QUE_RESERVAM),
        BlingOrder.data >= corte,
        ~exists().where(
            excluido.bling_id == BlingOrder.bling_id, excluido.situacao == "excluido"
        ),
    )


_ANTIGO = datetime.min.replace(tzinfo=UTC)


def _linhas_vivas(rows) -> dict[str, tuple]:
    """numero → (linha que decide, linhas dos itens) — DETERMINÍSTICO.

    `bling_orders` tem uma linha por item e pode ter mais de um `bling_id` pro
    mesmo número (pedido excluído e reimportado). Quem decide a situação e a
    ordem de entrada é a linha VIVA (não 'excluido') mais recente: maior
    `bling_id`, depois `updated_at`. Só os itens desse `bling_id` contam — a
    versão excluída não soma peça nem empresta a situação dela."""
    por_numero: dict[str, list] = {}
    for r in rows:
        por_numero.setdefault(r.numero, []).append(r)
    out: dict[str, tuple] = {}
    for numero, rs in por_numero.items():
        dono = max(
            rs,
            key=lambda r: (
                str(r.situacao or "") != "excluido",
                int(r.bling_id or 0),
                r.updated_at or _ANTIGO,
            ),
        )
        vistos: set[tuple] = set()
        itens = []
        for r in sorted(rs, key=lambda r: (r.item_index or 0, r.item_codigo or "")):
            chave = (r.item_index, r.item_codigo)
            if r.bling_id != dono.bling_id or chave in vistos:
                continue
            vistos.add(chave)
            itens.append(r)
        out[numero] = (dono, itens)
    return out


async def _composicao_kits(
    session: AsyncSession, kit_ids: set[int]
) -> dict[int, list[tuple[int, float]]]:
    """kit (id Bling) → [(componente, quantidade)] do cache `bling_kit_components`."""
    if not kit_ids:
        return {}
    comp: dict[int, list[tuple[int, float]]] = {}
    for r in (
        await session.execute(
            select(
                BlingKitComponent.kit_bling_product_id,
                BlingKitComponent.component_bling_product_id,
                BlingKitComponent.quantidade,
            ).where(BlingKitComponent.kit_bling_product_id.in_(kit_ids))
        )
    ).all():
        comp.setdefault(int(r.kit_bling_product_id), []).append(
            (int(r.component_bling_product_id), float(r.quantidade or 1))
        )
    return comp


async def _kits_sem_composicao(
    session: AsyncSession, numeros: list[str], corte: datetime
) -> set[int]:
    """Kits (`formato='E'`) SEM composição no cache que estão no lote ou
    seguram peça no espelho. Não dá pra saber qual componente eles prendem —
    o saldo deles vai na mesma leitura do Bling pra ver se algum está curto."""
    kits = select(Product.bling_product_id).where(
        Product.formato == "E", Product.bling_product_id.is_not(None)
    )
    com_composicao = select(BlingKitComponent.kit_bling_product_id)
    return {
        int(k)
        for k in (
            await session.execute(
                select(BlingOrder.item_produto_id)
                .where(BlingOrder.item_produto_id.in_(kits))
                .where(BlingOrder.item_produto_id.not_in(com_composicao))
                .where(or_(BlingOrder.numero.in_(numeros), and_(*_segura_peca(corte))))
                .distinct()
            )
        ).scalars().all()
        if k
    }


def _num(valor) -> float | None:
    try:
        return None if valor is None else float(valor)
    except (TypeError, ValueError):
        return None


async def _saldos_bling(
    client, produto_ids: set[int]
) -> dict[int, tuple[float | None, float | None]]:
    """produto → (saldo virtual, saldo físico) numa leitura só do Bling:
    `GET /estoques/saldos` em lotes de 50 ids, os dois da MESMA resposta (foto
    consistente; 1 chamada a cada 50 produtos, em vez de 1 `get_product` por
    SKU + 1 a mais pro físico). Vale também pro kit (formato E): o Bling
    devolve o saldo dele como devolve no `get_product`.

    Lote que falha só fica de fora (log) — os que responderam valem. Produto
    sem leitura (lote que falhou ou id que o Bling não devolveu) NÃO entra no
    mapa: quem chama ADIA o pedido que depende dele, nunca o deixa passar sem
    conferência."""
    ids = sorted(produto_ids)
    out: dict[int, tuple[float | None, float | None]] = {}
    for i in range(0, len(ids), 50):
        lote = ids[i : i + 50]
        try:
            r = await client._request(  # noqa: SLF001
                "GET",
                "/estoques/saldos",
                params=[("idsProdutos[]", str(b)) for b in lote],
            )
            r.raise_for_status()
            linhas = (r.json() or {}).get("data") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "nf_sem_estoque_bling_falhou",
                produtos=len(lote),
                primeiro=lote[0],
                erro=str(exc)[:200],
            )
            continue
        for row in linhas:
            try:
                pid = int((row.get("produto") or {}).get("id") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if pid:
                out[pid] = (
                    _num(row.get("saldoVirtualTotal")),
                    _num(row.get("saldoFisicoTotal")),
                )
    return out


class _SemEstoque(NamedTuple):
    # numero → SKUs: pedidos DO LOTE que estouram o saldo (vão pra 83955).
    lote: dict[str, list[str]]
    # numero → SKUs: o FIFO aponta (com certeza), mas o pedido NÃO está no lote
    # (ex.: ML / Amazon que o sweep não confere) — nunca é movido daqui, só
    # avisado.
    fora_do_lote: dict[str, list[str]]
    # numero → SKUs sem leitura no Bling: pedido do lote ADIADO nesta passada
    # (nem 83955, nem fila, nem marca) — a próxima confere de novo.
    adiados: dict[str, list[str]]
    # Kits sem composição no cache segurando peça curta com pedido do lote na
    # dúvida por causa deles (aviso).
    kits_sem_composicao: list[int]
    # numero → SKUs: a conta com o físico e a só com o virtual discordam
    # (uma manda pra 83955, a outra deixa). ADIADO, como o sem leitura, até o
    # prazo (`_aplicar_prazo_duvida`); vencido, sai daqui pra `lote` ou pra fila.
    duvidas: dict[str, list[str]] = {}  # noqa: RUF012 — nunca mutado
    # Das dúvidas, quem vai pra 83955 quando o prazo vencer (as outras ficam com
    # a peça): regra do físico; com kit sem composição suspeito, a do virtual.
    vencem_83955: frozenset[str] = frozenset()
    duvida_pelo_virtual: bool = False


def _nada() -> _SemEstoque:
    return _SemEstoque({}, {}, {}, [], {}, frozenset(), False)


async def _pedidos_sem_estoque(
    session: AsyncSession, numeros: list[str]
) -> dict[str, list[str]]:
    """Pedidos do lote que estouram o saldo VIRTUAL com CERTEZA — conferido
    AO VIVO, por FIFO. Devolve numero → SKUs do pedido. Só confere (não mexe
    no relógio da dúvida): o negativo fora do lote, os adiados por falta de
    leitura e as dúvidas físico × virtual ficam de fora daqui; quem enfileira
    usa `_sem_estoque_fifo` e respeita adiados e dúvidas."""
    return (await _sem_estoque_fifo(session, numeros, prazo=False)).lote


async def _sem_estoque_fifo(
    session: AsyncSession, numeros: list[str], *, prazo: bool = True
) -> _SemEstoque:
    """Quem estoura o saldo VIRTUAL — conferido AO VIVO, por FIFO.

    `prazo=False`: só confere (nada no Redis) — a dúvida volta crua em
    `duvidas`, sem o relógio decidir (conferência de quem só olha). Ver
    `_conferir_sem_estoque` (a regra) e `_aplicar_prazo_duvida` (o prazo).
    """
    res = await _conferir_sem_estoque(session, numeros)
    if prazo and numeros:
        res = await _aplicar_prazo_duvida(numeros, res)
    return res


async def _conferir_sem_estoque(session: AsyncSession, numeros: list[str]) -> _SemEstoque:
    """Quem estoura o saldo VIRTUAL — conferido AO VIVO, por FIFO.

    A fonte é o Bling (`GET /estoques/saldos` → `saldoVirtualTotal`), nunca o
    `products.stock` local: o sync clampa negativo pra 0 no cache (pro push),
    então o local é estruturalmente cego pra saldo negativo. O virtual já
    desconta a reserva dos pedidos em aberto: 0 ainda é atendível, negativo é
    que falta peça. O check local (best-effort) só vale sem integração Bling
    ou pra item sem id de produto (o Bling não reserva o que não tem produto).

    FIFO (Eduardo 30/10): negativo NÃO derruba todo pedido com o SKU — a peça
    fica com os mais antigos (ordem de entrada no Bling) e só os MAIS NOVOS que
    estouram o déficit são o negativo (`estoque_fifo.negativos_fifo`). Quem
    segura a peça sai do espelho local (`bling_orders` nas situações que
    reservam, 30 dias, de qualquer loja). Kit com composição no cache conta
    SEMPRE pelos componentes, no lote e entre quem segura — é neles que o
    Bling reserva (b026.12.18 e b026.18.24 disputam a b026.18; o kit "+"
    dg075.pi+a004.pi disputa a dg075.pi com o simples), e o saldo do próprio
    kit não acompanha o componente. UMA leitura no Bling (`_saldos_bling`)
    traz o virtual e o físico de tudo: com peça curta, o físico deixa a
    reserva de pedido que ainda não entrou no espelho com ele (é o mais novo),
    não com os conhecidos.

    Na dúvida, adia: pedido do lote que leva produto/componente SEM leitura no
    Bling (lote da consulta que falhou, id que não voltou) não vai pra 83955
    nem pra fila nesta passada — sai em `adiados` e a próxima confere de novo.
    A exceção é o pedido que já estoura uma peça LIDA: esse vai (é certo,
    qualquer que seja o saldo da outra peça).

    Dúvida do físico (`estoque_fifo.conferir_fifo`): só vai pra 83955 quem as
    DUAS contas (com o físico e só com o virtual) mandam. Quando discordam, o
    pedido sai em `duvidas` (adiado): a reserva que o espelho não vê pode ser
    de um pedido MAIS ANTIGO que ainda não entrou (30/10: o 306580 chegou
    depois do 306581) — o espelho alcança o Bling em minutos. Kit SEM
    composição no cache com saldo negativo (ou sem leitura) prende peça que o
    espelho não enxerga como dele: ele não desliga mais o físico de tudo — os
    pedidos que dependem do físico caem na dúvida e, vencido o prazo, decide o
    virtual (e sai o aviso do kit).
    """
    if not numeros:
        return _nada()
    colunas = (
        BlingOrder.numero,
        BlingOrder.bling_id,
        BlingOrder.item_index,
        BlingOrder.item_codigo,
        BlingOrder.item_produto_id,
        BlingOrder.item_quantidade,
        BlingOrder.situacao,
        BlingOrder.updated_at,
    )
    vivos_lote = _linhas_vivas(
        (
            await session.execute(
                select(*colunas)
                .where(BlingOrder.numero.in_(numeros))
                .where(BlingOrder.item_codigo.is_not(None))
                .distinct()
            )
        ).all()
    )
    linhas_lote = [r for _dono, rs in vivos_lote.values() for r in rs]
    skus = {r.item_codigo for r in linhas_lote if r.item_codigo}
    if not skus:
        return _nada()
    pid_por_sku: dict[str, int] = {}
    for r in linhas_lote:
        if r.item_codigo and r.item_produto_id:
            pid_por_sku.setdefault(r.item_codigo, int(r.item_produto_id))
    pids_lote = {
        int(r.item_produto_id or pid_por_sku.get(r.item_codigo) or 0) for r in linhas_lote
    } - {0}

    client = await nf_emissao_gerar._bling_client_opt(session)
    corte = datetime.now(UTC) - _JANELA_SEGURAM
    sem_composicao = await _kits_sem_composicao(session, numeros, corte)
    # Kit com composição no cache → abre nos componentes (sempre, com o Bling
    # em mãos). Sem o Bling não há saldo de componente: tudo fica pelo SKU.
    expandir: dict[int, list[tuple[int, float]]] = (
        await _composicao_kits(session, pids_lote) if client is not None else {}
    )
    bling: dict[int, tuple[float | None, float | None]] = {}

    def _virtual(pid: int | None) -> float | None:
        return bling.get(pid, (None, None))[0] if pid is not None else None

    def _pecas_do_item(r) -> dict[str, float]:
        qtd = float(r.item_quantidade) if r.item_quantidade is not None else 1.0
        if qtd <= 0:
            return {}
        pid = int(r.item_produto_id or pid_por_sku.get(r.item_codigo) or 0) or None
        if pid in expandir:
            out: dict[str, float] = {}
            for c, q in expandir[pid]:
                out[_peca(c, None)] = out.get(_peca(c, None), 0.0) + qtd * q
            return out
        return {_peca(pid, r.item_codigo): qtd}

    # peça → saldo virtual. Peça fora do mapa é desconhecida e não bloqueia.
    saldos: dict[str, float] = {}
    # Peças que o Bling devia ter lido e não leu: o pedido que depende delas
    # é ADIADO (nunca passa sem conferência).
    sem_leitura: set[str] = set()
    fallback_local: set[str] = set()
    if client is None:
        logger.warning("nf_sem_estoque_sem_bling_fallback_local", skus=len(skus))
        fallback_local = set(skus)
    else:
        componentes = {c for comps in expandir.values() for c, _q in comps}
        pedidos_ao_bling = (pids_lote - set(expandir)) | componentes
        bling = await _saldos_bling(client, pedidos_ao_bling | sem_composicao)
        for pid in pedidos_ao_bling:
            saldo = _virtual(pid)
            if saldo is None:
                sem_leitura.add(_peca(pid, None))
            else:
                saldos[_peca(pid, None)] = saldo
        # Item sem id de produto: o Bling não tem o que ler (nem reserva).
        fallback_local = {s for s in skus if s not in pid_por_sku}
    if fallback_local:
        for sku, stock in (
            await session.execute(
                select(Product.sku, func.min(Product.stock))
                .where(Product.sku.in_(fallback_local), Product.stock < 0)
                .group_by(Product.sku)
            )
        ).all():
            saldos[_peca(pid_por_sku.get(sku), sku)] = float(stock)

    # numero → linhas dos itens (o lote agora; quem segura a peça, adiante).
    linhas: dict[str, list] = {n: rs for n, (_d, rs) in vivos_lote.items()}

    def _skus_com(numero: str, pecas_alvo: set[str]) -> list[str]:
        """SKUs (na ordem dos itens) do pedido que levam alguma das peças."""
        out: list[str] = []
        for r in linhas.get(numero, []):
            if pecas_alvo & set(_pecas_do_item(r)) and r.item_codigo not in out:
                out.append(r.item_codigo)
        return out

    sem_leitura_lote = {
        n: s for n in dict.fromkeys(numeros) if (s := _skus_com(n, sem_leitura))
    }

    curtas = {p for p, v in saldos.items() if v < 0}
    if not curtas:
        if sem_leitura_lote:
            logger.warning(
                "nf_sem_estoque_adiado_sem_leitura",
                pedidos=sem_leitura_lote,
                pecas=sorted(sem_leitura),
            )
        return _nada()._replace(adiados=sem_leitura_lote)
    pids_curtos = {int(p[3:]) for p in curtas if p.startswith("id:")}
    skus_curtos = {p[4:] for p in curtas if p.startswith("sku:")}
    if pids_curtos and client is not None:
        # Kits FORA do lote que levam peça curta também seguram a peça no
        # Bling — abertos nos componentes, como os do lote.
        kits_fora = {
            int(k)
            for k in (
                await session.execute(
                    select(BlingKitComponent.kit_bling_product_id)
                    .where(BlingKitComponent.component_bling_product_id.in_(pids_curtos))
                    .distinct()
                )
            ).scalars().all()
        } - set(expandir)
        expandir.update(await _composicao_kits(session, kits_fora))

    # Quem segura alguma peça curta: os pedidos nas situações que reservam (de
    # qualquer loja/plataforma, não só os do lote) + o próprio lote.
    filtros = []
    if pids_curtos or expandir:
        filtros.append(BlingOrder.item_produto_id.in_(pids_curtos | set(expandir)))
    if skus_curtos:
        filtros.append(BlingOrder.item_codigo.in_(skus_curtos))
    seguram = (
        await session.execute(
            select(*colunas)
            .where(*_segura_peca(corte))
            .where(BlingOrder.item_codigo.is_not(None))
            .where(or_(*filtros))
            .distinct()
        )
    ).all()
    lote = set(numeros)
    vivos_fora = _linhas_vivas(r for r in seguram if r.numero not in lote)
    linhas.update({n: rs for n, (_d, rs) in vivos_fora.items()})
    donos = {n: d for n, (d, _rs) in (*vivos_lote.items(), *vivos_fora.items())}
    pecas: dict[str, dict[str, float]] = {}
    for numero, rs in linhas.items():
        soma = pecas.setdefault(numero, {})
        for r in rs:
            for p, q in _pecas_do_item(r).items():
                if p in curtas:
                    soma[p] = soma.get(p, 0.0) + q
    na_fila = {
        n
        for n, st in (
            await session.execute(
                select(NfFaturamento.pedido_bling, NfFaturamento.status_faturamento)
                .where(NfFaturamento.pedido_bling.in_(set(pecas) - lote))
            )
        ).all()
        if st in _STATUS_PECA_GARANTIDA
    }
    # Físico das peças curtas, da MESMA leitura. Kit SEM composição fica só no
    # virtual: o físico do kit não enxerga a reserva dos outros SKUs que levam
    # o mesmo componente. É o físico que separa a reserva dos pedidos que o
    # espelho já conhece da de quem ainda não entrou (ingest atrasado de 30/10).
    fisicos = {
        _peca(pid, None): bling[pid][1]
        for pid in pids_curtos - sem_composicao
        if pid in bling and bling[pid][1] is not None
    }
    # Kit SEM composição no cache com saldo negativo (ou sem leitura) prende
    # componente que o espelho não enxerga como dele: a reserva dele cairia
    # nos "que ainda não entraram" e o déficit do físico encolheria. Não
    # desliga o físico de tudo: quem depende dele fica na dúvida e, vencido o
    # prazo, decide o virtual (o lado seguro contra vender sem peça).
    suspeitos = sorted(
        k for k in sem_composicao if (v := _virtual(k)) is None or v < 0
    )
    conf = conferir_fifo(
        saldos,
        [
            PedidoPecas(
                numero=n,
                ordem=int(donos[n].bling_id) if donos[n].bling_id else None,
                pecas=p,
                reserva=str(donos[n].situacao or "") in _SITUACOES_QUE_RESERVAM,
                garantido=(
                    str(donos[n].situacao or "") in _SITUACOES_ENVIADO_ETIQUETA_STR
                    or n in na_fila
                ),
            )
            for n, p in pecas.items()
            if p
        ],
        fisicos,
    )
    certos = conf.certos
    em_duvida = conf.duvidas
    regra_do_prazo = conf.pelo_virtual if suspeitos else conf.pelo_fisico

    faltando = {
        n: s
        for n in dict.fromkeys(numeros)
        if n in certos and (s := _skus_com(n, certos[n]))
    }
    duvidas = {
        n: s
        for n in dict.fromkeys(numeros)
        if n in em_duvida and n not in faltando and (s := _skus_com(n, em_duvida[n]))
    }
    fora = {
        n: s
        for n in sorted(set(certos) - lote)
        if (s := _skus_com(n, certos[n]))
    }
    adiados = {n: s for n, s in sem_leitura_lote.items() if n not in faltando}
    kits_aviso = suspeitos if duvidas else []
    poupados = sorted(
        n
        for n in lote
        if pecas.get(n) and n not in faltando and n not in adiados and n not in duvidas
    )
    if poupados:
        # Tinham peça com saldo negativo, mas são os mais antigos: a peça é deles.
        logger.info("nf_sem_estoque_fifo_poupados", pedidos=poupados)
    if adiados:
        logger.warning(
            "nf_sem_estoque_adiado_sem_leitura", pedidos=adiados, pecas=sorted(sem_leitura)
        )
    if duvidas:
        logger.warning(
            "nf_sem_estoque_duvida_fisico_virtual",
            pedidos=duvidas,
            kits_sem_composicao=suspeitos,
            fisicos={p: fisicos[p] for p in sorted(fisicos)},
        )
    if fora:
        # O negativo é um pedido que ninguém conferiu agora (ex.: ML/Amazon fora
        # do sweep): não move sozinho — quem chama decide se avisa.
        logger.warning("nf_sem_estoque_fifo_negativo_fora_do_lote", pedidos=fora)
    return _SemEstoque(
        faltando,
        fora,
        adiados,
        kits_aviso,
        duvidas,
        frozenset(n for n in duvidas if n in regra_do_prazo),
        bool(suspeitos),
    )


async def _aplicar_prazo_duvida(numeros: list[str], res: _SemEstoque) -> _SemEstoque:
    """Prazo da dúvida físico × virtual: o relógio do 1º adiamento fica no
    Redis (chave por pedido, validade de um dia). Dentro do prazo o pedido
    segue ADIADO; vencido, decide a regra do prazo — a do físico (a peça fica
    com quem o espelho conhece) ou, com kit sem composição suspeito, a do
    virtual — com warning. Pedido do lote que saiu da dúvida zera o relógio.
    Redis fora = sem relógio: a dúvida segue adiada (nunca 83955 no escuro) e
    fica o warning."""
    from app.services import nf_auto_enfileirar as auto

    zerar = [n for n in dict.fromkeys(numeros) if n not in res.duvidas and n not in res.adiados]
    relogio = await auto.relogio_adiamento("duvida", list(res.duvidas), zerar=zerar)
    if not res.duvidas:
        return res
    if relogio is None:
        logger.warning("nf_sem_estoque_duvida_sem_relogio", pedidos=res.duvidas)
        return res
    # Quem também está sem leitura segue adiado: o teto do sem leitura decide.
    vencidas = [
        n
        for n in res.duvidas
        if n not in res.adiados and relogio.get(n, 0.0) >= auto.PRAZO_DUVIDA_S
    ]
    if not vencidas:
        return res
    vao = {n: res.duvidas[n] for n in vencidas if n in res.vencem_83955}
    logger.warning(
        "nf_sem_estoque_duvida_vencida",
        regra="virtual (kit sem composição)" if res.duvida_pelo_virtual else "fisico",
        vao_83955=vao,
        ficam=sorted(set(vencidas) - set(vao)),
        minutos={n: round(relogio[n] / 60, 1) for n in vencidas},
    )
    lote = {
        n: s
        for n in dict.fromkeys(numeros)
        if (s := res.lote.get(n) or vao.get(n))
    }
    return res._replace(
        lote=lote,
        duvidas={n: s for n, s in res.duvidas.items() if n not in vencidas},
    )


async def _marcar_aguardando_cancelamento(
    session: AsyncSession, numeros: list[str]
) -> set[str]:
    """Move os pedidos sem estoque pra "Aguardando Cancelamento" no Bling.

    BEST-EFFORT igual ao `_marcar_enviado_etiqueta`: falha no Bling só loga (o
    pedido fica de fora da fila do mesmo jeito, e a situação pode ser corrigida
    à mão). Espelha na `bling_orders` pra a tela refletir sem esperar o sync.

    Devolve quem ESTÁ em Aguardando Cancelamento agora (o Bling aceitou, ou já
    estava): só esses levam a marca no painel — quem falhou fica sem marca
    pra próxima passada tentar de novo (mesmo desenho do `_travar_ml`).
    """
    if not numeros:
        return set()
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
    movidos = {r.numero for r in rows} - set(pendentes)
    if not pendentes:
        return movidos
    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("nf_aguardando_cancelamento_sem_bling", numeros=list(pendentes))
        return movidos
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
        movidos.add(numero)
        logger.info("nf_aguardando_cancelamento", numero=numero)
    return movidos


async def _enfileirar_emissao_upseller(
    session: AsyncSession,
    faturador_id: UUID | None,
    numeros: list[str],
    *,
    urgente: bool = False,
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
                urgente=urgente,
            )
        )
    return True


async def _enfileirar_emissao_bling(
    session: AsyncSession,
    faturador_id: UUID | None,
    numeros: list[str],
    *,
    urgente: bool = False,
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
                urgente=urgente,
            )
        )
    return True


async def _enfileirar_captura_nf(
    session: AsyncSession,
    numeros: list[str],
    *,
    faturador_id: UUID | None = None,
    ads_power: str | None = None,
    urgente: bool = False,
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
                urgente=urgente,
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
    urgente: bool = False,
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
                urgente=urgente,
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
    session: AsyncSession, numeros: list[str], *, urgente: bool = False
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
                urgente=urgente,
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

    Pedido sem estoque (estourou o saldo virtual — FIFO: os mais novos perdem a
    peça, o antigo segue) NÃO entra na fila: vai pra Aguardando Cancelamento no
    Bling e sai como pulado — não faz sentido emitir NF/etiqueta de peça que
    não existe. Pedido cujo saldo o Bling não respondeu também sai como pulado
    (adiado, sem marca): nunca entra na fila sem conferir o estoque. Idem o
    pedido na dúvida físico × virtual (o espelho ainda não alcançou o Bling):
    adiado até o prazo da dúvida, depois decide a regra do prazo."""
    # PRIORIDADE de estoque (Tabela de Preços → coluna Prioridade): troca o
    # SKU pra tag prioritária ANTES do check de estoque — o check abaixo e a
    # NF já enxergam o SKU novo. Falha aqui nunca trava o enfileiramento.
    adiados: set[str] = set()
    adiados_envio: set[str] = set()
    if body.numeros:
        try:
            prio = await aplicar_prioridade_estoque(session, body.numeros)
            adiados = set(prio.get("adiados") or [])
            adiados_envio = set(prio.get("adiados_envio") or [])
        except Exception:  # noqa: BLE001
            logger.exception("enfileirar_prioridade_falhou")
    # O Bling não respondeu sobre o estoque destes: ficam de fora desta vez
    # (sem passar pelo check que mandaria para Aguardando Cancelamento). Os
    # ML/Shopee recém-chegados esperam a plataforma dizer se o envio é Flex
    # (pedido Flex sai do .sp — a NF não pode sair antes com o lote errado).
    pulados_adiados = [
        {
            "numero": n,
            "motivo": (
                "aguardando a plataforma informar o tipo de envio (Flex ou não)"
                " — tente de novo em alguns minutos"
                if n in adiados_envio
                else "o Bling não respondeu à consulta de estoque — tente de novo em alguns minutos"
            ),
        }
        for n in body.numeros
        if n in adiados
    ]
    candidatos = [n for n in body.numeros if n not in adiados]
    conferencia = await _sem_estoque_fifo(session, candidatos) if candidatos else _nada()
    sem_estoque = conferencia.lote
    # Pedido que leva produto sem leitura no Bling (consulta falhou) ou na
    # dúvida físico × virtual: fica de fora desta vez, sem marca — nunca passa
    # sem conferir o estoque, nunca vai pra 83955 sem certeza.
    adiados_estoque = conferencia.adiados
    duvidas = {n: s for n, s in conferencia.duvidas.items() if n not in adiados_estoque}
    if conferencia.kits_sem_composicao:
        from app.services.nf_auto_enfileirar import avisar_kit_sem_composicao

        try:
            async with session.begin_nested():
                await avisar_kit_sem_composicao(session, conferencia.kits_sem_composicao)
        except Exception:  # noqa: BLE001
            logger.exception("nf_sem_estoque_kit_sem_composicao_aviso_falhou")
    movidos: set[str] = set()
    if sem_estoque:
        # Recado nas Observações ANTES da situação (o PUT leva o body inteiro).
        from app.services.nf_auto_enfileirar import escrever_observacoes, observacao_sem_estoque

        await escrever_observacoes(
            session,
            {n: observacao_sem_estoque(skus) for n, skus in sem_estoque.items()},
            evento="nf_sem_estoque_observacao",
        )
        movidos = await _marcar_aguardando_cancelamento(session, list(sem_estoque))
        # Erro por pedido com os SKUs negativos — o painel mostra no tooltip
        # do badge "Sem estoque" QUAL peça travou (sem ir caçar no log). Só
        # pra quem o Bling ACEITOU: quem falhou fica sem marca e fora da fila.
        for numero, skus in sem_estoque.items():
            if numero not in movidos:
                continue
            await _marcar_faturamento(
                session,
                [numero],
                status_txt="sem_estoque",
                erro=f"Aguardando Cancelamento — saldo negativo: {', '.join(skus)}",
            )
        await session.commit()
    pulados_estoque = [
        {
            "numero": numero,
            "motivo": (
                f"sem estoque ({', '.join(skus)}) — Aguardando Cancelamento"
                if numero in movidos
                else f"sem estoque ({', '.join(skus)}) — o Bling não aceitou Aguardando"
                " Cancelamento; tente de novo em alguns minutos"
            ),
        }
        for numero, skus in sem_estoque.items()
    ] + [
        {
            "numero": numero,
            "motivo": (
                f"o Bling não respondeu o saldo de {', '.join(skus)}"
                " — tente de novo em alguns minutos"
            ),
        }
        for numero, skus in adiados_estoque.items()
    ] + [
        {
            "numero": numero,
            "motivo": (
                f"estoque em dúvida ({', '.join(skus)}): o saldo físico e o virtual do"
                " Bling discordam — o espelho ainda não tem todos os pedidos que"
                " reservam a peça; tente de novo em alguns minutos"
            ),
        }
        for numero, skus in duvidas.items()
    ]
    numeros = [
        n
        for n in candidatos
        if n not in sem_estoque and n not in adiados_estoque and n not in duvidas
    ]
    res = (
        await nf_emissao_gerar.gerar_por_faturador(session, numeros)
        if numeros
        else nf_emissao_gerar.ResultadoPorFaturador(blocos=[], pulados=[])
    )
    pulados = pulados_adiados + pulados_estoque + [
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
                # Clique no painel = alguém esperando esse pedido agora.
                urgente=True,
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


# Divisão de trabalho entre os dois executores. O robô da NUVEM (Windows) é o
# consumidor PADRÃO: o código dele não muda, então ele continua chamando o lease
# sem dizer plataforma nenhuma e recebe só o que é dele. O Mac local pede
# explicitamente `["ml", "amazon"]`.
_PLATAFORMAS_PADRAO: tuple[str, ...] = ("shopee", "tiktok")

# Impressão de etiqueta do ML sai do MESMO perfil AdsPower (58) que a nuvem usa
# pra Shopee/TikTok. Às 12h o Mac imprime o lote do ML; se a nuvem imprimir junto,
# os dois dirigem o mesmo Firefox e corrompem a sessão. Nessa janela o consumidor
# padrão não pega etiqueta — só importação, que segue normal.
_ACOES_ETIQUETA: tuple[str, ...] = ("imprimir_etiqueta", "import_etiqueta")
_HORA_ETIQUETA_ML = 12

_TZ_BRT = ZoneInfo("America/Sao_Paulo")


def _agora_brt() -> datetime:
    """Agora em Brasília (função separada pra teste monkeypatchar)."""
    return datetime.now(_TZ_BRT)


class NfAgentLeaseIn(BaseModel):
    limit: int = Field(default=5, ge=1, le=50)
    # Filtros opcionais por action: o loop contínuo do executor exclui
    # `imprimir_etiqueta` (impressão roda em passe horário separado) e o passe
    # horário pede SÓ `imprimir_etiqueta`. Vazio/None = sem filtro (compat).
    actions: list[str] | None = None
    exclude_actions: list[str] | None = None
    # Plataformas que ESTE executor atende. Vazio/None = consumidor PADRÃO (o
    # robô da nuvem, cujo código não muda) → recebe só `_PLATAFORMAS_PADRAO`.
    plataformas: list[str] | None = None


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
    # Urgente (clique no "Enfileirar" do painel) sai antes de todo o resto;
    # dentro de cada grupo segue a ordem de chegada.
    stmt = (
        select(NfCommand)
        .where(NfCommand.status == "pending")
        .order_by(NfCommand.urgente.desc(), NfCommand.created_at.asc())
        .limit(body.limit)
        .with_for_update(skip_locked=True)
    )
    if body.actions:
        stmt = stmt.where(NfCommand.action.in_(body.actions))
    if body.exclude_actions:
        # O loop contínuo do robô da nuvem pede tudo menos `imprimir_etiqueta`
        # (etiqueta fica pro passe horário). Comando urgente fura esse filtro:
        # a etiqueta de quem foi enfileirado na mão não espera a hora cheia.
        # Se o loop falhar nela, o recuperador devolve e o lote horário cobre.
        stmt = stmt.where(
            or_(
                NfCommand.urgente.is_(True),
                NfCommand.action.not_in(body.exclude_actions),
            )
        )

    # Plataforma do comando: derivada do 1º pedido (loja do Bling → store_info).
    # Não precisa de coluna nova porque os geradores agrupam por plataforma — um
    # comando nunca mistura marketplaces, então o 1º pedido vale por todos.
    plataforma_do_comando = (
        select(func.lower(func.trim(StoreInfo.platform)))
        .select_from(BlingOrder)
        .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
        .where(BlingOrder.numero == NfCommand.numeros[0].astext)
        .limit(1)
        .scalar_subquery()
    )
    pedidas = [p.strip().lower() for p in (body.plataformas or []) if p.strip()]
    if pedidas:
        stmt = stmt.where(plataforma_do_comando.in_(pedidas))
    else:
        # Consumidor padrão. Comando sem plataforma resolvível (pedido avulso,
        # loja não cadastrada) cai aqui pra não ficar órfão na fila.
        stmt = stmt.where(
            or_(
                plataforma_do_comando.in_(_PLATAFORMAS_PADRAO),
                plataforma_do_comando.is_(None),
            )
        )
        if _agora_brt().hour == _HORA_ETIQUETA_ML:
            stmt = stmt.where(NfCommand.action.not_in(_ACOES_ETIQUETA))

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
                urgente=cmd.urgente,
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
                urgente=cmd.urgente,
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
                session, numeros, ads_power=cmd.ads_power, urgente=cmd.urgente
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
            session, cmd.faturador_id, numeros, urgente=cmd.urgente
        ) or await _enfileirar_emissao_bling(
            session, cmd.faturador_id, numeros, urgente=cmd.urgente
        )
        if not emitindo:
            await _marcar_faturamento(session, numeros, status_txt="ok", erro=None)
        await _enfileirar_etiqueta_ml(session, numeros, urgente=cmd.urgente)
    else:
        await _marcar_faturamento(
            session, numeros, status_txt="erro", erro=cmd.result or "falha na importação"
        )

    await session.commit()
    logger.info("nf_agent_result", command_id=str(command_id), status=new_status)
    return {"ok": True, "status": new_status}



async def _duimp_do_pedido(session: AsyncSession, numero: str | None) -> str | None:
    """DUIMPs dos itens do pedido (mesma fonte da observação da NF 100%:
    `import_products.duimp` por SKU), sem repetir, na ordem dos itens. Nenhum
    item importado → None (etiqueta sai sem a linha)."""
    if not numero:
        return None
    # 15/09: só faturador de nota cheia (observacao_duimp) leva DUIMP na etiqueta;
    # celular a 1% (Correios) sai sem a linha, mesmo com DUIMP cadastrada no SKU.
    if not await nf_emissao_gerar.faturador_pede_duimp(session, numero):
        return None
    rows = (
        await session.execute(
            select(BlingOrder.item_codigo)
            .where(BlingOrder.numero == numero)
            .order_by(BlingOrder.item_index)
        )
    ).scalars().all()
    skus = [(s or "").strip().lower() for s in rows if (s or "").strip()]
    if not skus:
        return None
    mapa = await nf_emissao_gerar._duimp_por_sku(session, set(skus))
    textos: list[str] = []
    for sku in skus:
        d = mapa.get(sku)
        if d and d not in textos:
            textos.append(d)
    return " | ".join(textos) or None


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
        transformado = transformar_etiqueta(
            raw,
            (destinatario_nome or "").strip() or None,
            duimp=await _duimp_do_pedido(session, pedido_bling),
        )
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
    `imprimir_etiqueta` cobertos e move pra "Em digitação" (21) no Bling.

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
                fatia.pdf,
                (nome or "").strip() or None,
                duimp=await _duimp_do_pedido(session, numero),
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
    raw = await _ler_pdf_nf(file)
    row = await _linha_etiqueta(session, pedido_bling)
    row.nf_pdf = raw
    row.nf_size_bytes = len(raw)
    await session.commit()
    logger.info("nf_agent_nf", pedido_bling=pedido_bling, size=len(raw))
    return {"ok": True, "pedido_bling": pedido_bling, "nf_size_bytes": len(raw)}


@router.post(
    "/agent/nf-caixa",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_nf_caixa(
    session: Annotated[AsyncSession, Depends(get_session)],
    pedido_bling: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dict:
    """DANFE da NF de 100% (produto) que vai DENTRO DA CAIXA (28/09/2026).

    Os pedidos Correios seguem com a NF de 1% (`/agent/nf`, viaja com a
    etiqueta) e passam a levar também esta. Guardada à parte em
    `nf_caixa_pdf`: a impressão sai etiqueta → NF 1% → NF 100%, e o Controle de
    Estoque mostra na tela qual é a da caixa (o aviso NÃO vai pro papel — pedido
    do Eduardo). Mesma regra da `/agent/nf`: pode chegar antes da etiqueta."""
    raw = await _ler_pdf_nf(file)
    row = await _linha_etiqueta(session, pedido_bling)
    row.nf_caixa_pdf = raw
    row.nf_caixa_size_bytes = len(raw)
    await session.commit()
    logger.info("nf_agent_nf_caixa", pedido_bling=pedido_bling, size=len(raw))
    return {"ok": True, "pedido_bling": pedido_bling, "nf_caixa_size_bytes": len(raw)}


async def _ler_pdf_nf(file: UploadFile) -> bytes:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "nf_pdf_vazia"})
    if len(raw) > _ETIQUETA_MAX_BYTES:
        raise HTTPException(413, detail={"code": "nf_pdf_grande"})
    return raw


async def _linha_etiqueta(session: AsyncSession, pedido_bling: str) -> NfEtiquetaArquivo:
    """A linha do pedido em `nf_etiqueta_arquivo`. A NF pode chegar antes da
    etiqueta; cria a linha só com a NF (a etiqueta preenche blob/filename
    depois via /agent/etiqueta)."""
    row = (
        await session.execute(
            select(NfEtiquetaArquivo).where(
                NfEtiquetaArquivo.pedido_bling == pedido_bling
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = NfEtiquetaArquivo(
            pedido_bling=pedido_bling,
            filename=f"etiqueta_{pedido_bling}.pdf",
            content_type="application/pdf",
            size_bytes=0,
            blob=b"",
        )
        session.add(row)
    return row


# Janela do casamento nota → pedido. Nota emitida hoje casa com pedido recente;
# fora disso o risco de pegar um homônimo antigo do mesmo CPF é maior que o ganho.
_NOTA_JANELA = timedelta(days=30)
# 30/10/2026 — "faça esse ajuste para termos mais entregas de NF automaticamente
# quando é requisitado": a janela é contada da EMISSÃO da nota, dos dois lados
# (pedido de até 30 dias antes até 1 dia depois — o pedido do Bling vem só com a
# data, 00:00 de SP). Contada de hoje, a nota antiga re-enviada pelo robô casava
# com o pedido NOVO do mesmo CPF: vínculo errado em Pós Vendas/garantia e, se o
# novo já tem a sua nota cheia, a do pedido certo nunca saía no automático.
_NOTA_FOLGA_DEPOIS = timedelta(days=1)


async def _pedido_informado(
    session: AsyncSession, nota: nf_xml.NotaXml, pedido_bling: str | None
) -> str | None:
    """Pedido que o remetente do XML já conhece (lote Bling 100%): vale se
    existe no espelho e, quando os dois lados têm documento, o CPF/CNPJ do
    destinatário confere — senão ignora e cai na adivinhação normal."""
    numero = (pedido_bling or "").strip()
    if not numero:
        return None
    docs = (
        await session.execute(
            select(BlingOrder.documento_destinatario)
            .where(
                BlingOrder.numero == numero,
                BlingOrder.situacao.is_distinct_from("excluido"),
            )
            .distinct()
        )
    ).scalars().all()
    if not docs:
        return None
    doc_nota = re.sub(r"\D", "", nota.destinatario_doc or "")
    docs_pedido = {re.sub(r"\D", "", d or "") for d in docs if d}
    if doc_nota and docs_pedido and doc_nota not in docs_pedido:
        logger.warning(
            "nf_agent_nf_xml_pedido_nao_bate", pedido=numero, chave=nota.chave
        )
        return None
    return numero


async def _casar_pedido_da_nota(
    session: AsyncSession, nota: nf_xml.NotaXml
) -> str | None:
    """Acha o pedido do Bling que gerou a nota, pelo CPF/CNPJ do destinatário.

    O XML não traz o número do pedido do Bling (o `<xPed>` é o número interno do
    Upseller), então a chave é o documento do destinatário. Se o mesmo documento
    tem mais de um pedido na janela, desempata pelo nome. Continuou ambíguo →
    devolve None: a nota fica guardada sem pedido e uma passada futura tenta de
    novo, em vez de casar errado.
    """
    # Sem data de emissão não há âncora pra janela: não adivinha (a nota entra
    # sem pedido; só o `pedido_bling` informado casa).
    if not nota.destinatario_doc or nota.data_emissao is None:
        return None
    emissao = nota.data_emissao
    if emissao.tzinfo is None:
        emissao = emissao.replace(tzinfo=UTC)
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.nome_destinatario)
            .where(
                BlingOrder.documento_destinatario == nota.destinatario_doc,
                BlingOrder.numero.is_not(None),
                BlingOrder.situacao.is_distinct_from("excluido"),
                BlingOrder.data >= emissao - _NOTA_JANELA,
                BlingOrder.data <= emissao + _NOTA_FOLGA_DEPOIS,
            )
            .distinct()
        )
    ).all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0].numero

    alvo = nf_etiqueta_lote._norm(nota.destinatario_nome or "")
    if not alvo:
        return None
    por_nome = {
        r.numero
        for r in rows
        if r.nome_destinatario and nf_etiqueta_lote._norm(r.nome_destinatario) == alvo
    }
    return por_nome.pop() if len(por_nome) == 1 else None


@router.post(
    "/agent/nf-xml",
    dependencies=[Depends(_require_nf_agent_token)],
)
async def nf_agent_nf_xml(
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    pedido_bling: Annotated[str | None, Form()] = None,
) -> dict:
    """Recebe o XML autorizado de uma NF-e e grava a nota no davinci (`nf_nota`).

    O robô da nuvem já baixa esses XMLs; o coletor só os transporta pra cá. A
    identidade é a chave de acesso (44 dígitos), então re-subir o mesmo arquivo
    atualiza a linha em vez de duplicar. O pedido é descoberto pelo CPF/CNPJ do
    destinatário — quando não dá pra ter certeza, a nota entra sem pedido e o
    casamento é re-tentado depois. Quem já sabe o pedido (lote Bling 100% das
    22:30, que gerou a nota a partir dele) manda `pedido_bling` e pula a
    adivinhação — só é aceito se o pedido existe no espelho e o CPF/CNPJ bate.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "nf_xml_vazio"})
    if len(raw) > _ETIQUETA_MAX_BYTES:
        raise HTTPException(413, detail={"code": "nf_xml_grande"})
    try:
        nota = nf_xml.parse_nfe(raw)
    except nf_xml.NfXmlError as exc:
        raise HTTPException(422, detail={"code": exc.code}) from exc

    row = (
        await session.execute(select(NfNota).where(NfNota.chave == nota.chave))
    ).scalar_one_or_none()
    if row is None:
        row = NfNota(chave=nota.chave, numero=nota.numero, xml=raw)
        session.add(row)

    pedido = await _pedido_informado(session, nota, pedido_bling)
    # 30/10/2026: a adivinhação pelo CPF só preenche nota SEM pedido. Trocar um
    # vínculo existente só com o `pedido_bling` informado e validado — re-subir
    # o XML sem ele não pode mover a nota pra outra compra do mesmo comprador.
    if pedido is None and not row.pedido_bling:
        pedido = await _casar_pedido_da_nota(session, nota)
    row.numero = nota.numero
    row.serie = nota.serie
    row.emitente_cnpj = nota.emitente_cnpj
    row.emitente_nome = nota.emitente_nome
    row.destinatario_doc = nota.destinatario_doc
    row.destinatario_nome = nota.destinatario_nome
    row.valor = nota.valor
    row.data_emissao = nota.data_emissao
    row.protocolo = nota.protocolo
    row.situacao = nota.situacao
    row.situacao_motivo = nota.situacao_motivo
    row.upseller_pedido = nota.upseller_pedido
    row.xml = raw
    # Casamento só avança: uma passada que não conseguiu decidir não apaga o
    # pedido que uma anterior já achou.
    if pedido:
        row.pedido_bling = pedido
    await session.commit()
    logger.info(
        "nf_agent_nf_xml",
        chave=nota.chave,
        numero=nota.numero,
        pedido_bling=row.pedido_bling,
    )
    return {
        "ok": True,
        "chave": nota.chave,
        "numero": nota.numero,
        "pedido_bling": row.pedido_bling,
    }
