"""Bulk-import Mercado Livre integrations from CSV (apps/api/scripts/).

CSV columns: id,clientID,key,rt,at,Nome,loja_id_ML

Creates one Integration per row with platform=ml, store_id=NULL (link in UI later).
Skips rows whose user_id already exists in `integrations.credentials.user_id`.
Refreshes the access_token on import (rotates rt) so DB starts with valid creds.

Usage:
    .venv/bin/python -m scripts.import_ml_integrations <csv_path> <owner_email>
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.db import get_session
from app.models import Integration, IntegrationPlatform, User
from app.security.cipher import decrypt_json, encrypt_json

ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


async def refresh_token(client_id: str, client_secret: str, rt: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            ML_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": rt,
            },
        )
        if r.status_code != 200:
            print(f"  refresh failed: {r.status_code} {r.text[:200]}")
            return None
        return r.json()


async def main(csv_path: str, owner_email: str) -> None:
    async for s in get_session():
        owner = (await s.execute(select(User).where(User.email == owner_email))).scalar_one_or_none()
        if owner is None:
            sys.exit(f"user not found: {owner_email}")

        existing = (await s.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.ML)
        )).scalars().all()
        existing_user_ids: set[int] = set()
        for i in existing:
            try:
                c = decrypt_json(i.credentials)
                if c.get("user_id"):
                    existing_user_ids.add(int(c["user_id"]))
            except Exception:
                pass

        with open(csv_path) as f:
            rows = list(csv.DictReader(f))

        created = skipped = failed = 0
        for row in rows:
            ml_user_id = int(row["loja_id_ML"])
            name = f"ML {row['Nome']}".strip()
            if ml_user_id in existing_user_ids:
                print(f"skip {name} (user_id={ml_user_id} already exists)")
                skipped += 1
                continue

            print(f"refresh {name} (user_id={ml_user_id})...")
            tok = await refresh_token(row["clientID"], row["key"], row["rt"])
            if tok is None:
                failed += 1
                continue

            creds = {
                "client_id": row["clientID"],
                "client_secret": row["key"],
                "access_token": tok["access_token"],
                "refresh_token": tok["refresh_token"],
                "user_id": ml_user_id,
                "expires_at": int(time.time()) + int(tok.get("expires_in", 21600)),
            }
            integ = Integration(
                user_id=owner.id,
                store_id=None,
                platform=IntegrationPlatform.ML,
                name=name,
                credentials=encrypt_json(creds),
                token_expires_at=datetime.fromtimestamp(creds["expires_at"], tz=UTC),
                status="active",
            )
            s.add(integ)
            await s.flush()
            created += 1
            print(f"  ok -> {integ.id}")

        await s.commit()
        print(f"\ndone: created={created} skipped={skipped} failed={failed}")
        break


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m scripts.import_ml_integrations <csv_path> <owner_email>")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
