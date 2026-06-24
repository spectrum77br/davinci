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
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.models import BlingOrder, Devolution, Integration, Product
from app.models.enums import IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingClient
from app.services.sku_tags import SUFFIX_TAGS as _SKU_SUFFIX_TAGS
from app.services.sku_tags import classify_sku_tag

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_STOCK_CONDICOES = {"Novo", "Usado"}
# Condições que disparam retorno de estoque a partir do router. Trocado e
# Manutenção entram porque o destino/condição efetiva vêm do modal.
_STOCK_TRIGGER_CONDICOES = _STOCK_CONDICOES | {"Trocado", "Manutenção"}

# Situações do pedido no Bling (idSituacao).
SITUACAO_RESOLVIDO = 545902
SITUACAO_EXTRAVIADO = 83960
SITUACAO_MANUTENCAO = 84677
# 545901 ("Manutenção - Sucata") existe mas o Bling rejeita a transição (400);
# por isso Sucata resolve para 545902 (resolvido).

# Sufixos regionais válidos — fonte única em app.services.sku_tags.
_SUFFIX_TAGS = _SKU_SUFFIX_TAGS

# Mala (b+dígito) e Eletro (u…) voltam direto no próprio SKU — sem tag/bin.
_MALA_OR_ELETRO_RE = re.compile(r"^(b[0-9]|u)", re.IGNORECASE)

# Sentinel em `estoque_nova_tag` para "criar z000N sem sufixo" (z0093, não z0093.us).
NOVA_TAG_SEM = "-"


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
    extraviado | manutencao (pendente) | resolvido | unresolved.

    Manutenção→Sucata resolve como 'resolvido' (o pedido vai para 545902 —
    o Bling rejeita a situação 545901 "Sucata" na transição)."""
    c = (row.condicao_produto or "").strip()
    if c == "Extraviado":
        return "extraviado"
    if c == "Manutenção":
        d = (row.manutencao_destino or "").strip()
        if d in _STOCK_CONDICOES or d == "Sucata":
            return "resolvido"
        return "manutencao"  # ainda em manutenção (destino não escolhido)
    if c in ("Novo", "Usado", "Trocado"):
        return "resolvido"
    return "unresolved"


def _order_situacao_target(rows: list[Devolution]) -> int | None:
    """Situação única do pedido pela precedência pior→melhor.

    Extraviado > Manutenção(pendente) > Trocado/Resolvido. "Resolvido" por
    Novo/Usado/Sucata só quando TODOS os itens estão resolvidos; um Trocado
    força resolvido. Manutenção pendente mantém o pedido "em manutenção" até o
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
    if "manutencao" in res:
        return SITUACAO_MANUTENCAO
    if "Trocado" in conds:
        return SITUACAO_RESOLVIDO
    if all(r == "resolvido" for r in res):
        return SITUACAO_RESOLVIDO
    return None


async def apply_order_situacao(
    session: AsyncSession,
    pedido_bling: str | None,
    *,
    actor_id: UUID | None = None,
) -> StockResult | None:
    """Recalcula e aplica a situação do pedido no Bling a partir de TODOS os
    itens de devolução do mesmo `pedido_bling` (== BlingOrder.numero).
    Best-effort: nunca levanta para o caller.

    `actor_id` é o usuário que disparou a mudança (vindo do router), gravado na
    trilha de auditoria. None = sistema."""
    pedido = (pedido_bling or "").strip()
    if not pedido:
        return None
    rows = (
        await session.execute(select(Devolution).where(Devolution.pedido_bling == pedido))
    ).scalars().all()
    target = _order_situacao_target(list(rows))
    if target is None:
        return None

    order_row = (
        await session.execute(
            select(BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero == pedido, BlingOrder.bling_id.is_not(None))
            .limit(1)
        )
    ).first()
    bling_id = order_row.bling_id if order_row else None
    situacao_antiga = order_row.situacao if order_row else None
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
        await record_margem_audit(
            session,
            acao="situacao",
            pedido_bling=pedido,
            bling_id=bling_id,
            valor_antigo=situacao_antiga,
            valor_novo=target,
            origem="devolucao",
            mudado_por=actor_id,
        )
        return _result(True, "situacao_patched", message=f"Pedido {pedido} → situação {target}")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "devolution_situacao_patch_error",
            pedido_bling=pedido, situacao=target, error=str(exc),
        )
        return _result(False, "situacao_error", message=str(exc))


