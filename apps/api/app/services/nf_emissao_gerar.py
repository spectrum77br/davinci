"""Adaptador de banco da emissão (Fase 3a) — lê os pedidos do Bling principal
já sincronizados no davinci (`bling_orders`), resolve a regra do FATURADOR da
loja (`store_info.nf_faturador_id` → `nf_faturador`), transforma pelos motores
puros (`nf_emissao`) e monta o arquivo de importação avulsa (`nf_relatorio`).

É a cola entre o davinci e os núcleos puros — a única parte que toca o banco.
Um pedido sem faturador atribuído (ou sem itens) é PULADO com um motivo, e o
chamador (endpoint) decide o que reportar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import NfFaturador
from app.services import nf_emissao, nf_relatorio
from app.services.nf_emissao import ItemPedido
from app.services.nf_relatorio import PedidoInfo

logger = structlog.get_logger()
_SCHEMA = get_settings().database_schema
_BRT = ZoneInfo("America/Sao_Paulo")

# Todas as linhas (itens) dos pedidos pedidos, com o faturador da loja. Uma
# linha da bling_orders = um item; o cabeçalho (destinatário/data) se repete.
_ITENS_SQL = text(
    f"""
    SELECT
        bo.numero              AS numero,
        bo.data                AS data,
        bo.item_index          AS item_index,
        bo.item_codigo         AS sku,
        bo.item_descricao      AS nome,
        bo.item_quantidade     AS quantidade,
        bo.itemvalor           AS valor_unitario,
        bo.nome_destinatario   AS nome_destinatario,
        bo.cep_destino         AS cep_destino,
        bo.endereco_destino    AS endereco_destino,
        bo.numero_destino      AS numero_destino,
        bo.complemento_destino AS complemento_destino,
        bo.bairro_destino      AS bairro_destino,
        bo.cidade_destino      AS cidade_destino,
        bo.uf_destino          AS uf_destino,
        si.nf_faturador_id     AS nf_faturador_id
    FROM "{_SCHEMA}".bling_orders bo
    JOIN "{_SCHEMA}".store_info si ON si.bling_store_id::text = bo.loja
    WHERE bo.numero = ANY(:numeros)
      AND bo.situacao IS DISTINCT FROM 'excluido'
    ORDER BY bo.numero, bo.item_index
    """
)


@dataclass(frozen=True)
class PedidoPulado:
    numero: str
    motivo: str


@dataclass(frozen=True)
class ResultadoGeracao:
    csv: bytes
    pedidos_ok: list[str]
    pulados: list[PedidoPulado]


def _dec(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal(0)
    return Decimal(str(v))


async def _carregar_faturadores(
    session: AsyncSession, ids: set
) -> dict:
    if not ids:
        return {}
    rows = (
        await session.execute(select(NfFaturador).where(NfFaturador.id.in_(ids)))
    ).scalars().all()
    return {f.id: f for f in rows}


async def gerar_planilha(
    session: AsyncSession, numeros: list[str]
) -> ResultadoGeracao:
    """Gera o CSV de importação avulsa dos `numeros` (pedidos do Bling). Cada
    pedido usa a regra do faturador da sua loja. Pedidos sem faturador ou sem
    itens saem em `pulados`."""
    numeros = [n for n in (str(x).strip() for x in numeros) if n]
    if not numeros:
        return ResultadoGeracao(csv=nf_relatorio.montar_csv([]), pedidos_ok=[], pulados=[])

    rows = (
        await session.execute(_ITENS_SQL, {"numeros": numeros})
    ).mappings().all()

    # Agrupa por numero mantendo a ordem de chegada (item_index já ordenado).
    por_pedido: dict[str, list] = {}
    for r in rows:
        por_pedido.setdefault(r["numero"], []).append(r)

    faturador_ids = {r["nf_faturador_id"] for r in rows if r["nf_faturador_id"]}
    faturadores = await _carregar_faturadores(session, faturador_ids)

    pedidos: list[tuple[PedidoInfo, list]] = []
    pedidos_ok: list[str] = []
    pulados: list[PedidoPulado] = []

    for numero in numeros:
        itens_rows = por_pedido.get(numero)
        if not itens_rows:
            pulados.append(PedidoPulado(numero, "pedido não encontrado no davinci"))
            continue
        fid = itens_rows[0]["nf_faturador_id"]
        regra = faturadores.get(fid) if fid else None
        if regra is None:
            pulados.append(PedidoPulado(numero, "loja sem faturador atribuído"))
            continue

        itens = [
            ItemPedido(
                sku=r["sku"],
                nome=r["nome"],
                quantidade=int(r["quantidade"] or 0),
                valor_unitario=_dec(r["valor_unitario"]),
                ncm=None,
            )
            for r in itens_rows
        ]
        linhas = nf_emissao.transformar_pedido(regra, itens)
        cab = itens_rows[0]
        info = PedidoInfo(
            numero=numero,
            data=cab["data"],
            nome_destinatario=cab["nome_destinatario"],
            cep_destino=cab["cep_destino"],
            endereco_destino=cab["endereco_destino"],
            numero_destino=cab["numero_destino"],
            complemento_destino=cab["complemento_destino"],
            bairro_destino=cab["bairro_destino"],
            cidade_destino=cab["cidade_destino"],
            uf_destino=cab["uf_destino"],
        )
        pedidos.append((info, linhas))
        pedidos_ok.append(numero)

    csv_bytes = nf_relatorio.montar_csv(pedidos)
    logger.info(
        "nf_emissao_planilha",
        pedidos_ok=len(pedidos_ok),
        pulados=len(pulados),
    )
    return ResultadoGeracao(csv=csv_bytes, pedidos_ok=pedidos_ok, pulados=pulados)


def nome_arquivo() -> str:
    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    return f"nf_avulsa_{ts}.csv"
