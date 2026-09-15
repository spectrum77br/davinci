"""Cadastros › Redes Sociais — contas por marca e plataforma (15/09/2026).

Recurso de permissão `redes_sociais` (view/edit/delete), separado de
`marcas`: os grids devolvem só um `MarcaRef` (sem o login/e-mail do registro
INPI nem domínios, que ficam atrás da permissão `marcas`). Como na planilha
(aba r.social), fone / usuário(e-mail) / senha das redes são da MARCA
(`sac_*`): esta aba edita essas colunas por PATCH /marca/{marca_id} e revela
a senha da marca por GET /marca/{marca_id}/sac-senha — tudo sob
`redes_sociais:edit`. A conta só guarda e-mail/fone/senha quando DIFEREM
(NULL = herda; os `*_efetivo` do Out já resolvem isso).

Unicidade (índices parciais em models/marca.py): a mesma conta não pode
estar em duas marcas na mesma plataforma (`rede_social_conta_conflict`) e
cada (marca, plataforma) tem no máximo uma linha sem conta
(`rede_social_placeholder_conflict`). Pré-checa pra dar o código certo e
ainda captura IntegrityError (corrida) → 409.

Senha: nunca em listagem, `has_senha`/`has_senha_efetiva`, GET /{id}/senha
sob edit com log (devolve a da conta ou, sem ela, a herdada da marca — com
`origem`).
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.models import REDES_SOCIAIS_PLATAFORMAS, Marca, RedeSocial, User
from app.schemas.marcas import (
    MarcaRef,
    MarcaSocialPatch,
    RedeSocialCreate,
    RedeSocialOut,
    RedeSocialPatch,
    RedesSociaisGridOut,
    RedesSociaisGridRow,
    SenhaOut,
)
from app.security.cipher import decrypt, encrypt

logger = structlog.get_logger()
router = APIRouter(prefix="/api/redes-sociais", tags=["redes_sociais"])

_NAO_NULOS = ("marca_id", "plataforma", "verificacao_status", "ativo")

_view = require_permission("redes_sociais", "view")
_edit = require_permission("redes_sociais", "edit")
_delete = require_permission("redes_sociais", "delete")


def marca_ref(m: Marca) -> MarcaRef:
    out = MarcaRef.model_validate(m)
    out.has_sac_senha = bool(m.sac_senha_enc)
    out.has_logo = bool(m.logo_mime)
    return out


def rede_out(r: RedeSocial, marca: Marca) -> RedeSocialOut:
    out = RedeSocialOut.model_validate(r)
    out.marca_nome = marca.nome
    out.has_senha = bool(r.senha_enc)
    # Efetivos: o que a conta tem, senão o da marca (planilha: uma credencial
    # por marca, compartilhada pelas redes).
    out.email_efetivo = r.email or marca.sac_email
    out.fone_efetivo = r.fone or marca.sac_fone
    if r.senha_enc:
        out.senha_origem = "conta"
    elif marca.sac_senha_enc:
        out.senha_origem = "marca"
    out.has_senha_efetiva = out.senha_origem is not None
    return out


async def _get_or_404(session: AsyncSession, rede_id: UUID) -> RedeSocial:
    r = (
        await session.execute(select(RedeSocial).where(RedeSocial.id == rede_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail={"code": "rede_social_not_found"})
    return r


async def _marca_or_404(session: AsyncSession, marca_id: UUID) -> Marca:
    m = (await session.execute(select(Marca).where(Marca.id == marca_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return m


async def _checa_conflito(
    session: AsyncSession,
    *,
    marca_id: UUID,
    plataforma: str,
    conta: str | None,
    exceto: UUID | None = None,
) -> None:
    """Mesmas regras dos índices parciais, com código de erro legível."""
    if conta is not None:
        stmt = select(RedeSocial.id).where(
            RedeSocial.plataforma == plataforma,
            func.lower(RedeSocial.conta) == conta.lower(),
        )
        code = "rede_social_conta_conflict"
    else:
        stmt = select(RedeSocial.id).where(
            RedeSocial.marca_id == marca_id,
            RedeSocial.plataforma == plataforma,
            RedeSocial.conta.is_(None),
        )
        code = "rede_social_placeholder_conflict"
    if exceto is not None:
        stmt = stmt.where(RedeSocial.id != exceto)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, detail={"code": code})


def _conflict_code(conta: str | None) -> str:
    return "rede_social_conta_conflict" if conta else "rede_social_placeholder_conflict"


def _revela(valor_enc: str | None, *, log_evento: str, **log_kw: str) -> str:
    try:
        senha = decrypt(valor_enc or "")
    except Exception as e:
        logger.error(f"{log_evento}_decrypt_failed", **log_kw)
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info(log_evento, **log_kw)
    return senha


# ================================================================= linha da marca


@router.patch("/marca/{marca_id}", response_model=MarcaRef)
async def patch_marca_social(
    marca_id: UUID,
    body: MarcaSocialPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaRef:
    """Colunas da marca que a aba Redes Sociais edita (fone/usuário/senha das
    redes, verificação do Zap, função, tipo, obs) — gate `redes_sociais:edit`."""
    m = await _marca_or_404(session, marca_id)
    data = body.model_dump(exclude_unset=True)
    if "sac_senha" in data:
        pwd = data.pop("sac_senha")
        m.sac_senha_enc = encrypt(pwd) if pwd else None
    if data.get("whatsapp_verificacao_status") is None:
        data.pop("whatsapp_verificacao_status", None)
    for k, v in data.items():
        setattr(m, k, v)
    await session.commit()
    await session.refresh(m)
    return marca_ref(m)


@router.get("/marca/{marca_id}/sac-senha", response_model=SenhaOut)
async def reveal_marca_sac_senha(
    marca_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> SenhaOut:
    m = await _marca_or_404(session, marca_id)
    response.headers["Cache-Control"] = "no-store"
    if not m.sac_senha_enc:
        return SenhaOut(senha="")
    senha = _revela(
        m.sac_senha_enc,
        log_evento="marca_sac_senha_revelada",
        user_id=str(user.id),
        marca_id=str(marca_id),
    )
    return SenhaOut(senha=senha, origem="marca")


# ========================================================================= contas


@router.get("/grid", response_model=RedesSociaisGridOut)
async def redes_sociais_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> RedesSociaisGridOut:
    """Linha = marca (todas, mesmo sem conta), coluna = plataforma."""
    marcas = (await session.execute(select(Marca).order_by(Marca.nome))).scalars().all()
    redes = (
        await session.execute(
            select(RedeSocial).order_by(RedeSocial.plataforma, RedeSocial.conta)
        )
    ).scalars().all()
    by_id = {m.id: m for m in marcas}
    cells_by_marca: dict[UUID, dict[str, list[RedeSocialOut]]] = {
        m.id: {p: [] for p in REDES_SOCIAIS_PLATAFORMAS} for m in marcas
    }
    for r in redes:
        cells = cells_by_marca.get(r.marca_id)
        if cells is None:
            continue
        # Plataforma removida do enum ainda aparece (não some dado da tela).
        cells.setdefault(r.plataforma, []).append(rede_out(r, by_id[r.marca_id]))
    rows = [RedesSociaisGridRow(marca=marca_ref(m), cells=cells_by_marca[m.id]) for m in marcas]
    return RedesSociaisGridOut(plataformas=list(REDES_SOCIAIS_PLATAFORMAS), rows=rows)


@router.get("", response_model=list[RedeSocialOut])
async def list_redes_sociais(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    marca_id: UUID | None = Query(None),
    plataforma: str | None = Query(None),
    search: str | None = Query(None),
) -> list[RedeSocialOut]:
    stmt = select(RedeSocial, Marca).join(Marca, Marca.id == RedeSocial.marca_id)
    if marca_id is not None:
        stmt = stmt.where(RedeSocial.marca_id == marca_id)
    if plataforma:
        stmt = stmt.where(RedeSocial.plataforma == plataforma.strip().lower())
    if search:
        like = f"%{search.strip().lower().lstrip('@')}%"
        stmt = stmt.where(
            or_(RedeSocial.conta.ilike(like), RedeSocial.email.ilike(like), Marca.nome.ilike(like))
        )
    rows = (
        await session.execute(stmt.order_by(Marca.nome, RedeSocial.plataforma, RedeSocial.conta))
    ).all()
    return [rede_out(r, m) for r, m in rows]


@router.get("/{rede_id}", response_model=RedeSocialOut)
async def get_rede_social(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> RedeSocialOut:
    r = await _get_or_404(session, rede_id)
    m = await _marca_or_404(session, r.marca_id)
    return rede_out(r, m)


@router.post("", response_model=RedeSocialOut, status_code=status.HTTP_201_CREATED)
async def create_rede_social(
    body: RedeSocialCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> RedeSocialOut:
    m = await _marca_or_404(session, body.marca_id)
    await _checa_conflito(
        session, marca_id=body.marca_id, plataforma=body.plataforma, conta=body.conta
    )
    data = body.model_dump(exclude={"senha"})
    r = RedeSocial(senha_enc=encrypt(body.senha) if body.senha else None, **data)
    session.add(r)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": _conflict_code(body.conta)}) from e
    await session.refresh(r)
    logger.info(
        "rede_social_created",
        rede_id=str(r.id),
        marca_id=str(r.marca_id),
        plataforma=r.plataforma,
        conta=r.conta,
    )
    return rede_out(r, m)


@router.patch("/{rede_id}", response_model=RedeSocialOut)
async def patch_rede_social(
    rede_id: UUID,
    body: RedeSocialPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> RedeSocialOut:
    r = await _get_or_404(session, rede_id)
    data = body.model_dump(exclude_unset=True)
    if "senha" in data:
        # Ausente = mantém; ""/null = limpa (volta a herdar da marca); texto = cifra.
        pwd = data.pop("senha")
        r.senha_enc = encrypt(pwd) if pwd else None
    for k in _NAO_NULOS:
        # Colunas NOT NULL: null no body = "não mexe".
        if k in data and data[k] is None:
            data.pop(k)
    if "marca_id" in data:
        await _marca_or_404(session, data["marca_id"])
    novo_marca_id = data.get("marca_id", r.marca_id)
    nova_plataforma = data.get("plataforma", r.plataforma)
    nova_conta = data["conta"] if "conta" in data else r.conta
    if (novo_marca_id, nova_plataforma, nova_conta) != (r.marca_id, r.plataforma, r.conta):
        await _checa_conflito(
            session,
            marca_id=novo_marca_id,
            plataforma=nova_plataforma,
            conta=nova_conta,
            exceto=r.id,
        )
    for k, v in data.items():
        setattr(r, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": _conflict_code(nova_conta)}) from e
    await session.refresh(r)
    m = await _marca_or_404(session, r.marca_id)
    return rede_out(r, m)


@router.delete("/{rede_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rede_social(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_delete)],
) -> None:
    r = await _get_or_404(session, rede_id)
    await session.delete(r)
    await session.commit()
    logger.info("rede_social_deleted", rede_id=str(rede_id))
    return None


@router.get("/{rede_id}/senha", response_model=SenhaOut)
async def reveal_rede_social_senha(
    rede_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> SenhaOut:
    """Senha EFETIVA da conta sob demanda (só edit): a própria ou, sem ela, a
    herdada da marca (`origem`). "" se não há nenhuma. Sem cache, com log."""
    r = await _get_or_404(session, rede_id)
    m = await _marca_or_404(session, r.marca_id)
    response.headers["Cache-Control"] = "no-store"
    if r.senha_enc:
        senha = _revela(
            r.senha_enc,
            log_evento="rede_social_senha_revelada",
            user_id=str(user.id),
            rede_id=str(rede_id),
        )
        return SenhaOut(senha=senha, origem="conta")
    if m.sac_senha_enc:
        senha = _revela(
            m.sac_senha_enc,
            log_evento="marca_sac_senha_revelada",
            user_id=str(user.id),
            marca_id=str(m.id),
            via_rede_id=str(rede_id),
        )
        return SenhaOut(senha=senha, origem="marca")
    return SenhaOut(senha="")
