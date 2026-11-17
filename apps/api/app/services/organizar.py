"""ORGANIZAR da tela Produtos: esconde da TELA o que é inválido (17/11/2026).

Eduardo, 17/11/2026 (decisões 3 e 4): o "Remover os N mortos" sai da tela e
fica só o ORGANIZAR — só ADM, só ESCONDE, dá para desfazer (por lote, por
motivo ou por item). Grava só uma marca em `organizar_ocultos`: nunca apaga,
nunca altera products/product_links/listings, nunca grava no Bling nem no
marketplace.

NENHUM ROBÔ IMPORTA ESTE MÓDULO (tests/test_organizar.py confere): o filtro
entra só nas leituras da tela — list_products(ocultos=esconder|so),
contar_departamentos e list_listings. A limpeza de 30 dias da varredura de
vínculos se comporta como se o Organizar não existisse; só o "Remover os N
mortos" (endpoint de ADM) pula o que tem marca ativa
(vinculo_saude.com_marca_do_organizar). O robô continua mandando estoque
(inclusive o 0 dos excluídos) para tudo que está escondido.

A marca só vale enquanto o item continua INVÁLIDO — conferido NA LEITURA, sem
gatilho no banco, pela família da tabela e não pela chave gravada (um inativo
que vira excluído continua escondido; um encerrado de loja que foi arquivada
também): produto que volta a 'A' reaparece, vínculo morto que revive
reaparece, loja desarquivada volta, anúncio religado a um produto válido e
reaberto volta. Marca vencida é fechada (sem autor) na próxima prévia, no
aplicar e no "mostrar de novo" — não volta a esconder sozinha se o item ficar
inválido de novo; para vínculo morto e loja arquivada a data de depois da
marca também não a reativa. `motivo` gravado = a própria chave; as chaves da
mesma tabela não se sobrepõem (cada item cai numa só), então a contagem e o
"mostrar de novo por chave" casam exato.

SQL cru com lista: sempre `CAST(:x AS uuid[])` / `= ANY(...)`, nunca
`:x::tipo` no text() (o `::` grudado no parâmetro impede o bind).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import (
    ARRAY,
    and_,
    any_,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import (
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    OrganizarOculto,
    OrganizarPrevia,
    Product,
    ProductLink,
    User,
)
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

# Dois cliques em "Ocultar" ao mesmo tempo: o segundo recebe 409.
ORGANIZAR_LOCK_KEY = 0x6F72677A  # ascii "orgz"
PREVIA_MINUTOS = 30
POR_PAGINA = 100


class OrganizarError(Exception):
    """Erro com o código que a tela entende (o router vira HTTPException)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


# ── Condições (o que é inválido) ─────────────────────────────────────────────
# Cada condição recebe a entidade (ou um alias dela) e só usa ALIAS nas
# subconsultas de dentro: assim nunca correlaciona por engano com a consulta de
# fora (que pode ter products/integrations no FROM).


def _arquivadas():
    i = aliased(Integration)
    return select(i.id).where(i.archived_at.is_not(None))


def _conta_ativa(coluna):
    return or_(coluna.is_(None), coluna.not_in(_arquivadas()))


def _vinculo_vivo(produto_id):
    """Existe vínculo de marketplace VIVO (não morto) em conta não arquivada."""
    pl = aliased(ProductLink)
    return (
        select(pl.id)
        .where(
            pl.product_id == produto_id,
            pl.platform != IntegrationPlatform.BLING,
            _conta_ativa(pl.integration_id),
            pl.morto_desde.is_(None),
        )
        .exists()
    )


def _produto_excluido(produto_id):
    pz = aliased(Product)
    return select(pz.id).where(pz.id == produto_id, pz.situacao == "E").exists()


@dataclass(frozen=True)
class Motivo:
    chave: str
    tabela: str  # products | product_links | listings
    rotulo: str
    explicacao: str
    marcado_por_padrao: bool
    condicao: Callable[[Any], Any]


