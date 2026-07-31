"""Prefill do confere-frete AUTO (impressão tipo "próprio", Amazon).

Puxa do PEDIDO os campos que hoje o operador digita à mão no modal do confere-
frete: CEP destino (`bling_orders.cep_destino`), a caixa (dimensões do produto no
Bling) e o frete projetado (Tabela de Preços: conta da loja × slot do produto).

É best-effort e NUNCA chuta o frete projetado — quando o pedido não resolve com
segurança (sem PricingProduct, sem conta, ou itens de faixas diferentes) devolve
`frete_projetado=None` + um aviso; o operador informa no modal. O CEP de origem
vem do .env (`nf_origem_cep`), passado pelo chamador.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PricingAccount, PricingProduct, Segment
from app.services import nf_emissao_gerar

logger = structlog.get_logger()
_SCHEMA = get_settings().database_schema

# Caixa neutra quando o produto não tem dimensões no Bling — o operador revê.
_FALLBACK = {
    "width": Decimal("20"),
    "height": Decimal("15"),
    "length": Decimal("10"),
    "weight": Decimal("0.3"),
}

_PEDIDO_SQL = text(
    f"""
    SELECT
        bo.item_index          AS item_index,
        bo.item_produto_id     AS item_produto_id,
        bo.item_codigo         AS sku,
        bo.item_descricao      AS nome,
        bo.item_quantidade     AS quantidade,
        bo.cep_destino         AS cep_destino,
        bo.nome_destinatario   AS nome_destinatario,
        si.id                  AS store_info_id,
        si.account_name        AS conta,
        COALESCE(pl.label, initcap(si.platform)) AS plataforma
    FROM "{_SCHEMA}".bling_orders bo
    JOIN "{_SCHEMA}".store_info si ON si.bling_store_id::text = bo.loja
    LEFT JOIN (VALUES
        ('ml','Mercado Livre'),('shopee','Shopee'),('amazon','Amazon'),
        ('tiktok','TikTok'),('magalu','Magalu'),('aliexpress','AliExpress'),
        ('shein','Shein'),('temu','Temu')
    ) AS pl(code,label) ON pl.code = si.platform
    WHERE bo.numero = :numero
      AND bo.situacao IS DISTINCT FROM 'excluido'
    ORDER BY bo.item_index
    """
)


@dataclass(frozen=True)
class ProdutoPrefill:
    id: str
    width: Decimal
    height: Decimal
    length: Decimal
    weight: Decimal
    quantity: int


@dataclass
class ResultadoPrefill:
    to_cep: str
    produtos: list[ProdutoPrefill]
    frete_projetado: Decimal | None
    plataforma: str | None
    conta: str | None
    nome_destinatario: str | None
    avisos: list[str] = field(default_factory=list)


class PedidoNaoEncontrado(Exception):
    """O número do pedido não existe em bling_orders (ou loja sem store_info)."""


def _dec(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _dims_from_raw(raw: dict) -> dict | None:
    """Dimensões (cm) + peso (kg) do produto cru do Bling v3. None se faltar
    qualquer medida — o chamador usa a caixa fallback."""
    dim = raw.get("dimensoes") or {}
    w = _dec(dim.get("largura"))
    h = _dec(dim.get("altura"))
    length = _dec(dim.get("profundidade"))
    peso = _dec(raw.get("pesoBruto")) or _dec(raw.get("pesoLiquido"))
    if not (w and h and length and peso):
        return None
    return {"width": w, "height": h, "length": length, "weight": peso}


async def _resolver_frete_projetado(
    session: AsyncSession, store_info_id, skus: list[str]
) -> tuple[Decimal | None, str | None]:
    """Frete projetado = conta da loja (store_info) × slot do produto na Tabela
    de Preços. Só devolve valor quando TODOS os itens caem no MESMO valor; senão
    None + aviso (nunca chuta)."""
    segs = (await session.execute(select(Segment))).scalars().all()
    by_id = {s.id: s for s in segs}
    # leaf segment id → (root segment id, slot 1..5)
    leaf_info: dict = {}
    for s in segs:
        if s.parent_id is not None:
            parent = by_id.get(s.parent_id)
            if parent is not None and parent.parent_id is None:
                leaf_info[s.id] = (parent.id, int(s.sort_order) + 1)

    pps = (
        await session.execute(
            select(PricingProduct.sku, PricingProduct.segment_id).where(
                PricingProduct.sku.in_(skus)
            )
        )
    ).all()
    sku_leaves: dict[str, list] = defaultdict(list)
    for sku, seg in pps:
        sku_leaves[sku].append(seg)

    accs = (
        await session.execute(
            select(PricingAccount).where(
                PricingAccount.store_info_id == store_info_id
            )
        )
    ).scalars().all()
    acc_by_root = {a.segment_id: a for a in accs}

    valores: set[Decimal] = set()
    faltou = False
    for sku in skus:
        matched: Decimal | None = None
        for leaf in sku_leaves.get(sku, []):
            info = leaf_info.get(leaf)
            if info is None:
                continue
            root_id, slot = info
            acc = acc_by_root.get(root_id)
            if acc is None:
                continue
            val = _dec(getattr(acc, f"shipping{slot}", None))
            if val is not None:
                matched = val
                break
        if matched is None:
            faltou = True
        else:
            valores.add(matched)

    if len(valores) == 1 and not faltou:
        return next(iter(valores)), None
    if not valores:
        return None, "Frete projetado não encontrado na Tabela de Preços — informe manualmente."
    return (
        None,
        "Itens do pedido caem em faixas diferentes de frete — informe o projetado manualmente.",
    )


async def resolver_prefill(
    session: AsyncSession, pedido_bling: str
) -> ResultadoPrefill:
    """Monta o prefill do modal de confere-frete a partir do pedido do Bling."""
    rows = (
        await session.execute(_PEDIDO_SQL, {"numero": pedido_bling})
    ).mappings().all()
    if not rows:
        raise PedidoNaoEncontrado(pedido_bling)

    avisos: list[str] = []
    head = rows[0]
    to_cep = (head["cep_destino"] or "").strip()
    if not to_cep:
        avisos.append("Pedido sem CEP de destino — informe manualmente.")

    # Dimensões por produto (1 chamada Bling por produto distinto).
    client = await nf_emissao_gerar._bling_client_opt(session)
    dims_cache: dict[int, dict | None] = {}
    if client is None:
        avisos.append("Sem integração Bling — usando caixa padrão para todos os itens.")

    produtos: list[ProdutoPrefill] = []
    faltou_dim: list[str] = []
    for r in rows:
        pid = r["item_produto_id"]
        dims: dict | None = None
        if client is not None and pid:
            if pid not in dims_cache:
                try:
                    raw = await client.get_product(int(pid))
                    dims_cache[pid] = _dims_from_raw(raw)
                except Exception as exc:  # best-effort
                    logger.warning("nf_frete_auto_get_product_falhou", pid=pid, err=str(exc))
                    dims_cache[pid] = None
            dims = dims_cache[pid]
        if dims is None:
            dims = _FALLBACK
            faltou_dim.append(str(r["sku"] or r["item_index"]))
        qty = int(r["quantidade"] or 1)
        produtos.append(
            ProdutoPrefill(
                id=str(r["item_index"]),
                width=dims["width"],
                height=dims["height"],
                length=dims["length"],
                weight=dims["weight"],
                quantity=max(1, qty),
            )
        )
    if faltou_dim:
        avisos.append(
            "Sem dimensões no Bling (caixa padrão): " + ", ".join(faltou_dim[:20])
        )

    skus = [str(r["sku"]) for r in rows if r["sku"]]
    frete_projetado, aviso_frete = await _resolver_frete_projetado(
        session, head["store_info_id"], skus
    )
    if aviso_frete:
        avisos.append(aviso_frete)

    return ResultadoPrefill(
        to_cep=to_cep,
        produtos=produtos,
        frete_projetado=frete_projetado,
        plataforma=head["plataforma"],
        conta=head["conta"],
        nome_destinatario=head["nome_destinatario"],
        avisos=avisos,
    )
