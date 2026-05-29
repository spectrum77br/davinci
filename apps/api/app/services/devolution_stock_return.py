"""Retorno de produto devolvido ao estoque Bling + patch da situação do pedido.

Disparo (a partir do router): ao ADICIONAR a devolução ou ao ligar o toggle
"devolver estoque". Extraviado patcha a situação já no add (sem estoque);
Novo/Usado/Trocado/Manutenção só agem com o toggle ligado.

Estoque, por condição efetiva:
  Novo / Usado  → o operador escolhe no modal o destino:
                    * `estoque_destino_sku`  — bin já existente `base.<sufixo>`
                      (entrada direta de N unidades nesse SKU);
                    * `estoque_nova_tag`     — nenhuma variante existe, então
                      cria `z000N.<tag>` (sequencial pelo maior z na tabela
                      products), nome = nome original + " AVULSO" (Novo) /
                      " AVULSO SALVADO" (Usado), clonando a categoria do
                      produto original, com estoque inicial = quantidade.
  Trocado       → o item que volta é `troca_sku` (Novo/Usado em
                  `troca_condicao`); mesma lógica de destino sobre esse SKU.
  Manutenção    → `manutencao_destino` escolhido no modal:
                    * Novo/Usado → mesma lógica de estoque;
                    * Sucata     → não mexe no estoque (patch sucata no pedido).
  Extraviado / outros → não mexe no estoque.

Situação do pedido (valor único no Bling), precedência pior→melhor:
  qualquer Extraviado → 83960; senão qualquer Sucata → 545901; senão qualquer
  Trocado OU todos os itens resolvidos (Novo/Usado) → 545902 (resolvido).

Todas as funções públicas de estoque retornam um dict:
  ok: bool, action: str, sku: str|None, bling_product_id: int|None, message: str
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from app.models import BlingOrder, Devolution, Integration, Product
from app.models.enums import IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient
from app.services.sku_tags import SUFFIX_TAGS as _SKU_SUFFIX_TAGS
from app.services.sku_tags import classify_sku_tag

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_STOCK_CONDICOES = {"Novo", "Usado"}
# Condições que disparam retorno de estoque a partir do router. Trocado e
# Manutenção entram porque o destino/condição efetiva vêm do modal.
_STOCK_TRIGGER_CONDICOES = _STOCK_CONDICOES | {"Trocado", "Manutenção"}

# Situações do pedido no Bling (idSituacao).
SITUACAO_RESOLVIDO = 545902
SITUACAO_EXTRAVIADO = 83960
SITUACAO_SUCATA = 545901
SITUACAO_MANUTENCAO = 84677

# Sufixos regionais válidos — fonte única em app.services.sku_tags.
_SUFFIX_TAGS = _SKU_SUFFIX_TAGS

# Mala (b+dígito) e Eletro (u…) voltam direto no próprio SKU — sem tag/bin.
_MALA_OR_ELETRO_RE = re.compile(r"^(b[0-9]|u)", re.IGNORECASE)


def _is_mala_or_eletro(sku: str | None) -> bool:
    return bool(_MALA_OR_ELETRO_RE.match((sku or "").strip()))

type StockResult = dict[str, Any]


def _sku_base(sku: str) -> str:
    """Remove o sufixo regional conhecido do SKU, retornando a base.
    `x001.sp` → `x001`; `x001` → `x001`."""
    if "." in sku:
        head, tail = sku.rsplit(".", 1)
        if tail.lower() in _SUFFIX_TAGS:
            return head
    return sku


def _sku_tag(sku: str | None) -> str | None:
    """Tag de operador do SKU (sem ponto), espelhando o Controle de Estoque."""
    return classify_sku_tag(sku)


def _result(
    ok: bool,
    action: str,
    *,
    sku: str | None = None,
    bling_product_id: int | None = None,
    message: str = "",
) -> StockResult:
    return {
        "ok": ok,
        "action": action,
        "sku": sku,
        "bling_product_id": bling_product_id,
        "message": message,
    }


async def _get_bling_client(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


# ── Situação do pedido ───────────────────────────────────────────────────

def _resolution_of(row: Devolution) -> str:
    """Classifica a 'resolução' do item:
    extraviado | sucata | manutencao (pendente) | resolvido | unresolved."""
    c = (row.condicao_produto or "").strip()
    if c == "Extraviado":
        return "extraviado"
    if c == "Manutenção":
        d = (row.manutencao_destino or "").strip()
        if d == "Sucata":
            return "sucata"
        if d in _STOCK_CONDICOES:
            return "resolvido"
        return "manutencao"  # ainda em manutenção (destino não escolhido)
    if c in ("Novo", "Usado", "Trocado"):
        return "resolvido"
    return "unresolved"


def _order_situacao_target(rows: list[Devolution]) -> int | None:
    """Situação única do pedido pela precedência pior→melhor.

    Extraviado > Sucata > Manutenção(pendente) > Trocado/Resolvido. "Resolvido"
    por Novo/Usado só quando TODOS os itens estão resolvidos; um Trocado força
    resolvido. Manutenção pendente mantém o pedido "em manutenção" até o
    técnico escolher Novo/Usado/Sucata.

    "Entregue" é neutro (o cliente ficou com o item) — ignorado no cálculo; se
    TODOS os itens forem Entregue, não há patch.
    """
    rows = [r for r in rows if (r.condicao_produto or "").strip() != "Entregue"]
    if not rows:
        return None
    res = [_resolution_of(r) for r in rows]
    conds = [(r.condicao_produto or "").strip() for r in rows]
    if "extraviado" in res:
        return SITUACAO_EXTRAVIADO
    if "sucata" in res:
        return SITUACAO_SUCATA
    if "manutencao" in res:
        return SITUACAO_MANUTENCAO
    if "Trocado" in conds:
        return SITUACAO_RESOLVIDO
    if all(r == "resolvido" for r in res):
        return SITUACAO_RESOLVIDO
    return None


async def apply_order_situacao(
    session: AsyncSession, pedido_bling: str | None
) -> StockResult | None:
    """Recalcula e aplica a situação do pedido no Bling a partir de TODOS os
    itens de devolução do mesmo `pedido_bling` (== BlingOrder.numero).
    Best-effort: nunca levanta para o caller."""
    pedido = (pedido_bling or "").strip()
    if not pedido:
        return None
    rows = (
        await session.execute(select(Devolution).where(Devolution.pedido_bling == pedido))
    ).scalars().all()
    target = _order_situacao_target(list(rows))
    if target is None:
        return None

    bling_id = (
        await session.execute(
            select(BlingOrder.bling_id)
            .where(BlingOrder.numero == pedido, BlingOrder.bling_id.is_not(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if not bling_id:
        logger.warning("devolution_situacao_no_bling_id", pedido_bling=pedido, target=target)
        return _result(
            False, "situacao_no_bling_id",
            message=f"Pedido {pedido} sem bling_id — situação não atualizada",
        )

    client = await _get_bling_client(session)
    if client is None:
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")
    try:
        await client.update_order_situacao(int(bling_id), int(target))
        logger.info(
            "devolution_situacao_patched",
            pedido_bling=pedido, bling_id=int(bling_id), situacao=target,
        )
        return _result(True, "situacao_patched", message=f"Pedido {pedido} → situação {target}")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "devolution_situacao_patch_error",
            pedido_bling=pedido, situacao=target, error=str(exc),
        )
        return _result(False, "situacao_error", message=str(exc))


# ── Estoque ────────────────────────────────────────────────────────────────

async def return_product_to_bling_stock(
    session: AsyncSession, row: Devolution
) -> StockResult | None:
    """Best-effort: lê as escolhas dos modais persistidas em `row` e devolve as
    unidades ao estoque Bling. Retorna um dict de resultado; nunca levanta."""
    condicao = (row.condicao_produto or "").strip()
    if condicao not in _STOCK_TRIGGER_CONDICOES:
        return None

    # Resolve SKU e condição efetivos (Trocado/Manutenção vêm do modal).
    if condicao == "Trocado":
        eff_sku = (row.troca_sku or "").strip()
        eff_condicao = (row.troca_condicao or "").strip()
        if not eff_sku:
            return _result(False, "no_troca_sku", message="Trocado sem SKU de retorno")
        if eff_condicao not in _STOCK_CONDICOES:
            return _result(
                False, "no_troca_condicao", sku=eff_sku,
                message="Trocado sem condição (Novo/Usado) selecionada",
            )
    elif condicao == "Manutenção":
        dest = (row.manutencao_destino or "").strip()
        if dest == "Sucata":
            return _result(True, "sucata_no_stock", message="Manutenção → Sucata: estoque mantido")
        if dest not in _STOCK_CONDICOES:
            return _result(
                False, "no_manutencao_destino",
                message="Manutenção sem destino (Novo/Usado/Sucata) selecionado",
            )
        eff_sku = (row.sku or "").strip()
        eff_condicao = dest
    else:  # Novo / Usado
        eff_sku = (row.sku or "").strip()
        eff_condicao = condicao

    qty = max(1, int(row.quantidade or 1))
    obs = f"Devolução pedido {row.pedido_bling}" if row.pedido_bling else "Devolução"
    ctx = {
        "devolution_id": str(row.id),
        "condicao": condicao,
        "eff_condicao": eff_condicao,
        "sku": row.sku,
        "eff_sku": eff_sku,
        "qty": qty,
        "destino_sku": row.estoque_destino_sku,
        "nova_tag": row.estoque_nova_tag,
    }

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_no_bling_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")

    try:
        # 1) Destino existente escolhido no modal: entrada direta nesse bin.
        if row.estoque_destino_sku and (dest_sku := row.estoque_destino_sku.strip()):
            return await _return_to_existing(session, client, dest_sku, qty, obs, ctx)
        # 2) Criar produto novo z000N.<tag> (nenhuma variante existia).
        if row.estoque_nova_tag and (tag := row.estoque_nova_tag.strip().lower().lstrip(".")):
            return await _create_z_product(
                client, session, tag, eff_sku, eff_condicao, row, qty, obs, ctx
            )
        # 3) Legado: modal `.sp` antigo (sufixo regional direto).
        if row.estoque_suffix and (suffix := row.estoque_suffix.strip().lower().lstrip(".")):
            return await _return_with_suffix(client, eff_sku, suffix, row, qty, obs, ctx)
        # 4) Mala/Eletro NOVO: entrada direta no próprio SKU (salvaguarda — o
        #    front já manda estoque_destino_sku). Usado segue a lógica de usados.
        if eff_condicao == "Novo" and _is_mala_or_eletro(eff_sku):
            return await _return_to_existing(session, client, eff_sku, qty, obs, ctx)
        # 5) Sem destino do modal — comportamento direto pelo SKU.
        if eff_condicao == "Novo":
            return await _return_novo(session, client, eff_sku, qty, obs, ctx)
        return await _return_usado(session, client, eff_sku, row, qty, obs, ctx)
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_return_error", error=str(exc), **ctx)
        return _result(False, "error", message=str(exc))


async def _local_bling_product_id(session: AsyncSession, sku: str) -> int | None:
    """bling_product_id da tabela local products (ativo primeiro). Robusto para
    SKUs com ponto (ex.: b043.12), que a busca por código no Bling não acha."""
    if not sku:
        return None
    pid = (
        await session.execute(
            select(Product.bling_product_id)
            .where(
                func.lower(func.btrim(Product.sku)) == sku.strip().lower(),
                Product.bling_product_id.is_not(None),
            )
            .order_by((Product.situacao == "A").desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(pid) if pid else None


async def _resolve_product_id(
    session: AsyncSession, client: BlingClient, sku: str
) -> int | None:
    """Resolve o bling_product_id: tabela local primeiro, fallback na busca
    por código no Bling."""
    pid = await _local_bling_product_id(session, sku)
    if pid:
        return pid
    product = await client.find_active_product_by_sku(sku)
    return int(product["id"]) if product else None


async def _return_to_existing(
    session: AsyncSession, client: BlingClient, dest_sku: str, qty: int, obs: str, ctx: dict
) -> StockResult:
    """Entrada de N unidades num bin já existente escolhido no modal."""
    pid = await _resolve_product_id(session, client, dest_sku)
    if pid is None:
        logger.warning("devolution_stock_dest_not_found", dest_sku=dest_sku, **ctx)
        return _result(
            False, "sku_not_found", sku=dest_sku,
            message=f"SKU {dest_sku} não encontrado no Bling",
        )
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
    logger.info("devolution_stock_entry_existing", dest_sku=dest_sku, bling_product_id=pid, **ctx)
    return _result(
        True, "entry_existing", sku=dest_sku, bling_product_id=pid,
        message=f"SKU {dest_sku} · +{qty} unidade(s)",
    )


async def _max_z_in_products(session: AsyncSession) -> int:
    """Maior número de z-SKU já presente na tabela products (ignora sufixo)."""
    skus = (
        await session.execute(select(Product.sku).where(Product.sku.op("~*")("^z[0-9]")))
    ).scalars().all()
    max_n = 0
    for s in skus:
        m = re.match(r"(?i)^z0*([0-9]+)", s or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n


async def _next_z_sku_for_tag(session: AsyncSession, client: BlingClient, tag: str) -> str:
    """Próximo `z000N.<tag>` sequencial pelo maior z em products, garantindo
    que ainda não exista no Bling."""
    n = await _max_z_in_products(session) + 1
    while n < 100000:
        candidate = f"z{n:04d}.{tag}"
        if not await client.product_exists_by_sku(candidate):
            return candidate
        n += 1
    raise RuntimeError("Espaço de z-SKU esgotado")


async def _create_z_product(
    client: BlingClient, session: AsyncSession, tag: str,
    eff_sku: str, eff_condicao: str, row: Devolution, qty: int, obs: str, ctx: dict,
) -> StockResult:
    """Cria `z000N.<tag>` clonando nome/categoria do original e lança qty."""
    target_sku = await _next_z_sku_for_tag(session, client, tag)

    # Produto original: resolve id pela tabela local (robusto p/ SKUs com ponto)
    # e busca o detalhe no Bling para clonar nome/categoria.
    category_id = None
    bling_name = None
    orig_pid = await _resolve_product_id(session, client, eff_sku) if eff_sku else None
    if orig_pid:
        try:
            raw = await client.get_product(orig_pid)
            bling_name = (raw or {}).get("nome")
            cat = (raw or {}).get("categoria")
            if isinstance(cat, dict):
                category_id = cat.get("id")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "devolution_stock_clone_original_failed", orig_pid=orig_pid, error=str(exc)
            )
    base_name = (bling_name or row.produtos or eff_sku or target_sku).strip()
    suffix_label = " AVULSO SALVADO" if eff_condicao == "Usado" else " AVULSO"
    name = f"{base_name}{suffix_label}"

    price = float(row.custo_produto) if row.custo_produto else None
    new_data = await client.create_product(
        sku=target_sku, name=name, price=price, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_z_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
    logger.info(
        "devolution_stock_created_avulso",
        target_sku=target_sku, bling_product_id=pid, name=name, **ctx,
    )
    return _result(
        True, "product_created_avulso", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado · +{qty} unidade(s)",
    )


async def _return_with_suffix(
    client: BlingClient, eff_sku: str, suffix: str, row: Devolution, qty: int, obs: str, ctx: dict
) -> StockResult:
    """Legado (modal `.sp`): entrada em base.<suffix>; cria se não existir."""
    if not eff_sku:
        logger.warning("devolution_stock_suffix_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    target_sku = f"{_sku_base(eff_sku)}.{suffix}"
    product = await client.find_active_product_by_sku(target_sku)
    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
        logger.info(
            "devolution_stock_entry_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
        )
        return _result(
            True, "entry_suffix", sku=target_sku, bling_product_id=pid,
            message=f"SKU {target_sku} · +{qty} unidade(s)",
        )

    nome = row.produtos or f"{target_sku}"
    price = float(row.custo_produto) if row.custo_produto else None
    new_data = await client.create_product(sku=target_sku, name=nome, price=price)
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_suffix_create_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
    logger.info(
        "devolution_stock_created_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
    )
    return _result(
        True, "product_created_suffix", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado no Bling · +{qty} unidade(s)",
    )


async def _return_novo(
    session: AsyncSession, client: BlingClient, sku: str, qty: int, obs: str, ctx: dict
) -> StockResult:
    if not sku:
        logger.warning("devolution_stock_novo_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    pid = await _resolve_product_id(session, client, sku)
    if pid is None:
        logger.warning("devolution_stock_novo_sku_not_found", **ctx)
        return _result(
            False, "sku_not_found", sku=sku,
            message=f"SKU {sku} não encontrado no Bling",
        )

    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
    logger.info("devolution_stock_entry_novo", bling_product_id=pid, **ctx)
    return _result(
        True, "entry_novo", sku=sku, bling_product_id=pid,
        message=f"SKU {sku} · +{qty} unidade(s)",
    )


async def _return_usado(
    session: AsyncSession, client: BlingClient, sku: str, row: Devolution, qty: int,
    obs: str, ctx: dict,
) -> StockResult:
    sku = sku or ""
    if not sku:
        sku_usado = None
    elif "." in sku:
        sku_usado = sku.rsplit(".", 1)[0] + ".us"
    elif len(sku) > 3:
        sku_usado = sku[:-3] + ".us"
    else:
        sku_usado = sku + ".us"

    pid_usado = await _resolve_product_id(session, client, sku_usado) if sku_usado else None
    product = {"id": pid_usado} if pid_usado else None

    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
        logger.info(
            "devolution_stock_entry_usado", sku_usado=sku_usado, bling_product_id=pid, **ctx
        )
        return _result(
            True, "entry_usado", sku=sku_usado, bling_product_id=pid,
            message=f"SKU {sku_usado} · +{qty} unidade(s)",
        )

    # Não encontrado — cria sob o próximo z-SKU (sem tag, fallback legado)
    z_sku = await client.find_next_z_sku()
    nome = row.produtos or (f"Usado - {sku}" if sku else "Produto Usado")
    price = float(row.custo_produto) if row.custo_produto else None
    category_id = await client.get_category_id_by_name("Usado")

    new_data = await client.create_product(
        sku=z_sku, name=nome, price=price, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_product_no_id", z_sku=z_sku, **ctx)
        return _result(
            False, "create_failed", sku=z_sku,
            message=f"Falha ao criar produto {z_sku} no Bling",
        )

    pid = int(product_id)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs)
    logger.info(
        "devolution_stock_created_usado",
        z_sku=z_sku, bling_product_id=pid, original_sku=sku or None, **ctx,
    )
    return _result(
        True, "product_created_usado", sku=z_sku, bling_product_id=pid,
        message=f"Produto {z_sku} criado no Bling · +{qty} unidade(s)",
    )
