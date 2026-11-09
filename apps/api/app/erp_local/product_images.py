"""Own immutable raster assets and versioned product image order.

Files stay local. Upload stages an asset; only saving the product changes its
gallery. Removing a link preserves the original for history and reuse.
"""

import hashlib
import io
import json
import warnings
from pathlib import PurePath
from uuid import UUID, uuid4

from app.erp_local.core import ERPError, dumps, text

MAX_BYTES, MAX_PIXELS = 3_000_000, 40_000_000
FORMATS = {"JPEG": {"jpg", "jpeg"}, "PNG": {"png"}, "WEBP": {"webp"}}
MEDIA = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
ASSET_SELECT = (
    "SELECT id,digest,format,width,height,size,filename,created_at FROM catalog_image_assets"
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_image_assets (
 id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, format TEXT NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL, size INTEGER NOT NULL,
 filename TEXT NOT NULL, body BLOB NOT NULL, created_at TEXT NOT NULL,
 operation_key TEXT REFERENCES events(operation_key) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS product_images (
 sku TEXT PRIMARY KEY REFERENCES products(sku), version INTEGER NOT NULL,
 data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_image_history (
 sku TEXT REFERENCES products(sku), version INTEGER NOT NULL, data TEXT NOT NULL,
 reason TEXT NOT NULL, operation_key TEXT REFERENCES events(operation_key), created_at TEXT,
 PRIMARY KEY(sku,version));
"""
COMMANDS = {"catalog.image.upload", "product.images.save"}


def identifier(raw):
    try:
        result = str(UUID(raw))
        if result != raw:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise ERPError("image_identity", "Identificador da imagem inválido.") from None


def descriptor(row):
    return {**dict(row), "url": "/api/erp-local/catalog/images/" + row["id"]}


def inspect_image(raw, filename):
    # Imported here so the reception service runs without the image library.
    from PIL import Image, UnidentifiedImageError

    if not isinstance(filename, str) or any(ord(c) < 32 for c in filename):
        raise ERPError("image_name", "Nome do arquivo inválido.")
    filename = text(filename, "nome do arquivo", 120)
    if "/" in filename or "\\" in filename or any(ord(c) < 32 for c in filename):
        raise ERPError("image_name", "Informe somente o nome do arquivo.")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ERPError("image_size", "Envie imagem de até 3 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=list(FORMATS)) as image:
                kind, width, height = image.format, image.width, image.height
                if (
                    kind not in FORMATS
                    or PurePath(filename).suffix.lower().lstrip(".") not in FORMATS[kind]
                    or not 1 <= width * height <= MAX_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError
                image.verify()
            with Image.open(io.BytesIO(raw), formats=list(FORMATS)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ERPError(
            "image_invalid", "Confira JPG, PNG ou WebP estático, com até 40 milhões de pixels."
        ) from None
    return {
        "format": kind,
        "width": width,
        "height": height,
        "filename": filename,
        "size": len(raw),
    }


def upload(erp, raw, filename):
    info = inspect_image(raw, filename)
    filename = info["filename"]
    digest = hashlib.sha256(raw).hexdigest()
    operation_key = "catalog-image-" + digest
    with erp.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            old = db.execute(ASSET_SELECT + " WHERE digest=?", (digest,)).fetchone()
            asset_id = old["id"] if old else str(uuid4())
            if not old:
                db.execute(
                    "INSERT INTO catalog_image_assets VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        digest,
                        info["format"],
                        info["width"],
                        info["height"],
                        len(raw),
                        filename,
                        raw,
                        erp.now(),
                        operation_key,
                    ),
                )
            result = erp.execute_in(
                db,
                operation_key,
                "catalog.image.upload",
                {"id": asset_id, "digest": digest},
                actor="envio de imagem local",
            )
            db.commit()
            return {**result, "duplicate": bool(old)}
        except Exception:
            db.rollback()
            raise


def project(db, rows):
    result = []
    for index, row in enumerate(rows):
        asset = db.execute(ASSET_SELECT + " WHERE id=?", (row["id"],)).fetchone()
        if asset:
            result.append({**descriptor(asset), "alt": row["alt"], "primary": index == 0})
    return result


def details(db, sku):
    row = db.execute("SELECT version,data FROM product_images WHERE sku=?", (sku,)).fetchone()
    return (
        {"version": row["version"], "rows": project(db, json.loads(row["data"]))}
        if row
        else {"version": 0, "rows": []}
    )


def snapshot(db):
    assets = {r["id"]: descriptor(r) for r in db.execute(ASSET_SELECT)}
    return {
        r["sku"]: {
            "version": r["version"],
            "rows": [
                {**assets[i["id"]], "alt": i["alt"], "primary": index == 0}
                for index, i in enumerate(json.loads(r["data"]))
                if i["id"] in assets
            ],
        }
        for r in db.execute("SELECT * FROM product_images")
    }


def library(db, query="", offset=0):
    query = text(query, "pesquisa", 120) if query else ""
    total = db.execute(
        "SELECT count(*) FROM catalog_image_assets WHERE instr(lower(filename),lower(?))>0",
        (query,),
    ).fetchone()[0]
    rows = db.execute(
        ASSET_SELECT + " WHERE instr(lower(filename),lower(?))>0 "
        "ORDER BY created_at DESC,id LIMIT 50 OFFSET ?",
        (query, offset),
    ).fetchall()
    return {"items": [descriptor(r) for r in rows], "total": total, "offset": offset, "limit": 50}


def history(db, sku):
    return [
        {**dict(r), "data": project(db, json.loads(r["data"]))}
        for r in db.execute(
            "SELECT version,data,reason,created_at FROM product_image_history "
            "WHERE sku=? ORDER BY version DESC LIMIT 20",
            (sku,),
        )
    ]


def apply(erp, db, operation_key, kind, payload):
    if kind == "catalog.image.upload":
        if not isinstance(payload, dict) or set(payload) != {"id", "digest"}:
            raise ERPError("image_fields", "Dados da imagem não reconhecidos.")
        asset = db.execute(ASSET_SELECT + " WHERE id=?", (identifier(payload["id"]),)).fetchone()
        if not asset or asset["digest"] != payload["digest"]:
            raise ERPError("image_missing", "Envie o arquivo antes de vinculá-lo.")
        return {"image": descriptor(asset), "external_writes": False}
    if not isinstance(payload, dict) or set(payload) != {"sku", "version", "rows", "reason"}:
        raise ERPError("image_fields", "Informe produto, versão, imagens e motivo.")
    sku = erp.product(db, payload["sku"])["sku"]
    current = db.execute("SELECT version FROM product_images WHERE sku=?", (sku,)).fetchone()
    version = current["version"] if current else 0
    if type(payload["version"]) is not int or payload["version"] != version:
        raise ERPError("image_version", "As imagens mudaram. Atualize o cadastro.", 409)
    if not isinstance(payload["rows"], list) or len(payload["rows"]) > 50:
        raise ERPError("image_rows", "Vincule até cinquenta imagens por produto.")
    rows, seen = [], set()
    for row in payload["rows"]:
        if not isinstance(row, dict) or set(row) != {"id", "alt"}:
            raise ERPError("image_fields", "Dados do vínculo de imagem não reconhecidos.")
        asset_id = identifier(row["id"])
        if (
            asset_id in seen
            or not db.execute(
                "SELECT 1 FROM catalog_image_assets WHERE id=?", (asset_id,)
            ).fetchone()
        ):
            raise ERPError("image_missing", "Imagem ausente ou repetida no produto.")
        if not isinstance(row["alt"], str):
            raise ERPError("image_alt", "A descrição da imagem deve ser texto.")
        seen.add(asset_id)
        rows.append(
            {
                "id": asset_id,
                "alt": text(row["alt"], "descrição da imagem", 120) if row["alt"] else "",
            }
        )
    reason = text(payload["reason"], "motivo das imagens", 500)
    db.execute(
        "INSERT INTO product_images VALUES (?,?,?,?) ON CONFLICT(sku) DO UPDATE SET "
        "version=excluded.version,data=excluded.data,updated_at=excluded.updated_at",
        (sku, version + 1, dumps(rows), erp.now()),
    )
    db.execute(
        "INSERT INTO product_image_history VALUES (?,?,?,?,?,?)",
        (sku, version + 1, dumps(rows), reason, operation_key, erp.now()),
    )
    return {"sku": sku, "image_version": version + 1, "external_writes": False}
