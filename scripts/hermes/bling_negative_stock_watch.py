#!/usr/bin/env python3
"""Watch Bling stock negatives for active low-virtual-stock products.

- Reads active products whose saldo_virtual_total <= threshold from davinci.products.
- Reads/decrypts the Bling Geral token; never refreshes or updates it.
- Calls GET /Api/v3/estoques/saldos by idsProdutos[] in batches.
- Sends Threema alert to configured recipients if the negative set changed.
- Silent when no negatives or no change (for Hermes no_agent cron).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import requests

# Ensure repo helpers are importable under cron.
sys.path.insert(0, "/workspace")

HERMES_ENV = pathlib.Path("/root/.hermes/.env")
STATE_DIR = pathlib.Path("/root/.hermes/state")
STATE_FILE = STATE_DIR / "bling_negative_stock_watch.json"
BLING_BASE = "https://api.bling.com.br/Api/v3"
THREEMA_SEND = pathlib.Path("/workspace/conciliacao/scripts/threema_send.py")
THREEMA_ESTOQUE_ENV = pathlib.Path("/root/.hermes/threema-estoque.env")
DEFAULT_RECIPIENTS = ["USVBYM53", "9BH6R7HJ", "444UXUXN", "7KMPCBS5"]


def load_env_file(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def db_connect():
    import psycopg2

    # Cron/container defaults. Env wins when provided.
    attempts = []
    host = os.environ.get("DAVINCI_HOST") or os.environ.get("PGHOST") or os.environ.get("DB_HOST")
    port = os.environ.get("DAVINCI_PORT") or os.environ.get("PGPORT") or os.environ.get("DB_PORT")
    if host and port:
        attempts.append((host, int(port)))
    for hp in [("pg18", 5432), ("127.0.0.1", 15432), ("127.0.0.1", 5432)]:
        if hp not in attempts:
            attempts.append(hp)

    user = (
        os.environ.get("DAVINCI_USER")
        or os.environ.get("PGUSER")
        or os.environ.get("DB_USER")
        or "davinci"
    )
    password = (
        os.environ.get("DAVINCI_PASSWORD")
        or os.environ.get("PGPASSWORD")
        or os.environ.get("DB_PASS")
    )
    dbname = (
        os.environ.get("DAVINCI_DB")
        or os.environ.get("PGDATABASE")
        or os.environ.get("DB_NAME")
        or "davinci"
    )
    schema = os.environ.get("DAVINCI_SCHEMA", "davinci")
    last = None
    for h, p in attempts:
        try:
            conn = psycopg2.connect(
                host=h,
                port=p,
                user=user,
                password=password,
                dbname=dbname,
                options=f"-c search_path={schema},public",
            )
            conn.autocommit = True
            return conn
        except Exception as e:  # pragma: no cover - diagnostic path
            last = e
    raise RuntimeError(f"DB connect failed: {last}")


def get_bling_access_token(conn) -> str:
    from davinci_tokens import decrypt_json

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT credentials
            FROM davinci.integrations
            WHERE platform = 'bling' AND name = 'Bling Geral'
            ORDER BY created_at
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError("Bling Geral não encontrado em davinci.integrations")
    creds = decrypt_json(bytes(row[0]))
    token = creds.get("access_token")
    if not token:
        raise RuntimeError("Bling Geral sem access_token")
    return token


def load_candidates(conn, threshold: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bling_product_id, sku, name, saldo_virtual_total
            FROM davinci.products
            WHERE bling_product_id IS NOT NULL
              AND situacao = 'A'
              AND formato = 'S'
              AND COALESCE(saldo_virtual_total, 0) <= %s
            ORDER BY saldo_virtual_total NULLS FIRST, sku
            """,
            (threshold,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": int(r[0]),
            "sku": r[1] or "",
            "name": r[2] or "",
            "saldo_db": int(r[3] or 0),
        }
        for r in rows
    ]


def bling_stock_saldos(token: str, ids: list[int], batch_size: int = 50) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        params = [("idsProdutos[]", str(x)) for x in chunk]
        resp = None
        for attempt in range(6):
            resp = requests.get(
                f"{BLING_BASE}/estoques/saldos", headers=headers, params=params, timeout=45
            )
            if resp.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            if resp.status_code >= 500:
                time.sleep(2 + attempt * 2)
                continue
            break
        assert resp is not None
        if resp.status_code == 401:
            # Do not refresh/update DB token here. DaVinci owns this token.
            raise RuntimeError("Bling retornou 401 usando token do DB; refresh não tentado")
        if resp.status_code != 200:
            raise RuntimeError(f"Bling /estoques/saldos HTTP {resp.status_code}: {resp.text[:500]}")
        out.extend((resp.json() or {}).get("data") or [])
        time.sleep(0.45)
    return out


