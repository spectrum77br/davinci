"""Run: python -m app.erp_local. Uses neither .env nor production startup."""

import argparse
import json
import os
from pathlib import Path

import uvicorn

from app.erp_local.api import create_app
from app.erp_local.initial_load import bootstrap_missing_prices, bootstrap_real_catalog
from app.erp_local.reference import SSHReference


def main():
    parser = argparse.ArgumentParser(description="DaVinci ERP local, com dados fictícios")
    default = Path(__file__).resolve().parents[4] / "data/erp-local/local.sqlite3"
    parser.add_argument("--database", type=Path, default=default)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--empty", action="store_true", help="Não carregar cenário inicial")
    parser.add_argument("--reference-ssh", help="Alias SSH da origem para conferência em leitura")
    parser.add_argument(
        "--bootstrap-reference", action="store_true", help="Carga inicial real em base nova"
    )
    parser.add_argument(
        "--initial-bling-read", type=Path, help="Arquivo da leitura inicial já coletada"
    )
    args = parser.parse_args()
    source = SSHReference(args.reference_ssh) if args.reference_ssh else None
    if os.environ.get("ERP_READ_TRANSPORT") == "local":
        from app.erp_local.reference import LocalReference

        source = LocalReference()
    application = create_app(args.database, reference_source=source)
    if args.bootstrap_reference:
        from app.erp_local.fiscal import readiness

        application.state.fiscal_readiness = readiness()
    if source and not application.state.reference.info()["snapshot"]:
        try:
            application.state.reference.refresh()
        except Exception:
            if not application.state.reference.info()["snapshot"]:
                raise
    if args.bootstrap_reference and not application.state.erp.snapshot()["catalog_import"]:
        initial_read = (
            json.loads(args.initial_bling_read.read_text()) if args.initial_bling_read else None
        )
        bootstrap_real_catalog(application.state.erp, application.state.reference, initial_read)
    if application.state.erp.snapshot()["catalog_import"]:
        bootstrap_missing_prices(application.state.erp, application.state.reference)
    if not args.empty and not args.bootstrap_reference:
        erp = application.state.erp
        if not erp.snapshot()["products"]:
            erp.command("demo-v1", "demo.seed", {})
    uvicorn.run(application, host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
