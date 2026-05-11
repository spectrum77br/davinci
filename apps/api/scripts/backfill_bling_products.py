"""Backfill products columns (category, min_stock, observation, bling_cost_price)
by re-fetching each product from Bling API.

Does NOT touch `cost_price` (user-editable). Refreshes name/price/stock/image_url
opportunistically so DB matches Bling source-of-truth.

Usage:
    .venv/bin/python -m scripts.backfill_bling_products \\
        --user-email user@example.com \\
        [--products-user-email other@example.com] \\
        [--integration-id <uuid>] \\
        [--dry-run | --apply] \\
        [--limit N] \\
        [--only-missing] \\
        [--rate 2.5]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.db import session_scope
from app.models import Integration, IntegrationPlatform, Product, User
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient, parse_bling_product

WATCHED = (
    "name",
    "category",
    "min_stock",
    "observation",
    "bling_cost_price",
    "price",
    "stock",
    "image_url",
)


def _coerce(field: str, val: Any) -> Any:
    if val is None:
        return None
    if field in ("min_stock", "stock"):
        return int(val)
    if field in ("price", "bling_cost_price"):
        return Decimal(str(val))
    return val


def _diff(p: Product, norm: dict) -> dict[str, tuple[Any, Any]]:
    out: dict[str, tuple[Any, Any]] = {}
    for f in WATCHED:
        new = norm.get(f)
        if new is None:
            continue
        if f in ("category", "observation") and not new:
            continue
        new_c = _coerce(f, new)
        old = getattr(p, f)
        if old != new_c:
            out[f] = (old, new_c)
    return out


def _apply(p: Product, diff: dict[str, tuple[Any, Any]]) -> None:
    for f, (_old, new) in diff.items():
        setattr(p, f, new)


async def _build_client(session, integ: Integration) -> BlingClient:
    creds = decrypt_json(integ.credentials)

    async def on_refresh(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=on_refresh, integration_id=integ.id)


async def run(args: argparse.Namespace) -> int:
    apply_changes = bool(args.apply)
    gap_s = 1.0 / max(args.rate, 0.1)

    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.email == args.user_email))
        ).scalar_one_or_none()
        if user is None:
            print(f"user_not_found: {args.user_email}", file=sys.stderr)
            return 2

        products_user_id = user.id
        if args.products_user_email and args.products_user_email != args.user_email:
            puser = (
                await session.execute(select(User).where(User.email == args.products_user_email))
            ).scalar_one_or_none()
            if puser is None:
                print(f"products_user_not_found: {args.products_user_email}", file=sys.stderr)
                return 2
            products_user_id = puser.id
            print(f"cross_user mode: bling={args.user_email} products={args.products_user_email}")

        integ_q = select(Integration).where(
            and_(
                Integration.user_id == user.id,
                Integration.platform == IntegrationPlatform.BLING,
            )
        )
        if args.integration_id:
            integ_q = integ_q.where(Integration.id == UUID(args.integration_id))
        integrations = (await session.execute(integ_q)).scalars().all()
        if not integrations:
            print("no_bling_integration", file=sys.stderr)
            return 2
        clients: dict[UUID, BlingClient] = {}
        for integ in integrations:
            clients[integ.id] = await _build_client(session, integ)

        prod_q = select(Product).where(
            and_(
                Product.user_id == products_user_id,
                Product.bling_product_id.is_not(None),
            )
        )
        if args.integration_id:
            prod_q = prod_q.where(
                or_(
                    Product.integration_id == UUID(args.integration_id),
                    Product.integration_id.is_(None),
                )
            )
        if args.only_missing:
            prod_q = prod_q.where(
                or_(
                    Product.category.is_(None),
                    Product.observation.is_(None),
                    Product.bling_cost_price.is_(None),
                )
            )
        prod_q = prod_q.order_by(Product.sku)
        if args.limit:
            prod_q = prod_q.limit(args.limit)

        products = (await session.execute(prod_q)).scalars().all()
        total = len(products)
        print(f"target_products={total} mode={'APPLY' if apply_changes else 'DRY-RUN'}")
        if total == 0:
            return 0

        updated = 0
        unchanged = 0
        errors: list[tuple[str, str]] = []
        skipped_404: list[str] = []
        sample_diffs: list[tuple[str, dict]] = []
        t0 = time.time()

        sem = asyncio.Semaphore(args.concurrency)
        completed = 0
        completed_lock = asyncio.Lock()

        async def fetch(p: Product) -> tuple[Product, dict | None, str | None]:
            nonlocal completed
            client = clients.get(p.integration_id) if p.integration_id else None
            if client is None:
                client = next(iter(clients.values()))
            async with sem:
                raw = None
                err = None
                for attempt in range(3):
                    try:
                        raw = await client.get_product(int(p.bling_product_id))
                        break
                    except Exception as e:  # noqa: BLE001
                        msg = str(e)
                        if "404" in msg:
                            err = "404"
                            break
                        if attempt < 2 and ("429" in msg or "503" in msg or "502" in msg):
                            await asyncio.sleep(2 ** attempt)
                            continue
                        err = msg[:200]
                        break
                await asyncio.sleep(gap_s / max(args.concurrency, 1))
            async with completed_lock:
                completed += 1
                if completed % 100 == 0:
                    rate = completed / (time.time() - t0)
                    eta = (total - completed) / max(rate, 0.01)
                    print(
                        f"  [{completed}/{total}] rate={rate:.1f}r/s eta={eta:.0f}s",
                        flush=True,
                    )
            return p, raw, err

        results = await asyncio.gather(*(fetch(p) for p in products))

        for p, raw, err in results:
            if err == "404":
                skipped_404.append(p.sku)
                continue
            if err:
                errors.append((p.sku, err))
                continue
            if raw is None:
                continue
            norm = parse_bling_product({"id": p.bling_product_id, **raw})
            diff = _diff(p, norm)
            if not diff:
                unchanged += 1
            else:
                if len(sample_diffs) < 5:
                    sample_diffs.append((p.sku, diff))
                if apply_changes:
                    _apply(p, diff)
                updated += 1
        processed = len(results)

        if apply_changes:
            await session.flush()
        else:
            await session.rollback()

        elapsed = time.time() - t0
        print()
        print("=" * 60)
        print(f"processed={processed} updated={updated} unchanged={unchanged}")
        print(f"skipped_404={len(skipped_404)} errors={len(errors)} elapsed={elapsed:.1f}s")
        print(f"mode={'APPLIED' if apply_changes else 'DRY-RUN (no commit)'}")
        if sample_diffs:
            print()
            print("Sample diffs:")
            for sku, d in sample_diffs:
                print(f"  {sku}:")
                for f, (old, new) in d.items():
                    print(f"    {f}: {old!r} -> {new!r}")
        if skipped_404:
            print()
            print(f"404 SKUs ({len(skipped_404)}): {skipped_404[:20]}{'...' if len(skipped_404) > 20 else ''}")
        if errors:
            print()
            print(f"Errors ({len(errors)}):")
            for sku, msg in errors[:10]:
                print(f"  {sku}: {msg}")

    return 0 if not errors else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-email", required=True, help="Bling integration owner email")
    ap.add_argument(
        "--products-user-email",
        default=None,
        help="Owner of products to backfill (if different from --user-email)",
    )
    ap.add_argument("--integration-id", default=None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--rate", type=float, default=3.0, help="req/s aggregate cap (default 3)")
    ap.add_argument("--concurrency", type=int, default=3, help="concurrent fetches (default 3)")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