def find_negatives(
    stocks: list[dict[str, Any]], meta: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    neg: list[dict[str, Any]] = []
    for item in stocks:
        prod = item.get("produto") or {}
        pid = int(prod.get("id") or 0)
        try:
            sv = float(item.get("saldoVirtualTotal") or 0)
        except Exception:
            sv = 0.0
        if sv < 0:
            m = meta.get(pid, {})
            neg.append(
                {
                    "id": pid,
                    "sku": prod.get("codigo") or m.get("sku") or "",
                    "nome": m.get("name") or "",
                    "saldo_db": m.get("saldo_db"),
                    "saldo_fisico_bling": item.get("saldoFisicoTotal"),
                    "saldo_virtual_bling": sv,
                    "depositos": item.get("depositos") or [],
                }
            )
    neg.sort(key=lambda x: (float(x["saldo_virtual_bling"]), x["sku"]))
    return neg


def fingerprint(neg: list[dict[str, Any]]) -> str:
    payload = [(x["id"], x["sku"], x["saldo_virtual_bling"], x["saldo_fisico_bling"]) for x in neg]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(fp: str, neg_count: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "fingerprint": fp,
                "neg_count": neg_count,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_message(neg: list[dict[str, Any]], candidates: int, returned: int) -> str:
    lines = [
        f"⚠️ Bling: {len(neg)} produto(s) com estoque virtual negativo",
        "",
    ]
    for x in neg[:15]:
        lines.append(
            f"• {x['sku']}: virtual {x['saldo_virtual_bling']} | físico {x['saldo_fisico_bling']}"
        )
        if x.get("nome"):
            lines.append(f"  {x['nome'][:70]}")
    if len(neg) > 15:
        lines.append(f"... +{len(neg) - 15} itens")
    return "\n".join(lines)


def estoque_sender_env() -> dict[str, str]:
    """Use only the dedicated Basic stock credentials, never the Hermes globals."""
    required = {"THREEMA_ESTOQUE_GATEWAY_ID", "THREEMA_ESTOQUE_GATEWAY_SECRET"}
    values: dict[str, str] = {}
    try:
        for raw in THREEMA_ESTOQUE_ENV.read_text().splitlines():
            key, separator, value = raw.strip().partition("=")
            if not separator or key not in required:
                continue
            if key in values:
                raise ValueError("duplicate key")
            # The installer writes JSON-quoted dotenv values, without interpolation.
            decoded = json.loads(value)
            if not isinstance(decoded, str) or not decoded.strip():
                raise ValueError("invalid value")
            values[key] = decoded
    except (OSError, ValueError):
        raise RuntimeError("Perfil Threema de Estoque ausente ou inválido") from None

    sender = values.get("THREEMA_ESTOQUE_GATEWAY_ID", "")
    secret = values.get("THREEMA_ESTOQUE_GATEWAY_SECRET", "")
    if not re.fullmatch(r"\*[A-Z0-9]{7}", sender) or not secret:
        raise RuntimeError("Perfil Threema de Estoque incompleto ou inválido")
    return {
        **os.environ,
        "THREEMA_ID_ESTOQUE": sender,
        "THREEMA_SECRET_ESTOQUE": secret,
        # The stock profile is Basic; do not inherit Hermes' E2E private key.
        "THREEMA_PRIVKEY_ESTOQUE": "",
    }


def send_threema(text: str, recipients: list[str], dry_run: bool) -> None:
    if dry_run:
        print("DRY_RUN message:\n" + text)
        return
    if not recipients:
        raise RuntimeError("Nenhum destinatário configurado para o alerta de estoque")
    if not THREEMA_SEND.exists():
        raise RuntimeError(f"threema_send.py não encontrado: {THREEMA_SEND}")
    sender_env = estoque_sender_env()
    for to in recipients:
        subprocess.run(  # noqa: S603 — fixed Python helper, arguments passed without a shell
            [sys.executable, str(THREEMA_SEND), "--profile", "estoque", "--to", to, "--text", text],
            env=sender_env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=3)
    parser.add_argument("--recipients", default=",".join(DEFAULT_RECIPIENTS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="send even if fingerprint unchanged")
    args = parser.parse_args()

    load_env_file(HERMES_ENV)
    conn = db_connect()
    token = get_bling_access_token(conn)
    candidates = load_candidates(conn, args.threshold)
    meta = {x["id"]: x for x in candidates}
    stocks = bling_stock_saldos(token, [x["id"] for x in candidates]) if candidates else []
    neg = find_negatives(stocks, meta)
    fp = fingerprint(neg)
    old_fp = load_state().get("fingerprint")

    if not neg:
        if not args.dry_run:
            save_state(fp, 0)
        return 0
    if old_fp == fp and not args.force and not args.dry_run:
        return 0

    recipients = [x.strip() for x in args.recipients.split(",") if x.strip()]
    msg = build_message(neg, len(candidates), len(stocks))
    send_threema(msg, recipients, args.dry_run)
    if not args.dry_run:
        # A failed/misconfigured sender must remain eligible for the next run.
        save_state(fp, len(neg))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "negativos": len(neg),
                    "candidatos": len(candidates),
                    "retornados": len(stocks),
                    "fingerprint": fp,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
