"""Pricing router (Fase 9a) — accounts/products/overrides CRUD.

Push/Telegram/Audit endpoints arrive in 9b-9d. This sub-phase ships only
the data plane.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import (
    AuditDismissedSku,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    CellStatus,
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
    StoreInfo,
    User,
)
from app.schemas.pricing import (
    AccountSetDepartmentIn,
    AutoMatchResult,
    CompetitorPriceRow,
    ContaCatalogoIn,
    ContaCatalogoOut,
    PricingAccountCreate,
    PricingAccountOut,
    PricingAccountPatch,
    PricingGridCell,
    PricingGridOut,
    PricingOverrideCellColor,
    PricingOverrideCellStatus,
    PricingOverrideOut,
    PricingOverrideUpsert,
    PricingProductCreate,
    PricingProductImport,
    PricingProductImportResult,
    PricingProductOut,
    PricingProductPatch,
    PricingPushBatchIn,
    PricingPushItemOut,
    PricingPushOut,
    PricingPushReportIn,
    SkuAuditRow,
    StoreInfoCreate,
    StoreInfoOut,
    StoreInfoPatch,
)
from app.schemas.products import JobCreatedOut
from app.security.cipher import decrypt, encrypt
from app.services.mega_fotos import MegaError, sidecar_request
from app.services.mega_midias import RAIZ_POR_DEPARTAMENTO
from app.services.pricing.audit import (
    match_pricing_to_product_keys,
    scan_missing_skus,
)
from app.services.pricing.anuncios import (
    CANAL_CATALOGO,
    CANAL_KIT,
    info_celula_catalogo,
    resolver_anuncios,
)
from app.services.pricing.calc import calculate
from app.services.pricing.competitor import search_competitors
from app.services.pricing.push import push_one
from app.services.pricing.sku_match import ml_listing_type_for_account, variants_of
from app.services.store_departments import (
    MANUAL_DEPARTMENT_PLATFORMS as _MANUAL_DEPARTMENT_PLATFORMS,
)
from app.services.store_departments import (
    STORE_TO_PRICING_PLATFORM as _STORE_TO_PRICING_PLATFORM,
)
from app.services.store_departments import (
    manual_store_departments as _manual_store_departments,
)
from app.services.store_departments import resolve_store_departments
from app.services.telegram import TelegramClient, TelegramConfigError
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def _coerce_platform(v: str | None) -> PricingPlatform | None:
    if v is None:
        return None
    try:
        return PricingPlatform(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_platform"}) from e


def _coerce_cell_status(v: str | None) -> CellStatus | None:
    if v is None:
        return None
    try:
        return CellStatus(v)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_cell_status"}) from e


async def _resolve_root_segment_id(
    session: AsyncSession, dept_slug: str | None
) -> UUID | None:
    """Returns the root segment id whose slug matches the given department
    string. Returns None if the slug doesn't resolve to a root segment."""
    if not dept_slug:
        return None
    res = await session.execute(
        select(Segment.id).where(
            and_(Segment.parent_id.is_(None), Segment.slug == dept_slug)
        )
    )
    return res.scalar_one_or_none()


async def _resolve_leaf_segment_id(
    session: AsyncSession,
    dept_slug: str | None,
    product_type: int | None,
) -> UUID | None:
    """Returns the leaf segment id for (root slug, column-index 1..5)."""
    if not dept_slug or product_type is None:
        return None
    sort_order = max(0, min(4, int(product_type) - 1))
    root_subq = select(Segment.id).where(
        and_(Segment.parent_id.is_(None), Segment.slug == dept_slug)
    )
    res = await session.execute(
        select(Segment.id).where(
            and_(
                Segment.sort_order == sort_order,
                Segment.parent_id.in_(root_subq),
            )
        )
    )
    return res.scalar_one_or_none()


async def _segment_index(
    session: AsyncSession,
) -> tuple[dict[UUID, str], dict[UUID, tuple[str, int]], dict[str, UUID]]:
    """Returns three indexes used to compose pricing schema outputs and
    translate department-slug filter queries:

      * `roots_by_id`     {segment_id: root_slug}      — for accounts
      * `leaves_by_id`    {segment_id: (root_slug, product_type 1..5)} — products
      * `root_id_by_slug` {slug: root_segment_id}      — for filter queries
    """
    rows = (await session.execute(select(Segment))).scalars().all()
    by_id = {s.id: s for s in rows}
    roots_by_id: dict[UUID, str] = {}
    leaves_by_id: dict[UUID, tuple[str, int]] = {}
    root_id_by_slug: dict[str, UUID] = {}
    for s in rows:
        if s.parent_id is None:
            roots_by_id[s.id] = s.slug
            root_id_by_slug[s.slug] = s.id
    for s in rows:
        if s.parent_id is not None:
            parent = by_id.get(s.parent_id)
            if parent and parent.parent_id is None:
                leaves_by_id[s.id] = (parent.slug, int(s.sort_order) + 1)
    return roots_by_id, leaves_by_id, root_id_by_slug


async def _default_slot_segments(
    session: AsyncSession, root_id: UUID
) -> list[UUID | None]:
    """Filhos ordenados (sort_order) do segmento-raiz da aba — mapeamento
    default coluna N ↔ N-ésimo filho. A view de margens
    (vw_conciliacao_margens_marketplace) SÓ casa o frete projetado quando
    slot{N}_segment_id está preenchido; conta salva sem vínculo = coluna
    "Projetado" vazia na página Margem (caso 293416/293418/293422 — 97
    contas backfilled em 2026-08-31). Preencher o default no create garante
    que conta nova nunca nasce sem vínculo; o popover da UI continua
    permitindo remapear depois."""
    res = await session.execute(
        select(Segment.id)
        .where(Segment.parent_id == root_id)
        .order_by(Segment.sort_order)
        .limit(5)
    )
    kids = list(res.scalars().all())
    return [kids[i] if i < len(kids) else None for i in range(5)]


async def _segment_names_by_id(session: AsyncSession) -> dict[UUID, str]:
    """All segment ids → name, used to resolve slot{N}_segment_id labels for
    PricingAccountOut. Cached per-request by the caller (we expect this to
    be called once per request alongside `_segment_index`)."""
    rows = (await session.execute(select(Segment.id, Segment.name))).all()
    return {sid: name for sid, name in rows}


# O que a coluna de catálogo ("filha") herda da conta base na hora do cálculo
# e mostra no cabeçalho: os números da conta e as anotações de texto.
_CAMPOS_HERDADOS_DA_BASE = (
    "kit_number",
    "commission",
    "margin1", "shipping1",
    "margin2", "shipping2",
    "margin3", "shipping3",
    "margin4", "shipping4",
    "margin5", "shipping5",
    "discount", "affiliate", "ads", "coupon", "offer",
    "observation", "observation2", "observation3",
)

# O que a filha copia da base na criação e acompanha quando a base muda.
_CAMPOS_ESPELHADOS_NA_FILHA = (
    "user_id",
    "name",
    "platform",
    "listing_type",
    "segment_id",
    "slot1_segment_id",
    "slot2_segment_id",
    "slot3_segment_id",
    "slot4_segment_id",
    "slot5_segment_id",
    "sort_order",
    "kit_number",
)


def _account_out(
    row: PricingAccount,
    roots_by_id: dict[UUID, str],
    names_by_id: dict[UUID, str] | None = None,
    *,
    base: PricingAccount | None = None,
    conta_catalogo_id: UUID | None = None,
) -> PricingAccountOut:
    """`base`: a conta de kit de uma coluna de catálogo — a filha sai com os
    números e anotações dela. `conta_catalogo_id`: a filha de uma conta de kit
    (catálogo ligado)."""
    out = PricingAccountOut.model_validate(row)
    out.has_password = bool(row.password_enc)
    out.department = roots_by_id.get(row.segment_id)
    if names_by_id is not None:
        for n in (1, 2, 3, 4, 5):
            sid = getattr(row, f"slot{n}_segment_id", None)
            if sid is not None:
                setattr(out, f"slot{n}_segment_name", names_by_id.get(sid))
    if row.canal == CANAL_CATALOGO and base is not None:
        for campo in _CAMPOS_HERDADOS_DA_BASE:
            setattr(out, campo, getattr(base, campo))
    if row.canal != CANAL_CATALOGO:
        out.conta_catalogo_id = conta_catalogo_id
    return out


def _so_filhas_com_base(rows):
    """Coluna de catálogo só aparece junto da conta base: se a base ficou de
    fora (arquivada, outra equipe, outro filtro), a filha sai também."""
    bases = {r.id for r in rows if r.canal != CANAL_CATALOGO}
    return [r for r in rows if r.canal != CANAL_CATALOGO or r.conta_base_id in bases]


def _accounts_out(
    rows,
    roots_by_id: dict[UUID, str],
    names_by_id: dict[UUID, str] | None = None,
) -> list[PricingAccountOut]:
    por_id = {r.id: r for r in rows}
    filha_de = {r.conta_base_id: r.id for r in rows if r.canal == CANAL_CATALOGO}
    return [
        _account_out(
            r,
            roots_by_id,
            names_by_id,
            base=por_id.get(r.conta_base_id) if r.canal == CANAL_CATALOGO else None,
            conta_catalogo_id=filha_de.get(r.id),
        )
        for r in rows
    ]


async def _filha_de(session: AsyncSession, base_id: UUID) -> PricingAccount | None:
    return (
        await session.execute(
            select(PricingAccount).where(
                PricingAccount.conta_base_id == base_id,
                PricingAccount.canal == CANAL_CATALOGO,
            )
        )
    ).scalar_one_or_none()


