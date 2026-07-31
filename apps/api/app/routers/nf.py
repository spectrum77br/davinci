"""Notas Fiscais automáticas — cadastros (recurso `nf_faturador`) e painel de
faturamento (recurso `nf_faturamento`).

Fase 1: cadastro do FATURADOR (emissor da NF). Cada linha é um tipo de
faturador; a lista é extensível. A automação (emissão da NF) é construída
depois — aqui é só o CRUD do cadastro.
"""

import base64
import json
import secrets
from datetime import UTC, datetime
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
from sqlalchemy import asc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    NfCatalogoMala,
    NfCommand,
    NfEtiqueta,
    NfEtiquetaArquivo,
    NfFaturador,
    NfFaturamento,
    NfImpressao,
    User,
)
from app.schemas.nf import (
    ConferirFreteIn,
    ConferirFreteOut,
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
from app.services import melhor_envio, nf_emissao_gerar, nf_relatorio, nf_upseller
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


@router.post("/faturamento/enfileirar", response_model=EnfileirarOut)
async def enfileirar_importacao(
    body: GerarPlanilhaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_painel_edit)],
) -> EnfileirarOut:
    """Enfileira a importação avulsa dos pedidos escolhidos: gera a planilha por
    faturador, cria um NfCommand por faturador (CSV congelado) e marca cada
    pedido como 'processando'. 422 se nenhum pedido pôde ser gerado."""
    res = await nf_emissao_gerar.gerar_por_faturador(session, body.numeros)
    if not res.blocos:
        raise HTTPException(
            422,
            detail={
                "code": "nf_nenhum_pedido_gerado",
                "pulados": [{"numero": p.numero, "motivo": p.motivo} for p in res.pulados],
            },
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
    )
    return EnfileirarOut(
        comandos=len(res.blocos),
        pedidos_ok=total_ok,
        pulados=[{"numero": p.numero, "motivo": p.motivo} for p in res.pulados],
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


class NfAgentCommandOut(BaseModel):
    id: UUID
    faturador_id: UUID | None
    faturador_nome: str | None
    ads_power: str | None
    usuario: str | None
    senha: str | None
    action: str
    numeros: list[str]
    nome_arquivo: str
    planilha_b64: str


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
    claimed = (
        await session.execute(
            select(NfCommand)
            .where(NfCommand.status == "pending")
            .order_by(NfCommand.created_at.asc())
            .limit(body.limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

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
                    ads_power=fat.ads_power if fat else None,
                    usuario=fat.usuario if fat else None,
                    senha=(decrypt(fat.senha_enc) if fat and fat.senha_enc else None),
                    action=cmd.action,
                    numeros=list(cmd.numeros or []),
                    nome_arquivo=cmd.nome_arquivo,
                    planilha_b64=base64.b64encode(cmd.planilha).decode("ascii"),
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
    """Reporta o resultado de um comando. 'done' marca cada pedido do comando
    como status_faturamento='ok'; 'failed' marca 'erro' + guarda a mensagem."""
    cmd = await session.get(NfCommand, command_id)
    if cmd is None:
        raise HTTPException(404, detail={"code": "nf_command_not_found"})
    new_status = "done" if body.status == "done" else "failed"
    cmd.status = new_status
    cmd.result = (body.result or "")[:2000] or None
    cmd.completed_at = datetime.now(UTC)

    numeros = list(cmd.numeros or [])
    if new_status == "done":
        await _marcar_faturamento(session, numeros, status_txt="ok", erro=None)
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
