"""Snapshot diário do estoque Bling por local.

Porta web da rotina externa estoque-bling-diario (projeto ClaudeCode). Em vez
de mandar o detalhamento no Threema, gravamos um snapshot por dia em
`valuation_estoque_bling_diario` (consumido pela aba "Estoque Bling" da página
/financeiro/valuation) e atualizamos `valuation.estoque` com o total — pra a
aba "Resumo" da mesma página continuar coerente.

Crawl:
  1. Pagina /produtos (tipo=P, situacao=A) e coleta {bling_id, sku, precoCusto}.
  2. Filtra formato='S' (não-kit), exatamente como a rotina externa.
  3. Busca saldoFisicoTotal via /estoques/saldos em lotes de 50.
  4. Classifica cada SKU pelo sufixo: PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros.
  5. Soma saldo×precoCusto por local e grava o snapshot.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Integration, IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

_SCHEMA = get_settings().database_schema
_SP = ZoneInfo("America/Sao_Paulo")

# Ordem dos locais no snapshot (mesma do PDF/Threema antigo).
LOCAIS = ["PI", "SA", "SP", "RA", "CD", "CI", "US", "Eletro", "Mala", "Outros"]

_SUFIXO_LOCAL = {
    "pi": "PI", "sa": "SA", "sp": "SP", "ra": "RA",
    "cd": "CD", "ci": "CI", "us": "US",
}


def classificar_estoque(codigo: str) -> str:
    """Classifica o SKU pelo sufixo (réplica de routine_estoque_threema.py).

    - prefixo 'U…' → Eletro
    - prefixo 'b…' + sufixo numérico (ex: b001.24) → Mala
    - sufixo pi/sa/sp/ra/cd/ci/us → respectivo local
    - sem ponto: 'b…' → Mala, 'U…' → Eletro, demais → Outros
    """
    base = codigo.split("+", 1)[0]
    if "." not in base:
        if base.lower().startswith("b"):
            return "Mala"
        if base.upper().startswith("U"):
            return "Eletro"
        return "Outros"

    prefixo = base.split(".", 1)[0]
    sufixo = base.rsplit(".", 1)[-1].lower()
    if prefixo.upper().startswith("U"):
        return "Eletro"
    if prefixo.lower().startswith("b") and sufixo.isdigit():
        return "Mala"
    return _SUFIXO_LOCAL.get(sufixo, "Outros")


async def _build_client(session: AsyncSession, integ: Integration) -> BlingClient:
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def _list_produtos_ativos(client: BlingClient) -> list[dict]:
    """Pagina /produtos?tipo=P&situacao=A — listagem traz o precoCusto.

    O BlingClient.list_products_page passa só `pagina`/`limite`; usamos
    `_request` direto pra adicionar os filtros de tipo/situação (réplica
    da rotina externa: ~3800 produtos viram ~3800 ativos, evita custo de
    iterar inativos)."""
    produtos: list[dict] = []
    page = 1
    while True:
        r = await client._request(  # noqa: SLF001
            "GET",
            "/produtos",
            params={"pagina": page, "limite": 100, "tipo": "P", "situacao": "A"},
        )
        r.raise_for_status()
        batch = (r.json() or {}).get("data") or []
        if not batch:
            break
        produtos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return produtos


async def _saldos_fisicos(client: BlingClient, bling_ids: list[int]) -> dict[int, int]:
    """GET /estoques/saldos em lotes de 50, devolve {bling_id: saldoFisicoTotal}."""
    out: dict[int, int] = {}
    for i in range(0, len(bling_ids), 50):
        chunk = bling_ids[i : i + 50]
        params: list[tuple[str, str]] = [("idsProdutos[]", str(b)) for b in chunk]
        r = await client._request("GET", "/estoques/saldos", params=params)  # noqa: SLF001
        r.raise_for_status()
        for row in (r.json() or {}).get("data") or []:
            try:
                bid = int((row.get("produto") or {}).get("id") or 0)
            except (TypeError, ValueError):
                continue
            if not bid:
                continue
            depositos = row.get("depositos") or []
            if depositos:
                fisico = sum(int(d.get("saldoFisico") or 0) for d in depositos)
            else:
                raw = row.get("saldoFisicoTotal")
                fisico = int(float(raw)) if raw is not None else 0
            out[bid] = fisico
    return out


async def run_valuation_estoque_snapshot(session: AsyncSession) -> dict:
    """Grava o snapshot de hoje (SP) em valuation_estoque_bling_diario e
    atualiza valuation.estoque com o total. Idempotente (UPSERT por data)."""
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        logger.warning("valuation_estoque_no_integration")
        return {"status": "no_integration"}

    client = await _build_client(session, integ)

    produtos = await _list_produtos_ativos(client)
    simples = [p for p in produtos if p.get("formato") == "S"]
    bling_ids = [int(p["id"]) for p in simples if p.get("id")]
    saldos = await _saldos_fisicos(client, bling_ids)

    por_local: dict[str, dict[str, float]] = {
        l: {"qtd": 0, "valor": 0.0} for l in LOCAIS
    }
    total_qtd = 0
    total_valor = 0.0
    for p in simples:
        try:
            bid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            continue
        saldo = saldos.get(bid, 0)
        if saldo <= 0:
            continue
        custo = float(p.get("precoCusto") or 0)
        local = classificar_estoque(str(p.get("codigo") or ""))
        valor = saldo * custo
        por_local[local]["qtd"] += saldo
        por_local[local]["valor"] += valor
        total_qtd += saldo
        total_valor += valor

    # Float → arredondado p/ Numeric(16,2) sem ruído de ponto flutuante.
    por_local_json: dict[str, dict] = {
        l: {"qtd": int(v["qtd"]), "valor": round(v["valor"], 2)}
        for l, v in por_local.items()
        if v["qtd"] > 0 or v["valor"] > 0
    }
    total_valor_r = round(total_valor, 2)
    hoje = datetime.now(_SP).date()

    await session.execute(
        text(f"""
            INSERT INTO {_SCHEMA}.valuation_estoque_bling_diario
                (data, total_qtd, total_valor, por_local, updated_at)
            VALUES (:data, :qtd, :valor, CAST(:porl AS jsonb), now())
            ON CONFLICT (data) DO UPDATE
            SET total_qtd = EXCLUDED.total_qtd,
                total_valor = EXCLUDED.total_valor,
                por_local = EXCLUDED.por_local,
                updated_at = now()
        """),
        {
            "data": hoje,
            "qtd": total_qtd,
            "valor": Decimal(str(total_valor_r)),
            "porl": _jsonb_dumps(por_local_json),
        },
    )

    # Mantém valuation.estoque coerente: a aba Resumo lê dele. Upsert por data.
    existing = (
        await session.execute(
            text(f"SELECT id FROM {_SCHEMA}.valuation WHERE data = :d"),
            {"d": hoje},
        )
    ).scalar_one_or_none()
    if existing is None:
        await session.execute(
            text(f"INSERT INTO {_SCHEMA}.valuation (data, estoque) VALUES (:d, :e)"),
            {"d": hoje, "e": Decimal(str(total_valor_r))},
        )
    else:
        await session.execute(
            text(f"UPDATE {_SCHEMA}.valuation SET estoque = :e WHERE data = :d"),
            {"d": hoje, "e": Decimal(str(total_valor_r))},
        )

    await session.commit()

    summary = {
        "status": "ok",
        "data": hoje.isoformat(),
        "produtos_simples": len(simples),
        "total_qtd": total_qtd,
        "total_valor": total_valor_r,
        "locais": {l: int(v["qtd"]) for l, v in por_local_json.items()},
    }
    logger.info("valuation_estoque_snapshot_done", **summary)
    return summary


def _jsonb_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj)
