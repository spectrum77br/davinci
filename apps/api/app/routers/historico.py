"""Sistema › Histórico: quem mudou o quê no DaVinci.

Invisível para quem não está em `historico_acesso` — inclusive admin
(Eduardo, 25/09/2026: "essa aba tem que ser invisível para todas as outras
pessoas do nosso sistema, ATÉ PARA ADMIN"). Para essas pessoas toda rota daqui
responde exatamente o 404 de uma rota que não existe, com qualquer método, e o
router fica fora da documentação pública (/api/docs).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Text, and_, cast, distinct, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import get_current_user
from app.historico import nomes
from app.models import (
    HistoricoAcesso,
    HistoricoAlteracao,
    HistoricoEvento,
    User,
    UserRole,
    UserStatus,
)
from app.models.base import Base

router = APIRouter(prefix="/api/historico", include_in_schema=False)


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


# --- formatação ---------------------------------------------------------------


def _numero(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}".replace(",", ".")
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict):
        if v.get("_oculto"):
            return "(oculta)"
        if v.get("_arquivo"):
            kb = max(1, round((v.get("bytes") or 0) / 1024))
            return f"arquivo ({kb} KB)"
        if v.get("_longo"):
            return "(texto longo)"
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, (int, float)):
        return _numero(v)
    if isinstance(v, str):
        return v if v != "" else "(vazio)"
    return json.dumps(v, ensure_ascii=False)[:500]


# Coluna que dá nome à linha de outra tabela (para "dg053 · ML kia").
_ROTULOS = (
    "sku", "apelido", "nome", "name", "account_name", "titulo", "numero", "codigo",
    "razao_social", "slug", "label", "email",
)


def _tabela_meta(tabela: str):
    return Base.metadata.tables.get(f"{Base.metadata.schema}.{tabela}")


def _fks(tabela: str) -> dict[str, Any]:
    """coluna -> Table de destino (só destino com coluna de nome)."""
    t = _tabela_meta(tabela)
    if t is None:
        return {}
    saida = {}
    for fk in t.foreign_keys:
        col = fk.parent.name
        if col == "user_id" and tabela != "historico_acesso":
            continue  # em quase tudo é o "dono" da linha, não o assunto
        destino = fk.column.table
        if any(c in destino.c for c in _ROTULOS):
            saida[col] = destino
    return saida


async def _nomes_de_fk(session: AsyncSession, alteracoes: list[HistoricoAlteracao]) -> dict:
    """{(tabela_destino, id): nome} para todos os ids de FK citados."""
    pedidos: dict[Any, set] = {}
    for a in alteracoes:
        for col, destino in _fks(a.tabela).items():
            for fonte in (a.ident or {}, a.antes or {}, a.depois or {}):
                v = fonte.get(col)
                if isinstance(v, (str, int)) and v != "":
                    pedidos.setdefault(destino, set()).add(v)
    achados: dict = {}
    for destino, ids in pedidos.items():
        coluna = next(destino.c[c] for c in _ROTULOS if c in destino.c)
        pk = destino.c.get("id")
        if pk is None:
            continue
        chaves = []
        for v in ids:
            try:
                chaves.append(UUID(str(v)) if str(pk.type).upper() == "UUID" else int(v))
            except (ValueError, TypeError):
                continue
        if not chaves:
            continue
        for r in await session.execute(select(pk, coluna).where(pk.in_(chaves))):
            achados[(destino.name, str(r[0]))] = r[1]
    return achados


def _e_codigo(k: str) -> bool:
    return k == "id" or k.endswith("_id") or k in ("numero", "codigo", "bling_id", "pedido")


def _item(a: HistoricoAlteracao, fk_nomes: dict) -> str:
    partes = []
    for col, destino in _fks(a.tabela).items():
        v = (a.ident or {}).get(col)
        if v is not None and (destino.name, str(v)) in fk_nomes:
            partes.append(str(fk_nomes[(destino.name, str(v))]))
    if a.rotulo:
        return " · ".join([a.rotulo, *partes]) if partes else a.rotulo
    if partes:
        return " · ".join(partes)
    return f"#{a.registro_id}" if a.registro_id else ""


def _campos(a: HistoricoAlteracao, fk_nomes: dict) -> list[dict]:
    fks = _fks(a.tabela)
    chaves = list(dict.fromkeys([*(a.antes or {}).keys(), *(a.depois or {}).keys()]))
    saida = []
    for k in chaves:
        antes = (a.antes or {}).get(k)
        depois = (a.depois or {}).get(k)

        def rotular(v, k=k):
            if k in fks and v is not None and not isinstance(v, dict):
                nome = fk_nomes.get((fks[k].name, str(v)))
                if nome:
                    return str(nome)
            if isinstance(v, int) and not isinstance(v, bool) and _e_codigo(k):
                return str(v)  # id e número de pedido sem ponto de milhar
            return _fmt(v)

        saida.append(
            {
                "campo": k,
                "nome": nomes.nome_campo(k),
                "antes": rotular(antes) if a.operacao != "I" else None,
                "depois": rotular(depois) if a.operacao != "D" else None,
            }
        )
    return saida


def _alteracao_out(a: HistoricoAlteracao, fk_nomes: dict) -> dict:
    return {
        "id": a.id,
        "tabela": a.tabela,
        "entidade": nomes.nome_tabela(a.tabela),
        "operacao": a.operacao,
        "verbo": nomes.VERBOS.get(a.operacao, "alterou"),
        "item": _item(a, fk_nomes) if a.operacao != "X" else (a.rotulo or ""),
        "campos": _campos(a, fk_nomes) if a.operacao != "X" else [],
    }


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
    user, meu_acesso = acesso
    desde = datetime.now(UTC) - timedelta(days=dias)
    E, A = HistoricoEvento, HistoricoAlteracao  # noqa: N806 - apelidos curtos das consultas
    base = [E.criado_em >= desde]
    if tela:
        base.append(E.tela == tela)
    if ator:
        base.append(E.ator_id == ator)
    if tipo in TIPOS:
        base.append(exists().where(and_(A.req_id == E.req_id, A.operacao == TIPOS[tipo])))
    elif tipo == "acao":
        base.append(E.n_alteracoes == 0)
    if busca and busca.strip():
        b = f"%{busca.strip()}%"
        base.append(
            or_(
                E.ator_nome.ilike(b),
                E.tela.ilike(b),
                E.acao.ilike(b),
                E.caminho.ilike(b),
                exists().where(
                    and_(
                        A.req_id == E.req_id,
                        or_(A.rotulo.ilike(b), A.registro_id == busca.strip(),
                            cast(A.ident, Text).ilike(b)),
                    )
                ),
            )
        )

    total = (await session.execute(select(func.count()).select_from(E).where(*base))).scalar_one()
    eventos = (
        (await session.execute(
            select(E).where(*base).order_by(E.criado_em.desc(), E.id.desc()).limit(limit).offset(offset)
        ))
        .scalars()
        .all()
    )

    # As 3 primeiras alterações de cada evento, para o resumo da linha.
    reqs = [e.req_id for e in eventos]
    primeiras: dict[UUID, list[HistoricoAlteracao]] = {}
    if reqs:
        num = func.row_number().over(partition_by=A.req_id, order_by=A.id).label("n")
        sub = select(A.id, num).where(A.req_id.in_(reqs)).subquery()
        linhas = (
            (await session.execute(
                select(A).join(sub, sub.c.id == A.id).where(sub.c.n <= 3).order_by(A.id)
            ))
            .scalars()
            .all()
        )
        for a in linhas:
            primeiras.setdefault(a.req_id, []).append(a)
    fk_nomes = await _nomes_de_fk(session, [a for v in primeiras.values() for a in v])

    telas = [
        r[0]
        for r in await session.execute(
            select(distinct(E.tela)).where(E.criado_em >= desde, E.tela.is_not(None)).order_by(E.tela)
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
                "n_alteracoes": e.n_alteracoes,
                "alteracoes": [_alteracao_out(a, fk_nomes) for a in primeiras.get(e.req_id, [])],
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


# --- quem pode ver (só o Eduardo) ------------------------------------------------


class AcessoIn(BaseModel):
    liberado: bool


def _gerente(acesso: tuple[User, HistoricoAcesso]) -> User:
    user, meu = acesso
    if not meu.pode_gerenciar:
        raise _nao_existe()
    return user


@router.get("/acesso")
async def listar_acesso(acesso: Acesso, session: Sessao) -> list[dict]:
    _gerente(acesso)
    liberados = {a.user_id: a for a in (await session.execute(select(HistoricoAcesso))).scalars()}
    admins = (
        await session.execute(
            select(User)
            .where(
                or_(
                    and_(User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE),
                    User.id.in_(list(liberados)),
                )
            )
            .order_by(User.name)
        )
    ).scalars().all()
    return [
        {
            "id": str(u.id),
            "nome": u.name or u.email,
            "liberado": u.id in liberados,
            "pode_gerenciar": bool(liberados.get(u.id) and liberados[u.id].pode_gerenciar),
        }
        for u in admins
    ]


@router.put("/acesso/{user_id}")
async def mudar_acesso(user_id: UUID, body: AcessoIn, acesso: Acesso, session: Sessao) -> dict:
    eu = _gerente(acesso)
    if user_id == eu.id:
        raise HTTPException(400, detail={"code": "nao_pode_se_tirar"})
    alvo = await session.get(User, user_id)
    if alvo is None or alvo.role != UserRole.ADMIN or alvo.status != UserStatus.ACTIVE:
        raise HTTPException(400, detail={"code": "so_admin_ativo"})
    atual = await session.get(HistoricoAcesso, user_id)
    if body.liberado and atual is None:
        session.add(HistoricoAcesso(user_id=user_id, pode_gerenciar=False, liberado_por=eu.id))
    elif not body.liberado and atual is not None:
        if atual.pode_gerenciar:
            raise HTTPException(400, detail={"code": "nao_pode_tirar_gerente"})
        await session.delete(atual)
    await session.commit()
    return {"ok": True}


# --- detalhe de um evento ---------------------------------------------------------


@router.get("/{evento_id}")
async def detalhe(evento_id: int, acesso: Acesso, session: Sessao) -> dict:
    e = await session.get(HistoricoEvento, evento_id)
    if e is None:
        raise _nao_existe()
    alteracoes = (
        (await session.execute(
            select(HistoricoAlteracao)
            .where(HistoricoAlteracao.req_id == e.req_id)
            .order_by(HistoricoAlteracao.id)
        ))
        .scalars()
        .all()
    )
    fk_nomes = await _nomes_de_fk(session, list(alteracoes))
    return {
        "id": e.id,
        "criado_em": e.criado_em.isoformat(),
        "ator": e.ator_nome,
        "via": e.via,
        "tela": e.tela,
        "pagina": e.pagina,
        "acao": e.acao,
        "metodo": e.metodo,
        "caminho": e.caminho,
        "ip": e.ip,
        "corpo": e.corpo,
        "n_alteracoes": e.n_alteracoes,
        "alteracoes": [_alteracao_out(a, fk_nomes) for a in alteracoes],
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
