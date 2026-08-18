"""Botões INFORMAR (admin-only) — resumo sob demanda via Threema.

Dois contextos:
- `logistica`: manda a lista dos pedidos ACOMPANHADOS no painel Logística
  (mesma regra de visibilidade do painel, incluindo o passe-livre de
  "Problemas"), no formato `pedido marketplace - conta - status plataforma -
  status bling`.
- `controle_estoque`: manda os pedidos movidos pra Aguardando Cancelamento por
  falta de estoque (marca `sem_estoque` na nf_faturamento) que AINDA estão
  nessa situação no Bling.

Cada contexto tem seu cadastro de destinatários (`threema_informar_config`),
editado no modal do botão; o diretório de nomes vem do `.env` (o mesmo do
seletor da aba Status). Tudo aqui exige admin — botão, cadastro e envio.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin
from app.models import (
    BlingOrder,
    Logistica,
    NfFaturamento,
    StoreInfo,
    ThreemaInformarConfig,
    User,
)
from app.routers.nf import _SITUACAO_AGUARDANDO_CANCELAMENTO
from app.schemas.informar import InformarConfigIn, InformarConfigOut, InformarEnviarOut
from app.services import informar, threema
from app.services.logistica_ingest import _ids_pendentes

logger = structlog.get_logger()

router = APIRouter(prefix="/api/informar", tags=["informar"])

_CONTEXTOS = ("logistica", "controle_estoque")


def _valida_contexto(contexto: str) -> str:
    if contexto not in _CONTEXTOS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "contexto_desconhecido"}
        )
    return contexto


def _diretorio() -> list[dict[str, str]]:
    s = get_settings()
    return threema.parse_recipient_directory(
        s.threema_recipient_names, s.threema_recipients
    )


async def _config_row(
    session: AsyncSession, contexto: str
) -> ThreemaInformarConfig | None:
    return (
        await session.execute(
            select(ThreemaInformarConfig).where(
                ThreemaInformarConfig.contexto == contexto
            )
        )
    ).scalar_one_or_none()


def _to_config_out(contexto: str, row: ThreemaInformarConfig | None) -> InformarConfigOut:
    return InformarConfigOut(
        contexto=contexto,
        recipients=threema.parse_recipients(row.recipients if row else ""),
        destinatarios=_diretorio(),
    )


@router.get("/{contexto}", response_model=InformarConfigOut)
async def get_config(
    contexto: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_admin)],
) -> InformarConfigOut:
    """Cadastro atual + diretório de destinatários (pro modal do botão)."""
    contexto = _valida_contexto(contexto)
    return _to_config_out(contexto, await _config_row(session, contexto))


@router.put("/{contexto}", response_model=InformarConfigOut)
async def put_config(
    contexto: str,
    body: InformarConfigIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_admin)],
) -> InformarConfigOut:
    """Salva quem recebe o relatório deste contexto. Aceita só IDs que existem
    no diretório do `.env` (o modal manda os marcados)."""
    contexto = _valida_contexto(contexto)
    validos = {d["id"] for d in _diretorio()}
    escolhidos = [
        rid
        for rid in threema.parse_recipients(",".join(body.recipients))
        if rid in validos
    ]
    row = await _config_row(session, contexto)
    if row is None:
        row = ThreemaInformarConfig(contexto=contexto, recipients=",".join(escolhidos))
        session.add(row)
    else:
        row.recipients = ",".join(escolhidos)
    await session.commit()
    logger.info("informar_config_salva", contexto=contexto, recipients=escolhidos)
    return _to_config_out(contexto, row)


async def _linhas_logistica(session: AsyncSession) -> list[str]:
    """Os pedidos que o painel Logística está MOSTRANDO agora (mesma regra de
    visibilidade das abas, com o passe-livre de "Problemas")."""
    pend = await _ids_pendentes(session)
    ids = [i for v in pend.values() for i in v]
    if not ids:
        return []
    rows = (
        (await session.execute(select(Logistica).where(Logistica.id.in_(ids))))
        .scalars()
        .all()
    )
    return informar.linhas_logistica(rows)


async def _linhas_estoque(session: AsyncSession) -> list[str]:
    """Pedidos com a marca `sem_estoque` que AINDA estão em Aguardando
    Cancelamento no Bling. Rótulo da loja igual ao aviso automático do sweep
    (`plataforma conta equipe N`); SKUs saem do erro gravado na marca."""
    marcados = (
        await session.execute(
            select(NfFaturamento.pedido_bling, NfFaturamento.erro_faturamento).where(
                NfFaturamento.status_faturamento == "sem_estoque"
            )
        )
    ).all()
    if not marcados:
        return []
    numeros = [m.pedido_bling for m in marcados]
    lojas = {
        numero: " ".join(
            p
            for p in (
                plataforma,
                conta,
                f"equipe {equipe}" if equipe is not None else None,
            )
            if p
        )
        for numero, plataforma, conta, equipe in (
            await session.execute(
                select(
                    BlingOrder.numero,
                    func.max(func.lower(StoreInfo.platform)),
                    func.max(StoreInfo.account_name),
                    func.max(StoreInfo.sales_team),
                )
                .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
                .where(BlingOrder.numero.in_(numeros))
                .where(
                    BlingOrder.situacao == str(_SITUACAO_AGUARDANDO_CANCELAMENTO)
                )
                .group_by(BlingOrder.numero)
            )
        ).all()
    }
    entries: list[tuple[str, str, str]] = []
    for m in marcados:
        if m.pedido_bling not in lojas:
            continue  # já saiu de Aguardando Cancelamento — não informa
        skus = (m.erro_faturamento or "").partition("saldo negativo:")[2].strip()
        entries.append((m.pedido_bling, lojas[m.pedido_bling], skus))
    return informar.linhas_estoque(entries)


_CABECALHOS = {
    "logistica": "DaVinci — Logística: pedidos acompanhados",
    "controle_estoque": (
        "DaVinci — Controle de Estoque: Aguardando Cancelamento por falta de estoque"
    ),
}
_VAZIO = {
    "logistica": "DaVinci — Logística: nenhum pedido acompanhado no momento.",
    "controle_estoque": (
        "DaVinci — Controle de Estoque: nenhum pedido em Aguardando Cancelamento"
        " por falta de estoque no momento."
    ),
}


@router.post("/{contexto}/enviar", response_model=InformarEnviarOut)
async def enviar(
    contexto: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_admin)],
) -> InformarEnviarOut:
    """Monta o relatório do contexto e manda pros destinatários cadastrados."""
    contexto = _valida_contexto(contexto)
    row = await _config_row(session, contexto)
    recipients = threema.parse_recipients(row.recipients if row else "")
    if not recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "sem_destinatarios"},
        )
    linhas = (
        await _linhas_logistica(session)
        if contexto == "logistica"
        else await _linhas_estoque(session)
    )
    mensagens = informar.montar_mensagens(
        f"{_CABECALHOS[contexto]} ({len(linhas)})", linhas
    ) or [_VAZIO[contexto]]
    client = threema.ThreemaClient()
    sent_ok: set[str] = set()
    failed: set[str] = set()
    try:
        for msg in mensagens:
            result = await client.send_to_all(msg, recipients)
            sent_ok.update(result.get("sent", []))
            failed.update(result.get("failed", []))
    except threema.ThreemaConfigError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "threema_nao_configurado"},
        ) from exc
    logger.info(
        "informar_enviado",
        contexto=contexto,
        pedidos=len(linhas),
        mensagens=len(mensagens),
        sent=sorted(sent_ok - failed),
        failed=sorted(failed),
    )
    return InformarEnviarOut(
        pedidos=len(linhas),
        mensagens=len(mensagens),
        sent=sorted(sent_ok - failed),
        failed=sorted(failed),
    )
