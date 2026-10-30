"""As contas dos Horários dos anúncios: segmento, saldo de Ads e quem vende eletro.

Pedido do dono (13/10/2026): em Marketing › Conferência › (Shopee | Mercado
Livre) › Horários dos anúncios, o seletor "Conta" virou os botões Mala |
Celular | Eletro e a lista das contas do segmento, uma embaixo da outra.

Cada conta de Ads (`marketing_accounts`) é ligada à MESMA loja na lista da
Conferência (`conferencia_shopee_conta`), que sabe o grupo (Mala/Celular), o
perfil do AdsPower (o saldo da Shopee) e entra no relatório (o eletro):

  • vínculo: `marketing_accounts.conferencia_conta_id` (semeado pela 0392);
    NULL → acha na hora pelas MESMAS regras da semente: ML pela
    `integration_id`; Shopee pelo nome normalizado (`chave_nome`), com os
    apelidos JLAS → Atlas e KIA → Fiore valendo só quando não há conta da
    Conferência com o próprio nome. Só par um-para-um; conta da Conferência
    que já tem dono não serve a outra;
  • segmento: o grupo da conta da Conferência → senão o `department` da conta
    de Ads, se for mala/celular → senão celular (a Shopee inteira está
    'geral'; o kfa do ML está 'celular' e na Conferência é mala — o grupo
    vence);
  • saldo (só Shopee): a ÚLTIMA leitura de `conferencia_shopee_saldo` do
    perfil do AdsPower da conta da Conferência (as coletas de seg/qua). Nada
    é coletado aqui. O Mercado Livre cobra depois: o `credit_balance` das
    contas do ML não quer dizer nada e NUNCA sai daqui;
  • eletro (decisão do dono): quem teve vendas ou Ads de eletro (> 0) em
    alguma das 4 semanas da ÚLTIMA rodada pronta da Conferência da
    plataforma, com a MESMA separação item a item do relatório — o cálculo
    (`fila.montar`, que não grava) refeito sobre os `dados` guardados das
    coletas. O relatório congelado não é tocado. Guardado em memória por 10
    min por execução (a conta é pesada: os itens das 4 semanas de cada loja).
    Se o cálculo falhar, vale o Eletro do relatório congelado.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models import (
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
    ConferenciaShopeeSaldo,
    MarketingAccount,
)
from app.services.conferencia_shopee import fila
from app.services.conferencia_shopee.classificacao import sem_acento

logger = structlog.get_logger()

PLATAFORMAS = ("shopee", "ml")
SEGMENTOS = ("mala", "celular")
# Apelido da conta de Ads → nome da loja na Conferência (os dois normalizados).
# Os mesmos da migration 0392.
APELIDOS = {"jlas": "atlas", "kia": "fiore"}

# Vendas ou Ads de eletro: as métricas da linha Eletro do relatório que contam
# (fora: comissão e cliques de afiliados sozinhos, sem venda).
METRICAS_ELETRO = (
    "vendas",
    "vendas_afiliados",
    "vendas_ads",
    "pedidos_afiliados",
    "pedidos_ads",
    "invest_ads",
    "impressoes",
    "cliques_ads",
)

CACHE_SEGUNDOS = 600.0
# execucao_id → (vale até [time.monotonic], contas da Conferência com eletro)
_cache_eletro: dict[UUID, tuple[float, frozenset[UUID]]] = {}

_INVISIVEIS = re.compile(r"[\s\u200b-\u200d\u2060\ufeff]+")
_NAO_ALNUM = re.compile(r"[^a-z0-9]")


def nome_limpo(nome: str | None) -> str:
    """Sem espaço nem caractere invisível nas pontas e sem espaço repetido
    (" barbosa" do ML ia para o topo da lista) — o `nomeLimpo` da tela."""
    return _INVISIVEIS.sub(" ", nome or "").strip()


def chave_nome(nome: str | None) -> str:
    """Minúsculo, sem acento, só letras e números (" Lucas MEI" → "lucasmei")."""
    return _NAO_ALNUM.sub("", sem_acento(nome))


def limpar_cache() -> None:
    _cache_eletro.clear()


# ───────────────────────────────────────────────────────────── vínculo


def _um_para_um(candidatos: Iterable[tuple[UUID, UUID]]) -> dict[UUID, UUID]:
    lista = list(candidatos)
    por_ads: dict[UUID, int] = defaultdict(int)
    por_conta: dict[UUID, int] = defaultdict(int)
    for a_id, c_id in lista:
        por_ads[a_id] += 1
        por_conta[c_id] += 1
    return {a: c for a, c in lista if por_ads[a] == 1 and por_conta[c] == 1}


def resolver_vinculos(
    ads: Sequence[MarketingAccount],
    contas: Sequence[ConferenciaShopeeConta],
    plataforma: str,
) -> dict[UUID, ConferenciaShopeeConta]:
    """conta de Ads → conta da Conferência (da MESMA plataforma).

    O vínculo gravado vale primeiro; quem não tem (ou aponta para conta que
    não é desta plataforma) é achado pelas regras da 0392 entre as contas da
    Conferência que ainda não têm dono entre as `ads` (as não arquivadas: a
    conta arquivada sumiu das telas e não segura a loja)."""
    da_plataforma = {c.id: c for c in contas if c.plataforma == plataforma}
    saida: dict[UUID, ConferenciaShopeeConta] = {}
    for a in ads:
        gravada = da_plataforma.get(a.conferencia_conta_id) if a.conferencia_conta_id else None
        # Duas contas de Ads na mesma (o índice único não deixa) → a primeira.
        if gravada is not None and all(c.id != gravada.id for c in saida.values()):
            saida[a.id] = gravada
    usadas = {c.id for c in saida.values()}
    livres = [c for c in da_plataforma.values() if c.id not in usadas]
    sem = [a for a in ads if a.id not in saida]

    candidatos: list[tuple[UUID, UUID]] = []
    if plataforma == "ml":
        por_integracao: dict[UUID, list[UUID]] = defaultdict(list)
        for c in livres:
            if c.integration_id is not None:
                por_integracao[c.integration_id].append(c.id)
        for a in sem:
            if a.integration_id is not None:
                candidatos += [(a.id, cid) for cid in por_integracao.get(a.integration_id, [])]
    else:
        # O apelido só vale se NENHUMA conta da Conferência (com dono ou não)
        # tem o próprio nome — como na semente.
        nomes_todas = {chave_nome(c.nome) for c in da_plataforma.values()}
        por_nome: dict[str, list[UUID]] = defaultdict(list)
        for c in livres:
            chave = chave_nome(c.nome)
            if chave:
                por_nome[chave].append(c.id)
        for a in sem:
            chave = chave_nome(a.name)
            if not chave:
                continue
            if chave in nomes_todas:
                alvo = chave
            else:
                alvo = APELIDOS.get(chave, "")
            candidatos += [(a.id, cid) for cid in por_nome.get(alvo, [])]
    for a_id, c_id in _um_para_um(candidatos).items():
        saida[a_id] = da_plataforma[c_id]
    return saida


def segmento(conta_ads: MarketingAccount, conta_conf: ConferenciaShopeeConta | None) -> str:
    """mala | celular: o grupo da Conferência → o `department` → celular."""
    if conta_conf is not None and conta_conf.grupo in SEGMENTOS:
        return conta_conf.grupo
    if (conta_ads.department or "").strip().lower() in SEGMENTOS:
        return conta_ads.department.strip().lower()
    return "celular"


# ───────────────────────────────────────────────────────────── saldo


async def saldos_mais_recentes(
    session: AsyncSession, perfis: Iterable[str | None]
) -> dict[str, tuple[datetime, float]]:
    """perfil do AdsPower → (lido_em, valor) da leitura mais nova."""
    lista = sorted({p for p in perfis if p})
    if not lista:
        return {}
    s = ConferenciaShopeeSaldo
    linhas = (
        await session.execute(
            select(s.adspower_user_id, s.lido_em, s.valor)
            .where(s.adspower_user_id.in_(lista))
            .distinct(s.adspower_user_id)
            .order_by(s.adspower_user_id, s.lido_em.desc(), s.id.desc())
        )
    ).all()
    return {perfil: (lido_em, float(valor)) for perfil, lido_em, valor in linhas}


# ───────────────────────────────────────────────────────────── eletro


async def ultima_execucao(
    session: AsyncSession, plataforma: str
) -> ConferenciaShopeeExecucao | None:
    """A rodada PRONTA mais nova da plataforma. Só a pronta tem relatório
    (fila.finalizar/recalcular): a cancelada fica sem e a que está coletando
    ainda não tem — e um `relatorio` JSON null não passa por "tem relatório".

    Sem o `relatorio` (JSONB grande: grupos × linhas × 4 semanas): a lista
    não usa e o eletro sai do cache ou das coletas; só o caminho de falha lê o
    congelado, à parte (`_relatorio_congelado`). Na sessão assíncrona o
    atributo adiado NÃO carrega sozinho — não ler `execucao.relatorio`."""
    e = ConferenciaShopeeExecucao
    return (
        await session.execute(
            select(e)
            .options(defer(e.relatorio))
            .where(e.plataforma == plataforma, e.status == "pronto")
            .order_by(e.criado_em.desc(), e.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def contas_eletro_do_relatorio(relatorio: Mapping[str, Any] | None) -> frozenset[UUID]:
    """As contas da Conferência da seção Eletro com vendas ou Ads (> 0) em
    alguma semana."""
    saida: set[UUID] = set()
    grupos = (relatorio or {}).get("grupos")
    for grupo in grupos if isinstance(grupos, list) else []:
        if not isinstance(grupo, Mapping) or grupo.get("chave") != "eletro":
            continue
        linhas = grupo.get("linhas")
        for linha in linhas if isinstance(linhas, list) else []:
            if not isinstance(linha, Mapping) or not linha.get("conta_id"):
                continue
            semanas = linha.get("semanas")
            teve = any(
                isinstance(sem, Mapping)
                and any(_positivo(sem.get(m)) for m in METRICAS_ELETRO)
                for sem in (semanas if isinstance(semanas, list) else [])
            )
            if not teve:
                continue
            try:
                saida.add(UUID(str(linha["conta_id"])))
            except ValueError:
                continue
    return frozenset(saida)


def _positivo(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


async def _relatorio_congelado(session: AsyncSession, execucao_id: UUID) -> Any:
    """Só o `relatorio` guardado da rodada (o `ultima_execucao` não traz)."""
    e = ConferenciaShopeeExecucao
    async with session.begin_nested():
        return (
            await session.execute(select(e.relatorio).where(e.id == execucao_id))
        ).scalar_one_or_none()


async def contas_eletro(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao
) -> frozenset[UUID]:
    """As contas da Conferência com eletro na rodada (cálculo refeito sobre os
    `dados` guardados, sem gravar; 10 min em memória)."""
    agora = time.monotonic()
    guardado = _cache_eletro.get(execucao.id)
    if guardado is not None and guardado[0] > agora:
        return guardado[1]
    try:
        # SAVEPOINT: um erro de banco no meio do cálculo não deixa a sessão
        # da rota inutilizável.
        async with session.begin_nested():
            relatorio = await fila.montar(session, execucao, datetime.now(UTC))
    except Exception as e:  # noqa: BLE001 — a lista de contas não pode cair por isso
        logger.warning(
            "horarios_eletro_calculo_falhou",
            execucao=str(execucao.id),
            erro=f"{type(e).__name__}: {e}"[:300],
        )
        try:
            relatorio = await _relatorio_congelado(session, execucao.id)
        except Exception as e2:  # noqa: BLE001
            # Nem o congelado: sem eletro AGORA, e nada guardado — a próxima
            # chamada tenta de novo (falha passageira não vale 10 min).
            logger.warning(
                "horarios_eletro_relatorio_falhou",
                execucao=str(execucao.id),
                erro=f"{type(e2).__name__}: {e2}"[:300],
            )
            return frozenset()
    ids = contas_eletro_do_relatorio(relatorio)
    for chave in [k for k, (vale, _) in _cache_eletro.items() if vale <= agora]:
        _cache_eletro.pop(chave, None)
    _cache_eletro[execucao.id] = (agora + CACHE_SEGUNDOS, ids)
    return ids


# ───────────────────────────────────────────────────────────── a lista


async def contas_dos_horarios(session: AsyncSession, plataforma: str) -> dict[str, Any]:
    """O corpo de GET /api/marketing/horarios/contas."""
    ads = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.platform == plataforma,
                MarketingAccount.arquivada_em.is_(None),
            )
        )
    ).scalars().all()
    contas = (
        await session.execute(
            select(ConferenciaShopeeConta).where(ConferenciaShopeeConta.plataforma == plataforma)
        )
    ).scalars().all()
    vinculos = resolver_vinculos(ads, contas, plataforma)

    saldos: dict[str, tuple[datetime, float]] = {}
    if plataforma == "shopee":
        saldos = await saldos_mais_recentes(
            session, (c.adspower_user_id for c in vinculos.values())
        )

    execucao = await ultima_execucao(session, plataforma)
    eletro = await contas_eletro(session, execucao) if execucao is not None else frozenset()

    saida = []
    for a in ads:
        conf = vinculos.get(a.id)
        item: dict[str, Any] = {
            "id": a.id,
            "name": nome_limpo(a.name),
            "segmento": segmento(a, conf),
            "eletro": conf is not None and conf.id in eletro,
            "acos_target": a.acos_target,
        }
        if plataforma == "shopee":
            lido = saldos.get(conf.adspower_user_id) if conf and conf.adspower_user_id else None
            item["saldo"] = lido[1] if lido else None
            item["saldo_em"] = lido[0] if lido else None
        saida.append(item)
    saida.sort(key=lambda c: (sem_acento(c["name"]), c["name"], str(c["id"])))

    return {
        "plataforma": plataforma,
        "contas": saida,
        "eletro_execucao": (
            {
                "id": execucao.id,
                "gerado_em": execucao.finalizado_em or execucao.criado_em,
            }
            if execucao is not None
            else None
        ),
    }