async def _uma_conta_out(
    session: AsyncSession,
    row: PricingAccount,
    roots_by_id: dict[UUID, str],
    names_by_id: dict[UUID, str] | None = None,
) -> PricingAccountOut:
    """Saída de UMA conta (create/patch/department), com a base (se for
    filha) ou a filha (se for de kit) já resolvida."""
    if row.canal == CANAL_CATALOGO:
        base = await session.get(PricingAccount, row.conta_base_id)
        return _account_out(row, roots_by_id, names_by_id, base=base)
    filha = await _filha_de(session, row.id)
    return _account_out(
        row, roots_by_id, names_by_id, conta_catalogo_id=filha.id if filha else None
    )


async def _sincronizar_filha(session: AsyncSession, base: PricingAccount) -> None:
    """A conta base mudou (nome, tipo, aba, slots, ordem…): a coluna de
    catálogo acompanha. Base que deixou de ser do ML perde o catálogo."""
    filha = await _filha_de(session, base.id)
    if filha is None:
        return
    if base.platform != PricingPlatform.ML:
        await session.delete(filha)
        return
    for campo in _CAMPOS_ESPELHADOS_NA_FILHA:
        setattr(filha, campo, getattr(base, campo))


def _recusar_edicao_da_filha(row: PricingAccount) -> None:
    if row.canal == CANAL_CATALOGO:
        raise HTTPException(
            409,
            detail={
                "code": "conta_catalogo_herda_da_base",
                "message": (
                    "A coluna de catálogo usa os números da conta de kit: edite a conta base"
                ),
            },
        )


def _product_out(
    row: PricingProduct, leaves_by_id: dict[UUID, tuple[str, int]]
) -> PricingProductOut:
    out = PricingProductOut.model_validate(row)
    pair = leaves_by_id.get(row.segment_id)
    if pair:
        out.department, out.product_type = pair
    return out


# =============================================================================
# Accounts
# =============================================================================

def _exclude_archived_accounts(stmt):
    """Tira da Tabela de Preço as contas cuja loja (store_info) OU integração
    vinculada está arquivada. FK-based: linhas legadas sem FK permanecem
    visíveis (o `or_ is_(None)` evita a armadilha `NULL NOT IN (...)` = NULL =
    exclui). Usado no `/accounts` E no `/grid` — os dois têm que esconder as
    mesmas contas, senão a arquivada some da lista mas continua como coluna no
    grid de preços."""
    archived_stores = select(StoreInfo.id).where(StoreInfo.archived_at.is_not(None))
    archived_integs = select(Integration.id).where(Integration.archived_at.is_not(None))
    return stmt.where(
        or_(
            PricingAccount.store_info_id.is_(None),
            PricingAccount.store_info_id.not_in(archived_stores),
        ),
        or_(
            PricingAccount.integration_id.is_(None),
            PricingAccount.integration_id.not_in(archived_integs),
        ),
    )


async def _escopo_precos(session: AsyncSession, user: User) -> TeamScope:
    """Escopo de equipe da Tabela de Preços, com uma saída própria.

    Eduardo, 10/09/2026: "para o usuário israel, em tabela de preços, pode
    fazer aparecer todas as contas somente na tabela de preços para ele".
    A cerca normal é por EQUIPE (user.sales_teams → lojas da equipe), e ela
    vale pro sistema inteiro; quem precisa ver todas as contas SÓ aqui ganha
    a permissão `tabela_precos_todas_contas` (view) na tela de Usuários —
    mesma ideia do "gerente de etiquetas" no Controle de Estoque. Nenhuma
    outra aba muda: fora da Tabela de Preços a equipe continua cercando.
    """
    perms = (user.permissions or {}).get("tabela_precos_todas_contas") or {}
    if isinstance(perms, dict) and perms.get("view"):
        return TeamScope(unrestricted=True)
    return await resolve_team_scope(session, user)


def _team_scope_accounts(stmt, scope):
    """Restringe as contas da Tabela de Preço às lojas da equipe do usuário
    (não-admin com equipe). A conta casa por `store_info_id` OU por
    `integration_id` da(s) loja(s) da equipe. Aplicado no `/accounts` E no
    `/grid` (as colunas do grid vêm da mesma fonte de contas)."""
    if scope.unrestricted:
        return stmt
    return stmt.where(
        or_(
            PricingAccount.store_info_id.in_(scope.store_info_ids),
            PricingAccount.integration_id.in_(scope.integration_ids),
            # Coluna de catálogo não tem loja nem integração: passa aqui e só
            # fica se a base dela ficou (_so_filhas_com_base).
            PricingAccount.canal == CANAL_CATALOGO,
        )
    )


@router.get("/accounts", response_model=list[PricingAccountOut])
async def list_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "view"))
    ],
    department: str | None = Query(None),
    platform: str | None = Query(None),
) -> list[PricingAccountOut]:
    roots_by_id, _, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        stmt = stmt.where(PricingAccount.segment_id == rid)
    if platform:
        stmt = stmt.where(PricingAccount.platform == _coerce_platform(platform))
    stmt = _exclude_archived_accounts(stmt)
    stmt = _team_scope_accounts(stmt, await _escopo_precos(session, user))
    stmt = stmt.order_by(PricingAccount.sort_order, PricingAccount.name)
    rows = _so_filhas_com_base((await session.execute(stmt)).scalars().all())
    names_by_id = await _segment_names_by_id(session)
    return _accounts_out(rows, roots_by_id, names_by_id)


@router.post(
    "/accounts",
    response_model=PricingAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    body: PricingAccountCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    data = body.model_dump(exclude={"password", "department"})
    data["platform"] = _coerce_platform(body.platform)
    # Resolve segment_id: prefer explicit, fall back to department slug, then
    # default to root "celular" for backward compatibility.
    if data.get("segment_id") is None:
        sid = await _resolve_root_segment_id(session, body.department or "celular")
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        data["segment_id"] = sid
    # Slots default: sem vínculo slot↔segmento a página Margem não consegue
    # casar o frete projetado (ver _default_slot_segments).
    if not any(data.get(f"slot{n}_segment_id") for n in (1, 2, 3, 4, 5)):
        slots = await _default_slot_segments(session, data["segment_id"])
        for n in (1, 2, 3, 4, 5):
            data[f"slot{n}_segment_id"] = slots[n - 1]
    row = PricingAccount(user_id=user.id, **data)
    if body.password:
        row.password_enc = encrypt(body.password)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    names_by_id = await _segment_names_by_id(session)
    return await _uma_conta_out(session, row, roots_by_id, names_by_id)


@router.patch("/accounts/{account_id}", response_model=PricingAccountOut)
async def patch_account(
    account_id: UUID,
    body: PricingAccountPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    row = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "account_not_found"})
    _recusar_edicao_da_filha(row)

    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        pwd = data.pop("password")
        row.password_enc = encrypt(pwd) if pwd else None
    if "platform" in data and data["platform"] is not None:
        data["platform"] = _coerce_platform(data["platform"])
    # Translate legacy department-string into segment_id; explicit segment_id
    # wins if both are present.
    dept_str = data.pop("department", None)
    if "segment_id" not in data and dept_str:
        sid = await _resolve_root_segment_id(session, dept_str)
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        data["segment_id"] = sid
    # Conta mudou de aba sem slots explícitos no body → re-deriva os slots
    # default da aba nova. Slots da aba velha nunca casam com os leaves da
    # nova na view de margens e o frete projetado sumiria (ver
    # _default_slot_segments).
    if (
        data.get("segment_id") is not None
        and data["segment_id"] != row.segment_id
        and not any(data.get(f"slot{n}_segment_id") for n in (1, 2, 3, 4, 5))
    ):
        slots = await _default_slot_segments(session, data["segment_id"])
        for n in (1, 2, 3, 4, 5):
            data[f"slot{n}_segment_id"] = slots[n - 1]
    for k, v in data.items():
        setattr(row, k, v)
    await _sincronizar_filha(session, row)
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    names_by_id = await _segment_names_by_id(session)
    return await _uma_conta_out(session, row, roots_by_id, names_by_id)


