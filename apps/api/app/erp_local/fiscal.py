"""Own XML archive using the existing pure parser and Comunicador DANFE renderer."""

import hashlib
import json
from pathlib import Path

from app.erp_local.core import ERPError, dumps
from app.services.nf_xml import NfXmlError, parse_nfe

SCHEMA = """
CREATE TABLE IF NOT EXISTS fiscal_xml (
 id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, xml BLOB NOT NULL,
 metadata TEXT NOT NULL, imported_at TEXT NOT NULL);
"""


def apply(erp, db, p):
    # Imported here so the reception service runs without the DANFE renderer.
    from app.services.danfe_render import DanfeInvalida, ler_nota

    if set(p) != {"xml"} or not isinstance(p["xml"], str):
        raise ERPError("invalid_xml", "Envie o XML original da NF-e.")
    raw = p["xml"].encode("utf-8")
    if len(raw) > 8 * 1024 * 1024 or any(m in p["xml"].upper() for m in ("<!DOCTYPE", "<!ENTITY")):
        raise ERPError("invalid_xml", "XML excede o limite ou contém declaração não permitida.")
    try:
        nota, layout = parse_nfe(raw), ler_nota(raw)
    except (DanfeInvalida, NfXmlError):
        raise ERPError("invalid_xml", "Arquivo não é uma NF-e reconhecida pelo gerador.") from None
    digest = hashlib.sha256(raw).hexdigest()
    old = db.execute("SELECT digest FROM fiscal_xml WHERE id=?", (nota.chave,)).fetchone()
    if old:
        if old["digest"] != digest:
            raise ERPError(
                "xml_identity_conflict", "Já há outro XML com essa chave; confira a origem.", 409
            )
        return {"id": nota.chave, "duplicate": True}
    metadata = {
        "id": nota.chave,
        "number": nota.numero,
        "series": nota.serie,
        "issuer": nota.emitente_nome,
        "value": str(nota.valor) if nota.valor is not None else None,
        "environment": "homologation" if layout.tp_amb == "2" else "production_claimed_by_xml",
        "source_status": nota.situacao,
        "has_protocol": bool(nota.protocolo),
        "items": len(layout.itens),
        "signature_verified": False,
        "current_status_verified": False,
        "xml_digest": digest,
    }
    db.execute(
        "INSERT INTO fiscal_xml VALUES (?,?,?,?,?)",
        (nota.chave, digest, raw, dumps(metadata), erp.now()),
    )
    return {"id": nota.chave, "duplicate": False}


def render(erp, identifier):
    from app.services.danfe_render import gerar_pdf

    with erp.connection() as db:
        row = db.execute("SELECT xml FROM fiscal_xml WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("xml_not_found", "XML não encontrado na base própria.", 404)
    return gerar_pdf(bytes(row[0]))


def readiness():
    path = Path(__file__).resolve().parents[4] / "data/erp-local/certificate-verification-read.json"
    if not path.is_file():
        return {"captured_at": None, "certificates": [], "external_emission": False}
    data = json.loads(path.read_text())
    if (
        data.get("source") != "certificate_metadata_probe"
        or data.get("private_material_exported") is not False
    ):
        raise ERPError("invalid_fiscal_metadata", "Projeção fiscal fora do formato esperado.")
    allowed = {
        "id",
        "company_id",
        "company_name",
        "opens",
        "has_private_key",
        "valid_now",
        "company_matches",
        "expires_at",
        "error",
        "has_download_lock",
        "file_kind",
    }
    return {
        "captured_at": data["captured_at"],
        "certificates": [
            {k: v for k, v in r.items() if k in allowed} for r in data["certificates"]
        ],
        "external_emission": False,
        "revocation_verified": False,
        "service_environments": data.get("service_environments", []),
    }


def snapshot(db, metadata=None):
    return {
        "documents": [
            {**json.loads(r["metadata"]), "imported_at": r["imported_at"]}
            for r in db.execute(
                "SELECT metadata,imported_at FROM fiscal_xml ORDER BY imported_at DESC"
            )
        ],
        "readiness": metadata
        or {"captured_at": None, "certificates": [], "external_emission": False},
        "renderer": "shared_with_comunicador",
        "external_emission": False,
    }
