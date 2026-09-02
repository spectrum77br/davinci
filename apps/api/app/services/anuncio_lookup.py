"""Busca de UM anúncio pelo ID — sem varrer a conta inteira.

Motivação (Eduardo, 2026-08-20): anúncio novo na Shopee com 52 variações. Pra
sincronizar o estoque era preciso rodar "Vincular Automático"/"Sincronizar
Todos" da conta toda — varre TODOS os anúncios, leva minutos. Aqui a gente pede
ao marketplace só aquele anúncio (Shopee: get_item_base_info + get_model_list;
ML: /items/{id}) e devolve as variações com os SKUs já casados com os produtos
do DaVinci, prontos pra vincular e sincronizar.

Regras que este módulo NÃO muda: o casamento continua sendo por SKU exato
(`_SkuIndex` do auto_link, mesma normalização), SKU duplicado em dois produtos
continua ambíguo (não vincula), e o push de estoque continua saindo pelo
`SyncOrchestrator` — mesmo caminho do botão de sync individual.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import user_scope
from app.models import (
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
    User,
)
from app.services.auto_link import (
    _ml_client_for,
    _norm_sku,
    _now,
    _shopee_client_for,
    _SkuIndex,
    _tiktok_client_for,
)
from app.services.marketplaces.ml import _iter_ml_variants

logger = structlog.get_logger()

# Quantas contas sondar em paralelo quando o operador não escolhe a conta.
LOOKUP_CONCURRENCY = 6

_ML_ID_RE = re.compile(r"^MLB\d{5,}$", re.IGNORECASE)
_NUM_ID_RE = re.compile(r"^\d{6,}$")

# ID só de dígitos é Shopee OU TikTok — o comprimento separa com folga:
# medido em prod (2026-08-24), TODOS os item_id Shopee têm 11 dígitos
# (9.388 links) e TODOS os product_id TikTok têm 19 (3.088 links). O corte
# em 15 aguenta os dois lados crescerem. Se um dia a heurística errar, o
# lookup ainda sonda as plataformas dos vínculos já gravados (ver
# `lookup_anuncio`), então a conta dona continua sendo achada.
_TIKTOK_MIN_DIGITS = 15


def parece_id_anuncio(raw: str | None) -> bool:
    """Heurística usada pelo front: o texto digitado parece um ID de anúncio?"""
    s = (raw or "").strip()
    return bool(_ML_ID_RE.match(s) or _NUM_ID_RE.match(s))


def _normaliza_id(raw: str) -> str:
    s = (raw or "").strip()
    return s.upper() if _ML_ID_RE.match(s) else s


def plataforma_do_id(external_id: str) -> IntegrationPlatform | None:
    """MLB123… → ML; só dígitos → Shopee (curto) ou TikTok (15+ dígitos).
    Outros formatos: None (aí só o caminho local, por vínculos já
    existentes, consegue resolver)."""
    s = _normaliza_id(external_id)
    if _ML_ID_RE.match(s):
        return IntegrationPlatform.ML
    if _NUM_ID_RE.match(s):
        if len(s) >= _TIKTOK_MIN_DIGITS:
            return IntegrationPlatform.TIKTOK
        return IntegrationPlatform.SHOPEE
    return None


async def _integracoes_candidatas(
    session: AsyncSession,
    user: User,
    *,
    platforms: set[IntegrationPlatform] | None,
    integration_id: UUID | None,
) -> list[Integration]:
    stmt = select(Integration).where(
        and_(user_scope(Integration, user), Integration.archived_at.is_(None))
    )
    if integration_id is not None:
        stmt = stmt.where(Integration.id == integration_id)
    elif platforms:
        stmt = stmt.where(Integration.platform.in_(tuple(platforms)))
    return list((await session.execute(stmt)).scalars().all())


async def _linhas_shopee(integ: Integration, session: AsyncSession, item_id: str) -> list[dict]:
    """As variações de um item Shopee. Item que não é da loja → lista vazia
    (a API responde erro e o cliente já loga)."""
    client = _shopee_client_for(integ, session)
    out: list[dict] = []
    async for norm in client._fetch_base_info([int(item_id)], "NORMAL"):
        out.append(norm)
    return out


async def _linhas_ml(integ: Integration, session: AsyncSession, item_id: str) -> list[dict]:
    """As variações de um item ML. `/items/{id}` é público, então conferimos o
    dono: se o seller_id não for o da conta, o anúncio não é dela."""
    client = _ml_client_for(integ, session)
    body = await client.get_item(item_id)
    if not body:
        return []
    seller = str(body.get("seller_id") or "")
    creds_user = str((client.creds or {}).get("user_id") or "")
    if seller and creds_user and seller != creds_user:
        return []
    return list(_iter_ml_variants(body))


async def _linhas_tiktok(integ: Integration, session: AsyncSession, item_id: str) -> list[dict]:
    """As variações (skus) de um produto TikTok. O GET é escopado na loja
    (shop_cipher), então produto de outra conta volta None → lista vazia.
    Estoque = inventory[0].quantity, igual à normalização do
    `search_products` que alimenta o Vincular Automático."""
    client = _tiktok_client_for(integ, session)
    data = await client.get_product(item_id)
    if not data:
        return []
    titulo = (data.get("title") or "").strip()
    linhas: list[dict] = []
    for sku in data.get("skus") or []:
        inv = sku.get("inventory") or []
        stock = inv[0].get("quantity", 0) if inv else 0
        # "Roxo Escuro / 24" — vira o rótulo da coluna Variação via o
        # "Título - Variação" que o _rotulo_variacao espera.
        attrs = " / ".join(
            str(a.get("value_name") or "").strip()
            for a in (sku.get("sales_attributes") or [])
            if str(a.get("value_name") or "").strip()
        )
        linhas.append(
            {
                "external_id": str(data.get("id") or item_id),
                "variation_id": str(sku.get("id") or "") or None,
                "sku": (sku.get("seller_sku") or "").strip(),
                "title": f"{titulo} - {attrs}" if attrs else titulo,
                "listing_type": None,
                "stock": stock,
            }
        )
    return linhas


async def _busca_no_marketplace(
    session: AsyncSession,
    integ: Integration,
    external_id: str,
) -> list[dict]:
    try:
        linhas: list[dict] = []
        if integ.platform == IntegrationPlatform.SHOPEE:
            linhas = await _linhas_shopee(integ, session, external_id)
        elif integ.platform == IntegrationPlatform.ML:
            linhas = await _linhas_ml(integ, session, external_id)
        elif integ.platform == IntegrationPlatform.TIKTOK:
            linhas = await _linhas_tiktok(integ, session, external_id)
        # Cinto de segurança: só aceita linha do anúncio pedido. Se um dia a
        # API devolver vizinhos (ou o id vier de outro jeito), nada de vincular
        # SKU de outro anúncio.
        alvo = external_id.strip().lower()
        return [
            ln
            for ln in linhas
            if not (ln.get("external_id") or "")
            or str(ln.get("external_id")).strip().lower() == alvo
        ]
    except Exception as e:  # noqa: BLE001
        logger.info(
            "anuncio_lookup_conta_falhou",
            integration_id=str(integ.id),
            platform=integ.platform.value,
            err=str(e)[:200],
        )
    return []


async def _procura_conta(
    session: AsyncSession,
    integracoes: list[Integration],
    external_id: str,
) -> tuple[Integration | None, list[dict]]:
    """Sonda as contas candidatas em paralelo e devolve a primeira que
    reconhece o anúncio. Uma conta que não tem o anúncio custa 1 chamada."""
    if not integracoes:
        return None, []
    if len(integracoes) == 1:
        linhas = await _busca_no_marketplace(session, integracoes[0], external_id)
        return (integracoes[0], linhas) if linhas else (None, [])

    sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)

    async def _one(integ: Integration) -> tuple[Integration, list[dict]]:
        async with sem:
            return integ, await _busca_no_marketplace(session, integ, external_id)

    for coro in asyncio.as_completed([_one(i) for i in integracoes]):
        integ, linhas = await coro
        if linhas:
            return integ, linhas
    return None, []


def _rotulo_variacao(linha: dict) -> str | None:
    """Nome da variação = o pedaço do título depois do " - " que a
    normalização acrescenta (Shopee/ML montam "Título - Variação")."""
    titulo = (linha.get("title") or "").strip()
    if " - " in titulo:
        return titulo.rsplit(" - ", 1)[1] or None
    return None


async def lookup_anuncio(
    session: AsyncSession,
    user: User,
    *,
    external_id: str,
    integration_id: UUID | None = None,
) -> dict[str, Any]:
    """Devolve o anúncio + suas variações com o casamento por SKU resolvido.

    `origem`: "marketplace" quando as linhas vieram da API do canal (mostra as
    variações NOVAS, ainda sem vínculo); "local" quando só deu pra montar a
    partir dos vínculos já gravados (plataformas sem busca por ID, ou canal
    fora do ar).
    """
    ext = _normaliza_id(external_id)
    if not ext:
        return {"encontrado": False, "motivo": "id_vazio", "external_id": ext}

    # 1) O que já existe localmente pra esse ID. CUIDADO (caso 58207423113,
    #    2026-08-21): podem existir vínculos-fantasma do MESMO anúncio gravados
    #    em OUTRAS contas (lixo antigo). Um anúncio pertence a UMA loja, então
    #    a conta dona é resolvida PRIMEIRO e só os vínculos dela contam — antes
    #    disso, o fantasma desviava a busca pra conta errada ("marketplace não
    #    respondeu"), roubava o casamento por variação e entrava no sync com
    #    erro "item_not_found".
    links_locais = list(
        (
            await session.execute(
                select(ProductLink).where(
                    and_(user_scope(ProductLink, user), ProductLink.external_id == ext)
                )
            )
        )
        .scalars()
        .all()
    )

    platform = plataforma_do_id(ext)
    if links_locais and platform is None:
        platform = links_locais[0].platform

    # Sonda a plataforma que o formato do ID sugere E as dos vínculos já
    # gravados — se a heurística de comprimento errar (ID numérico é Shopee
    # OU TikTok), a conta dona ainda entra na roda. Conta de plataforma
    # errada não reconhece o item, então o custo do extra é 1 chamada.
    plataformas = {lk.platform for lk in links_locais if lk.platform is not None}
    if platform is not None:
        plataformas.add(platform)

    ids_com_link = {lk.integration_id for lk in links_locais}

    async def _sonda_todas(
        excluir: UUID | None,
    ) -> tuple[Integration | None, list[dict]]:
        todas = await _integracoes_candidatas(
            session, user, platforms=plataformas or None, integration_id=None
        )
        candidatas = [i for i in todas if i.id != excluir]
        com_link = [i for i in candidatas if i.id in ids_com_link]
        sem_link = [i for i in candidatas if i.id not in ids_com_link]
        # Atalho: primeiro as contas que já têm vínculo com esse ID (quase
        # sempre só uma). Conta errada não reconhece o item, então um vínculo-
        # fantasma não engana a busca — quem responde é a dona de verdade.
        integ_, linhas_ = await _procura_conta(session, com_link, ext)
        if integ_ is None:
            integ_, linhas_ = await _procura_conta(session, sem_link, ext)
        return integ_, linhas_

    if integration_id is not None:
        integracoes = await _integracoes_candidatas(
            session, user, platforms=plataformas or None, integration_id=integration_id
        )
        integ, linhas = await _procura_conta(session, integracoes, ext)
        if integ is None:
            # A conta pedida não reconheceu o anúncio (tela desviada por um
            # fantasma?): sonda as outras antes de cair no fallback local.
            integ, linhas = await _sonda_todas(excluir=integration_id)
    else:
        integ, linhas = await _sonda_todas(excluir=None)

    origem = "marketplace"
    if not linhas and links_locais:
        # Canal não respondeu (ou plataforma sem busca por ID): monta a partir
        # dos vínculos gravados pra pelo menos permitir o sync — de UMA conta
        # só (a pedida; senão a que tem mais vínculos deste anúncio).
        origem = "local"
        alvo_integ = integration_id
        if alvo_integ is None:
            contagem = Counter(lk.integration_id for lk in links_locais)
            alvo_integ = contagem.most_common(1)[0][0]
        escolhidos = [lk for lk in links_locais if lk.integration_id == alvo_integ]
        if escolhidos:
            integ = await session.get(Integration, alvo_integ)
            linhas = [
                {
                    "external_id": lk.external_id,
                    "variation_id": lk.variation_id,
                    "sku": lk.external_sku,
                    "title": lk.listing_title,
                    "listing_type": lk.listing_type,
                    "stock": lk.stock,
                }
                for lk in escolhidos
            ]

    if not linhas or integ is None:
        return {
            "encontrado": False,
            "motivo": "nao_encontrado",
            "external_id": ext,
            "plataforma": platform.value if platform else None,
        }

    # Só os vínculos DA conta dona do anúncio entram no casamento por variação.
    por_variacao = {
        (lk.variation_id or ""): lk
        for lk in links_locais
        if lk.integration_id == integ.id
    }

    # Só os produtos cujo SKU pode casar com alguma variação deste anúncio —
    # carregar o catálogo inteiro (como o Vincular Automático faz) deixaria uma
    # busca interativa lenta. A expressão abaixo é o `_norm_sku` em SQL (trim +
    # colapsa espaços + minúsculas), então o casamento continua idêntico; como
    # ela traz TODOS os produtos que compartilham o mesmo SKU, a detecção de
    # SKU ambíguo do `_SkuIndex` segue valendo.
    alvos = {_norm_sku(ln.get("sku")) for ln in linhas}
    alvos.discard("")
    produtos: list[Product] = []
    if alvos:
        sku_norm = func.lower(func.regexp_replace(func.btrim(Product.sku), r"\s+", " ", "g"))
        produtos = list(
            (
                await session.execute(
                    select(Product).where(
                        and_(user_scope(Product, user), sku_norm.in_(alvos))
                    )
                )
            )
            .scalars()
            .all()
        )
    sku_index = _SkuIndex(produtos)

    titulo = next((ln.get("title") for ln in linhas if ln.get("title")), None)
    if titulo and " - " in titulo and len(linhas) > 1:
        titulo = titulo.rsplit(" - ", 1)[0]

    variacoes: list[dict[str, Any]] = []
    prontos = ja_vinculados = sem_sku = ambiguos = sem_produto = trocados = 0
    for linha in linhas:
        sku = (linha.get("sku") or "").strip()
        var_id = (linha.get("variation_id") or "").strip() or None
        link = por_variacao.get(var_id or "")
        produto = motivo = None
        if sku:
            produto, motivo = sku_index.resolve(sku)
        estado: str
        if link is not None:
            # SKU trocado DENTRO do anúncio (Eduardo, 2026-08-21: i222.sa →
            # i222.sp): o link ainda aponta pro produto antigo, mas o SKU
            # atual do anúncio casa com OUTRO produto. Só quando as linhas
            # vieram frescas do marketplace — no fallback "local" o SKU sai
            # do próprio link, não dá pra saber se o anúncio mudou. SKU novo
            # sem produto/ambíguo fica "vinculado" (não há pra onde mover).
            if (
                origem == "marketplace"
                and produto is not None
                and produto.id != link.product_id
            ):
                estado = "trocado"
                trocados += 1
            else:
                estado = "vinculado"
                ja_vinculados += 1
        elif not sku:
            estado = "sem_sku"
            sem_sku += 1
        elif produto is not None:
            estado = "pronto"
            prontos += 1
        elif motivo == "ambiguo":
            estado = "sku_ambiguo"
            ambiguos += 1
        else:
            estado = "sem_produto"
            sem_produto += 1
        variacoes.append(
            {
                "variation_id": var_id,
                "variacao": _rotulo_variacao(linha),
                "sku": sku or None,
                "estado": estado,
                # ML separa Clássico/Premium por aqui — sem isso o vínculo cai
                # na coluna errada da tela de Produtos.
                "listing_type": linha.get("listing_type"),
                "product_id": str(produto.id) if produto is not None else (
                    str(link.product_id) if link is not None else None
                ),
                "produto_nome": produto.name if produto is not None else None,
                "estoque_bling": produto.stock if produto is not None else None,
                "estoque_anuncio": linha.get("stock"),
                "link_id": str(link.id) if link is not None else None,
                # Só no estado "trocado": o SKU que o vínculo ainda carrega.
                "sku_anterior": (
                    link.external_sku if estado == "trocado" and link is not None else None
                ),
            }
        )

    return {
        "encontrado": True,
        "external_id": ext,
        "plataforma": integ.platform.value,
        "integration_id": str(integ.id),
        "conta": integ.name,
        "titulo": titulo,
        "origem": origem,
        "resumo": {
            "total": len(variacoes),
            "prontos": prontos,
            "ja_vinculados": ja_vinculados,
            "sem_sku": sem_sku,
            "sku_ambiguo": ambiguos,
            "sem_produto": sem_produto,
            "trocados": trocados,
        },
        "variacoes": variacoes,
    }


async def vincular_anuncio(
    session: AsyncSession,
    user: User,
    *,
    external_id: str,
    integration_id: UUID,
) -> dict[str, Any]:
    """Cria os `product_links` que faltam, MOVE os que ficaram pra trás e
    APAGA os de variação extinta.

    Criar: só pro que tem SKU casado com UM produto — SKU vazio, ambíguo ou sem
    produto no DaVinci continua de fora (mesma regra do Vincular Automático).
    Mover (estado "trocado"): o operador editou o seller_sku DENTRO do anúncio
    (i222.sa → i222.sp) e o link ainda aponta pro produto antigo — re-aponta o
    link pro produto dono do SKU atual, igual ao `link_reconcile` (re-point, não
    apaga/recria), pra o estoque certo fluir no sync que vem em seguida.
    Apagar ("removidos"): vínculo cuja variação NÃO existe mais no anúncio
    (lista fresca do marketplace manda) — ver o comentário no corpo.
    Retorna o resumo + os ids dos links do anúncio (novos e antigos), que o
    caller usa pra sincronizar só eles.
    """
    dados = await lookup_anuncio(
        session, user, external_id=external_id, integration_id=integration_id
    )
    if not dados.get("encontrado"):
        return {**dados, "criados": 0, "movidos": 0, "movimentos": [], "link_ids": []}

    # A conta que RECONHECEU o anúncio manda (pode diferir da pedida quando um
    # vínculo-fantasma apontou a tela pra conta errada).
    integ_id = integration_id
    if dados.get("integration_id"):
        integ_id = UUID(str(dados["integration_id"]))
    integ = await session.get(Integration, integ_id)
    if integ is None:
        return {
            "encontrado": False,
            "motivo": "conta_nao_encontrada",
            "criados": 0,
            "movidos": 0,
            "movimentos": [],
            "link_ids": [],
        }

    ext = dados["external_id"]
    criados = 0
    novos: list[ProductLink] = []
    for v in dados["variacoes"]:
        if v["estado"] != "pronto" or not v.get("product_id"):
            continue
        novos.append(
            ProductLink(
                user_id=integ.user_id,
                product_id=UUID(v["product_id"]),
                integration_id=integ.id,
                store_id=integ.store_id,
                platform=integ.platform,
                external_id=ext,
                variation_id=v.get("variation_id"),
                external_sku=v.get("sku"),
                listing_title=dados.get("titulo"),
                listing_type=v.get("listing_type"),
                stock=v.get("estoque_anuncio"),
                last_sync_status=LinkSyncStatus.OK,
                last_sync_at=_now(),
            )
        )
    # Vínculos presos no produto antigo: o SKU do anúncio mudou e agora casa
    # com outro produto — move o MESMO registro (identidade conta+anúncio+
    # variação intacta, então não briga com o índice único).
    movidos = 0
    movimentos: list[dict[str, Any]] = []
    for v in dados["variacoes"]:
        if v["estado"] != "trocado" or not v.get("product_id") or not v.get("link_id"):
            continue
        link = await session.get(ProductLink, UUID(v["link_id"]))
        if link is None:
            continue
        alvo_id = UUID(v["product_id"])
        if link.product_id == alvo_id:
            continue
        # Defensivo (mesma regra do link_reconcile): se um estado quebrado
        # deixou um gêmeo já no produto destino, remove antes de mover.
        vid = link.variation_id or ""
        excedentes = (
            await session.execute(
                select(ProductLink).where(
                    and_(
                        ProductLink.product_id == alvo_id,
                        ProductLink.integration_id == link.integration_id,
                        ProductLink.platform == link.platform,
                        ProductLink.external_id == link.external_id,
                        func.coalesce(ProductLink.variation_id, "") == vid,
                        ProductLink.id != link.id,
                    )
                )
            )
        ).scalars().all()
        for dup in excedentes:
            await session.delete(dup)
        if excedentes:
            await session.flush()
        movimentos.append(
            {
                "variation_id": v.get("variation_id"),
                "variacao": v.get("variacao"),
                "de_sku": v.get("sku_anterior"),
                "para_sku": v.get("sku"),
            }
        )
        link.product_id = alvo_id
        link.external_sku = v.get("sku")
        movidos += 1

    # Vínculos-zumbi (Eduardo, 2026-09-02, anúncio 23994153133): variação
    # APAGADA do anúncio no marketplace deixava o product_link pra trás — cada
    # sync ainda empurrava estoque pro model extinto e a Shopee rejeitava
    # ("model ID not exist in sku"), enchendo o resultado de requires_review.
    # Quando as linhas vieram FRESCAS do canal (origem "marketplace"), a lista
    # de variações é a verdade: vínculo desta conta+anúncio cuja variação não
    # está nela morreu — apaga. No fallback "local" não dá pra saber, não mexe.
    removidos = 0
    remocoes: list[dict[str, Any]] = []
    if dados.get("origem") == "marketplace":
        vivos = {(v.get("variation_id") or "") for v in dados["variacoes"]}
        zumbis = [
            lk
            for lk in (
                await session.execute(
                    select(ProductLink).where(
                        and_(
                            user_scope(ProductLink, user),
                            ProductLink.external_id == ext,
                            ProductLink.integration_id == integ.id,
                        )
                    )
                )
            )
            .scalars()
            .all()
            if (lk.variation_id or "") not in vivos
        ]
        for lk in zumbis:
            remocoes.append({"variation_id": lk.variation_id, "sku": lk.external_sku})
            await session.delete(lk)
        removidos = len(zumbis)

    if novos:
        session.add_all(novos)
    if novos or movidos or removidos:
        try:
            await session.commit()
            criados = len(novos)
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.warning("anuncio_vincular_falhou", external_id=ext, err=str(e)[:200])
            criados = 0
            movidos = 0
            movimentos = []
            removidos = 0
            remocoes = []
    if movidos:
        logger.info(
            "anuncio_links_movidos",
            external_id=ext,
            movidos=movidos,
            movimentos=movimentos,
        )
    if removidos:
        logger.info(
            "anuncio_links_zumbis_removidos",
            external_id=ext,
            removidos=removidos,
            remocoes=remocoes,
        )

    # Só os vínculos DA conta dona: um fantasma de outra conta não pode entrar
    # no push de estoque (dava "item_not_found" na loja errada).
    link_ids = list(
        (
            await session.execute(
                select(ProductLink.id).where(
                    and_(
                        user_scope(ProductLink, user),
                        ProductLink.external_id == ext,
                        ProductLink.integration_id == integ.id,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        **dados,
        "criados": criados,
        "movidos": movidos,
        "movimentos": movimentos,
        "removidos": removidos,
        "remocoes": remocoes,
        "link_ids": [str(i) for i in link_ids],
    }
