"""Backfill único: re-busca no Bling o status atual dos pedidos travados
em "Em andamento" (situacao 15) e re-ingere cada um (idempotente).

Contexto: o Bling V3 perde webhooks. Quando o webhook `15 → Entregue` se
perde, NADA reconferia a situacao 15 (a safety-net só cobria 6/83965 — hoje
6, 21 e o legado 83965), e
o pedido ficava eternamente "em rota" no DB. Como a aba Faturamento conta
só Entregue (83953), o total ficava ABAIXO do Bling — mais grave nos
meses recentes. A correção definitiva é a safety-net ampliada (que passa
a reconferir a 15); este script faz a correção IMEDIATA do backlog que já
existe, sem esperar o cron drenar 30/ciclo.

Cada pedido é re-buscado via `run_ingest_bling_order` (mesmo caminho do
webhook): pega a situacao real do Bling e faz upsert. Para um pedido já
entregue, só atualiza a situacao (itens inalterados).

Single-tenant: usa a única Integration BLING ativa (mesmo argumento que o
webhook passa pro ingest).

Uso (dentro do container `api`):
    python -m scripts.backfill_stale_em_andamento            # dry-run
    python -m scripts.backfill_stale_em_andamento --apply
    python -m scripts.backfill_stale_em_andamento --apply --min-days 7 \\
        --max-days 120 --rate 1.5 --limit 0
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text

from app.db import session_scope
from app.models import BlingOrder, Integration, IntegrationPlatform
from app.services.bling_orders import run_ingest_bling_order

# Situação alvo padrão: "Em andamento". Operador pode ampliar via
# --situacoes "15,21,83965,6" se quiser varrer mais estados intermediários
# (21 = Em digitação, etiqueta enviada, canônica desde 03/09/2026;
# 83965 = Enviado Etiqueta, legado).
_DEFAULT_SITUACOES = ("15",)


async def _situacao_atual(session, bling_id: int) -> str | None:
    return (
        await session.execute(
            select(BlingOrder.situacao)
            .where(BlingOrder.bling_id == bling_id, BlingOrder.item_index == 0)
            .limit(1)
        )
    ).scalar_one_or_none()


async def run(args: argparse.Namespace) -> int:
    apply_changes = bool(args.apply)
    situacoes = tuple(
        s.strip() for s in args.situacoes.split(",") if s.strip()
    ) or _DEFAULT_SITUACOES
    gap_s = 1.0 / max(args.rate, 0.1)
    now = datetime.now(UTC)
    recente = now.date() - timedelta(days=args.min_days)
    antigo = now.date() - timedelta(days=args.max_days)

    async with session_scope() as session:
        integ = (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform.BLING,
                    Integration.status == "active",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if integ is None:
            print("no_active_bling_integration", file=sys.stderr)
            return 2
        uid: UUID = integ.user_id

        q = (
            select(BlingOrder.bling_id, BlingOrder.situacao, BlingOrder.em_andamento_data)
            .where(
                BlingOrder.situacao.in_(situacoes),
                BlingOrder.em_andamento_data.isnot(None),
                BlingOrder.em_andamento_data <= recente,
                BlingOrder.em_andamento_data >= antigo,
                BlingOrder.bling_id.isnot(None),
                BlingOrder.item_index == 0,
            )
            .order_by(BlingOrder.em_andamento_data.asc())
        )
        if args.limit:
            q = q.limit(args.limit)
        rows = (await session.execute(q)).all()

    candidates = [int(r[0]) for r in rows]
    total = len(candidates)
    por_situacao = Counter(r[1] for r in rows)
    print(
        f"candidates={total} situacoes={list(situacoes)} "
        f"janela=[{antigo} .. {recente}] (em_andamento_data) "
        f"mode={'APPLY' if apply_changes else 'DRY-RUN'}"
    )
    print(f"  por_situacao={dict(por_situacao)}")
    if total == 0:
        return 0
    if not apply_changes:
        print("  amostra (bling_id, situacao, em_andamento_data):")
        for r in rows[:10]:
            print(f"    {int(r[0])}  sit={r[1]}  ead={r[2]}")
        print("DRY-RUN: nada re-ingerido. Rode com --apply pra corrigir.")
        return 0

    transitions: Counter[str] = Counter()
    errors: list[tuple[int, str]] = []
    t0 = time.time()
    for i, bid in enumerate(candidates, 1):
        try:
            async with session_scope() as s:
                # Lock por pedido — serializa com webhooks concorrentes do
                # mesmo pedido (mesmo padrão do ingest_bling_order_run).
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"ingest_bling_order:{bid}"},
                )
                before = await _situacao_atual(s, bid)
                await run_ingest_bling_order(
                    s, bling_order_id=bid, user_id=uid, event=None
                )
            async with session_scope() as s2:
                after = await _situacao_atual(s2, bid)
            transitions[f"{before}->{after}"] += 1
        except Exception as e:  # noqa: BLE001
            errors.append((bid, str(e)[:200]))
        if i % 25 == 0 or i == total:
            rate = i / max(time.time() - t0, 0.01)
            eta = (total - i) / max(rate, 0.01)
            print(
                f"  [{i}/{total}] rate={rate:.1f}/s eta={eta:.0f}s "
                f"errors={len(errors)}",
                flush=True,
            )
        await asyncio.sleep(gap_s)

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print(f"processed={total - len(errors)} errors={len(errors)} elapsed={elapsed:.1f}s")
    print("transições (situacao_antes -> situacao_depois):")
    for k, n in sorted(transitions.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")
    if errors:
        print()
        print(f"Errors ({len(errors)}):")
        for bid, msg in errors[:15]:
            print(f"  {bid}: {msg}")
    return 0 if not errors else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--situacoes", default="15",
        help='Situações alvo separadas por vírgula (default "15"; '
        'etiqueta enviada = 21 Em digitação, 83965 legado).',
    )
    ap.add_argument(
        "--min-days", type=int, default=7,
        help="Mínimo de dias parado (em_andamento_data) pra reconferir (default 7).",
    )
    ap.add_argument(
        "--max-days", type=int, default=120,
        help="Máximo de dias parado — além disso é zumbi (default 120).",
    )
    ap.add_argument(
        "--rate", type=float, default=1.5,
        help="Pedidos por segundo (default 1.5; cada ingest faz +1 chamada Bling).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Limita o nº de pedidos (0=todos).")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
