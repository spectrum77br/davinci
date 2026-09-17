"""Preenche a assinatura de status da Shopee (`Logistica.meli_status`) puxando
da API v2 da Shopee.

Espelha o `logistica_meli.py`, mas a Shopee tem um vocabulário PRÓPRIO e bem
mais enxuto: o sinal de pós-venda que importa pro fluxo (cancelamento,
devolução, concluído, em envio) já vem no `order_status` do pedido — e a API v2
entrega isso em lote via `get_order_status_map`. Então a assinatura da Shopee é
um único campo:

    {"order_status": "COMPLETED" | "CANCELLED" | "TO_RETURN" | ...}

renderizado em PT por `logistica_rules.assinatura_shopee`. Camadas futuras
(rastreio, devolução detalhada) podem enriquecer, mas o order_status já casa a
maioria dos casos.

Só se aplica a pedidos Shopee. Best-effort: pedido que a Shopee não devolve fica
sem status (não derruba o lote).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt
from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

_SHOPEE_PLATAFORMAS = logistica_rules._SHOPEE_PLATAFORMAS


async def build_enrichment(client: ShopeeClient, order_sn: str) -> dict:
    """Monta a assinatura da Shopee + rastreio + localização física da SPX.

    Puxa três coisas (best-effort): o `order_status` COMERCIAL (get_order_status_map),
    o número de rastreio (get_tracking_number) e os eventos da SPX (get_tracking_info)
    — daí tira o `logistics_status` FÍSICO e a localização (descrição do último
    evento, ex. "Pedido postado Piracicaba - SP").

    Retorna `{"meli_status": {"order_status": ..., "logistics_status": ...} | {},
    "rastreio": str | None, "localizacao": str | None, "datas": {...}}`. Campos
    que a Shopee não devolver ficam de fora / None.

    `datas` = quando cada campo mudou (ver logistica_datas). A Shopee data as
    duas pontas: `update_time` do pedido e o horário do último evento da SPX."""
    order_sn = str(order_sn)
    status_map = await client.get_order_status_map([order_sn])
    info = status_map.get(order_sn) or {}
    st = (info.get("status") or "").strip().upper()
    meli: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    if st:
        meli["order_status"] = st
        # `update_time` = quando o pedido mudou de estado na Shopee.
        logistica_datas.propor(
            datas, "order_status", info.get("update_time"), logistica_datas.FONTE_PLATAFORMA
        )

    rastreio = await client.get_tracking_number(order_sn)

    localizacao: str | None = None
    track = await client.get_tracking_info(order_sn)
    log_status = (track.get("logistics_status") or "").strip().upper()
    if log_status:
        meli["logistics_status"] = log_status
    eventos = track.get("tracking_info") or []
    if eventos:
        # Shopee devolve os eventos em ordem decrescente, mas escolhe pelo
        # maior update_time pra não depender da ordem.
        top = max(eventos, key=lambda e: (e or {}).get("update_time") or 0)
        desc = ((top or {}).get("description") or "").strip()
        if desc:
            localizacao = desc
        if log_status:
            # O último evento da SPX é o que produziu o logistics_status atual.
            logistica_datas.propor(
                datas, "logistics_status", (top or {}).get("update_time"),
                logistica_datas.FONTE_PLATAFORMA,
            )
    if log_status:
        logistica_datas.propor(
            datas, "logistics_status", info.get("update_time"), logistica_datas.FONTE_APROX
        )

    return {
        "meli_status": meli,
        "rastreio": rastreio or None,
        "localizacao": localizacao,
        "datas": {f: datas[f] for f in meli if f in datas},
    }


async def _shopee_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração Shopee cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.SHOPEE)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_shopee_client(
    session: AsyncSession,
    integration: Integration,
    *,
    lock: asyncio.Lock | None = None,
) -> ShopeeClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        # Único acesso ao banco durante a rajada concorrente do enrich_recent —
        # serializado pelo lock (sessão async não aceita flush simultâneo).
        async with (lock or logistica_enrich.NOLOCK):
            await session.flush()

    return ShopeeClient(creds, on_token_refresh=_persist)


class ShopeeEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha Shopee (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, ShopeeClient] | None = None,
) -> bool:
    """Preenche `row.meli_status` puxando o order_status da Shopee. Retorna True
    se atualizou.

    Levanta `ShopeeEnrichError` com código quando não dá pra prosseguir (linha
    não-Shopee, sem pedido de marketplace, conta sem integração Shopee)."""
    if (row.plataforma or "").strip().lower() not in _SHOPEE_PLATAFORMAS:
        raise ShopeeEnrichError("logistica_nao_shopee")
    order_sn = (row.pedido_marketplace or "").strip()
    if not order_sn:
        raise ShopeeEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: ShopeeClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _shopee_integration_for_conta(session, conta)
        if integ is None:
            raise ShopeeEnrichError("logistica_sem_integracao")
        client = _build_shopee_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_sn)
    meli = enr["meli_status"]
    # `return_status` vem do sweep de pós-venda (returns API), não do
    # build_enrichment — preserva no re-enrich, senão a devolução detectada
    # sumiria da assinatura no tick seguinte e a regra de status regrediria.
    ret = (row.meli_status or {}).get("return_status")
    if ret:
        meli = {**meli, "return_status": ret}
    # Antes de trocar o status: o carimbo compara o valor velho com o novo.
    row.status_datas = logistica_datas.aplicar(row, meli, enr.get("datas"))
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
    # Divergência Shopee: cruza order_status comercial × logistics_status físico.
    row.divergencia = logistica_rules.detectar_divergencia_shopee(
        row.meli_status, row.localizacao
    )
    return True


# Janela do sweep de pós-venda: linhas Shopee com `data` (data do pedido) até
# 45 dias atrás — cobre com folga o prazo de devolução da Shopee (7 dias após a
# entrega). ~2.9k linhas ≈ 58 chamadas em lote por varredura (medido 28/08).
_SWEEP_JANELA_DIAS = 45
# Máximo que a returns API aceita por chamada (recusa janela maior).
_RETURNS_JANELA_DIAS = 15


async def sweep_pos_venda(session: AsyncSession) -> dict:
    """Re-olha TODAS as linhas Shopee da janela — inclusive as resolvidas que o
    painel esconde — e devolve os ids cuja situação de pós-venda MUDOU.

    Por que existe: o recarregar só re-enriquece as pendentes/visíveis. Linha
    escondida (ex. "Concluído" sem regra pendente) nunca mais era consultada —
    então devolução aberta DEPOIS da entrega ficava invisível pra sempre e a
    regra "Devolução solicitada → Aguardando Devolução" não disparava (caso
    real: pedidos 290580/291557/291145/291351, 27/08).

    Duas fontes, ambas em lote e por conta:
    - `get_order_status_map` (50 pedidos/chamada): order_status vivo mudou
      (TO_RETURN, IN_CANCEL, ...) → atualiza meli_status + carimbo de data;
    - `get_return_list` (janela máx. de 15 dias da Shopee): devoluções que o
      order_status nem mostra quando o pedido já está COMPLETED → grava
      `meli_status["return_status"]`; `assinatura_shopee` então rende
      "Devolução solicitada". Havendo mais de um caso pro mesmo pedido, vale
      o VIVO mais recente (ex. 290580: um CANCELLED + um ACCEPTED → ACCEPTED).

    Retorna {"ids": [UUID...], **contadores}. O recarregar passa os ids como
    `extras` do `_ids_pendentes` — extras furam o escondimento — e o fluxo
    normal re-enriquece a linha e aplica a regra de status no Bling."""
    corte = datetime.now(UTC).date() - timedelta(days=_SWEEP_JANELA_DIAS)
    rows = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(_SHOPEE_PLATAFORMAS)
                ),
                func.coalesce(Logistica.pedido_marketplace, "") != "",
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()

    por_conta: dict[str, list[Logistica]] = {}
    for r in rows:
        por_conta.setdefault((r.conta or "").strip(), []).append(r)

    mudados: set[UUID] = set()
    n_status = n_returns = contas_ok = 0
    agora = int(datetime.now(UTC).timestamp())
    ret_from = agora - _RETURNS_JANELA_DIAS * 24 * 3600 + 300
    for conta, linhas in por_conta.items():
        integ = await _shopee_integration_for_conta(session, conta)
        if integ is None:
            continue
        client = _build_shopee_client(session, integ)
        contas_ok += 1

        # 1) order_status vivo, em lotes de 50.
        sns = [(r.pedido_marketplace or "").strip() for r in linhas]
        smap = await client.get_order_status_map(sns)
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
                "logistica_shopee_sweep_returns_falhou",
                conta=conta, err=str(e)[:200],
            )
            devolucoes = []
        melhor: dict[str, tuple[bool, int, str]] = {}
        for d in devolucoes:
            sn = str(d.get("order_sn") or "").strip()
            st = str(d.get("status") or "").strip().upper()
            if not sn or not st:
                continue
            vivo = st not in logistica_rules._SHOPEE_RETURN_ENCERRADO
            cand = (vivo, int(d.get("update_time") or 0), st)
            if sn not in melhor or cand[:2] > melhor[sn][:2]:
                melhor[sn] = cand
        for r in linhas:
            got = melhor.get((r.pedido_marketplace or "").strip())
            if got is None:
                continue
            st = got[2]
            atual = ((r.meli_status or {}).get("return_status") or "").strip().upper()
            if st != atual:
                meli = dict(r.meli_status or {})
                meli["return_status"] = st
                # Carimba a data da devolução (update_time da Shopee) — é a
                # "última movimentação" da aba Acompanhamento; sem isso o
                # carimbo ficava parado no dia da 1ª devolução (caso 291981).
                datas_ret: dict[str, dict[str, str]] = {}
                logistica_datas.propor(
                    datas_ret, "return_status", got[1] or None,
                    logistica_datas.FONTE_PLATAFORMA,
                )
                r.status_datas = logistica_datas.aplicar(r, meli, datas_ret)
                r.meli_status = meli
                r.status_lido_em = datetime.now(UTC)
                mudados.add(r.id)
                n_returns += 1

        # Salva o que já foi lido ANTES de ir pra próxima conta: o deploy
        # recria o worker ~25×/dia e a varredura leva ~5 min — sem isto, um
        # kill no meio jogava fora tudo (15/09: a passada das :09 morreu aos
        # 368s e nada foi gravado).
        await session.commit()

    await session.commit()
    summary = {
        "seen": len(rows), "contas": contas_ok,
        "order_status": n_status, "returns": n_returns,
    }
    logger.info("logistica_shopee_sweep_pos_venda", **summary)
    return {"ids": list(mudados), **summary}


# Devolução de pedidos ANTIGOS: a returns API só filtra por janela de até 15
# dias, então `returns_por_pedido` varre fatias por create_time de hoje até a
# data do pedido mais velho da conta (menos a folga abaixo), com este teto —
# cobre com sobra o prazo de devolução da Shopee (7 dias após a entrega).
_RETURNS_TETO_DIAS = 120
_RETURNS_FOLGA_DIAS = 2
# Chaves onde a Shopee PODERIA informar a transportadora do retorno (o payload
# medido em 03/09 não traz nenhuma — carrier fica None na prática).
_RETURN_CARRIER_KEYS = (
    "carrier", "carrier_name", "logistics_channel_name", "shipping_carrier",
    "return_carrier", "return_logistics_channel_name",
)


def _return_carrier(d: dict) -> str | None:
    for k in _RETURN_CARRIER_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# Perna reversa concluída: a Shopee usa o mesmo vocabulário do envio de ida.
_SHOPEE_REVERSA_ENTREGUE = {"LOGISTICS_DELIVERY_DONE"}


def _entregue_em_do_detalhe(det: dict | None) -> datetime | None:
    """Quando a Shopee confirma que o pacote de VOLTA chegou ao vendedor.

    Só existe no DETALHE (`get_return_detail`): a lista devolve
    `logistics_status`/`reverse_logistics_status` nulos. Sem isso a aba
    Acompanhamento ficava parada no status do CASO (Eduardo, 10/09, pedido
    291516: pacote entregue e a tela dizendo "Devolução em processamento")."""
    d = det or {}
    reversa = str(d.get("reverse_logistics_status") or "").strip().upper()
    geral = str(d.get("logistics_status") or "").strip().upper()
    if reversa in _SHOPEE_REVERSA_ENTREGUE or geral in _SHOPEE_REVERSA_ENTREGUE:
        return epoch_to_dt(d.get("update_time"))
    return None


# Status do caso em que, com o pacote de volta ENTREGUE, a loja ainda tem o
# que fazer: conferir e, se veio errado, contestar antes do prazo
# (`return_seller_due_date`, 3 dias corridos — medido em prod 16/09:
# entregue 16/09 09:54 → prazo 19/09 09:50). SELLER_DISPUTE já contestou;
# REFUND_PAID/CLOSED acabou.
_SHOPEE_STATUS_CONFERIR = {"PROCESSING", "ACCEPTED"}


def _acao_pendente_shopee(det: dict | None, status: str | None) -> tuple[str | None, datetime | None]:
    """O que a Shopee espera da LOJA neste caso e até quando — a ação de prazo
    mais curto (Vinicius 16/09: "prazo de resposta da loja no painel"). Tudo
    vem do DETALHE da devolução:

      - REQUESTED + `due_date`: responder à solicitação;
      - `seller_proof.seller_proof_status == PENDING` + `seller_evidence_deadline`:
        a Shopee pediu prova da loja (2 dias);
      - `negotiation.negotiation_status == PENDING_RESPOND` + `offer_due_date`:
        o cliente fez uma proposta;
      - pacote de volta ENTREGUE + status em `_SHOPEE_STATUS_CONFERIR` +
        `return_seller_due_date`: conferir o pacote e responder (3 dias). A
        Shopee às vezes já manda essa data ANTES da entrega (estimativa, ex.
        289941 em 16/09) — por isso o gate da entrega;
      - `seller_compensation_status == PENDING_REQUEST` + due date: pedir
        compensação.
    Nunca levanta; sem nada pendente → (None, None)."""
    d = det if isinstance(det, dict) else {}
    st = str(status or d.get("status") or "").strip().upper()
    cands: list[tuple[str, datetime | None]] = []
    if st == "REQUESTED":
        cands.append(("SHOPEE_RESPONDER_SOLICITACAO", epoch_to_dt(d.get("due_date"))))
    proof = d.get("seller_proof") if isinstance(d.get("seller_proof"), dict) else {}
    if str(proof.get("seller_proof_status") or "").strip().upper() == "PENDING":
        cands.append(("SHOPEE_ENVIAR_EVIDENCIAS", epoch_to_dt(proof.get("seller_evidence_deadline"))))
    neg = d.get("negotiation") if isinstance(d.get("negotiation"), dict) else {}
    if str(neg.get("negotiation_status") or "").strip().upper() == "PENDING_RESPOND":
        cands.append(("SHOPEE_RESPONDER_PROPOSTA", epoch_to_dt(neg.get("offer_due_date"))))
    if st in _SHOPEE_STATUS_CONFERIR and _entregue_em_do_detalhe(d) is not None:
        cands.append(("SHOPEE_CONFERIR_PACOTE", epoch_to_dt(d.get("return_seller_due_date"))))
    comp = d.get("seller_compensation") if isinstance(d.get("seller_compensation"), dict) else {}
    if str(comp.get("seller_compensation_status") or "").strip().upper() == "PENDING_REQUEST":
        cands.append(("SHOPEE_PEDIR_COMPENSACAO", epoch_to_dt(comp.get("seller_compensation_due_date"))))
    validos = [(a, pz) for a, pz in cands if pz is not None]
    if not validos:
        return (None, None)
    return min(validos, key=lambda x: x[1])


# Status do caso em que a Shopee JÁ PAGOU o reembolso ao cliente (e desconta
# da loja no escrow). Medido 17/09 em 3 contas: 15/15 casos ACCEPTED cujo
# escrow foi lido depois da mudança tinham `seller_return_refund` negativo;
# nenhum PROCESSING/JUDGING tinha. REFUND_PAID (da doc) não apareceu em 250
# casos no BR — a lista fica em ACCEPTED mesmo depois de pago.
_SHOPEE_STATUS_REEMBOLSADO = {"ACCEPTED", "REFUND_PAID"}


def _valor(v: object) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _reembolso_do_caso(
    d: dict, status: str | None
) -> tuple[bool, Decimal | None, datetime | None, str | None]:
    """Já saiu dinheiro do nosso neste caso? Só o status do caso: ACCEPTED =
    reembolso pago (valor `refund_amount`, data `update_time`). A compensação
    que a Shopee paga à loja não vem na lista/detalhe do caso
    (`seller_compensation_status` sempre vazio no BR) — quem cruza isso com o
    escrow já baixado é o sync (devolucao_rastreio_sync)."""
    st = str(status or d.get("status") or "").strip().upper()
    if st not in _SHOPEE_STATUS_REEMBOLSADO:
        return False, None, None, None
    return (
        True,
        _valor(d.get("refund_amount")),
        epoch_to_dt(d.get("update_time")),
        "Caso aceito na Shopee — reembolso pago ao cliente",
    )


def _tipo_do_caso(d: dict) -> str | None:
    """`needs_logistics` da Shopee → tipo no vocabulário do TikTok, que a aba
    Acompanhamento já entende: False = SÓ reembolso (o cliente fica com o
    produto; "não recebi"/"chegou vazio") → "REFUND"; True = pacote volta →
    "RETURN_AND_REFUND"; ausente → None. É isso que separa a aba Fraude
    (Vinicius 17/09: "o cliente alega que chegou vazio, pede reembolso sem
    devolução — outra pessoa cuida") da Acompanhamento."""
    needs = d.get("needs_logistics")
    if needs is False:
        return "REFUND"
    if needs is True:
        return "RETURN_AND_REFUND"
    return None


def _return_info(d: dict, status: str) -> ReturnInfo:
    tracking = d.get("tracking_number")
    tracking = tracking.strip() if isinstance(tracking, str) else None
    return_sn = str(d.get("return_sn") or "").strip()
    pago, valor, em, detalhe = _reembolso_do_caso(d, status)
    return ReturnInfo(
        fonte="shopee",
        status=status,
        tracking=tracking or None,
        carrier=_return_carrier(d),
        created_at=epoch_to_dt(d.get("create_time")),
        updated_at=epoch_to_dt(d.get("update_time")),
        return_id=return_sn or None,
        return_type=_tipo_do_caso(d),
        reembolso=pago,
        reembolso_valor=valor,
        reembolso_em=em,
        reembolso_detalhe=detalhe,
    )


# ---------------------------------------------------------------------------
# Pacote que volta pela SPX do envio de IDA (entrega falhou).
#
# Medido em 16/09/2026 (pedido 295534 e mais 28): quando a SPX não consegue
# entregar, o comprador abre "não recebi" e a Shopee aceita o caso como SÓ
# REEMBOLSO (`needs_logistics=false`, `tracking_number` vazio,
# `reverse_logistics_status` LOGISTICS_NOT_STARTED) — ou o pedido é cancelado
# sem caso nenhum. O pacote volta pro vendedor pelo MESMO rastreio da ida, e o
# único lugar que conta isso é `get_tracking_info(order_sn)`: eventos
# RETURN_STARTED ("Pedido será devolvido ao vendedor") → RETURN_INITIATED →
# RETURNED ("Pedido devolvido"). O `logistics_status` agregado do pedido fica
# em LOGISTICS_DELIVERY_FAILED o tempo todo, então só os EVENTOS servem.
# ---------------------------------------------------------------------------

# Status agregado da ida (logistica.meli_status.logistics_status) que diz que
# o pacote NÃO chegou ao comprador — candidato a voltar pela SPX.
_SHOPEE_IDA_FALHOU = {"LOGISTICS_DELIVERY_FAILED", "LOGISTICS_RETURNED", "LOGISTICS_LOST"}
# Eventos do rastreio da ida que pertencem à volta ao remetente.
_SHOPEE_RTS_EVENTOS = {"RETURN_STARTED", "RETURN_INITIATED", "RETURNED", "LOGISTICS_RETURNED"}
# Evento que fecha a volta: o pacote foi entregue ao vendedor.
_SHOPEE_RTS_ENTREGUE = {"RETURNED", "LOGISTICS_RETURNED"}
# Status SINTÉTICO gravado em devolucao_rastreio.devolucao_status_auto quando o
# pedido volta pela ida SEM caso de devolução na Shopee (pedido cancelado após
# a falha de entrega). Tradução em logistica_rules._SHOPEE_RETURN_LABELS_PT.
STATUS_RTS = "RTS"


class RtsIda(NamedTuple):
    """O que o rastreio da IDA conta sobre a volta ao vendedor."""

    iniciado_em: datetime | None  # 1º evento de volta (vira "Em devolução desde")
    ultimo_em: datetime | None  # último evento de qualquer tipo
    localizacao: str | None  # descrição do último evento
    entregue_em: datetime | None  # evento RETURNED — chegou no vendedor


def rts_da_ida(track: dict | None) -> RtsIda | None:
    """Lê os eventos de `get_tracking_info` e devolve o retrato da volta ao
    remetente, ou None quando o rastreio da ida não tem nenhum evento de volta
    (pacote ainda tentando entregar, ou já entregue ao comprador)."""
    eventos = [e for e in ((track or {}).get("tracking_info") or []) if isinstance(e, dict)]
    volta = [
        e for e in eventos
        if str(e.get("logistics_status") or "").strip().upper() in _SHOPEE_RTS_EVENTOS
    ]
    if not volta:
        return None
    top = max(eventos, key=lambda e: _epoch_int(e.get("update_time")))
    entregues = [
        e for e in volta
        if str(e.get("logistics_status") or "").strip().upper() in _SHOPEE_RTS_ENTREGUE
    ]
    return RtsIda(
        iniciado_em=epoch_to_dt(min(_epoch_int(e.get("update_time")) for e in volta)),
        ultimo_em=epoch_to_dt(top.get("update_time")),
        localizacao=(str(top.get("description") or "").strip() or None),
        entregue_em=(
            epoch_to_dt(max(_epoch_int(e.get("update_time")) for e in entregues))
            if entregues
            else None
        ),
    )


def _epoch_int(v: object) -> int:
    """Epoch (s) tolerante: "1789565570", 1789565570.0 → 1789565570; lixo → 0
    (um evento ilegível não pode derrubar a rodada Shopee inteira)."""
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _ida_falhou(linha: Logistica) -> bool:
    st = str(((linha.meli_status or {}).get("logistics_status")) or "").strip().upper()
    return st in _SHOPEE_IDA_FALHOU


def _caso_vivo_na_linha(linha: Logistica) -> bool:
    """O sweep de pós-venda já viu um caso VIVO deste pedido
    (`meli_status.return_status`). Não achá-lo na varredura de agora é lacuna
    da lista (`get_return_list` é best-effort e devolve [] em erro), não
    ausência de caso — não pode virar RTS sintético por cima do que a aba já
    mostra."""
    ret = str(((linha.meli_status or {}).get("return_status")) or "").strip().upper()
    return bool(ret) and ret not in logistica_rules._SHOPEE_RETURN_ENCERRADO


def _sem_perna_reversa(det: dict | None, info: ReturnInfo) -> bool:
    """O caso de devolução NÃO vai trazer pacote pela reversa: só reembolso
    (`needs_logistics=false`) ou sem código de retorno."""
    d = det or {}
    if d.get("needs_logistics") is False:
        return True
    return not (info.tracking or "").strip()


_CARRIER_IDA = "Shopee Xpress (volta pela ida)"


async def _rts_da_ida_do_pedido(client: ShopeeClient, linha: Logistica) -> ReturnInfo | None:
    """Consulta o rastreio da IDA de uma linha cuja entrega falhou e devolve um
    `ReturnInfo` da volta ao remetente (status sintético `STATUS_RTS`), ou None
    quando a ida ainda não tem evento de volta. Best-effort: erro de API loga e
    devolve None (a linha fica como estava)."""
    order_sn = (linha.pedido_marketplace or "").strip()
    if not order_sn:
        return None
    try:
        track = await client.get_tracking_info(order_sn)
    except Exception as e:  # noqa: BLE001 — extra, não derruba o sync
        logger.warning(
            "logistica_shopee_rts_tracking_falhou",
            pedido=linha.pedido_bling, order_sn=order_sn, err=str(e)[:200],
        )
        return None
    rts = rts_da_ida(track)
    if rts is None:
        return None
    # O código da ida já está na linha da Logística (build_enrichment); só
    # pergunta de novo se ela estiver vazia.
    tracking = (linha.rastreio or "").strip() or None
    if tracking is None:
        try:
            tracking = await client.get_tracking_number(order_sn)
        except Exception:  # noqa: BLE001
            tracking = None
    return ReturnInfo(
        fonte="shopee",
        status=STATUS_RTS,
        tracking=tracking,
        carrier=_CARRIER_IDA,
        created_at=rts.iniciado_em,
        updated_at=rts.ultimo_em,
        return_id=None,
        entregue_em=rts.entregue_em,
        localizacao=rts.localizacao,
        localizacao_em=rts.ultimo_em,
    )


async def returns_por_pedido(
    session: AsyncSession,
    linhas: list[Logistica],
    *,
    ja_entregues: set[str] | None = None,
    reconsultar: set[str] | None = None,
) -> dict[str, ReturnInfo]:
    """Devolução (o pacote que VOLTA) de cada linha Shopee: `{pedido_bling:
    ReturnInfo}`. Pedido sem devolução conhecida fica de fora do dict.

    Por conta: varre `get_return_list` em fatias de 15 dias por create_time,
    de agora até a `data` mais antiga das linhas da conta (menos folga), com
    teto de `_RETURNS_TETO_DIAS` — devolução aberta antes disso não é achada.
    Havendo vários casos pro mesmo pedido vale o VIVO mais recente (mesma
    regra do sweep_pos_venda), senão o mais recente. Best-effort: conta sem
    integração ou erro de API loga e pula (nunca levanta).

    Mapeamento do return da Shopee: tracking=tracking_number (vazio → None;
    `needs_logistics=false` = só reembolso, sem pacote de volta), carrier=None
    (a Shopee não informa), status cru, created_at=create_time,
    updated_at=update_time, return_id=return_sn."""
    por_conta: dict[str, list[Logistica]] = {}
    for r in linhas:
        if (r.plataforma or "").strip().lower() not in _SHOPEE_PLATAFORMAS:
            continue
        if not (r.pedido_marketplace or "").strip() or not (r.pedido_bling or "").strip():
            continue
        por_conta.setdefault((r.conta or "").strip(), []).append(r)

    out: dict[str, ReturnInfo] = {}
    agora = int(datetime.now(UTC).timestamp())
    passo = _RETURNS_JANELA_DIAS * 24 * 3600 - 300
    piso = agora - _RETURNS_TETO_DIAS * 24 * 3600
    for conta, rows in por_conta.items():
        try:
            integ = await _shopee_integration_for_conta(session, conta)
            if integ is None:
                logger.warning("logistica_shopee_returns_sem_integracao", conta=conta)
                continue
            client = _build_shopee_client(session, integ)
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_shopee_returns_client_falhou", conta=conta, err=str(e)[:200]
            )
            continue

        # Início da varredura: data do pedido mais velho da conta menos folga;
        # linha sem data não limita (cai no teto).
        datas = [r.data for r in rows if r.data is not None]
        inicio = piso
        if datas and len(datas) == len(rows):
            mais_velha = datetime.combine(min(datas), datetime.min.time(), tzinfo=UTC)
            inicio = max(piso, int(mais_velha.timestamp()) - _RETURNS_FOLGA_DIAS * 24 * 3600)
        # Data no futuro (lixo) não pode zerar a varredura: olha ao menos 1 dia.
        inicio = min(inicio, agora - 24 * 3600)

        vistos: set[str] = set()
        melhor: dict[str, tuple[tuple[bool, int, int], dict, str]] = {}
        ate = agora
        while ate > inicio:
            de = max(inicio, ate - passo)
            try:
                devolucoes = await client.get_return_list(
                    create_time_from=de, create_time_to=ate
                )
            except Exception as e:  # noqa: BLE001 — best-effort por conta
                logger.warning(
                    "logistica_shopee_returns_falhou",
                    conta=conta, de=de, ate=ate, err=str(e)[:200],
                )
                break
            for d in devolucoes:
                if not isinstance(d, dict):
                    continue
                sn = str(d.get("order_sn") or "").strip()
                st = str(d.get("status") or "").strip().upper()
                if not sn or not st:
                    continue
                rsn = str(d.get("return_sn") or "").strip()
                chave = rsn or f"{sn}:{d.get('create_time')}:{st}"
                if chave in vistos:  # fatias se tocam na borda
                    continue
                vistos.add(chave)
                vivo = st not in logistica_rules._SHOPEE_RETURN_ENCERRADO
                rank = (vivo, int(d.get("update_time") or 0), int(d.get("create_time") or 0))
                if sn not in melhor or rank > melhor[sn][0]:
                    melhor[sn] = (rank, d, st)
            # Fatias encostadas (a borda repete e é deduplicada acima).
            ate = de

        for r in rows:
            order_sn = (r.pedido_marketplace or "").strip()
            pedido = (r.pedido_bling or "").strip()
            ja_chegou = pedido in (ja_entregues or set())
            got = melhor.get(order_sn)
            if got is None:
                # Sem caso na Shopee. Se a ida falhou, o pacote pode estar
                # voltando pela SPX mesmo assim (pedido cancelado) — senão a
                # aba fica em branco. Linha que já conhece um caso VIVO fica
                # como está: a lista pode só ter falhado nesta rodada.
                if _ida_falhou(r) and not ja_chegou and not _caso_vivo_na_linha(r):
                    rts = await _rts_da_ida_do_pedido(client, r)
                    if rts is not None:
                        out[pedido] = rts
                continue
            info = _return_info(got[1], got[2])
            # Uma chamada a mais por devolução AINDA NÃO entregue — ou entregue
            # mas em `reconsultar` (o sync manda quem chegou há poucos dias ou
            # ainda está em análise: é quando existe o prazo de resposta da
            # loja) — pra saber se o pacote de volta chegou e o que a Shopee
            # espera da loja. A lista não traz nada disso, só o detalhe. O
            # `ja_chegou` continua valendo sozinho pras decisões da SPX abaixo.
            det: dict | None = None
            if info.return_id and (not ja_chegou or pedido in (reconsultar or set())):
                try:
                    det = await client.get_return_detail(info.return_id)
                    entregue = _entregue_em_do_detalhe(det)
                except Exception as e:  # noqa: BLE001 — detalhe é extra, não derruba
                    logger.warning(
                        "logistica_shopee_return_detail_falhou",
                        pedido=pedido, return_sn=info.return_id, err=str(e)[:200],
                    )
                    entregue = None
                    # Sem resposta da Shopee não dá pra dizer que o prazo sumiu:
                    # o sync mantém o da rodada anterior.
                    info = info._replace(prazo_desconhecido=True)
                if entregue is not None:
                    info = info._replace(entregue_em=entregue)
                if det is not None:
                    acao, prazo = _acao_pendente_shopee(det, info.status)
                    info = info._replace(acao_pendente=acao, prazo_acao=prazo)
            # Caso sem perna reversa (só reembolso) com a ida falhada: a volta
            # está no rastreio da IDA, não no caso.
            rts: ReturnInfo | None = None
            if info.entregue_em is None and _ida_falhou(r) and _sem_perna_reversa(det, info):
                if ja_chegou:
                    # Já provou a volta numa rodada anterior: completa o código
                    # e a transportadora pela própria linha, sem ir à API —
                    # senão o sync regravaria rastreio/transportadora vazios.
                    info = info._replace(
                        tracking=info.tracking or (r.rastreio or "").strip() or None,
                        carrier=info.carrier or _CARRIER_IDA,
                    )
                else:
                    rts = await _rts_da_ida_do_pedido(client, r)
                    if rts is not None:
                        mexidas = [d for d in (info.updated_at, rts.updated_at) if d is not None]
                        info = info._replace(
                            tracking=info.tracking or rts.tracking,
                            carrier=info.carrier or rts.carrier,
                            # "Data últ. movimentação": o evento da SPX conta.
                            updated_at=max(mexidas) if mexidas else None,
                            entregue_em=rts.entregue_em,
                            localizacao=rts.localizacao,
                            localizacao_em=rts.localizacao_em,
                        )
                # Caso ENCERRADO (CANCELLED/CLOSED) não pinta a aba
                # (devolucao_status_pt → None) e esconderia a volta pela SPX
                # que acabamos de ler: o status vira o sintético RTS. O
                # return_id fica — chamado e prazo usam só ele.
                if (
                    (info.status or "").upper() in logistica_rules._SHOPEE_RETURN_ENCERRADO
                    and (rts is not None or ja_chegou)
                ):
                    info = info._replace(status=STATUS_RTS)
            out[pedido] = info

    logger.info(
        "logistica_shopee_returns_por_pedido",
        linhas=len(linhas), contas=len(por_conta), encontradas=len(out),
    )
    return out


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
) -> dict[str, int]:
    """Enriquece um lote de linhas Shopee (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status.
    `ids` restringe às linhas dadas (o recarregar passa as pendentes do
    painel) — aí o `limit` não se aplica."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_SHOPEE_PLATAFORMAS))
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

    cache: dict[str, ShopeeClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_shopee_integration_for_conta,
        build=lambda s, i: _build_shopee_client(s, i, lock=lock),
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
            if isinstance(r, ShopeeEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                logger.warning(
                    "logistica_shopee_row_failed",
                    id=str(row.id), pedido=row.pedido_marketplace, err=str(r)[:200],
                )
            elif r:
                updated += 1
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_shopee_enrich_batch", **summary)
    return summary
