"""Amazon × Bling — o que o Bling sabe do envio que a SP-API não conta.

Pra pedido Amazon, o rastreio dos Correios NÃO vem pela SP-API (Easy Ship dá
403 sem o papel de shipping e, no Envio próprio, a etiqueta nem é da Amazon).
Quem tem o código é o BLING: a etiqueta sai por lá (Melhor Envio) e fica no
objeto de postagem do pedido. De lá vêm:

- `transporte.volumes[].servico` — "SEDEX"/"PAC" (Envio próprio) ou
  "Logistica Amazon Dba" (DBA): segundo sinal do canal;
- `transporte.volumes[].codigoRastreamento` — o `…BR` que o 17track rastreia;
- objeto de postagem (`/logisticas/objetos/{volume.id}`) — `dataSaida` +
  `prazoEntregaPrevisto` em dias úteis = previsão de entrega dos Correios (a
  "Data de entrega" da cotação, ex. 14/09 + 7 = 23/09);
- contato do pedido (`/contatos/{id}`) — nome do comprador e o e-mail de
  retransmissão da Amazon (`…@marketplace.amazon.com.br`), único endereço
  pelo qual o DaVinci pode escrever ao cliente.

Roda dentro do motor da Logística (recarregar de 5 min e ingest de hora em
hora): só linhas Amazon da janela que ainda não têm tudo, com teto por rodada
(Bling: 3 req/s, até 4 GETs por pedido). Best-effort: Bling fora do ar não
derruba o motor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica
from app.services import logistica_amazon_canal, logistica_rules, logistica_track
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

_AMAZON_PLATAFORMAS = logistica_rules._AMAZON_PLATAFORMAS

JANELA_DIAS = 60
# Teto por rodada: até 4 GETs por pedido. 20 pedidos por rodada de 5 min
# (≈ 80 requisições) com uma pausa entre pedidos: o limite real do Bling é
# 3 req/s e os outros jobs (ingest de pedidos, NF) já disputam essa cota —
# com 40 por rodada a primeira passada choveu 429 (15/09).
MAX_POR_RODADA = 20
PAUSA_ENTRE_PEDIDOS_S = 0.5
# Pedido já lido mas ainda incompleto (etiqueta gerada depois, contato sem
# e-mail…) só é relido depois deste intervalo.
RELER_APOS = timedelta(hours=1)


def _data(raw: object) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _int(raw: object) -> int | None:
    try:
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _primeiro_volume(pedido: dict) -> dict:
    tp = pedido.get("transporte") if isinstance(pedido.get("transporte"), dict) else {}
    vols = tp.get("volumes") if isinstance(tp.get("volumes"), list) else []
    for v in vols:
        if isinstance(v, dict):
            return v
    return {}


def _completa(row: Logistica) -> bool:
    """Linha que já tem tudo que o Bling pode dar — não precisa reler."""
    if row.amazon_canal == logistica_amazon_canal.CANAL_DBA:
        # DBA: só o serviço (canal) e o contato interessam; rastreio é da Amazon.
        return bool(row.servico_envio) and bool(row.cliente_email)
    return (
        bool(row.rastreio)
        and row.previsao_correios is not None
        and bool(row.cliente_email)
        and bool(row.servico_envio)
    )


async def _bling_ids(session: AsyncSession, numeros: Iterable[str]) -> dict[str, int]:
    nums = sorted({n for n in numeros if n})
    if not nums:
        return {}
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.bling_id).where(
                BlingOrder.numero.in_(nums), BlingOrder.bling_id.isnot(None)
            )
        )
    ).all()
    return {str(n): int(b) for n, b in rows if b}


async def _alvo(session: AsyncSession, *, limit: int) -> list[Logistica]:
    corte = date.today() - timedelta(days=JANELA_DIAS)
    reler = datetime.now(UTC) - RELER_APOS
    stmt = (
        select(Logistica)
        .where(
            func.lower(func.trim(Logistica.plataforma)).in_(tuple(_AMAZON_PLATAFORMAS)),
            func.coalesce(Logistica.pedido_bling, "") != "",
            or_(Logistica.data.is_(None), Logistica.data >= corte),
            or_(
                Logistica.bling_enriquecido_em.is_(None),
                Logistica.bling_enriquecido_em < reler,
            ),
        )
        .order_by(Logistica.data.desc().nulls_last(), Logistica.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [r for r in rows if not _completa(r)][:limit]


async def enrich_row(row: Logistica, client: BlingClient, bling_id: int) -> bool:
    """Lê pedido, objeto de postagem e contato no Bling e preenche a linha.
    Retorna True se algum campo mudou. Levanta em erro de rede/HTTP (quem chama
    conta e segue)."""
    mudou = False
    pedido = await client.get_order(bling_id)
    vol = _primeiro_volume(pedido)
    servico = str(vol.get("servico") or "").strip() or None
    if servico and servico != row.servico_envio:
        row.servico_envio = servico
        mudou = True
    codigo = str(vol.get("codigoRastreamento") or "").strip().upper() or None
    # Rastreio digitado à mão só é trocado quando o Bling traz um código dos
    # Correios de verdade; vazio nunca apaga o que já está na linha.
    if codigo and codigo != (row.rastreio or "").strip().upper():
        if not row.rastreio or logistica_track.is_correios(codigo):
            row.rastreio = codigo
            mudou = True

    canal = logistica_amazon_canal.classificar(row.meli_status, row.servico_envio)
    if canal and canal != row.amazon_canal:
        row.amazon_canal = canal
        mudou = True

    # Objeto de postagem: só faz sentido fora do DBA (a Amazon entrega o DBA).
    vol_id = _int(vol.get("id"))
    if vol_id and canal != logistica_amazon_canal.CANAL_DBA:
        try:
            obj = await client.get_logistica_objeto(vol_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            obj = {}
        saida = _data(obj.get("dataSaida"))
        prazo = _int(obj.get("prazoEntregaPrevisto"))
        if saida and saida != row.postagem_data:
            row.postagem_data = saida
            mudou = True
        previsao = logistica_amazon_canal.previsao_correios(saida, prazo)
        if previsao and previsao != row.previsao_correios:
            row.previsao_correios = previsao
            mudou = True
        rast = obj.get("rastreamento") if isinstance(obj.get("rastreamento"), dict) else {}
        codigo_obj = str((rast or {}).get("codigo") or "").strip().upper() or None
        if codigo_obj and not row.rastreio:
            row.rastreio = codigo_obj
            mudou = True

    # Contato: nome + e-mail de retransmissão da Amazon (nunca e-mail real).
    contato = pedido.get("contato") if isinstance(pedido.get("contato"), dict) else {}
    nome = str((contato or {}).get("nome") or "").strip() or None
    if nome and nome != row.cliente_nome:
        row.cliente_nome = nome
        mudou = True
    contato_id = _int((contato or {}).get("id"))
    if contato_id and not row.cliente_email:
        try:
            dados = await client.get_contato(contato_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            dados = {}
        email = str(dados.get("email") or "").strip().lower() or None
        if email and logistica_amazon_canal.eh_email_relay_amazon(email):
            row.cliente_email = email
            mudou = True
        nome_cad = str(dados.get("nome") or "").strip() or None
        if nome_cad and not row.cliente_nome:
            row.cliente_nome = nome_cad
            mudou = True

    row.bling_enriquecido_em = datetime.now(UTC)
    return mudou


async def enrich_pendentes(
    session: AsyncSession, *, limit: int = MAX_POR_RODADA, client: BlingClient | None = None
) -> dict[str, int]:
    """Uma rodada: as linhas Amazon da janela que ainda não têm tudo do Bling.
    Sem integração Bling ou com o Bling fora do ar, conta e segue — o motor da
    Logística não pode parar por isso."""
    rows = await _alvo(session, limit=limit)
    resumo = {"alvo": len(rows), "lidos": 0, "mudados": 0, "sem_bling_id": 0, "falhas": 0}
    if not rows:
        return resumo
    if client is None:
        from app.services import logistica_bling  # tardio: evita import circular

        try:
            client = await logistica_bling._bling_client(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("logistica_amazon_bling_sem_cliente", err=str(e)[:200])
            return resumo
    ids = await _bling_ids(session, [r.pedido_bling or "" for r in rows])
    for r in rows:
        bid = ids.get((r.pedido_bling or "").strip())
        if not bid:
            resumo["sem_bling_id"] += 1
            # Sem espelho ainda: carimba pra não bater no Bling toda rodada.
            r.bling_enriquecido_em = datetime.now(UTC)
            continue
        try:
            mudou = await enrich_row(r, client, bid)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Pedido apagado no Bling: nada a ler; carimba pra não insistir.
                r.bling_enriquecido_em = datetime.now(UTC)
                resumo["sem_bling_id"] += 1
                continue
            resumo["falhas"] += 1
            logger.warning(
                "logistica_amazon_bling_row_http",
                pedido=r.pedido_bling,
                status=e.response.status_code,
            )
            continue
        except Exception as e:  # noqa: BLE001
            resumo["falhas"] += 1
            logger.warning(
                "logistica_amazon_bling_row_falhou",
                pedido=r.pedido_bling,
                err=str(e)[:200],
            )
            continue
        resumo["lidos"] += 1
        if mudou:
            resumo["mudados"] += 1
        await asyncio.sleep(PAUSA_ENTRE_PEDIDOS_S)
    await session.commit()
    logger.info("logistica_amazon_bling_enrich", **resumo)
    return resumo
