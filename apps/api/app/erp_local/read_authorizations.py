"""Verify existing native grants on-server; import only classifications locally."""

import argparse
import base64
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from uuid import UUID

from app.erp_local.authorization_probe import ENDPOINTS
from app.erp_local.core import ERPError, LocalERP
from app.erp_local.integration_registry import prepare, record_authorizations
from app.erp_local.reference import ReferenceStore


def main():
    parser = argparse.ArgumentParser(description="Conferir acesso próprio às lojas sem gravações")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--all-active", action="store_true", help="Padrão: uma conta por canal")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh):
        parser.error("Alias SSH inválido")
    erp = LocalERP(args.database)
    reference = ReferenceStore(erp)
    rows = sorted(
        (
            r
            for r in reference.all_rows("integrations")
            if r["platform"] in ENDPOINTS and r["status"] == "active" and not r["archived_at"]
        ),
        key=lambda r: (r["platform"], r["id"]),
    )
    selected, platforms = [], set()
    for row in rows:
        if args.all_active or row["platform"] not in platforms:
            selected.append(str(UUID(row["id"])))
            platforms.add(row["platform"])
    if not selected or len(selected) > 64:
        parser.error("Não há contas elegíveis ou o limite de 64 contas foi excedido")
    encoded = base64.b64encode(json.dumps({"integration_ids": selected}).encode()).decode()
    try:
        result = subprocess.run(  # noqa: S603 - fixed command, validated SSH alias and UUIDs
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                args.reference_ssh,
                "docker exec -i davinci-api-1 uv run python - " + encoded,
            ],
            input=Path(__file__).with_name("authorization_probe.py").read_bytes(),
            capture_output=True,
            timeout=300,
        )
        if result.returncode:
            raise ValueError
        read = json.loads(result.stdout)
        if {a["integration_id"] for a in read["accounts"]} != set(selected):
            raise ValueError
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        raise ERPError(
            "authorization_unavailable", "A conferência não concluiu; nenhum envio foi feito.", 503
        ) from None
    imported = record_authorizations(erp, read)
    prepare(erp, reference)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(read, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                **imported,
                "states": Counter(a["state"] for a in read["accounts"]),
                "channels": Counter(a["platform"] for a in read["accounts"]),
                "no_token_refresh": True,
                "publication_enabled": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