@router.delete(
    "/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_account(
    account_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "delete"))
    ],
) -> None:
    res = await session.execute(
        delete(PricingAccount).where(
            and_(
                PricingAccount.id == account_id,
                user_scope(PricingAccount, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "account_not_found"})
    await session.commit()
    return None


# =============================================================================
# Products
# =============================================================================

@router.get("/products", response_model=list[PricingProductOut])
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
    department: str | None = Query(None),
    is_active: bool | None = Query(None),
) -> list[PricingProductOut]:
    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    if is_active is not None:
        stmt = stmt.where(PricingProduct.is_active == is_active)
    # Acessório avulso (balança, chaveiro, encosto, rodinha, mochila) vai pro
    # FIM da lista — Eduardo 04/09: "os acessorios balança cheveiro, etc deixe
    # tudo la em baixo". O que separa é o SKU: produto principal começa com
    # letra + dígito (b005, b109…); acessório usa outro prefixo (a015, bp003).
    # Nos departamentos sem esse padrão (celular) todos caem no mesmo grupo e a
    # ordem por SKU segue igual à de antes.
    stmt = stmt.order_by(
        case((PricingProduct.sku.op("~")(r"^b[0-9]"), 0), else_=1),
        PricingProduct.sku,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_product_out(r, leaves_by_id) for r in rows]


@router.post(
    "/products",
    response_model=PricingProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    body: PricingProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductOut:
    data = body.model_dump(exclude={"department", "product_type"})
    if data.get("segment_id") is None:
        sid = await _resolve_leaf_segment_id(
            session, body.department or "celular", body.product_type or 2
        )
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_segment"})
        data["segment_id"] = sid
    row = PricingProduct(user_id=user.id, **data)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_pricing_products_user_sku" in str(e.orig):
            raise HTTPException(409, detail={"code": "sku_exists"}) from e
        raise
    await session.refresh(row)
    _, leaves_by_id, _ = await _segment_index(session)
    return _product_out(row, leaves_by_id)


@router.patch("/products/{product_id}", response_model=PricingProductOut)
async def patch_product(
    product_id: UUID,
    body: PricingProductPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductOut:
    row = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    data = body.model_dump(exclude_unset=True)
    # Apagar o link das fotos na tela = desligar a pasta do MEGA desta linha.
    # Antes só o link sumia: o fotos_path ficava, e o próximo envio continuava
    # indo para a pasta antiga que a tela já dizia não ter. Vão junto a
    # embalagem (subpasta dela) e as contagens, que eram daquela pasta.
    if "fotos_url" in data and not (data["fotos_url"] or "").strip():
        data.update(
            fotos_url=None,
            fotos_path=None,
            embalagens_url=None,
            embalagens_path=None,
            fotos_count=None,
            videos_count=None,
            embalagens_count=None,
            midias_contadas_em=None,
        )
    # Translate (department + product_type) → segment_id when the caller used
    # the legacy fields. Explicit segment_id wins if both are present.
    dept_str = data.pop("department", None)
    pt_val = data.pop("product_type", None)
    if "segment_id" not in data and (dept_str or pt_val is not None):
        # Need current values for whichever field was left out.
        _, leaves_by_id, _ = await _segment_index(session)
        cur = leaves_by_id.get(row.segment_id, ("celular", 2))
        sid = await _resolve_leaf_segment_id(
            session, dept_str or cur[0], pt_val if pt_val is not None else cur[1]
        )
        if sid is None:
            raise HTTPException(400, detail={"code": "invalid_segment"})
        data["segment_id"] = sid
    for k, v in data.items():
        setattr(row, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "uq_pricing_products_user_sku" in str(e.orig):
            raise HTTPException(409, detail={"code": "sku_exists"}) from e
        raise
    await session.refresh(row)
    _, leaves_by_id, _ = await _segment_index(session)
    return _product_out(row, leaves_by_id)


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "delete"))
    ],
) -> dict:
    """Exclui o produto e manda a pasta dele no MEGA para a LIXEIRA do MEGA.

    Eduardo, 29/09/2026: "quando eu remover um produto, ele tem que remover do
    MEGA também". Lixeira, não exclusão definitiva — recupera pelo site do MEGA.
    A pasta FICA quando outra linha ainda usa (as variações de memória dividem
    a pasta: S7 16.128 e S7 32.256) ou quando está fora de /Celular, /Malas e
    /uranyx. O produto sai do banco ANTES: MEGA fora do ar não pode impedir a
    exclusão — a resposta diz o que aconteceu com a pasta, e a tela mostra.
    """
    row = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    pasta = row.fotos_path
    sku = row.sku
    await session.execute(delete(PricingProduct).where(PricingProduct.id == product_id))
    await session.commit()

    out: dict = {"pasta": pasta, "mega": "sem_pasta"}
    if not (pasta or "").strip():
        return out
    # Quem mais usa a pasta — de QUALQUER usuário: a pasta é uma só no MEGA.
    outros = (
        await session.execute(
            select(PricingProduct.sku).where(PricingProduct.fotos_path == pasta).limit(10)
        )
    ).scalars().all()
    if outros:
        return {**out, "mega": "mantida", "usada_por": list(outros)}
    if not any(pasta.startswith(f"{r}/") for r in RAIZ_POR_DEPARTAMENTO.values()):
        return {**out, "mega": "fora_das_raizes"}
    try:
        res = await sidecar_request("POST", "/lixeira", json={"path": pasta}, timeout=180.0)
    except MegaError as exc:
        logger.warning("produto_excluido_pasta_mega_falhou", sku=sku, pasta=pasta, erro=exc.message)
        return {**out, "mega": "erro", "erro": exc.message}
    logger.info("produto_excluido_pasta_na_lixeira", sku=sku, pasta=pasta, user_id=str(user.id))
    return {**out, "mega": "lixeira", "lixeira": res.get("lixeira")}


