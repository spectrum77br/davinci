"""Conciliação — fatura do Mercado Livre por período (detalhes de faturamento).

Sondagem de 14/10/2026 (`docs/conciliacao-avaliacao.md` §13): o período de
faturamento da loja vai do dia 23 ao 22, a chave é sempre o primeiro dia do
mês; cada cobrança diz o pedido, o envio e se foi descontada da venda; as
bonificações (tarifa devolvida quando a venda cai) vêm com `detail_type=BONUS`;
a API aceita **5 chamadas por minuto** — por isso a leitura dorme entre uma
página e outra e lê de forma incremental (`from_id` = último id lido).

Sub-tipos vistos (MLB): CVVML custo por vender · CVVPRC custo por cobrar no
Mercado Pago · CVVFN taxa de parcelamento · CDSB tarifa do Mercado Envios ·
PADS Product Ads (sem pedido) · BVVML/BVVPRC/BVVFN/BDSB = cancelamentos
(bonificações) das quatro primeiras.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync
from app.models.conciliacao import ORIGEM_FATURA
from app.services.conciliacao_mp_csv import dinheiro

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")

SUBTIPOS = {
    "CVVML": "fatura_tarifa_venda",
    "CV": "fatura_tarifa_venda",
    "CVVPRC": "fatura_tarifa_cobranca",
    "CVVFN": "fatura_parcelamento",
    "CDSB": "fatura_frete",
    "CXD": "fatura_frete",
    "PADS": "fatura_publicidade",
    "BVVML": "fatura_bonificacao",
    "BV": "fatura_bonificacao",
    "BVVPRC": "fatura_bonificacao",
    "BVVFN": "fatura_bonificacao",
    "BDSB": "fatura_bonificacao",
    "BXD": "fatura_bonificacao",
}
PAUSA_PADRAO = 15.0  # s entre chamadas: 5/min é o limite (e a Margem divide o balde)
LIMITE_PAGINA = 1000


def chave_periodo(d: date) -> str:
    return d.strftime("%Y-%m-01")


def periodo_anterior(chave: str) -> str:
    d = date.fromisoformat(chave)
    if d.month == 1:
        return d.replace(year=d.year - 1, month=12).strftime("%Y-%m-01")
    return d.replace(month=d.month - 1).strftime("%Y-%m-01")


def tipo_do_detalhe(detail_type: str | None, sub_type: str | None) -> str:
    st = (sub_type or "").upper()
    if st in SUBTIPOS:
        return SUBTIPOS[st]
    if (detail_type or "").upper() == "BONUS" or st.startswith("B"):
        return "fatura_bonificacao"
    return "fatura_outro"


def _lista(v: Any) -> list[dict]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).strip())
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=BRT)


def detalhe_para_movimento(det: dict[str, Any]) -> dict[str, Any] | None:
    ci = det.get("charge_info") or {}
    if not isinstance(ci, dict) or ci.get("detail_id") is None:
        return None
    data = _dt(ci.get("creation_date_time"))
    if data is None:
        return None
    valor = dinheiro(ci.get("detail_amount"))
    detail_type = str(ci.get("detail_type") or "").upper()
    bonus = detail_type == "BONUS"
    vendas = _lista(det.get("sales_info"))
    venda = vendas[0] if vendas else {}
    envio = det.get("shipping_info") if isinstance(det.get("shipping_info"), dict) else {}
    itens = _lista(det.get("items_info"))
    item = itens[0] if itens else {}
    sub = str(ci.get("detail_sub_type") or "") or None
    tipo = tipo_do_detalhe(detail_type, sub)
    debited = str(ci.get("debited_from_operation") or "").upper()
    return {
        "origem": ORIGEM_FATURA,
        "id_externo": str(ci["detail_id"]),
        "data": data,
        "tipo": tipo,
        "descricao": ci.get("transaction_detail"),
        "sub_tipo": sub,
        "pagamento_id": str(venda["operation_id"]) if venda.get("operation_id") else None,
        "pedido_marketplace": str(venda["order_id"]) if venda.get("order_id") else None,
        "pack_id": str(envio["pack_id"]) if envio.get("pack_id") else None,
        "shipping_id": str(envio["shipping_id"]) if envio.get("shipping_id") else None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": dinheiro(venda.get("transaction_amount")) if venda else None,
        "tarifa": valor if tipo in ("fatura_tarifa_venda", "fatura_tarifa_cobranca") else None,
        "parcelamento": valor if tipo == "fatura_parcelamento" else None,
        "frete": valor if tipo == "fatura_frete" else None,
        "cupom": None,
        "credito": valor if bonus else dinheiro(0),
        "debito": dinheiro(0) if bonus else valor,
        "saldo_apos": None,
        "descontado_da_venda": True if debited == "YES" else (False if debited == "NO" else None),
        "sku": None,
        "item_id": str(item["item_id"]) if item.get("item_id") else None,
        "data_aprovacao": _dt(venda.get("sale_date_time")) if venda else None,
        "classificacao": "publicidade" if tipo == "fatura_publicidade" else None,
        "raw": det,
    }


ESPERA_429 = 65.0  # s: o balde de 5/min do ML enche de novo em um minuto
TENTATIVAS_429 = 4


async def ler_pagina(
    cliente: Any,
    chave: str,
    detail_type: str,
    from_id: int,
    limite: int = LIMITE_PAGINA,
    *,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Uma página de detalhes. Em 429 (o balde de 5/min é compartilhado com a
    Margem, que lê a fatura pedido a pedido) espera um minuto e tenta de novo,
    até `TENTATIVAS_429` vezes — a rodada é diária, pode demorar."""
    for tentativa in range(TENTATIVAS_429):
        r = await cliente._request(
            "GET",
            f"/billing/integration/periods/key/{chave}/group/ML/details",
            params={
                "document_type": "BILL",
                "detail_type": detail_type,
                "limit": limite,
                "from_id": from_id,
                "sort_by": "ID",
                "order_by": "ASC",
            },
        )
        if r.status_code == 429 and tentativa < TENTATIVAS_429 - 1:
            logger.info(
                "conciliacao_fatura_429_espera",
                periodo=chave,
                tipo=detail_type,
                tentativa=tentativa + 1,
            )
            await dormir(ESPERA_429)
            continue
        if r.status_code != 200:
            raise RuntimeError(
                f"fatura {chave} {detail_type} from_id={from_id}: "
                f"HTTP {r.status_code} {r.text[:200]}"
            )
        return r.json() or {}
    raise RuntimeError(f"fatura {chave} {detail_type}: 429 depois de {TENTATIVAS_429} tentativas")


