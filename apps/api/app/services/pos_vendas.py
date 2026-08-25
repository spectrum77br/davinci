"""Pós Vendas: sync das NF-e das contas de emissão + casamento com pedidos.

A página /notas-fiscais (menu "Pós vendas") mostra cada pedido ENVIADO com
as duas notas fiscais do envio:

- NF EMBALAGEM — emitida pela conta Bling da EMPRESA DONA da loja (traz o
  nome da loja; item simbólico "embalagem", valor baixo);
- NF PRODUTO — nota cheia emitida por uma conta avulsa (sem nome de loja;
  item = o produto real).

Duas metades independentes:

1. `sync_notas_emitidas(s)` — cron (10 em 10 min). Lista as NF-e de cada
   conta `bling_notas` ativa e faz upsert em `bling_notas_emitidas`. O
   VALOR da nota só existe no detalhe (GET /nfe/{id} — a listagem não traz
   `valorNota`), então o sync completa os valores aos poucos com teto por
   rodada. Também preenche `bling_notas.cnpj`/`emitente` uma única vez por
   conta (1 XML autorizado — o emitente é fixo por conta).

2. `match_notas(pedidos, notas)` — função PURA (testável sem banco) que
   casa pedido ↔ notas:
     1ª chave  complemento do endereço da nota == numeroloja do pedido
               (o fluxo de emissão grava o pedido do marketplace no
               complemento — medido 9/9 em produção, ago/2026);
     2ª chave  CPF do destinatário + janela de dias em torno do envio.
   Classificação por conta emissora: CNPJ da conta == CNPJ da empresa dona
   da loja (store_info.cnpj) → embalagem; senão → produto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bling_nota import BlingNota, BlingNotaEmitida

logger = structlog.get_logger()

# Situações Bling com NF-e efetivamente emitida (espelha o router).
ISSUED_SITUACOES = {5, 6, 7}

# Janela relistada a cada rodada (upsert idempotente — relistar é barato:
# 1-2 páginas por conta). 40 dias cobre reprocessos e contas paradas.
SYNC_WINDOW_DAYS = 40
# Teto de buscas de DETALHE por rodada (valor da nota). O rate do Bling é
# por app OAuth (cada conta é um app), mas o gate global do processo é
# 5 req/s — 80 detalhes ≈ 16s de fila no pior caso.
DETAIL_FETCH_CAP = 80

_DIGITS_RE = re.compile(r"[^0-9]")


def _digits(v: str | None) -> str:
    return _DIGITS_RE.sub("", v or "")


def _parse_emissao(v: str | None) -> datetime | None:
    """"2026-08-07 07:23:29" (BRT local do Bling) → datetime naive."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


# ─── metade 1: sync (cron) ────────────────────────────────────────────