# Ordem = ordem da prévia. Os critérios são os que já existem no DaVinci
# (vinculo_saude, products.situacao, integrations.archived_at, listings.status).
_LISTA = (
    Motivo(
        "vinculo_morto",
        "product_links",
        "Vínculos mortos",
        "O marketplace disse que o anúncio acabou (encerrado, excluído, congelado ou "
        "bloqueado). Pausado e em revisão não entram. Volta a aparecer sozinho se o "
        "anúncio reviver.",
        True,
        lambda pl: and_(
            pl.morto_desde.is_not(None),
            pl.platform != IntegrationPlatform.BLING,
            _conta_ativa(pl.integration_id),
        ),
    ),
    Motivo(
        "produto_excluido_sem_anuncio",
        "products",
        "Produtos excluídos no Bling, sem anúncio vivo",
        "SKU apagado no Bling (situação E) e sem nenhum vínculo vivo de marketplace em "
        "loja ativa. Volta a aparecer se o produto voltar no Bling.",
        True,
        lambda p: and_(p.situacao == "E", ~_vinculo_vivo(p.id)),
    ),
    Motivo(
        "produto_excluido_com_anuncio",
        "products",
        "Produtos excluídos no Bling, ainda com anúncio vivo",
        "Ainda é anúncio no ar: o robô continua mandando 0 de estoque mesmo oculto. "
        "Decida antes de ocultar. Volta a aparecer se o produto voltar no Bling.",
        False,
        lambda p: and_(p.situacao == "E", _vinculo_vivo(p.id)),
    ),
    Motivo(
        "produto_inativo",
        "products",
        "Produtos inativos no Bling",
        "Situação I no Bling. Volta a aparecer quando o produto voltar a ativo.",
        False,
        lambda p: p.situacao == "I",
    ),
    Motivo(
        "anuncio_loja_arquivada",
        "listings",
        "Anúncios de loja arquivada",
        "Na aba Produtos esses anúncios já não aparecem; na aba Anúncios ainda aparecem. "
        "Voltam se a loja for desarquivada.",
        True,
        lambda a: a.integration_id.in_(_arquivadas()),
    ),
    Motivo(
        "anuncio_encerrado",
        "listings",
        "Anúncios encerrados",
        "Encerrados no marketplace, em loja ativa. Voltam se o anúncio for reaberto.",
        True,
        lambda a: and_(a.status == ListingStatus.CLOSED, _conta_ativa(a.integration_id)),
    ),
    Motivo(
        "anuncio_produto_excluido",
        "listings",
        "Anúncios ligados a produto excluído no Bling",
        "Anúncio de loja ativa ligado a um produto com situação E. Volta se o produto "
        "voltar ou se o anúncio for ligado a um produto válido.",
        False,
        # Encerrado fica na chave de cima: cada anúncio cai numa chave só.
        lambda a: and_(
            a.status != ListingStatus.CLOSED,
            _conta_ativa(a.integration_id),
            _produto_excluido(a.product_id),
        ),
    ),
)
MOTIVOS: dict[str, Motivo] = {m.chave: m for m in _LISTA}

_ENTIDADE = {"products": Product, "product_links": ProductLink, "listings": Listing}


def _ainda_invalido(tabela: str, ent, o):
    """O item `ent` continua inválido para a marca `o`, por QUALQUER chave da
    tabela (a família), e não só pela gravada: inativo que vira excluído,
    excluído que ganha/perde anúncio vivo e encerrado de loja que foi arquivada
    continuam escondidos. Vínculo morto e loja arquivada só valem com a data de
    ANTES da marca: morrer de novo (ou arquivar de novo) depois de voltar à
    tela não reativa a marca velha."""
    if tabela == "products":
        return ent.situacao.in_(("E", "I"))
    if tabela == "product_links":
        return and_(
            ent.morto_desde.is_not(None),
            ent.morto_desde <= o.oculto_em,
            ent.platform != IntegrationPlatform.BLING,
        )
    i = aliased(Integration)
    arquivada_antes = (
        select(i.id)
        .where(
            i.id == ent.integration_id,
            i.archived_at.is_not(None),
            i.archived_at <= o.oculto_em,
        )
        .exists()
    )
    return or_(
        arquivada_antes,
        ent.status == ListingStatus.CLOSED,
        _produto_excluido(ent.product_id),
    )