# ── Registro / estorno do movimento de estoque ──────────────────────────────

def record_stock_movement(row: Devolution, sr: StockResult | None) -> None:
    """Grava em `row` o que foi efetivamente lançado no Bling, pra permitir o
    estorno depois. Só registra entradas reais (ok + produto resolvido); no-op
    pra falhas, 'sucata_no_stock' e afins. Limpa um estorno anterior — uma nova
    entrada zera o `revertido_at`. NÃO faz commit (o caller persiste)."""
    if not sr or not sr.get("ok") or not sr.get("bling_product_id"):
        return
    row.estoque_mov_sku = sr.get("sku")
    row.estoque_mov_bling_id = int(sr["bling_product_id"])
    row.estoque_mov_action = sr.get("action")
    row.estoque_mov_qty = max(1, int(row.quantidade or 1))
    row.estoque_mov_revertido_at = None


async def reverse_stock_movement(
    session: AsyncSession, row: Devolution
) -> StockResult | None:
    """Estorna (dá baixa "S") a entrada de estoque registrada em `row`, quando o
    operador desliga "devolver estoque" depois — ex.: item entrou como Usado e
    numa verificação posterior virou Sucata, então não pode ficar vendável.

    Best-effort: nunca levanta. Retorna None quando não há nada a estornar
    (sem movimento registrado ou já estornado). Damos BAIXA na mesma quantidade
    (não excluímos o produto): o z-SKU criado fica com estoque 0 e some das
    vendas, sem quebrar histórico/movimentos no Bling."""
    pid = row.estoque_mov_bling_id
    if not pid or row.estoque_mov_revertido_at is not None:
        return None
    qty = max(1, int(row.estoque_mov_qty or row.quantidade or 1))
    mov_sku = row.estoque_mov_sku or row.sku
    obs = (
        f"Estorno devolução pedido {row.pedido_bling}"
        if row.pedido_bling
        else "Estorno devolução"
    )
    ctx = {
        "devolution_id": str(row.id),
        "bling_product_id": int(pid),
        "mov_sku": mov_sku,
        "qty": qty,
    }

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_reverse_no_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")
    try:
        await client.update_stock_by_id(int(pid), qty=qty, operation="S", observacao=obs)
        row.estoque_mov_revertido_at = datetime.now(UTC)
        logger.info("devolution_stock_reversed", **ctx)
        return _result(
            True, "stock_reversed", sku=mov_sku, bling_product_id=int(pid),
            message=f"Estoque estornado · −{qty} em {mov_sku or pid}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_reverse_error", error=str(exc), **ctx)
        return _result(False, "error", sku=mov_sku, message=str(exc))


# ── Estoque ────────────────────────────────────────────────────────────────

