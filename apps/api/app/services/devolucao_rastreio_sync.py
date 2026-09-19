"""Rastreio AUTOMÁTICO do pacote que VOLTA (aba Acompanhamento de Devoluções).

Eduardo (03/09): "o TikTok não está pegando o número de rastreio correto" /
"esse rastreio está incorreto, precisa sempre estar atualizadinho" / "em
devolução desde todas as datas estão iguais". A aba mostrava o rastreio da
ENTREGA original (painel Logística); o que interessa ali é a DEVOLUÇÃO — que
tem código e transportadora próprios na returns API de cada marketplace.

Fluxo (cron a cada 30 min + botão de recarregar quando houver):
  1. pedidos hoje em "Aguardando Devolução" (83957) + a linha da Logística de
     cada um (pedido_marketplace/conta/plataforma);
  2. por marketplace, `returns_por_pedido(session, linhas)` (contrato em
     services/devolucao_returns.ReturnInfo — implementado em logistica_tiktok /
     logistica_shopee / logistica_meli);
  3. grava em `devolucao_rastreio.*_auto` (grão pedido; o MANUAL continua
     mandando na aba) — inclusive `devolucao_criada_em`, que vira o "Em
     devolução desde" real;
  4. registra no 17track os códigos Correios (`...BR`) novos E os que o
     17track disser que não conhece (registro que falhou numa rodada anterior
     — sem saldo, rede) — a localização do pacote de volta chega pelo webhook
     (routers/logistica_track) e pelo pull de cada rodada (`_puxar_correios`).
     Como na Logística, só conta como registrado o que o 17track confirmou.

Best-effort em todas as camadas: um marketplace fora do ar não derruba os
outros; um pedido sem devolução conhecida fica como está.
"""

from __future__ import annotations

import inspect
import unicodedata
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio, Logistica, MarketplaceOrderFinancial
from app.services import logistica_rules, logistica_track, logistica_track_sync
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt

logger = structlog.get_logger()

# Depois da entrega do pacote de volta, o detalhe da devolução continua sendo
# consultado por estes dias: cobre o prazo de conferência da loja (Shopee: 3
# dias corridos) com folga...
DIAS_DETALHE_APOS_ENTREGA = 5
# ...e enquanto o caso estiver numa situação em que a plataforma ainda pode
# pedir algo da loja (evidência, proposta, compensação) — poucos casos.
STATUS_COM_DETALHE = frozenset({"REQUESTED", "JUDGING", "SELLER_DISPUTE"})

_SITUACAO_AGUARDANDO_DEVOLUCAO = "83957"


def _qualified(table: str) -> str:
    return f'"{get_settings().database_schema}".{table}'


def _plataforma_key(plataforma: str | None) -> str | None:
    p = (plataforma or "").strip().lower()
    if p in logistica_rules._ML_PLATAFORMAS:
        return "ml"
    if p in logistica_rules._SHOPEE_PLATAFORMAS:
        return "shopee"
    if p in logistica_rules._TIKTOK_PLATAFORMAS:
        return "tiktok"
    return None


def _num(rastreio: str | None) -> str:
    """Número do jeito que o 17track o devolve (MAIÚSCULAS, sem espaços) — é
    assim que a resposta dele precisa casar com o que mandamos."""
    return (rastreio or "").strip().upper()


async def _fetch_por_marketplace(
    session: AsyncSession,
    key: str,
    linhas: list[Logistica],
    ja_entregues: set[str] | None = None,
    reconsultar: set[str] | None = None,
) -> dict[str, ReturnInfo]:
    """Chama o `returns_por_pedido` do módulo do marketplace. Import tardio e
    tolerante: módulo sem a função (ainda) ou erro → dict vazio + log.

    `ja_entregues` = pedidos cujo pacote de volta já consta entregue (a Shopee
    usa pra não refazer a leitura da SPX). `reconsultar` = os que, mesmo
    entregues, ainda precisam do DETALHE (prazo de resposta da loja). Só os
    kwargs que a função aceita são passados — inspecionar a assinatura evita
    o antigo `except TypeError`, que refazia a chamada inteira quando um
    TypeError vinha de DENTRO do fetcher."""
    try:
        if key == "tiktok":
            from app.services import logistica_tiktok as mod
        elif key == "shopee":
            from app.services import logistica_shopee as mod
        else:
            from app.services import logistica_meli as mod
        fn = getattr(mod, "returns_por_pedido", None)
        if fn is None:
            logger.warning("devolucao_rastreio_sync_sem_fetcher", marketplace=key)
            return {}
        aceita = inspect.signature(fn).parameters
        kwargs: dict[str, set[str]] = {}
        if "ja_entregues" in aceita:
            kwargs["ja_entregues"] = ja_entregues or set()
        if "reconsultar" in aceita:
            kwargs["reconsultar"] = reconsultar or set()
        out = await fn(session, linhas, **kwargs)
        return dict(out or {})
    except Exception as e:  # noqa: BLE001 — um marketplace não derruba os outros
        logger.warning("devolucao_rastreio_sync_fetch_falhou", marketplace=key, err=str(e)[:300])
        return {}


