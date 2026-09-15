"""Cadastros › E-mails — padrões de e-mail por marca e canal (15/09/2026).

Eduardo: "o envio automático de e-mail dos SAC tem que ser padronizado com
logo da marca, assinatura da empresa, site, logo do zap e o zap" e "em
cadastro, uma padronização de e-mails por marca: o padrão pro SAC, o padrão
pro Mercado Livre…". Recurso de permissão `email_padroes` (view/edit/delete).

Além do CRUD: POST /preview renderiza um rascunho (prévia na tela), POST
/{id}/render devolve html+texto prontos (robôs/automações) e POST
/{id}/enviar-teste manda o e-mail pelo sender do app (Mailjet em prod; sem
chave, o ConsoleEmailSender só registra no log — a resposta diz qual foi).
A renderização é sandbox Jinja (services/email_marca.py).
"""

from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.config import get_settings
from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.models import EMAIL_CONTEXTOS, Company, Marca, MarcaEmailPadrao, User, UserRole
from app.schemas.marcas import (
    EmailPadraoCreate,
    EmailPadraoOut,
    EmailPadraoPatch,
    EmailPadroesGridOut,
    EmailPadroesGridRow,
    EmailPreviewIn,
    EmailRenderIn,
    EmailRenderOut,
    EmailTesteIn,
    EmailTesteOut,
    MarcaRefEmail,
)
from app.services.email import get_email_sender
from app.services.email_marca import (
    EmailRenderizado,
    TemplateInvalidoError,
    render_email,
    render_padrao,
)
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()
router = APIRouter(prefix="/api/email-padroes", tags=["email_padroes"])

_NAO_NULOS = (
    "marca_id",
    "contexto",
    "nome",
    "assunto",
    "corpo",
    "incluir_logo",
    "incluir_assinatura",
    "ativo",
)

_view = require_permission("email_padroes", "view")
_edit = require_permission("email_padroes", "edit")
_delete = require_permission("email_padroes", "delete")


def marca_ref_email(m: Marca) -> MarcaRefEmail:
    out = MarcaRefEmail.model_validate(m)
    out.has_logo = bool(m.logo_mime)
    return out


def padrao_out(p: MarcaEmailPadrao, marca_nome: str) -> EmailPadraoOut:
    out = EmailPadraoOut.model_validate(p)
    out.marca_nome = marca_nome
    return out


def _render_out(r: EmailRenderizado) -> EmailRenderOut:
    return EmailRenderOut(
        assunto=r.assunto,
        html=r.html,
        text=r.text,
        from_email=r.from_email,
        from_name=r.from_name,
        reply_to=r.reply_to[0] if r.reply_to else None,
        inline_images=sorted(r.inline_images.keys()),
        avisos=r.avisos,
        variaveis_desconhecidas=r.variaveis_desconhecidas,
    )


def _template_422(e: TemplateInvalidoError) -> HTTPException:
    msg = str(e)
    code = msg.split(":", 1)[0].strip() or "template_invalido"
    return HTTPException(422, detail={"code": code, "erro": msg[:300]})


async def _get_or_404(session: AsyncSession, padrao_id: UUID) -> MarcaEmailPadrao:
    p = (
        await session.execute(select(MarcaEmailPadrao).where(MarcaEmailPadrao.id == padrao_id))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "email_padrao_not_found"})
    return p


async def _marca_or_404(session: AsyncSession, marca_id: UUID, *, com_logo: bool = False) -> Marca:
    stmt = select(Marca).where(Marca.id == marca_id)
    if com_logo:
        # `logo` é deferred no model; a renderização precisa dos bytes.
        stmt = stmt.options(undefer(Marca.logo))
    m = (await session.execute(stmt)).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return m


async def _company(session: AsyncSession, marca: Marca) -> Company | None:
    if marca.company_id is None:
        return None
    return (
        await session.execute(select(Company).where(Company.id == marca.company_id))
    ).scalar_one_or_none()


async def _checa_conflito(
    session: AsyncSession,
    *,
    marca_id: UUID,
    contexto: str,
    nome: str,
    exceto: UUID | None = None,
) -> None:
    stmt = select(MarcaEmailPadrao.id).where(
        MarcaEmailPadrao.marca_id == marca_id,
        MarcaEmailPadrao.contexto == contexto,
        func.lower(MarcaEmailPadrao.nome) == nome.lower(),
    )
    if exceto is not None:
        stmt = stmt.where(MarcaEmailPadrao.id != exceto)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, detail={"code": "email_padrao_conflict"})