async def sync_notas_emitidas(
    s: AsyncSession, *, window_days: int = SYNC_WINDOW_DAYS
) -> dict[str, int]:
    """Espelha as NF-e das contas ativas em `bling_notas_emitidas`.

    `window_days` maior serve pro backfill inicial (chamada manual única).
    Import do router é tardio de propósito: o router importa este service
    (endpoint Pós Vendas) — import no topo criaria ciclo.
    """
    from app.routers import notas_fiscais as nf

    hoje = date.today()
    date_from = hoje - timedelta(days=window_days)
    date_to = hoje + timedelta(days=1)

    contas = (
        (
            await s.execute(
                select(BlingNota)
                .where(BlingNota.status == "active")
                .order_by(BlingNota.nome)
            )
        )
        .scalars()
        .all()
    )

    listadas = upserts = detalhes = falhas = 0
    detail_budget = DETAIL_FETCH_CAP
    for conta in contas:
        try:
            token = await nf._ensure_token(s, conta)
            notas = await nf._list_notas_bling(token, date_from, date_to)
        except Exception as e:  # noqa: BLE001 — conta ruim não derruba as demais
            falhas += 1
            logger.warning(
                "pos_vendas_sync_conta_failed", conta=conta.nome, err=str(e)[:200]
            )
            continue
        listadas += len(notas)
        for n in notas:
            if not n.get("id"):
                continue
            contato = n.get("contato") or {}
            endereco = contato.get("endereco") or {}
            stmt = pg_insert(BlingNotaEmitida).values(
                conta_id=conta.id,
                bling_id=int(n["id"]),
                numero=n.get("numero"),
                situacao=n.get("situacao"),
                data_emissao=_parse_emissao(n.get("dataEmissao")),
                chave_acesso=n.get("chaveAcesso"),
                cpf_dest=_digits(contato.get("numeroDocumento")) or None,
                nome_dest=contato.get("nome"),
                complemento=(endereco.get("complemento") or "").strip() or None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    BlingNotaEmitida.conta_id,
                    BlingNotaEmitida.bling_id,
                ],
                set_={
                    "numero": stmt.excluded.numero,
                    "situacao": stmt.excluded.situacao,
                    "data_emissao": stmt.excluded.data_emissao,
                    "chave_acesso": stmt.excluded.chave_acesso,
                    "cpf_dest": stmt.excluded.cpf_dest,
                    "nome_dest": stmt.excluded.nome_dest,
                    "complemento": stmt.excluded.complemento,
                    "updated_at": func.now(),
                },
            )
            await s.execute(stmt)
            upserts += 1
        await s.commit()

        # Valores pendentes desta conta (mais recentes primeiro).
        if detail_budget > 0:
            pendentes = (
                (
                    await s.execute(
                        select(BlingNotaEmitida)
                        .where(
                            BlingNotaEmitida.conta_id == conta.id,
                            BlingNotaEmitida.detalhe_ok.is_(False),
                            BlingNotaEmitida.situacao.in_(ISSUED_SITUACOES),
                        )
                        .order_by(BlingNotaEmitida.data_emissao.desc())
                        .limit(detail_budget)
                    )
                )
                .scalars()
                .all()
            )
            for row in pendentes:
                try:
                    payload = await nf._bling_get(token, f"/nfe/{row.bling_id}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "pos_vendas_sync_detail_failed",
                        conta=conta.nome,
                        bling_id=row.bling_id,
                        err=str(e)[:200],
                    )
                    break  # conta com problema — tenta na próxima rodada
                detail = payload.get("data") or {}
                row.valor = detail.get("valorNota")
                row.detalhe_ok = True
                detalhes += 1
                detail_budget -= 1
            await s.commit()

        # Emitente da conta (uma vez na vida): 1 XML autorizado.
        if not conta.cnpj:
            alvo = next(
                (n for n in notas if n.get("situacao") in ISSUED_SITUACOES), None
            )
            if alvo is not None:
                try:
                    _, xml, _ = await nf._fetch_nota_xml(token, alvo)
                    parsed = nf._parse_nfe_xml(xml) if xml else None
                    if parsed and parsed.get("cnpj"):
                        conta.cnpj = parsed["cnpj"]
                        conta.emitente = parsed.get("conta_nf") or None
                        await s.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "pos_vendas_sync_emitente_failed",
                        conta=conta.nome,
                        err=str(e)[:200],
                    )

    summary = {
        "contas": len(contas),
        "falhas": falhas,
        "listadas": listadas,
        "upserts": upserts,
        "detalhes": detalhes,
    }
    return summary


# ─── metade 2: casamento (puro) ───────────────────────────────────────

# Janela do fallback por CPF: a nota nasce ANTES do envio (a etiqueta
# depende dela), com folga pra retido/reemissão; depois do envio só sobra
# reemissão imediata.
JANELA_ANTES_DIAS = 10
JANELA_DEPOIS_DIAS = 3


@dataclass
class PedidoIn:
    """Pedido enviado (uma linha por pedido, itens já agregados)."""

    numero: str
    numeroloja: str | None
    cpf: str | None  # só dígitos
    envio: date | None
    store_cnpj: str | None  # só dígitos (empresa dona da loja)


