"""Margens — per-order margin rows with approval status."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Integration, IntegrationPlatform, Margens, User
from app.schemas.margens import ALLOWED_STATUS, MargensOut, MargensPatch
from app.security.cipher import decrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()
router = APIRouter(prefix="/api/margens", tags=["margens"])

SITUACAO_APROVADO = 6
SITUACAO_ATENDIDO = 9
SITUACAO_REPROVADO = 83955
SITUACAO_VERIFICAR_MARGEM = 84680
SITUACAO_VERIFICAR_MARGEM_NOME = "Verificar Margem"


@router.get("", response_model=list[MargensOut])
async def list_margens(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("margem", "view"))],
    status: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> list[MargensOut]:
    stmt = select(Margens)
    if status:
        if status not in ALLOWED_STATUS:
            raise HTTPException(400, detail={"code": "invalid_status"})
        stmt = stmt.where(Margens.status == status)
    stmt = stmt.order_by(Margens.data.desc().nullslast(), Margens.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [MargensOut.model_validate(r) for r in rows]


@router.patch("/{margem_id}", response_model=MargensOut)
async def patch_margens(
    margem_id: UUID,
    body: MargensPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("margem", "edit"))],
) -> MargensOut:
    row = (
        await session.execute(select(Margens).where(Margens.id == margem_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "margem_not_found"})

    data = body.model_dump(exclude_unset=True)
    local_only = bool(data.pop("local_only", False))
    new_status = data.get("status")
    if "status" in data and new_status is not None and new_status not in ALLOWED_STATUS:
        raise HTTPException(400, detail={"code": "invalid_status"})

    if new_status in ("Aprovado", "Reprovado"):
        await _apply_bling_decision(
            session,
            user.id,
            row,
            new_status,
            update_bling=not local_only,
        )

    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    logger.info(
        "margens_patched",
        margem_id=str(row.id),
        pedido_bling=row.pedido_bling,
        status=row.status,
    )
    return MargensOut.model_validate(row)


async def _apply_bling_decision(
    session: AsyncSession,
    actor_id: UUID,
    margem: Margens,
    new_status: str,
    *,
    update_bling: bool = True,
) -> None:
    pedido_bling = margem.pedido_bling
    if pedido_bling is None:
        raise HTTPException(400, detail={"code": "pedido_bling_missing"})

    order = await _find_bling_order_for_margem(session, margem)
    if order is None or order.bling_id is None:
        raise HTTPException(404, detail={"code": "bling_order_not_found"})

    situacao_id = SITUACAO_APROVADO if new_status == "Aprovado" else SITUACAO_REPROVADO
    current_situacao_id = order.situacao
    client: BlingClient | None = None
    if update_bling and (
        new_status == "Reprovado" or str(current_situacao_id or "") != str(situacao_id)
    ):
        client = await _global_bling_client(session)
        if client is None:
            raise HTTPException(400, detail={"code": "bling_integration_missing"})

        if new_status == "Reprovado":
            current_situacao_id, _current_situacao_nome = await _require_verificar_margem(
                client,
                int(order.bling_id),
            )

    if update_bling and str(current_situacao_id or "") != str(situacao_id):
        if client is None:
            client = await _global_bling_client(session)
            if client is None:
                raise HTTPException(400, detail={"code": "bling_integration_missing"})

        if new_status == "Aprovado":
            steps: list[int] = []
            if str(current_situacao_id or "") != str(SITUACAO_ATENDIDO):
                steps.append(SITUACAO_ATENDIDO)
            steps.append(SITUACAO_APROVADO)
        else:
            steps = [SITUACAO_REPROVADO]

        for step_id in steps:
            try:
                await client.update_order_situacao(int(order.bling_id), step_id)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                body = e.response.text[:500] if e.response is not None else ""
                message = _bling_error_message(e.response) if e.response is not None else None
                logger.warning(
                    "bling_situacao_patch_failed",
                    bling_id=order.bling_id,
                    situacao_id=step_id,
                    http=code,
                    body=body,
                )
                raise HTTPException(
                    502,
                    detail={
                        "code": "bling_patch_failed",
                        "http": code,
                        "message": message or "Falha ao atualizar situacao no Bling",
                    },
                ) from e
    else:
        logger.info(
            "bling_situacao_already_target",
            bling_id=order.bling_id,
            situacao_id=situacao_id,
        )

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == order.bling_id)
        .values(
            aprovado_por=actor_id,
            status=new_status,
            verificado=True,
            **({"situacao": str(situacao_id)} if update_bling else {}),
        )
    )


async def _find_bling_order_for_margem(
    session: AsyncSession,
    margem: Margens,
) -> BlingOrder | None:
    stmt = (
        select(BlingOrder)
        .where(BlingOrder.numero == str(margem.pedido_bling))
        .where(BlingOrder.bling_id.is_not(None))
    )
    if margem.sku:
        stmt = stmt.where(BlingOrder.item_codigo == margem.sku)
    return (
        await session.execute(stmt.order_by(BlingOrder.item_index.asc()).limit(1))
    ).scalar_one_or_none()


async def _require_verificar_margem(
    client: BlingClient,
    bling_order_id: int,
) -> tuple[str, str | None]:
    try:
        raw_order = await client.get_order(bling_order_id)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else 0
        body = e.response.text[:500] if e.response is not None else ""
        message = _bling_error_message(e.response) if e.response is not None else None
        logger.warning(
            "bling_situacao_check_failed",
            bling_id=bling_order_id,
            http=code,
            body=body,
        )
        raise HTTPException(
            502,
            detail={
                "code": "bling_situacao_check_failed",
                "http": code,
                "message": message or "Nao foi possivel verificar a situacao atual no Bling.",
            },
        ) from e
    except httpx.HTTPError as e:
        logger.warning(
            "bling_situacao_check_failed",
            bling_id=bling_order_id,
            error=str(e),
        )
        raise HTTPException(
            502,
            detail={
                "code": "bling_situacao_check_failed",
                "message": "Nao foi possivel verificar a situacao atual no Bling.",
            },
        ) from e

    current_situacao_id, current_situacao_nome = _bling_order_situacao(raw_order)
    if current_situacao_id != str(SITUACAO_VERIFICAR_MARGEM):
        current_label = _format_situacao_label(current_situacao_id, current_situacao_nome)
        logger.info(
            "bling_reprovacao_blocked_by_situacao",
            bling_id=bling_order_id,
            current_situacao_id=current_situacao_id,
            current_situacao_nome=current_situacao_nome,
            required_situacao_id=SITUACAO_VERIFICAR_MARGEM,
        )
        raise HTTPException(
            409,
            detail={
                "code": "bling_situacao_not_verificar_margem",
                "current_situacao": current_situacao_id,
                "current_situacao_nome": current_situacao_nome,
                "required_situacao": str(SITUACAO_VERIFICAR_MARGEM),
                "message": (
                    f"O pedido esta em {current_label} no Bling. "
                    "Para reprovar, ele precisa estar em Verificar Margem. "
                    "Nenhuma alteracao foi enviada ao Bling."
                ),
            },
        )

    return current_situacao_id, current_situacao_nome


def _bling_order_situacao(raw_order: dict) -> tuple[str | None, str | None]:
    situacao = raw_order.get("situacao") if isinstance(raw_order, dict) else None
    if isinstance(situacao, dict):
        situacao_id = situacao.get("id")
        situacao_nome = (
            situacao.get("nome")
            or situacao.get("descricao")
            or situacao.get("valor")
            or situacao.get("name")
        )
        return (
            str(situacao_id) if situacao_id is not None else None,
            str(situacao_nome) if situacao_nome else None,
        )
    if situacao is not None:
        return str(situacao), None
    return None, None


def _format_situacao_label(situacao_id: str | None, situacao_nome: str | None) -> str:
    if situacao_nome and situacao_id:
        return f"{situacao_nome} ({situacao_id})"
    if situacao_nome:
        return situacao_nome
    if situacao_id:
        return f"situacao {situacao_id}"
    return "uma situacao desconhecida"


async def _global_bling_client(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .where(Integration.status == "active")
            .where(Integration.store_id.is_(None))
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    return BlingClient(decrypt_json(integ.credentials), integration_id=integ.id)


def _bling_error_message(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        fields = error.get("fields")
        if isinstance(fields, list) and fields:
            first = fields[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        for key in ("description", "message"):
            if error.get(key):
                return str(error[key])
    return None
