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
from app.services import nf_catalogo, nf_emissao, nf_relatorio
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


# Nome (com a família M1..P6/ME1/ME2) de cada SKU base de mala presente, pra
# derivar o modelo do catálogo automaticamente (sem vínculo manual).
_NOMES_BASE_SQL = text(
    f"""
    SELECT DISTINCT ON (split_part(sku, '.', 1))
        split_part(sku, '.', 1) AS base,
        name                    AS nome
    FROM "{_SCHEMA}".products
    WHERE split_part(sku, '.', 1) = ANY(:bases)
      AND name IS NOT NULL
    ORDER BY split_part(sku, '.', 1)
    """
)


async def _modelos_por_base(session: AsyncSession, bases: set[str]) -> dict[str, str]:
    """Mapa base do SKU (ex. `b001`) → modelo do catálogo (`abs`), derivado do
    nome do produto. Bases sem família conhecida ficam de fora."""
    if not bases:
        return {}
    rows = (
        await session.execute(_NOMES_BASE_SQL, {"bases": list(bases)})
    ).mappings().all()
    out: dict[str, str] = {}
    for r in rows:
        modelo = nf_catalogo.modelo_do_nome(r["nome"])
        if modelo:
            out[r["base"]] = modelo
    return out


def _valor_unitario(
    regra: NfFaturador,
    catalogo_mala: list,
    modelos_por_base: dict[str, str],
    row: object,
) -> Decimal:
    """Valor unitário do item: em NF cheia de MALA que casa o catálogo, usa o
    valor CHEIO do catálogo (por modelo/tamanho); senão o `itemvalor` (venda)."""
    if regra.nf_cheia:
        parsed = nf_catalogo.parse_sku_mala(row["sku"])
        if parsed is not None:
            modelo = modelos_por_base.get(parsed[0])
            do_catalogo = nf_catalogo.valor_para(catalogo_mala, row["sku"], modelo)
            if do_catalogo is not None:
                return do_catalogo
    return _dec(row["valor_unitario"])


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

    # Catálogo de mala: valor CHEIO por (modelo, tamanho). Só interessa às linhas
    # de faturador nf_cheia; a família (modelo) vem do nome do produto.
    bases_mala = {
        p[0]
        for r in rows
        if (p := nf_catalogo.parse_sku_mala(r["sku"])) is not None
    }
    catalogo_mala = await nf_catalogo.carregar_todos(session)
    modelos_por_base = await _modelos_por_base(session, bases_mala)

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
                valor_unitario=_valor_unitario(
                    regra, catalogo_mala, modelos_por_base, r
                ),
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
