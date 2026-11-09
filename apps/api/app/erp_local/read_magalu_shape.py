"""Confirm types of one Magalu order per own account; no order-value export."""

import argparse
import base64
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from uuid import UUID

from app.erp_local import amazon_order_probe as common
from app.erp_local import magalu_order_shape as probe
from app.erp_local.release import connect


def validate(report, identifiers):
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "version",
            "source",
            "read_only",
            "no_token_refresh",
            "external_business_writes",
            "sample_limit",
            "window",
            "captured_at",
            "accounts",
        }
        or type(report["version"]) is not int
        or report["version"] not in {1, 2}
        or report["source"] != "magalu_order_shape_read"
        or report["read_only"] != "on"
        or report["no_token_refresh"] is not True
        or report["external_business_writes"] is not False
        or type(report["sample_limit"]) is not int
        or report["sample_limit"] != 1
        or report["window"] != "not_requested"
        or not isinstance(report["accounts"], list)
        or len(report["accounts"]) != len(identifiers)
    ):
        raise ValueError("shape_report")
    captured = common.stamp(report["captured_at"])
    seen = set()
    for a in report["accounts"]:
        if (
            not isinstance(a, dict)
            or set(a)
            != {
                "integration_id",
                "platform",
                "credentials_revision",
                "checked_at",
                "state",
                "http_status",
                "sample_count",
                "has_more",
                "order_shape",
            }
            | ({"meta_shape"} if report["version"] == 2 else set())
            or a["platform"] != "magalu"
            or a["state"] not in probe.STATES
            or str(UUID(a["integration_id"])) != a["integration_id"]
            or a["integration_id"] not in identifiers
            or a["integration_id"] in seen
            or common.stamp(a["credentials_revision"]) > common.stamp(a["checked_at"])
            or common.stamp(a["checked_at"]) > captured
        ):
            raise ValueError("shape_report")
        seen.add(a["integration_id"])
        if report["version"] == 2 and a["meta_shape"] is not None:
            probe.validate_shape(a["meta_shape"])
        if (
            type(a["sample_count"]) is not int
            or a["sample_count"] not in {0, 1}
            or a["has_more"] is not None
            and type(a["has_more"]) is not bool
            or a["http_status"] is not None
            and (type(a["http_status"]) is not int or not 100 <= a["http_status"] <= 599)
        ):
            raise ValueError("shape_report")
        if a["state"] == "shape_observed":
            if a["sample_count"] != 1 or a["http_status"] != 200:
                raise ValueError("shape_report")
            probe.validate_shape(a["order_shape"])
        elif (
            a["sample_count"]
            or a["order_shape"] is not None
            or a["state"] == "empty_sample"
            and a["http_status"] != 200
        ):
            raise ValueError("shape_report")


def main():
    parser = argparse.ArgumentParser(
        description="Conferir estrutura Magalu sem copiar valores dos pedidos"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--integration", type=UUID, action="append")
    parser.add_argument("--reference-ssh", default="davinci-prod")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.reference_ssh)
        or args.output.exists()
    ):
        parser.error("Informe um alias válido e um novo arquivo de saída")
    with connect(args.database) as db:
        integrations = [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM reference_rows WHERE kind='integrations'")
        ]
    identifiers = sorted(
        str(UUID(i["id"]))
        for i in integrations
        if i["platform"] == "magalu" and i["status"] == "active" and not i["archived_at"]
    )
    if args.integration:
        selected = [str(i) for i in args.integration]
        if len(set(selected)) != len(selected) or not set(selected) <= set(identifiers):
            parser.error("Selecione somente contas Magalu ativas identificadas na referência")
        identifiers = sorted(selected)
    if not 1 <= len(identifiers) <= 3:
        parser.error("A referência precisa identificar de uma a três contas Magalu ativas")
    encoded = base64.b64encode(json.dumps({"integration_ids": identifiers}).encode()).decode()
    try:
        result = subprocess.run(  # noqa: S603 - fixed SSH command, validated alias and UUIDs
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                args.reference_ssh,
                "docker exec -i davinci-api-1 uv run python - " + encoded,
            ],
            input=Path(probe.__file__).read_bytes(),
            capture_output=True,
            timeout=180,
        )
        if result.returncode:
            raise ValueError
        report = json.loads(result.stdout)
        validate(report, set(identifiers))
    except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.TimeoutExpired):
        raise SystemExit(
            "A conferência de estrutura não concluiu; nenhum dado operacional foi importado."
        ) from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "accounts": len(report["accounts"]),
                "states": dict(Counter(a["state"] for a in report["accounts"])),
                "order_values_exported": False,
                "external_business_writes": False,
            }
        )
    )


if __name__ == "__main__":
    main()
