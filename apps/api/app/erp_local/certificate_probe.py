"""Run INSIDE the existing server. Emits only certificate verification metadata."""

import asyncio
import json
from datetime import UTC, datetime


async def collect():
    from cryptography.hazmat.primitives.serialization import pkcs12
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.security.cipher import decrypt_bytes
    from app.services.nfse.certificado import _cnpj

    now = datetime.now(UTC)
    result = {
        "source": "certificate_metadata_probe",
        "captured_at": now.isoformat(),
        "read_only": None,
        "private_material_exported": False,
        "certificates": [],
        "service_environments": [],
    }
    async with SessionLocal() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result["read_only"] = (await session.execute(text("SHOW transaction_read_only"))).scalar()
        rows = (
            await session.execute(
                text(
                    "SELECT cc.id,cc.company_id,c.apelido,c.cnpj,cc.blob,cc.password_enc "
                    "FROM davinci.company_certificates cc JOIN davinci.companies c "
                    "ON c.id=cc.company_id ORDER BY cc.created_at DESC"
                )
            )
        ).all()
        for row in rows:
            outcome = {
                "id": str(row.id),
                "company_id": str(row.company_id),
                "company_name": row.apelido,
                "opens": False,
                "has_private_key": False,
                "valid_now": False,
                "company_matches": None,
                "expires_at": None,
                "error": None,
                "has_download_lock": row.password_enc is not None,
                "file_kind": None,
            }
            try:
                raw = decrypt_bytes(row.blob)
                password = (
                    None  # Stored password protects DOWNLOAD, not necessarily the PKCS#12 key.
                )
                outcome["file_kind"] = (
                    "der"
                    if raw.startswith(b"\x30")
                    else "zip"
                    if raw.startswith(b"PK")
                    else "pdf"
                    if raw.startswith(b"%PDF")
                    else "other"
                )
                key, cert, chain = pkcs12.load_key_and_certificates(raw, None)
                outcome.update(opens=bool(cert), has_private_key=key is not None)
                if cert:
                    identity = _cnpj(cert)
                    outcome.update(
                        valid_now=cert.not_valid_before_utc <= now <= cert.not_valid_after_utc,
                        expires_at=cert.not_valid_after_utc.isoformat(),
                        company_matches=identity == row.cnpj if identity and row.cnpj else None,
                    )
                del raw, password, key, cert, chain
            except Exception as exc:
                outcome["error"] = (
                    "requires_pfx_password_or_format_review"
                    if isinstance(exc, ValueError)
                    else type(exc).__name__
                )
            result["certificates"].append(outcome)
        settings = (
            await session.execute(
                text(
                    "SELECT company_id,nfeio_ambiente,nfeio_status_fiscal,nfeio_cert_status "
                    "FROM davinci.company_fiscal WHERE nfeio_company_id IS NOT NULL"
                )
            )
        ).all()
        result["service_environments"] = [
            {
                "company_id": str(r.company_id),
                "environment": r.nfeio_ambiente,
                "status": r.nfeio_status_fiscal,
                "certificate_status": r.nfeio_cert_status,
            }
            for r in settings
        ]
        await session.rollback()
    await engine.dispose()
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(collect()), ensure_ascii=False))