def _marca_vale(o, tabela: str):
    """A marca `o` (alias de OrganizarOculto) ainda vale: o item existe e
    continua inválido AGORA (ver `_ainda_invalido`)."""
    ent = aliased(_ENTIDADE[tabela])
    return (
        select(ent.id)
        .where(ent.id == o.registro_id, _ainda_invalido(tabela, ent, o))
        .exists()
    )


def _oculto(tabela: str, registro_id):
    o = aliased(OrganizarOculto)
    return (
        select(o.id)
        .where(
            o.tabela == tabela,
            o.registro_id == registro_id,
            o.reexibido_em.is_(None),
            _marca_vale(o, tabela),
        )
        .exists()
    )


# ── Predicados da TELA (correlacionados com a consulta de fora) ──────────────


def produto_oculto(produto_id=None):
    """O produto está escondido (marca ativa e o motivo ainda vale). Sem
    argumento, correlaciona com `Product` da consulta de fora."""
    return _oculto("products", Product.id if produto_id is None else produto_id)


def vinculo_marcado():
    """O vínculo (`ProductLink` de fora) está escondido pela marca DELE (sem
    olhar o produto). Para a lista de Produtos com ocultos=esconder, que já
    tira o produto oculto: o OR com a marca do produto ali dentro custava um
    SubPlan por linha."""
    return _oculto("product_links", ProductLink.id)


def vinculo_oculto():
    """O vínculo (`ProductLink` de fora) está escondido: pela marca dele OU
    porque o produto dele está escondido."""
    return or_(
        _oculto("product_links", ProductLink.id),
        produto_oculto(ProductLink.product_id),
    )


def anuncio_oculto():
    """O anúncio (`Listing` de fora) está escondido."""
    return _oculto("listings", Listing.id)


def _oculto_na_tela(tabela: str):
    if tabela == "products":
        return produto_oculto()
    if tabela == "product_links":
        return vinculo_oculto()
    return anuncio_oculto()


def _onde_candidato(m: Motivo) -> list:
    """Inválido por este motivo e ainda aparecendo na tela."""
    return [m.condicao(_ENTIDADE[m.tabela]), ~_oculto_na_tela(m.tabela)]


def _uuids(valores) -> Any:
    tipo = ARRAY(PG_UUID(as_uuid=True))
    return cast(literal(list(valores), type_=tipo), tipo)


def _tabela_sql() -> str:
    t = OrganizarOculto.__table__
    return f'"{t.schema}".{t.name}' if t.schema else t.name


def _sql_analyze() -> str:
    return f"ANALYZE {_tabela_sql()}"


async def atualizar_estatisticas(session: AsyncSession) -> None:
    """ANALYZE da tabela de marcas logo depois de um "Ocultar"/"Mostrar de
    novo" (17/11/2026). Medido na cópia com os dados reais: com a tabela nunca
    analisada (recém-criada) e 9.428 marcas, a lista de Produtos › Mala foi de
    86 ms para 2,1 s — o planejador não sabe quantas marcas existem até o
    autovacuum passar. Melhor esforço: erro aqui só vira aviso no log (a marca
    já foi gravada). Chamar DEPOIS do commit."""
    try:
        await session.execute(text(_sql_analyze()))
        await session.commit()
    except Exception as e:  # noqa: BLE001 — nunca derruba o Ocultar
        await session.rollback()
        logger.warning("organizar_analyze_falhou", erro=str(e)[:200])