@router.get("/grid", response_model=EmailPadroesGridOut)
async def email_padroes_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> EmailPadroesGridOut:
    """Linha = marca (todas), coluna = contexto (SAC, Mercado Livre…)."""
    marcas = (await session.execute(select(Marca).order_by(Marca.nome))).scalars().all()
    padroes = (
        await session.execute(
            select(MarcaEmailPadrao).order_by(MarcaEmailPadrao.contexto, MarcaEmailPadrao.nome)
        )
    ).scalars().all()
    by_id = {m.id: m for m in marcas}
    cells_by_marca: dict[UUID, dict[str, list[EmailPadraoOut]]] = {
        m.id: {c: [] for c in EMAIL_CONTEXTOS} for m in marcas
    }
    for p in padroes:
        cells = cells_by_marca.get(p.marca_id)
        if cells is None:
            continue
        cells.setdefault(p.contexto, []).append(padrao_out(p, by_id[p.marca_id].nome))
    rows = [
        EmailPadroesGridRow(marca=marca_ref_email(m), cells=cells_by_marca[m.id]) for m in marcas
    ]
    return EmailPadroesGridOut(contextos=list(EMAIL_CONTEXTOS), rows=rows)


@router.get("", response_model=list[EmailPadraoOut])
async def list_email_padroes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    marca_id: UUID | None = Query(None),
    contexto: str | None = Query(None),
) -> list[EmailPadraoOut]:
    stmt = select(MarcaEmailPadrao, Marca.nome).join(Marca, Marca.id == MarcaEmailPadrao.marca_id)
    if marca_id is not None:
        stmt = stmt.where(MarcaEmailPadrao.marca_id == marca_id)
    if contexto:
        stmt = stmt.where(MarcaEmailPadrao.contexto == contexto.strip().lower())
    rows = (
        await session.execute(
            stmt.order_by(Marca.nome, MarcaEmailPadrao.contexto, MarcaEmailPadrao.nome)
        )
    ).all()
    return [padrao_out(p, nome) for p, nome in rows]


