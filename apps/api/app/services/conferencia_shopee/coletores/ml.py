"""Coletor do Mercado Livre da Conferência (07/10/2026) — no servidor, sem AdsPower.

Interface: coletores/__init__.py (`collect(conta, semanas, *, session)` →
ColetaDados versao 1). Por loja:

VENDAS — itens dos pedidos do Bling da loja (coletores/vendas_bling.py): pela
data do pedido, valor de tabela (sem frete e sem os descontos do pedido), sem os
cancelados na hora da coleta; eletro pelo SKU. Semana em que o zero não seria
confiável (espelho do Bling parado, loja que o espelho não conhece) vem sem
Vendas ("—", coleta `parcial`) e com aviso.

ADS — API de Anúncios do ML (Product Ads), pelo cliente consertado em 07/10/2026
(services/ml_ads.py: endereços documentados, api-version 2, resposta paginada
só COMPLETA, erro nunca vira zero):
  • Totais da semana: `fetch_daily_metrics` (campaigns/search com
    aggregation_type=DAILY, de S4.inicio a S1.fim — o ML guarda 90 dias) e a
    soma dos dias de cada semana. Vendas Ads = `total_amount` (venda direta +
    indireta), Impressões = `prints`, Cliques = `clicks`, Invest. Ads = `cost`,
    Pedidos Ads = `units_quantity`. A Conversão Ads (pedidos ÷ cliques) sai no
    cálculo.
  • Semana com um dia JÁ FECHADO que o ML não trouxe: o Ads da semana fica sem
    número (aviso). O ML manda todo dia, até os zerados (simulação de
    07/10/2026: 30 de 30 dias em todas as contas) — dia faltando é resposta
    incompleta, e somar sem ele daria um número menor que o real.
  • Eletro do Ads (só contas de Celular), anúncio a anúncio: as métricas por
    "ad group" de cada semana (`ad_groups/search`, uma busca por semana). O
    grupo ITEM é o próprio anúncio (`ad_group_external_id` = MLB…); FAMILY
    (User Products) e CATALOG juntam variações — os itens deles vêm de
    `ad_groups/{id}/ads` (uma chamada por grupo com movimento nas 4 semanas).
    Item → SKU pelo vínculo VIVO do DaVinci (`product_links`, plataforma ml;
    o da própria integração primeiro, depois o de outra; vínculo morto não
    conta, como na Shopee). O grupo
    é eletro se QUALQUER item dele for eletro (a regra da Shopee); a linha do
    grupo leva o item/SKU que decidiu. Grupo sem a lista de itens (falha,
    tempo) ou sem vínculo: eletro pelo título do anúncio.
  • Falhou a busca por anúncio de uma semana: a semana fica SEM `ads_itens` e
    o cálculo põe o Ads inteiro dela em Celular, com aviso (decisão do dono).
  • Conta sem Publicidade / token sem o escopo / API fora: `ads` fica de fora
    (o relatório mostra "—", a coleta vira `parcial`) e o motivo vai nos
    avisos. Nunca zero no lugar de erro.

Token: o cliente é o mesmo do Marketing (`ml_sync._client`): o refresh_token
renovado se grava numa sessão própria NA HORA (uso único no ML), então o
rollback de uma coleta que falhou não o desfaz.

Afiliados ("Venda com Afiliados"): sem API — não se manda nada; a célula sai
"não coletado" pelo perfil da plataforma (plataformas.ESTADOS). Saldo Ads não
existe no ML ("não se aplica").

Tempo: a loja tem 10 min no job (servidor.LIMITE_POR_CONTA_S). A busca dos itens
dos grupos para em PRAZO_ITENS_S (o que faltou fica pelo título, com aviso),
para Vendas e totais de Ads não se perderem por causa do detalhe.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.services.conferencia_shopee import calculo, classificacao
from app.services.conferencia_shopee.coletores import (
    ColetaDados,
    ColetorError,
    ContaColeta,
    Semana,
    vendas_bling,
)
from app.services.conferencia_shopee.periodos import rotulo
from app.services.ml_ads import (
    ML_ADS_MAX_DIAS,
    ML_SITE_ID_BR,
    MLAdsClient,
    MLAdsError,
    MLAdsScopeError,
    MLAdsSemPermissaoError,
    MLDailyMetric,
    _metricas_da_linha,
    _num,
    hoje_brt,
)

logger = structlog.get_logger()

# Depois disto (desde o começo da coleta da loja) nenhum `ad_groups/{id}/ads`
# novo é pedido: os grupos que faltaram ficam pelo título.
PRAZO_ITENS_S = 360.0
# Tantas falhas seguidas no `ad_groups/{id}/ads` = a API está fora: para de pedir.
_MAX_FALHAS_SEGUIDAS = 5
# Métricas do `ad_groups/search` como a doc do ML escreve (maiúsculas); se a
# resposta vier sem elas, tenta de novo em minúsculas (como o campaigns/search).
METRICAS_GRUPO = "CLICKS,PRINTS,COST,TOTAL_AMOUNT,UNITS_QUANTITY"
METRICAS_ANUNCIOS = "clicks,prints,cost,total_amount,units_quantity"
_IN_LOTE = 500

SEM_LOJA = "Vendas: a conta não tem loja do Bling ligada — Vendas não coletadas"
SEM_INTEGRACAO = "Ads: a conta não tem integração do Mercado Livre ligada — Ads não coletados"


def criar_cliente(session: AsyncSession, integration: Integration) -> MLAdsClient:
    """O cliente de Ads da integração, com o token renovado gravado na hora
    (o mesmo do Marketing). A simulação em produção troca esta função por um
    cliente que não renova token."""
    # Import tardio: ml_sync puxa o módulo de Marketing inteiro.
    from app.services.marketing import ml_sync

    return ml_sync._client(session, integration, simular=False)


def _hoje() -> date:
    return hoje_brt()


# ───────────────────────────────────────────────────────────── coleta


async def collect(
    conta: ContaColeta, semanas: Sequence[Semana], *, session: AsyncSession
) -> ColetaDados:
    t0 = time.monotonic()
    avisos: list[str] = []
    saida: dict[Semana, dict[str, Any]] = {s: {**s.chave(), "avisos": []} for s in semanas}

    tem_vendas = False
    if conta.bling_loja_id:
        vendas = await vendas_bling.vendas_por_semana(
            session,
            conta.bling_loja_id,
            semanas,
            marcar_eletro=conta.grupo == "celular",
            avisos_loja=avisos,
        )
        for s, v in vendas.items():
            # Semana sem "vendas" = o zero não seria confiável (espelho do Bling
            # parado, loja desconhecida): fica "—" e a coleta, `parcial`.
            if "vendas" in v:
                saida[s]["vendas"] = v["vendas"]
                saida[s]["vendas_itens"] = v["vendas_itens"]
                tem_vendas = True
            saida[s]["avisos"].extend(v["avisos"])
    else:
        avisos.append(SEM_LOJA)

    contador = [0]
    tem_ads = False
    if conta.integration_id is None:
        avisos.append(SEM_INTEGRACAO)
    else:
        tem_ads = await _coletar_ads(
            session, conta, semanas, saida, avisos, contador, prazo=t0 + PRAZO_ITENS_S
        )

    if not tem_vendas and not tem_ads:
        raise ColetorError("; ".join(avisos) or "nada coletado")
    duracao = round(time.monotonic() - t0, 1)
    logger.info(
        "conferencia_ml_conta",
        conta=conta.nome,
        chamadas=contador[0],
        duracao_s=duracao,
        vendas=tem_vendas,
        ads=tem_ads,
        avisos=len(avisos),
    )
    return {
        "versao": 1,
        "duracao_s": duracao,
        "chamadas": contador[0],
        "semanas": [saida[s] for s in semanas],
        "avisos": avisos,
    }


def _contar_chamadas(client: MLAdsClient, contador: list[int]) -> None:
    """Conta as chamadas à API (vão no `chamadas` da coleta)."""
    original = client._ads_request

    async def contado(*args: Any, **kwargs: Any) -> dict:
        contador[0] += 1
        return await original(*args, **kwargs)

    client._ads_request = contado  # type: ignore[method-assign]


def _motivo(e: BaseException) -> str:
    """O porquê da falha em poucas palavras, sem corpo de resposta (token) e
    sem SQL."""
    if isinstance(e, MLAdsError):
        return f"{e.status} {e.code}"
    if isinstance(e, httpx.HTTPError):
        return f"rede: {type(e).__name__}"
    if isinstance(e, SQLAlchemyError):
        return f"banco: {type(e).__name__}"
    texto = str(e)
    if "ml_refresh_failed" in texto or "refresh_token" in texto or "client_secret" in texto:
        return "o Mercado Livre recusou a renovação do token — reconectar a conta"
    return f"{type(e).__name__}: {texto[:120]}"


def _dias(s: Semana) -> list[date]:
    return [s.inicio + timedelta(days=i) for i in range((s.fim - s.inicio).days + 1)]


def _lista_dias(dias: Sequence[date]) -> str:
    return ", ".join(f"{d:%d/%m}" for d in dias)


def _soma_semana(dias: Sequence[MLDailyMetric]) -> dict[str, float | int]:
    return {
        "impressoes": sum(d.impressions for d in dias),
        "cliques": sum(d.clicks for d in dias),
        "gasto": round(sum(d.spend for d in dias), 2),
        "vendas": round(sum(d.revenue for d in dias), 2),
        "pedidos": sum(d.units for d in dias),
    }


async def _coletar_ads(
    session: AsyncSession,
    conta: ContaColeta,
    semanas: Sequence[Semana],
    saida: dict[Semana, dict[str, Any]],
    avisos: list[str],
    contador: list[int],
    *,
    prazo: float,
) -> bool:
    """Põe `ads` (e, no Celular, `ads_itens`) nas semanas. True se ao menos
    uma semana ficou com o Ads."""
    integ = await session.get(Integration, conta.integration_id)
    if integ is None:
        avisos.append("Ads: a integração ligada a esta conta não existe mais — Ads não coletados")
        return False
    if integ.platform != IntegrationPlatform.ML:
        avisos.append("Ads: a integração ligada não é do Mercado Livre — Ads não coletados")
        return False
    if integ.archived_at is not None:
        avisos.append("Ads: a integração está arquivada (Lojas) — Ads não coletados")
        return False
    try:
        client = criar_cliente(session, integ)
    except Exception as e:  # noqa: BLE001 — credencial ilegível: só o Ads cai
        avisos.append(
            f"Ads: credencial da integração ilegível ({type(e).__name__}) — Ads não coletados"
        )
        return False
    _contar_chamadas(client, contador)

    hoje = _hoje()
    limite = hoje - timedelta(days=ML_ADS_MAX_DIAS - 1)
    dentro = [s for s in semanas if s.inicio >= limite]
    for s in semanas:
        if s not in dentro:
            saida[s]["avisos"].append(
                "Ads: semana fora dos 90 dias que o Mercado Livre guarda — sem Ads"
            )
    if not dentro:
        return False

    try:
        diario = await client.fetch_daily_metrics(
            date_from=min(s.inicio for s in dentro), date_to=max(s.fim for s in dentro), hoje=hoje
        )
    except (MLAdsScopeError, MLAdsSemPermissaoError) as e:
        avisos.append(
            f"Ads: a conta não tem permissão de Publicidade no Mercado Livre ({e.code}) — Ads não "
            "coletados"
        )
        return False
    except (MLAdsError, httpx.HTTPError, RuntimeError, ValueError) as e:
        logger.warning("conferencia_ml_ads_falhou", conta=conta.nome, erro=_motivo(e))
        avisos.append(
            f"Ads: a API de Anúncios do Mercado Livre falhou ({_motivo(e)}) — Ads não coletados"
        )
        return False

    com_ads: list[Semana] = []
    for s in dentro:
        faltam = [d for d in _dias(s) if d < hoje and d not in diario]
        if faltam:
            saida[s]["avisos"].append(
                f"Ads: o Mercado Livre não trouxe {_lista_dias(faltam)} — Ads da semana sem número"
            )
            continue
        saida[s]["ads"] = _soma_semana([diario[d] for d in _dias(s) if d in diario])
        com_ads.append(s)
    if not com_ads:
        return False

    if conta.grupo == "celular":
        try:
            itens = await _ads_por_item(client, session, conta, com_ads, saida, avisos, prazo)
        except Exception as e:  # noqa: BLE001 — o detalhe não derruba os totais
            logger.exception("conferencia_ml_ads_itens_falhou", conta=conta.nome)
            avisos.append(
                f"Ads por anúncio: falhou ({_motivo(e)}) — o Ads inteiro ficou em Celular"
            )
        else:
            for s, lista in itens.items():
                saida[s]["ads_itens"] = lista
    return True


# ───────────────────────────────────────────────────────────── por anúncio


@dataclass
class _Grupo:
    """Um ad group do ML e as métricas dele em cada semana."""

    id: str
    tipo: str
    externo: str
    titulo: str | None
    por_semana: dict[Semana, dict[str, float | int]] = field(default_factory=dict)


def _metricas_grupo(row: Any, path: str) -> tuple[str, str, str, str | None, dict]:
    """(id, tipo, externo, título, métricas) de uma linha do ad_groups/search.
    Linha sem id ou sem cost/prints/clicks = resposta fora do formato → erro
    (nunca vira zero)."""
    if not isinstance(row, dict):
        raise MLAdsError(502, "resposta_inesperada", f"linha não é objeto: {row!r}"[:200], path)
    gid = row.get("id") if row.get("id") is not None else row.get("ad_group_id")
    if gid is None:
        raise MLAdsError(502, "resposta_inesperada", "grupo de anúncios sem 'id'", path)
    m = _metricas_da_linha(row, path)
    assert m is not None  # sem tolerar_nulo: métrica faltando já levantou
    metricas = {
        "impressoes": int(_num(m, "prints", path)),
        "cliques": int(_num(m, "clicks", path)),
        "gasto": round(_num(m, "cost", path), 2),
        "vendas": round(_num(m, "total_amount", path), 2),
        "pedidos": int(_num(m, "units_quantity", path)),
    }
    tipo = str(row.get("ad_group_type") or "ITEM").upper()
    externo = str(row.get("ad_group_external_id") or "").strip()
    titulo = row.get("title")
    return str(gid), tipo, externo, (str(titulo) if titulo else None), metricas


def _tem_movimento(m: dict[str, float | int]) -> bool:
    return any(v for v in m.values())


async def _buscar_grupos(
    client: MLAdsClient, path: str, s: Semana, metricas: str
) -> tuple[list[tuple[str, str, str, str | None, dict]], str]:
    """Os grupos da semana `s` (todas as páginas) e as métricas que valeram."""
    params = {"date_from": s.inicio.isoformat(), "date_to": s.fim.isoformat()}
    rows = await client._buscar_todas(path, {**params, "metrics": metricas})
    try:
        return [_metricas_grupo(r, path) for r in rows], metricas
    except MLAdsError as e:
        if e.code != "metricas_ausentes" or metricas != METRICAS_GRUPO:
            raise
    minusc = METRICAS_GRUPO.lower()
    rows = await client._buscar_todas(path, {**params, "metrics": minusc})
    return [_metricas_grupo(r, path) for r in rows], minusc


async def _itens_de_grupo(
    client: MLAdsClient, grupo: _Grupo, inicio: date, fim: date
) -> list[tuple[str, str | None]]:
    """[(item_id, título)] dos anúncios de um grupo FAMILY/CATALOG."""
    path = f"/advertising/{ML_SITE_ID_BR}/product_ads/ad_groups/{grupo.id}/ads"
    rows = await client._buscar_todas(
        path,
        {"date_from": inicio.isoformat(), "date_to": fim.isoformat(),
         "metrics": METRICAS_ANUNCIOS},
    )
    itens: list[tuple[str, str | None]] = []
    for r in rows:
        if isinstance(r, dict) and r.get("item_id"):
            titulo = r.get("title")
            itens.append((str(r["item_id"]).strip(), str(titulo) if titulo else None))
    return itens


async def _skus_dos_itens(
    session: AsyncSession, integration_id: Any, itens: set[str]
) -> dict[str, tuple[str, bool]]:
    """item MLB → (SKU do DaVinci, eletro?) pelo vínculo VIVO (`product_links`,
    plataforma ml, `morto_desde` vazio — a regra da Shopee: vínculo morto não
    conta, e o anúncio sem vínculo vivo fica pelo título). Preferência: o da
    integração da conta, depois o de outra integração. Item com variações de
    SKUs diferentes: o SKU eletro, se houver (qualquer variação eletro =
    eletro)."""
    if not itens:
        return {}
    lista = sorted(itens)
    linhas: list[Any] = []
    for i in range(0, len(lista), _IN_LOTE):
        linhas.extend(
            (
                await session.execute(
                    select(
                        ProductLink.external_id,
                        ProductLink.integration_id,
                        Product.sku,
                    )
                    .join(Product, Product.id == ProductLink.product_id)
                    .where(
                        ProductLink.platform == IntegrationPlatform.ML,
                        ProductLink.morto_desde.is_(None),
                        ProductLink.external_id.in_(lista[i : i + _IN_LOTE]),
                    )
                )
            ).all()
        )
    candidatos: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for item, integracao, sku in linhas:
        sku = (sku or "").strip()
        if not sku:
            continue
        nivel = 0 if integracao == integration_id else 1
        candidatos[str(item)][nivel].add(sku)
    eletro = await classificacao.eletro_por_sku(
        session, (s for niveis in candidatos.values() for skus in niveis.values() for s in skus)
    )
    saida: dict[str, tuple[str, bool]] = {}
    for item, niveis in candidatos.items():
        melhores = sorted(niveis[min(niveis)])
        de_eletro = [s for s in melhores if eletro.get(s.lower(), False)]
        saida[item] = (de_eletro[0], True) if de_eletro else (melhores[0], False)
    return saida


def _representante(
    grupo: _Grupo,
    itens: list[tuple[str, str | None]] | None,
    sku_do_item: dict[str, tuple[str, bool]],
) -> tuple[str, str | None, bool | None]:
    """(item_id, SKU, eletro?) da linha do grupo. Grupo sem a lista de itens:
    `<tipo>:<id externo>` sem SKU (o cálculo decide pelo título)."""
    if itens is None:
        return f"{grupo.tipo.lower()}:{grupo.externo or grupo.id}", None, None
    ordenados = sorted({item for item, _ in itens})
    com_sku = [(item, sku_do_item[item]) for item in ordenados if item in sku_do_item]
    for item, (sku, eletro) in com_sku:
        if eletro:
            return item, sku, True
    if com_sku:
        item, (sku, _) = com_sku[0]
        return item, sku, False
    return ordenados[0], None, None


async def _ads_por_item(
    client: MLAdsClient,
    session: AsyncSession,
    conta: ContaColeta,
    semanas: Sequence[Semana],
    saida: dict[Semana, dict[str, Any]],
    avisos: list[str],
    prazo: float,
) -> dict[Semana, list[dict[str, Any]]]:
    """`ads_itens` de cada semana que deu certo (as outras ficam de fora, com
    aviso: o cálculo põe o Ads inteiro delas em Celular)."""
    adv = await client.get_advertiser_id()
    path = f"/advertising/{ML_SITE_ID_BR}/advertisers/{adv}/product_ads/ad_groups/search"
    grupos: dict[str, _Grupo] = {}
    ok: list[Semana] = []
    metricas = METRICAS_GRUPO
    for s in semanas:
        if time.monotonic() > prazo:
            saida[s]["avisos"].append(
                "Ads por anúncio: sem tempo para buscar os anúncios — o Ads da semana ficou "
                "inteiro em Celular"
            )
            continue
        try:
            linhas, metricas = await _buscar_grupos(client, path, s, metricas)
        except (MLAdsError, httpx.HTTPError) as e:
            saida[s]["avisos"].append(
                f"Ads por anúncio: a busca dos anúncios falhou ({_motivo(e)}) — o Ads da semana "
                "ficou inteiro em Celular"
            )
            continue
        for gid, tipo, externo, titulo, m in linhas:
            g = grupos.setdefault(gid, _Grupo(gid, tipo, externo, titulo))
            if g.titulo is None and titulo:
                g.titulo = titulo
            atual = g.por_semana.get(s)
            g.por_semana[s] = m if atual is None else {k: atual[k] + m[k] for k in m}
        ok.append(s)
    if not ok:
        return {}

    ativos = [g for g in grupos.values() if any(_tem_movimento(m) for m in g.por_semana.values())]
    itens_do_grupo: dict[str, list[tuple[str, str | None]]] = {}
    sem_lista = 0
    falhas_seguidas = 0
    inicio, fim = min(s.inicio for s in ok), max(s.fim for s in ok)
    for g in sorted(ativos, key=lambda x: x.id):
        if g.tipo == "ITEM" and g.externo:
            itens_do_grupo[g.id] = [(g.externo, g.titulo)]
            continue
        if falhas_seguidas >= _MAX_FALHAS_SEGUIDAS or time.monotonic() > prazo:
            sem_lista += 1
            continue
        try:
            itens = await _itens_de_grupo(client, g, inicio, fim)
        except (MLAdsError, httpx.HTTPError) as e:
            falhas_seguidas += 1
            sem_lista += 1
            logger.warning(
                "conferencia_ml_grupo_sem_itens", conta=conta.nome, grupo=g.id, erro=_motivo(e)
            )
            continue
        falhas_seguidas = 0
        if itens:
            itens_do_grupo[g.id] = itens
        else:
            sem_lista += 1
    if sem_lista:
        avisos.append(
            f"Ads por anúncio: {sem_lista} anúncio(s) com variações ou de catálogo sem a lista de "
            "itens — o eletro deles saiu pelo título"
        )

    # Num SAVEPOINT: erro de banco aqui só derruba o detalhe (o Ads fica em
    # Celular, com aviso); a sessão da loja continua boa para o resto.
    async with session.begin_nested():
        sku_do_item = await _skus_dos_itens(
            session,
            conta.integration_id,
            {item for lista in itens_do_grupo.values() for item, _ in lista},
        )
    resultado: dict[Semana, list[dict[str, Any]]] = {}
    for s in ok:
        lista: list[dict[str, Any]] = []
        sem_vinculo = 0
        gasto_sem_vinculo = 0.0
        for g in sorted(grupos.values(), key=lambda x: x.id):
            da_semana = g.por_semana.get(s)
            if not da_semana or not _tem_movimento(da_semana):
                continue
            item_id, sku, eletro = _representante(g, itens_do_grupo.get(g.id), sku_do_item)
            linha: dict[str, Any] = {
                "item_id": item_id,
                "nome": g.titulo,
                "tipo": g.tipo,
                "ad_group_id": g.id,
                **da_semana,
            }
            if sku:
                linha["sku"] = sku
                linha["eletro"] = bool(eletro)
            else:
                sem_vinculo += 1
                gasto_sem_vinculo += float(da_semana["gasto"])
            lista.append(linha)
        resultado[s] = lista
        total = (saida[s].get("ads") or {}).get("gasto")
        soma = round(sum(float(it["gasto"]) for it in lista), 2)
        if total is not None and abs(soma - total) > max(1.0, 0.01 * max(soma, total)):
            saida[s]["avisos"].append(
                f"Ads por anúncio: a soma dos anúncios ({calculo.dinheiro(soma)}) não fecha com o "
                f"total da conta ({calculo.dinheiro(total)})"
            )
        if s == semanas[0] and sem_vinculo:
            avisos.append(
                f"Ads por anúncio: {sem_vinculo} anúncio(s) sem vínculo no DaVinci em "
                f"{rotulo(s.inicio, s.fim)} ({calculo.dinheiro(gasto_sem_vinculo)} de Ads) — "
                "o eletro deles saiu pelo título"
            )
    return resultado
