"""Resumable catalog read from the Bling API into a local file. Nothing is written at the origin."""

import argparse
import base64
import json
import re
import subprocess
import time
from pathlib import Path
from uuid import UUID

from app.erp_local import release
from app.erp_local.core import ERPError


def main():
    parser = argparse.ArgumentParser(description="Leitura retomável do cadastro de produtos")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--integration", required=True, type=UUID)
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chunk", type=int, default=60)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh):
        parser.error("Alias SSH inválido")
    if not 1 <= args.chunk <= 200:
        parser.error("Lote entre 1 e 200 produtos")
    db = release.connect(args.database)
    wanted = sorted(
        {
            str(json.loads(row[0]).get("bling_product_id"))
            for row in db.execute("SELECT data FROM catalog_origin")
            if json.loads(row[0]).get("bling_product_id")
        },
        key=int,
    )
    db.close()
    done, have_categories = set(), False
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("kind") == "category":
                have_categories = True
            elif row.get("kind") == "product":
                done.add(str(row["data"]["id"]))
            elif row.get("kind") == "missing":
                done.add(str(row["id"]))
    pending = [i for i in wanted if i not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(
        json.dumps({"wanted": len(wanted), "done": len(done), "pending": len(pending)}), flush=True
    )
    script = (Path(__file__).parent / "bling_catalog_read.py").read_bytes()
    limited = unavailable = 0
    with args.output.open("a", encoding="utf-8") as output:
        while pending or not have_categories:
            ids = pending[: args.chunk]
            request = {
                "integration_id": str(args.integration),
                "product_ids": ids,
                "minimum_interval": args.interval,
                "categories": not have_categories,
            }
            encoded = base64.b64encode(json.dumps(request).encode()).decode()
            # Fixed command and validated alias; no credentials cross this boundary.
            result = subprocess.run(  # noqa: S603
                [
                    "/usr/bin/ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=15",
                    args.reference_ssh,
                    "docker exec -i davinci-api-1 uv run python - " + encoded,
                ],
                input=script,
                capture_output=True,
                timeout=120 + args.chunk * (args.interval + 3) + 300,
            )
            try:
                if result.returncode:
                    raise ValueError
                read = json.loads(result.stdout)
            except ValueError:
                # The container restarts on every deploy; wait for it and retry the same chunk.
                unavailable += 1
                if unavailable > 30:
                    raise ERPError(
                        "provider_unavailable", "A leitura não concluiu; nada foi enviado."
                    ) from None
                print(json.dumps({"unavailable": unavailable}), flush=True)
                time.sleep(90)
                continue
            unavailable = 0
            for row in read["categories"]:
                output.write(
                    json.dumps({"kind": "category", "data": row}, ensure_ascii=False) + "\n"
                )
                have_categories = True
            finished = set()
            for row in read["products"]:
                output.write(
                    json.dumps({"kind": "product", "data": row}, ensure_ascii=False) + "\n"
                )
                finished.add(str(row["id"]))
            for failure in read["failures"]:
                if failure.get("status") == 404:
                    output.write(json.dumps({"kind": "missing", "id": failure["id"]}) + "\n")
                    finished.add(str(failure["id"]))
            output.flush()
            pending = [i for i in pending if i not in finished]
            print(
                json.dumps(
                    {
                        "read": len(read["products"]),
                        "left": len(pending),
                        "failures": read["failures"][:3],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            stop = next((f for f in read["failures"] if f.get("stop")), None)
            if stop and stop.get("status") == 429 and limited < 30:
                # The account quota is shared with DaVinci: step back instead of insisting.
                limited += 1
                time.sleep(120)
                continue
            if stop:
                raise SystemExit("Leitura interrompida pela origem; retome depois sem repetir.")
            limited = 0
            if not read["products"] and not finished and ids:
                raise SystemExit("Nenhum progresso nesta rodada; retome depois.")
            time.sleep(1)


if __name__ == "__main__":
    main()
