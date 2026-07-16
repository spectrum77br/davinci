"""Logística — casos de pós-venda a acompanhar (CRUD manual) + aba Status.

Aba "Logística": formato da planilha (Data | pedido bling | pedido marketplace
| plataforma | conta | STATUS PLATAFORMA | rastreio | localização | STATUS
BLING | Chamado). Cada linha guarda os dados do pedido + a assinatura de status
do Meli (`meli_status`); a partir dela, `/sugestao` devolve os Status Bling
candidatos que a curadoria da planilha já viu — é só uma dica, a classificação
final é do operador.

Aba "Status": cadastro/referência do que fazer pra cada STATUS PLATAFORMA
(alterar status bling, abrir chamado, mensagem). Por enquanto é só cadastro —
não escreve no Bling automaticamente.

Gated pelo recurso `logistica`.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Logistica, LogisticaStatus, LogisticaStatusAnexo, SituacaoBling, User
from app.schemas.logistica import (
    AnexoOut,
    CandidatoOut,
    EnviarThreemaOut,
    LogisticaCreate,
    LogisticaOut,
    LogisticaPatch,
    LogisticaStatusCreate,
    LogisticaStatusOut,
    LogisticaStatusPatch,
    OpcoesOut,
    SugestaoIn,
    SugestaoOut,
)
from app.services import logistica_meli, logistica_rules, threema

logger = structlog.get_logger()
router = APIRouter(prefix="/api/logistica", tags=["logistica"])

# Chave canônica -> rótulo gravado em `logistica.plataforma` (o filtro da aba por
# marketplace manda a chave; futuras abas Shopee/Amazon só trocam o valor).
_PLATAFORMA_LABELS = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "tiktok": "TikTok",
    "magalu": "Magalu",
}


def _to_out(c: Logistica) -> LogisticaOut:
    return LogisticaOut(
        id=c.id,
        data=c.data,
        pedido_bling=c.pedido_bling,
        pedido_marketplace=c.pedido_marketplace,
        plataforma=c.plataforma,
        conta=c.conta,
        meli_status=c.meli_status or {},
        status_plataforma=logistica_rules.assinatura_pt(c.meli_status or {}),
        rastreio=c.rastreio,
        localizacao=c.localizacao,
        divergencia=c.divergencia,
        status_bling=c.status_bling,
        chamado=c.chamado,
        observacao=c.observacao,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _to_status_out(s: LogisticaStatus) -> LogisticaStatusOut:
    return LogisticaStatusOut(
        id=s.id,
        plataforma=s.plataforma,
        status_plataforma=s.status_plataforma,
        status_atual=s.status_atual,
        alterar_status_bling=s.alterar_status_bling,
        monitoramento=s.monitoramento,
        abrir_chamado=s.abrir_chamado,
        abrir_reembolso=s.abrir_reembolso,
        mensagem_chamado=s.mensagem_chamado,
        mensagem_bling=s.mensagem_bling,
        mensagem_threema=s.mensagem_threema,
        anexos=[
            AnexoOut(
                id=a.id,
                filename=a.filename,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                created_at=a.created_at,
            )
            for a in s.anexos
        ],
        created_by=s.created_by,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _clean_meli(m: dict[str, str] | None) -> dict[str, str]:
    """Mantém só os campos conhecidos com valor não-vazio."""
    if not m:
        return {}
    return {
        f: str(m[f]).strip()
        for f in logistica_rules.FIELD_ORDER
        if m.get(f) and str(m[f]).strip()
    }


# ---- Opções + sugestão ----


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> OpcoesOut:
    nomes = (
        await session.execute(
            select(SituacaoBling.nome).distinct().order_by(SituacaoBling.nome)
        )
    ).scalars().all()
    return OpcoesOut(
        field_order=logistica_rules.FIELD_ORDER,
        field_labels=logistica_rules.FIELD_LABELS,
        field_options=logistica_rules.FIELD_OPTIONS,
        status_bling_options=[n for n in nomes if n],
    )


@router.post("/sugestao", response_model=SugestaoOut)
async def sugestao(
    body: SugestaoIn,
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> SugestaoOut:
    cand = logistica_rules.sugerir(body.meli_status)
    return SugestaoOut(candidatos=[CandidatoOut(**c) for c in cand])


# ---- Aba Status (cadastro) ----


async def _load_status(session: AsyncSession, status_id: UUID) -> LogisticaStatus | None:
    return (
        await session.execute(
            select(LogisticaStatus)
            .options(selectinload(LogisticaStatus.anexos))
            .where(LogisticaStatus.id == status_id)
        )
    ).scalar_one_or_none()


@router.get("/status", response_model=list[LogisticaStatusOut])
async def list_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> list[LogisticaStatusOut]:
    stmt = (
        select(LogisticaStatus)
        .options(selectinload(LogisticaStatus.anexos))
        .order_by(LogisticaStatus.status_plataforma)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_status_out(s) for s in rows]


@router.post("/status", response_model=LogisticaStatusOut, status_code=status.HTTP_201_CREATED)
async def create_status(
    body: LogisticaStatusCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = LogisticaStatus(
        plataforma=_clean(body.plataforma),
        status_plataforma=_clean(body.status_plataforma),
        status_atual=_clean(body.status_atual),
        alterar_status_bling=_clean(body.alterar_status_bling),
        monitoramento=bool(body.monitoramento),
        abrir_chamado=bool(body.abrir_chamado),
        abrir_reembolso=bool(body.abrir_reembolso),
        mensagem_chamado=_clean(body.mensagem_chamado),
        mensagem_bling=_clean(body.mensagem_bling),
        mensagem_threema=_clean(body.mensagem_threema),
        created_by=user.id,
    )
    session.add(s)
    await session.commit()
    s = await _load_status(session, s.id)
    return _to_status_out(s)


@router.patch("/status/{status_id}", response_model=LogisticaStatusOut)
async def patch_status(
    status_id: UUID,
    body: LogisticaStatusPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = await _load_status(session, status_id)
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "plataforma" in data:
        s.plataforma = _clean(data["plataforma"])
    if "status_plataforma" in data:
        s.status_plataforma = _clean(data["status_plataforma"])
    if "status_atual" in data:
        s.status_atual = _clean(data["status_atual"])
    if "alterar_status_bling" in data:
        s.alterar_status_bling = _clean(data["alterar_status_bling"])
    if "monitoramento" in data:
        s.monitoramento = bool(data["monitoramento"])
    if "abrir_chamado" in data:
        s.abrir_chamado = bool(data["abrir_chamado"])
    if "abrir_reembolso" in data:
        s.abrir_reembolso = bool(data["abrir_reembolso"])
    if "mensagem_chamado" in data:
        s.mensagem_chamado = _clean(data["mensagem_chamado"])
    if "mensagem_bling" in data:
        s.mensagem_bling = _clean(data["mensagem_bling"])
    if "mensagem_threema" in data:
        s.mensagem_threema = _clean(data["mensagem_threema"])

    await session.commit()
    s = await _load_status(session, s.id)
    return _to_status_out(s)


@router.post("/status/{status_id}/enviar-threema", response_model=EnviarThreemaOut)
async def enviar_threema(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> EnviarThreemaOut:
    """Envia a `mensagem_threema` desta linha da aba Status pra lista fixa de
    destinatários do Threema Gateway (config no `.env`). Notifica as pessoas do
    problema. 404 se a linha some; 422 sem mensagem/sem config."""
    s = await _load_status(session, status_id)
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})
    texto = (s.mensagem_threema or "").strip()
    if not texto:
        raise HTTPException(422, detail={"code": "logistica_sem_mensagem_threema"})
    try:
        result = await threema.ThreemaClient().send_to_all(texto)
    except threema.ThreemaConfigError as e:
        raise HTTPException(422, detail={"code": str(e)}) from e
    logger.info(
        "logistica_threema_enviado",
        status_id=str(status_id),
        sent=len(result["sent"]),
        failed=len(result["failed"]),
    )
    return EnviarThreemaOut(sent=result["sent"], failed=result["failed"])


@router.delete("/status/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})
    await session.delete(s)
    await session.commit()
    return None


# ---- Anexos (imagens) da aba Status ----

_ANEXO_TIPOS = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ANEXO_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post(
    "/status/{status_id}/anexos",
    response_model=AnexoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_status_anexo(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> AnexoOut:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})

    ctype = (file.content_type or "").lower()
    if ctype not in _ANEXO_TIPOS:
        raise HTTPException(400, detail={"code": "logistica_anexo_tipo_invalido"})
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "logistica_anexo_vazio"})
    if len(raw) > _ANEXO_MAX_BYTES:
        raise HTTPException(413, detail={"code": "logistica_anexo_muito_grande"})

    a = LogisticaStatusAnexo(
        status_id=status_id,
        filename=(file.filename or "imagem").strip() or "imagem",
        content_type=ctype,
        size_bytes=len(raw),
        blob=raw,
        created_by=user.id,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return AnexoOut(
        id=a.id,
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        created_at=a.created_at,
    )


@router.get("/anexos/{anexo_id}")
async def get_status_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> Response:
    a = (
        await session.execute(
            select(LogisticaStatusAnexo).where(LogisticaStatusAnexo.id == anexo_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "logistica_anexo_not_found"})
    return Response(
        content=a.blob,
        media_type=a.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/anexos/{anexo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    a = (
        await session.execute(
            select(LogisticaStatusAnexo).where(LogisticaStatusAnexo.id == anexo_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "logistica_anexo_not_found"})
    await session.delete(a)
    await session.commit()
    return None


# ---- Aba Logística (casos) ----


@router.get("", response_model=list[LogisticaOut])
async def list_logistica(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
    plataforma: Annotated[str | None, Query()] = None,
) -> list[LogisticaOut]:
    # Mais recentes primeiro (data desc, depois criação desc).
    stmt = select(Logistica).order_by(
        desc(Logistica.data.is_(None)), desc(Logistica.data), desc(Logistica.created_at)
    )
    if plataforma:
        label = _PLATAFORMA_LABELS.get(plataforma.strip().lower(), plataforma)
        stmt = stmt.where(Logistica.plataforma == label)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(c) for c in rows]


@router.post("", response_model=LogisticaOut, status_code=status.HTTP_201_CREATED)
async def create_logistica(
    body: LogisticaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = Logistica(
        data=body.data,
        pedido_bling=_clean(body.pedido_bling),
        pedido_marketplace=_clean(body.pedido_marketplace),
        plataforma=_clean(body.plataforma),
        conta=_clean(body.conta),
        meli_status=_clean_meli(body.meli_status),
        rastreio=_clean(body.rastreio),
        localizacao=_clean(body.localizacao),
        status_bling=_clean(body.status_bling),
        chamado=_clean(body.chamado),
        observacao=_clean(body.observacao),
        created_by=user.id,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    logger.info("logistica_created", id=str(c.id), pedido_bling=c.pedido_bling)
    return _to_out(c)


@router.post("/{logistica_id}/atualizar-meli", response_model=LogisticaOut)
async def atualizar_meli(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Puxa a assinatura de status do Meli (8 campos) da API do ML e grava em
    `meli_status`. Só vale pra pedidos de Mercado Livre com pedido de
    marketplace e conta com integração ML."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        await logistica_meli.enrich_row(session, c)
    except logistica_meli.MeliEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_meli_atualizar_falhou", id=str(logistica_id), err=str(e)[:200])
        raise HTTPException(502, detail={"code": "logistica_meli_erro"}) from e
    await session.commit()
    await session.refresh(c)
    return _to_out(c)


async def _mensagem_chamado_para(session: AsyncSession, c: Logistica) -> str | None:
    """Mensagem do chamado da regra da aba Status que casa com o Status
    Plataforma (assinatura PT) da linha. Prefere a regra específica da
    plataforma; cai na regra geral (plataforma vazia). None se nenhuma casar
    ou a que casou não tiver mensagem."""
    assinatura = logistica_rules.assinatura_pt(c.meli_status or {}).strip().lower()
    if not assinatura:
        return None
    plat = (c.plataforma or "").strip().lower()
    rows = (await session.execute(select(LogisticaStatus))).scalars().all()
    especifica: str | None = None
    geral: str | None = None
    for s in rows:
        if (s.status_plataforma or "").strip().lower() != assinatura:
            continue
        msg = (s.mensagem_chamado or "").strip()
        if not msg:
            continue
        sp = (s.plataforma or "").strip().lower()
        if sp and sp == plat:
            especifica = msg
        elif not sp:
            geral = msg
    return especifica or geral


@router.post("/{logistica_id}/enviar-chamado", response_model=LogisticaOut)
async def enviar_chamado(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Abre o chamado (mediação) direto no Mercado Livre pro pedido da linha e
    manda a mensagem da regra da aba Status (que casa com o Status Plataforma)
    pro mediador do ML. Grava o claim_id em `chamado`. Só vale pra pedidos de ML
    que já tenham uma reclamação aberta pelo comprador (a API do ML não deixa o
    vendedor abrir reclamação do zero)."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    mensagem = await _mensagem_chamado_para(session, c)
    if not mensagem:
        raise HTTPException(422, detail={"code": "logistica_sem_mensagem_chamado"})
    try:
        await logistica_meli.enviar_chamado_for_row(session, c, mensagem)
    except logistica_meli.MeliEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_enviar_chamado_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_chamado_erro", "erro": str(e)[:300]}) from e
    await session.commit()
    await session.refresh(c)
    logger.info("logistica_chamado_enviado", id=str(logistica_id), chamado=c.chamado)
    return _to_out(c)


@router.patch("/{logistica_id}", response_model=LogisticaOut)
async def patch_logistica(
    logistica_id: UUID,
    body: LogisticaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "data" in data:
        c.data = data["data"]
    if "pedido_bling" in data:
        c.pedido_bling = _clean(data["pedido_bling"])
    if "pedido_marketplace" in data:
        c.pedido_marketplace = _clean(data["pedido_marketplace"])
    if "plataforma" in data:
        c.plataforma = _clean(data["plataforma"])
    if "conta" in data:
        c.conta = _clean(data["conta"])
    if "meli_status" in data:
        c.meli_status = _clean_meli(data["meli_status"])
    if "rastreio" in data:
        c.rastreio = _clean(data["rastreio"])
    if "localizacao" in data:
        c.localizacao = _clean(data["localizacao"])
    if "status_bling" in data:
        c.status_bling = _clean(data["status_bling"])
    if "chamado" in data:
        c.chamado = _clean(data["chamado"])
    if "observacao" in data:
        c.observacao = _clean(data["observacao"])

    await session.commit()
    await session.refresh(c)
    return _to_out(c)


@router.delete("/{logistica_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_logistica(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("logistica_deleted", id=str(logistica_id))
    return None
