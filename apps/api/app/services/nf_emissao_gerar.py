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
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.security.cipher import decrypt_json, encrypt_json
from app.models import Integration, IntegrationPlatform, NfFaturador
from app.services import (
    nf_catalogo,
    nf_emissao,
    nf_relatorio,
    nf_upseller,
)
from app.services.marketplaces.bling import BlingClient
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
        bo.bling_id            AS bling_id,
        bo.numero_documento    AS documento,
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


@dataclass(frozen=True)
class BlocoFaturador:
    """Um comando de importação: os pedidos de UM faturador + a planilha
    congelada (CSV do Bling ou XLSX do Upseller, conforme o modo)."""

    faturador_id: UUID
    numeros: list[str]
    planilha: bytes
    nome_arquivo: str


@dataclass(frozen=True)
class ResultadoPorFaturador:
    blocos: list[BlocoFaturador]
    pulados: list[PedidoPulado]


def _dec(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal(0)
    return Decimal(str(v))


def _clean(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


async def _bling_client_opt(session: AsyncSession) -> BlingClient | None:
    """Cliente Bling BEST-EFFORT pra enriquecer o destinatário do pedido.
    Devolve None quando não há integração Bling (ou em qualquer erro) — a
    geração segue com o que a `bling_orders` já tem (sem quebrar)."""
    try:
        integ = (
            await session.execute(
                select(Integration)
                .where(Integration.platform == IntegrationPlatform.BLING)
                .limit(1)
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return None
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


def _extrair_destinatario(order: dict) -> dict:
    """Campos de destinatário do pedido do Bling (`contato` + `transporte.
    etiqueta`) pra completar o que a `bling_orders` não guarda (endereço/UF/CEP,
    tipo de pessoa). Chaves ausentes/vazias saem como None e NÃO sobrescrevem."""
    contato = order.get("contato") or {}
    transporte = order.get("transporte") or {}
    etiqueta = transporte.get("etiqueta") or {}
    return {
        "documento": _clean(contato.get("numeroDocumento")),
        "tipo_pessoa": _clean(contato.get("tipoPessoa")),
        # Nome do Comprador tem que vir da MESMA fonte do CPF (contato) — a
        # etiqueta guarda quem RECEBE (pode ser outra pessoa, ex. Amazon) e
        # pareado com o CPF do contato geraria NF com nome que não bate.
        "nome_destinatario": _clean(contato.get("nome")) or _clean(etiqueta.get("nome")),
        "endereco_destino": _clean(etiqueta.get("endereco")),
        "numero_destino": _clean(etiqueta.get("numero")),
        "complemento_destino": _clean(etiqueta.get("complemento")),
        "bairro_destino": _clean(etiqueta.get("bairro")),
        "cidade_destino": _clean(etiqueta.get("municipio")),
        "uf_destino": _clean(etiqueta.get("uf")),
        "cep_destino": _clean(etiqueta.get("cep")),
    }


async def _montar_info(
    client: BlingClient | None, base: dict, bling_id: object
) -> PedidoInfo:
    """Monta o PedidoInfo do cabeçalho e, quando há cliente Bling + bling_id,
    ENRIQUECE o destinatário (só preenche campos vazios, nunca apaga)."""
    campos = dict(base)
    if client is not None and bling_id is not None:
        try:
            order = await client.get_order(int(bling_id))
            for k, v in _extrair_destinatario(order).items():
                if v:
                    campos[k] = v
        except Exception as e:  # noqa: BLE001
            logger.warning("nf_enriquecer_falhou", bling_id=bling_id, err=str(e))
    return PedidoInfo(**campos)


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


@dataclass(frozen=True)
class _PedidoMontado:
    numero: str
    faturador_id: UUID
    info: PedidoInfo
    linhas: list


async def _montar_pedidos(
    session: AsyncSession, numeros: list[str]
) -> tuple[list[_PedidoMontado], list[PedidoPulado], dict]:
    """Núcleo compartilhado: lê itens, resolve o faturador de cada pedido e
    transforma pelos motores puros. Devolve os pedidos montados (na ordem
    pedida) + os pulados (sem itens / sem faturador) + o mapa dos faturadores
    (id → NfFaturador) pra o chamador decidir o formato do arquivo por modo."""
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

    # Cliente Bling best-effort pra enriquecer o destinatário (endereço/CEP/UF/
    # tipo de pessoa) que a bling_orders não persiste — obrigatório pra NF-e no
    # Upseller. None se não houver integração: segue com o que já tem.
    client = await _bling_client_opt(session)

    montados: list[_PedidoMontado] = []
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
        base = {
            "numero": numero,
            "data": cab["data"],
            "nome_destinatario": cab["nome_destinatario"],
            "cep_destino": cab["cep_destino"],
            "endereco_destino": cab["endereco_destino"],
            "numero_destino": cab["numero_destino"],
            "complemento_destino": cab["complemento_destino"],
            "bairro_destino": cab["bairro_destino"],
            "cidade_destino": cab["cidade_destino"],
            "uf_destino": cab["uf_destino"],
            "documento": cab["documento"],
        }
        info = await _montar_info(client, base, cab["bling_id"])
        montados.append(_PedidoMontado(numero, regra.id, info, linhas))

    return montados, pulados, faturadores


async def gerar_planilha(
    session: AsyncSession, numeros: list[str]
) -> ResultadoGeracao:
    """Gera o CSV de importação avulsa dos `numeros` (pedidos do Bling). Cada
    pedido usa a regra do faturador da sua loja. Pedidos sem faturador ou sem
    itens saem em `pulados`."""
    numeros = [n for n in (str(x).strip() for x in numeros) if n]
    if not numeros:
        return ResultadoGeracao(csv=nf_relatorio.montar_csv([]), pedidos_ok=[], pulados=[])

    montados, pulados, _ = await _montar_pedidos(session, numeros)
    pedidos = [(m.info, m.linhas) for m in montados]
    csv_bytes = nf_relatorio.montar_csv(pedidos)
    logger.info(
        "nf_emissao_planilha",
        pedidos_ok=len(montados),
        pulados=len(pulados),
    )
    return ResultadoGeracao(
        csv=csv_bytes,
        pedidos_ok=[m.numero for m in montados],
        pulados=pulados,
    )


# Limites POR ARQUIVO das telas de importação — um faturador com mais pedidos
# que isso gera VÁRIOS blocos (comandos), senão a tela recusa o arquivo inteiro.
#   Bling "Importar vendas": até 500 vendas/arquivo.
#   Upseller "Importar Pedidos": até 300 pedidos E 1500 linhas/arquivo.
_LIMITE_BLING_VENDAS = 500
_LIMITE_UPSELLER_PEDIDOS = 300
_LIMITE_UPSELLER_LINHAS = 1500


def _chunks_por_limite(
    grupo: list[_PedidoMontado], modo: str | None
) -> list[list[_PedidoMontado]]:
    """Fatia os pedidos de UM faturador em pedaços que cabem num arquivo da tela
    de destino. Preenche de forma gulosa preservando a ordem e NUNCA quebra um
    pedido no meio. Upseller respeita os DOIS tetos (pedidos E linhas)."""
    if not grupo:
        return []
    if modo == "upseller":
        lim_pedidos, lim_linhas = _LIMITE_UPSELLER_PEDIDOS, _LIMITE_UPSELLER_LINHAS
    else:
        lim_pedidos, lim_linhas = _LIMITE_BLING_VENDAS, None

    chunks: list[list[_PedidoMontado]] = []
    atual: list[_PedidoMontado] = []
    linhas_atual = 0
    for m in grupo:
        n_linhas = len(m.linhas)
        estoura_pedidos = len(atual) >= lim_pedidos
        estoura_linhas = (
            lim_linhas is not None and atual and linhas_atual + n_linhas > lim_linhas
        )
        if atual and (estoura_pedidos or estoura_linhas):
            chunks.append(atual)
            atual = []
            linhas_atual = 0
        atual.append(m)
        linhas_atual += n_linhas
    if atual:
        chunks.append(atual)
    return chunks


async def gerar_por_faturador(
    session: AsyncSession, numeros: list[str]
) -> ResultadoPorFaturador:
    """Como `gerar_planilha`, mas quebra o resultado em UM bloco por faturador —
    cada bloco tem seu subconjunto de pedidos + a própria planilha CONGELADA. É
    o que o outbox de importação enfileira (um comando por login/AdsPower). O
    FORMATO da planilha depende do `modo` do faturador: 'upseller' vira .xlsx no
    template do Upseller; 'bling' (ou qualquer outro) vira um .csv no template do
    importador de Vendas do Bling (tela Importações de Dados → Importar vendas,
    que aceita CSV/XLSX)."""
    numeros = [n for n in (str(x).strip() for x in numeros) if n]
    if not numeros:
        return ResultadoPorFaturador(blocos=[], pulados=[])

    montados, pulados, faturadores = await _montar_pedidos(session, numeros)

    # Agrupa preservando a ordem em que cada faturador apareceu.
    por_fat: dict[UUID, list[_PedidoMontado]] = {}
    for m in montados:
        por_fat.setdefault(m.faturador_id, []).append(m)

    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    blocos: list[BlocoFaturador] = []
    seq = 0
    for fid, grupo in por_fat.items():
        regra = faturadores.get(fid)
        modo = regra.modo if regra is not None else None
        # Quebra o faturador em N arquivos que caibam no limite da tela de
        # destino (Bling 500 vendas; Upseller 300 pedidos/1500 linhas).
        for chunk in _chunks_por_limite(grupo, modo):
            pedidos = [(m.info, m.linhas) for m in chunk]
            if modo == "upseller":
                planilha = nf_upseller.montar_xlsx(regra.nome, pedidos)
                ext = "xlsx"
            else:
                # Bling destino importa a VENDA avulsa na tela "Importar vendas"
                # (Importações de Dados), que aceita o CSV do relatório de vendas
                # — exatamente o layout que `nf_relatorio.montar_csv` produz.
                planilha = nf_relatorio.montar_csv(pedidos)
                ext = "csv"
            seq += 1
            blocos.append(
                BlocoFaturador(
                    faturador_id=fid,
                    numeros=[m.numero for m in chunk],
                    planilha=planilha,
                    nome_arquivo=f"nf_avulsa_{ts}_{seq}.{ext}",
                )
            )

    logger.info(
        "nf_emissao_por_faturador",
        blocos=len(blocos),
        pedidos_ok=len(montados),
        pulados=len(pulados),
    )
    return ResultadoPorFaturador(blocos=blocos, pulados=pulados)


@dataclass(frozen=True)
class LinhaAgregada:
    """Uma linha do resumo de conferência do lote: um SKU agregado (soma da
    quantidade e do valor total das linhas daquele SKU no lote)."""

    sku: str
    nome: str
    modelo: str | None
    quantidade: int
    valor_total: Decimal


async def agregar_por_sku(
    session: AsyncSession, numeros: list[str]
) -> list[LinhaAgregada]:
    """Agrega os itens dos `numeros` (pedidos de um lote) por SKU, aplicando a
    regra do faturador de cada pedido. Devolve uma linha por SKU com a soma da
    quantidade e do valor total; pula linhas sem informação (qtd e total zero).
    O `modelo` é a família da mala derivada do nome (abs/pp/me1/me2), ou None."""
    numeros = [n for n in (str(x).strip() for x in numeros) if n]
    if not numeros:
        return []
    montados, _, _ = await _montar_pedidos(session, numeros)

    acc: dict[str, dict] = {}
    for m in montados:
        for ln in m.linhas:
            sku = (ln.sku or "").strip()
            slot = acc.get(sku)
            if slot is None:
                slot = {
                    "nome": ln.nome or "",
                    "modelo": nf_catalogo.modelo_do_nome(ln.nome),
                    "quantidade": 0,
                    "valor_total": Decimal("0.00"),
                }
                acc[sku] = slot
            slot["quantidade"] += int(ln.quantidade or 0)
            slot["valor_total"] += _dec(ln.valor_total)

    out: list[LinhaAgregada] = []
    for sku, s in acc.items():
        if s["quantidade"] == 0 and s["valor_total"] == 0:
            continue
        out.append(
            LinhaAgregada(
                sku=sku,
                nome=s["nome"],
                modelo=s["modelo"],
                quantidade=s["quantidade"],
                valor_total=s["valor_total"],
            )
        )
    out.sort(key=lambda x: x.sku)
    return out


def nome_arquivo() -> str:
    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    return f"nf_avulsa_{ts}.csv"