async def _fechar_vencidas(session: AsyncSession) -> None:
    """Fecha (reexibido_em = agora, sem autor: o item voltou sozinho) as marcas
    ativas que não valem mais. Sem isto a marca vencida ficava aberta e, se o
    item voltasse a ficar inválido, ele sumia de novo sem ninguém ocultar — e,
    enquanto vencida, não aparecia em "Ocultos" para desfazer."""
    for tabela in _ENTIDADE:
        await session.execute(
            update(OrganizarOculto)
            .where(
                OrganizarOculto.reexibido_em.is_(None),
                OrganizarOculto.tabela == tabela,
                ~_marca_vale(OrganizarOculto, tabela),
            )
            .values(reexibido_em=func.now())
            .execution_options(synchronize_session=False)
        )


def validar_chave(chave: str) -> Motivo:
    m = MOTIVOS.get(chave)
    if m is None:
        raise OrganizarError(400, "chave_invalida")
    return m


# ── Prévia ───────────────────────────────────────────────────────────────────


async def _ocultos_e_lotes(session: AsyncSession) -> tuple[dict, list[dict]]:
    """Quantos itens estão escondidos AGORA (marca ativa que ainda vale), por
    chave e por lote."""
    por_chave = {m.chave: 0 for m in _LISTA}
    lotes: dict[str, dict] = {}
    for tabela in _ENTIDADE:
        o = aliased(OrganizarOculto)
        linhas = await session.execute(
            select(o.lote_id, o.oculto_por, o.motivo, func.count(), func.min(o.oculto_em))
            .where(o.tabela == tabela, o.reexibido_em.is_(None), _marca_vale(o, tabela))
            .group_by(o.lote_id, o.oculto_por, o.motivo)
        )
        for lote_id, quem, motivo, n, quando in linhas.all():
            por_chave[motivo] = por_chave.get(motivo, 0) + n
            lote = lotes.setdefault(
                str(lote_id),
                {
                    "lote_id": str(lote_id),
                    "oculto_em": quando,
                    "oculto_por": quem,
                    "quantidade": 0,
                    "por_chave": {},
                },
            )
            lote["quantidade"] += n
            lote["por_chave"][motivo] = lote["por_chave"].get(motivo, 0) + n
            if quando is not None and (lote["oculto_em"] is None or quando < lote["oculto_em"]):
                lote["oculto_em"] = quando
    quem_ids = {lote["oculto_por"] for lote in lotes.values() if lote["oculto_por"]}
    nomes: dict = {}
    if quem_ids:
        for uid, nome, email in (
            await session.execute(
                select(User.id, User.name, User.email).where(User.id.in_(quem_ids))
            )
        ).all():
            nomes[uid] = nome or email
    saida = []
    for lote in sorted(
        lotes.values(),
        key=lambda x: x["oculto_em"] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    ):
        quem = lote.pop("oculto_por")
        lote["oculto_por_nome"] = nomes.get(quem)
        lote["oculto_em"] = lote["oculto_em"].isoformat() if lote["oculto_em"] else None
        saida.append(lote)
    ocultos = {"total": sum(por_chave.values()), "por_chave": por_chave}
    return ocultos, saida


