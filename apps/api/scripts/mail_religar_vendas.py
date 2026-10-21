"""Religa à VENDA os e-mails das pastas de venda que ficaram sem pedido (21/10/2026).

Medido em produção (09–21/10/2026, só contagem): ~460 e-mails das pastas
"vendas *" entraram numa conversa de e-mail SEM pedido, porque o nº do e-mail
ainda não estava no Bling quando a ponte leu (100% deles existiam no Bling
~30 min depois). Desde a 0424 (categoria por pasta) a pasta de venda abre a
conversa da venda pelo próprio nº (`ponte._conversa_da_venda`, alerta
`venda_pelo_numero`); o PASSADO não foi refeito (decisão 6 do dono). Este
script liga esses e-mails antigos — SÓ COM O OK DO EDUARDO (é alteração no
banco de produção).

Critério (o mesmo do desenho §3):
  • a meta está numa pasta de categoria `venda`, `gravado`/`sem_vinculo`, numa
    conversa de e-mail da ponte, sem pedido (nem na meta, nem na conversa) e
    sem chamado da Pós-venda;
  • a meta cita EXATAMENTE 1 nº (`pedidos_citados`), e esse nº existe HOJE
    nesta loja (`pedido.existe_na_loja`: Bling, índice ou conversa);
  • a conversa não mistura vendas: nenhum outro e-mail dela cita ou tem outro
    pedido (senão fica de fora: uma pessoa liga à mão na tela).
O passo é o da rota "vincular" (a mesma de uma pessoa na aba E-mail): o nº vai
para a conversa — ou, se a conversa de e-mail daquela venda já existe na loja,
as mensagens MUDAM para ela (`venda.mover_conversa`, sem duplicar) — e as
metas da conversa ficam com o pedido (`sem_vinculo` → `gravado`, vinculado_por
`religado`), com o registro no cartão do e-mail (alerta `religado_venda`).

SECO POR PADRÃO (`--dry-run`): calcula e conta, cada conversa numa transação
desfeita no fim. `--gravar` grava, uma conversa por transação, com a conversa
travada (a que estiver ocupada agora fica de fora: rodar de novo pega). Só lê
e grava o banco do DaVinci: nenhuma chamada a loja, nada sai para o comprador.

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.mail_religar_vendas                 # seco (--dry-run)
    uv run python -m scripts.mail_religar_vendas --gravar
    uv run python -m scripts.mail_religar_vendas --desde 2026-10-09 --ate 2026-10-22

  --dry-run     só conta (o padrão; pode ser escrito, para deixar claro).
  --gravar      grava (sem ele, só conta).
  --desde DATA  e-mails recebidos a partir desta data (padrão 2026-10-09).
  --ate DATA    e-mails recebidos antes desta data (padrão: agora).
  --limite N    para depois de N conversas.

A saída é o resumo em JSON: só contagens (nada de comprador, assunto ou nº).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.models import AtendimentoConversa, MailMessage
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from app.services.atendimento import gravar
from app.services.atendimento.constantes import CANAL_EMAIL, FONTE_TUTA
from app.services.mail_atendimento import pedido, ponte, venda
from app.services.mail_atendimento.constantes import (
    CATEGORIA_VENDA,
    ESTADO_GRAVADO,
    ESTADO_SEM_VINCULO,
)

DESDE_PADRAO = date(2026, 10, 9)
VINCULADO_POR = "religado"
ALERTA = "religado_venda"


def _citados(meta: MailMessageMeta) -> list[str]:
    return list(
        dict.fromkeys(
            str(p["pedido"])
            for p in (meta.pedidos_citados or [])
            if isinstance(p, dict) and p.get("pedido")
        )
    )


async def _candidatas(
    session: AsyncSession, *, desde: datetime, ate: datetime | None
) -> list[tuple[UUID, UUID]]:
    """(message_id, conversa_id) das metas de pasta de venda numa conversa sem pedido."""
    q = (
        select(MailMessageMeta.message_id, MailMessageMeta.conversa_id)
        .join(MailFolder, MailFolder.id == MailMessageMeta.folder_id)
        .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
        .join(AtendimentoConversa, AtendimentoConversa.id == MailMessageMeta.conversa_id)
        .where(
            MailFolder.categoria == CATEGORIA_VENDA,
            MailMessageMeta.estado.in_((ESTADO_GRAVADO, ESTADO_SEM_VINCULO)),
            MailMessageMeta.pedido_marketplace.is_(None),
            MailMessageMeta.chamado_id.is_(None),
            AtendimentoConversa.canal == CANAL_EMAIL,
            AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
            AtendimentoConversa.pedido_marketplace.is_(None),
            MailMessage.received_at >= desde,
        )
        .order_by(MailMessage.received_at, MailMessage.id)
    )
    if ate is not None:
        q = q.where(MailMessage.received_at < ate)
    return [(m, c) for m, c in (await session.execute(q)).all()]


async def _religar_uma(
    session: AsyncSession, meta: MailMessageMeta, conversa: AtendimentoConversa, numero: str
) -> tuple[str, bool]:
    """Liga a conversa ao nº (ou move para a conversa de e-mail da venda) → (resultado, moveu)."""
    # A conversa não mistura vendas: todo e-mail dela cita só este nº (ou nenhum).
    da_conversa = (
        (
            await session.execute(
                select(MailMessageMeta).where(MailMessageMeta.conversa_id == conversa.id)
            )
        )
        .scalars()
        .all()
    )
    for outra in da_conversa:
        if set(_citados(outra)) - {numero} or outra.pedido_marketplace not in (None, numero):
            return "conversa_mista", False
    destino = (
        (
            await session.execute(
                select(AtendimentoConversa).where(
                    *ponte.da_mesma_loja_sql(conversa),
                    AtendimentoConversa.canal == CANAL_EMAIL,
                    AtendimentoConversa.pedido_marketplace == numero,
                    AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
                    AtendimentoConversa.id != conversa.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if destino is not None:
        await gravar.travar_linha(session, destino)
        await venda.mover_conversa(session, conversa, destino)
        alvo = destino
    else:
        conversa.pedido_marketplace = numero[:64]
        alvo = conversa
    await session.execute(
        update(MailMessageMeta)
        .where(MailMessageMeta.conversa_id.in_({conversa.id, alvo.id}))
        .values(conversa_id=alvo.id, pedido_marketplace=numero[:64])
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailMessageMeta)
        .where(
            MailMessageMeta.conversa_id == alvo.id,
            MailMessageMeta.estado == ESTADO_SEM_VINCULO,
        )
        .values(estado=ESTADO_GRAVADO, vinculado_por=VINCULADO_POR)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(meta)
    agora = datetime.now(UTC)
    meta.alertas = [
        *(meta.alertas or []),
        {
            "codigo": ALERTA,
            "texto": "ligado à venda pelo religamento das pastas de venda (21/10/2026)",
            "em": agora.isoformat(),
        },
    ]
    await gravar._recalcular_etiqueta(
        session, alvo, motivo=gravar.MOTIVO_ETIQUETA_LEITURA, travar=False
    )
    return "ligado", destino is not None


async def religar(
    *,
    seco: bool = True,
    desde: datetime | None = None,
    ate: datetime | None = None,
    limite: int | None = None,
) -> dict[str, Any]:
    """Religa as conversas de venda sem pedido (`seco` = só conta, nada gravado)."""
    desde = desde or datetime.combine(DESDE_PADRAO, time(), tzinfo=UTC)
    resumo: Counter[str] = Counter()
    por_plataforma: Counter[str] = Counter()
    async with _db.SessionLocal() as session:
        candidatas = await _candidatas(session, desde=desde, ate=ate)
    vistas: set[UUID] = set()
    for message_id, conversa_id in candidatas:
        if conversa_id in vistas:
            resumo["mesma_conversa"] += 1
            continue
        if limite is not None and len(vistas) >= limite:
            break
        vistas.add(conversa_id)
        resumo["conversas"] += 1
        async with _db.SessionLocal() as session:
            meta = await session.get(MailMessageMeta, message_id)
            citados = _citados(meta) if meta is not None else []
            if meta is None or meta.conversa_id != conversa_id or len(citados) != 1:
                resumo["sem_um_numero"] += 1
                continue
            numero = citados[0]
            existe = await pedido.existe_na_loja(
                session,
                integration_id=meta.integration_id,
                plataforma=meta.plataforma or "",
                numero=numero,
                store_info_id=meta.store_info_id if meta.integration_id is None else None,
            )
            if not existe:
                resumo["nao_existe_no_bling"] += 1
                continue
            conversa = await session.get(AtendimentoConversa, conversa_id)
            if conversa is None or conversa.pedido_marketplace:
                resumo["conversa_ja_tem_pedido"] += 1
                continue
            await gravar.travar_linha(session, conversa)
            resultado, moveu = await _religar_uma(session, meta, conversa, numero)
            resumo[resultado] += 1
            if resultado == "ligado":
                resumo["movidas_para_a_venda" if moveu else "pedido_na_conversa"] += 1
                por_plataforma[meta.plataforma or "?"] += 1
            if seco:
                await session.rollback()
            else:
                await session.commit()
    saida: dict[str, Any] = {
        "seco": seco,
        "desde": desde.isoformat(),
        "ate": ate.isoformat() if ate else None,
        "candidatas": len(candidatas),
        **dict(sorted(resumo.items())),
        "ligado_por_plataforma": dict(sorted(por_plataforma.items())),
    }
    return saida


def _data(valor: str) -> datetime:
    return datetime.combine(date.fromisoformat(valor), time(), tzinfo=UTC)


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mail_religar_vendas",
        description="Religa à venda os e-mails das pastas de venda sem pedido (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="só conta (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="grava (sem ele, só conta)")
    p.add_argument("--desde", type=_data, default=None, help="AAAA-MM-DD (padrão 2026-10-09)")
    p.add_argument("--ate", type=_data, default=None, help="AAAA-MM-DD (padrão: agora)")
    p.add_argument("--limite", type=int, default=None, help="para depois de N conversas")
    args = p.parse_args(argv)
    if args.limite is not None and args.limite < 1:
        p.error("--limite precisa ser maior que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    resumo = await religar(seco=not args.gravar, desde=args.desde, ate=args.ate, limite=args.limite)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