async def pedidos_em_devolucao(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT v.pedido_bling::text AS pedido "  # noqa: S608
                f"FROM {_qualified('vw_devolucoes')} v "
                "WHERE v.situacao = :s"
            ),
            {"s": _SITUACAO_AGUARDANDO_DEVOLUCAO},
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def _linhas_logistica(
    session: AsyncSession, pedidos: Collection[str]
) -> dict[str, Logistica]:
    """Linha da Logística por pedido (a mais recente quando há mais de uma)."""
    if not pedidos:
        return {}
    rows = (
        await session.execute(select(Logistica).where(Logistica.pedido_bling.in_(list(pedidos))))
    ).scalars().all()
    por_pedido: dict[str, Logistica] = {}
    for r in rows:
        cur = por_pedido.get(r.pedido_bling or "")
        if cur is None or (r.updated_at and cur.updated_at and r.updated_at > cur.updated_at):
            por_pedido[r.pedido_bling or ""] = r
    return por_pedido


# "Objeto entregue ao destinatário" no pacote de VOLTA = chegou em NÓS (numa
# devolução, o destinatário é o vendedor). "Entregue ao remetente" fica de
# fora de propósito: aí o pacote voltou pro comprador, não pra gente.
# EXCETO no ML com revisão: o destinatário da perna atual é o galpão do ML
# em Cajamar (`devolucao_destino_auto = warehouse`) — ver `_perna_do_galpao`.
_TEXTO_ENTREGUE = "entregue ao destinat"


def _texto_diz_entregue(localizacao: str | None) -> bool:
    txt = unicodedata.normalize("NFKD", (localizacao or "").casefold())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    alvo = unicodedata.normalize("NFKD", _TEXTO_ENTREGUE)
    alvo = "".join(c for c in alvo if not unicodedata.combining(c))
    return alvo in txt


def _perna_do_galpao(row: DevolucaoRastreio) -> bool:
    """O `rastreio_auto` desta linha é o da perna comprador → GALPÃO do ML
    (revisão em Cajamar): "entregue" nele — texto do 17track, pull dos
    Correios ou push — é o pacote no Mercado Livre, não na loja, e NÃO
    carimba `pacote_entregue_em`. A chegada de verdade vem da perna seguinte
    (`seller_address`), pela API (`ReturnInfo.entregue_em`) ou pelo rastreio
    novo dela. TikTok/Shopee não separam (destino None → False)."""
    return (row.fonte_auto or "").strip().lower() == "ml" and logistica_rules.devolucao_no_galpao(
        {logistica_rules.RETURN_DESTINO_KEY: row.devolucao_destino_auto or ""}
    )


def _carimbo_do_galpao(row: DevolucaoRastreio, info: ReturnInfo, *, codigo_novo: bool) -> bool:
    """O `pacote_entregue_em` desta linha do ML foi deixado pelo rastreio da
    perna do GALPÃO e precisa ser apagado (Vinicius 19/09: 295359/292659/
    294679 mostravam "Chegou em" com o pacote em Cajamar). Chamado DEPOIS de
    gravar status/destino/rastreio da rodada; só quando o ML ainda não deu a
    perna da loja como entregue (`info.entregue_em`, que carimba a data real):

      - perna atual indo pro galpão: "entregue" ali (17track/Correios) é o
        Mercado Livre, não a loja;
      - o ML abriu a perna da loja com OUTRO código: o carimbo era do código
        anterior (o do galpão) — o rastreio novo começa do zero, como a
        localização;
      - perna da loja com código que NÃO é dos Correios (LF72…/K4VS…, da
        rede do ML): 17track/pull/push só sabem de código Correios, então o
        carimbo só pode ter vindo da perna anterior.

    Devolução direta (perna única pra loja, código Correios) fica de fora: aí
    o 17track/Correios é uma prova legítima. TikTok/Shopee: destino None →
    nunca."""
    if row.pacote_entregue_em is None or info.entregue_em is not None:
        return False
    if (info.fonte or "").strip().lower() != "ml":
        return False
    destino = (info.destino or "").strip().lower()
    if destino == logistica_rules.RETURN_DESTINO_GALPAO:
        return True
    if destino == logistica_rules.RETURN_DESTINO_LOJA:
        return codigo_novo or not logistica_track.is_correios(row.rastreio_auto or "")
    return False


