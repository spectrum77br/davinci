"""Adaptador de banco da emissão (Fase 3a) — lê os pedidos do Bling principal
já sincronizados no davinci (`bling_orders`), resolve a regra do FATURADOR da
loja (`store_info.nf_faturador_id` → `nf_faturador`), transforma pelos motores
puros (`nf_emissao`) e monta o arquivo de importação avulsa (`nf_relatorio`).

É a cola entre o davinci e os núcleos puros — a única parte que toca o banco.
Um pedido sem faturador atribuído (ou sem itens) é PULADO com um motivo, e o
chamador (endpoint) decide o que reportar.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.security.cipher import decrypt_json, encrypt_json
from app.models import Integration, IntegrationPlatform, NfEtiqueta, NfFaturador
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
        bo.total               AS pedido_total,
        bo.custofrete          AS pedido_frete,
        bo.categoria_nome      AS categoria,
        bo.bling_id            AS bling_id,
        bo.documento_destinatario AS documento,
        bo.nome_destinatario   AS nome_destinatario,
        bo.cep_destino         AS cep_destino,
        bo.endereco_destino    AS endereco_destino,
        bo.numero_destino      AS numero_destino,
        bo.complemento_destino AS complemento_destino,
        bo.bairro_destino      AS bairro_destino,
        bo.cidade_destino      AS cidade_destino,
        bo.uf_destino          AS uf_destino,
        si.nf_faturador_id     AS nf_faturador_id,
        si.nf_faturador_por_tipo AS nf_faturador_por_tipo,
        si.nf_etiqueta_id      AS nf_etiqueta_id,
        si.account_name        AS conta,
        si.platform            AS plataforma
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


# DUIMP por SKU do produto importado, digitada à mão na tela Importação. O
# campo já guarda a frase inteira ("produto importado pela duimp 26BR..."), e o
# SKU é gravado COM sufixo (b001.20, uaf001m1.220) — casa direto com o do pedido.
_DUIMP_SQL = text(
    f"""
    SELECT DISTINCT ON (lower(trim(sku)))
        lower(trim(sku)) AS sku,
        trim(duimp)      AS duimp
    FROM "{_SCHEMA}".import_products
    WHERE lower(trim(sku)) = ANY(:skus)
      AND duimp IS NOT NULL
      AND trim(duimp) <> ''
    ORDER BY lower(trim(sku)), updated_at DESC
    """
)


async def _duimp_por_sku(session: AsyncSession, skus: set[str]) -> dict[str, str]:
    """Mapa SKU (minúsculo) → texto da DUIMP. SKU sem DUIMP fica de fora."""
    if not skus:
        return {}
    rows = (
        await session.execute(_DUIMP_SQL, {"skus": sorted(skus)})
    ).mappings().all()
    return {r["sku"]: r["duimp"] for r in rows}


def _observacao_duimp(duimp_por_sku: dict[str, str], itens_rows: list) -> str | None:
    """Observação da nota: as DUIMPs dos itens do pedido, sem repetir e na ordem
    dos itens. Nenhum item importado → None (nota sai sem observação)."""
    textos: list[str] = []
    for r in itens_rows:
        duimp = duimp_por_sku.get((r["sku"] or "").strip().lower())
        if duimp and duimp not in textos:
            textos.append(duimp)
    return " | ".join(textos) or None


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


def _fator_rateio(regra: NfFaturador, rows: list) -> Decimal:
    """Traz o `itemvalor` (preço de ANÚNCIO) pro valor que o cliente PAGOU.

    Shopee/TikTok põem o desconto no nível do PEDIDO: `item_desconto` vem 0 e o
    `itemvalor` fica no preço de tabela, então o percentual do faturador incidia
    sobre uma base inflada (291422: item 600, pago 362,90 → NF saía 420 em vez
    de 254,03). Rateia `(total − frete) ÷ soma dos itens` em cada linha.

    Nunca passa de 1: quando o pago supera a soma dos itens (ML cobra o frete à
    parte e o `custofrete` é o custo, não o cobrado), o valor fica o de hoje.
    NF cheia não rateia — lá o valor vem do catálogo/venda integral.
    """
    if regra.nf_cheia:
        return Decimal(1)
    soma = sum(
        (_dec(r["valor_unitario"]) * Decimal(int(r["quantidade"] or 0)) for r in rows),
        Decimal(0),
    )
    pago = _dec(rows[0]["pedido_total"]) - _dec(rows[0]["pedido_frete"])
    if soma <= 0 or pago <= 0:
        return Decimal(1)
    return min(Decimal(1), pago / soma)


# Tipos de produto que a loja pode ter num faturador próprio
# (store_info.nf_faturador_por_tipo, migration 0228). A categoria do Bling
# ("Celular Kit", "Mala Usada", "Eletro Kit") casa pelo prefixo.
_TIPOS_FATURADOR = ("celular", "mala", "eletro")


def _uuid(v: object) -> UUID | None:
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except (TypeError, ValueError):
        return None


def _tipo_do_item(categoria: object) -> str | None:
    cat = str(categoria or "").strip().lower()
    for tipo in _TIPOS_FATURADOR:
        if cat.startswith(tipo):
            return tipo
    return None


def _faturador_do_pedido(itens_rows: list) -> UUID | None:
    """Faturador do pedido. A loja pode vender tipos diferentes de produto na
    MESMA conta com regras diferentes (ex. celular 1% e eletro 100%) — isso vem
    de `store_info.nf_faturador_por_tipo`. Cai no faturador base da loja quando
    a categoria não tem regra própria ou quando o pedido mistura tipos com
    faturadores diferentes (aí não dá pra escolher um só sem errar metade)."""
    base = _uuid(itens_rows[0]["nf_faturador_id"])
    por_tipo = itens_rows[0]["nf_faturador_por_tipo"] or {}
    if not por_tipo:
        return base
    escolhidos = {
        _uuid(por_tipo.get(tipo)) if (tipo := _tipo_do_item(r["categoria"])) else None
        for r in itens_rows
    }
    if len(escolhidos) == 1:
        return escolhidos.pop() or base
    return base


async def _carregar_faturadores(
    session: AsyncSession, ids: set
) -> dict:
    if not ids:
        return {}
    rows = (
        await session.execute(select(NfFaturador).where(NfFaturador.id.in_(ids)))
    ).scalars().all()
    return {f.id: f for f in rows}


async def _carregar_etiquetas(session: AsyncSession, ids: set) -> dict:
    if not ids:
        return {}
    rows = (
        await session.execute(select(NfEtiqueta).where(NfEtiqueta.id.in_(ids)))
    ).scalars().all()
    return {e.id: e for e in rows}


@dataclass(frozen=True)
class BlocoEtiqueta:
    """Um comando de importação da ETIQUETA no Upseller: os pedidos de UM cadastro
    de Etiqueta + a planilha do Upseller com NF-e=NÃO (a NF já saiu do Bling no
    faturamento; o Upseller entra só pra puxar a etiqueta)."""

    etiqueta_id: UUID
    ads_power: str | None
    numeros: list[str]
    planilha: bytes
    nome_arquivo: str


@dataclass(frozen=True)
class ResultadoEtiqueta:
    blocos: list[BlocoEtiqueta]
    pulados: list[PedidoPulado]


@dataclass(frozen=True)
class _PedidoMontado:
    numero: str
    faturador_id: UUID
    etiqueta_id: UUID | None
    info: PedidoInfo
    linhas: list
    # Categoria do Bling (bling_orders.categoria_nome) de cada item, na mesma
    # ordem de `linhas` — usada só no arquivo do Upseller (SKU genérico).
    categorias: list = field(default_factory=list)
    # A conta importa no Upseller com o catálogo de mala (m100/m200)?
    catalogo_mala: bool = False
    # store_info.platform da loja do pedido (shopee/tiktok/ml/amazon). É a chave
    # que divide o trabalho entre os executores no /agent/lease — por isso entra
    # no agrupamento: um comando NUNCA mistura plataformas.
    plataforma: str | None = None


def _linhas_upseller(m: _PedidoMontado) -> list:
    """Linhas com o SKU trocado pelo GENÉRICO do catálogo Upseller — o import
    rejeita SKU que não exista lá. Só o arquivo .xlsx do Upseller usa; o CSV do
    Bling mantém o SKU real. O catálogo é por CONTA: Shopee poofy usa m200/m100
    (por categoria), as demais usam e3/e4/e2/e5. SKUs NÃO se repetem no mesmo
    pedido (Upseller rejeita duplicados) — itens seguintes andam na cadeia."""
    cats = m.categorias or [None] * len(m.linhas)
    skus = nf_upseller.skus_para_itens(cats, catalogo_mala=m.catalogo_mala)
    return [replace(linha, sku=sku) for linha, sku in zip(m.linhas, skus)]


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
    for r in rows:
        for valor in (r["nf_faturador_por_tipo"] or {}).values():
            if (fid := _uuid(valor)) is not None:
                faturador_ids.add(fid)
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

    # DUIMP dos produtos importados (mala/airfryer). Só carrega se algum
    # faturador dos pedidos pedir a observação.
    duimp_por_sku: dict[str, str] = {}
    if any(f.observacao_duimp for f in faturadores.values()):
        duimp_por_sku = await _duimp_por_sku(
            session, {(r["sku"] or "").strip().lower() for r in rows if r["sku"]}
        )

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
        fid = _faturador_do_pedido(itens_rows)
        regra = faturadores.get(fid) if fid else None
        if regra is None:
            pulados.append(PedidoPulado(numero, "loja sem faturador atribuído"))
            continue

        fator = _fator_rateio(regra, itens_rows)
        itens = [
            ItemPedido(
                sku=r["sku"],
                nome=r["nome"],
                quantidade=int(r["quantidade"] or 0),
                valor_unitario=_valor_unitario(
                    regra, catalogo_mala, modelos_por_base, r
                )
                * fator,
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
            # Conta de marketplace = Loja no Upseller (o CSV do Bling ignora).
            "loja": _clean(cab["conta"]),
            "observacao": (
                _observacao_duimp(duimp_por_sku, itens_rows)
                if regra.observacao_duimp
                else None
            ),
        }
        info = await _montar_info(client, base, cab["bling_id"])
        montados.append(
            _PedidoMontado(
                numero,
                regra.id,
                cab["nf_etiqueta_id"],
                info,
                linhas,
                [r["categoria"] for r in itens_rows],
                nf_upseller.usa_catalogo_mala(cab["conta"], cab["plataforma"]),
                (cab["plataforma"] or "").strip().lower() or None,
            )
        )

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

    # Agrupa preservando a ordem em que cada faturador apareceu. A PLATAFORMA
    # entra na chave porque um mesmo faturador pode servir lojas de marketplaces
    # diferentes — e o /agent/lease divide o trabalho entre os executores por
    # plataforma, então um comando não pode misturar duas.
    por_fat: dict[tuple[UUID, str | None], list[_PedidoMontado]] = {}
    for m in montados:
        por_fat.setdefault((m.faturador_id, m.plataforma), []).append(m)

    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    blocos: list[BlocoFaturador] = []
    seq = 0
    for (fid, _plat), grupo in por_fat.items():
        regra = faturadores.get(fid)
        modo = regra.modo if regra is not None else None
        # Quebra o faturador em N arquivos que caibam no limite da tela de
        # destino (Bling 500 vendas; Upseller 300 pedidos/1500 linhas).
        for chunk in _chunks_por_limite(grupo, modo):
            if modo == "upseller":
                # Loja avulsa REGISTRADA + SKU genérico do catálogo Upseller
                # (nome do faturador/SKU real são rejeitados pelo import).
                pedidos = [(m.info, _linhas_upseller(m)) for m in chunk]
                planilha = nf_upseller.montar_xlsx(nf_upseller.LOJA_AVULSA, pedidos)
                ext = "xlsx"
            else:
                pedidos = [(m.info, m.linhas) for m in chunk]
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


async def gerar_etiqueta_upseller(
    session: AsyncSession, numeros: list[str]
) -> ResultadoEtiqueta:
    """Gera a planilha de IMPORTAÇÃO da ETIQUETA no Upseller (fluxo ML): a NF já
    saiu do Bling no faturamento, então o Upseller entra só pra puxar a etiqueta
    — o pedido é importado como loja avulsa com **NF-e=NÃO** (senão o Upseller
    emitiria uma 2ª NF). Um bloco por cadastro de ETIQUETA (o AdsPower do comando
    vem do cadastro Etiqueta, não do faturador). PULA:
      - pedido cujo faturador é 'upseller' (já entrou no Upseller com NF-e=SIM no
        fluxo normal — nada a reimportar);
      - pedido cujo cadastro de Etiqueta não é 'upseller' (Amazon ou sem cadastro
        — a etiqueta sai por outro caminho)."""
    numeros = [n for n in (str(x).strip() for x in numeros) if n]
    if not numeros:
        return ResultadoEtiqueta(blocos=[], pulados=[])

    montados, pulados, faturadores = await _montar_pedidos(session, numeros)
    etiquetas = await _carregar_etiquetas(
        session, {m.etiqueta_id for m in montados if m.etiqueta_id}
    )

    # Agrupa por cadastro de Etiqueta preservando a ordem; separa por faturador
    # (o nome da loja avulsa no Upseller vem do faturador).
    por_etq: dict[tuple[UUID, UUID, str | None], list[_PedidoMontado]] = {}
    for m in montados:
        etq = etiquetas.get(m.etiqueta_id) if m.etiqueta_id else None
        fat = faturadores.get(m.faturador_id)
        if etq is None or etq.modo != "upseller":
            pulados.append(PedidoPulado(m.numero, "etiqueta não é upseller"))
            continue
        if fat is not None and fat.modo == "upseller":
            pulados.append(PedidoPulado(m.numero, "faturador upseller já importa"))
            continue
        por_etq.setdefault((m.etiqueta_id, m.faturador_id, m.plataforma), []).append(m)

    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    blocos: list[BlocoEtiqueta] = []
    seq = 0
    for (eid, fid, _plat), grupo in por_etq.items():
        etq = etiquetas[eid]
        for chunk in _chunks_por_limite(grupo, "upseller"):
            pedidos = [(m.info, _linhas_upseller(m)) for m in chunk]
            planilha = nf_upseller.montar_xlsx(
                nf_upseller.LOJA_AVULSA, pedidos, emitir_nfe=False
            )
            seq += 1
            blocos.append(
                BlocoEtiqueta(
                    etiqueta_id=eid,
                    ads_power=etq.ads_power,
                    numeros=[m.numero for m in chunk],
                    planilha=planilha,
                    nome_arquivo=f"nf_etiqueta_{ts}_{seq}.xlsx",
                )
            )

    logger.info(
        "nf_emissao_etiqueta_upseller",
        blocos=len(blocos),
        pedidos_ok=sum(len(b.numeros) for b in blocos),
        pulados=len(pulados),
    )
    return ResultadoEtiqueta(blocos=blocos, pulados=pulados)


def nome_arquivo() -> str:
    ts = datetime.now(_BRT).strftime("%Y%m%d_%H%M%S")
    return f"nf_avulsa_{ts}.csv"