async def previa(session: AsyncSession, user: User) -> dict:
    """Quanto há de inválido por chave (o que ainda aparece na tela). Os ids
    ficam guardados no servidor por 30 minutos (token): o aplicar só oculta o
    que esta prévia mostrou. Não faz commit (quem chama faz)."""
    # As vencidas saem a cada prévia nova (tabela pequena, sem cron).
    await session.execute(delete(OrganizarPrevia).where(OrganizarPrevia.expira_em < func.now()))
    await _fechar_vencidas(session)
    ids: dict[str, list[str]] = {}
    for m in _LISTA:
        ent = _ENTIDADE[m.tabela]
        linhas = await session.execute(select(ent.id).where(*_onde_candidato(m)).order_by(ent.id))
        ids[m.chave] = [str(i) for i in linhas.scalars()]
    expira = datetime.now(UTC) + timedelta(minutes=PREVIA_MINUTOS)
    p = OrganizarPrevia(token=uuid.uuid4(), criado_por=user.id, expira_em=expira, ids=ids)
    session.add(p)
    await session.flush()
    ocultos, lotes = await _ocultos_e_lotes(session)
    return {
        "token": str(p.token),
        "expira_em": expira.isoformat(),
        "chaves": [
            {
                "chave": m.chave,
                "tabela": m.tabela,
                "rotulo": m.rotulo,
                "explicacao": m.explicacao,
                "quantidade": len(ids[m.chave]),
                "marcado_por_padrao": m.marcado_por_padrao,
            }
            for m in _LISTA
        ],
        "ocultos": ocultos,
        "lotes": lotes,
    }


# ── Lista de itens ("Ver lista" e a aba "Ocultos") ───────────────────────────


def _consulta_itens(m: Motivo, ocultos: bool, ids_previa: list[uuid.UUID] | None = None):
    """(select das colunas de exibição, função que monta o item). Com
    `ids_previa`, só os itens que a prévia mostrou e ainda valem — o mesmo
    conjunto que o "Ocultar" grava."""
    o = aliased(OrganizarOculto)
    extra = [o.id.label("marcador_id"), o.oculto_em.label("oculto_em")] if ocultos else []
    if m.tabela == "products":
        ent = Product
        q = select(Product.id, Product.sku, Product.name, Product.updated_at, *extra)
        ordem = (Product.sku, Product.id)

        def item(r) -> dict:
            return {"registro_id": r.id, "sku": r.sku, "nome": r.name, "conta": None,
                    "anuncio": None, "variacao": None, "sku_anuncio": None,
                    "motivo": m.rotulo, "data": r.updated_at}
    elif m.tabela == "product_links":
        ent = ProductLink
        q = (
            select(
                ProductLink.id,
                Product.sku,
                Product.name,
                Integration.name.label("conta"),
                ProductLink.external_id,
                ProductLink.variation_id,
                ProductLink.external_sku,
                ProductLink.morto_motivo,
                ProductLink.morto_desde,
                *extra,
            )
            .join(Product, Product.id == ProductLink.product_id)
            .outerjoin(Integration, Integration.id == ProductLink.integration_id)
        )
        ordem = (Product.sku, ProductLink.external_id, ProductLink.variation_id, ProductLink.id)

        def item(r) -> dict:
            return {"registro_id": r.id, "sku": r.sku, "nome": r.name, "conta": r.conta,
                    "anuncio": r.external_id, "variacao": r.variation_id,
                    "sku_anuncio": r.external_sku, "motivo": r.morto_motivo or m.rotulo,
                    "data": r.morto_desde}
    else:
        ent = Listing
        q = (
            select(
                Listing.id,
                Product.sku,
                Listing.title,
                Integration.name.label("conta"),
                Integration.archived_at,
                Listing.external_id,
                Listing.sku.label("sku_anuncio"),
                Listing.updated_at,
                *extra,
            )
            .join(Integration, Integration.id == Listing.integration_id)
            .outerjoin(Product, Product.id == Listing.product_id)
        )
        ordem = (Integration.name, Listing.external_id, Listing.id)

        def item(r) -> dict:
            data = r.archived_at if m.chave == "anuncio_loja_arquivada" else r.updated_at
            return {"registro_id": r.id, "sku": r.sku, "nome": r.title, "conta": r.conta,
                    "anuncio": r.external_id, "variacao": None, "sku_anuncio": r.sku_anuncio,
                    "motivo": m.rotulo, "data": data}

    if ocultos:
        q = q.join(
            o,
            and_(
                o.registro_id == ent.id,
                o.tabela == m.tabela,
                o.motivo == m.chave,
                o.reexibido_em.is_(None),
            ),
        ).where(_marca_vale(o, m.tabela))
    else:
        q = q.where(*_onde_candidato(m))
        if ids_previa is not None:
            q = q.where(ent.id == any_(_uuids(ids_previa)))
    return q, ordem, item