def aplicar_push(dev: DevolucaoRastreio, loc: str, *, entregue: bool, agora: datetime) -> None:
    """Evento do 17track (push) no código do pacote de VOLTA: localização
    sempre; carimbo de chegada quando os Correios dizem ENTREGUE — a prova
    mais direta pra qualquer plataforma cujo retorno vá pelos Correios (todo o
    TikTok, parte da Shopee). Nunca apaga um carimbo já existente. Exceto a
    perna comprador → galpão do ML (revisão): "entregue" ali é Cajamar."""
    dev.localizacao_auto = loc
    dev.localizacao_auto_data = agora
    if entregue and dev.pacote_entregue_em is None and not _perna_do_galpao(dev):
        dev.pacote_entregue_em = agora


def _galpao_recebeu(row: DevolucaoRastreio) -> bool:
    """O ML já deu a perna do galpão como entregue (status da devolução
    `delivered`) ou o rastreio dela já disse "entregue": pacote em Cajamar."""
    return (row.devolucao_status_auto or "").strip().lower() == "delivered" or _texto_diz_entregue(
        row.localizacao_auto
    )


async def _puxar_correios(
    session: AsyncSession, *, vivos: Collection[str] | None = None
) -> dict[str, Any]:
    """Rede de segurança do rastreio do pacote de VOLTA.

    Até aqui a localização física só entrava pelo PUSH do 17track. Push é
    evento: se ele acontece antes de a gente passar a escutar (ou se perde),
    a linha fica parada pra sempre — foi o caso do 293437, entregue pelos
    Correios em 09/09 e ainda sem "Chegou em" (Eduardo, 10/09). Agora o sync
    também PERGUNTA o estado dos códigos já registrados. Consulta é grátis no
    17track (crédito só se gasta ao registrar), então roda a cada rodada.

    Devolve também `desconhecidos`: códigos que o 17track diz NÃO conhecer, de
    pedido em `vivos` (os que ainda estão em Aguardando Devolução; None = todos)
    — `run` registra esses de novo. Vinicius 17/09: o 291955 (AP436496123BR)
    e o 292357 entraram em 04/09, dia em que a conta do 17track estava sem
    saldo; o registro falhou uma vez, nunca foi refeito, e a Localização ficou
    13 dias parada no status do TikTok enquanto os Correios já mostravam o
    pacote a caminho.
    """
    linhas = list(
        (
            await session.execute(
                select(DevolucaoRastreio).where(
                    DevolucaoRastreio.rastreio_auto.is_not(None),
                    DevolucaoRastreio.pacote_entregue_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    resumo: dict[str, Any] = {
        "consultados": 0,
        "entregues": 0,
        "localizacoes": 0,
        "desconhecidos": [],
    }

    # Antes de perguntar: a prova pode já estar guardada aqui. O 17track APAGA
    # o número depois da entrega (consultar AP444879986BR hoje devolve "não
    # conhece"), então o evento que ele empurrou na época é a única cópia que
    # sobrou — e ela está em `localizacao_auto`. Foi o caso do 293437, que o
    # Eduardo apontou: entregue em 09/09, texto salvo, campo vazio.
    for row in linhas:
        if (
            _texto_diz_entregue(row.localizacao_auto)
            and row.pacote_entregue_em is None
            and not _perna_do_galpao(row)
        ):
            row.pacote_entregue_em = row.localizacao_auto_data or datetime.now(UTC)
            resumo["entregues"] += 1

    # Só quem ainda não chegou entra na consulta: um número que o 17track já
    # apagou (entregue) voltaria como "desconhecido" e seria registrado de
    # novo — 1 crédito à toa.
    por_codigo = {
        _num(r.rastreio_auto): r
        for r in linhas
        if r.pacote_entregue_em is None
        and logistica_track.is_correios(r.rastreio_auto or "")
        # Perna do galpão do ML que o próprio ML já deu como entregue: não há
        # mais o que perguntar aos Correios sobre esse código (a linha fica
        # sem carimbo de propósito e voltaria aqui toda rodada).
        and not (_perna_do_galpao(r) and _galpao_recebeu(r))
    }
    resumo["consultados"] = len(por_codigo)

    if not por_codigo:
        if resumo["entregues"]:
            await session.commit()
        return resumo
    try:
        got = await logistica_track.fetch_detalhado(sorted(por_codigo))
    except Exception as e:  # noqa: BLE001 — 17track fora do ar não derruba o sync
        logger.warning("devolucao_rastreio_pull_falhou", err=str(e)[:200])
        if resumo["entregues"]:
            await session.commit()
        return resumo
    agora = datetime.now(UTC)
    for numero, dados in (got.get("info") or {}).items():
        row = por_codigo.get(_num(numero))
        if row is None:
            continue
        loc = (dados.get("localizacao") or "").strip()
        if loc and loc != (row.localizacao_auto or ""):
            row.localizacao_auto = loc
            row.localizacao_auto_data = dados.get("sync_at") or agora
            resumo["localizacoes"] += 1
        # "Delivered" na perna do galpão do ML é Cajamar: a localização vale,
        # o carimbo de chegada não.
        if str(dados.get("status") or "").strip() == "Delivered" and not _perna_do_galpao(row):
            row.pacote_entregue_em = dados.get("sync_at") or agora
            resumo["entregues"] += 1
    vivos_set = {str(p) for p in vivos} if vivos is not None else None
    resumo["desconhecidos"] = sorted(
        {
            _num(n)
            for n in (got.get("desconhecidos") or [])
            if _num(n) in por_codigo
            and (vivos_set is None or por_codigo[_num(n)].pedido_bling in vivos_set)
        }
    )
    if resumo["entregues"] or resumo["localizacoes"]:
        await session.commit()
    return resumo


class _Extrato(NamedTuple):
    """O que o extrato financeiro JÁ BAIXADO (marketplace_order_financials) diz
    do pedido: reembolso descontado da loja e compensação paga pela Shopee."""

    plataforma: str
    reembolso: Decimal | None  # descontado do repasse (positivo)
    compensacao: Decimal | None  # Shopee pagou à loja (positivo)
    compensacao_em: datetime | None
    lido_em: datetime | None


def _dec(v: object) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _mais_recente(*datas: datetime | None) -> datetime | None:
    validas = [d for d in datas if d is not None]
    return max(validas) if validas else None


def _compensacao_shopee(raw: dict | None) -> tuple[Decimal | None, datetime | None]:
    """`order_income.order_adjustment[]` do escrow guardado em `raw`: ajuste com
    motivo "... Compensation" e valor positivo = a Shopee pagou a loja (medido
    17/09 pelo sync dos Chamados: `seller_compensation_status` do caso vem
    sempre vazio no BR; só o escrow mostra a compensação, com valor e data)."""
    esc = (raw or {}).get("escrow") if isinstance(raw, dict) else None
    if not isinstance(esc, dict):
        return None, None
    ajustes = esc.get("order_adjustment") or (esc.get("order_income") or {}).get("order_adjustment")
    total: Decimal | None = None
    quando: datetime | None = None
    for a in ajustes if isinstance(ajustes, list) else []:
        if not isinstance(a, dict):
            continue
        v = _dec(a.get("amount"))
        motivo = str(a.get("adjustment_reason") or "").lower()
        if v is None or v <= 0 or "compensation" not in motivo:
            continue
        total = (total or Decimal("0")) + v
        em = epoch_to_dt(a.get("date"))
        if em is not None and (quando is None or em > quando):
            quando = em
    return total, quando


async def _reembolsos_do_extrato(
    session: AsyncSession, pedidos: Collection[str]
) -> dict[str, _Extrato]:
    """Por pedido, o que o extrato já baixado diz. Pedido com mais de uma linha
    (pack/refund parcial) soma. Não chama API nenhuma: é o escrow/statement que
    o financeiro do marketplace já guarda e re-lê por conta própria."""
    if not pedidos:
        return {}
    rows = (
        await session.execute(
            select(MarketplaceOrderFinancial).where(
                MarketplaceOrderFinancial.pedido_bling.in_(list(pedidos))
            )
        )
    ).scalars().all()
    out: dict[str, _Extrato] = {}
    for f in rows:
        pedido = f.pedido_bling or ""
        reembolso = abs(f.refund_amount) if f.refund_amount else None
        comp, comp_em = _compensacao_shopee(f.raw)
        cur = out.get(pedido)
        plat = str(getattr(f.platform, "value", f.platform) or "")
        if cur is None:
            out[pedido] = _Extrato(plat, reembolso, comp, comp_em, f.fetched_at)
            continue
        out[pedido] = _Extrato(
            cur.plataforma or plat,
            (cur.reembolso or Decimal("0")) + reembolso if reembolso else cur.reembolso,
            (cur.compensacao or Decimal("0")) + comp if comp else cur.compensacao,
            _mais_recente(cur.compensacao_em, comp_em),
            _mais_recente(cur.lido_em, f.fetched_at),
        )
    return out


_PLATAFORMA_EXTRATO_PT = {"shopee": "Shopee", "tiktok": "TikTok", "ml": "Mercado Livre"}


def _aplicar_reembolso(
    row: DevolucaoRastreio, info: ReturnInfo | None, ext: _Extrato | None
) -> None:
    """Coluna "Reembolso": o caso no marketplace diz se a plataforma pagou o
    cliente; o extrato diz se DESCONTOU da loja (e se a Shopee compensou).
    Regra: compensação paga → nada saiu do nosso (Não); desconto no extrato →
    Sim com o valor do extrato; senão vale o que o caso disse. Sem informação
    nova a linha fica como estava (não apaga o que já se sabia)."""
    pago = info.reembolso if info is not None else None
    valor = info.reembolso_valor if info is not None else None
    em = info.reembolso_em if info is not None else None
    detalhe = info.reembolso_detalhe if info is not None else None
    if ext is not None and ext.compensacao and ext.compensacao > 0:
        pago = False
        valor = valor or ext.reembolso
        em = em or ext.compensacao_em
        detalhe = f"Shopee compensou a loja em R$ {ext.compensacao:.2f} — o nosso valor ficou"
    elif ext is not None and ext.reembolso and ext.reembolso > 0:
        # O extrato manda sobre o caso: desconto lançado = saiu do nosso, mesmo
        # que o caso ainda apareça em análise (Shopee fecha o caso depois).
        pago = True
        valor = ext.reembolso
        em = em or ext.lido_em
        plat = _PLATAFORMA_EXTRATO_PT.get(ext.plataforma, ext.plataforma)
        detalhe = detalhe or f"Reembolso descontado no extrato ({plat})"
    if pago is None and valor is None:
        return
    row.reembolso_auto = pago
    row.reembolso_valor_auto = valor
    row.reembolso_em_auto = em
    row.reembolso_detalhe_auto = detalhe


async def run(session: AsyncSession, *, pedidos: Collection[str] | None = None) -> dict[str, Any]:
    """Sincroniza o rastreio automático das devoluções. `pedidos` restringe
    (o recarregar de um pedido); sem ele, todos os 83957."""
    alvo = list(pedidos) if pedidos is not None else await pedidos_em_devolucao(session)
    linhas = await _linhas_logistica(session, alvo)
    por_mk: dict[str, list[Logistica]] = {}
    for linha in linhas.values():
        k = _plataforma_key(linha.plataforma)
        if k:
            por_mk.setdefault(k, []).append(linha)

    # Devolução já marcada como entregue não precisa da consulta extra de
    # detalhe a cada rodada — o dado não volta atrás. EXCETO quando o prazo
    # de resposta da loja pode existir, que só vem no detalhe: nos primeiros
    # dias depois da entrega (Shopee: 3 dias corridos pra conferir), enquanto
    # o caso estiver em análise/disputa (a Shopee pode pedir evidência) ou
    # enquanto houver prazo recente gravado.
    agora_ = datetime.now(UTC)
    entregues_rows = (
        await session.execute(
            select(
                DevolucaoRastreio.pedido_bling,
                DevolucaoRastreio.pacote_entregue_em,
                DevolucaoRastreio.devolucao_status_auto,
                DevolucaoRastreio.prazo_acao_auto,
            ).where(DevolucaoRastreio.pacote_entregue_em.is_not(None))
        )
    ).all()
    ja_entregues = {r.pedido_bling for r in entregues_rows}
    reconsultar = {
        r.pedido_bling
        for r in entregues_rows
        if r.pacote_entregue_em >= agora_ - timedelta(days=DIAS_DETALHE_APOS_ENTREGA)
        or (r.devolucao_status_auto or "").strip().upper() in STATUS_COM_DETALHE
        or (r.prazo_acao_auto is not None and r.prazo_acao_auto >= agora_ - timedelta(days=1))
    }

    infos: dict[str, ReturnInfo] = {}
    for key, ls in por_mk.items():
        got = await _fetch_por_marketplace(session, key, ls, ja_entregues, reconsultar)
        for pedido, info in got.items():
            if isinstance(info, ReturnInfo) and pedido:
                infos[str(pedido)] = info

    agora = datetime.now(UTC)
    gravados = 0
    novos_codigos: list[str] = []
    # Extrato financeiro já baixado: quem diz se o reembolso foi DESCONTADO da
    # loja (Shopee/TikTok) e se a Shopee compensou. Pedido sem caso conhecido
    # mas com desconto no extrato (pacote voltando pela SPX, cancelamento)
    # também ganha linha — a coluna "Reembolso" precisa dele.
    extratos = await _reembolsos_do_extrato(session, alvo)
    com_extrato = {p for p, e in extratos.items() if (e.reembolso or e.compensacao)}
    gravar = set(infos) | (com_extrato & {str(p) for p in alvo})
    reembolsos = 0
    if gravar:
        existentes = {
            r.pedido_bling: r
            for r in (
                await session.execute(
                    select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_(list(gravar)))
                )
            ).scalars().all()
        }
        for pedido in gravar - set(infos):
            row = existentes.get(pedido)
            if row is None:
                row = DevolucaoRastreio(pedido_bling=pedido)
                session.add(row)
            _aplicar_reembolso(row, None, extratos.get(pedido))
            reembolsos += 1 if row.reembolso_auto else 0
        for pedido, info in infos.items():
            row = existentes.get(pedido)
            if row is None:
                row = DevolucaoRastreio(pedido_bling=pedido)
                session.add(row)
            _aplicar_reembolso(row, info, extratos.get(pedido))
            reembolsos += 1 if row.reembolso_auto else 0
            tracking = (info.tracking or "").strip() or None
            codigo_novo = bool(tracking and tracking != row.rastreio_auto)
            if codigo_novo:
                # Código novo → localização anterior (de outro código) não vale mais.
                row.localizacao_auto = None
                row.localizacao_auto_data = None
                if logistica_track.is_correios(tracking):
                    novos_codigos.append(_num(tracking))
            row.rastreio_auto = tracking
            row.transportadora_auto = (info.carrier or "").strip() or None
            row.devolucao_status_auto = (info.status or "").strip() or None
            row.devolucao_tipo_auto = (info.return_type or "").strip() or None
            row.devolucao_destino_auto = (info.destino or "").strip().lower() or None
            if _carimbo_do_galpao(row, info, codigo_novo=codigo_novo):
                row.pacote_entregue_em = None
            # Prazo de resposta da loja (0286): reescrito sempre — quando a
            # plataforma para de pedir ação, o prazo some da tela. Exceto
            # quando o marketplace NÃO respondeu a consulta de onde o prazo sai
            # (detalhe com erro): fica o da rodada anterior.
            if not info.prazo_desconhecido:
                row.acao_auto = (info.acao_pendente or "").strip() or None
                row.prazo_acao_auto = info.prazo_acao
            row.devolucao_id_auto = (info.return_id or "").strip() or None
            row.fonte_auto = info.fonte
            if info.created_at:
                row.devolucao_criada_em = info.created_at
            # A "última mexida" só anda pra frente: numa rodada em que a
            # Shopee não é reconsultada (pacote já chegou) o carimbo do caso
            # não pode voltar pra trás do evento da SPX gravado antes.
            if info.updated_at and (
                row.devolucao_atualizada_em is None
                or info.updated_at > row.devolucao_atualizada_em
            ):
                row.devolucao_atualizada_em = info.updated_at
            # Só carimba a chegada; nunca apaga (a plataforma pode parar de
            # informar numa rodada e a data não pode sumir da tela).
            if info.entregue_em and row.pacote_entregue_em is None:
                row.pacote_entregue_em = info.entregue_em
            # Localização vinda do PRÓPRIO marketplace (Shopee: pacote voltando
            # pela SPX da ida). Só quando ele informa — senão quem manda na
            # localização continua sendo o 17track.
            loc = (info.localizacao or "").strip() or None
            if loc and loc != row.localizacao_auto:
                row.localizacao_auto = loc
                row.localizacao_auto_data = info.localizacao_em or agora
            row.auto_sync_at = agora
            gravados += 1
    # Commit SEMPRE (mesmo sem devolução nova): o refresh de token dos clients
    # (TikTok/Shopee/ML) só dá flush — sem commit o token rotacionado se perde.
    await session.commit()

    pull = await _puxar_correios(session, vivos=alvo)

    # Registra no 17track os códigos novos desta rodada + os que ele disse não
    # conhecer (registro anterior falhou por saldo/rede, ou o 17track apagou).
    # Só o que o 17track CONFIRMOU conta como registrado; o resto volta na
    # rodada seguinte pelo pull — antes o registro era tentado uma única vez,
    # sem olhar a resposta, e um "sem saldo" naquele minuto deixava o pacote
    # sem localização pra sempre (291955/292357, 04/09).
    pendentes = sorted(set(novos_codigos) | set(pull["desconhecidos"]))
    presos = await logistica_track_sync.em_quarentena(pendentes)
    if presos:
        pendentes = [n for n in pendentes if n not in presos]
    registrados = 0
    sem_quota = False
    if pendentes:
        try:
            res = await logistica_track.register(pendentes)
        except Exception as e:  # noqa: BLE001 — 17track fora do ar não derruba o sync
            logger.warning("devolucao_rastreio_sync_17track_falhou", err=str(e)[:200])
            res = {"ok": [], "sem_quota": False}
        ok = {_num(n) for n in (res.get("ok") or [])}
        sem_quota = bool(res.get("sem_quota"))
        # Mesma conta da Logística: o aviso de saldo esgotado da tela vale
        # pros dois fluxos.
        await logistica_track_sync.marcar_sem_quota(sem_quota)
        registrados = len(ok)
        recusados = [n for n in pendentes if n not in ok]
        if recusados and not sem_quota:
            # Recusa que NÃO é saldo = problema do próprio número (formato,
            # transportadora). Um dia de quarentena, senão volta a cada 30 min.
            await logistica_track_sync.por_de_quarentena(recusados)
            logger.warning(
                "devolucao_rastreio_sync_17track_recusou", numeros=recusados[:10], n=len(recusados)
            )
        if sem_quota:
            logger.warning(
                "devolucao_rastreio_sync_17track_sem_quota",
                pendentes=len(pendentes),
                mensagem=(
                    "17track sem saldo — a Localização do pacote de volta não atualiza "
                    "até recarregar"
                ),
            )

    # Prazos de resposta acabaram de ser atualizados: hora de avisar quem
    # precisa responder na plataforma (Threema, um aviso por caso e prazo).
    # Best-effort: Threema fora do ar/sem cadastro não derruba o sync.
    avisos: dict[str, int] = {}
    try:
        from app.services import devolucao_acao_avisos

        # Só os pedidos cujo caso foi relido AGORA: conta que deu 429 fica com o
        # prazo da rodada anterior na tela, mas não rende aviso com dado velho.
        avisos = await devolucao_acao_avisos.run(
            session,
            pedidos=[p for p in alvo if str(p) in infos and not infos[str(p)].prazo_desconhecido],
            linhas=linhas,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("devolucao_acao_avisos_falhou", err=str(e)[:200])

    summary = {
        "pedidos": len(alvo),
        "com_logistica": len(linhas),
        "devolucoes": len(infos),
        "gravados": gravados,
        "reembolsos": reembolsos,
        "avisos_prazo": avisos.get("enviados", 0),
        "codigos_17track": registrados,
        "codigos_17track_pendentes": max(0, len(pendentes) - registrados),
        "sem_quota": sem_quota,
        "correios_consultados": pull["consultados"],
        "correios_entregues": pull["entregues"],
        "correios_localizacoes": pull["localizacoes"],
    }
    logger.info("devolucao_rastreio_sync_done", **summary)
    return summary
