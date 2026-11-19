"""Sistema › Histórico: quem mudou o quê no DaVinci.

Invisível para quem não está em `historico_acesso` — inclusive admin
(Eduardo, 25/09/2026: "essa aba tem que ser invisível para todas as outras
pessoas do nosso sistema, ATÉ PARA ADMIN"). Para essas pessoas toda rota daqui
responde exatamente o 404 de uma rota que não existe, com qualquer método, e o
router fica fora da documentação pública (/api/docs).

Como as linhas cruas viram texto legível: app/historico/leitura.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, distinct, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_session
from app.deps.auth import get_current_user
from app.historico import leitura
from app.models import (
    HistoricoAcesso,
    HistoricoAlteracao,
    HistoricoEvento,
    ThreemaEnvioRegistro,
    User,
    UserRole,
    UserStatus,
)
from app.services.threema_catalogo import CATALOGO

router = APIRouter(prefix="/api/historico", include_in_schema=False)
BRT = ZoneInfo("America/Sao_Paulo")


def _nao_existe() -> HTTPException:
    # Igual ao 404 do FastAPI para rota desconhecida.
    return HTTPException(404, detail="Not Found")


async def _acesso(
    user: Annotated[User | None, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[User, HistoricoAcesso]:
    if user is None or user.status != UserStatus.ACTIVE:
        raise _nao_existe()
    acesso = await session.get(HistoricoAcesso, user.id)
    if acesso is None:
        raise _nao_existe()
    return user, acesso


Acesso = Annotated[tuple[User, HistoricoAcesso], Depends(_acesso)]
Sessao = Annotated[AsyncSession, Depends(get_session)]


def _desde(dias: int) -> datetime:
    """'hoje' = desde a meia-noite de Brasília; '7 dias' = hoje e os 6 antes."""
    hoje = datetime.now(BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    return hoje - timedelta(days=dias - 1)


# --- lista ------------------------------------------------------------------------

TIPOS = {"criou": "I", "alterou": "U", "excluiu": "D"}


@router.get("")
async def listar(
    acesso: Acesso,
    session: Sessao,
    dias: Annotated[int, Query(ge=1, le=366)] = 7,
    tela: str | None = None,
    ator: UUID | None = None,
    tipo: str | None = None,
    busca: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    _user, meu_acesso = acesso
    desde = _desde(dias)
    E, A = HistoricoEvento, HistoricoAlteracao  # noqa: N806 - apelidos curtos das consultas
    base = [E.criado_em >= desde]
    if tela:
        base.append(E.tela == tela)
    if ator:
        base.append(E.ator_id == ator)
    if tipo in TIPOS:
        base.append(
            exists().where(
                and_(A.req_id == E.req_id, A.criado_em >= desde, A.operacao == TIPOS[tipo])
            )
        )
    elif tipo == "acao":
        base.append(E.n_alteracoes == 0)
    if busca and busca.strip():
        # % e _ digitados valem como texto, não como curinga
        termo = busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        b = f"%{termo}%"
        base.append(
            or_(
                E.itens_texto.ilike(b, escape="\\"),
                E.ator_nome.ilike(b, escape="\\"),
                E.tela.ilike(b, escape="\\"),
                E.acao.ilike(b, escape="\\"),
                E.caminho.ilike(b, escape="\\"),
            )
        )

    total = (await session.execute(select(func.count()).select_from(E).where(*base))).scalar_one()
    pagina = select(E).where(*base).order_by(E.criado_em.desc(), E.id.desc())
    eventos = (await session.execute(pagina.limit(limit).offset(offset))).scalars().all()

    # As 3 primeiras alterações de cada evento, para o resumo da linha.
    reqs = [e.req_id for e in eventos]
    primeiras: dict[UUID, list[HistoricoAlteracao]] = {}
    if reqs:
        num = func.row_number().over(partition_by=A.req_id, order_by=A.id).label("n")
        sub = select(A.id, num).where(A.req_id.in_(reqs)).subquery()
        q = select(A).join(sub, sub.c.id == A.id).where(sub.c.n <= 3).order_by(A.id)
        for a in (await session.execute(q)).scalars().all():
            primeiras.setdefault(a.req_id, []).append(a)
    fk_nomes = await leitura.nomes_de_fk(session, [a for v in primeiras.values() for a in v])

    telas = [
        r[0]
        for r in await session.execute(
            select(distinct(E.tela))
            .where(E.criado_em >= desde, E.tela.is_not(None))
            .order_by(E.tela)
        )
    ]
    pessoas = [
        {"id": str(r[0]), "nome": r[1]}
        for r in await session.execute(
            select(E.ator_id, func.max(E.ator_nome))
            .where(E.criado_em >= desde, E.ator_id.is_not(None))
            .group_by(E.ator_id)
            .order_by(func.max(E.ator_nome))
        )
    ]

    return {
        "items": [
            {
                "id": e.id,
                "criado_em": e.criado_em.isoformat(),
                "ator": e.ator_nome,
                "via": e.via,
                "tela": e.tela,
                "acao": e.acao,
                "metodo": e.metodo,
                "status": e.status,
                "n_alteracoes": e.n_alteracoes,
                "itens": e.itens_texto,
                "alteracoes": leitura.agrupar(
                    [leitura.alteracao_out(a, fk_nomes) for a in primeiras.get(e.req_id, [])]
                ),
            }
            for e in eventos
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "telas": telas,
        "pessoas": pessoas,
        "pode_gerenciar": meu_acesso.pode_gerenciar,
    }


# --- Envios Threema (tarefa 16, C2) -----------------------------------------------
#
# O registro de cada envio do Threema (`threema_envio_registro`, gravado pelo
# `send_to_all`): uma linha por destinatário, agrupada aqui por LOTE (uma
# chamada de envio). Mesma porta do resto do Histórico. Declarada ANTES de
# `/{evento_id}`, que capturaria "threema".

ORIGENS_THREEMA = ("automatico", "manual", "teste")
SEM_CHAVE = "sem_chave"


def _mascarar(rid: str | None) -> str | None:
    # Nunca o ID inteiro na tela: as 2 primeiras letras bastam para conferir.
    return f"{rid[:2]}••••••" if rid else None


def _envio_info(chave: str | None) -> dict:
    d = CATALOGO.get(chave or "")
    if d is None:
        return {
            "chave": chave,
            "nome": chave or "Sem marca (ponto de envio não catalogado)",
            "painel": None,
        }
    return {"chave": d.chave, "nome": d.nome, "painel": d.painel}


@router.get("/threema")
async def envios_threema(
    acesso: Acesso,
    session: Sessao,
    dias: Annotated[int, Query(ge=1, le=366)] = 7,
    chave: Annotated[str | None, Query(max_length=60)] = None,
    ok: bool | None = None,
    origem: str | None = None,
    busca: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Os envios do Threema do período, um item por lote (uma chamada de
    envio), mais novo primeiro. `ok=true`: lotes em que todos receberam;
    `ok=false`: lotes com alguma falha. `chave=sem_chave`: os envios sem
    marca (ponto que o levantamento não pegou)."""
    desde = _desde(dias)
    R = ThreemaEnvioRegistro  # noqa: N806 - apelido curto das consultas
    filtros = [R.criado_em >= desde]
    if chave == SEM_CHAVE:
        filtros.append(R.envio_chave.is_(None))
    elif chave:
        filtros.append(R.envio_chave == chave)
    if origem in ORIGENS_THREEMA:
        filtros.append(R.origem == origem)
    if busca and busca.strip():
        termo = busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        b = f"%{termo}%"
        # Filtra o LOTE inteiro (achou em um destinatário, vêm todos os dele).
        B = aliased(ThreemaEnvioRegistro)  # noqa: N806
        filtros.append(
            R.lote_id.in_(
                select(B.lote_id).where(
                    B.criado_em >= desde,
                    or_(
                        B.titulo.ilike(b, escape="\\"),
                        B.destinatario_nome.ilike(b, escape="\\"),
                        B.ator_nome.ilike(b, escape="\\"),
                        B.envio_chave.ilike(b, escape="\\"),
                    ),
                )
            )
        )
    n = func.count().label("n")
    n_ok = func.count().filter(R.ok).label("n_ok")
    lotes = (
        select(R.lote_id, func.min(R.criado_em).label("criado_em"), n, n_ok)
        .where(*filtros)
        .group_by(R.lote_id)
    )
    if ok is True:
        lotes = lotes.having(func.count().filter(R.ok) == func.count())
    elif ok is False:
        lotes = lotes.having(func.count().filter(R.ok) < func.count())
    sub = lotes.subquery()
    total = (await session.execute(select(func.count()).select_from(sub))).scalar_one()
    pagina = (
        (
            await session.execute(
                select(sub.c.lote_id)
                .order_by(sub.c.criado_em.desc(), sub.c.lote_id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    linhas: dict[UUID, list[ThreemaEnvioRegistro]] = {lote: [] for lote in pagina}
    if pagina:
        q = select(R).where(R.lote_id.in_(list(pagina)), R.criado_em >= desde).order_by(R.id)
        for r in (await session.execute(q)).scalars():
            linhas[r.lote_id].append(r)

    itens = []
    for lote in pagina:
        rs = linhas[lote]
        if not rs:
            continue
        p = rs[0]
        itens.append(
            {
                "lote_id": str(lote),
                "criado_em": min(r.criado_em for r in rs).isoformat(),
                "envio": _envio_info(p.envio_chave),
                "escopo": p.escopo,
                "contexto": p.contexto,
                "canal": p.canal,
                "onde_roda": p.onde_roda,
                "origem": p.origem,
                "via": p.via,
                "ator": p.ator_nome,
                "titulo": p.titulo,
                "bytes": p.bytes,
                "total": sum(1 for r in rs if r.destinatario_id),
                "entregues": sum(1 for r in rs if r.ok),
                "falhas": sum(1 for r in rs if not r.ok),
                # Erro antes de tentar qualquer pessoa (configuração, ninguém).
                "erro_geral": next((r.erro for r in rs if not r.destinatario_id), None),
                "destinatarios": [
                    {
                        "nome": r.destinatario_nome,
                        "id": _mascarar(r.destinatario_id),
                        "ok": r.ok,
                        "http_status": r.http_status,
                        "erro": r.erro,
                        "quando": r.criado_em.isoformat(),
                    }
                    for r in rs
                    if r.destinatario_id
                ],
            }
        )

    # Os envios que aparecem no período (para o filtro), com o nome do catálogo.
    chaves = [
        r[0]
        for r in await session.execute(select(distinct(R.envio_chave)).where(R.criado_em >= desde))
    ]
    envios = sorted(
        (_envio_info(c) for c in chaves),
        key=lambda e: ((e["painel"] or "~"), e["nome"].lower()),
    )
    for e in envios:
        if e["chave"] is None:
            e["chave"] = SEM_CHAVE
    return {
        "items": itens,
        "total": total,
        "limit": limit,
        "offset": offset,
        "envios": envios,
    }


# --- quem pode ver (só o Eduardo) ------------------------------------------------


class AcessoIn(BaseModel):
    liberado: bool


def _gerente(acesso: tuple[User, HistoricoAcesso]) -> User:
    user, meu = acesso
    if not meu.pode_gerenciar:
        raise _nao_existe()
    return user


def _situacao(u: User) -> str | None:
    if u.status != UserStatus.ACTIVE:
        return "suspenso"
    if u.role != UserRole.ADMIN:
        return "não é mais admin"
    return None


@router.get("/acesso")
async def listar_acesso(acesso: Acesso, session: Sessao) -> list[dict]:
    _gerente(acesso)
    liberados = {a.user_id: a for a in (await session.execute(select(HistoricoAcesso))).scalars()}
    filtro = and_(User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE)
    if liberados:
        # liberado que depois foi suspenso ou deixou de ser admin continua na
        # lista, marcado, para o Eduardo poder tirar
        filtro = or_(filtro, User.id.in_(list(liberados)))
    pessoas = (await session.execute(select(User).where(filtro).order_by(User.name))).scalars()
    return [
        {
            "id": str(u.id),
            "nome": u.name or u.email,
            "liberado": u.id in liberados,
            "pode_gerenciar": bool(liberados.get(u.id) and liberados[u.id].pode_gerenciar),
            "situacao": _situacao(u),
        }
        for u in pessoas
    ]


@router.put("/acesso/{user_id}")
async def mudar_acesso(user_id: str, request: Request, acesso: Acesso, session: Sessao) -> dict:
    # Corpo e id lidos só DEPOIS de saber que a pessoa gerencia: com corpo
    # Pydantic o FastAPI responderia 422 a um JSON quebrado antes da trava,
    # entregando que a rota existe.
    eu = _gerente(acesso)
    try:
        body = AcessoIn.model_validate(await request.json())
        user_id = UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(400, detail={"code": "pedido_invalido"}) from None
    if user_id == eu.id:
        raise HTTPException(400, detail={"code": "nao_pode_se_tirar"})
    atual = await session.get(HistoricoAcesso, user_id)
    if body.liberado:
        alvo = await session.get(User, user_id)
        if alvo is None or _situacao(alvo) is not None:
            raise HTTPException(400, detail={"code": "so_admin_ativo"})
        if atual is None:
            session.add(HistoricoAcesso(user_id=user_id, pode_gerenciar=False, liberado_por=eu.id))
    elif atual is not None:
        # Tirar vale para qualquer um (inclusive suspenso), menos quem gerencia.
        if atual.pode_gerenciar:
            raise HTTPException(400, detail={"code": "nao_pode_tirar_gerente"})
        await session.delete(atual)
    await session.commit()
    return {"ok": True}


# --- detalhe de um evento ---------------------------------------------------------


@router.get("/{evento_id}")
async def detalhe(evento_id: str, acesso: Acesso, session: Sessao) -> dict:
    if not evento_id.isdigit():
        raise _nao_existe()
    e = await session.get(HistoricoEvento, int(evento_id))
    if e is None:
        raise _nao_existe()
    q = (
        select(HistoricoAlteracao)
        .where(HistoricoAlteracao.req_id == e.req_id)
        .order_by(HistoricoAlteracao.id)
    )
    alteracoes = (await session.execute(q)).scalars().all()
    fk_nomes = await leitura.nomes_de_fk(session, list(alteracoes))
    return {
        "id": e.id,
        "criado_em": e.criado_em.isoformat(),
        "ator": e.ator_nome,
        "via": e.via,
        "tela": e.tela,
        "pagina": e.pagina,
        "acao": e.acao,
        "metodo": e.metodo,
        "status": e.status,
        "caminho": e.caminho,
        "ip": e.ip,
        "corpo": leitura.corpo_sem_ids_threema(e.corpo),
        "n_alteracoes": e.n_alteracoes,
        "itens": e.itens_texto,
        "alteracoes": leitura.agrupar([leitura.alteracao_out(a, fk_nomes) for a in alteracoes]),
    }


# Qualquer outro método/caminho daqui: o mesmo 404 (sem isso, um POST em
# /api/historico daria 405 e entregaria que a rota existe).
@router.api_route(
    "/{resto:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
async def _resto(resto: str) -> None:
    raise _nao_existe()


@router.api_route("", methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def _raiz_outros_metodos() -> None:
    raise _nao_existe()