@router.post("/products/import", response_model=PricingProductImportResult)
async def import_products(
    body: PricingProductImport,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> PricingProductImportResult:
    if not body.items:
        return PricingProductImportResult(created=0, updated=0, skipped=0)

    existing = (
        await session.execute(
            select(PricingProduct).where(user_scope(PricingProduct, user))
        )
    ).scalars().all()
    by_sku = {row.sku: row for row in existing}

    created = 0
    updated = 0
    skipped = 0
    for item in body.items:
        if not item.sku:
            skipped += 1
            continue
        data = item.model_dump(exclude={"department", "product_type"})
        # Linha de importação sem a coluna Catálogo não apaga o preço de
        # catálogo que já existe.
        if "preco_catalogo" not in item.model_fields_set:
            data.pop("preco_catalogo", None)
        if data.get("segment_id") is None:
            sid = await _resolve_leaf_segment_id(
                session,
                item.department or "celular",
                item.product_type if item.product_type is not None else 2,
            )
            if sid is None:
                skipped += 1
                continue
            data["segment_id"] = sid
        row = by_sku.get(item.sku)
        if row is None:
            session.add(PricingProduct(user_id=user.id, **data))
            created += 1
        else:
            for k, v in data.items():
                setattr(row, k, v)
            updated += 1

    await session.commit()
    return PricingProductImportResult(created=created, updated=updated, skipped=skipped)


# =============================================================================
# Overrides
# =============================================================================

@router.get("/overrides", response_model=list[PricingOverrideOut])
async def list_overrides(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: str | None = Query(None),
) -> list[PricingOverrideOut]:
    stmt = select(PricingOverride).where(user_scope(PricingOverride, user))
    if department:
        _, leaves_by_id, root_id_by_slug = await _segment_index(session)
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.join(
            PricingProduct, PricingProduct.id == PricingOverride.pricing_product_id
        ).where(PricingProduct.segment_id.in_(leaf_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return [PricingOverrideOut.model_validate(r) for r in rows]


@router.put("/overrides", response_model=PricingOverrideOut)
async def upsert_override(
    body: PricingOverrideUpsert,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingOverrideOut:
    cell_status = _coerce_cell_status(body.cell_status) or CellStatus.AUTO

    # Validate FK ownership
    prod = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == body.pricing_product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if prod is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    acc = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == body.pricing_account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if acc is None:
        raise HTTPException(404, detail={"code": "account_not_found"})

    row = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_product_id == body.pricing_product_id,
                    PricingOverride.pricing_account_id == body.pricing_account_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = PricingOverride(
            user_id=user.id,
            pricing_product_id=body.pricing_product_id,
            pricing_account_id=body.pricing_account_id,
            price_override=body.price_override,
            cell_status=cell_status,
            cell_color=body.cell_color,
        )
        session.add(row)
    else:
        row.price_override = body.price_override
        row.cell_status = cell_status
        # Only overwrite cell_color when the caller explicitly sent one;
        # the dedicated cell-color endpoint is how it's normally set, so
        # the price-edit path must not silently clear an existing highlight.
        if body.cell_color is not None:
            row.cell_color = body.cell_color

    await session.commit()
    await session.refresh(row)
    return PricingOverrideOut.model_validate(row)


@router.put("/overrides/cell-status", response_model=PricingOverrideOut)
async def set_cell_status(
    body: PricingOverrideCellStatus,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingOverrideOut:
    cell_status = _coerce_cell_status(body.cell_status) or CellStatus.AUTO

    # Auto-create row when toggling cell status on a cell that has no override yet.
    row = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_product_id == body.pricing_product_id,
                    PricingOverride.pricing_account_id == body.pricing_account_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        prod_owned = (
            await session.execute(
                select(PricingProduct.id).where(
                    and_(
                        PricingProduct.id == body.pricing_product_id,
                        user_scope(PricingProduct, user),
                    )
                )
            )
        ).scalar_one_or_none()
        acc_owned = (
            await session.execute(
                select(PricingAccount.id).where(
                    and_(
                        PricingAccount.id == body.pricing_account_id,
                        user_scope(PricingAccount, user),
                    )
                )
            )
        ).scalar_one_or_none()
        if prod_owned is None or acc_owned is None:
            raise HTTPException(404, detail={"code": "override_target_not_found"})
        row = PricingOverride(
            user_id=user.id,
            pricing_product_id=body.pricing_product_id,
            pricing_account_id=body.pricing_account_id,
            cell_status=cell_status,
        )
        session.add(row)
    else:
        row.cell_status = cell_status

    await session.commit()
    await session.refresh(row)
    return PricingOverrideOut.model_validate(row)


# Allowed swatch values — match the frontend palette. NULL clears the
# highlight. Adding/renaming is a 1-file change (here + the FE
# CELL_COLORS array) with no migration.
_ALLOWED_CELL_COLORS: frozenset[str] = frozenset({
    "red", "orange", "yellow", "green", "blue", "purple", "pink", "gray",
})


@router.put("/overrides/cell-color", response_model=PricingOverrideOut)
async def set_cell_color(
    body: PricingOverrideCellColor,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> PricingOverrideOut:
    """Set or clear the Excel-style highlight color on one pricing cell.

    Auto-creates the override row with sentinel values (no price, status
    AUTO) when the cell didn't have an override yet — picking a color on
    a never-touched cell shouldn't drag any other state with it.
    """
    if body.cell_color is not None and body.cell_color not in _ALLOWED_CELL_COLORS:
        raise HTTPException(400, detail={
            "code": "invalid_color",
            "allowed": sorted(_ALLOWED_CELL_COLORS),
        })

    row = (
        await session.execute(
            select(PricingOverride).where(
                and_(
                    PricingOverride.pricing_product_id == body.pricing_product_id,
                    PricingOverride.pricing_account_id == body.pricing_account_id,
                    user_scope(PricingOverride, user),
                )
            )
        )
    ).scalar_one_or_none()

    if row is None:
        prod_owned = (
            await session.execute(
                select(PricingProduct.id).where(
                    and_(
                        PricingProduct.id == body.pricing_product_id,
                        user_scope(PricingProduct, user),
                    )
                )
            )
        ).scalar_one_or_none()
        acc_owned = (
            await session.execute(
                select(PricingAccount.id).where(
                    and_(
                        PricingAccount.id == body.pricing_account_id,
                        user_scope(PricingAccount, user),
                    )
                )
            )
        ).scalar_one_or_none()
        if prod_owned is None or acc_owned is None:
            raise HTTPException(404, detail={"code": "override_target_not_found"})
        row = PricingOverride(
            user_id=user.id,
            pricing_product_id=body.pricing_product_id,
            pricing_account_id=body.pricing_account_id,
            cell_status=CellStatus.AUTO,
            cell_color=body.cell_color,
        )
        session.add(row)
    else:
        row.cell_color = body.cell_color

    await session.commit()
    await session.refresh(row)
    return PricingOverrideOut.model_validate(row)


@router.delete("/overrides", status_code=status.HTTP_204_NO_CONTENT)
async def remove_override(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "delete"))],
    pricing_product_id: Annotated[UUID, Query(...)],
    pricing_account_id: Annotated[UUID, Query(...)],
) -> None:
    res = await session.execute(
        delete(PricingOverride).where(
            and_(
                PricingOverride.pricing_product_id == pricing_product_id,
                PricingOverride.pricing_account_id == pricing_account_id,
                user_scope(PricingOverride, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "override_not_found"})
    await session.commit()
    return None


# =============================================================================
# Push (9b) — single + batch, idempotency via header
# =============================================================================

@router.post("/push", response_model=PricingPushOut)
async def push_prices(
    body: PricingPushBatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PricingPushOut:
    if not body.items:
        return PricingPushOut(results=[])

    results: list[PricingPushItemOut] = []
    for i, item in enumerate(body.items):
        # Per-item key when batch has >1 entry — keeps each (acct,prod) replay-safe.
        key = (
            f"{idempotency_key}:{i}" if idempotency_key and len(body.items) > 1
            else idempotency_key
        )
        outcome = await push_one(
            session,
            user=user,
            account_id=item.pricing_account_id,
            product_id=item.pricing_product_id,
            idempotency_key=key,
        )
        results.append(
            PricingPushItemOut(
                pricing_account_id=item.pricing_account_id,
                pricing_product_id=item.pricing_product_id,
                ok=outcome.ok,
                code=outcome.code,
                detail=outcome.detail,
                price=outcome.price,
                item_id=outcome.item_id,
                variation_id=outcome.variation_id,
                cached=outcome.cached,
            )
        )
    await session.commit()
    return PricingPushOut(results=results)


# =============================================================================
# Grid (9b) — matrix produtos × contas com preço calculado
# =============================================================================

@router.get("/grid", response_model=PricingGridOut)
async def get_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: str | None = Query(None),
) -> PricingGridOut:
    roots_by_id, leaves_by_id, root_id_by_slug = await _segment_index(session)
    accounts_stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    products_stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        accounts_stmt = accounts_stmt.where(PricingAccount.segment_id == rid)
        products_stmt = products_stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    # Mesma exclusão do `/accounts`: conta de loja/integração arquivada não
    # pode virar coluna no grid de preços.
    accounts_stmt = _exclude_archived_accounts(accounts_stmt)
    # Mesmo escopo por equipe do `/accounts`: as colunas do grid não podem
    # mostrar contas de lojas fora da equipe do usuário.
    accounts_stmt = _team_scope_accounts(
        accounts_stmt, await _escopo_precos(session, user)
    )
    accounts = _so_filhas_com_base(
        (
            await session.execute(
                accounts_stmt.order_by(PricingAccount.sort_order, PricingAccount.name)
            )
        ).scalars().all()
    )
    products = (
        await session.execute(products_stmt.order_by(PricingProduct.sku))
    ).scalars().all()

    # Colunas de catálogo: a base de cada uma (números + integração) e os
    # anúncios de catálogo dessas integrações, carregados UMA vez para a grade
    # inteira (o resolvedor roda em memória por célula).
    contas_por_id = {a.id: a for a in accounts}
    filhas = [a for a in accounts if a.canal == CANAL_CATALOGO]
    links_catalogo: dict[UUID, list[ProductLink]] = defaultdict(list)
    sku_dos_links: dict[UUID, str] = {}
    integs_catalogo = {
        contas_por_id[f.conta_base_id].integration_id
        for f in filhas
        if contas_por_id[f.conta_base_id].integration_id is not None
    }
    if integs_catalogo:
        for lk in (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.integration_id.in_(integs_catalogo),
                    ProductLink.catalog_listing.is_(True),
                )
            )
        ).scalars().all():
            links_catalogo[lk.integration_id].append(lk)
        ids_produtos = {lk.product_id for lks in links_catalogo.values() for lk in lks}
        if ids_produtos:
            sku_dos_links = {
                pid: sku
                for pid, sku in (
                    await session.execute(
                        select(Product.id, Product.sku).where(Product.id.in_(ids_produtos))
                    )
                ).all()
                if sku
            }

    def _dept_da_conta(acc: PricingAccount) -> str | None:
        return roots_by_id.get(acc.segment_id) or (leaves_by_id.get(acc.segment_id) or (None,))[0]

    overrides = (
        await session.execute(
            select(PricingOverride).where(user_scope(PricingOverride, user))
        )
    ).scalars().all()
    by_pair = {(o.pricing_product_id, o.pricing_account_id): o for o in overrides}

    # cell_status reflects only what's stored in pricing_overrides. NA/SV are
    # user-set flags persisted after a failed push (SSH semantics); the grid
    # never infers them. Cells without a calculable price render as "—" in
    # the UI via the cellLabel fallback, not as NA.
    cells: list[PricingGridCell] = []
    for prod in products:
        pair = leaves_by_id.get(prod.segment_id)
        prod_type = pair[1] if pair else None
        for acc in accounts:
            ovr = by_pair.get((prod.id, acc.id))
            base = contas_por_id.get(acc.conta_base_id) if acc.canal == CANAL_CATALOGO else None
            outcome = calculate(acc, prod, ovr, prod_type, conta_base=base)
            catalogo = None
            if acc.canal == CANAL_CATALOGO:
                integ_id = base.integration_id if base is not None else None
                resolucao = resolver_anuncios(
                    links_catalogo.get(integ_id, ()) if integ_id else (),
                    sku_dos_links,
                    pricing_sku=prod.sku,
                    dept=_dept_da_conta(acc),
                    plataforma="ml",
                    canal=CANAL_CATALOGO,
                    listing_type_conta=acc.listing_type,
                )
                catalogo = info_celula_catalogo(
                    resolucao,
                    sem_preco=outcome.price is None and outcome.detail == "sem_preco_catalogo",
                    sem_integracao=integ_id is None,
                )
            cells.append(
                PricingGridCell(
                    pricing_account_id=acc.id,
                    pricing_product_id=prod.id,
                    price=outcome.price,
                    source=outcome.source,
                    cell_status=(ovr.cell_status.value if ovr else "auto"),
                    has_override=ovr is not None,
                    cell_color=(ovr.cell_color if ovr else None),
                    catalogo=catalogo,
                )
            )

    names_by_id = await _segment_names_by_id(session)
    return PricingGridOut(
        accounts=_accounts_out(accounts, roots_by_id, names_by_id),
        products=[_product_out(p, leaves_by_id) for p in products],
        cells=cells,
    )


# =============================================================================
# Bulk push job (9c) — Arq
# =============================================================================

@router.post(
    "/push-batch",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_push_batch(
    body: PricingPushBatchIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    notify_telegram: bool = Query(True),
) -> JobCreatedOut:
    if not body.items:
        raise HTTPException(400, detail={"code": "empty_batch"})

    items = [
        {
            "pricing_account_id": str(it.pricing_account_id),
            "pricing_product_id": str(it.pricing_product_id),
        }
        for it in body.items
    ]
    job = BackgroundJob(
        type=BackgroundJobType.PUSH_PRICES_BATCH,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={
            "count": len(items),
            "idempotency_prefix": idempotency_key,
            "notify_telegram": notify_telegram,
        },
        total=len(items),
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "push_prices_batch_run",
        str(job.id),
        str(user.id),
        items,
        idempotency_key,
        notify_telegram,
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


# =============================================================================
# Manual Telegram report (9c)
# =============================================================================

@router.post("/push-report", status_code=status.HTTP_204_NO_CONTENT)
async def send_push_report(
    body: PricingPushReportIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    chat_id = body.chat_id
    if not chat_id:
        from app.models import UserSettings  # local import — avoids cycle

        st = (
            await session.execute(
                select(UserSettings).where(user_scope(UserSettings, user))
            )
        ).scalar_one_or_none()
        chat_id = st.telegram_chat_id if st else None
    try:
        cli = TelegramClient(default_chat_id=chat_id)
        await cli.send_message(body.summary)
    except TelegramConfigError as e:
        raise HTTPException(
            400,
            detail={"code": "telegram_not_configured", "reason": str(e)},
        ) from e


# =============================================================================
# Stock + sales maps (used by the grid Bling/7d/30d columns)
# =============================================================================


@router.get("/stock-map")
async def get_stock_map(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
    department: Annotated[str | None, Query()] = None,
) -> dict[str, int]:
    """Returns {pricing_product_id: total_stock}.

    Algorithm (per spec):
      1. Split pricing_product.sku by comma → list of bases.
      2. For each base (celular): find products whose sku startswith
         base + "." AND does NOT contain "+". Pick the single "simple"
         representative — preferred order: shortest sku, then lex-min
         (so `x043.ra` is picked over `x043.kit2`). Take its stock.
         If no dotted variant exists, fall back to an exact `base` match.
      3. For mala / eletro / catalogo: pieces are already simple SKUs;
         use an exact case-insensitive lookup.
      4. SUM contributions across pieces. Missing pieces contribute 0.
    """
    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        stmt = stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    pricing_rows = (await session.execute(stmt)).scalars().all()
    if not pricing_rows:
        logger.info(
            "pricing.stock_map.empty",
            user_id=str(user.id),
            department=department,
            reason="no_pricing_products",
        )
        return {}

    product_rows = (
        await session.execute(
            select(Product.sku, Product.stock).where(user_scope(Product, user))
        )
    ).all()

    # Two indexes:
    #   exact_stock[sku_lower] = stock (max on duplicate keys)
    #   variant_pick[base]     = (preferred_sku, stock) — best simple variant
    exact_stock: dict[str, int] = {}
    variant_pick: dict[str, tuple[str, int]] = {}
    for sku, stock in product_rows:
        if not sku:
            continue
        skl = sku.strip().lower()
        st = int(stock or 0)
        exact_stock[skl] = max(exact_stock.get(skl, 0), st)
        if "+" in skl or "." not in skl:
            continue
        base = skl.split(".", 1)[0]
        if not base:
            continue
        cur = variant_pick.get(base)
        if cur is None or (len(skl), skl) < (len(cur[0]), cur[0]):
            variant_pick[base] = (skl, st)

    from app.services.pricing.audit import _dept_value, _load_segment_roots

    segment_roots = await _load_segment_roots(session)

    out: dict[str, int] = {}
    matched_pids = 0
    matched_pieces_total = 0
    for pp in pricing_rows:
        dept_v = _dept_value(pp, segment_roots)
        total = 0
        any_match = False
        for piece in (pp.sku or "").split(","):
            key = piece.strip().lower()
            if not key:
                continue
            if dept_v == "celular":
                hit = variant_pick.get(key)
                if hit is not None:
                    total += hit[1]
                    any_match = True
                    matched_pieces_total += 1
                elif key in exact_stock:
                    total += exact_stock[key]
                    any_match = True
                    matched_pieces_total += 1
            else:
                if key in exact_stock:
                    total += exact_stock[key]
                    any_match = True
                    matched_pieces_total += 1
        out[str(pp.id)] = total
        if any_match:
            matched_pids += 1

    logger.info(
        "pricing.stock_map",
        user_id=str(user.id),
        department=department,
        pricing_products=len(pricing_rows),
        product_skus=len(exact_stock),
        celular_bases=len(variant_pick),
        matched_pricing_products=matched_pids,
        matched_pieces=matched_pieces_total,
    )
    return out


@router.get("/sales-map")
async def get_sales_map(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos", "view"))],
    department: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict[str, int]:
    """Returns {pricing_product_id: units_sold_in_last_N_days} from bling_orders.

    bling_orders is single-tenant (no user_scope). Cancelled / returned orders
    are excluded by `situacao`.
    Sales sum every matched product key — see celular base-SKU rule.
    """
    from datetime import UTC, datetime, timedelta

    _, leaves_by_id, root_id_by_slug = await _segment_index(session)
    pp_stmt = select(PricingProduct).where(user_scope(PricingProduct, user))
    if department:
        if root_id_by_slug.get(department) is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        leaf_ids = [lid for lid, (rs, _pt) in leaves_by_id.items() if rs == department]
        pp_stmt = pp_stmt.where(PricingProduct.segment_id.in_(leaf_ids))
    pricing_rows = (await session.execute(pp_stmt)).scalars().all()
    if not pricing_rows:
        logger.info(
            "pricing.sales_map.empty",
            user_id=str(user.id),
            department=department,
            days=days,
            reason="no_pricing_products",
        )
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=days)
    excluded_situations = ("Cancelado", "Devolvido")
    rows = (
        await session.execute(
            select(BlingOrder.item_codigo, BlingOrder.item_quantidade).where(
                and_(
                    BlingOrder.item_codigo.isnot(None),
                    BlingOrder.item_codigo != "",
                    BlingOrder.data >= cutoff,
                    BlingOrder.situacao.notin_(excluded_situations),
                )
            )
        )
    ).all()

    sales_by_key: dict[str, int] = {}
    for codigo, qty in rows:
        key = (codigo or "").strip().lower()
        if not key:
            continue
        sales_by_key[key] = sales_by_key.get(key, 0) + int(qty or 0)

    from app.services.pricing.audit import _load_segment_roots

    segment_roots = await _load_segment_roots(session)
    matches = match_pricing_to_product_keys(
        pricing_rows, sales_by_key.keys(), segment_roots
    )

    out: dict[str, int] = {}
    matched_pids = 0
    for pp in pricing_rows:
        keys = matches.get(pp.id)
        if not keys:
            out[str(pp.id)] = 0
            continue
        out[str(pp.id)] = sum(sales_by_key.get(k, 0) for k in keys)
        matched_pids += 1

    logger.info(
        "pricing.sales_map",
        user_id=str(user.id),
        department=department,
        days=days,
        pricing_products=len(pricing_rows),
        order_rows=len(rows),
        sales_keys=len(sales_by_key),
        matched_pricing_products=matched_pids,
    )
    return out


# =============================================================================
# Actual marketplace prices — per-account current price for a pricing_product
# =============================================================================

@router.get("/actual-prices/{pricing_product_id}")
async def get_actual_prices(
    pricing_product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    department: Annotated[str | None, Query()] = None,
) -> dict[str, float | None]:
    """Fetch the *current* listing price from each connected marketplace for
    every pricing_account that could carry this product. Frontend uses the
    response to show "real: R$ XXX" alongside the computed price so the
    seller can spot drift between the table and what's live.

    Phase 1: ML only. Other platforms return null until their `get_price`
    helper lands.

    Os anúncios lidos são os do resolvedor único (`services/pricing/anuncios`)
    — os mesmos que o envio usaria, por canal. Na coluna de catálogo entram
    também os anúncios bloqueados (sincronizado/pausado): o preço vivo deles
    é justamente o que a tela quer mostrar.
    """
    from app.services.marketplaces.factory import client_for

    product = (
        await session.execute(
            select(PricingProduct).where(
                and_(
                    PricingProduct.id == pricing_product_id,
                    user_scope(PricingProduct, user),
                )
            )
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, detail={"code": "product_not_found"})

    roots_by_id, leaves_by_id, root_id_by_slug = await _segment_index(session)
    accounts_stmt = select(PricingAccount).where(user_scope(PricingAccount, user))
    if department:
        rid = root_id_by_slug.get(department)
        if rid is None:
            raise HTTPException(400, detail={"code": "invalid_department"})
        accounts_stmt = accounts_stmt.where(PricingAccount.segment_id == rid)
    accounts = (await session.execute(accounts_stmt)).scalars().all()
    contas_por_id = {a.id: a for a in accounts}
    faltam = {
        a.conta_base_id
        for a in accounts
        if a.canal == CANAL_CATALOGO and a.conta_base_id not in contas_por_id
    }
    if faltam:
        for a in (
            await session.execute(select(PricingAccount).where(PricingAccount.id.in_(faltam)))
        ).scalars().all():
            contas_por_id[a.id] = a

    result: dict[str, float | None] = {str(acc.id): None for acc in accounts}

    # Produtos candidatos: SKU começando por algum código base da linha. É um
    # superconjunto — quem decide de verdade é o resolvedor.
    bases = {v.split(".", 1)[0].lower() for v in variants_of(product.sku)}
    if not bases:
        return result
    sku_map: dict[UUID, str] = {
        pid: sku
        for pid, sku in (
            await session.execute(
                select(Product.id, Product.sku).where(
                    user_scope(Product, user),
                    or_(
                        *[
                            func.lower(Product.sku).like(_like_prefixo(b), escape="\\")
                            for b in bases
                        ]
                    ),
                )
            )
        ).all()
        if sku
    }
    if not sku_map:
        return result

    def _dept(acc: PricingAccount) -> str | None:
        return roots_by_id.get(acc.segment_id) or (leaves_by_id.get(acc.segment_id) or (None,))[0]

    integration_cache: dict[UUID, Integration | None] = {}
    links_cache: dict[UUID, list[ProductLink]] = {}
    client_cache: dict[UUID, object] = {}

    for acc in accounts:
        dona = contas_por_id.get(acc.conta_base_id) if acc.canal == CANAL_CATALOGO else acc
        if dona is None or dona.integration_id is None:
            continue
        integ_id = dona.integration_id

        if integ_id not in integration_cache:
            integration_cache[integ_id] = (
                await session.execute(
                    select(Integration).where(
                        and_(
                            Integration.id == integ_id,
                            user_scope(Integration, user),
                        )
                    )
                )
            ).scalar_one_or_none()
        integ = integration_cache[integ_id]
        if integ is None:
            continue
        # Only platforms with get_listing_price implemented carry real data;
        # the rest silently leave the cell at null.
        platform_value = integ.platform.value if hasattr(integ.platform, "value") else integ.platform
        if platform_value not in ("ml", "shopee", "amazon", "tiktok"):
            continue

        if integ_id not in links_cache:
            links_cache[integ_id] = list(
                (
                    await session.execute(
                        select(ProductLink).where(
                            ProductLink.integration_id == integ_id,
                            ProductLink.product_id.in_(list(sku_map)),
                        )
                    )
                ).scalars().all()
            )
        resolucao = resolver_anuncios(
            links_cache[integ_id],
            sku_map,
            pricing_sku=product.sku,
            dept=_dept(dona),
            plataforma=platform_value,
            canal=acc.canal or CANAL_KIT,
            listing_type_conta=acc.listing_type,
        )
        links = resolucao.todos
        if not links:
            continue

        if integ_id not in client_cache:
            from app.security.cipher import decrypt_json
            creds = decrypt_json(integ.credentials)
            client_cache[integ_id] = client_for(integ.platform, creds)
        client = client_cache[integ_id]

        # Try each candidate link until one returns a price — protects
        # against single-listing failures (closed/under_review) blanking
        # the whole cell.
        for link in links:
            try:
                price = await client.get_listing_price(link)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                price = None
            if price is not None:
                result[str(acc.id)] = float(price)
                break

    return result


def _like_prefixo(codigo: str) -> str:
    """Padrão LIKE "começa com" para um código de SKU (escapa % e _)."""
    esc = codigo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + "%"


# =============================================================================
# SKU Audit (9d) — scan + dismiss/undismiss
# =============================================================================

@router.get("/sku-audit", response_model=list[SkuAuditRow])
async def get_sku_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    include_dismissed: Annotated[bool, Query()] = False,
) -> list[SkuAuditRow]:
    rows = await scan_missing_skus(
        session, user_id=user.id, include_dismissed=include_dismissed
    )
    return [SkuAuditRow(**r) for r in rows]


@router.get("/sku-audit/dismissed", response_model=list[str])
async def list_dismissed_skus(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
) -> list[str]:
    # Dispensar é global: retorna todos os SKUs dispensados por qualquer usuário.
    rows = (
        await session.execute(
            select(AuditDismissedSku.sku)
        )
    ).scalars().all()
    return list(rows)


@router.post("/sku-audit/{sku}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_sku(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    # Dispensar é global: se qualquer usuário já dispensou este SKU, não recria.
    # Guardamos user_id apenas como rastro de quem dispensou primeiro.
    existing = (
        await session.execute(
            select(AuditDismissedSku)
            .where(AuditDismissedSku.sku == sku)
            .limit(1)
        )
    ).scalars().first()
    if existing is None:
        session.add(AuditDismissedSku(user_id=user.id, sku=sku))
        await session.commit()
    return None


@router.post("/sku-audit/{sku}/undismiss", status_code=status.HTTP_204_NO_CONTENT)
async def undismiss_sku(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "edit"))
    ],
) -> None:
    # Dispensar é global: remove a dispensa deste SKU para todos os usuários.
    await session.execute(
        delete(AuditDismissedSku).where(AuditDismissedSku.sku == sku)
    )
    await session.commit()
    return None


# =============================================================================
# Competitor (9d) — ML public search com cache 5min
# =============================================================================

@router.get("/competitor-prices", response_model=list[CompetitorPriceRow])
async def competitor_prices(
    user: Annotated[
        User, Depends(require_permission("tabela_precos", "view"))
    ],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[CompetitorPriceRow]:
    del user  # auth-only dep
    entries = await search_competitors(q, limit=limit)
    return [
        CompetitorPriceRow(
            item_id=e.item_id,
            title=e.title,
            price=e.price,
            currency=e.currency,
            permalink=e.permalink,
            seller_id=e.seller_id,
            condition=e.condition,
            sold_quantity=e.sold_quantity,
            available_quantity=e.available_quantity,
            thumbnail=e.thumbnail,
        )
        for e in entries
    ]


# =============================================================================
# Bling cost sync (9d) — Arq job
# =============================================================================

@router.post(
    "/jobs/sync-bling-costs",
    response_model=JobCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_sync_bling_costs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> JobCreatedOut:
    job = BackgroundJob(
        type=BackgroundJobType.SYNC_BLING_COSTS,
        status=BackgroundJobStatus.PENDING,
        created_by=user.id,
        payload={},
    )
    session.add(job)
    await session.flush()

    pool = await get_arq_pool()
    arq = await pool.enqueue_job(
        "sync_bling_costs_run", str(job.id), str(user.id)
    )
    if arq is not None:
        job.arq_job_id = arq.job_id
    await session.commit()
    return JobCreatedOut(job_id=job.id)


# =============================================================================
# Auto-match (9d) — link pricing_accounts to integrations by platform
# =============================================================================

PRICING_TO_INTEG_PLATFORM = {
    "mercadolivre": IntegrationPlatform.ML,
    "shopee": IntegrationPlatform.SHOPEE,
    "amazon": IntegrationPlatform.AMAZON,
    "tiktok": IntegrationPlatform.TIKTOK,
    "temu": IntegrationPlatform.TEMU,
    "magalu": IntegrationPlatform.MAGALU,
}

# Sufixos de modalidade no nome da conta de preço ("kfa classico", "kfa premium").
_SUFIXOS_MODALIDADE = {"classico", "clássico", "premium"}


def _escolher_integracao(
    nome_conta: str | None,
    integracao_da_loja: UUID | None,
    candidatas: list[Integration],
) -> Integration | None:
    """Qual integração (da mesma plataforma) é a desta conta de preço.

    25/09/2026: o casamento era por PEDAÇO do nome ("kfa" in "kfa2"), e a
    primeira que casasse ganhava — "kfa classico" e "kfa premium" foram ligadas
    à integração da kfa2. Resultado: a conta sumia da Tabela de Preços da
    equipe da KFA (a cerca por equipe olha a integração) e os preços lidos
    eram os dos anúncios da kfa2. Agora, em ordem:
      1. a integração da loja (store_info) à qual a conta já está ligada;
      2. nome exato, sem o sufixo classico/premium;
      3. TODAS as palavras do nome da integração aparecem no nome da conta
         ("kfa" em "kfa amazon"), e só se apontar para UMA integração.
    Sem prova pelo nome não liga — nem quando a plataforma tem uma integração
    só ("poofy" ia parar em "KFA Amazon"; "lucas mei" em "victor mei" pela
    palavra "mei"). Fica sem ligar, para alguém escolher na mão.
    """
    if integracao_da_loja is not None:
        for c in candidatas:
            if c.id == integracao_da_loja:
                return c
    palavras = [w for w in (nome_conta or "").lower().split() if w]
    base = " ".join(w for w in palavras if w not in _SUFIXOS_MODALIDADE)
    exatas = [c for c in candidatas if (c.name or "").strip().lower() == base]
    if len(exatas) == 1:
        return exatas[0]
    if exatas:
        return None
    palavras_base = set(base.split())
    por_nome = [
        c
        for c in candidatas
        if (c.name or "").split() and set((c.name or "").lower().split()) <= palavras_base
    ]
    if len(por_nome) == 1:
        return por_nome[0]
    return None


@router.post("/accounts/auto-match", response_model=AutoMatchResult)
async def auto_match_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> AutoMatchResult:
    """Tries to fill `pricing_accounts.integration_id` when null by matching
    pricing_account.platform → integration.platform and then the rules of
    `_escolher_integracao` (loja ligada → nome exato → palavra inteira única).
    """
    accounts = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    user_scope(PricingAccount, user),
                    PricingAccount.integration_id.is_(None),
                    # Coluna de catálogo fica SEM integração de propósito (usa
                    # a da base) — o CHECK do banco recusaria.
                    PricingAccount.canal == CANAL_KIT,
                )
            )
        )
    ).scalars().all()
    # Arquivada fica de fora: ligar nela esconde a conta da Tabela de Preços
    # (_exclude_archived_accounts).
    integrations = (
        await session.execute(
            select(Integration).where(
                and_(user_scope(Integration, user), Integration.archived_at.is_(None))
            )
        )
    ).scalars().all()

    by_platform: dict[IntegrationPlatform, list[Integration]] = {}
    for integ in integrations:
        by_platform.setdefault(integ.platform, []).append(integ)

    loja_ids = {a.store_info_id for a in accounts if a.store_info_id is not None}
    integracao_da_loja: dict[UUID, UUID | None] = {}
    if loja_ids:
        integracao_da_loja = dict(
            (
                await session.execute(
                    select(StoreInfo.id, StoreInfo.integration_id).where(
                        StoreInfo.id.in_(loja_ids)
                    )
                )
            ).all()
        )

    matched: list = []
    skipped = 0
    for acc in accounts:
        plat_val = acc.platform.value if hasattr(acc.platform, "value") else acc.platform
        target_plat = PRICING_TO_INTEG_PLATFORM.get(plat_val)
        if target_plat is None:
            skipped += 1
            continue
        candidates = by_platform.get(target_plat) or []
        if not candidates:
            skipped += 1
            continue
        chosen = _escolher_integracao(
            acc.name,
            integracao_da_loja.get(acc.store_info_id) if acc.store_info_id else None,
            candidates,
        )
        if chosen is None:
            skipped += 1
            continue
        acc.integration_id = chosen.id
        matched.append(acc.id)

    await session.commit()
    return AutoMatchResult(matched=len(matched), skipped=skipped, accounts=matched)


@router.post("/accounts/{account_id}/department", response_model=PricingAccountOut)
async def set_account_department(
    account_id: UUID,
    body: AccountSetDepartmentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> PricingAccountOut:
    sid = await _resolve_root_segment_id(session, body.department)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})
    row = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "account_not_found"})
    _recusar_edicao_da_filha(row)
    row.segment_id = sid
    await _sincronizar_filha(session, row)
    await session.commit()
    await session.refresh(row)
    roots_by_id, _, _ = await _segment_index(session)
    names_by_id = await _segment_names_by_id(session)
    return await _uma_conta_out(session, row, roots_by_id, names_by_id)


