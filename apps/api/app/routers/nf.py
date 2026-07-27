"""Notas Fiscais automáticas — cadastros (recurso `nf_faturador`) e painel de
faturamento (recurso `nf_faturamento`).

Fase 1: cadastro do FATURADOR (emissor da NF). Cada linha é um tipo de
faturador; a lista é extensível. A automação (emissão da NF) é construída
depois — aqui é só o CRUD do cadastro.
"""

import base64
import json
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import NfCatalogoMala, NfEtiqueta, NfFaturador, NfImpressao, User
from app.schemas.nf import (
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
from app.services import nf_emissao_gerar, nf_relatorio

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
# pela emissão quando o faturador é nf_cheia; o vínculo `sku_base` (editável,
# começa NULL) é o que casa o SKU do pedido com a linha do catálogo.
# ---------------------------------------------------------------------------


def _catalogo_out(c: NfCatalogoMala) -> NfCatalogoMalaOut:
    return NfCatalogoMalaOut(
        id=c.id,
        modelo=c.modelo,
        tamanho=c.tamanho,
        valor=c.valor,
        sku_base=c.sku_base,
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
        sku_base=_clean(body.sku_base),
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
    if "sku_base" in data:
        c.sku_base = _clean(data["sku_base"])
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
