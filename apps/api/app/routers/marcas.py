"""Cadastros › Marcas — CRUD, logo e senha do registro (15/09/2026).

Recurso de permissão `marcas` (view/edit/delete). A senha do registro
(`senha_enc`) nunca sai na listagem: `has_senha` diz se existe e
GET /{id}/senha (edit) devolve o valor sob demanda, com log de quem pediu —
mesmo desenho do nf_faturador e do store_info. A senha das redes/SAC
(`sac_senha_enc`) tem UM dono: a aba Redes Sociais (routers/redes_sociais.py,
gate redes_sociais:edit) — aqui ela só aparece como `has_sac_senha`. O logo
(bytes no banco) alimenta a assinatura dos e-mails (services/email_marca.py)
e é servido pra quem vê qualquer uma das três abas.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission, require_permission_any
from app.models import Company, Marca, User
from app.schemas.marcas import EmpresaRef, MarcaCreate, MarcaOut, MarcaPatch, SenhaOut
from app.schemas.segments import _slugify
from app.security.cipher import decrypt, encrypt

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marcas", tags=["marcas"])

_NAO_NULOS = ("nome", "slug", "inpi_status", "ativo")

# Logo: ≤ 1 MB no upload, tipo pelos MAGIC BYTES (o content-type do upload é
# do cliente). Só png/jpeg/gif: WebP não abre no Outlook desktop e SVG viraria
# HTML. Depois de reduzido (≤ 480 px de largura, PyMuPDF) tem que caber em
# LOGO_MAX_GUARDADO — ele vai em base64 dentro de cada e-mail/assinatura.
LOGO_MAX_BYTES = 1_048_576
LOGO_MAX_GUARDADO = 300 * 1024
LOGO_MAX_LARGURA = 480
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_view = require_permission("marcas", "view")
_edit = require_permission("marcas", "edit")
_delete = require_permission("marcas", "delete")
# Miniatura do logo aparece nos grids das três abas.
_view_logo = require_permission_any(("marcas", "redes_sociais", "email_padroes"), "view")


def marca_out(m: Marca, razao_social: str | None = None) -> MarcaOut:
    out = MarcaOut.model_validate(m)
    out.has_senha = bool(m.senha_enc)
    out.has_sac_senha = bool(m.sac_senha_enc)
    out.has_logo = bool(m.logo_mime)
    out.empresa_razao_social = razao_social
    return out


async def _razao_social(session: AsyncSession, company_id: UUID | None) -> str | None:
    if company_id is None:
        return None
    return (
        await session.execute(select(Company.razao_social).where(Company.id == company_id))
    ).scalar_one_or_none()


async def _get_or_404(session: AsyncSession, marca_id: UUID) -> Marca:
    m = (await session.execute(select(Marca).where(Marca.id == marca_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return m


async def _company_or_404(session: AsyncSession, company_id: UUID) -> None:
    ok = (
        await session.execute(select(Company.id).where(Company.id == company_id))
    ).scalar_one_or_none()
    if ok is None:
        raise HTTPException(404, detail={"code": "company_not_found"})


async def _slug_em_uso(session: AsyncSession, slug: str, *, exceto: UUID | None = None) -> bool:
    stmt = select(Marca.id).where(Marca.slug == slug)
    if exceto is not None:
        stmt = stmt.where(Marca.id != exceto)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


def _tipo_imagem(raw: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    return None


def _reduzir_logo(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Encolhe o logo pra ≤ LOGO_MAX_LARGURA px (metade a cada passo) e
    reencoda em PNG. Se o PyMuPDF não decodificar (GIF animado etc.), devolve
    o original — o teto LOGO_MAX_GUARDADO ainda vale."""
    try:
        import fitz  # PyMuPDF (já é dependência: certificações em PDF)

        pix = fitz.Pixmap(raw)
        if pix.width <= LOGO_MAX_LARGURA:
            return raw, mime
        while pix.width > LOGO_MAX_LARGURA:
            pix.shrink(1)
        return pix.tobytes("png"), "image/png"
    except Exception:  # noqa: BLE001 — imagem exótica: guarda como veio
        return raw, mime


def _revela(valor_enc: str | None, *, log_evento: str, **log_kw: str) -> SenhaOut:
    if not valor_enc:
        return SenhaOut(senha="")
    try:
        senha = decrypt(valor_enc)
    except Exception as e:
        # Chave rotacionada ou blob corrompido: não deixa virar traceback
        # com bytes no log/Sentry.
        logger.error(f"{log_evento}_decrypt_failed", **log_kw)
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info(log_evento, **log_kw)
    return SenhaOut(senha=senha)


@router.get("", response_model=list[MarcaOut])
async def list_marcas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    search: str | None = Query(None),
    ativo: bool | None = Query(None),
) -> list[MarcaOut]:
    stmt = select(Marca, Company.razao_social).outerjoin(Company, Company.id == Marca.company_id)
    if search:
        like = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                Marca.nome.ilike(like),
                Marca.slug.ilike(like),
                Marca.dominio_br.ilike(like),
                Marca.dominio.ilike(like),
                Marca.classe.ilike(like),
            )
        )
    if ativo is not None:
        stmt = stmt.where(Marca.ativo == ativo)
    rows = (await session.execute(stmt.order_by(Marca.nome))).all()
    return [marca_out(m, razao) for m, razao in rows]


