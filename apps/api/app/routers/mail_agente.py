"""Aba E-mail › Agente (12/11/2026): o agente do atendimento no e-mail. Só admin vê e mexe.

O Eduardo: "a chave usa mesma do atendimento e como se fosse a extensao do
atendimento pro email so", "o do email pode deixar ali na parte de email mesmo a
config do agente deixe bem intuitivo" e "so admin mexe no agente ok e ve".

- GET  (raiz)               → onde o agente responde e-mail (por enquanto só o SAC dos
                               sites, com o liga/desliga) e as regras;
- PUT  /sites/{site}        → liga (sugere = copiloto; responde sozinho = automático)
                               ou desliga o agente no e-mail do SAC do site;
- POST/PATCH/DELETE /regras → as regras do e-mail: onde valem (os sites) e se a
                               resposta que as segue sai sozinha (a do formulário de
                               SAC só desliga/edita, não apaga).

A regra mora em `services/atendimento/email_agente.py`.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.models.mail_agente import MailAgenteRegra
from app.services.atendimento import email_agente

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento/email/admin/agente",
    tags=["mail"],
    dependencies=[Depends(require_admin)],
)
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require_admin)]

Titulo = Annotated[str, Field(min_length=3, max_length=120)]
Instrucao = Annotated[str, Field(min_length=10, max_length=4000)]


Site = Literal["uranyx", "charlots"]


class RegraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: Titulo
    instrucao: Instrucao
    ativa: bool = True
    # Onde vale (vazio = os dois sites) e se a resposta que a segue sai sozinha.
    sites: list[Site] = Field(default_factory=list, max_length=2)
    responde_sozinho: bool = False


class RegraPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: Titulo | None = None
    instrucao: Instrucao | None = None
    ativa: bool | None = None
    ordem: int | None = Field(default=None, ge=0, le=999)
    sites: list[Site] | None = Field(default=None, max_length=2)
    responde_sozinho: bool | None = None


class SitePut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # None = desligado; copiloto = sugere (a equipe confere); auto = responde sozinho.
    modo: Literal["copiloto", "auto"] | None


def _regra_out(r: MailAgenteRegra) -> dict:
    return {
        "id": str(r.id),
        "chave": r.chave,
        "titulo": r.titulo,
        "instrucao": r.instrucao,
        "ativa": r.ativa,
        "sites": list(r.sites or []),
        "responde_sozinho": r.responde_sozinho,
        "id_agente": email_agente.id_da_regra(r),
        "ordem": r.ordem,
        "fixa": r.chave is not None,
        "atualizada_em": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _regras(session: AsyncSession) -> list[dict]:
    linhas = (
        await session.execute(
            select(MailAgenteRegra).order_by(MailAgenteRegra.ordem, MailAgenteRegra.created_at)
        )
    ).scalars()
    return [_regra_out(r) for r in linhas]


@router.get("")
async def ver(session: Session, user: Admin) -> dict:
    onde = await email_agente.onde_responde(session)
    return {**onde, "regras": await _regras(session)}


@router.put("/sites/{site}")
async def ligar_site(site: str, body: SitePut, session: Session, user: Admin) -> dict:
    if site not in email_agente.FORMULARIOS_SAC:
        raise HTTPException(404, detail={"code": "site_desconhecido"})
    try:
        await email_agente.ligar_site(session, site, modo=body.modo)
    except LookupError:
        raise HTTPException(
            409,
            detail={
                "code": "site_sem_linha",
                "detail": "O site ainda não tem a linha dele no atendimento (Lojas).",
            },
        ) from None
    await session.commit()
    logger.info("email_agente_site", site=site, modo=body.modo, user_id=str(user.id))
    return await ver(session, user)


@router.post("/regras", status_code=201)
async def criar_regra(body: RegraIn, session: Session, user: Admin) -> dict:
    ordem = await session.scalar(select(func.coalesce(func.max(MailAgenteRegra.ordem), 0)))
    r = MailAgenteRegra(
        titulo=body.titulo.strip(),
        instrucao=body.instrucao.strip(),
        ativa=body.ativa,
        sites=sorted(set(body.sites)),
        responde_sozinho=body.responde_sozinho,
        ordem=int(ordem or 0) + 1,
        updated_by=user.id,
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)
    logger.info("email_agente_regra_criada", regra_id=str(r.id), user_id=str(user.id))
    return _regra_out(r)


@router.patch("/regras/{regra_id}")
async def editar_regra(regra_id: UUID, body: RegraPatch, session: Session, user: Admin) -> dict:
    r = await session.get(MailAgenteRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_existe"})
    campos = body.model_dump(exclude_unset=True)
    if "titulo" in campos and body.titulo is not None:
        r.titulo = body.titulo.strip()
    if "instrucao" in campos and body.instrucao is not None:
        r.instrucao = body.instrucao.strip()
    if "ativa" in campos and body.ativa is not None:
        r.ativa = body.ativa
    if "ordem" in campos and body.ordem is not None:
        r.ordem = body.ordem
    if "sites" in campos and body.sites is not None:
        r.sites = sorted(set(body.sites))
    if "responde_sozinho" in campos and body.responde_sozinho is not None:
        r.responde_sozinho = body.responde_sozinho
    r.updated_by = user.id
    await session.commit()
    await session.refresh(r)
    logger.info("email_agente_regra_editada", regra_id=str(r.id), user_id=str(user.id))
    return _regra_out(r)


@router.delete("/regras/{regra_id}", status_code=204)
async def apagar_regra(regra_id: UUID, session: Session, user: Admin) -> None:
    r = await session.get(MailAgenteRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_existe"})
    if r.chave is not None:
        raise HTTPException(
            409,
            detail={
                "code": "regra_fixa",
                "detail": "Esta regra é usada pelo sistema: desligue em vez de apagar.",
            },
        )
    await session.delete(r)
    await session.commit()
    logger.info("email_agente_regra_apagada", regra_id=str(regra_id), user_id=str(user.id))