async def ler_resumo(cliente: Any, chave: str) -> dict:
    r = await cliente._request(
        "GET",
        f"/billing/integration/periods/key/{chave}/summary/details",
        params={"group": "ML", "document_type": "BILL"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"resumo {chave}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() or {}


async def _sync_row(session: AsyncSession, loja: ConciliacaoLoja, chave: str) -> ConciliacaoSync:
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_FATURA,
                ConciliacaoSync.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_FATURA,
            chave=chave,
            status="lido",
            detalhe={},
        )
        session.add(row)
        await session.flush()
    return row


async def _inserir(
    session: AsyncSession, loja: ConciliacaoLoja, movimentos: list[dict[str, Any]]
) -> int:
    if not movimentos:
        return 0
    for m in movimentos:
        m["integration_id"] = loja.integration_id
        m["platform"] = loja.platform
    inseridos = 0
    for k in range(0, len(movimentos), 200):
        stmt = (
            pg_insert(ConciliacaoMovimento)
            .values(movimentos[k : k + 200])
            .on_conflict_do_nothing(constraint="uq_conciliacao_mov_externo")
            .returning(ConciliacaoMovimento.id)
        )
        inseridos += len((await session.execute(stmt)).all())
    return inseridos


async def run_fatura_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    hoje: date | None = None,
    pausa: float = PAUSA_PADRAO,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    limite: int = LIMITE_PAGINA,
    max_paginas: int = 6,
) -> dict[str, Any]:
    """Lê, de forma incremental, as cobranças e bonificações do período aberto e
    do anterior, mais o resumo do período aberto. Dorme `pausa` s entre as
    chamadas (limite de 5/min do ML)."""
    agora = datetime.now(UTC)
    hoje = hoje or agora.astimezone(BRT).date()
    atual = chave_periodo(hoje)
    chaves = [atual, periodo_anterior(atual)]
    resumo: dict[str, Any] = {
        "loja": loja.conta,
        "periodos": {},
        "chamadas": 0,
        "inseridos": 0,
        "erros": [],
    }
    primeira = True

    async def _espera() -> None:
        nonlocal primeira
        if primeira:
            primeira = False
            return
        await dormir(pausa)

    for chave in chaves:
        for detail_type in ("CHARGE", "BONUS"):
            row = await _sync_row(session, loja, f"{chave}|{detail_type}")
            from_id = int((row.detalhe or {}).get("last_id") or 0)
            lidos = inseridos = paginas = 0
            try:
                while paginas < max_paginas:
                    await _espera()
                    pagina = await ler_pagina(
                        cliente, chave, detail_type, from_id, limite, dormir=dormir
                    )
                    resumo["chamadas"] += 1
                    paginas += 1
                    results = [x for x in (pagina.get("results") or []) if isinstance(x, dict)]
                    movimentos = [m for m in (detalhe_para_movimento(d) for d in results) if m]
                    inseridos += await _inserir(session, loja, movimentos)
                    lidos += len(results)
                    ids = [int(m["id_externo"]) for m in movimentos]
                    ultimo = int(pagina.get("last_id") or 0) or (max(ids) if ids else from_id)
                    if ultimo > from_id:
                        from_id = ultimo
                    if len(results) < limite:
                        break
            except Exception as e:  # noqa: BLE001
                row.erro = f"{type(e).__name__}: {str(e)[:300]}"
                resumo["erros"].append({"periodo": chave, "tipo": detail_type, "erro": row.erro})
                logger.warning(
                    "conciliacao_fatura_falhou",
                    loja=loja.conta,
                    periodo=chave,
                    tipo=detail_type,
                    erro=row.erro,
                )
            else:
                row.erro = None
            row.lido_em = agora
            row.linhas = (row.linhas or 0) + lidos
            row.detalhe = {**(row.detalhe or {}), "last_id": from_id}
            resumo["inseridos"] += inseridos
            resumo["periodos"][f"{chave}|{detail_type}"] = {
                "lidos": lidos,
                "inseridos": inseridos,
                "last_id": from_id,
            }
        if chave == atual:
            try:
                await _espera()
                r = await ler_resumo(cliente, chave)
                resumo["chamadas"] += 1
                row = await _sync_row(session, loja, f"{chave}|resumo")
                row.lido_em = agora
                row.detalhe = {"resumo": r}
                row.erro = None
                resumo["periodos"][f"{chave}|resumo"] = {"ok": True}
            except Exception as e:  # noqa: BLE001
                resumo["erros"].append(
                    {
                        "periodo": chave,
                        "tipo": "resumo",
                        "erro": f"{type(e).__name__}: {str(e)[:300]}",
                    }
                )
    loja.ultimo_fatura_em = agora
    await session.flush()
    return resumo