@dataclass
class NotaIn:
    """NF-e espelhada + emitente da conta."""

    key: Any  # id da linha (UUID) — devolvido no resultado
    conta_cnpj: str | None  # só dígitos
    cpf: str | None  # só dígitos
    complemento: str | None
    data_emissao: datetime | None
    situacao: int | None


@dataclass
class Casamento:
    embalagem: Any | None = None  # NotaIn.key
    produto: Any | None = None
    embalagem_via: str | None = None  # "pedido" | "cpf"
    produto_via: str | None = None


@dataclass
class _Cand:
    nota: NotaIn
    usada: bool = field(default=False)


def _dist_dias(nota: NotaIn, envio: date | None) -> float:
    if nota.data_emissao is None or envio is None:
        return 999.0
    return abs((nota.data_emissao.date() - envio).days)


def match_notas(
    pedidos: list[PedidoIn], notas: list[NotaIn]
) -> dict[str, Casamento]:
    """Casa cada pedido com (até) uma NF embalagem e uma NF produto.

    Cada nota é usada por no máximo UM pedido. Passada 1 (chave exata por
    numeroloja) tem prioridade global sobre a passada 2 (CPF + janela);
    dentro de cada passada, empate resolve pela emissão mais próxima do
    envio. Notas não emitidas (situação fora de 5/6/7) ficam de fora.
    """
    cands = [_Cand(n) for n in notas if n.situacao in ISSUED_SITUACOES]
    por_complemento: dict[str, list[_Cand]] = {}
    por_cpf: dict[str, list[_Cand]] = {}
    for c in cands:
        if c.nota.complemento:
            por_complemento.setdefault(c.nota.complemento, []).append(c)
        if c.nota.cpf:
            por_cpf.setdefault(c.nota.cpf, []).append(c)

    out: dict[str, Casamento] = {p.numero: Casamento() for p in pedidos}
    ordenados = sorted(
        pedidos, key=lambda p: (p.envio or date.min, p.numero), reverse=True
    )

    def _atribuir(p: PedidoIn, disponiveis: list[_Cand], via: str) -> None:
        r = out[p.numero]
        store = _digits(p.store_cnpj)
        emb = [
            c
            for c in disponiveis
            if not c.usada and store and _digits(c.nota.conta_cnpj) == store
        ]
        prod = [
            c
            for c in disponiveis
            if not c.usada and (not store or _digits(c.nota.conta_cnpj) != store)
        ]
        if r.embalagem is None and emb:
            best = min(emb, key=lambda c: _dist_dias(c.nota, p.envio))
            best.usada = True
            r.embalagem, r.embalagem_via = best.nota.key, via
        if r.produto is None and prod:
            best = min(prod, key=lambda c: _dist_dias(c.nota, p.envio))
            best.usada = True
            r.produto, r.produto_via = best.nota.key, via

    # Passada 1 — chave exata: complemento da nota == numeroloja.
    for p in ordenados:
        if p.numeroloja:
            _atribuir(p, por_complemento.get(p.numeroloja, []), "pedido")

    # Passada 2 — CPF + janela em torno do envio.
    for p in ordenados:
        r = out[p.numero]
        if r.embalagem is not None and r.produto is not None:
            continue
        cpf = _digits(p.cpf)
        if not cpf:
            continue
        janela = [
            c
            for c in por_cpf.get(cpf, [])
            if not c.usada
            and _na_janela(c.nota, p.envio)
        ]
        _atribuir(p, janela, "cpf")

    return out


def _na_janela(nota: NotaIn, envio: date | None) -> bool:
    if envio is None:
        return False
    if nota.data_emissao is None:
        return False
    d = nota.data_emissao.date()
    return (
        envio - timedelta(days=JANELA_ANTES_DIAS)
        <= d
        <= envio + timedelta(days=JANELA_DEPOIS_DIAS)
    )


__all__ = [
    "Casamento",
    "NotaIn",
    "PedidoIn",
    "match_notas",
    "sync_notas_emitidas",
]