async def return_product_to_bling_stock(
    session: AsyncSession, row: Devolution, *, obs_override: str | None = None
) -> StockResult | None:
    """Best-effort: lê as escolhas dos modais persistidas em `row` e devolve as
    unidades ao estoque Bling. Retorna um dict de resultado; nunca levanta.

    `obs_override` define a observação do movimento no Bling (usado pela correção
    manual de estoque); quando ausente, usa "Devolução pedido <n>"."""
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
    obs = (obs_override or "").strip() or (
        f"Devolução pedido {row.pedido_bling}" if row.pedido_bling else "Devolução"
    )
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

    # Custo a carimbar no lançamento de estoque (alimenta o custo médio do Bling
    # e a coluna "Preço de Custo" do extrato). Resolvido uma vez e passado a
    # TODOS os caminhos — entrada em bin existente também leva custo, não só a
    # criação de avulso.
    cost = await _resolve_cost(session, row, eff_sku)

    try:
        # 1) Destino existente escolhido no modal: entrada direta nesse bin.
        if row.estoque_destino_sku and (dest_sku := row.estoque_destino_sku.strip()):
            return await _return_to_existing(session, client, dest_sku, qty, obs, ctx, cost)
        # 2) Criar produto novo z000N(.<tag>) — sentinel "-" = sem sufixo.
        if row.estoque_nova_tag and (raw_nt := row.estoque_nova_tag.strip()):
            tag = None if raw_nt == NOVA_TAG_SEM else raw_nt.lower().lstrip(".")
            return await _create_z_product(
                client, session, tag, eff_sku, eff_condicao, row, qty, obs, ctx, cost
            )
        # 3) Legado: modal `.sp` antigo (sufixo regional direto).
        if row.estoque_suffix and (suffix := row.estoque_suffix.strip().lower().lstrip(".")):
            return await _return_with_suffix(session, client, eff_sku, suffix, row, qty, obs, ctx, cost)
        # 4) Mala/Eletro NOVO: entrada direta no próprio SKU (salvaguarda — o
        #    front já manda estoque_destino_sku). Usado segue a lógica de usados.
        if eff_condicao == "Novo" and _is_mala_or_eletro(eff_sku):
            return await _return_to_existing(session, client, eff_sku, qty, obs, ctx, cost)
        # 5) Sem destino do modal — comportamento direto pelo SKU.
        if eff_condicao == "Novo":
            return await _return_novo(session, client, eff_sku, qty, obs, ctx, cost)
        return await _return_usado(session, client, eff_sku, row, qty, obs, ctx, cost)
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


def _sku_root(sku: str) -> str:
    """Raiz do SKU p/ achar variantes semelhantes: 1º componente do kit, sem
    qualquer extensão após o 1º ponto. `b048.12` → `b048`; `x001.ra` → `x001`."""
    first = (sku or "").split("+")[0].strip()
    return first.split(".")[0].lower()


