"""Reception rounds for every enabled account, on the server. Nothing is written at the stores.

Buyers are kept only where ERP_RECIPIENT_CAPTURE=1. One account failing never stops the others.
"""

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from app.erp_local import channel_recipients, native_ingest, recipient_capture
from app.erp_local.core import ERPError, LocalERP, dumps

log = logging.getLogger("erp.service")
REFRESH_EVERY = 12


# Stores read every minute, like Bling takes their orders; the others keep the slower pace their
# platforms allow (Amazon's order quota is shared with DaVinci; Magalu goes through the owner's
# Mac).
FAST_CHANNELS = frozenset({"ml", "shopee", "tiktok"})


def archived_stores(db):
    """Stores DaVinci archived or turned off (a banned or closed account): there is nothing to read
    and their tokens no longer work, so they are skipped instead of failing every round."""
    try:
        rows = db.execute("SELECT data FROM reference_rows WHERE kind='integrations'").fetchall()
    except sqlite3.Error:
        return set()
    out = set()
    for row in rows:
        data = json.loads(row[0])
        if data.get("archived_at") or data.get("status") not in (None, "active"):
            out.add(str(data.get("id")))
    return out


def once(erp, *, provider_for=native_ingest.ssh_provider, reader=None, channels=None):
    with erp.connection() as db:
        accounts = db.execute(
            "SELECT channel,store FROM ingest_accounts WHERE enabled=1 ORDER BY channel,store"
        ).fetchall()
        archived = archived_stores(db)
    if channels is not None:
        accounts = [a for a in accounts if a["channel"] in channels]
    copy_reader = None
    if reader is None and os.environ.get(recipient_capture.CAPTURE_ENV) == "1":
        reader = recipient_capture.ssh_reader()
        copy_reader = recipient_capture.bling_copy_reader()
    report = {}
    for row in accounts:
        channel, store = row["channel"], row["store"]
        if store in archived:
            report[f"{channel}:{store}"] = {"state": "archived"}
            continue
        try:
            report[f"{channel}:{store}"] = native_ingest.service_round(
                erp,
                channel,
                store,
                provider_for(channel),
                reader=(copy_reader or reader)
                if channel in recipient_capture.FROM_BLING
                else reader
                if channel in channel_recipients.READERS
                else None,
            )
        except (ERPError, OSError, ValueError) as error:
            report[f"{channel}:{store}"] = {"state": "error", "code": type(error).__name__}
    return report


def daily_check(erp, output):
    """Once a day, the sales read so far side by side with Bling, in counts only."""
    from app.erp_local import parallel_check

    try:
        with erp.connection() as db:
            local = parallel_check.local_sales(
                db, *parallel_check.robot_view(db), at=parallel_check.balance_at(db)
            )
        script = (Path(parallel_check.__file__).parent / "parallel_check_read.py").read_bytes()
        keys, bling = sorted(local), {}
        for start in range(0, len(keys), 500):
            bling.update(native_ingest.run_on_host(script, {"keys": keys[start : start + 500]}))
        output.write_text(dumps(parallel_check.compare(local, bling)) + "\n", "utf-8")
        log.info("conferência gravada em %s", output.name)
    except (ERPError, OSError, ValueError) as error:
        log.warning("conferência não concluída: %s", type(error).__name__)


def stock_daily(erp, output):
    """Once a day, Bling's available balance change against what the sales read explain."""
    from app.erp_local import stock_check

    try:
        with erp.connection() as db:
            result = stock_check.report(db, outside=stock_check.outside_reader())
        output.write_text(dumps(result) + "\n", "utf-8")
        log.info("conferência de estoque gravada em %s", output.name)
    except (ERPError, OSError, ValueError, StopIteration) as error:
        log.warning("conferência de estoque não concluída: %s", type(error).__name__)


