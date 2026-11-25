"""Trocar a tag (= o fim do código) em massa no Bling — Produtos 2 › Configuração (M-46, Harry).

A tag do produto É o fim do código (`dg050.ra` → tag `.ra`; `z2819` sem tag), então trocar a tag
é trocar o código do produto no Bling: `PATCH /produtos/{id}` só com `{"codigo": novo}` (o PATCH
do Bling v3 só altera os campos informados; o PUT trocaria o cadastro inteiro e nunca é usado).
Logo depois de cada troca certa o DaVinci acompanha pelo MESMO caminho do webhook "Bling renomeou
o código do produto" (services/produto_renomeio.py), sem esperar o aviso; o ERP acompanha em até
~1 hora (catalog_follow).

Cada SKU é PULADO, com o motivo na tela, quando (riscos da resposta P-39):
- não encontrado no DaVinci; repetido na lista;
- kit / com composição / serviço: SKU com "+", formato E, estrutura no cache do Bling
  (`bling_kit_components`) — e, na hora de trocar, formato E, tipo S ou estrutura no Bling;
  produto com variações (formato V) também não;
- inativo / excluído / sem id no Bling;
- já tem a tag escolhida;
- peça que entra em algum kit: o código do kit (`dg019.ra+a001.ra`) não muda junto e ficaria
  com lotes misturados — kit pelo "+" do SKU (qualquer kit não excluído) ou pela estrutura do
  Bling (`bling_kit_components`, cópia semanal);
- mala: a Tabela de Preços casa mala pelo código INTEIRO (`pricing/sku_match.py`) — o SKU está
  numa linha de departamento mala da Tabela, ou o produto é do segmento Mala, ou a tag do
  Controle de Estoque o dá como mala (`sku_tags.classify_sku_tag`); idem catálogo (também casa
  pelo código inteiro);
- anúncio da Amazon ou da Carrefour vinculado: a escrita de estoque/preço desses canais depende
  de o SKU do vínculo ser o do produto (lista conferida canal a canal em `CANAIS_SKU_IGUAL`);
- o código novo já existe no DaVinci (qualquer situação) ou no Bling (qualquer situação, com
  conferência estrita: erro na consulta também pula) → "já existe: use Transferir no estoque";
- outro item da mesma lista já vai virar o mesmo código.

Execução (`executar`): uma troca de tag por vez (trava no banco); recalcula tudo no servidor (não
confia na prévia da tela); para cada "alterar", lê o produto no Bling (o código lá tem de ser o
antigo; formato/situação conferidos ao vivo), grava "enviando" e só então o PATCH. Resposta
duvidosa (5xx/timeout) → GET para ver se o código mudou; nunca repete às cegas. Se o GET ainda
mostra o código antigo, o item fica "sem_confirmacao" (o Bling pode aplicar o PATCH depois) com
aviso no sino, e o worker reconfere (`reconferir_sem_confirmacao`): código novo no Bling →
acompanha no DaVinci e marca ok; ainda o antigo depois de 30 min → falhou, com aviso. Nunca
outro PATCH. Recusa certa → "falhou" e segue para o próximo. Cada item fica registrado
(`produto_trocas_tag_itens`).
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    BlingKitComponent,
    Integration,
    IntegrationPlatform,
    PricingProduct,
    Product,
    ProductLink,
    ProdutoTrocaTag,
    ProdutoTrocaTagItem,
)
from app.models.enums import AlertSeverity, AlertType
from app.services.alerts import emit_alert
from app.services.devolution_stock_return import _falha_sem_envio
from app.services.pricing.audit import _load_segment_roots
from app.services.pricing.push import linhas_do_departamento
from app.services.pricing.sku_match import casa_pelo_codigo, variants_of
from app.services.produto_renomeio import aplicar_codigo_do_bling, resolver_produto
from app.services.sku_tag_envio import codigo_com_tag, tag_do_sku, tags_de_envio
from app.services.sku_tags import classify_sku_tag

logger = structlog.get_logger()

MAX_SKUS = 200

ALTERAR = "alterar"
PULAR = "pular"

PENDENTE = "pendente"
ENVIANDO = "enviando"
OK = "ok"
PULADO = "pulado"
FALHOU = "falhou"
SEM_CONFIRMACAO = "sem_confirmacao"

MOTIVO_EXISTE = "já existe: use Transferir no estoque"
MOTIVO_KIT = "kit não muda de tag: troque nas peças"
MOTIVO_MALA = "mala: a Tabela de Preços casa pelo código inteiro"
MOTIVO_CATALOGO = "catálogo: a Tabela de Preços casa pelo código inteiro"
MOTIVO_TABELA_LINHA = (
    "a Tabela de Preços passaria a ligar este código a outra linha (o código mais específico "
    "decide): troque à mão"
)
MOTIVO_AMAZON = "tem anúncio da Amazon vinculado"
MOTIVO_CARREFOUR = "tem anúncio da Carrefour vinculado"
MOTIVO_NAO_ENCONTRADO = "não encontrado"
MOTIVO_INATIVO = "inativo no Bling"
MOTIVO_EXCLUIDO = "excluído no Bling"
MOTIVO_SEM_BLING = "sem id no Bling"
MOTIVO_VARIACOES = "produto com variações não muda de tag"
MOTIVO_REPETIDO = "repetido na lista"
MOTIVO_JA_NO_BLING = "o Bling já está com o código novo: o DaVinci acompanha"
MOTIVO_NAO_CONFIRMOU = "o Bling não confirmou a troca; o DaVinci confere de novo em alguns minutos"
MOTIVO_CONFIRMADO_DEPOIS = "o Bling confirmou a troca depois: o DaVinci acompanhou"
_KITS_NO_MOTIVO = 5

# Canais em que a escrita de estoque/preço depende de o SKU do vínculo (ou o
# do produto, usado como SKU do anúncio) ser IGUAL ao código do produto. Depois
# da troca o produto muda de código e o anúncio não: o canal para de receber.
# Conferido em 25/11/2026 (revisão C3), canal por canal:
# - Amazon: o envio de preço acha o anúncio pelo SKU do PRODUTO
#   (pricing/push.py `_dispatch_price_update_link`: `product_sku or external_sku`).
# - Carrefour: a oferta é achada pelo SKU e o vínculo tem de ter exatamente o
#   SKU do produto (carrefour_offer_write `_source_valid`/`enqueue_offer_write`
#   → `carrefour_sku_mismatch`; carrefour_stock_source
#   `refresh_carrefour_stock_target` → None).
# NÃO dependem (endereçam pelo id do anúncio e seguem recebendo depois da
# troca): Mercado Livre (item/variação; a conferência do SELLER_SKU cai no
# envio de sempre com o aviso `sku_divergente`), Shopee (item/model), TikTok
# (product/sku id), Temu (goodsId/skuId), Magalu (o SKU da Magalu fica no
# `external_id` do vínculo e não muda) e o próprio Bling (id do produto).
# Shein não tem cliente (501) e Netshoes não tem integração.
CANAIS_SKU_IGUAL: dict[IntegrationPlatform, str] = {
    IntegrationPlatform.AMAZON: MOTIVO_AMAZON,
    IntegrationPlatform.CARREFOUR: MOTIVO_CARREFOUR,
}


class TrocaInvalidaError(Exception):
    """O pedido não pode ser feito: nada foi gravado nem trocado."""

    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(code)


def norm(sku: str | None) -> str:
    return (sku or "").strip().lower()


@dataclass
class ItemPrevia:
    sku: str
    novo: str | None
    acao: str
    motivo: str | None = None
    product_id: UUID | None = None
    bling_product_id: int | None = None
    # O código do produto no DaVinci (a grafia de lá; `sku` é a da tela).
    sku_atual: str | None = None
    # O próprio produto já está com o código novo no Bling (o DaVinci ainda não acompanhou).
    ja_no_bling: bool = False

    def pular(self, motivo: str) -> ItemPrevia:
        self.acao = PULAR
        self.motivo = motivo
        return self


async def validar_pedido(
    session: AsyncSession, skus: list[str], tag: str
) -> tuple[list[str], str, list[str]]:
    """(SKUs limpos, tag em minúsculas, lista de tags). A tag tem de estar na
    lista "Tag de envio" de Cadastros › Produtos; de 1 a 200 SKUs."""
    tags = await tags_de_envio(session)
    t = (tag or "").strip().lower()
    if not t or t not in tags:
        raise TrocaInvalidaError(
            "tag_invalida",
            f"A tag {tag!s} não está na lista Tag de envio de Cadastros › Produtos.",
        )
    limpos = [(s or "").strip() for s in skus or []]
    if not (1 <= len(limpos) <= MAX_SKUS) or any(not s for s in limpos):
        raise TrocaInvalidaError("skus", f"Escolha de 1 a {MAX_SKUS} produtos.")
    return limpos, t, tags


async def _skus_casados_pelo_codigo_inteiro(session: AsyncSession) -> tuple[set[str], set[str]]:
    """(mala, catálogo): os SKUs (minúsculos) que aparecem nas linhas da
    Tabela de Preços desses departamentos — os dois casam o anúncio pelo
    código INTEIRO (`sku_match.sku_casa_no_departamento`). O departamento é o
    segmento raiz da linha, como a Tabela decide (`pricing/audit._dept_value`).
    As partes de um kit da célula ("b109.20+a075") entram também."""
    raizes = await _load_segment_roots(session)
    mala: set[str] = set()
    catalogo: set[str] = set()
    for sku_celula, segmento, departamento in (
        await session.execute(
            select(PricingProduct.sku, PricingProduct.segment_id, PricingProduct.department)
        )
    ).all():
        dept = raizes.get(segmento) or (str(departamento.value) if departamento else "")
        if dept not in ("mala", "catalogo"):
            continue
        alvo = mala if dept == "mala" else catalogo
        for v in variants_of(sku_celula):
            v = v.strip().lower()
            alvo.add(v)
            alvo.update(p.strip() for p in v.split("+") if p.strip())
    return mala, catalogo


async def _linhas_celular_eletro(
    session: AsyncSession,
) -> list[tuple[object, frozenset[str], frozenset[str], frozenset[str]]]:
    """Linhas de celular/eletro da Tabela de Preços: (linha, SKUs inteiros,
    códigos base, SKUs das OUTRAS linhas do departamento). Para conferir que a
    troca não muda a linha que casa o código (`sku_match.casa_pelo_codigo`:
    o mais específico vence; 25/11/2026, Eletro com o mesmo código base)."""
    linhas = await linhas_do_departamento(session)
    out = []
    for dept, por_linha in linhas.por_dept.items():
        if dept not in ("celular", "eletro"):
            continue
        for chave, full in por_linha.items():
            base = frozenset(v.split(".", 1)[0] for v in full)
            outras = frozenset(linhas.outras(dept, chave) - full)
            out.append((chave, full, base, outras))
    return out


def _linhas_que_casam(
    sku: str, linhas: list[tuple[object, frozenset[str], frozenset[str], frozenset[str]]]
) -> set[object]:
    principal = (sku or "").strip().lower().split("+", 1)[0]
    if not principal:
        return set()
    return {
        chave
        for chave, full, base, outras in linhas
        if casa_pelo_codigo(
            principal, sku_full_set=full, sku_base_set=base, skus_outras_linhas=outras
        )
    }


async def previa(
    session: AsyncSession,
    client: Any,
    skus: list[str],
    tag: str,
    *,
    conferir_bling: bool = True,
) -> list[ItemPrevia]:
    """Para cada SKU: alterar ou pular (com o motivo). Não grava nada. A
    consulta ao Bling (o código novo já existe lá?) só é feita para quem
    passou em todo o resto. `conferir_bling=False` (execução): a consulta é
    feita item a item, logo antes de cada PATCH (`_trocar_item`)."""
    limpos, tag, tags = await validar_pedido(session, skus, tag)
    chaves = {norm(s) for s in limpos}

    produtos = (
        (
            await session.execute(
                select(Product)
                .where(func.lower(func.btrim(Product.sku)).in_(chaves))
                .order_by((Product.situacao == "A").desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    por_sku: dict[str, list[Product]] = defaultdict(list)
    for p in produtos:
        por_sku[norm(p.sku)].append(p)
    pids = {int(p.bling_product_id) for p in produtos if p.bling_product_id}

    # Kit pela estrutura do Bling (cópia semanal): o produto É kit…
    kits_com_estrutura = (
        set(
            (
                await session.execute(
                    select(BlingKitComponent.kit_bling_product_id)
                    .where(BlingKitComponent.kit_bling_product_id.in_(pids))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if pids
        else set()
    )
    # …ou é PEÇA de algum kit.
    kits_da_peca: dict[int, set[int]] = defaultdict(set)
    if pids:
        for comp, kit in (
            await session.execute(
                select(
                    BlingKitComponent.component_bling_product_id,
                    BlingKitComponent.kit_bling_product_id,
                ).where(BlingKitComponent.component_bling_product_id.in_(pids))
            )
        ).all():
            kits_da_peca[int(comp)].add(int(kit))
    ids_dos_kits = {k for ks in kits_da_peca.values() for k in ks}
    sku_do_kit: dict[int, tuple[str, str | None]] = {}
    if ids_dos_kits:
        for bid, ksku, ksit in (
            await session.execute(
                select(Product.bling_product_id, Product.sku, Product.situacao)
                .where(Product.bling_product_id.in_(ids_dos_kits))
                .order_by((Product.situacao == "A").desc().nullslast())
            )
        ).all():
            sku_do_kit.setdefault(int(bid), (ksku.strip(), ksit))
    # Kit pelo "+" do SKU: as partes do código (qualquer kit não excluído).
    kits_por_peca_sku: dict[str, set[str]] = defaultdict(set)
    for (ksku,) in (
        await session.execute(
            select(Product.sku).where(
                Product.sku.contains("+"),
                or_(Product.situacao.is_(None), Product.situacao != "E"),
            )
        )
    ).all():
        for parte in ksku.split("+"):
            k = norm(parte)
            if k in chaves:
                kits_por_peca_sku[k].add(ksku.strip())

    mala_tabela, catalogo_tabela = await _skus_casados_pelo_codigo_inteiro(session)
    linhas_tabela = await _linhas_celular_eletro(session)
    raizes = await _load_segment_roots(session)

    ids_produtos = [p.id for p in produtos]
    # Canais cuja escrita de estoque/preço depende de o SKU do vínculo ser o
    # do produto (ver `CANAIS_SKU_IGUAL`): produto → canais; SKU do vínculo →
    # canais.
    canais_do_produto: dict[UUID, set[IntegrationPlatform]] = defaultdict(set)
    canais_do_sku: dict[str, set[IntegrationPlatform]] = defaultdict(set)
    plataformas = list(CANAIS_SKU_IGUAL)
    for prod_id, ext_sku, plat_link, plat_integ in (
        await session.execute(
            select(
                ProductLink.product_id,
                ProductLink.external_sku,
                ProductLink.platform,
                Integration.platform,
            )
            .select_from(ProductLink)
            .outerjoin(Integration, Integration.id == ProductLink.integration_id)
            .where(
                or_(
                    ProductLink.platform.in_(plataformas),
                    Integration.platform.in_(plataformas),
                ),
                or_(
                    ProductLink.product_id.in_(ids_produtos),
                    func.lower(func.btrim(ProductLink.external_sku)).in_(chaves),
                ),
            )
        )
    ).all():
        for plat in (plat_link, plat_integ):
            if plat in CANAIS_SKU_IGUAL:
                canais_do_produto[prod_id].add(plat)
                if ext_sku:
                    canais_do_sku[norm(ext_sku)].add(plat)

    novos = {norm(codigo_com_tag(s, tag, tags)) for s in limpos}
    existentes = set(
        (
            await session.execute(
                select(func.lower(func.btrim(Product.sku))).where(
                    func.lower(func.btrim(Product.sku)).in_(novos)
                )
            )
        )
        .scalars()
        .all()
    )

    itens: list[ItemPrevia] = []
    vistos: set[str] = set()
    reservados: dict[str, str] = {}
    for s in limpos:
        k = norm(s)
        item = ItemPrevia(sku=s, novo=codigo_com_tag(s, tag, tags), acao=ALTERAR)
        itens.append(item)
        if k in vistos:
            item.pular(MOTIVO_REPETIDO)
            continue
        vistos.add(k)
        linhas = por_sku.get(k) or []
        if "+" in k:
            item.pular(MOTIVO_KIT)
            continue
        if not linhas:
            item.pular(MOTIVO_NAO_ENCONTRADO)
            continue
        vivos = [p for p in linhas if (p.situacao or "").upper() != "E"] or linhas
        if any(
            (p.formato or "").upper() == "E"
            or (p.bling_product_id and int(p.bling_product_id) in kits_com_estrutura)
            for p in vivos
        ):
            item.pular(MOTIVO_KIT)
            continue
        if any((p.formato or "").upper() == "V" for p in vivos):
            item.pular(MOTIVO_VARIACOES)
            continue
        ativos = [p for p in linhas if p.situacao in ("A", None)]
        if not ativos:
            excluido = all((p.situacao or "").upper() == "E" for p in linhas)
            item.pular(MOTIVO_EXCLUIDO if excluido else MOTIVO_INATIVO)
            continue
        com_bling = [p for p in ativos if p.bling_product_id]
        if not com_bling:
            item.pular(MOTIVO_SEM_BLING)
            continue
        p = com_bling[0]
        item.product_id = p.id
        item.bling_product_id = int(p.bling_product_id)
        item.sku_atual = p.sku.strip()
        item.novo = codigo_com_tag(item.sku_atual, tag, tags)
        if tag_do_sku(k, tags) == tag:
            item.pular(f"já está com a tag {tag}")
            continue
        kits = set(kits_por_peca_sku.get(k, ()))
        for kid in kits_da_peca.get(item.bling_product_id, ()):
            ksku, ksit = sku_do_kit.get(kid, (f"#{kid} no Bling", None))
            if (ksit or "").upper() != "E":
                kits.add(ksku)
        if kits:
            nomes = sorted(kits, key=str.lower)
            lista = ", ".join(nomes[:_KITS_NO_MOTIVO])
            if len(nomes) > _KITS_NO_MOTIVO:
                lista += f" e mais {len(nomes) - _KITS_NO_MOTIVO}"
            item.pular(f"faz parte de kit ({lista})")
            continue
        if (
            k in mala_tabela
            or any(raizes.get(x.segment_id) == "mala" for x in linhas if x.segment_id)
            or classify_sku_tag(k) == "mala"
        ):
            item.pular(MOTIVO_MALA)
            continue
        if k in catalogo_tabela:
            item.pular(MOTIVO_CATALOGO)
            continue
        if _linhas_que_casam(item.sku_atual, linhas_tabela) != _linhas_que_casam(
            item.novo, linhas_tabela
        ):
            item.pular(MOTIVO_TABELA_LINHA)
            continue
        canais = set(canais_do_sku.get(k, ()))
        for x in linhas:
            canais |= canais_do_produto.get(x.id, set())
        motivo_canal = next((m for c, m in CANAIS_SKU_IGUAL.items() if c in canais), None)
        if motivo_canal:
            item.pular(motivo_canal)
            continue
        novo = norm(item.novo)
        if novo in existentes:
            item.pular(MOTIVO_EXISTE)
            continue
        if novo in reservados:
            item.pular(f"outro item da lista ({reservados[novo]}) já vai virar {item.novo}")
            continue
        reservados[novo] = s

    # O código novo já existe no Bling (qualquer situação)? Só para quem passou.
    for item in itens:
        if item.acao != ALTERAR or not conferir_bling:
            continue
        motivo, achados = await _codigo_no_bling(client, item.novo, item.bling_product_id)
        if motivo:
            item.pular(motivo)
        elif achados:
            item.ja_no_bling = True
            item.motivo = MOTIVO_JA_NO_BLING
    return itens


async def _codigo_no_bling(client: Any, novo: str, pid: int | None) -> tuple[str | None, list]:
    """(motivo para pular | None, produtos do Bling com o código novo). Outro
    produto com o código → "já existe"; erro na consulta → pula (na dúvida
    não troca); só o próprio produto → nada a pular (já trocado no Bling)."""
    try:
        achados = await client.produtos_com_codigo(novo)
    except Exception as exc:  # noqa: BLE001 — na dúvida não troca
        logger.warning("produto_troca_tag_bling_consulta", novo=novo, erro=str(exc)[:200])
        return f"não deu para conferir no Bling se {novo} já existe: tente de novo", []
    if any(_int(a.get("id")) != pid for a in achados):
        return MOTIVO_EXISTE, achados
    return None, achados


async def _existe_no_davinci(novo: str) -> bool:
    async with session_scope() as s:
        return (
            await s.execute(
                select(Product.id).where(func.lower(func.btrim(Product.sku)) == norm(novo)).limit(1)
            )
        ).first() is not None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── Execução ──────────────────────────────────────────────────────────────


async def por_idem(session: AsyncSession, idem_key: str) -> ProdutoTrocaTag | None:
    return (
        await session.execute(select(ProdutoTrocaTag).where(ProdutoTrocaTag.idem_key == idem_key))
    ).scalar_one_or_none()


@asynccontextmanager
async def _trava():
    """Uma troca de tag por vez (clique, aba repetida, outra pessoa): a
    conferência "o código novo não existe" vale até o PATCH acabar. Não
    espera: quem chega com outra rodando ouve "em andamento"."""
    chave = {"k": "produto_troca_tag"}
    async with session_scope() as s:
        ok = bool(
            (await s.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), chave)).scalar()
        )
        try:
            yield ok
        finally:
            if ok:
                await s.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), chave)


async def _gravar_item(item_id: UUID, **campos: Any) -> None:
    async with session_scope() as s:
        it = await s.get(ProdutoTrocaTagItem, item_id)
        if it is None:
            return
        for k, v in campos.items():
            setattr(it, k, v)


async def _refletir(product_id: UUID | None, pid: int, novo: str) -> bool:
    """Leva o código novo ao DaVinci pelo caminho do webhook "Bling renomeou o
    código do produto" (`aplicar_codigo_do_bling`), sem esperar o aviso. O
    produto é o que foi conferido; se sumiu, o que o webhook acharia.

    O vínculo do PRÓPRIO Bling (o id do produto no `external_id`) também passa
    a guardar o código novo: é o código do produto lá, e a Carrefour confere o
    SKU do vínculo do Bling de cada lote da família antes de mandar estoque
    (`carrefour_stock_source`) — com o código velho, a oferta de um lote irmão
    pararia de receber."""
    try:
        async with session_scope() as s:
            p = await s.get(Product, product_id) if product_id else None
            if p is None or p.bling_product_id is None or int(p.bling_product_id) != int(pid):
                p = await resolver_produto(s, sku=novo, bling_product_id=pid)
            aplicar_codigo_do_bling(p, novo, pid, evento="produto_troca_tag_sku_renomeado")
            if p is not None:
                for link in (
                    (
                        await s.execute(
                            select(ProductLink).where(
                                ProductLink.product_id == p.id,
                                ProductLink.platform == IntegrationPlatform.BLING,
                                ProductLink.external_id == str(int(pid)),
                            )
                        )
                    )
                    .scalars()
                    .all()
                ):
                    if link.external_sku and link.external_sku.strip() != novo:
                        link.external_sku = novo
        return True
    except Exception:  # noqa: BLE001 — o Bling já trocou; o webhook leva depois
        logger.exception("produto_troca_tag_refletir_falhou", bling_product_id=pid, novo=novo)
        return False


def _recusa_certa(exc: Exception) -> bool:
    """O Bling com certeza NÃO trocou: limite de chamadas/conexão que nem
    abriu, recusa 4xx, ou erro de outra chamada (renovar o token) antes de
    enviar o PATCH. O resto (5xx do PATCH, timeout, conexão caída na
    resposta) é dúvida: confere com GET."""
    if _falha_sem_envio(exc):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            do_patch = exc.request.method == "PATCH"
        except (AttributeError, RuntimeError):
            do_patch = False
        return not do_patch or exc.response.status_code < 500
    return False


def _texto_recusa(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        detalhe = ""
        try:
            err = (exc.response.json() or {}).get("error") or {}
            detalhe = str(err.get("description") or err.get("message") or "").strip()
            campos = err.get("fields") if isinstance(err.get("fields"), list) else []
            msgs = [str(f.get("msg") or "").strip() for f in campos if isinstance(f, dict)]
            if any(msgs):
                detalhe = "; ".join(m for m in msgs if m)
        except Exception:  # noqa: BLE001
            detalhe = ""
        base = f"o Bling recusou a troca (HTTP {exc.response.status_code})"
        return f"{base}: {detalhe[:200]}" if detalhe else base
    if _falha_sem_envio(exc):
        return "o Bling estava ocupado (limite de chamadas ou conexão) e recusou a troca"
    return f"o Bling não respondeu direito: {str(exc)[:160]}"


async def _avisar(
    user_id: UUID | None, item_id: UUID, titulo: str, msg: str, *, chave: str | None = None
) -> None:
    """Sino de quem pediu a troca (best-effort). `chave`: um aviso por
    acontecimento do item (o "confira" da hora e o "não trocou" da
    reconferência são dois avisos)."""
    if user_id is None:
        return
    try:
        async with session_scope() as s:
            await emit_alert(
                s,
                user_id=user_id,
                type=AlertType.GENERIC,
                escopo="pessoal",
                severity=AlertSeverity.WARNING,
                title=titulo,
                message=msg,
                dedupe_key=f"produto_troca_tag:{item_id}" + (f":{chave}" if chave else ""),
                payload={"item_id": str(item_id)},
            )
    except Exception:  # noqa: BLE001
        logger.warning("produto_troca_tag_aviso_falhou", item=str(item_id))


async def _trocar_item(client: Any, item: ProdutoTrocaTagItem, prev: ItemPrevia, quem: UUID | None):
    """Troca UM código no Bling e grava o resultado do item. Nunca levanta."""
    pid = int(prev.bling_product_id)
    antigo, novo = prev.sku_atual or prev.sku, prev.novo
    agora = datetime.now

    async def fim(estado: str, motivo: str | None = None) -> None:
        await _gravar_item(item.id, estado=estado, motivo=motivo, concluido_at=agora(UTC))

    # 1. O produto no Bling, agora: o código lá tem de ser o antigo.
    try:
        raw = await client.get_product(pid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("produto_troca_tag_get_falhou", pid=pid, erro=str(exc)[:200])
        await fim(FALHOU, "não deu para ler o produto no Bling agora: nada mudou")
        return
    codigo_bling = str((raw or {}).get("codigo") or "").strip()
    if codigo_bling.casefold() == novo.casefold():
        await _refletir(prev.product_id, pid, novo)
        await fim(OK, MOTIVO_JA_NO_BLING)
        return
    if codigo_bling.casefold() != antigo.casefold():
        await fim(
            PULADO,
            f"no Bling o código é {codigo_bling or '(vazio)'}, não {antigo}: "
            "espere o DaVinci acompanhar e tente de novo",
        )
        return
    estrutura = (raw or {}).get("estrutura") or {}
    if (
        str(raw.get("formato") or "").upper() == "E"
        or str(raw.get("tipo") or "").upper() == "S"
        or (isinstance(estrutura, dict) and estrutura.get("componentes"))
    ):
        await fim(PULADO, MOTIVO_KIT)
        return
    if str(raw.get("formato") or "").upper() == "V":
        await fim(PULADO, MOTIVO_VARIACOES)
        return
    situacao = str(raw.get("situacao") or "").upper()
    if situacao and situacao != "A":
        await fim(PULADO, MOTIVO_EXCLUIDO if situacao == "E" else MOTIVO_INATIVO)
        return
    # O código novo não pode existir em lugar nenhum — conferido agora, logo
    # antes do PATCH (a prévia pode ter sido minutos antes).
    if await _existe_no_davinci(novo):
        await fim(PULADO, MOTIVO_EXISTE)
        return
    motivo, _achados = await _codigo_no_bling(client, novo, pid)
    if motivo:
        await fim(PULADO, motivo)
        return

    # 2. "enviando" gravado ANTES do PATCH.
    await _gravar_item(item.id, estado=ENVIANDO, tentativa_at=agora(UTC))
    try:
        await client.update_product_codigo(pid, novo)
    except Exception as exc:  # noqa: BLE001 — classificado abaixo
        logger.warning("produto_troca_tag_patch_erro", pid=pid, novo=novo, erro=str(exc)[:200])
        if _recusa_certa(exc):
            await fim(FALHOU, f"{_texto_recusa(exc)}: nada mudou")
            return
        # 3. Dúvida: o código mudou? GET, nunca outro PATCH.
        try:
            depois = str(((await client.get_product(pid)) or {}).get("codigo") or "").strip()
        except Exception:  # noqa: BLE001
            depois = None
        if depois is not None and depois.casefold() == novo.casefold():
            await _refletir(prev.product_id, pid, novo)
            await fim(OK, None)
            return
        if depois is not None and depois.casefold() == antigo.casefold():
            # O Bling pode aplicar o PATCH depois de responder erro (pedido
            # que leva até ~1 min — casos 300334/298826): não é "nada mudou".
            # O worker reconfere (`reconferir_sem_confirmacao`).
            await fim(SEM_CONFIRMACAO, MOTIVO_NAO_CONFIRMOU)
            await _avisar(
                quem,
                item.id,
                f"Trocar a tag: {antigo} ainda sem confirmação no Bling",
                f"{antigo} → {novo}: {MOTIVO_NAO_CONFIRMOU}.",
            )
            return
        motivo = (
            "o Bling respondeu com erro e não deu para conferir se o código mudou: confira "
            f"{antigo} no Bling (se mudou, o DaVinci acompanha sozinho)"
            if depois is None
            else f"o Bling respondeu com erro e o código lá agora é {depois}: confira no Bling"
        )
        await fim(SEM_CONFIRMACAO, motivo)
        await _avisar(quem, item.id, f"Trocar a tag: confira {antigo} no Bling", motivo)
        return
    await _refletir(prev.product_id, pid, novo)
    await fim(OK, None)
    logger.info("produto_troca_tag_ok", pid=pid, antigo=antigo, novo=novo)


async def _fechar_parada(troca_id: UUID) -> None:
    """Troca que ficou no meio (o processo caiu): o que nem foi enviado vira
    falhou (nada mudou); o que estava "enviando" fica para a pessoa conferir
    (se o Bling trocou, o aviso do Bling leva ao DaVinci)."""
    agora = datetime.now(UTC)
    async with session_scope() as s:
        troca = await s.get(ProdutoTrocaTag, troca_id)
        itens = (
            (
                await s.execute(
                    select(ProdutoTrocaTagItem).where(ProdutoTrocaTagItem.troca_id == troca_id)
                )
            )
            .scalars()
            .all()
        )
        for it in itens:
            if it.estado == PENDENTE:
                it.estado, it.motivo, it.concluido_at = (
                    FALHOU,
                    "o DaVinci parou antes de trocar: nada mudou",
                    agora,
                )
            elif it.estado == ENVIANDO:
                it.estado, it.motivo, it.concluido_at = (
                    SEM_CONFIRMACAO,
                    "o DaVinci parou no meio da troca: confira o código no Bling",
                    agora,
                )
        if troca is not None and troca.concluido_em is None:
            troca.concluido_em = agora


async def executar(
    session: AsyncSession,
    client: Any,
    *,
    idem_key: str,
    user_id: UUID | None,
    user_nome: str | None,
    skus: list[str],
    tag: str,
) -> ProdutoTrocaTag:
    """Recalcula a prévia no servidor e troca, um por um, só os "alterar".
    Mesma `idem_key` = a mesma troca (não troca de novo); com outro pedido,
    409. Outra troca rodando: 409 "em andamento"."""
    limpos, t, _tags = await validar_pedido(session, skus, tag)
    pedido = ",".join(norm(s) for s in limpos)
    existente = await por_idem(session, idem_key)
    if existente is not None:
        if existente.pedido != pedido or existente.tag != t:
            raise TrocaInvalidaError(
                "idempotency_conflito",
                "Esta janela já aplicou outra troca. Feche e abra de novo.",
                409,
            )
        if existente.concluido_em is None:
            async with _trava() as livre:
                if not livre:
                    raise TrocaInvalidaError(
                        "em_andamento", "Esta troca ainda está rodando. Espere terminar.", 409
                    )
                await _fechar_parada(existente.id)
        return existente
    async with _trava() as livre:
        if not livre:
            raise TrocaInvalidaError(
                "em_andamento",
                "Outra troca de tag está rodando agora. Tente de novo em alguns minutos.",
                409,
            )
        itens = await previa(session, client, limpos, t, conferir_bling=False)
        troca = ProdutoTrocaTag(
            idem_key=idem_key,
            tag=t,
            pedido=pedido,
            criado_por=user_id,
            criado_por_nome=(user_nome or "").strip() or None,
        )
        session.add(troca)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existente = await por_idem(session, idem_key)
            if existente is None:
                raise
            return existente
        agora = datetime.now(UTC)
        linhas: list[tuple[ProdutoTrocaTagItem, ItemPrevia]] = []
        for i, it in enumerate(itens):
            linha = ProdutoTrocaTagItem(
                troca_id=troca.id,
                ordem=i,
                sku_antigo=it.sku_atual or it.sku,
                sku_novo=it.novo,
                bling_product_id=it.bling_product_id,
                estado=PENDENTE if it.acao == ALTERAR else PULADO,
                motivo=it.motivo,
                concluido_at=None if it.acao == ALTERAR else agora,
            )
            session.add(linha)
            linhas.append((linha, it))
        await session.commit()
        logger.info(
            "produto_troca_tag_criada",
            troca=str(troca.id),
            tag=t,
            alterar=sum(1 for _l, it in linhas if it.acao == ALTERAR),
            pular=sum(1 for _l, it in linhas if it.acao != ALTERAR),
        )
        for linha, it in linhas:
            if it.acao != ALTERAR:
                continue
            try:
                await _trocar_item(client, linha, it, user_id)
            except Exception:  # noqa: BLE001 — um item não para os outros
                logger.exception("produto_troca_tag_item_falhou", item=str(linha.id))
                await _gravar_item(
                    linha.id,
                    estado=FALHOU,
                    motivo=(
                        "erro no DaVinci durante a troca: confira o código no Bling "
                        "(se mudou, o DaVinci acompanha sozinho)"
                    ),
                    concluido_at=datetime.now(UTC),
                )
        troca.concluido_em = datetime.now(UTC)
        await session.commit()
    return troca


async def itens_da_troca(session: AsyncSession, troca_id: UUID) -> list[ProdutoTrocaTagItem]:
    return list(
        (
            await session.execute(
                select(ProdutoTrocaTagItem)
                .where(ProdutoTrocaTagItem.troca_id == troca_id)
                .order_by(ProdutoTrocaTagItem.ordem)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def estado_na_tela(estado: str) -> str:
    """ok | pulado | falhou (sem confirmação e o que ficou no meio contam
    como falhou: a tela mostra o motivo)."""
    if estado in (OK, PULADO):
        return estado
    return FALHOU


# ── Worker: reconfere no Bling as trocas sem confirmação ─────────────────

# Só os itens das últimas 24 h (depois disso fica o aviso que já foi dado).
_RECONFERIR_JANELA = timedelta(hours=24)
# Ainda o código antigo no Bling 30 min depois do PATCH: o Bling não trocou.
_RECONFERIR_PRAZO = timedelta(minutes=30)


def _quando_item():
    return func.coalesce(
        ProdutoTrocaTagItem.tentativa_at,
        ProdutoTrocaTagItem.concluido_at,
        ProdutoTrocaTagItem.created_at,
    )


def _filtro_reconferir(agora: datetime) -> list:
    return [
        ProdutoTrocaTagItem.estado == SEM_CONFIRMACAO,
        ProdutoTrocaTagItem.bling_product_id.is_not(None),
        ProdutoTrocaTagItem.sku_novo.is_not(None),
        _quando_item() >= agora - _RECONFERIR_JANELA,
    ]


async def ha_para_reconferir(session: AsyncSession, *, agora: datetime | None = None) -> bool:
    """Há item "sem_confirmacao" das últimas 24 h? (1 SELECT; o worker só
    abre o Bling quando há.)"""
    agora = agora or datetime.now(UTC)
    return (
        await session.execute(
            select(ProdutoTrocaTagItem.id).where(*_filtro_reconferir(agora)).limit(1)
        )
    ).first() is not None


async def _produto_do_item(session: AsyncSession, it: ProdutoTrocaTagItem) -> UUID | None:
    """O produto do DaVinci do item, pelo id do Bling (o que ainda tem o
    código antigo primeiro)."""
    rows = (
        await session.execute(
            select(Product.id, Product.sku).where(
                Product.bling_product_id == int(it.bling_product_id)
            )
        )
    ).all()
    for prod_id, sku in rows:
        if norm(sku) == norm(it.sku_antigo):
            return prod_id
    return rows[0][0] if rows else None


async def _reconferir_item(client: Any, item_id: UUID, agora: datetime) -> str | None:
    """UM GET do produto no Bling (nunca PATCH). Devolve "confirmado",
    "falhou" ou None (fica para o próximo tick)."""
    aviso: tuple[UUID | None, str, str] | None = None
    resultado: str | None = None
    async with session_scope() as s:
        it = (
            await s.execute(
                select(ProdutoTrocaTagItem)
                .where(
                    ProdutoTrocaTagItem.id == item_id,
                    ProdutoTrocaTagItem.estado == SEM_CONFIRMACAO,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if it is None:
            return None
        pid = int(it.bling_product_id)
        antigo, novo = it.sku_antigo, it.sku_novo
        try:
            raw = await client.get_product(pid)
        except Exception as exc:  # noqa: BLE001 — confere de novo no próximo tick
            logger.warning("produto_troca_tag_reconferir_get", pid=pid, erro=str(exc)[:200])
            return None
        codigo = str((raw or {}).get("codigo") or "").strip()
        if codigo.casefold() == novo.casefold():
            await _refletir(await _produto_do_item(s, it), pid, novo)
            it.estado, it.motivo, it.concluido_at = OK, MOTIVO_CONFIRMADO_DEPOIS, agora
            resultado = "confirmado"
            logger.info("produto_troca_tag_confirmado_depois", pid=pid, antigo=antigo, novo=novo)
        elif codigo.casefold() == antigo.casefold():
            t0 = it.tentativa_at or it.concluido_at or it.created_at or agora
            if agora - t0 < _RECONFERIR_PRAZO:
                return None
            minutos = int(_RECONFERIR_PRAZO.total_seconds() // 60)
            motivo = (
                f"o Bling não aplicou a troca: {minutos} min depois o código continua "
                f"{antigo}: nada mudou"
            )
            it.estado, it.motivo, it.concluido_at = FALHOU, motivo, agora
            quem = (
                await s.execute(
                    select(ProdutoTrocaTag.criado_por).where(ProdutoTrocaTag.id == it.troca_id)
                )
            ).scalar_one_or_none()
            aviso = (
                quem,
                f"Trocar a tag: {antigo} não mudou no Bling",
                f"{antigo} → {novo}: {motivo}.",
            )
            resultado = "falhou"
            logger.warning("produto_troca_tag_nao_aplicou", pid=pid, antigo=antigo, novo=novo)
        else:
            # Outro código (alguém mexeu no Bling): fica para a pessoa conferir.
            return None
    if aviso is not None:
        quem, titulo, msg = aviso
        await _avisar(quem, item_id, titulo, msg, chave=FALHOU)
    return resultado


async def reconferir_sem_confirmacao(
    client: Any, *, agora: datetime | None = None
) -> dict[str, int]:
    """Worker (a cada 2 min): os itens "sem_confirmacao" das últimas 24 h
    (o PATCH teve resposta duvidosa e o GET logo depois não confirmou). Para
    cada um, um GET do produto no Bling — nunca outro PATCH:
    - o Bling já está com o código novo → o mesmo reflexo no DaVinci da troca
      certa (`_refletir`) e "ok";
    - ainda o código antigo 30 min depois do PATCH → "falhou", com aviso no
      sino de quem pediu;
    - antes disso, outro código ou GET com erro → fica para o próximo tick."""
    agora = agora or datetime.now(UTC)
    out = {"conferidos": 0, "confirmados": 0, "falharam": 0}
    async with session_scope() as s:
        ids = (
            (
                await s.execute(
                    select(ProdutoTrocaTagItem.id)
                    .where(*_filtro_reconferir(agora))
                    .order_by(_quando_item())
                )
            )
            .scalars()
            .all()
        )
    for item_id in ids:
        out["conferidos"] += 1
        try:
            r = await _reconferir_item(client, item_id, agora)
        except Exception:  # noqa: BLE001 — um item não para os outros
            logger.exception("produto_troca_tag_reconferir_falhou", item=str(item_id))
            continue
        if r == "confirmado":
            out["confirmados"] += 1
        elif r == "falhou":
            out["falharam"] += 1
    return out