@router.get("/empresas", response_model=list[EmpresaRef])
async def list_empresas_para_marca(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> list[EmpresaRef]:
    """Empresas pro select "Empresa da assinatura" do modal de Marcas — sob
    `marcas:view`, pra não exigir `empresa:view` de quem cadastra marca."""
    rows = (await session.execute(select(Company).order_by(Company.apelido))).scalars().all()
    return [EmpresaRef.model_validate(c) for c in rows]


@router.get("/{marca_id}", response_model=MarcaOut)
async def get_marca(
    marca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> MarcaOut:
    m = await _get_or_404(session, marca_id)
    return marca_out(m, await _razao_social(session, m.company_id))


@router.post("", response_model=MarcaOut, status_code=status.HTTP_201_CREATED)
async def create_marca(
    body: MarcaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaOut:
    slug = body.slug or _slugify(body.nome)
    if not slug:
        raise HTTPException(400, detail={"code": "slug_invalid"})
    if await _slug_em_uso(session, slug):
        raise HTTPException(409, detail={"code": "marca_slug_conflict"})
    if body.company_id is not None:
        await _company_or_404(session, body.company_id)
    data = body.model_dump(exclude={"senha", "slug"})
    m = Marca(slug=slug, senha_enc=encrypt(body.senha) if body.senha else None, **data)
    session.add(m)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": "marca_slug_conflict"}) from e
    await session.refresh(m)
    logger.info("marca_created", marca_id=str(m.id), slug=m.slug)
    return marca_out(m, await _razao_social(session, m.company_id))


@router.patch("/{marca_id}", response_model=MarcaOut)
async def patch_marca(
    marca_id: UUID,
    body: MarcaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaOut:
    m = await _get_or_404(session, marca_id)
    data = body.model_dump(exclude_unset=True)
    if "senha" in data:
        # Ausente = mantém; ""/null = limpa; texto = cifra (schemas/marcas.py).
        pwd = data.pop("senha")
        m.senha_enc = encrypt(pwd) if pwd else None
    for k in _NAO_NULOS:
        # Colunas NOT NULL: null no body = "não mexe" (slug: renomear a marca
        # não troca o slug sozinho — ele é a chave estável pras outras telas).
        if k in data and data[k] is None:
            data.pop(k)
    if "slug" in data and await _slug_em_uso(session, data["slug"], exceto=m.id):
        raise HTTPException(409, detail={"code": "marca_slug_conflict"})
    if data.get("company_id") is not None:
        await _company_or_404(session, data["company_id"])
    for k, v in data.items():
        setattr(m, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": "marca_slug_conflict"}) from e
    await session.refresh(m)
    return marca_out(m, await _razao_social(session, m.company_id))


@router.delete("/{marca_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_marca(
    marca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_delete)],
) -> None:
    m = await _get_or_404(session, marca_id)
    # As redes sociais e os padrões de e-mail da marca vão junto (FK CASCADE).
    await session.delete(m)
    await session.commit()
    logger.info("marca_deleted", marca_id=str(marca_id))
    return None


@router.get("/{marca_id}/senha", response_model=SenhaOut)
async def reveal_marca_senha(
    marca_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> SenhaOut:
    """Senha do registro (aba `marcas`) descriptografada sob demanda (só quem
    tem edit). "" quando não há. Resposta sem cache e com log de quem revelou."""
    m = await _get_or_404(session, marca_id)
    response.headers["Cache-Control"] = "no-store"
    return _revela(
        m.senha_enc, log_evento="marca_senha_revelada", user_id=str(user.id), marca_id=str(marca_id)
    )


# ============================================================================ logo


@router.get("/{marca_id}/logo")
async def get_marca_logo(
    marca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view_logo)],
) -> Response:
    m = (
        await session.execute(
            select(Marca).options(undefer(Marca.logo)).where(Marca.id == marca_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    if not m.logo or not m.logo_mime:
        raise HTTPException(404, detail={"code": "marca_sem_logo"})
    # nosniff + inline: um GIF/PNG "poliglota" nunca vira HTML no navegador.
    return Response(
        content=m.logo,
        media_type=m.logo_mime,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.put("/{marca_id}/logo", response_model=MarcaOut)
async def put_marca_logo(
    marca_id: UUID,
    file: Annotated[UploadFile, File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaOut:
    """Sobe/troca o logo (png/jpeg/webp/gif, ≤ 1 MB). O tipo vem dos magic
    bytes, não do content-type do navegador; SVG não entra (vira HTML)."""
    m = await _get_or_404(session, marca_id)
    raw = await file.read(LOGO_MAX_BYTES + 1)
    if len(raw) > LOGO_MAX_BYTES:
        raise HTTPException(413, detail={"code": "logo_too_large"})
    mime = _tipo_imagem(raw)
    if not raw or mime is None:
        raise HTTPException(415, detail={"code": "logo_tipo_invalido"})
    raw, mime = _reduzir_logo(raw, mime)
    if len(raw) > LOGO_MAX_GUARDADO:
        raise HTTPException(413, detail={"code": "logo_too_large"})
    m.logo = raw
    m.logo_mime = mime
    await session.commit()
    await session.refresh(m)
    logger.info("marca_logo_atualizado", marca_id=str(marca_id), mime=mime, bytes=len(raw))
    return marca_out(m, await _razao_social(session, m.company_id))


@router.delete("/{marca_id}/logo", response_model=MarcaOut)
async def delete_marca_logo(
    marca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaOut:
    m = await _get_or_404(session, marca_id)
    m.logo = None
    m.logo_mime = None
    await session.commit()
    await session.refresh(m)
    logger.info("marca_logo_removido", marca_id=str(marca_id))
    return marca_out(m, await _razao_social(session, m.company_id))