@router.post("/accounts/{account_id}/catalogo", response_model=ContaCatalogoOut)
async def definir_catalogo_da_conta(
    account_id: UUID,
    body: ContaCatalogoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_contas", "edit"))
    ],
) -> ContaCatalogoOut:
    """Liga/desliga o Catálogo ML de uma conta ML de kit (decisão D4: por
    conta, na aba Contas; padrão desligado).

    Ligar cria a coluna de catálogo ("filha": canal='catalogo',
    conta_base_id = esta conta, sem integração) com nome, tipo, aba, slots e
    ordem da base; os números vêm da base na hora do cálculo. Desligar apaga a
    filha e os preços fixados nela. Idempotente nos dois sentidos."""
    base = (
        await session.execute(
            select(PricingAccount)
            .where(
                and_(
                    PricingAccount.id == account_id,
                    user_scope(PricingAccount, user),
                )
            )
            # Dois cliques seguidos não criam duas filhas.
            .with_for_update()
        )
    ).scalar_one_or_none()
    if base is None:
        raise HTTPException(404, detail={"code": "account_not_found"})
    if base.canal != CANAL_KIT or base.platform != PricingPlatform.ML:
        raise HTTPException(
            400,
            detail={
                "code": "conta_nao_ml_kit",
                "message": "O Catálogo ML só liga em conta do Mercado Livre (coluna de kit)",
            },
        )

    filha = await _filha_de(session, base.id)
    if body.ativo and filha is None:
        if ml_listing_type_for_account(base.listing_type) is None:
            raise HTTPException(
                409,
                detail={
                    "code": "tipo_obrigatorio",
                    "message": (
                        "Preencha o tipo (clássico/premium) desta conta antes de "
                        "ativar o catálogo"
                    ),
                },
            )
        filha = PricingAccount(
            canal=CANAL_CATALOGO,
            conta_base_id=base.id,
            integration_id=None,
            **{campo: getattr(base, campo) for campo in _CAMPOS_ESPELHADOS_NA_FILHA},
        )
        session.add(filha)
        await session.commit()
        await session.refresh(filha)
        logger.info(
            "pricing.catalogo_ligado", conta_base_id=str(base.id), user_id=str(user.id)
        )
    elif not body.ativo and filha is not None:
        await session.delete(filha)
        await session.commit()
        filha = None
        logger.info(
            "pricing.catalogo_desligado", conta_base_id=str(base.id), user_id=str(user.id)
        )
    else:
        await session.commit()

    roots_by_id, _, _ = await _segment_index(session)
    names_by_id = await _segment_names_by_id(session)
    return ContaCatalogoOut(
        ativo=filha is not None,
        conta=_account_out(
            base, roots_by_id, names_by_id, conta_catalogo_id=filha.id if filha else None
        ),
        conta_catalogo=(
            _account_out(filha, roots_by_id, names_by_id, base=base) if filha else None
        ),
    )