def listing_preview(erp, output, reader=None):
    """Once a day, right after the Bling mirror: the stock each listing would get from the ERP."""
    from app.erp_local import stock_publish_preview

    try:
        result = stock_publish_preview.run(erp, reader)
        output.write_text(dumps(result) + "\n", "utf-8")
        log.info(
            "prévia dos anúncios %s",
            {k: result[k] for k in ("anuncios", "iguais", "diferentes", "motivos")},
        )
    except (ERPError, OSError, ValueError) as error:
        log.warning("prévia dos anúncios não concluída: %s", type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description="Recepção contínua das lojas")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--every", type=int, default=300, help="Rodada completa, em segundos")
    parser.add_argument(
        "--orders-every", type=int, default=60, help="Pedidos de ML, Shopee e TikTok, em segundos"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    erp = LocalERP(args.database)
    rounds, checked_on, previewed_on, last_full = 0, None, None, None
    while True:
        full = last_full is None or time.monotonic() - last_full >= max(60, args.every)
        if not full:
            # Between full rounds only the stores whose orders must arrive within minutes.
            for account, result in once(erp, channels=FAST_CHANNELS).items():
                state = result.get("reception", result).get("state")
                if state not in {"complete", "waiting", "archived"}:
                    log.info("%s %s", account, state)
            time.sleep(max(30, args.orders_every))
            continue
        last_full = time.monotonic()
        if os.environ.get("ERP_READ_TRANSPORT") == "local" and rounds % REFRESH_EVERY == 0:
            # Stores, listings and links change in DaVinci; keep the copy here fresh.
            from app.erp_local.reference import LocalReference, ReferenceStore

            try:
                store = ReferenceStore(erp, LocalReference())
                store.refresh()
                from app.erp_local import stock_check

                stock_check.take(erp, store.all_rows("products"))
                from app.erp_local import stock_priority

                log.info(
                    "prioridades %s", stock_priority.sync_rules(erp, store.all_rows("priorities"))
                )
                from app.erp_local import catalog_follow

                log.info(
                    "cadastro %s",
                    catalog_follow.follow(
                        erp, store.all_rows("products"), store.all_rows("components")
                    ),
                )
                log.info(
                    "situação do cadastro %s",
                    catalog_follow.follow_status(erp, store.all_rows("products")),
                )
                from app.erp_local import multi_company

                try:
                    log.info("empresas emissoras %s", multi_company.refresh(erp))
                except ERPError as error:
                    log.warning("empresas emissoras não lidas: %s", error.code)
                from app.erp_local import bling_coverage

                try:
                    log.info("conferência com o Bling %s", bling_coverage.run(erp))
                except (ERPError, OSError, ValueError) as error:
                    log.warning("conferência com o Bling não feita: %s", type(error).__name__)
            except ERPError as error:
                log.warning("referência não atualizada: %s", error.code)
        rounds += 1
        today = time.strftime("%Y-%m-%d")
        if os.environ.get("ERP_READ_TRANSPORT") == "local" and today != checked_on:
            checked_on = today
            from app.erp_local import pii

            try:
                log.info("proteção de dados %s", pii.purge(erp))
            except (ERPError, OSError, ValueError) as error:
                log.warning("proteção de dados não concluída: %s", type(error).__name__)
            daily_check(erp, args.database.parent / f"conferencia-{today}.json")
            stock_daily(erp, args.database.parent / f"conferencia-estoque-{today}.json")
        for account, result in once(erp).items():
            log.info("%s %s", account, result.get("reception", result).get("state"))
        if os.environ.get("ERP_READ_TRANSPORT") == "local":
            from app.erp_local import bling_mirror

            log.info("espelho do Bling %s", bling_mirror.run(erp))
            if today != previewed_on:
                previewed_on = today
                listing_preview(erp, args.database.parent / f"previa-anuncios-{today}.json")
        if os.environ.get(recipient_capture.CAPTURE_ENV) == "1":
            from app.erp_local import ml_shipping, store_labels

            try:
                log.info("envios ML %s", ml_shipping.sync(erp, store_labels.host_reader()))
                log.info(
                    "cupons ML %s", ml_shipping.sync_discounts(erp, store_labels.host_reader())
                )
            except (ERPError, OSError, ValueError) as error:
                log.warning("envios ML não lidos: %s", type(error).__name__)
        time.sleep(max(30, args.orders_every))


if __name__ == "__main__":
    main()
