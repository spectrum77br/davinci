"""Reception rounds for every enabled account, on the server. Nothing is written at the stores.

Buyers are kept only where ERP_RECIPIENT_CAPTURE=1. One account failing never stops the others.
"""

import argparse
import logging
import os
import time
from pathlib import Path

from app.erp_local import channel_recipients, native_ingest, recipient_capture
from app.erp_local.core import ERPError, LocalERP, dumps

log = logging.getLogger("erp.service")
REFRESH_EVERY = 12


def once(erp, *, provider_for=native_ingest.ssh_provider, reader=None):
    with erp.connection() as db:
        accounts = db.execute(
            "SELECT channel,store FROM ingest_accounts WHERE enabled=1 ORDER BY channel,store"
        ).fetchall()
    if reader is None and os.environ.get(recipient_capture.CAPTURE_ENV) == "1":
        reader = recipient_capture.ssh_reader()
    report = {}
    for row in accounts:
        channel, store = row["channel"], row["store"]
        try:
            report[f"{channel}:{store}"] = native_ingest.service_round(
                erp,
                channel,
                store,
                provider_for(channel),
                reader=reader if channel in channel_recipients.READERS else None,
            )
        except (ERPError, OSError, ValueError) as error:
            report[f"{channel}:{store}"] = {"state": "error", "code": type(error).__name__}
    return report


def daily_check(erp, output):
    """Once a day, the sales read so far side by side with Bling, in counts only."""
    from app.erp_local import parallel_check

    try:
        with erp.connection() as db:
            local = parallel_check.local_sales(db, *parallel_check.robot_view(db))
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
            result = stock_check.report(db)
        output.write_text(dumps(result) + "\n", "utf-8")
        log.info("conferência de estoque gravada em %s", output.name)
    except (ERPError, OSError, ValueError, StopIteration) as error:
        log.warning("conferência de estoque não concluída: %s", type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description="Recepção contínua das lojas")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--every", type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    erp = LocalERP(args.database)
    rounds, checked_on = 0, None
    while True:
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
            except ERPError as error:
                log.warning("referência não atualizada: %s", error.code)
        rounds += 1
        today = time.strftime("%Y-%m-%d")
        if os.environ.get("ERP_READ_TRANSPORT") == "local" and today != checked_on:
            checked_on = today
            daily_check(erp, args.database.parent / f"conferencia-{today}.json")
            stock_daily(erp, args.database.parent / f"conferencia-estoque-{today}.json")
        for account, result in once(erp).items():
            log.info("%s %s", account, result.get("reception", result).get("state"))
        if os.environ.get(recipient_capture.CAPTURE_ENV) == "1":
            from app.erp_local import ml_shipping, store_labels

            try:
                log.info("envios ML %s", ml_shipping.sync(erp, store_labels.host_reader()))
            except (ERPError, OSError, ValueError) as error:
                log.warning("envios ML não lidos: %s", type(error).__name__)
        time.sleep(max(60, args.every))


if __name__ == "__main__":
    main()