# =============================================================================
# Store info (9d) — CRUD com password cifrado
# =============================================================================

async def _compute_store_info_badges(
    session: AsyncSession, user: User, row: StoreInfo
) -> tuple[list[str], bool, bool]:
    """Recompute the (departments, has_pricing, has_integration) triple for a
    single StoreInfo row. Shares type resolution with Lojas/Faturamento so
    PATCH/POST responses don't return stale "Tab.Preço = Não" / "Integração
    = Não" badges when the user edits an unrelated field."""
    classification = (await resolve_store_departments(session, user, [row]))[row.id]
    sname = (row.account_name or "").strip().lower()
    splat = (row.platform or "").strip().lower()

    integ_rows = (
        await session.execute(select(Integration.name, Integration.platform))
    ).all()
    has_integ = False
    if sname:
        for iname, iplat in integ_rows:
            iplat_val = iplat.value if hasattr(iplat, "value") else str(iplat)
            if (iname or "").strip().lower() == sname and iplat_val.lower() == splat:
                has_integ = True
                break

    return classification.departments, classification.has_pricing, has_integ


def _store_info_out(
    row: StoreInfo,
    departments: list[str] | None = None,
    has_pricing: bool | None = None,
    has_integration: bool | None = None,
) -> StoreInfoOut:
    out = StoreInfoOut.model_validate(row)
    out.has_password = bool(row.password_enc)
    out.departments = sorted(set(departments or []) | _manual_store_departments(row))
    # Manual tags classify a store without creating a price table. Only
    # departments derived from pricing accounts contribute to this badge.
    out.has_pricing = bool(has_pricing) if has_pricing is not None else bool(departments)
    # `has_integration` is the strict (lower(name), platform) match against the
    # `integrations` table. Falls back to the legacy FK if the caller didn't
    # compute the strict variant.
    out.has_integration = (
        bool(has_integration) if has_integration is not None else (row.integration_id is not None)
    )
    return out