async def _resolve_cost_from_similar(session: AsyncSession, sku: str | None) -> float | None:
    """Custo (cost_price) de um produto original/semelhante na tabela local.

    Prioridade: SKU exato → qualquer variante com a mesma raiz (`b048.*`),
    sempre exigindo cost_price > 0 e preferindo ativo. `None` se nada bater.
    O exato vem primeiro porque cada variante tem custo próprio (b048.12 ≠
    b048); a raiz é só fallback quando a variante exata não existe."""
    if not sku:
        return None
    s = sku.strip().lower()
    root = _sku_root(s)
    if not root:
        return None
    exact = func.lower(func.btrim(Product.sku)) == s
    val = (
        await session.execute(
            select(Product.cost_price)
            .where(
                or_(
                    exact,
                    func.lower(Product.sku) == root,
                    func.lower(Product.sku).like(f"{root}.%"),
                ),
                Product.cost_price.is_not(None),
                Product.cost_price > 0,
            )
            .order_by(exact.desc(), (Product.situacao == "A").desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    return float(val) if val is not None else None


async def _resolve_cost(
    session: AsyncSession, row: Devolution, eff_sku: str | None
) -> float | None:
    """Custo a gravar no produto avulso: o custo da própria devolução quando
    presente (>0); senão busca do produto original/semelhante pelo SKU."""
    if row.custo_produto and row.custo_produto > 0:
        return float(row.custo_produto)
    return await _resolve_cost_from_similar(session, eff_sku)


# Fornecedor padrão (âncora do precoCusto no Bling V3) — resolvido 1x e cacheado.
_default_supplier_id: int | None = None
_default_supplier_resolved = False


async def _get_default_supplier_id(client: BlingClient) -> int | None:
    """Resolve (e cacheia) o contato.id do fornecedor padrão do config."""
    global _default_supplier_id, _default_supplier_resolved
    if _default_supplier_resolved:
        return _default_supplier_id
    name = get_settings().bling_default_supplier_name
    try:
        _default_supplier_id = (
            await client.find_contato_id_by_name(name) if name else None
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("devolution_supplier_resolve_failed", error=str(exc))
        _default_supplier_id = None
    _default_supplier_resolved = True
    return _default_supplier_id


async def _persist_cost(
    client: BlingClient, product_id: int, cost: float | None, ctx: dict
) -> None:
    """Persiste o precoCusto do produto recém-criado via fornecedor padrão.
    No-op silencioso quando não há custo (>0) ou fornecedor resolvível —
    nunca derruba o fluxo de estoque (a entrada já foi/será lançada)."""
    if not cost or cost <= 0:
        return
    supplier_id = await _get_default_supplier_id(client)
    if not supplier_id:
        logger.warning("devolution_stock_cost_no_supplier", **ctx)
        return
    try:
        await client.link_supplier_to_product(
            product_id=int(product_id), supplier_id=int(supplier_id), cost_price=float(cost)
        )
        logger.info(
            "devolution_stock_cost_linked",
            bling_product_id=int(product_id), cost=float(cost), **ctx,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "devolution_stock_cost_link_failed",
            bling_product_id=int(product_id), error=str(exc), **ctx,
        )


async def _return_to_existing(
    session: AsyncSession, client: BlingClient, dest_sku: str, qty: int, obs: str,
    ctx: dict, cost: float | None = None,
) -> StockResult:
    """Entrada de N unidades num bin já existente escolhido no modal."""
    pid = await _resolve_product_id(session, client, dest_sku)
    if pid is None:
        logger.warning("devolution_stock_dest_not_found", dest_sku=dest_sku, **ctx)
        return _result(
            False, "sku_not_found", sku=dest_sku,
            message=f"SKU {dest_sku} não encontrado no Bling",
        )
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
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


async def _next_z_sku_for_tag(
    session: AsyncSession, client: BlingClient, tag: str | None
) -> str:
    """Próximo `z000N.<tag>` (ou `z000N` sem sufixo quando tag=None) sequencial
    pelo maior z em products, garantindo que ainda não exista no Bling."""
    n = await _max_z_in_products(session) + 1
    while n < 100000:
        candidate = f"z{n:04d}.{tag}" if tag else f"z{n:04d}"
        if not await client.product_exists_by_sku(candidate):
            return candidate
        n += 1
    raise RuntimeError("Espaço de z-SKU esgotado")


async def _ensure_local_product(
    session: AsyncSession, bling_product_id: int, ctx: dict
) -> None:
    """Garante a linha local em `products` pro avulso recém-criado, na hora.

    Idempotente e best-effort: nunca derruba o retorno de estoque. Resolve o
    dono pelo ProductLink Bling (mesma atribuição single-tenant do webhook)."""
    from app.models.product import ProductLink
    from app.services.bling_product_create import run_auto_create_product_from_bling

    try:
        link = (
            await session.execute(
                select(ProductLink)
                .where(ProductLink.platform == IntegrationPlatform.BLING)
                .limit(1)
            )
        ).scalar_one_or_none()
        if link is None:
            logger.warning("devolution_local_product_no_link", **ctx)
            return
        await run_auto_create_product_from_bling(
            session, bling_product_id=bling_product_id, user_id=link.user_id
        )
    except Exception as exc:  # noqa: BLE001
        # Webhook/job ainda é o fallback; só logamos.
        logger.warning(
            "devolution_local_product_failed",
            bling_product_id=bling_product_id, error=str(exc), **ctx,
        )


async def _create_z_product(
    client: BlingClient, session: AsyncSession, tag: str | None,
    eff_sku: str, eff_condicao: str, row: Devolution, qty: int, obs: str, ctx: dict,
    cost: float | None = None,
) -> StockResult:
    """Cria `z000N.<tag>` (ou `z000N` sem sufixo se tag=None) clonando
    nome/categoria do original e lança qty."""
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

    if cost is None:
        cost = await _resolve_cost(session, row, eff_sku)
    new_data = await client.create_product(
        sku=target_sku, name=name, price=cost, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_z_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
    # Cria a linha local em `products` JÁ — sem isso o avulso só apareceria na
    # busca da devolução depois que o webhook `product.created` do Bling
    # dispara o job arq `auto_create_product_from_bling`, que é frágil (o Bling
    # V3 perde webhooks). Quando o job não rodava, o z ficava invisível pra
    # seleção. Reaproveita o mesmo serviço (idempotente) de forma síncrona.
    await _ensure_local_product(session, pid, ctx)
    logger.info(
        "devolution_stock_created_avulso",
        target_sku=target_sku, bling_product_id=pid, name=name, **ctx,
    )
    return _result(
        True, "product_created_avulso", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado · +{qty} unidade(s)",
    )


async def _return_with_suffix(
    session: AsyncSession, client: BlingClient, eff_sku: str, suffix: str,
    row: Devolution, qty: int, obs: str, ctx: dict, cost: float | None = None,
) -> StockResult:
    """Legado (modal `.sp`): entrada em base.<suffix>; cria se não existir."""
    if not eff_sku:
        logger.warning("devolution_stock_suffix_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    if cost is None:
        cost = await _resolve_cost(session, row, eff_sku)
    target_sku = f"{_sku_base(eff_sku)}.{suffix}"
    product = await client.find_active_product_by_sku(target_sku)
    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
        logger.info(
            "devolution_stock_entry_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
        )
        return _result(
            True, "entry_suffix", sku=target_sku, bling_product_id=pid,
            message=f"SKU {target_sku} · +{qty} unidade(s)",
        )

    nome = row.produtos or f"{target_sku}"
    new_data = await client.create_product(sku=target_sku, name=nome, price=cost)
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_suffix_create_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
    logger.info(
        "devolution_stock_created_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
    )
    return _result(
        True, "product_created_suffix", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado no Bling · +{qty} unidade(s)",
    )


async def _return_novo(
    session: AsyncSession, client: BlingClient, sku: str, qty: int, obs: str, ctx: dict,
    cost: float | None = None,
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

    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
    logger.info("devolution_stock_entry_novo", bling_product_id=pid, **ctx)
    return _result(
        True, "entry_novo", sku=sku, bling_product_id=pid,
        message=f"SKU {sku} · +{qty} unidade(s)",
    )


async def _return_usado(
    session: AsyncSession, client: BlingClient, sku: str, row: Devolution, qty: int,
    obs: str, ctx: dict, cost: float | None = None,
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

    if cost is None:
        cost = await _resolve_cost(session, row, sku)
    pid_usado = await _resolve_product_id(session, client, sku_usado) if sku_usado else None
    product = {"id": pid_usado} if pid_usado else None

    if product is not None:
        pid = int(product["id"])
        await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
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
    category_id = await client.get_category_id_by_name("Usado")

    new_data = await client.create_product(
        sku=z_sku, name=nome, price=cost, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_product_no_id", z_sku=z_sku, **ctx)
        return _result(
            False, "create_failed", sku=z_sku,
            message=f"Falha ao criar produto {z_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
    logger.info(
        "devolution_stock_created_usado",
        z_sku=z_sku, bling_product_id=pid, original_sku=sku or None, **ctx,
    )
    return _result(
        True, "product_created_usado", sku=z_sku, bling_product_id=pid,
        message=f"Produto {z_sku} criado no Bling · +{qty} unidade(s)",
    )
