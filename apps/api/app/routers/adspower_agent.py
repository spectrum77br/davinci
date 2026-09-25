"""Ponte DaVinci → AdsPower para o IP de cada empresa.

Eduardo (25/09/2026): "quando colocarmos um ip novo para a empresa, já vai
diretamente para o ads power". A API do AdsPower só responde na máquina onde
ele roda (o Mac), então o servidor não fala com ele: um serviço no Mac busca
aqui o que está pendente, aplica nos perfis e devolve o resultado.

  GET  /api/agent/adspower/ip-pendentes   empresas cujo IP ainda não foi
                                          confirmado no AdsPower, com os perfis
  POST /api/agent/adspower/ip-resultado   o que o serviço conseguiu fazer

Protegido pelo X-Agent-Token (`adspower_agent_token`); vazio = fechado.

Proteção que mora AQUI e não no Mac: perfil que atende lojas de MAIS DE UMA
empresa não é entregue para ser alterado. Trocar o proxy dele mudaria o IP das
duas empresas de uma vez — é justamente o compartilhamento que a coluna de IP
existe para acabar.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Company, StoreInfo

logger = structlog.get_logger()

router = APIRouter(prefix="/api/agent/adspower", tags=["adspower-agent"])

_ESPERA_DEPOIS_DE_ERRO = timedelta(hours=1)
# Espelho do AdsPower: tabela sem modelo ORM, alimentada pelo adspower_sync.
_ESPELHO = f"{get_settings().database_schema}.adspower"


async def _require_adspower_agent_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    esperado = get_settings().adspower_agent_token
    if not esperado or not x_agent_token or not secrets.compare_digest(x_agent_token, esperado):
        raise HTTPException(401, detail={"code": "adspower_agent_unauthorized"})


def _ordem(p: "PerfilAdspower") -> tuple[int, str]:
    # Pelo número do perfil, não pelo texto ("84" antes de "119").
    return (int(p.profile_no), "") if p.profile_no.isdigit() else (10**9, p.profile_no)


def _norm(s: str | None) -> str:
    # Mesmo critério da tabela de Empresas (companies._norm_conta): a loja é
    # da empresa cujo apelido bate com o nome da conta, sem espaço e minúsculo.
    return "".join((s or "").split()).lower()


class PerfilAdspower(BaseModel):
    user_id: str
    profile_no: str
    nome: str | None = None


class PendenciaIp(BaseModel):
    company_id: UUID
    apelido: str
    ip: str
    perfis: list[PerfilAdspower]
    # Perfis da empresa que também atendem outra empresa: NÃO devem ser
    # alterados. Vêm à parte só para o serviço reportar por que não aplicou.
    compartilhados: list[PerfilAdspower] = []
    # Lojas da empresa cujo servidor não existe no espelho do AdsPower.
    sem_perfil: list[str] = []


class ResultadoIp(BaseModel):
    company_id: UUID
    ip: str
    ok: bool
    # Texto curto e SEM credencial — o serviço do Mac nunca manda usuário nem
    # senha de proxy aqui. O limite impede que um log inteiro caia no banco.
    erro: str | None = Field(default=None, max_length=500)


async def _perfis_por_empresa(
    session: AsyncSession,
) -> tuple[dict[str, dict[str, PerfilAdspower]], dict[str, set[str]], dict[str, list[str]]]:
    """(perfis por apelido normalizado, empresas por perfil, lojas sem perfil)."""
    lojas = (
        await session.execute(
            select(StoreInfo.platform, StoreInfo.account_name, StoreInfo.server).where(
                StoreInfo.server.is_not(None)
            )
        )
    ).all()
    # O espelho do AdsPower é uma tabela sem modelo ORM (vem do adspower_sync).
    espelho = {
        str(no).strip(): (uid, nome)
        for uid, no, nome in (
            # _ESPELHO vem da configuração, não de entrada de usuário.
            await session.execute(text(f"SELECT id, profile_no, name FROM {_ESPELHO}"))  # noqa: S608
        ).all()
        if no is not None
    }
    perfis: dict[str, dict[str, PerfilAdspower]] = {}
    empresas_do_perfil: dict[str, set[str]] = {}
    sem_perfil: dict[str, list[str]] = {}
    for plataforma, conta, servidor in lojas:
        dono = _norm(conta)
        no = (servidor or "").strip()
        if not dono or not no:
            continue
        achado = espelho.get(no)
        if achado is None:
            sem_perfil.setdefault(dono, []).append(f"{plataforma}/{conta} no servidor {no}")
            continue
        uid, nome = achado
        perfis.setdefault(dono, {})[uid] = PerfilAdspower(user_id=uid, profile_no=no, nome=nome)
        empresas_do_perfil.setdefault(uid, set()).add(dono)
    return perfis, empresas_do_perfil, sem_perfil


@router.get(
    "/ip-pendentes",
    response_model=list[PendenciaIp],
    dependencies=[Depends(_require_adspower_agent_token)],
)
async def ip_pendentes(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PendenciaIp]:
    empresas = (
        (
            await session.execute(
                select(Company).where(
                    Company.ip.is_not(None),
                    Company.ip.is_distinct_from(Company.ip_adspower),
                    # Quem falhou volta de hora em hora, não a cada minuto: o
                    # motivo costuma ser algo que só uma pessoa resolve. Trocar
                    # o IP na tela zera o erro e ela volta na hora.
                    or_(
                        Company.ip_adspower_erro.is_(None),
                        Company.ip_adspower_em.is_(None),
                        Company.ip_adspower_em < datetime.now(UTC) - _ESPERA_DEPOIS_DE_ERRO,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not empresas:
        return []
    perfis, empresas_do_perfil, sem_perfil = await _perfis_por_empresa(session)
    saida: list[PendenciaIp] = []
    for c in empresas:
        dono = _norm(c.apelido)
        livres, compartilhados = [], []
        for p in perfis.get(dono, {}).values():
            atende_outras = len(empresas_do_perfil.get(p.user_id, set())) > 1
            (compartilhados if atende_outras else livres).append(p)
        saida.append(
            PendenciaIp(
                company_id=c.id,
                apelido=c.apelido,
                ip=c.ip,
                perfis=sorted(livres, key=_ordem),
                compartilhados=sorted(compartilhados, key=_ordem),
                sem_perfil=sem_perfil.get(dono, []),
            )
        )
    return saida


@router.post(
    "/ip-resultado",
    dependencies=[Depends(_require_adspower_agent_token)],
)
async def ip_resultado(
    body: ResultadoIp,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    c = await session.get(Company, body.company_id)
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    # Alguém trocou o IP enquanto o serviço aplicava o anterior: esse
    # resultado é de um IP que não vale mais e não pode marcar o novo como
    # aplicado. O novo volta como pendente na próxima passada.
    if (c.ip or "") != body.ip:
        logger.info("adspower_ip_resultado_obsoleto", company=c.apelido)
        return {"registrado": False, "motivo": "ip_mudou"}
    agora = datetime.now(UTC)
    if body.ok:
        c.ip_adspower = body.ip
        c.ip_adspower_erro = None
    else:
        c.ip_adspower_erro = (body.erro or "falhou sem motivo").strip()[:500]
    c.ip_adspower_em = agora
    await session.commit()
    logger.info("adspower_ip_resultado", company=c.apelido, ok=body.ok)
    return {"registrado": True}