@router.get("/store-info", response_model=list[StoreInfoOut])
async def list_store_info(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "view"))
    ],
    archived: bool = Query(False),
) -> list[StoreInfoOut]:
    # Default view hides archived stores; `?archived=true` shows only the
    # archived ones (the "Arquivadas" tab). archived_at NULL = ativa.
    archived_filter = (
        StoreInfo.archived_at.is_not(None)
        if archived
        else StoreInfo.archived_at.is_(None)
    )
    rows = (
        await session.execute(
            select(StoreInfo)
            .where(user_scope(StoreInfo, user), archived_filter)
            .order_by(StoreInfo.sort_order, StoreInfo.platform)
        )
    ).scalars().all()
    classifications = await resolve_store_departments(session, user, rows)

    # has_integration: strict (lower(name), platform) match against integrations.
    # Both store_info.platform and integrations.platform use short codes (ml,
    # shopee, …) so no alias translation is needed here.
    integ_rows = (
        await session.execute(select(Integration.name, Integration.platform))
    ).all()
    integ_keys: set[tuple[str, str]] = set()
    for iname, iplat in integ_rows:
        iplat_val = iplat.value if hasattr(iplat, "value") else str(iplat)
        if iname:
            integ_keys.add((iname.strip().lower(), iplat_val.lower()))

    out_list: list[StoreInfoOut] = []
    for r in rows:
        sname = (r.account_name or "").strip().lower()
        splat = (r.platform or "").strip().lower()
        classification = classifications[r.id]
        has_integ = bool(sname and (sname, splat) in integ_keys)
        out_list.append(
            _store_info_out(
                r, classification.departments,
                has_pricing=classification.has_pricing, has_integration=has_integ,
            )
        )
    return out_list


