"""CLI entrypoint for the cutover.

Usage (from inside the api container, with `LEGACY_DATABASE_URL` env set):

    python -m app.cutover.cli migrate --reset
    python -m app.cutover.cli validate
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from app.cutover.migrate import run_cutover
from app.cutover.validate import compare_counts, render_table


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cutover")
    p.add_argument(
        "--legacy-url",
        default=os.environ.get("LEGACY_DATABASE_URL"),
        help="asyncpg URL pointing at the side-loaded legacy schema",
    )
    p.add_argument(
        "--target-url",
        default=os.environ.get("DATABASE_URL_RAW") or os.environ.get("DATABASE_URL"),
        help="asyncpg URL for the new davinci schema",
    )
    p.add_argument("--legacy-schema", default="stocksync_legacy")
    p.add_argument("--target-schema", default="davinci")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate")
    m.add_argument("--reset", action="store_true", help="TRUNCATE target tables first")
    m.add_argument(
        "--keep-credentials",
        action="store_true",
        help="copy integrations.credentials as-is instead of forcing re-OAuth",
    )

    sub.add_parser("validate")
    return p


def _normalize_dsn(url: str | None) -> str | None:
    if not url:
        return url
    # asyncpg only accepts plain postgresql:// — strip any +driver suffix used
    # by SQLAlchemy (e.g. postgresql+asyncpg://).
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _main_async(args: argparse.Namespace) -> int:
    legacy = _normalize_dsn(args.legacy_url)
    target = _normalize_dsn(args.target_url)
    if not legacy or not target:
        print("missing --legacy-url or --target-url", file=sys.stderr)
        return 2

    if args.cmd == "migrate":
        stats = await run_cutover(
            legacy_url=legacy,
            target_url=target,
            legacy_schema=args.legacy_schema,
            target_schema=args.target_schema,
            clear_credentials=not args.keep_credentials,
            reset=args.reset,
        )
        print("\n=== migration stats ===")
        for s in stats:
            print(
                f"{s.table:24s} legacy={s.legacy_count:6d} "
                f"inserted={s.inserted:6d} skipped={s.skipped:4d} "
                f"reasons={s.reasons or ''}"
            )
        return 0

    if args.cmd == "validate":
        results = await compare_counts(
            legacy_url=legacy,
            target_url=target,
            legacy_schema=args.legacy_schema,
            target_schema=args.target_schema,
        )
        print(render_table(results))
        return 0 if all(r.ok for r in results) else 1

    return 2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
