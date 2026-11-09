"""Portable catalog collector, executed over stdin in the existing API container.

Only GET requests. Credentials stay in the server's memory and no response body is echoed on
failure. Image links lose their query string so signed access never leaves the server.
"""

import asyncio
import base64
import json
import sys
from datetime import UTC, datetime
from uuid import UUID

FIELDS = [
    "id",
    "codigo",
    "nome",
    "tipo",
    "situacao",
    "formato",
    "unidade",
    "preco",
    "precoCusto",
    "pesoLiquido",
    "pesoBruto",
    "volumes",
    "itensPorCaixa",
    "gtin",
    "gtinEmbalagem",
    "tipoProducao",
    "condicao",
    "freteGratis",
    "marca",
    "descricaoComplementar",
    "descricaoCurta",
    "linkExterno",
    "observacoes",
    "descricaoEmbalagemDiscreta",
    "dataValidade",
    "artigoPerigoso",
    "departamento",
    "categoria",
    "estoque",
    "fornecedor",
    "dimensoes",
    "tributacao",
    "estrutura",
    "variacoes",
]


def project(raw):
    row = {key: raw[key] for key in FIELDS if key in raw}
    media = raw.get("midia") or {}
    images = media.get("imagens") or {}
    row["video_url"] = (media.get("video") or {}).get("url") or ""
    row["image_paths"] = [
        str(item.get("link") or "").split("?", 1)[0]
        for group in ["internas", "imagensURL", "externas"]
        for item in images.get(group) or []
    ]
    return row


async def collect(request):
    import httpx
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.redis_client import redis
    from app.security.cipher import decrypt_json
    from app.services.marketplaces.bling import _acquire_bling_rate_slot

    integration_id = str(UUID(request["integration_id"]))
    ids = request.get("product_ids", [])
    interval = max(1.0, min(10.0, float(request.get("minimum_interval", 2.0))))
    if len(ids) > 200 or any(not isinstance(i, str) or not i.isdigit() for i in ids):
        raise ValueError("Invalid collection scope")
    result = {"captured_at": None, "products": [], "categories": [], "failures": []}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            raw = (
                await session.execute(
                    text(
                        "SELECT credentials FROM davinci.integrations "
                        "WHERE id=:id AND platform='bling'"
                    ),
                    {"id": UUID(integration_id)},
                )
            ).scalar_one()
            token = decrypt_json(raw).get("access_token")
            await session.rollback()
        if not token:
            raise ValueError("No existing access token")
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:

            async def get(path, params=None):
                # The shared queue must be the last wait, or the call leaves outside its turn.
                await asyncio.sleep(interval)
                await _acquire_bling_rate_slot()
                return await client.get(
                    "https://api.bling.com.br/Api/v3" + path,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                    params=params,
                )

            if request.get("categories"):
                for page in range(1, 51):
                    response = await get("/categorias/produtos", {"pagina": page, "limite": 100})
                    if response.status_code != 200:
                        result["failures"].append(
                            {"path": "categories", "status": response.status_code}
                        )
                        break
                    rows = response.json()["data"]
                    result["categories"].extend(
                        {
                            "id": str(r["id"]),
                            "name": r.get("descricao"),
                            "parent_id": str((r.get("categoriaPai") or {}).get("id") or "") or None,
                        }
                        for r in rows
                    )
                    if len(rows) < 100:
                        break
            for identifier in ids:
                response = await get("/produtos/" + identifier)
                if response.status_code in {401, 403, 429}:
                    result["failures"].append(
                        {"id": identifier, "status": response.status_code, "stop": True}
                    )
                    break
                if response.status_code != 200:
                    result["failures"].append({"id": identifier, "status": response.status_code})
                    continue
                result["products"].append(project(response.json()["data"]))
    except Exception as exc:
        # Never print provider bodies, headers, credentials or connection strings.
        result["failures"].append({"code": type(exc).__name__, "stop": True})
    finally:
        result["captured_at"] = datetime.now(UTC).isoformat()
        await engine.dispose()
        await redis.aclose()
    return result


if __name__ == "__main__":
    payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(asyncio.run(collect(payload)), ensure_ascii=False))
