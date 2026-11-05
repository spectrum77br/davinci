"""Controle de Estoque › "Transferir estoque" (Eduardo, 05/11/2026) — só admin.

Troca a loja interna "Centro de Distribuição" do Bling. Regras e o passo a
passo de cada lançamento em `services/estoque_transferencia.py`.

  GET  /api/estoque/transferencias/produtos?q=   busca de SKU (simples, ativo)
  POST /api/estoque/transferencias/conferir      confere linhas e saldo ao vivo, sem lançar
  POST /api/estoque/transferencias               grava e lança (Idempotency-Key)
  GET  /api/estoque/transferencias               últimas transferências
  POST /api/estoque/transferencias/linhas/{id}/resolver   já lançou | tentar de novo
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import EstoqueTransferencia, EstoqueTransferenciaLinha, User
from app.services import estoque_transferencia as svc

logger = structlog.get_logger()

router = APIRouter(prefix="/api/estoque/transferencias", tags=["estoque"])

Admin = Annotated[User, Depends(require_admin)]
Sessao = Annotated[AsyncSession, Depends(get_session)]


class LinhaIn(BaseModel):
    origem: str = Field(..., max_length=80)
    destino: str = Field(..., max_length=80)
    quantidade: int


class ConferirIn(BaseModel):
    linhas: list[LinhaIn] = Field(..., min_length=1, max_length=svc.MAX_LINHAS)


class TransferirIn(ConferirIn):
    observacao: str | None = Field(None, max_length=500)


class ResolverIn(BaseModel):
    acao: str = Field(..., pattern="^(ja_lancou|tentar_de_novo)$")
    conferi_extrato: bool = False
    # A tentativa da perna que a pessoa viu na tela (`LinhaOut.tentativa`):
    # se mudou, a ação é recusada (aba antiga não lança de novo).
    tentativa: datetime | None = None


class LinhaOut(BaseModel):
    id: UUID
    ordem: int
    origem: str
    destino: str
    quantidade: int
    custo: float | None
    estado: str
    texto: str
    saida_status: str
    entrada_status: str
    pode_resolver: bool
    precisa_conferir: bool
    # "Está no extrato" / "Já lancei no Bling": perna sem confirmação, ou
    # entrada recusada (a pessoa pode ter lançado à mão no Bling).
    pode_ja_lancou: bool
    tentativa: datetime | None


class TransferenciaOut(BaseModel):
    id: UUID
    numero: int
    criado_em: datetime
    criado_por: str | None
    observacao: str | None
    estado: str
    linhas: list[LinhaOut]


async def _client(session: AsyncSession):
    from app.services.devolution_stock_return import _get_bling_client

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(
            503, detail={"code": "bling_not_connected", "message": "Bling não conectado."}
        )
    return client


def _linha_out(linha: EstoqueTransferenciaLinha) -> LinhaOut:
    estado, texto = svc.situacao_da_linha(linha)
    parada = linha.saida_status in (svc.FALHOU, svc.SEM_CONFIRMACAO) or (
        linha.saida_status == svc.OK and linha.entrada_status in (svc.FALHOU, svc.SEM_CONFIRMACAO)
    )
    precisa = svc.SEM_CONFIRMACAO in (linha.saida_status, linha.entrada_status)
    perna = svc.perna_parada(linha)
    st = getattr(linha, f"{perna}_status")
    ja = st == svc.SEM_CONFIRMACAO or (perna == "entrada" and st == svc.FALHOU)
    return LinhaOut(
        id=linha.id,
        ordem=linha.ordem,
        origem=linha.origem_sku,
        destino=linha.destino_sku,
        quantidade=linha.quantidade,
        custo=float(linha.custo) if linha.custo is not None else None,
        estado=estado,
        texto=texto,
        saida_status=linha.saida_status,
        entrada_status=linha.entrada_status,
        pode_resolver=parada,
        precisa_conferir=precisa,
        pode_ja_lancou=parada and ja,
        tentativa=getattr(linha, f"{perna}_tentativa_at"),
    )


def _estado_geral(linhas: list[LinhaOut]) -> str:
    estados = {x.estado for x in linhas}
    for e in ("atencao", "andamento", "falhou"):
        if e in estados:
            return e
    return "ok"


async def _out(session: AsyncSession, t: EstoqueTransferencia) -> TransferenciaOut:
    linhas = (
        (
            await session.execute(
                select(EstoqueTransferenciaLinha)
                .where(EstoqueTransferenciaLinha.transferencia_id == t.id)
                .order_by(EstoqueTransferenciaLinha.ordem)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    outs = [_linha_out(x) for x in linhas]
    return TransferenciaOut(
        id=t.id,
        numero=t.numero,
        criado_em=t.created_at,
        criado_por=t.criado_por_nome,
        observacao=t.observacao,
        estado=_estado_geral(outs),
        linhas=outs,
    )


@router.get("/produtos")
async def buscar_produtos(
    session: Sessao, _u: Admin, q: str = Query(..., min_length=1)
) -> list[dict]:
    return await svc.buscar_produtos(session, q)


@router.post("/conferir")
async def conferir(body: ConferirIn, session: Sessao, _u: Admin) -> dict:
    client = await _client(session)
    try:
        linhas = await svc.conferir(session, client, [x.model_dump() for x in body.linhas])
    except Exception as exc:  # noqa: BLE001 — leitura do saldo no Bling
        logger.warning("estoque_transferencia_conferir_falhou", erro=str(exc)[:200])
        raise HTTPException(
            502,
            detail={
                "code": "bling_saldo",
                "message": "Não deu para ler o saldo no Bling agora. Tente de novo.",
            },
        ) from exc
    return {"linhas": [{k: v for k, v in x.items() if not k.startswith("_")} for x in linhas]}


@router.post("", response_model=TransferenciaOut)
async def transferir(
    body: TransferirIn,
    session: Sessao,
    user: Admin,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TransferenciaOut:
    chave = (idempotency_key or "").strip()
    if not (8 <= len(chave) <= 100):
        raise HTTPException(
            422, detail={"code": "idempotency_key", "message": "Chave da tela ausente."}
        )
    client = await _client(session)
    try:
        t, nova = await svc.criar(
            session,
            client,
            idem_key=chave,
            user_id=user.id,
            user_nome=user.name or user.email,
            observacao=body.observacao,
            linhas=[x.model_dump() for x in body.linhas],
        )
    except svc.TransferenciaInvalidaError as exc:
        raise HTTPException(
            422, detail={"code": "transferencia_invalida", "linhas": exc.linhas}
        ) from exc
    except Exception as exc:  # noqa: BLE001 — leitura do saldo no Bling
        await session.rollback()
        logger.warning("estoque_transferencia_criar_falhou", erro=str(exc)[:200])
        raise HTTPException(
            502,
            detail={
                "code": "bling_saldo",
                "message": "Não deu para ler o saldo no Bling agora. Nada foi lançado.",
            },
        ) from exc
    if nova:
        logger.info(
            "estoque_transferencia_criada",
            numero=t.numero,
            user=str(user.id),
            linhas=len(body.linhas),
        )
        await svc.executar(client, t.id)
    return await _out(session, t)


@router.get("", response_model=list[TransferenciaOut])
async def listar(
    session: Sessao, _u: Admin, limite: int = Query(30, ge=1, le=200)
) -> list[TransferenciaOut]:
    tl = EstoqueTransferenciaLinha
    # As últimas `limite` + qualquer uma mais antiga com linha ainda parada ou
    # em andamento (senão a pendência some da lista e fica sem onde resolver).
    pendentes = select(tl.transferencia_id).where(
        or_(
            tl.saida_status.in_((svc.PENDENTE, svc.ENVIANDO, svc.CONFERINDO, svc.SEM_CONFIRMACAO)),
            and_(tl.saida_status == svc.OK, tl.entrada_status != svc.OK),
            # Recusada sem nada sair (ex.: o processo caiu antes de lançar e a
            # pessoa nem viu): fica à vista por 7 dias para o "Tentar de novo".
            and_(
                tl.saida_status == svc.FALHOU,
                tl.updated_at >= func.now() - timedelta(days=7),
            ),
        )
    )
    recentes = (
        select(EstoqueTransferencia.id)
        .order_by(EstoqueTransferencia.created_at.desc())
        .limit(limite)
    )
    ts = (
        (
            await session.execute(
                select(EstoqueTransferencia)
                .where(
                    or_(
                        EstoqueTransferencia.id.in_(recentes),
                        EstoqueTransferencia.id.in_(pendentes),
                    )
                )
                .order_by(EstoqueTransferencia.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _out(session, t) for t in ts]


@router.post("/linhas/{linha_id}/resolver", response_model=TransferenciaOut)
async def resolver(
    linha_id: UUID, body: ResolverIn, session: Sessao, user: Admin
) -> TransferenciaOut:
    client = await _client(session)
    try:
        await svc.resolver(
            session,
            client,
            linha_id,
            acao=body.acao,
            conferi_extrato=body.conferi_extrato,
            quem=(user.name or user.email or "admin"),
            tentativa=body.tentativa,
        )
    except svc.AcaoInvalidaError as exc:
        await session.rollback()
        status = 404 if exc.code == "linha_nao_encontrada" else 409
        raise HTTPException(status, detail={"code": exc.code, "message": exc.message}) from exc
    linha = await session.get(EstoqueTransferenciaLinha, linha_id)
    t = await session.get(EstoqueTransferencia, linha.transferencia_id)
    return await _out(session, t)
