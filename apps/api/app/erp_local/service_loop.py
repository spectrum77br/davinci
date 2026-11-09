"""Reception rounds for every enabled account, on the server. Nothing is written at the stores.

Buyers are kept only where ERP_RECIPIENT_CAPTURE=1. One account failing never stops the others.
"""

import argparse
import logging
import os
import time
from pathlib import Path

from app.erp_local import channel_recipients, native_ingest, recipient_capture
from app.erp_local.core import ERPError, LocalERP

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


def main():
    parser = argparse.ArgumentParser(description="Recepção contínua das lojas")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--every", type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    erp = LocalERP(args.database)
    rounds = 0
    while True:
        if os.environ.get("ERP_READ_TRANSPORT") == "local" and rounds % REFRESH_EVERY == 0:
            # Stores, listings and links change in DaVinci; keep the copy here fresh.
            from app.erp_local.reference import LocalReference, ReferenceStore

            try:
                ReferenceStore(erp, LocalReference()).refresh()
            except ERPError as error:
                log.warning("referência não atualizada: %s", error.code)
        rounds += 1
        for account, result in once(erp).items():
            log.info("%s %s", account, result.get("reception", result).get("state"))
        time.sleep(max(60, args.every))


if __name__ == "__main__":
    main()
