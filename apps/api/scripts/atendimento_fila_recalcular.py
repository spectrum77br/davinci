"""Refaz a FILA ("Falta responder") das conversas com a régua nova (05/10/2026).

A régua de "mensagem automática" mudou junto com as mensagens automáticas
(docs/atendimento-automacoes.md §7.5): o cartão do pedido da campanha do
Duoke (Shopee `order`/`openapi`; TikTok `ORDER_CARD` de `CUSTOMER_SERVICE`) e
as respostas das opções 1, 2, 3 e 5 passaram a ser AUTOMÁTICAS (não fecham a
vez do comprador), e o "bom dia! ficou alguma dúvida" passou a ser PESSOA.
Medido em produção (05/10, 7 dias): ~2.018 mensagens da Shopee, 157 do TikTok
e 11 do ML mudam de lado, em ~1.870 conversas. O `recalcular_conversa` só roda
quando chega mensagem nova: as conversas paradas ficam com o carimbo da régua
velha, e a fila e a métrica misturam as duas. Este script refaz o carimbo de
uma vez — SÓ COM O OK DO EDUARDO (a fila cresce e a mediana muda).

SECO POR PADRÃO: sem `--gravar` só calcula e conta (quantas mudariam, quantas
entrariam e sairiam da fila), sem gravar nada (cada lote é desfeito). Com
`--gravar`, grava, um lote por transação, com as conversas travadas (a que
estiver sendo lida agora fica de fora: rodar de novo pega). Só lê e grava o
banco do DaVinci: nenhuma chamada a loja, nada sai para o comprador.

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.atendimento_fila_recalcular            # seco: só conta
    uv run python -m scripts.atendimento_fila_recalcular --gravar
    uv run python -m scripts.atendimento_fila_recalcular --dias 60 --lote 200

  --seco        só conta (o padrão; pode ser escrito, para deixar claro).
  --gravar      grava (sem ele, só conta).
  --dias N      conversas com mensagem nos últimos N dias (padrão 30).
  --lote N      conversas por transação (padrão 200).
  --limite N    para depois de N conversas.

A saída é o resumo em JSON: só contagens (nada de comprador).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

import app.db as _db
from app.models import AtendimentoConversa
from app.services.atendimento import gravar

# As plataformas em que a régua mudou.
PLATAFORMAS = ("shopee", "tiktok", "ml")
# O que o recálculo muda na fila (os outros carimbos andam junto).
CAMPOS = ("aguardando_resposta", "ultima_da_loja_em", "prazo_resposta_em")


async def recalcular(
    *,
    seco: bool = True,
    dias: int = 30,
    lote: int = 200,
    limite: int | None = None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Refaz `recalcular_conversa` nas conversas com mensagem em `dias` dias. `seco` = só conta."""
    agora = agora or datetime.now(UTC)
    lote = max(1, lote)
    resumo: dict[str, Any] = {
        "seco": seco,
        "dias": dias,
        "conversas": 0,
        "mudaram": 0,
        "entraram_na_fila": 0,
        "sairam_da_fila": 0,
    }
    por_plataforma: Counter[str] = Counter()
    depois_de: UUID | None = None
    while limite is None or resumo["conversas"] < limite:
        tamanho = lote if limite is None else min(lote, limite - resumo["conversas"])
        async with _db.SessionLocal() as session:
            q = select(AtendimentoConversa).where(
                AtendimentoConversa.plataforma.in_(PLATAFORMAS),
                AtendimentoConversa.ultima_mensagem_em >= agora - timedelta(days=dias),
            )
            if depois_de is not None:
                q = q.where(AtendimentoConversa.id > depois_de)
            q = q.order_by(AtendimentoConversa.id).limit(tamanho)
            # A que a leitura está gravando agora fica para a próxima vez —
            # também no seco: o flush do recálculo trava a linha até o rollback.
            q = q.with_for_update(skip_locked=True)
            conversas = list((await session.execute(q)).scalars().all())
            if not conversas:
                break
            depois_de = conversas[-1].id
            for c in conversas:
                antes = {k: getattr(c, k) for k in CAMPOS}
                await gravar.recalcular_conversa(session, c)
                depois = {k: getattr(c, k) for k in CAMPOS}
                resumo["conversas"] += 1
                if antes == depois:
                    continue
                resumo["mudaram"] += 1
                por_plataforma[c.plataforma] += 1
                if depois["aguardando_resposta"] and not antes["aguardando_resposta"]:
                    resumo["entraram_na_fila"] += 1
                elif antes["aguardando_resposta"] and not depois["aguardando_resposta"]:
                    resumo["sairam_da_fila"] += 1
            if seco:
                await session.rollback()
            else:
                await session.commit()
    resumo["mudaram_por_plataforma"] = dict(sorted(por_plataforma.items()))
    return resumo


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atendimento_fila_recalcular",
        description="Refaz a fila do atendimento com a régua nova (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--seco", action="store_true", help="só conta (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="grava (sem ele, só conta)")
    p.add_argument("--dias", type=int, default=30, help="conversas com mensagem em N dias")
    p.add_argument("--lote", type=int, default=200, help="conversas por transação")
    p.add_argument("--limite", type=int, default=None, help="para depois de N conversas")
    args = p.parse_args(argv)
    if args.dias < 1 or args.lote < 1 or (args.limite is not None and args.limite < 1):
        p.error("--dias, --lote e --limite precisam ser maiores que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    resumo = await recalcular(
        seco=not args.gravar, dias=args.dias, lote=args.lote, limite=args.limite
    )
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