@router.post("/preview", response_model=EmailRenderOut)
async def preview_email_padrao(
    body: EmailPreviewIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> EmailRenderOut:
    """Prévia de um rascunho (não precisa estar salvo)."""
    m = await _marca_or_404(session, body.marca_id, com_logo=True)
    company = await _company(session, m)
    try:
        r = render_email(
            marca=m,
            company=company,
            assunto=body.assunto,
            corpo=body.corpo,
            incluir_logo=body.incluir_logo,
            incluir_assinatura=body.incluir_assinatura,
            remetente_nome=body.remetente_nome,
            remetente_email=body.remetente_email,
            variaveis=body.variaveis,
            imagens="data",
            previa=True,
        )
    except TemplateInvalidoError as e:
        raise _template_422(e) from e
    return _render_out(r)


@router.get("/{padrao_id}", response_model=EmailPadraoOut)
async def get_email_padrao(
    padrao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> EmailPadraoOut:
    p = await _get_or_404(session, padrao_id)
    m = await _marca_or_404(session, p.marca_id)
    return padrao_out(p, m.nome)


@router.post("", response_model=EmailPadraoOut, status_code=status.HTTP_201_CREATED)
async def create_email_padrao(
    body: EmailPadraoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> EmailPadraoOut:
    m = await _marca_or_404(session, body.marca_id)
    await _checa_conflito(session, marca_id=body.marca_id, contexto=body.contexto, nome=body.nome)
    p = MarcaEmailPadrao(**body.model_dump())
    session.add(p)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": "email_padrao_conflict"}) from e
    await session.refresh(p)
    logger.info(
        "email_padrao_created", padrao_id=str(p.id), marca_id=str(p.marca_id), contexto=p.contexto
    )
    return padrao_out(p, m.nome)


@router.patch("/{padrao_id}", response_model=EmailPadraoOut)
async def patch_email_padrao(
    padrao_id: UUID,
    body: EmailPadraoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> EmailPadraoOut:
    p = await _get_or_404(session, padrao_id)
    data = body.model_dump(exclude_unset=True)
    for k in _NAO_NULOS:
        # Colunas NOT NULL: null no body = "não mexe".
        if k in data and data[k] is None:
            data.pop(k)
    if "marca_id" in data:
        await _marca_or_404(session, data["marca_id"])
    chave_nova = (
        data.get("marca_id", p.marca_id),
        data.get("contexto", p.contexto),
        data.get("nome", p.nome),
    )
    if chave_nova != (p.marca_id, p.contexto, p.nome):
        await _checa_conflito(
            session, marca_id=chave_nova[0], contexto=chave_nova[1], nome=chave_nova[2], exceto=p.id
        )
    for k, v in data.items():
        setattr(p, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": "email_padrao_conflict"}) from e
    await session.refresh(p)
    m = await _marca_or_404(session, p.marca_id)
    return padrao_out(p, m.nome)


@router.delete("/{padrao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_email_padrao(
    padrao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_delete)],
) -> None:
    p = await _get_or_404(session, padrao_id)
    await session.delete(p)
    await session.commit()
    logger.info("email_padrao_deleted", padrao_id=str(padrao_id))
    return None


@router.post("/{padrao_id}/render", response_model=EmailRenderOut)
async def render_email_padrao(
    padrao_id: UUID,
    body: EmailRenderIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> EmailRenderOut:
    """HTML + texto prontos com as variáveis do contexto (cliente, pedido…) —
    pra robôs e automações montarem o e-mail sem reimplementar a assinatura."""
    p = await _get_or_404(session, padrao_id)
    m = await _marca_or_404(session, p.marca_id, com_logo=True)
    try:
        r = render_padrao(p, m, await _company(session, m), body.variaveis)
    except TemplateInvalidoError as e:
        raise _template_422(e) from e
    return _render_out(r)


@router.post("/{padrao_id}/enviar-teste", response_model=EmailTesteOut)
async def enviar_teste_email_padrao(
    padrao_id: UUID,
    body: EmailTesteIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> EmailTesteOut:
    """Manda o padrão renderizado pro e-mail informado (teste). Em prod sai
    pelo Mailjet — o remetente da marca precisa estar validado lá, senão a
    API recusa e devolvemos 502 com o motivo. Sem chave (dev) o
    ConsoleEmailSender só registra no log e `sender` avisa."""
    p = await _get_or_404(session, padrao_id)
    m = await _marca_or_404(session, p.marca_id, com_logo=True)
    proprio = (user.email or "").lower()
    if get_settings().is_prod and user.role != UserRole.ADMIN and body.para != proprio:
        # Em produção o teste só vai pro próprio e-mail de quem pediu (admin
        # pode mandar pra qualquer um) — evita usar a conta Mailjet da
        # empresa pra disparar e-mail com a marca pra endereço alheio.
        raise HTTPException(403, detail={"code": "teste_so_para_proprio_email"})
    try:
        # 5 testes por usuário a cada 10 min (mesmo helper do OTP).
        await sliding_window_check(key=f"email_teste:{user.id}", limit=5, window_seconds=600)
    except RateLimitError as e:
        raise HTTPException(429, detail={"code": "rate_limited"}) from e
    try:
        r = render_padrao(p, m, await _company(session, m), body.variaveis)
    except TemplateInvalidoError as e:
        raise _template_422(e) from e
    sender = get_email_sender()
    nome_sender = getattr(sender, "name", type(sender).__name__)
    try:
        await sender.send(
            to=body.para,
            subject=f"[TESTE] {r.assunto}",
            html=r.html,
            text=r.text,
            from_email=r.from_email,
            from_name=r.from_name,
            reply_to=r.reply_to,
            inline_images=r.inline_images or None,
        )
    except httpx.HTTPStatusError as e:
        logger.warning(
            "email_padrao_teste_recusado",
            padrao_id=str(padrao_id),
            status=e.response.status_code,
            erro=e.response.text[:300],
        )
        raise HTTPException(
            502,
            detail={
                "code": "email_envio_falhou",
                "status": e.response.status_code,
                "erro": e.response.text[:300],
            },
        ) from e
    except Exception as e:
        logger.warning("email_padrao_teste_falhou", padrao_id=str(padrao_id), erro=str(e)[:300])
        raise HTTPException(
            502, detail={"code": "email_envio_falhou", "erro": str(e)[:300]}
        ) from e
    logger.info(
        "email_padrao_teste_enviado",
        padrao_id=str(padrao_id),
        para=body.para,
        sender=nome_sender,
        user_id=str(user.id),
    )
    return EmailTesteOut(ok=True, sender=nome_sender, para=body.para)
