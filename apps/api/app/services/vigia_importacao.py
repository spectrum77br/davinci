"""Vigia de importação — pedido PAGO no marketplace que não caiu no Bling.

Eduardo (2026-08-27): pedidos que não caem sozinhos em "Pedidos de Venda"
são raros, mas quando acontece a equipe precisa achar o pedido na tela
"Pedidos de lojas virtuais" (canal multi loja) do Bling e importar na mão —
e o risco é ninguém perceber a tempo. A API pública do Bling v3 NÃO expõe
essa tela (verificado no OpenAPI oficial da referência, 162 endpoints,
2026-08-27), então o vigia olha pelo OUTRO lado: busca os pedidos pagos
direto na API do marketplace e confere se cada um já existe no espelho
bling_orders (numeroloja = order_id ou pack_id). Pedido pago há mais de
_TOLERANCIA_MIN sem aparecer no Bling → aviso no Threema (destinatários em
VIGIA_IMPORTACAO_THREEMA_RECIPIENTS; vazio = vigia desligado) pra alguém
importar manualmente.

Fase 1: só Mercado Livre (search_orders pronto no client). Shopee/TikTok/
Amazon precisariam de método de listagem por período nos clients.

Anti-spam (tabela vigia_importacao, migration 0230): 1 aviso por pedido;
re-aviso a cada 24h enquanto o pedido persistir E continuar vindo como
"paid" na busca do ML (se o ML cancelar, ele some da busca e o vigia cala);
resolve sozinho (resolvido_em) quando o pedido aparece no bling_orders —
sem mensagem de "resolvido". BEST-EFFORT: falha numa conta loga e segue
pras outras; falha no Threema loga sem carimbar avisado_em (retenta no
próximo tick). O espelho bling_orders sincroniza a cada ~10min
(bling_orders_safety_net_tick), então a tolerância de 90min nunca acusa
por atraso do próprio espelho.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    StoreInfo,
    VigiaImportacao,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services import threema
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76696769  # ascii "vigi"

# Janela de busca no marketplace: 72h cobre fim de semana/feriado sem varrer
# histórico infinito. Pedido legítimo mais velho que isso já foi avisado
# (re-avisos param quando ele sai da janela).
_JANELA_HORAS = 72
# Pago há menos que isso = ainda dentro do normal da importação automática
# do Bling; não avisa (evita falso alarme).
_TOLERANCIA_MIN = 90
# Re-aviso enquanto persistir (e continuar na busca do ML).
_REAVISO_HORAS = 24
_MAX_PAGINAS = 20
_TZ_BR = ZoneInfo("America/Sao_Paulo")


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _pago_em(order: dict) -> datetime | None:
    """payments[].date_approved mais antigo; fallback date_created."""
    aprovados = [
        d
        for p in (order.get("payments") or [])
        if (d := _parse_dt(p.get("date_approved"))) is not None
    ]
    if aprovados:
        return min(aprovados)
    return _parse_dt(order.get("date_created"))


async def _pedidos_pagos_ml(
    client: MercadoLivreClient, seller_id: str
) -> list[dict]:
    """Pedidos status=paid da janela, paginados. [{numero, pack, pago_em}]."""
    date_to = datetime.now(UTC)
    date_from = date_to - timedelta(hours=_JANELA_HORAS)
    fmt = "%Y-%m-%dT%H:%M:%S.000-00:00"  # formato da doc do ML
    out: list[dict] = []
    offset = 0
    for _ in range(_MAX_PAGINAS):
        data = await client.search_orders(
            seller_id=seller_id,
            date_from=date_from.strftime(fmt),
            date_to=date_to.strftime(fmt),
            limit=50,
            offset=offset,
        )
        results = data.get("results") or []
        for order in results:
            if (order.get("status") or "").lower() != "paid":
                continue
            numero = str(order.get("id") or "").strip()
            if not numero:
                continue
            pack = order.get("pack_id")
            out.append(
                {
                    "numero": numero,
                    "pack": str(pack).strip() if pack else None,
                    "pago_em": _pago_em(order),
                }
            )
        total = int((data.get("paging") or {}).get("total") or 0)
        offset += 50
        if not results or offset >= total:
            break
    return out


async def _contas_ml(session: AsyncSession) -> list[tuple[Integration, str]]:
    """Integrações ML ativas + nome amigável (store_info.account_name;
    fallback integration.name). store_info pode ter mais de uma linha por
    integração — o primeiro nome não-vazio ganha."""
    rows = (
        await session.execute(
            select(Integration, StoreInfo.account_name)
            .outerjoin(StoreInfo, StoreInfo.integration_id == Integration.id)
            .where(Integration.platform == IntegrationPlatform.ML)
            .where(Integration.status == "active")
            .where(Integration.archived_at.is_(None))
        )
    ).all()
    por_id: dict = {}
    for integration, account_name in rows:
        atual = por_id.get(integration.id)
        if atual is None:
            por_id[integration.id] = (integration, account_name or integration.name)
        elif account_name and atual[1] == atual[0].name:
            por_id[integration.id] = (integration, account_name)
    return list(por_id.values())


def _texto_aviso(rows: list[VigiaImportacao]) -> str:
    linhas = []
    for r in sorted(rows, key=lambda x: (x.conta or "", x.numero_loja)):
        pago = (
            r.pago_em.astimezone(_TZ_BR).strftime("%d/%m %H:%M") if r.pago_em else "?"
        )
        conta = f" — {r.conta}" if r.conta else ""
        linhas.append(f"{r.numero_loja}{conta} — pago {pago}")
    plural = "s" if len(linhas) > 1 else ""
    return (
        f"Pedido{plural} pago{plural} NÃO importado{plural} no Bling:\n"
        + "\n".join(linhas)
        + "\n\nImportar manualmente: Bling > Vendas > Pedidos de lojas virtuais"
    )


async def vigia_importacao_run(session: AsyncSession) -> dict:
    """Uma varredura completa. Commit fica com o caller (sweep/one-off)."""
    settings = get_settings()
    recipients = threema.parse_recipients(settings.vigia_importacao_threema_recipients)
    if not recipients:
        return {"skipped": "recipients_vazio"}

    summary = {
        "contas": 0,
        "contas_falha": 0,
        "pedidos_pagos": 0,
        "faltantes": 0,
        "avisados": 0,
        "resolvidos": 0,
    }
    now = datetime.now(UTC)

    # 1) Pedidos pagos da janela, por conta ML.
    vistos: dict[str, dict] = {}  # numero -> {pack, pago_em, conta}
    for integration, conta in await _contas_ml(session):
        summary["contas"] += 1
        creds = decrypt_json(integration.credentials)
        seller_id = str(creds.get("user_id") or "").strip()
        if not seller_id:
            logger.warning(
                "vigia_importacao_sem_seller_id", integration=str(integration.id)
            )
            summary["contas_falha"] += 1
            continue

        async def _persist_refresh(new_creds: dict, _i: Integration = integration) -> None:
            _i.credentials = encrypt_json(new_creds)
            exp = new_creds.get("expires_at") or new_creds.get("token_expires_at")
            if exp:
                try:
                    _i.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
                except (TypeError, ValueError):
                    pass
            await session.flush()

        client = client_for(
            IntegrationPlatform.ML,
            creds,
            on_token_refresh=_persist_refresh,
            integration_id=integration.id,
        )
        try:
            pedidos = await _pedidos_pagos_ml(client, seller_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "vigia_importacao_conta_falhou",
                integration=str(integration.id),
                conta=conta,
                error=str(e)[:300],
            )
            summary["contas_falha"] += 1
            continue
        for p in pedidos:
            vistos[p["numero"]] = {
                "pack": p["pack"],
                "pago_em": p["pago_em"],
                "conta": conta,
            }
    summary["pedidos_pagos"] = len(vistos)

    # 2) O que já existe no espelho do Bling (order_id OU pack_id).
    abertos = (
        (
            await session.execute(
                select(VigiaImportacao).where(VigiaImportacao.resolvido_em.is_(None))
            )
        )
        .scalars()
        .all()
    )
    candidatos: set[str] = set(vistos)
    candidatos |= {v["pack"] for v in vistos.values() if v["pack"]}
    candidatos |= {a.numero_loja for a in abertos}
    candidatos |= {a.pack_id for a in abertos if a.pack_id}
    existentes: set[str] = set()
    ids = list(candidatos)
    for i in range(0, len(ids), 500):
        existentes |= {
            str(n)
            for n in (
                await session.execute(
                    select(BlingOrder.numeroloja).where(
                        BlingOrder.numeroloja.in_(ids[i : i + 500])
                    )
                )
            ).scalars()
        }

    def _no_bling(numero: str, pack: str | None) -> bool:
        return numero in existentes or (pack is not None and pack in existentes)

    # 3) Resolve os abertos que agora apareceram no Bling (sem mensagem).
    por_numero = {a.numero_loja: a for a in abertos}
    for a in abertos:
        if _no_bling(a.numero_loja, a.pack_id):
            a.resolvido_em = now
            summary["resolvidos"] += 1

    # 4) Upsert dos pagos que seguem faltando (só os vistos NESTE run
    #    entram na fila de aviso — quem saiu da busca do ML fica quieto).
    faltantes: list[VigiaImportacao] = []
    for numero, info in vistos.items():
        if _no_bling(numero, info["pack"]):
            continue
        row = por_numero.get(numero)
        if row is None:
            row = (
                await session.execute(
                    select(VigiaImportacao)
                    .where(VigiaImportacao.plataforma == "ml")
                    .where(VigiaImportacao.numero_loja == numero)
                )
            ).scalar_one_or_none()
        if row is None:
            row = VigiaImportacao(
                plataforma="ml",
                conta=info["conta"],
                numero_loja=numero,
                pack_id=info["pack"],
                pago_em=info["pago_em"],
            )
            session.add(row)
        if row.resolvido_em is not None:
            continue  # já deu match no Bling um dia; não reabre
        row.ultima_verificacao = now
        faltantes.append(row)
    summary["faltantes"] = len(faltantes)

    # 5) Aviso Threema: novos fora da tolerância + re-aviso dos persistentes.
    corte_novo = now - timedelta(minutes=_TOLERANCIA_MIN)
    corte_reaviso = now - timedelta(hours=_REAVISO_HORAS)
    avisar = [
        r
        for r in faltantes
        if (r.avisado_em is None and r.pago_em is not None and r.pago_em <= corte_novo)
        or (r.avisado_em is not None and r.avisado_em <= corte_reaviso)
    ]
    if avisar:
        try:
            resultado = await threema.ThreemaClient().send_to_all(
                _texto_aviso(avisar), recipients
            )
            if resultado.get("sent"):
                for r in avisar:
                    r.avisado_em = now
                summary["avisados"] = len(avisar)
            logger.info(
                "vigia_importacao_aviso",
                pedidos=[r.numero_loja for r in avisar],
                **resultado,
            )
        except Exception as e:  # noqa: BLE001
            # Sem carimbo → retenta no próximo tick.
            logger.warning("vigia_importacao_threema_falhou", error=str(e)[:300])

    return summary


async def vigia_importacao_sweep() -> dict:
    """Sweep do cron: sessão e commit próprios, serializado por advisory
    lock transacional — dois workers nunca varrem juntos."""
    async with session_scope() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        return await vigia_importacao_run(session)
