"""Certificados digitais (.p12/.pfx) por empresa — SOMENTE admin.

Arquivo e senha são guardados cifrados (AES-GCM) na tabela
`company_certificates`; em claro só trafegam no upload/download/reveal.
Todas as rotas exigem `require_admin`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import Company, CompanyCertificate, User
from app.schemas.company_certificates import (
    CertificateOut,
    CertificatePasswordOut,
    CertificatePatch,
)
from app.security.cipher import decrypt_bytes, encrypt_bytes

logger = structlog.get_logger()
router = APIRouter(prefix="/api/companies", tags=["company-certificates"])

MAX_CERT_BYTES = 1 * 1024 * 1024  # 1 MB — certificado A1 tem poucos KB
_ALLOWED_EXT = (".p12", ".pfx")


async def _get_company_or_404(session: AsyncSession, company_id: UUID) -> Company:
    c = (
        await session.execute(select(Company).where(Company.id == company_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    return c


async def _get_cert_or_404(
    session: AsyncSession, company_id: UUID, cert_id: UUID
) -> CompanyCertificate:
    cert = (
        await session.execute(
            select(CompanyCertificate).where(
                CompanyCertificate.id == cert_id,
                CompanyCertificate.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if cert is None:
        raise HTTPException(404, detail={"code": "certificate_not_found"})
    return cert


def _to_out(row: CompanyCertificate, uploader_name: str | None) -> CertificateOut:
    return CertificateOut(
        id=row.id,
        company_id=row.company_id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        label=row.label,
        expires_at=row.expires_at,
        notes=row.notes,
        has_password=row.password_enc is not None,
        uploaded_by=row.uploaded_by,
        uploaded_by_name=uploader_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _parse_expires_at(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_expires_at"}) from e


@router.get("/{company_id}/certificates", response_model=list[CertificateOut])
async def list_certificates(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[CertificateOut]:
    await _get_company_or_404(session, company_id)
    rows = (
        await session.execute(
            select(CompanyCertificate)
            .where(CompanyCertificate.company_id == company_id)
            .order_by(CompanyCertificate.created_at.desc())
        )
    ).scalars().all()
    uploader_ids = {r.uploaded_by for r in rows if r.uploaded_by}
    names: dict[UUID, str] = {}
    if uploader_ids:
        urows = (
            await session.execute(
                select(User.id, User.name, User.email).where(User.id.in_(uploader_ids))
            )
        ).all()
        names = {uid: (name or email) for uid, name, email in urows}
    return [_to_out(r, names.get(r.uploaded_by)) for r in rows]


@router.post(
    "/{company_id}/certificates",
    response_model=CertificateOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_certificate(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
    file: Annotated[UploadFile, File(...)],
    password: Annotated[str | None, Form()] = None,
    label: Annotated[str | None, Form()] = None,
    expires_at: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
) -> CertificateOut:
    await _get_company_or_404(session, company_id)

    fname = (file.filename or "").strip()
    if not fname.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(400, detail={"code": "unsupported_file_type"})

    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "empty_file"})
    if len(raw) > MAX_CERT_BYTES:
        raise HTTPException(413, detail={"code": "file_too_large"})

    exp = _parse_expires_at(expires_at)
    pwd = (password or "").strip()

    cert = CompanyCertificate(
        company_id=company_id,
        filename=fname,
        content_type=file.content_type or "application/x-pkcs12",
        size_bytes=len(raw),
        blob=encrypt_bytes(raw),
        password_enc=encrypt_bytes(pwd.encode()) if pwd else None,
        label=(label or "").strip() or None,
        expires_at=exp,
        notes=(notes or "").strip() or None,
        uploaded_by=admin.id,
    )
    session.add(cert)
    await session.commit()
    await session.refresh(cert)
    logger.info(
        "company_certificate_uploaded",
        cert_id=str(cert.id),
        company_id=str(company_id),
        by=str(admin.id),
        size=len(raw),
    )
    return _to_out(cert, admin.name or admin.email)


@router.get("/{company_id}/certificates/{cert_id}/download")
async def download_certificate(
    company_id: UUID,
    cert_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> StreamingResponse:
    cert = await _get_cert_or_404(session, company_id, cert_id)
    try:
        data = decrypt_bytes(cert.blob)
    except Exception as e:  # noqa: BLE001 - blob corrompido/chave errada
        logger.error("company_certificate_decrypt_failed", cert_id=str(cert_id))
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info(
        "company_certificate_downloaded",
        cert_id=str(cert_id),
        company_id=str(company_id),
        by=str(admin.id),
    )
    disposition = f"attachment; filename*=UTF-8''{quote(cert.filename)}"
    return StreamingResponse(
        iter([data]),
        media_type=cert.content_type or "application/x-pkcs12",
        headers={"Content-Disposition": disposition},
    )


@router.get(
    "/{company_id}/certificates/{cert_id}/password",
    response_model=CertificatePasswordOut,
)
async def reveal_certificate_password(
    company_id: UUID,
    cert_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> CertificatePasswordOut:
    cert = await _get_cert_or_404(session, company_id, cert_id)
    if cert.password_enc is None:
        return CertificatePasswordOut(password=None)
    try:
        pwd = decrypt_bytes(cert.password_enc).decode()
    except Exception as e:  # noqa: BLE001
        logger.error("company_certificate_pwd_decrypt_failed", cert_id=str(cert_id))
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info(
        "company_certificate_password_revealed",
        cert_id=str(cert_id),
        company_id=str(company_id),
        by=str(admin.id),
    )
    return CertificatePasswordOut(password=pwd)


@router.patch(
    "/{company_id}/certificates/{cert_id}", response_model=CertificateOut
)
async def patch_certificate(
    company_id: UUID,
    cert_id: UUID,
    body: CertificatePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> CertificateOut:
    cert = await _get_cert_or_404(session, company_id, cert_id)
    data = body.model_dump(exclude_unset=True)
    if "label" in data:
        cert.label = (data["label"] or "").strip() or None
    if "notes" in data:
        cert.notes = (data["notes"] or "").strip() or None
    if "expires_at" in data:
        cert.expires_at = data["expires_at"]
    if "password" in data:
        pwd = (data["password"] or "").strip()
        cert.password_enc = encrypt_bytes(pwd.encode()) if pwd else None
    await session.commit()
    await session.refresh(cert)
    uploader_name = None
    if cert.uploaded_by:
        u = (
            await session.execute(
                select(User.name, User.email).where(User.id == cert.uploaded_by)
            )
        ).first()
        if u:
            uploader_name = u[0] or u[1]
    return _to_out(cert, uploader_name)


@router.delete(
    "/{company_id}/certificates/{cert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_certificate(
    company_id: UUID,
    cert_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> None:
    cert = await _get_cert_or_404(session, company_id, cert_id)
    await session.delete(cert)
    await session.commit()
    logger.info(
        "company_certificate_deleted",
        cert_id=str(cert_id),
        company_id=str(company_id),
        by=str(admin.id),
    )
    return None