@router.post(
    "/store-info",
    response_model=StoreInfoOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_store_info(
    body: StoreInfoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> StoreInfoOut:
    data = body.model_dump(exclude={"password"})
    row = StoreInfo(user_id=user.id, **data)
    if body.password:
        row.password_enc = encrypt(body.password)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _store_info_out(row)


@router.patch("/store-info/{store_info_id}", response_model=StoreInfoOut)
async def patch_store_info(
    store_info_id: UUID,
    body: StoreInfoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> StoreInfoOut:
    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        pwd = data.pop("password")
        row.password_enc = encrypt(pwd) if pwd else None
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    # Recompute the Tab.Preço / Integração badges so the response doesn't
    # regress to "Não" after editing an unrelated field (UpseSeller, Duoker,
    # bling_store_id, etc).
    depts, has_pricing, has_integ = await _compute_store_info_badges(
        session, user, row
    )
    return _store_info_out(
        row,
        departments=depts,
        has_pricing=has_pricing,
        has_integration=has_integ,
    )


async def _resolve_linked_integration(
    session: AsyncSession, user: User, row: StoreInfo
) -> Integration | None:
    """Encontra a integração vinculada à loja: prefere o FK store_info.
    integration_id; se ausente, casa por (lower(name)==lower(account_name),
    platform) — mesmo pareamento estrito do badge "Integração"."""
    if row.integration_id is not None:
        return (
            await session.execute(
                select(Integration).where(
                    and_(
                        Integration.id == row.integration_id,
                        user_scope(Integration, user),
                    )
                )
            )
        ).scalar_one_or_none()
    sname = (row.account_name or "").strip().lower()
    splat = (row.platform or "").strip().lower()
    if not sname:
        return None
    integs = (
        await session.execute(
            select(Integration).where(user_scope(Integration, user))
        )
    ).scalars().all()
    for integ in integs:
        iplat = integ.platform.value if hasattr(integ.platform, "value") else str(integ.platform)
        if (integ.name or "").strip().lower() == sname and iplat.lower() == splat:
            return integ
    return None


@router.post("/store-info/{store_info_id}/archive", response_model=StoreInfoOut)
async def archive_store_info(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> StoreInfoOut:
    """Arquiva uma loja suspensa: some de Lojas, da Tabela de Preço e de
    Produtos, e o sync para de empurrar estoque/preço. Propaga o
    `archived_at` pra integração vinculada. Reversível pelo /unarchive."""
    from datetime import UTC, datetime

    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(StoreInfo.id == store_info_id, user_scope(StoreInfo, user))
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    now = datetime.now(UTC)
    row.archived_at = now
    integ = await _resolve_linked_integration(session, user, row)
    if integ is not None:
        integ.archived_at = now
        # Backfill do FK pra desarquivar depois sem depender do name-match.
        if row.integration_id is None:
            row.integration_id = integ.id
    await session.commit()
    await session.refresh(row)
    logger.info(
        "store_info.archived",
        user_id=str(user.id),
        store_info_id=str(store_info_id),
        integration_id=str(integ.id) if integ else None,
    )
    return _store_info_out(row)


@router.post("/store-info/{store_info_id}/unarchive", response_model=StoreInfoOut)
async def unarchive_store_info(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> StoreInfoOut:
    """Reativa uma loja arquivada (botão "Ativar"): volta pra Lojas, Tabela de
    Preço e Produtos, e o sync volta a mirá-la. Limpa o `archived_at` da loja
    e da integração vinculada."""
    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(StoreInfo.id == store_info_id, user_scope(StoreInfo, user))
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    row.archived_at = None
    integ = await _resolve_linked_integration(session, user, row)
    if integ is not None:
        integ.archived_at = None
    await session.commit()
    await session.refresh(row)
    depts, has_pricing, has_integ = await _compute_store_info_badges(
        session, user, row
    )
    logger.info(
        "store_info.unarchived",
        user_id=str(user.id),
        store_info_id=str(store_info_id),
        integration_id=str(integ.id) if integ else None,
    )
    return _store_info_out(
        row, departments=depts, has_pricing=has_pricing, has_integration=has_integ
    )


@router.get("/store-info/{store_info_id}/password")
async def reveal_store_info_password(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> dict[str, str]:
    row = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    if not row.password_enc:
        raise HTTPException(404, detail={"code": "no_password"})
    logger.info(
        "store_info.password_revealed",
        user_id=str(user.id),
        store_info_id=str(store_info_id),
    )
    return {"password": decrypt(row.password_enc)}


@router.post(
    "/store-info/{store_info_id}/department",
    response_model=PricingAccountOut | StoreInfoOut,
)
async def set_store_info_department(
    store_info_id: UUID,
    body: AccountSetDepartmentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> PricingAccountOut | StoreInfoOut:
    """Bind a department to a store_info. Registration-only platforms retain
    the type on the store, independently of pricing. Existing pricing flow:
      1. Map StoreInfo.platform (short code "ml", "shein", …) into a
         PricingPlatform value via `_STORE_TO_PRICING_PLATFORM`. Direct
         `PricingPlatform(info.platform)` would crash on "ml"/"shein" because
         the enum values are "mercadolivre"/"shopee".
      2. If an account is already linked for (store_info, root_segment), reuse it.
      3. Otherwise try to LINK an existing-but-unlinked account by name
         match (exact or name-prefix), instead of always creating a duplicate.
      4. Only as a last resort, create a fresh account with sort_order placed
         AFTER existing accounts for the same (platform, segment).

    'catalogo' é recusado (06/10/2026): o Catálogo ML deixou de ser um tipo de
    loja e virou coluna de catálogo das contas ML de kit (aba Contas). Tirar
    o tipo antigo continua pelo DELETE.
    """
    if (body.department or "").strip().lower() == "catalogo":
        raise HTTPException(
            400,
            detail={
                "code": "departamento_invalido",
                "message": (
                    "Catálogo não é mais tipo de loja: ligue o Catálogo ML na conta de "
                    "kit, em Tabela de preços › Contas"
                ),
            },
        )
    sid = await _resolve_root_segment_id(session, body.department)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})

    info = (
        await session.execute(
            select(StoreInfo).where(
                and_(
                    StoreInfo.id == store_info_id,
                    user_scope(StoreInfo, user),
                )
            ).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if info is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})

    raw_platform = (info.platform or "").strip().lower()
    if raw_platform in _MANUAL_DEPARTMENT_PLATFORMS:
        info.manual_departments = sorted(
            set(info.manual_departments or []) | {body.department}
        )
        await session.commit()
        await session.refresh(info)
        depts, has_pricing, has_integ = await _compute_store_info_badges(session, user, info)
        return _store_info_out(
            info, departments=depts, has_pricing=has_pricing, has_integration=has_integ
        )
    pricing_value = _STORE_TO_PRICING_PLATFORM.get(raw_platform)
    if not pricing_value:
        raise HTTPException(400, detail={"code": "store_info_platform_unsupported"})
    try:
        platform = PricingPlatform(pricing_value)
    except ValueError as e:
        raise HTTPException(
            400, detail={"code": "store_info_platform_unsupported"},
        ) from e

    roots_by_id, _, _ = await _segment_index(session)
    names_by_id = await _segment_names_by_id(session)

    existing = (
        await session.execute(
            select(PricingAccount).where(
                and_(
                    user_scope(PricingAccount, user),
                    PricingAccount.store_info_id == store_info_id,
                    PricingAccount.segment_id == sid,
                    PricingAccount.canal == CANAL_KIT,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await _uma_conta_out(session, existing, roots_by_id, names_by_id)

    # SSH setDepartment: try to LINK an existing unlinked account whose name
    # matches the store_info account_name (exact or "<name> <suffix>" form),
    # instead of creating a duplicate.
    account_name = (info.account_name or "").strip()
    if account_name:
        name_lower = account_name.lower()
        unlinked = (
            await session.execute(
                select(PricingAccount).where(
                    and_(
                        user_scope(PricingAccount, user),
                        PricingAccount.platform == platform,
                        PricingAccount.segment_id == sid,
                        PricingAccount.store_info_id.is_(None),
                        # A coluna de catálogo tem o nome da base e nunca é
                        # ligada a loja (ela segue a base).
                        PricingAccount.canal == CANAL_KIT,
                    )
                )
            )
        ).scalars().all()
        matches = [
            a for a in unlinked
            if (a.name or "").strip().lower() == name_lower
            or (a.name or "").strip().lower().startswith(name_lower + " ")
        ]
        if matches:
            for m in matches:
                m.store_info_id = store_info_id
            await session.commit()
            await session.refresh(matches[0])
            return await _uma_conta_out(session, matches[0], roots_by_id, names_by_id)

    dept_slug = roots_by_id.get(sid, body.department)
    name = account_name or f"{raw_platform} — {dept_slug}"

    max_sort = (
        await session.execute(
            select(func.coalesce(func.max(PricingAccount.sort_order), 0)).where(
                and_(
                    user_scope(PricingAccount, user),
                    PricingAccount.platform == platform,
                    PricingAccount.segment_id == sid,
                )
            )
        )
    ).scalar() or 0

    row = PricingAccount(
        user_id=user.id,
        name=name,
        platform=platform,
        segment_id=sid,
        store_info_id=store_info_id,
        sort_order=int(max_sort) + 1,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _account_out(row, roots_by_id, names_by_id)


@router.delete(
    "/store-info/{store_info_id}/department/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_store_info_department(
    store_info_id: UUID,
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "edit"))
    ],
) -> None:
    """Removes the pricing_accounts that wire `store_info_id` to a root segment
    matching `slug`. Mirrors the FK-first badge match in `list_store_info`:
    deletes rows whose FK `store_info_id` points at THIS store_info, **plus**
    FK-less rows whose `(platform, name)` matches the store_info entry — the
    SSH-style implicit name binding. The `store_info_id IS NULL` guard on the
    name branch is critical: without it, unbinding e.g. Shein "kia" would also
    delete the Shopee "kia" account (same collapsed platform + name) even
    though it is FK-wired to a different store_info row.
    """
    sid = await _resolve_root_segment_id(session, slug)
    if sid is None:
        raise HTTPException(400, detail={"code": "invalid_department"})
    info = (
        await session.execute(
            select(StoreInfo).where(
                and_(StoreInfo.id == store_info_id, user_scope(StoreInfo, user))
            ).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if info is None:
        raise HTTPException(404, detail={"code": "store_info_not_found"})

    # Clear stored manual tags as well, including a tag selected before a
    # platform change, so it cannot reappear if the platform changes back.
    if slug in (info.manual_departments or []):
        info.manual_departments = sorted(set(info.manual_departments or []) - {slug}) or None

    sname = (info.account_name or "").strip().lower()
    splat_alias = _STORE_TO_PRICING_PLATFORM.get(
        (info.platform or "").strip().lower(), (info.platform or "").strip().lower()
    )
    pricing_plat: PricingPlatform | None = None
    try:
        pricing_plat = PricingPlatform(splat_alias)
    except ValueError:
        pricing_plat = None

    # Build the delete predicate: FK match OR (FK-less + same platform + same
    # name). The `store_info_id IS NULL` guard keeps the name branch from
    # nuking an account that is FK-wired to a *different* store_info row.
    cond = PricingAccount.store_info_id == store_info_id
    if sname and pricing_plat is not None:
        cond = or_(
            cond,
            and_(
                PricingAccount.store_info_id.is_(None),
                PricingAccount.platform == pricing_plat,
                func.lower(PricingAccount.name) == sname,
            ),
        )
    await session.execute(
        delete(PricingAccount).where(
            and_(
                user_scope(PricingAccount, user),
                PricingAccount.segment_id == sid,
                # Coluna de catálogo sai junto com a base (ON DELETE CASCADE),
                # nunca pelo casamento por nome.
                PricingAccount.canal == CANAL_KIT,
                cond,
            )
        )
    )
    await session.commit()


@router.delete("/store-info/{store_info_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store_info(
    store_info_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("lojas_info", "delete"))
    ],
) -> None:
    res = await session.execute(
        delete(StoreInfo).where(
            and_(
                StoreInfo.id == store_info_id,
                user_scope(StoreInfo, user),
            )
        )
    )
    if res.rowcount == 0:
        raise HTTPException(404, detail={"code": "store_info_not_found"})
    await session.commit()
    return None
