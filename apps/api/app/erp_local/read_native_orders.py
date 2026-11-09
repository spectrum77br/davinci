"""Bounded native orders reads; credentials stay on the API host."""

import argparse
import base64
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from uuid import UUID

from app.erp_local.channels import ChannelFlow
from app.erp_local.core import ERPError, LocalERP
from app.erp_local.integration_registry import prepare
from app.erp_local.native_orders import import_read, validate
from app.erp_local.reference import ReferenceStore


def main():
    parser = argparse.ArgumentParser(description="Ler pedidos nativos sem alterar lojas")
    parser.add_argument(
        "--platform",
        choices=["amazon", "shopee", "tiktok", "carrefour", "magalu", "ml"],
        default="amazon",
    )
    parser.add_argument(
        "--integration", type=UUID, action="append", help="Somente estas contas próprias"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh):
        parser.error("Alias SSH inválido")
    if args.output.exists():
        parser.error("Escolha um novo arquivo para preservar a leitura anterior")
    erp = LocalERP(args.database)
    reference = ReferenceStore(erp)
    identifiers = sorted(
        str(UUID(row["id"]))
        for row in reference.all_rows("integrations")
        if row["platform"] == args.platform and row["status"] == "active" and not row["archived_at"]
    )
    if args.integration:
        selected = [str(i) for i in args.integration]
        if len(set(selected)) != len(selected) or not set(selected) <= set(identifiers):
            parser.error("As contas selecionadas precisam constar como ativas neste canal")
        identifiers = sorted(selected)
    if (
        not 1
        <= len(identifiers)
        <= {"shopee": 16, "amazon": 4, "tiktok": 8, "carrefour": 2, "magalu": 3, "ml": 24}[
            args.platform
        ]
    ):
        parser.error("Quantidade de contas fora do limite de leitura deste canal")
    encoded = base64.b64encode(json.dumps({"integration_ids": identifiers}).encode()).decode()
    try:
        result = subprocess.run(  # noqa: S603 - fixed command, validated alias/UUIDs
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                args.reference_ssh,
                "docker exec -i davinci-api-1 uv run python - " + encoded,
            ],
            input=Path(__file__).with_name(args.platform + "_order_probe.py").read_bytes(),
            capture_output=True,
            timeout=300,
        )
        if result.returncode:
            raise ValueError
        read = json.loads(result.stdout)
        validate(read)
        if {a["integration_id"] for a in read["accounts"]} != set(identifiers):
            raise ValueError
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        raise ERPError(
            "native_read_unavailable", "A leitura não concluiu. Nenhum envio foi feito.", 503
        ) from None
    prepare(erp, reference)
    ChannelFlow(erp).prepare_routes(reference)
    imported = import_read(erp, read)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(read, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                **imported,
                "states": Counter(a["state"] for a in read["accounts"]),
                "failure_stages": Counter(
                    a.get("failure_stage") for a in read["accounts"] if a.get("failure_stage")
                ),
                "orders_observed": sum(len(a["orders"]) for a in read["accounts"]),
                "accounts_with_more_pages": sum(a["has_more"] is True for a in read["accounts"]),
                "production_activation": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
