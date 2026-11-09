"""Run a bounded direct ML read without changing the production application."""

import argparse
import base64
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.erp_local.channels import ChannelFlow, timestamp
from app.erp_local.core import ERPError, LocalERP
from app.erp_local.provider_read import import_ml_read
from app.erp_local.reference import ReferenceStore


def main():
    parser = argparse.ArgumentParser(description="Consulta pontual ML → ERP local, sem envios")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--integration", required=True, type=UUID)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--since", help="Início da janela ISO com fuso; padrão: última hora")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh):
        parser.error("Alias SSH inválido")
    erp = LocalERP(args.database)
    reference = ReferenceStore(erp)
    integration = next(
        (row for row in reference.all_rows("integrations") if row["id"] == str(args.integration)),
        None,
    )
    if (
        not integration
        or integration["platform"] != "ml"
        or integration["status"] != "active"
        or integration["archived_at"]
    ):
        raise ERPError(
            "integration_review", "A conta precisa constar como ML ativa na referência local."
        )
    since = timestamp(args.since) if args.since else datetime.now(UTC) - timedelta(hours=1)
    request = {"integration_id": str(args.integration), "date_from": since.isoformat()}
    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    # Arguments are fixed or validated; no credentials or caller-provided code cross this boundary.
    result = subprocess.run(  # noqa: S603 - fixed SSH command and UUID/base64 arguments
        [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            args.reference_ssh,
            "docker exec -i davinci-api-1 uv run python - " + encoded,
        ],
        input=(Path(__file__).parent / "ml_read_once.py").read_bytes(),
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise ERPError(
            "provider_unavailable", "A consulta direta não concluiu; nenhum envio foi feito.", 503
        )
    try:
        read = json.loads(result.stdout)
    except ValueError:
        raise ERPError(
            "provider_unavailable", "A consulta não retornou a projeção esperada.", 503
        ) from None
    ChannelFlow(erp).prepare_routes(reference)
    report = import_ml_read(erp, read)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
