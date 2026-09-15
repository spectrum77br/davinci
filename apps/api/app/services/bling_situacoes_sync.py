"""Sincroniza o catálogo `situacao_bling` (id → nome das situações de pedido
de venda) com o que EXISTE no Bling hoje.

Histórico: a tabela veio do userdb na migration 0017 e nunca mais foi
atualizada — o dropdown "Situação no Bling ao fechar" dos Chamados (e o
"Alterar status Bling" da Logística) mostrava situações que o Bling já apagou
("Enviado Geral CI", "Enviado Fake"…). Eduardo 15/09/2026: "o bling não tem
mais todas essas situações, tem situação ali que nem existe mais".

Regra:
- `GET /situacoes/modulos` → módulo "Vendas" → `GET /situacoes/modulos/{id}`;
- upsert por id (nome atualizado, `ativo = true`);
- o que sumiu do Bling vira `ativo = false` — NUNCA apaga: `bling_orders.situacao`
  e as views de pedidos ainda apontam pros ids antigos e precisam do nome;
- resposta vazia do Bling = aborta sem inativar nada (defesa contra a API
  devolver lista vazia num soluço e o catálogo inteiro sumir dos dropdowns).

Roda 1x/dia no worker principal (e no startup) — `worker.bling_situacoes_sync`.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SituacaoBling
from app.services import logistica_bling
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

NOME_MODULO_VENDAS = "vendas"


class SituacoesSyncError(Exception):
    """Falha de negócio do sync (código legível pro log)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def modulo_vendas(modulos: list[dict]) -> dict | None:
    """Módulo dos pedidos de venda: nome "Vendas" (exato, sem caixa); se o
    Bling renomear, cai no primeiro que contenha "venda"."""
    por_nome = [m for m in modulos if isinstance(m, dict)]
    for m in por_nome:
        if (str(m.get("nome") or "").strip().lower()) == NOME_MODULO_VENDAS:
            return m
    for m in por_nome:
        if "venda" in str(m.get("nome") or "").strip().lower():
            return m
    return None


def situacoes_por_id(rows: list[dict]) -> dict[int, str]:
    """{id: nome} das situações vindas do Bling, ignorando linhas sem id/nome."""
    out: dict[int, str] = {}
    for s in rows:
        if not isinstance(s, dict):
            continue
        try:
            sid = int(s.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        nome = str(s.get("nome") or s.get("descricao") or "").strip()
        if nome:
            out[sid] = nome
    return out


async def sync_situacoes_bling(session: AsyncSession, *, client: BlingClient | None = None) -> dict:
    """Espelha o módulo Vendas do Bling em `situacao_bling`. Commita.

    Devolve o resumo {modulo, total, novas, renomeadas, reativadas, inativadas}.
    Levanta `SituacoesSyncError` (módulo não achado / lista vazia) e deixa
    subir os erros de integração (`logistica_bling.BlingObsError`, httpx)."""
    client = client or await logistica_bling._bling_client(session)
    modulo = modulo_vendas(await client.list_situacoes_modulos())
    if modulo is None:
        raise SituacoesSyncError("bling_modulo_vendas_nao_encontrado")
    modulo_id = int(modulo["id"])
    atuais = situacoes_por_id(await client.list_situacoes_modulo(modulo_id))
    if not atuais:
        raise SituacoesSyncError("bling_situacoes_vazio")

    existentes = {int(r.id): r for r in (await session.execute(select(SituacaoBling))).scalars()}
    novas = renomeadas = reativadas = inativadas = 0
    for sid, nome in atuais.items():
        row = existentes.get(sid)
        if row is None:
            session.add(SituacaoBling(id=sid, nome=nome, ativo=True))
            novas += 1
            continue
        if row.nome != nome:
            row.nome = nome
            renomeadas += 1
        if not row.ativo:
            row.ativo = True
            reativadas += 1
    for sid, row in existentes.items():
        if sid not in atuais and row.ativo:
            row.ativo = False
            inativadas += 1
    await session.commit()
    resumo = {
        "modulo": modulo_id,
        "total": len(atuais),
        "novas": novas,
        "renomeadas": renomeadas,
        "reativadas": reativadas,
        "inativadas": inativadas,
    }
    logger.info("bling_situacoes_sync", **resumo)
    return resumo