async def _previa_do_token(session: AsyncSession, user: User, token: str | None):
    """A prévia guardada de `user` (não vencida), ou 409 previa_expirada."""
    try:
        tok = uuid.UUID(str(token))
    except (TypeError, ValueError):
        raise OrganizarError(409, "previa_expirada") from None
    p = (
        await session.execute(
            select(OrganizarPrevia).where(
                OrganizarPrevia.token == tok,
                OrganizarPrevia.criado_por == user.id,
                OrganizarPrevia.expira_em > func.now(),
            )
        )
    ).scalar_one_or_none()
    if p is None:
        raise OrganizarError(409, "previa_expirada")
    return p


async def itens(
    session: AsyncSession,
    chave: str,
    ocultos: bool,
    pagina: int,
    *,
    token: str | None = None,
    user: User | None = None,
) -> dict:
    """Uma página da lista da chave. Com `token` (e ocultos=false), o "Ver
    lista" mostra exatamente o que a prévia guardou e ainda vale — o total bate
    com o botão "Ocultar N" e o que entrou depois fica para a próxima prévia.
    Sem token, o conjunto de agora."""
    m = validar_chave(chave)
    pagina = max(1, int(pagina or 1))
    ids_previa = None
    if token and not ocultos and user is not None:
        p = await _previa_do_token(session, user, token)
        ids_previa = [uuid.UUID(x) for x in (p.ids or {}).get(chave) or []]
    q, ordem, item = _consulta_itens(m, ocultos, ids_previa)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    pagina_q = q.order_by(*ordem).offset((pagina - 1) * POR_PAGINA).limit(POR_PAGINA)
    linhas = (await session.execute(pagina_q)).all()
    saida = []
    for r in linhas:
        d = item(r)
        if ocultos:
            d = {"marcador_id": str(r.marcador_id), **d, "oculto_em": r.oculto_em}
        d["registro_id"] = str(d["registro_id"])
        for k in ("data", "oculto_em"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
        saida.append(d)
    return {"itens": saida, "total": total, "pagina": pagina, "por_pagina": POR_PAGINA}


# ── Aplicar e mostrar de novo ────────────────────────────────────────────────


def _chaves_validas(chaves: list[str] | None) -> list[str]:
    vistas: list[str] = []
    for c in chaves or []:
        validar_chave(c)
        if c not in vistas:
            vistas.append(c)
    if not vistas:
        raise OrganizarError(400, "sem_chaves")
    return vistas


async def aplicar(session: AsyncSession, user: User, token: str, chaves: list[str]) -> dict:
    """Oculta os itens que a prévia `token` mostrou nas `chaves` escolhidas e
    que AINDA valem (continuam inválidos e aparecendo). Os que saíram da lista
    desde a prévia ficam de fora (contados em `sairam_desde_previa`) e os novos
    ficam para a próxima prévia: nunca se oculta o que não foi mostrado. Não
    faz commit (quem chama faz — a trava 'orgz' vale até o commit)."""
    chaves = _chaves_validas(chaves)
    livre = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": ORGANIZAR_LOCK_KEY},
        )
    ).scalar()
    if not livre:
        raise OrganizarError(409, "organizar_em_andamento")
    p = await _previa_do_token(session, user, token)
    await _fechar_vencidas(session)

    tabelas: list[str] = []
    ids: list[uuid.UUID] = []
    motivos: list[str] = []
    mostrados_total = 0
    for chave in chaves:
        m = MOTIVOS[chave]
        mostrados = [uuid.UUID(x) for x in (p.ids or {}).get(chave) or []]
        mostrados_total += len(mostrados)
        if not mostrados:
            continue
        ent = _ENTIDADE[m.tabela]
        validos = (
            await session.execute(
                select(ent.id).where(ent.id == any_(_uuids(mostrados)), *_onde_candidato(m))
            )
        ).scalars().all()
        tabelas += [m.tabela] * len(validos)
        ids += list(validos)
        motivos += [chave] * len(validos)

    por_chave = dict.fromkeys(chaves, 0)
    lote = uuid.uuid4()
    if ids:
        tabela_sql = _tabela_sql()
        # Item que ainda aparece mas tem marca ATIVA = marca vencida (o motivo
        # dela deixou de valer, ex.: excluído sem anúncio que ganhou anúncio).
        # Ela é fechada (sem autor: o item já tinha voltado sozinho) para a
        # marca nova caber no índice único parcial.
        for tabela in sorted(set(tabelas)):
            await session.execute(
                text(
                    f"UPDATE {tabela_sql} SET reexibido_em = now() "  # noqa: S608
                    "WHERE reexibido_em IS NULL AND tabela = :t "
                    "AND registro_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"t": tabela, "ids": [i for i, t in zip(ids, tabelas, strict=True) if t == tabela]},
            )
        res = await session.execute(
            text(
                f"INSERT INTO {tabela_sql} "  # noqa: S608
                "(tabela, registro_id, motivo, lote_id, oculto_por) "
                "SELECT x.tabela, x.registro_id, x.motivo, :lote, :quem "
                "FROM unnest(CAST(:tabelas AS text[]), CAST(:ids AS uuid[]), "
                "CAST(:motivos AS text[])) AS x(tabela, registro_id, motivo) "
                "ON CONFLICT (tabela, registro_id) WHERE reexibido_em IS NULL DO NOTHING "
                "RETURNING motivo"
            ),
            {
                "tabelas": tabelas,
                "ids": ids,
                "motivos": motivos,
                "lote": lote,
                "quem": user.id,
            },
        )
        for (motivo,) in res.all():
            por_chave[motivo] = por_chave.get(motivo, 0) + 1
    ocultados = sum(por_chave.values())
    return {
        "lote_id": str(lote) if ocultados else None,
        "ocultados": ocultados,
        "por_chave": por_chave,
        "sairam_desde_previa": mostrados_total - len(ids),
    }


async def reexibir(
    session: AsyncSession,
    user: User,
    *,
    lote_id: uuid.UUID | None = None,
    marcador_ids: list[uuid.UUID] | None = None,
    chave: str | None = None,
) -> dict:
    """"Mostrar de novo": fecha as marcas ativas do lote, dos itens ou da chave
    (os filtros dados valem juntos). Nada é apagado: a marca fica com quem e
    quando mostrou de novo. Sem filtro nenhum → 400. Não faz commit."""
    if lote_id is None and not marcador_ids and not chave:
        raise OrganizarError(400, "sem_filtro")
    if chave:
        validar_chave(chave)
    # As vencidas (o item já tinha voltado sozinho) fecham antes e sem autor:
    # o "N voltaram para a tela" conta só o que estava escondido de verdade.
    await _fechar_vencidas(session)
    filtros = [OrganizarOculto.reexibido_em.is_(None)]
    if lote_id is not None:
        filtros.append(OrganizarOculto.lote_id == lote_id)
    if marcador_ids:
        filtros.append(OrganizarOculto.id == any_(_uuids(marcador_ids)))
    if chave:
        filtros.append(OrganizarOculto.motivo == chave)
    res = await session.execute(
        update(OrganizarOculto)
        .where(*filtros)
        .values(reexibido_em=func.now(), reexibido_por=user.id)
        .execution_options(synchronize_session=False)
    )
    return {"reexibidos": res.rowcount or 0}
