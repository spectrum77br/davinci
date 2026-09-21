"""Preenche a assinatura de status do TikTok (`Logistica.meli_status`) + rastreio
+ localização física puxando das APIs 202309 do TikTok Shop.

Espelha o `logistica_shopee.py`. O TikTok tem vocabulário PRÓPRIO e enxuto: o
sinal de pós-venda que importa pro fluxo já vem no `status` do pedido (Order API
202309). Então a assinatura do TikTok é um único campo:

    {"order_status": "IN_TRANSIT" | "DELIVERED" | "CANCELLED" | ...}

renderizado em PT por `logistica_rules.assinatura_tiktok`. O rastreio vem do
`tracking_number` do pedido e a localização física dos eventos de tracking
(Fulfillment API — a `description` do último evento, que a API só dá em inglês
e `logistica_rules.tiktok_localizacao_pt` traduz; Vinicius, 21/09/2026).

Só se aplica a pedidos TikTok. Best-effort: pedido que o TikTok não devolve fica
sem status (não derruba o lote).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

_TIKTOK_PLATAFORMAS = logistica_rules._TIKTOK_PLATAFORMAS
# Campos da assinatura que só o sweep de pós-venda escreve (returns API):
# o status do caso e o TIPO (devolução + reembolso × só reembolso × troca).
_CAMPOS_DO_SWEEP = ("return_status", "return_type")


def _tiktok_localizacao(track: dict) -> str | None:
    """Evento de tracking de maior `update_time_millis` — o local/estágio
    físico mais recente que o TikTok expõe — traduzido pra PT. O `action_code`
    vai junto como fallback de família pra texto que a tabela não conhece."""
    eventos = track.get("tracking") or []
    if not eventos:
        return None
    top = max(eventos, key=lambda e: (e or {}).get("update_time_millis") or 0) or {}
    try:
        code: int | None = int(top.get("action_code"))
    except (TypeError, ValueError):
        code = None
    return logistica_rules.tiktok_localizacao_pt(top.get("description"), code)


def _tiktok_destino(order: dict) -> str | None:
    """Destino do pedido a partir de `recipient_address.district_info` (níveis
    do endereço). Fallback de localização quando ainda não há eventos de
    tracking. Junta os nomes não-vazios (mais específico por último)."""
    addr = order.get("recipient_address") or {}
    districts = addr.get("district_info") or []
    nomes = [((d or {}).get("address_name") or "").strip() for d in districts]
    nomes = [n for n in nomes if n]
    if not nomes:
        return None
    return " - ".join(nomes)


async def build_enrichment(client: TikTokClient, order_id: str) -> dict:
    """Monta a assinatura do TikTok + rastreio + localização física.

    Retorna `{"meli_status": {"order_status": ...} | {}, "rastreio": str | None,
    "localizacao": str | None, "datas": {...}}`. Campos que o TikTok não
    devolver ficam de fora / None.

    `datas` = quando o status mudou: o `update_time` do pedido (epoch), que a
    TikTok mexe a cada mudança de estado."""
    order_id = str(order_id)
    order = await client.get_order_detail(order_id)
    st = (order.get("status") or "").strip().upper()
    meli: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    if st:
        meli["order_status"] = st
        logistica_datas.propor(
            datas, "order_status", order.get("update_time"), logistica_datas.FONTE_PLATAFORMA
        )

    rastreio = (order.get("tracking_number") or "").strip() or None

    track = await client.get_tracking(order_id)
    localizacao = _tiktok_localizacao(track)
    if not localizacao:
        localizacao = _tiktok_destino(order)

    return {
        "meli_status": meli,
        "rastreio": rastreio,
        "localizacao": localizacao,
        "datas": {f: datas[f] for f in meli if f in datas},
    }


async def _tiktok_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração TikTok cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.TIKTOK)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_tiktok_client(
    session: AsyncSession,
    integration: Integration,
    *,
    lock: asyncio.Lock | None = None,
) -> TikTokClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("token_expires_at") or new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        # Único acesso ao banco durante a rajada concorrente do enrich_recent —
        # serializado pelo lock (sessão async não aceita flush simultâneo).
        async with (lock or logistica_enrich.NOLOCK):
            await session.flush()

    return TikTokClient(creds, on_token_refresh=_persist)


class TikTokEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha TikTok (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, TikTokClient] | None = None,
    reler_devolucao: bool = False,
) -> bool:
    """Preenche `row.meli_status`/rastreio/localizacao puxando do TikTok. Retorna
    True se atualizou.

    `reler_devolucao=True` (botão ⟳ do painel): se a assinatura diz que há
    devolução VIVA, consulta também o returns/search desse pedido e atualiza
    `return_status`/`return_type` — senão o recarregar manual re-lia o pedido
    e mantinha a devolução velha ("lido há 2 min" e ainda "Devolução
    solicitada" com o caso já encerrado no TikTok; 585358874025494337, 21/09).
    Fica desligado nos lotes automáticos de propósito: uma chamada a mais por
    linha viraria rajada (429) — lá quem re-lê é o `sweep_pos_venda`.

    Levanta `TikTokEnrichError` com código quando não dá pra prosseguir (linha
    não-TikTok, sem pedido de marketplace, conta sem integração TikTok)."""
    if (row.plataforma or "").strip().lower() not in _TIKTOK_PLATAFORMAS:
        raise TikTokEnrichError("logistica_nao_tiktok")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise TikTokEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: TikTokClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _tiktok_integration_for_conta(session, conta)
        if integ is None:
            raise TikTokEnrichError("logistica_sem_integracao")
        client = _build_tiktok_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_id)
    meli = enr["meli_status"]
    # `return_status`/`return_type` vêm do sweep de pós-venda (returns API),
    # não do build_enrichment — preserva no re-enrich, senão a devolução
    # detectada sumiria da assinatura no tick seguinte e a regra regrediria.
    for campo in _CAMPOS_DO_SWEEP:
        val = (row.meli_status or {}).get(campo)
        if val:
            meli = {**meli, campo: val}
    datas = enr.get("datas")
    if reler_devolucao and _devolucao_viva(row.meli_status):
        d = await _caso_do_pedido(client, order_id)
        if d is not None:
            meli = {**meli, **{k: v for k, v in _sinal_do_caso(d).items() if v}}
            datas = dict(datas or {})
            logistica_datas.propor(
                datas, "return_status", d.get("update_time"),
                logistica_datas.FONTE_PLATAFORMA,
            )
    # Antes de trocar o status: o carimbo compara o valor velho com o novo.
    row.status_datas = logistica_datas.aplicar(row, meli, datas)
    row.meli_status = meli
    row.status_lido_em = datetime.now(UTC)
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    # Envio por Correios com evento real do 17track (`localizacao_at`) não é
    # sobrescrito pelo proxy da plataforma — o físico é melhor que a estimativa.
    if enr.get("localizacao") and not (
        logistica_track.is_correios(row.rastreio) and row.localizacao_at
    ):
        row.localizacao = enr["localizacao"]
    # Divergência TikTok: order_status comercial × último evento físico.
    row.divergencia = logistica_rules.detectar_divergencia_tiktok(
        row.meli_status, row.localizacao
    )
    return True


# Janela do sweep de pós-venda: linhas TikTok com `data` (data do pedido) até 45
# dias atrás — cobre com folga o prazo de devolução. ~750 linhas ≈ 16 chamadas
# em lote por varredura (medido 28/08). Mesma mecânica do sweep da Shopee.
_SWEEP_JANELA_DIAS = 45
# Janela das devoluções (paridade com a Shopee, que limita em 15 dias).
_RETURNS_JANELA_DIAS = 15


async def sweep_pos_venda(session: AsyncSession) -> dict:
    """Re-olha TODAS as linhas TikTok da janela — inclusive as resolvidas que o
    painel esconde — e devolve os ids cuja situação de pós-venda MUDOU.

    Espelho do `logistica_shopee.sweep_pos_venda` (mesmo ponto cego: linha
    escondida nunca mais era re-consultada — 16 pedidos ficaram "Em trânsito"
    no painel dias depois de entregues, e devolução pós-entrega era invisível;
    caso real 28/08, pedidos 585411441781475242/585612645547804469/
    585673041600415018 + 15 pra "Entregue").

    Duas fontes, ambas em lote e por conta:
    - `get_order_status_map` (50 pedidos/chamada): status vivo mudou
      (DELIVERED, COMPLETED, ...) → atualiza meli_status + carimbo de data;
    - `get_return_list` (returns/search): devolução que o order_status do
      TikTok NEM TEM como mostrar → grava `meli_status["return_status"]` +
      `["return_type"]`; `assinatura_tiktok` então rende "Devolução
      solicitada" / "Reembolso solicitado" / etc. Havendo mais de um caso pro
      mesmo pedido, vale o VIVO mais recente (`_melhor_devolucao_por_pedido`).

    Fora da janela de 45 dias (mesmo furo da Shopee, fechado em 19/09):
    - linha velha cuja assinatura ainda diz devolução VIVA entra na varredura
      de qualquer jeito e o caso é re-lido POR PEDIDO (sem filtro de data) —
      senão o encerramento que a TikTok fizer depois dos 45 dias nunca chega
      (real 21/09: 585358874025494337 / 288403, venda de 03/08, 3ª devolução
      cancelada em 19/09 por atraso do cliente; o painel seguiu "Devolução
      solicitada" e o Bling em Aguardando Devolução — e a faxina segura a
      linha pra sempre por causa disso);
    - a lista da loja casa com linha de QUALQUER idade, e venda viva sem linha
      nenhuma (saiu da aba por Entregue > 90 dias, ou nunca entrou) é recriada
      do espelho do Bling (`logistica_ingest.recriar_linhas_do_bling`). Só
      caso VIVO recria linha: devolução cancelada de venda velha não tem o
      que acompanhar.

    Retorna {"ids": [UUID...], **contadores}. O recarregar passa os ids como
    `extras` do `_ids_pendentes` — extras furam o escondimento — e o fluxo
    normal re-enriquece a linha e aplica a regra de status no Bling."""
    corte = datetime.now(UTC).date() - timedelta(days=_SWEEP_JANELA_DIAS)
    rows = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(_TIKTOK_PLATAFORMAS)
                ),
                func.coalesce(Logistica.pedido_marketplace, "") != "",
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()
    velhas_vivas = await _linhas_velhas_com_devolucao_viva(session, corte)

    por_conta: dict[str, list[Logistica]] = {}
    for r in rows:
        por_conta.setdefault((r.conta or "").strip(), []).append(r)
    velhas_por_conta: dict[str, list[Logistica]] = {}
    for r in velhas_vivas:
        velhas_por_conta.setdefault((r.conta or "").strip(), []).append(r)

    mudados: set[UUID] = set()
    n_status = n_returns = contas_ok = n_recriadas = n_velhas = 0
    agora = int(datetime.now(UTC).timestamp())
    ret_from = agora - _RETURNS_JANELA_DIAS * 24 * 3600 + 300
    for conta in list(dict.fromkeys([*por_conta, *velhas_por_conta])):
        linhas = por_conta.get(conta, [])
        velhas = velhas_por_conta.get(conta, [])
        integ = await _tiktok_integration_for_conta(session, conta)
        if integ is None:
            continue
        client = _build_tiktok_client(session, integ)
        contas_ok += 1

        # 1) status vivo do pedido, em lotes de 50 — as velhas com devolução
        # viva vão no mesmo lote (poucas; sai de graça).
        linhas = [*linhas, *velhas]
        oids = [(r.pedido_marketplace or "").strip() for r in linhas]
        smap = await client.get_order_status_map(oids)
        for r in linhas:
            info = smap.get((r.pedido_marketplace or "").strip()) or {}
            st = (info.get("status") or "").strip().upper()
            atual = ((r.meli_status or {}).get("order_status") or "").strip().upper()
            if st and st != atual:
                meli = dict(r.meli_status or {})
                meli["order_status"] = st
                datas: dict[str, dict[str, str]] = {}
                logistica_datas.propor(
                    datas, "order_status", info.get("update_time"),
                    logistica_datas.FONTE_PLATAFORMA,
                )
                r.status_datas = logistica_datas.aplicar(r, meli, datas)
                r.meli_status = meli
                r.status_lido_em = datetime.now(UTC)
                mudados.add(r.id)
                n_status += 1

        # 2) devoluções da loja nos últimos 15 dias (por update_time).
        try:
            devolucoes = await client.get_return_list(
                update_time_from=ret_from, update_time_to=agora
            )
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_tiktok_sweep_returns_falhou",
                conta=conta, err=str(e)[:200],
            )
            devolucoes = []
        # 2b) as velhas com devolução viva: o caso inteiro POR PEDIDO, sem
        # filtro de data — encerramento de semanas atrás não cai na janela.
        if velhas:
            n_velhas += len(velhas)
            try:
                devolucoes = [
                    *devolucoes,
                    *await client.get_return_list(
                        order_ids=[(r.pedido_marketplace or "").strip() for r in velhas]
                    ),
                ]
            except Exception as e:  # noqa: BLE001 — best-effort por conta
                logger.warning(
                    "logistica_tiktok_sweep_returns_velhas_falhou",
                    conta=conta, pedidos=len(velhas), err=str(e)[:200],
                )
        melhor = _melhor_devolucao_por_pedido(devolucoes)
        # 2c) caso da lista da loja sem linha carregada: casa com a linha velha
        # que ainda existir (qualquer idade) e recria, do espelho do Bling, a
        # de venda viva que já saiu da aba.
        alvo_ret = list(linhas)
        faltam = set(melhor) - {(r.pedido_marketplace or "").strip() for r in linhas}
        if faltam:
            outras = await _linhas_tiktok_por_venda(session, faltam)
            alvo_ret.extend(outras)
            faltam -= {(r.pedido_marketplace or "").strip() for r in outras}
        vivas_sem_linha = [oid for oid in faltam if _caso_vivo(melhor[oid])]
        if vivas_sem_linha:
            from app.services import logistica_ingest  # lazy: evita ciclo de import

            novas = await logistica_ingest.recriar_linhas_do_bling(
                session, "tiktok", vivas_sem_linha
            )
            alvo_ret.extend(novas)
            n_recriadas += len(novas)
        for r in alvo_ret:
            d = melhor.get((r.pedido_marketplace or "").strip())
            if d is None:
                continue
            novo = _sinal_do_caso(d)
            if not novo.get("return_status"):
                continue
            meli_atual = r.meli_status or {}
            atual = {
                campo: (meli_atual.get(campo) or "").strip().upper()
                for campo in _CAMPOS_DO_SWEEP
            }
            if novo != atual:
                meli = {k: v for k, v in meli_atual.items() if k not in _CAMPOS_DO_SWEEP}
                meli.update({k: v for k, v in novo.items() if v})
                # Carimba a data do caso (update_time do TikTok) — é a "última
                # movimentação"; sem isso o carimbo ficava no dia em que o
                # sweep viu a 1ª devolução.
                datas_ret: dict[str, dict[str, str]] = {}
                logistica_datas.propor(
                    datas_ret, "return_status", d.get("update_time"),
                    logistica_datas.FONTE_PLATAFORMA,
                )
                r.status_datas = logistica_datas.aplicar(r, meli, datas_ret)
                r.meli_status = meli
                r.status_lido_em = datetime.now(UTC)
                mudados.add(r.id)
                n_returns += 1

        # Salva por conta (mesma razão do sweep Shopee): deploy no meio da
        # varredura não joga fora o que já foi lido.
        await session.commit()

    await session.commit()
    summary = {
        "seen": len(rows), "contas": contas_ok,
        "order_status": n_status, "returns": n_returns,
        "velhas_vivas": n_velhas, "recriadas": n_recriadas,
    }
    logger.info("logistica_tiktok_sweep_pos_venda", **summary)
    return {"ids": list(mudados), **summary}


def _devolucao_viva(meli_status: dict | None) -> bool:
    """A assinatura da linha diz que há caso de devolução ainda aberto (nem
    cancelado/recusado, nem concluído)? É o que ainda pode mudar no TikTok e
    o que a faxina da Logística segura."""
    st = ((meli_status or {}).get("return_status") or "").strip().upper()
    return bool(st) and st not in logistica_rules.RETURN_ENCERRADO


def _caso_vivo(d: dict) -> bool:
    """Caso do returns/search ainda em aberto (mesmo critério de "vivo" do
    `_melhor_devolucao_por_pedido`)."""
    st = str(d.get("return_status") or "").strip().upper()
    return bool(st) and st not in logistica_rules._TIKTOK_RETURN_ENCERRADO


async def _caso_do_pedido(client: TikTokClient, order_id: str) -> dict | None:
    """O caso de devolução que vale pro pedido (vivo mais recente; sem vivo, o
    mais recente) direto do returns/search por pedido. None sem caso ou com a
    API fora (best-effort: loga e segue)."""
    try:
        devolucoes = await client.get_return_list(order_ids=[order_id])
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "logistica_tiktok_caso_do_pedido_falhou", pedido=order_id, err=str(e)[:200]
        )
        return None
    return _melhor_devolucao_por_pedido(devolucoes).get(order_id)


async def _linhas_velhas_com_devolucao_viva(
    session: AsyncSession, corte: date
) -> list[Logistica]:
    """Linhas TikTok com data de venda ANTES do corte do sweep cuja assinatura
    ainda diz devolução viva — as que o sweep deixou de olhar e que só o
    TikTok pode encerrar."""
    ret = func.upper(func.coalesce(Logistica.meli_status["return_status"].astext, ""))
    return list(
        (
            await session.execute(
                select(Logistica).where(
                    func.lower(func.trim(Logistica.plataforma)).in_(
                        tuple(_TIKTOK_PLATAFORMAS)
                    ),
                    func.coalesce(Logistica.pedido_marketplace, "") != "",
                    Logistica.data < corte,
                    ret != "",
                    ret.notin_(sorted(logistica_rules.RETURN_ENCERRADO)),
                )
            )
        ).scalars().all()
    )


async def _linhas_tiktok_por_venda(
    session: AsyncSession, vendas: Collection[str]
) -> list[Logistica]:
    """Linhas TikTok (qualquer idade) das vendas dadas — número do pedido no
    marketplace. Casa a devolução da lista da loja com a linha que o sweep não
    carregou por estar fora da janela de 45 dias."""
    if not vendas:
        return []
    return list(
        (
            await session.execute(
                select(Logistica).where(
                    func.lower(func.trim(Logistica.plataforma)).in_(
                        tuple(_TIKTOK_PLATAFORMAS)
                    ),
                    Logistica.pedido_marketplace.in_(sorted(vendas)),
                )
            )
        ).scalars().all()
    )


# ---- devolução: o pacote que VOLTA (aba Acompanhamento de Devoluções) --------


def _epoch_int(v: object) -> int:
    """Epoch cru do payload (int/str) → int; 0 se ilegível/ausente."""
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _sinal_do_caso(d: dict) -> dict[str, str]:
    """O que o sweep grava na assinatura a partir de um caso do returns/search:
    `{"return_status": ..., "return_type": ...}` (maiúsculas; ausente → "")."""
    return {
        campo: str(d.get(campo) or "").strip().upper() for campo in _CAMPOS_DO_SWEEP
    }


def _acao_pendente(d: dict) -> tuple[str | None, datetime | None]:
    """A ação que a TikTok espera da loja neste caso e o prazo mais próximo:
    `seller_next_action_response: [{action, deadline(epoch s)}]`. Havendo mais
    de uma, vale a de prazo mais curto. Nunca levanta (entrada suja → None):
    o sync do retorno trata TypeError como "fetcher sem kwarg" e refaria a
    chamada inteira."""
    melhor: tuple[str | None, datetime | None] = (None, None)
    for a in (d.get("seller_next_action_response") or []) if isinstance(d, dict) else []:
        if not isinstance(a, dict):
            continue
        prazo = epoch_to_dt(a.get("deadline"))
        if prazo is None:
            continue
        if melhor[1] is None or prazo < melhor[1]:
            melhor = (str(a.get("action") or "").strip().upper() or None, prazo)
    return melhor


def _acao_pendente_do_pedido(casos: Iterable[dict]) -> tuple[str | None, datetime | None]:
    """Entre os casos VIVOS do pedido (fora de encerrado/concluído), a ação
    com o prazo mais curto — cada caso tem o próprio prazo."""
    melhor: tuple[str | None, datetime | None] = (None, None)
    for d in casos:
        if not isinstance(d, dict):
            continue
        st = str(d.get("return_status") or "").strip().upper()
        if st in logistica_rules._TIKTOK_RETURN_ENCERRADO or st in logistica_rules._TIKTOK_RETURN_CONCLUIDO:
            continue
        acao, prazo = _acao_pendente(d)
        if prazo is not None and (melhor[1] is None or prazo < melhor[1]):
            melhor = (acao, prazo)
    return melhor


def _valor(v: object) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _reembolso_do_pedido(
    casos: Iterable[dict],
) -> tuple[bool, Decimal | None, datetime | None, str | None]:
    """Já saiu dinheiro do nosso pra este pedido? Olha TODOS os casos do pedido
    (um cancelado + um concluído é comum: 294865): caso em
    `_TIKTOK_RETURN_CONCLUIDO` = a TikTok pagou o reembolso ao cliente e
    desconta da loja no repasse. Soma `refund_amount.refund_total` dos
    concluídos; data = `update_time` mais recente deles. Sem concluído →
    (False, None, None, None)."""
    total: Decimal | None = None
    quando: datetime | None = None
    n = 0
    for d in casos:
        if not isinstance(d, dict):
            continue
        st = str(d.get("return_status") or "").strip().upper()
        if st not in logistica_rules._TIKTOK_RETURN_CONCLUIDO:
            continue
        n += 1
        ra = d.get("refund_amount") if isinstance(d.get("refund_amount"), dict) else {}
        v = _valor(ra.get("refund_total"))
        if v is not None:
            total = (total or Decimal("0")) + v
        em = epoch_to_dt(d.get("update_time"))
        if em is not None and (quando is None or em > quando):
            quando = em
    if not n:
        return False, None, None, None
    return True, total, quando, "Caso concluído no TikTok — reembolso pago ao cliente"


def _tiktok_return_info(d: dict) -> ReturnInfo:
    """Caso do returns/search → `ReturnInfo`. Devolução só-reembolso (return_type
    REFUND) não tem pacote: entra mesmo assim, com tracking None — e o tipo vai
    junto pra aba Acompanhamento não chamar de devolução. A ação pendente da
    loja + prazo vêm no mesmo payload (custo zero de API)."""
    sinal = _sinal_do_caso(d)
    acao, prazo = _acao_pendente(d)
    return ReturnInfo(
        fonte="tiktok",
        status=sinal["return_status"] or None,
        tracking=str(d.get("return_tracking_number") or "").strip() or None,
        carrier=str(d.get("return_provider_name") or "").strip() or None,
        created_at=epoch_to_dt(d.get("create_time")),
        updated_at=epoch_to_dt(d.get("update_time")),
        return_id=str(d.get("return_id") or "").strip() or None,
        return_type=sinal["return_type"] or None,
        acao_pendente=acao,
        prazo_acao=prazo,
    )


def _melhor_devolucao_por_pedido(devolucoes: Iterable[dict]) -> dict[str, dict]:
    """{order_id: caso} — havendo mais de um caso pro mesmo pedido vale o VIVO
    (fora de `_TIKTOK_RETURN_ENCERRADO`) mais recente; sem vivo, o mais recente.
    "Recente" = `update_time` (fallback `create_time`). Serve o `sweep_pos_venda`
    (que grava status + tipo na assinatura) e o `returns_por_pedido` (que
    guarda o caso inteiro: rastreio, transportadora)."""
    melhor: dict[str, tuple[tuple[bool, int], dict]] = {}
    for d in devolucoes:
        if not isinstance(d, dict):
            continue
        oid = str(d.get("order_id") or "").strip()
        if not oid:
            continue
        st = str(d.get("return_status") or "").strip().upper()
        vivo = bool(st) and st not in logistica_rules._TIKTOK_RETURN_ENCERRADO
        quando = _epoch_int(d.get("update_time")) or _epoch_int(d.get("create_time"))
        chave = (vivo, quando)
        if oid not in melhor or chave > melhor[oid][0]:
            melhor[oid] = (chave, d)
    return {oid: d for oid, (_, d) in melhor.items()}


async def returns_por_pedido(
    session: AsyncSession, linhas: list[Logistica]
) -> dict[str, ReturnInfo]:
    """Devolução conhecida no TikTok pra cada linha da Logística recebida:
    `{pedido_bling: ReturnInfo}` — pedido sem caso de devolução fica FORA do
    dict (ausente = desconhecido). Contrato em `services/devolucao_returns`.

    Eduardo (03/09): a aba Acompanhamento mostrava o rastreio da ENTREGA; o que
    interessa é o pacote que VOLTA (`return_tracking_number` +
    `return_provider_name` do returns/search).

    Busca por `order_ids` (lotes de 50 por conta, sem filtro de tempo) — o
    sweep só olha `update_time` dos últimos 15 dias e perderia devolução aberta
    meses atrás e nunca mais mexida. Só linhas TikTok com pedido de marketplace
    e pedido Bling; mais de um caso → o VIVO mais recente
    (`_melhor_devolucao_por_pedido`). Best-effort por conta: sem integração ou
    API caída → loga e pula, nunca levanta."""
    # conta → order_id → [pedido_bling] (a mesma venda pode ter 2 linhas).
    por_conta: dict[str, dict[str, list[str]]] = {}
    for r in linhas:
        if (r.plataforma or "").strip().lower() not in _TIKTOK_PLATAFORMAS:
            continue
        oid = (r.pedido_marketplace or "").strip()
        pb = (r.pedido_bling or "").strip()
        if not oid or not pb:
            continue
        por_conta.setdefault((r.conta or "").strip(), {}).setdefault(oid, []).append(pb)

    out: dict[str, ReturnInfo] = {}
    for conta, pedidos in por_conta.items():
        try:
            integ = await _tiktok_integration_for_conta(session, conta)
            if integ is None:
                logger.warning(
                    "logistica_tiktok_returns_sem_integracao",
                    conta=conta, pedidos=len(pedidos),
                )
                continue
            client = _build_tiktok_client(session, integ)
            devolucoes = await client.get_return_list(order_ids=list(pedidos))
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_tiktok_returns_por_pedido_falhou",
                conta=conta, pedidos=len(pedidos), err=str(e)[:200],
            )
            continue
        melhor = _melhor_devolucao_por_pedido(devolucoes)
        por_pedido: dict[str, list[dict]] = {}
        for d in devolucoes:
            if isinstance(d, dict):
                por_pedido.setdefault(str(d.get("order_id") or "").strip(), []).append(d)
        for oid, d in melhor.items():
            info = _tiktok_return_info(d)
            # Prazo: o mais curto entre TODOS os casos vivos do pedido, não só
            # o do caso escolhido pro status/rastreio — dois casos abertos no
            # mesmo pedido (devolução + reembolso) têm prazos independentes.
            acao, prazo = _acao_pendente_do_pedido(por_pedido.get(oid) or [d])
            # Reembolso: idem, qualquer caso CONCLUÍDO do pedido já tirou
            # dinheiro do nosso (coluna "Reembolso" da aba Acompanhamento).
            pago, valor, em, detalhe = _reembolso_do_pedido(por_pedido.get(oid) or [d])
            info = info._replace(
                acao_pendente=acao,
                prazo_acao=prazo,
                reembolso=pago,
                reembolso_valor=valor,
                reembolso_em=em,
                reembolso_detalhe=detalhe,
            )
            for pb in pedidos.get(oid) or ():
                out[pb] = info
    return out


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
) -> dict[str, int]:
    """Enriquece um lote de linhas TikTok (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status.
    `ids` restringe às linhas dadas (o recarregar passa as pendentes do
    painel) — aí o `limit` não se aplica."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_TIKTOK_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(
        Logistica.data.desc().nulls_last(), Logistica.created_at.desc()
    )
    if ids is not None:
        stmt = stmt.where(Logistica.id.in_(list(ids)))
    else:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, TikTokClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_tiktok_integration_for_conta,
        build=lambda s, i: _build_tiktok_client(s, i, lock=lock),
    )
    # Conta sem integração fica fora do cache: descarta aqui pra o enrich_row
    # não ir ao banco no meio da rajada concorrente.
    alvo = [r for r in rows if r.conta in cache]
    updated = 0
    skipped = len(rows) - len(alvo)
    failed = 0
    for lote in logistica_enrich.chunked(alvo):
        res = await asyncio.gather(
            *(enrich_row(session, r, client_cache=cache) for r in lote),
            return_exceptions=True,
        )
        for row, r in zip(lote, res, strict=True):
            if isinstance(r, TikTokEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                logger.warning(
                    "logistica_tiktok_row_failed",
                    id=str(row.id), pedido=row.pedido_marketplace, err=str(r)[:200],
                )
            elif r:
                updated += 1
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_tiktok_enrich_batch", **summary)
    return summary
