"""Ask each channel, through our own credentials, whether invoice and label data are reachable.

Run only with the owner's authorization. It reads buyer data in memory on the server and prints
field names with counts; no personal value leaves the server.
"""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path
from uuid import UUID

from app.erp_local.core import LocalERP
from app.erp_local.reference import ReferenceStore

HERE = Path(__file__).parent
PLATFORMS = ["ml", "shopee", "tiktok", "amazon", "magalu", "carrefour"]


def source_for(platform):
    """One file over stdin: the channel's signing helpers, then the presence reader."""
    probe = (HERE / f"{platform}_order_probe.py").read_text("utf-8")
    helpers = probe.split('if __name__ == "__main__":')[0]
    return helpers + "\n" + (HERE / "recipient_presence_probe.py").read_text("utf-8")


def main():
    parser = argparse.ArgumentParser(description="Sondagem de campos, sem valores pessoais")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--accounts", type=int, default=2)
    parser.add_argument("--only", action="append", choices=PLATFORMS)
    parser.add_argument("--integration", action="append", type=UUID, default=[])
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh):
        parser.error("Alias SSH inválido")
    reference = ReferenceStore(LocalERP(args.database))
    rows = reference.all_rows("integrations")
    result = json.loads(args.output.read_text("utf-8")) if args.output.exists() else {}
    for platform in args.only or PLATFORMS:
        chosen = sorted(
            str(UUID(r["id"]))
            for r in rows
            if r["platform"] == platform
            and r["status"] == "active"
            and not r["archived_at"]
            and (not args.integration or UUID(r["id"]) in args.integration)
        )[: args.accounts]
        encoded = base64.b64encode(
            json.dumps({"platform": platform, "integration_ids": chosen}).encode()
        ).decode()
        # Fixed command and validated alias; no credentials cross this boundary.
        reply = subprocess.run(  # noqa: S603
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                args.reference_ssh,
                "docker exec -i davinci-api-1 uv run python - " + encoded,
            ],
            input=source_for(platform).encode("utf-8"),
            capture_output=True,
            timeout=240,
        )
        try:
            result[platform] = json.loads(reply.stdout)
        except ValueError:
            result[platform] = {"error": "no_reply", "code": reply.returncode}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")


if __name__ == "__main__":
    main()
