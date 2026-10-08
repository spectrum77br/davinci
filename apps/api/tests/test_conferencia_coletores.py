"""Conferência — os coletores do SERVIDOR (Mercado Livre e Amazon), 07/10/2026.

O que fica travado:
  • Vendas (ML e Amazon): itens dos pedidos do Bling da loja na semana, pelo
    dia do pedido em Brasília, valor dos produtos SEM frete (itemvalor ×
    quantidade), sem os cancelados (12 e `excluido`) — "Em digitação" e
    situação vazia entram; linha sem SKU fica pelo nome; marca de eletro pelo
    SKU do DaVinci; semana sem pedido = R$ 0,00 de verdade — menos quando o
    espelho do Bling parou (semana depois do pedido mais novo de qualquer
    loja) ou a loja nunca teve pedido (loja errada em Contas): aí "—" com
    aviso; loja conhecida sem pedido nas 4 semanas e S1 zerada avisam;
  • Ads do ML: totais da semana pela soma dos dias (DAILY), Pedidos Ads =
    unidades; dia fechado faltando → semana sem Ads (nunca soma menor);
    sem permissão / API fora / token recusado → `ads` de fora + aviso, nunca
    zero; fora dos 90 dias → nem chama;
  • eletro do Ads anúncio a anúncio: grupo ITEM pelo MLB, FAMILY pelos itens
    de `ad_groups/{id}/ads` (qualquer item eletro = eletro), vínculo vivo da
    integração primeiro, vínculo morto não conta (a regra da Shopee); grupo
    sem lista ou sem vínculo vivo → título; busca de
    uma semana que falhou → só aquela semana sem `ads_itens` (Ads inteiro em
    Celular, com aviso); métricas em maiúsculas ignoradas → tenta minúsculas;
    sem tempo → sem os itens, totais intactos; conta de Mala nem busca;
  • ponta a ponta pelo job do servidor (`servidor.coletar_execucao`, coletor
    de verdade): coleta `ok`/`parcial`, relatório com Eletro separado, Amazon
    com Ads "aguardando acesso".

Rede: httpx.MockTransport com respostas no formato da doc oficial do ML
(developers.mercadolivre.com.br/pt_br/product-ads-para-catalogo-e-user-products-leitura)
e as formas da simulação em produção de 07/10/2026; valores INVENTADOS.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.marketing.ml_sync as ml_sync_mod
import app.services.ml_ads as ml_ads_mod
from app.models import (
    BlingOrder,
    ConferenciaShopeeConta,
    Integration,
    IntegrationPlatform,
    Product,
    ProductCategory,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.services.conferencia_shopee import (
    calculo,
    classificacao,
    coletores,
    dados,
    fila,
    periodos,
    servidor,
)
from app.services.conferencia_shopee.coletores import amazon as coletor_amazon
from app.services.conferencia_shopee.coletores import ml as coletor_ml
from app.services.conferencia_shopee.coletores import vendas_bling
from tests.test_conferencia_shopee_fila import AGORA, coletas_de

HOJE = date(2026, 10, 6)  # terça; AGORA = 13:30 BRT
SEMANAS = coletores.semanas_do_job(periodos.semanas("semanal", HOJE))
S1, S2, S3, S4 = SEMANAS
LOJA_KIA = "204713113"
LOJA_KFA = "204438129"
ADV = 900001
_AsyncClientReal = httpx.AsyncClient


def _dia_brt(d: date, hora: int = 0, minuto: int = 0) -> datetime:
    """Meia-noite (ou a hora pedida) de Brasília em UTC (UTC−3)."""
    return datetime(d.year, d.month, d.day, hora, minuto, tzinfo=periodos.FUSO).astimezone(UTC)


# ───────────────────────────────────────────────────────────── rede falsa


def _pagina(request: httpx.Request, linhas: list[dict]) -> dict:
    q = request.url.params
    offset, limit = int(q.get("offset", 0)), int(q.get("limit", 50))
    return {
        "paging": {"offset": offset, "total": len(linhas), "limit": limit},
        "results": linhas[offset : offset + limit],
    }


def _metr(prints: int, clicks: int, cost: float, total: float, units: int) -> dict:
    return {
        "clicks": clicks,
        "prints": prints,
        "cost": cost,
        "cpc": round(cost / clicks, 2) if clicks else 0.0,
        "direct_amount": total,
        "indirect_amount": 0.0,
        "total_amount": total,
        "direct_units_quantity": units,
        "indirect_units_quantity": 0,
        "units_quantity": units,
    }


def _grupo(gid: int, tipo: str, externo: str, titulo: str, metricas: dict) -> dict:
    return {
        "channel": "MARKETPLACE",
        "title": titulo,
        "advertiser_id": ADV,
        "ad_group_type": tipo,
        "id": gid,
        "campaign_id": 355000001,
        "ad_group_external_id": externo,
        "status": "ACTIVE",
        "metrics": metricas,
    }


# Uma semana inteira de Ads da conta: 7 dias × (1.000 impressões, 20 cliques,
# R$ 10, R$ 100 de vendas, 1 unidade) = 7.000 / 140 / R$ 70 / R$ 700 / 7.
GRUPOS_SEMANA = [
    _grupo(11, "ITEM", "MLB1", "Air Fryer 4L Digital", _metr(2000, 40, 20.0, 200.0, 2)),
    _grupo(12, "ITEM", "MLB2", "Capinha iPhone 15", _metr(3000, 50, 25.0, 300.0, 3)),
    _grupo(13, "FAMILY", "777001", "Kit Cozinha", _metr(1000, 20, 10.0, 100.0, 1)),
    _grupo(14, "FAMILY", "777002", "Fritadeira Air Fryer Família", _metr(500, 10, 5.0, 50.0, 0)),
    _grupo(15, "ITEM", "MLB5", "Película de vidro", _metr(500, 20, 10.0, 50.0, 1)),
    _grupo(16, "ITEM", "MLB6", "Parado", _metr(0, 0, 0.0, 0.0, 0)),
]


class FakeML:
    """A API de Ads do ML de mentira: anunciante, campaigns/search DAILY,
    ad_groups/search (por semana) e ad_groups/{id}/ads."""

    def __init__(self) -> None:
        self.anunciante: tuple[int, dict] = (
            200,
            {"advertisers": [{"advertiser_id": ADV, "site_id": "MLB",
                              "advertiser_name": "Kia", "account_name": "MLB - KIA"}]},
        )
        self.diario: dict[str, dict] = {}
        d = S4.inicio
        while d <= S1.fim:
            self.diario[d.isoformat()] = {"date": d.isoformat(), **_metr(1000, 20, 10.0, 100.0, 1)}
            d += timedelta(days=1)
        self.diario_erro: tuple[int, dict] | None = None
        self.grupos: dict[str, list[dict]] = {s.inicio.isoformat(): GRUPOS_SEMANA for s in SEMANAS}
        self.grupos_erro: dict[str, tuple[int, dict]] = {}
        self.maiusculas_ignoradas = False
        self.anuncios: dict[str, Any] = {
            "13": [{"item_id": "MLB3", "title": "Kit Cozinha Azul"},
                   {"item_id": "MLB4", "title": "Kit Cozinha Panela Elétrica"}],
            "14": (500, {"message": "internal error", "error": "internal_error"}),
        }
        self.token: tuple[int, dict] = (500, {"error": "refresh nao deveria ser chamado"})
        self.feitos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.feitos.append(request)
        path, q = request.url.path, request.url.params
        if path == "/advertising/advertisers":
            st, body = self.anunciante
        elif path == f"/advertising/MLB/advertisers/{ADV}/product_ads/campaigns/search":
            if self.diario_erro is not None:
                st, body = self.diario_erro
            else:
                ini, fim = q["date_from"], q["date_to"]
                linhas = [r for dia, r in sorted(self.diario.items()) if ini <= dia <= fim]
                st, body = 200, _pagina(request, linhas)
        elif path == f"/advertising/MLB/advertisers/{ADV}/product_ads/ad_groups/search":
            semana = q["date_from"]
            if semana in self.grupos_erro:
                st, body = self.grupos_erro[semana]
            else:
                linhas = self.grupos.get(semana, [])
                if self.maiusculas_ignoradas and q["metrics"].isupper():
                    linhas = [{k: v for k, v in g.items() if k != "metrics"} for g in linhas]
                st, body = 200, _pagina(request, linhas)
        elif m := re.fullmatch(r"/advertising/MLB/product_ads/ad_groups/(\d+)/ads", path):
            rota = self.anuncios.get(m.group(1), [])
            if isinstance(rota, tuple):
                st, body = rota
            else:
                linhas = [{"campaign_id": 355000001, "ad_group_id": int(m.group(1)), **r,
                           "metrics": _metr(1, 0, 0.0, 0.0, 0)} for r in rota]
                st, body = 200, _pagina(request, linhas)
        elif path == "/oauth/token":
            st, body = self.token
        else:
            st, body = 404, {"message": "resource not found", "error": "not_found", "status": 404}
        return httpx.Response(st, json=body)

    def chamadas(self, trecho: str) -> list[httpx.Request]:
        return [r for r in self.feitos if trecho in r.url.path]


@pytest.fixture
def fake_ml(monkeypatch) -> FakeML:
    fake = FakeML()

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    monkeypatch.setattr(ml_ads_mod, "_RATE_LIMIT_SECONDS", 0)
    monkeypatch.setattr(coletor_ml, "_hoje", lambda: HOJE)
    futuro = int((AGORA + timedelta(days=365)).timestamp())
    fake.creds = {"access_token": "tok", "refresh_token": "rt", "expires_at": futuro}
    monkeypatch.setattr(ml_sync_mod, "decrypt_json", lambda blob: dict(fake.creds))
    return fake


# ───────────────────────────────────────────────────────────── banco


@pytest.fixture
async def dono(db: AsyncSession) -> User:
    u = User(open_id="email:conf-coletor@davinci-test.com", email="conf-coletor@davinci-test.com",
             role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    return u


def _pedido(bling_id: int, dia: datetime, itens: list[tuple], *, loja: str = LOJA_KIA,
            situacao: str | None = "6", total: str = "999.90") -> list[BlingOrder]:
    """Um pedido do Bling = uma linha por item: (SKU, descrição, valor, qtd)."""
    return [
        BlingOrder(bling_id=bling_id, item_index=i, numero=str(bling_id), data=dia, loja=loja,
                   situacao=situacao, total=Decimal(total), item_codigo=sku,
                   item_descricao=descricao,
                   itemvalor=None if valor is None else Decimal(str(valor)),
                   item_quantidade=qtd)
        for i, (sku, descricao, valor, qtd) in enumerate(itens)
    ]


async def _cenario(db: AsyncSession, dono: User) -> dict[str, Any]:
    """Produtos, vínculos do ML e pedidos do Bling da Kia (loja 204713113) e
    da KFA (204438129)."""
    kia = Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="kia",
                      credentials=b"x")
    outra = Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="outra",
                        credentials=b"x")
    kfa = Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="kfa",
                      credentials=b"x")
    db.add_all([kia, outra, kfa])
    db.add(ProductCategory(bling_category_id=501, name="Eletro"))
    prod = {
        "uaf001": Product(user_id=dono.id, sku="uaf001", name="Air Fryer 4L"),  # SKU u… = eletro
        "dg053.sp": Product(user_id=dono.id, sku="dg053.sp", name="Capinha"),
        "dg099.sp": Product(user_id=dono.id, sku="dg099.sp", name="Panela", category="501"),
    }
    db.add_all(prod.values())
    await db.flush()

    def link(item: str, sku: str, integ: Integration = kia, **extra) -> ProductLink:
        return ProductLink(user_id=dono.id, product_id=prod[sku].id, integration_id=integ.id,
                           platform=IntegrationPlatform.ML, external_id=item,
                           variation_id=extra.pop("variation_id", None), **extra)

    db.add_all([
        link("MLB1", "uaf001"),
        link("MLB2", "dg053.sp"),
        # Vínculo MORTO com SKU de eletro: o vivo (capinha) vence.
        link("MLB2", "uaf001", variation_id="9",
             morto_desde=datetime(2026, 8, 1, tzinfo=UTC)),
        # Vínculo de OUTRA integração: só vale sem o da própria.
        link("MLB2", "dg099.sp", integ=outra, variation_id="8"),
        link("MLB3", "dg053.sp"),
        link("MLB4", "dg099.sp"),
    ])
    pedidos = [
        # S1: capinha + 2 air fryers (o total do pedido tem frete: não entra).
        *_pedido(1001, _dia_brt(date(2026, 9, 29)),
                 [("uaf001", "Air Fryer 4L", 150, 2), ("dg053.sp", "Capinha", 50, 1)]),
        *_pedido(1002, _dia_brt(date(2026, 9, 30)), [("dg053.sp", "Capinha", 100, 1)],
                 situacao="12"),  # cancelado
        *_pedido(1003, _dia_brt(date(2026, 9, 30)), [("dg053.sp", "Capinha", 100, 1)],
                 situacao="excluido"),
        # Domingo 23:30 em Brasília (segunda 02:30 UTC): ainda é S1. "Em
        # digitação" entra. Sem SKU → pelo nome; sem quantidade → 1.
        *_pedido(1004, _dia_brt(date(2026, 10, 4), 23, 30),
                 [(None, "Fritadeira Air Fryer avulsa", 80, None)], situacao="21"),
        # Segunda 00:00 em Brasília: já é a semana seguinte (fora).
        *_pedido(1005, _dia_brt(date(2026, 10, 5)), [("dg053.sp", "Capinha", 70, 1)]),
        # Outra loja: fora.
        *_pedido(1006, _dia_brt(date(2026, 9, 29)), [("dg053.sp", "Capinha", 40, 1)],
                 loja="999"),
        # Situação vazia entra; linha sem valor conta R$ 0,00 (com aviso).
        *_pedido(1007, _dia_brt(date(2026, 10, 1)), [("dg099.sp", "Panela", None, 1)],
                 situacao=None),
        # S2: entregue.
        *_pedido(2001, _dia_brt(date(2026, 9, 22)), [("dg053.sp", "Capinha", 200, 1)],
                 situacao="83953"),
        # KFA (Mala).
        *_pedido(3001, _dia_brt(date(2026, 9, 29)), [("b001.ci", "Mala P", 300, 1)],
                 loja=LOJA_KFA),
    ]
    db.add_all(pedidos)
    await db.commit()
    return {"kia": kia, "kfa": kfa, "outra": outra}


def _conta(integ: Integration | None, *, nome: str = "Kia", grupo: str = "celular",
           loja: str | None = LOJA_KIA, plataforma: str = "ml") -> coletores.ContaColeta:
    import uuid

    return coletores.ContaColeta(
        coleta_id=uuid.uuid4(), execucao_id=uuid.uuid4(), conta_id=uuid.uuid4(),
        plataforma=plataforma, nome=nome, grupo=grupo,
        integration_id=integ.id if integ is not None else None, bling_loja_id=loja,
        conta_key=nome.lower(),
    )


def _semana(d: dict, s: coletores.Semana) -> dict:
    return next(x for x in d["semanas"] if (x["inicio"], x["fim"]) == (
        s.inicio.isoformat(), s.fim.isoformat()))


def _item(lista: list[dict], item_id: str) -> dict:
    return next(it for it in lista if it["item_id"] == item_id)


async def _relatorio(db: AsyncSession, contas: list[tuple[str, str, dict]],
                     plataforma: str = "ml") -> dict:
    """O relatório como o fila.montar faz para o servidor (eletro pelo SKU)."""
    coletas, skus = [], {}
    for nome, grupo, d in contas:
        coletas.append({"conta_id": f"id-{nome}", "conta_key": nome, "adspower_user_id": None,
                        "nome": nome, "grupo": grupo, "status": "ok", "erro": None, "dados": d})
        if grupo == "celular":
            skus[nome] = fila._skus(d)
    mapa = await classificacao.carregar_mapa_por_sku(db, skus)
    execucao = {"id": "x", "plataforma": plataforma, "tipo": "semanal", "origem": "agenda",
                "semanas": [s.chave() for s in SEMANAS], "afiliados_ate": S1.fim,
                "criado_em": AGORA}
    return calculo.montar_relatorio(execucao, coletas, mapa_davinci=mapa, gerado_em=AGORA)


def _linha(rel: dict, grupo: str, conta: str) -> dict:
    g = next(g for g in rel["grupos"] if g["chave"] == grupo)
    return next(lin for lin in g["linhas"] if lin["conta"] == conta)


# ───────────────────────────────────────────────────────────── vendas (Bling)


async def test_vendas_do_bling_por_semana(db, dono):
    await _cenario(db, dono)
    v = await vendas_bling.vendas_por_semana(db, LOJA_KIA, SEMANAS)
    # S1: 300 + 50 (pedido 1001) + 80 (1004, domingo 23:30) + 0 (1007, sem valor).
    assert v[S1]["vendas"] == {"valor": 430.0, "pedidos": 3}
    itens = {it["item_id"]: it for it in v[S1]["vendas_itens"]}
    assert set(itens) == {"uaf001", "dg053.sp", "(sem SKU) Fritadeira Air Fryer avulsa", "dg099.sp"}
    assert itens["uaf001"] == {"item_id": "uaf001", "sku": "uaf001", "nome": "Air Fryer 4L",
                               "valor": 300.0, "pedidos": 1, "unidades": 2, "eletro": True}
    assert (itens["dg053.sp"]["valor"], itens["dg053.sp"]["eletro"]) == (50.0, False)
    assert (itens["dg099.sp"]["valor"], itens["dg099.sp"]["eletro"]) == (0.0, True)
    sem_sku = itens["(sem SKU) Fritadeira Air Fryer avulsa"]
    assert "sku" not in sem_sku and "eletro" not in sem_sku and sem_sku["unidades"] == 1
    assert v[S1]["avisos"] == [
        "Vendas: 1 linha de item sem valor no Bling (contadas como R$ 0,00)",
        "Vendas: 1 linha sem SKU no Bling — o eletro dessas sai pelo nome do produto",
    ]
    # Maior valor primeiro.
    assert v[S1]["vendas_itens"][0]["item_id"] == "uaf001"
    assert v[S2]["vendas"] == {"valor": 200.0, "pedidos": 1}
    # Semana sem pedido: zero de verdade, sem aviso.
    assert v[S3] == {"vendas": {"valor": 0.0, "pedidos": 0}, "vendas_itens": [], "avisos": []}
    # Mala não consulta a lista de eletro.
    kfa = await vendas_bling.vendas_por_semana(db, LOJA_KFA, SEMANAS, marcar_eletro=False)
    assert kfa[S1]["vendas_itens"] == [{"item_id": "b001.ci", "nome": "Mala P", "valor": 300.0,
                                        "pedidos": 1, "unidades": 1, "sku": "b001.ci"}]


async def test_vendas_da_parcial_so_os_dias_do_trecho(db, dono):
    await _cenario(db, dono)
    # Quinta 01/10: S1 = seg 28/09 a qua 30/09 — o pedido de 01/10 e o de
    # domingo ficam de fora.
    parcial = coletores.semanas_do_job(periodos.semanas("parcial", date(2026, 10, 1)))
    v = await vendas_bling.vendas_por_semana(db, LOJA_KIA, parcial)
    assert v[parcial[0]]["vendas"] == {"valor": 350.0, "pedidos": 1}
    assert v[parcial[1]]["vendas"] == {"valor": 200.0, "pedidos": 1}  # seg 21 a qua 23/09


_AVISO_ESPELHO_PARADO = (
    "Vendas: o espelho do Bling no DaVinci não tem pedido nenhum (de loja nenhuma) depois de "
    "01/10/2026 — semana sem Vendas (conferir a sincronização do Bling)"
)
_AVISO_LOJA_DESCONHECIDA = (
    "Vendas: a loja do Bling 123456789 não tem nenhum pedido no DaVinci — confira a loja "
    "ligada em Contas (Vendas não coletadas)"
)


async def test_vendas_espelho_do_bling_parado_nunca_vira_zero(db, dono, fake_ml):
    """O espelho do Bling parou em 01/10 (nenhum pedido de loja nenhuma depois):
    a S1 (até 04/10) fica SEM Vendas ("—", coleta parcial) — somar daria menos
    que o real. As semanas que o espelho cobre seguem com o número."""
    integ = await _cenario(db, dono)
    await db.execute(delete(BlingOrder).where(BlingOrder.data >= _dia_brt(date(2026, 10, 2))))
    await db.commit()
    assert await vendas_bling.ultimo_dia_do_espelho(db) == date(2026, 10, 1)
    avisos: list[str] = []
    v = await vendas_bling.vendas_por_semana(db, LOJA_KIA, SEMANAS, avisos_loja=avisos)
    assert v[S1] == {"avisos": [_AVISO_ESPELHO_PARADO]}
    assert v[S2]["vendas"] == {"valor": 200.0, "pedidos": 1}
    assert v[S3]["vendas"] == {"valor": 0.0, "pedidos": 0} and avisos == []
    # ML: a semana sai sem Vendas e a coleta fica parcial (os outros números ficam).
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    dados.confere_coleta_dados(d)
    s1 = _semana(d, S1)
    assert "vendas" not in s1 and "vendas_itens" not in s1 and s1["ads"]["gasto"] == 70.0
    assert s1["avisos"][0] == _AVISO_ESPELHO_PARADO
    assert servidor._faltou_secao(d, SEMANAS, "ml")
    rel = await _relatorio(db, [("Kia", "celular", d)])
    assert _linha(rel, "celular", "Kia")["semanas"][0]["vendas"] is None
    # Amazon: as semanas cobertas ficam; a S1, "—" (parcial).
    d = await coletor_amazon.collect(_conta(None, plataforma="amazon"), SEMANAS, session=db)
    assert "vendas" not in _semana(d, S1) and _semana(d, S2)["vendas"]["valor"] == 200.0
    assert servidor._faltou_secao(d, SEMANAS, "amazon")


async def test_vendas_loja_que_o_espelho_nao_conhece(db, dono, fake_ml):
    """Loja do Bling digitada errado (nenhum pedido dela, nunca): nada de
    R$ 0,00 calado — sem Vendas em toda semana e aviso para conferir Contas."""
    integ = await _cenario(db, dono)
    v = await vendas_bling.vendas_por_semana(db, "123456789", SEMANAS)
    # Sem a lista de avisos da loja, o aviso vai na primeira semana.
    assert v[S1] == {"avisos": [_AVISO_LOJA_DESCONHECIDA]}
    assert all(v[s] == {"avisos": []} for s in (S2, S3, S4))
    # ML: só o Ads, com o aviso da loja; a coleta vira parcial.
    d = await coletor_ml.collect(_conta(integ["kia"], loja="123456789"), SEMANAS, session=db)
    assert all("vendas" not in s and "ads" in s for s in d["semanas"])
    assert d["avisos"][0] == _AVISO_LOJA_DESCONHECIDA
    assert servidor._faltou_secao(d, SEMANAS, "ml")
    # ML sem Ads também: nada coletado → erro com o porquê.
    with pytest.raises(coletores.ColetorError, match="não tem nenhum pedido no DaVinci"):
        await coletor_ml.collect(_conta(None, loja="123456789"), SEMANAS, session=db)
    # Amazon (só Vendas): erro com o porquê.
    with pytest.raises(coletores.ColetorError, match="não tem nenhum pedido no DaVinci"):
        await coletor_amazon.collect(
            _conta(None, plataforma="amazon", loja="123456789"), SEMANAS, session=db
        )


async def test_vendas_loja_conhecida_sem_pedido_e_s1_zerada_avisam(db, dono):
    """Loja que existe no espelho mas não vendeu nas 4 semanas: R$ 0,00 (pode
    ser loja parada), com a data do último pedido (o cancelado não conta). S1
    zerada com venda nas anteriores: R$ 0,00 com aviso."""
    await _cenario(db, dono)
    db.add_all([
        *_pedido(4001, _dia_brt(date(2026, 7, 10)), [("dg053.sp", "Capinha", 10, 1)],
                 loja="555"),
        *_pedido(4002, _dia_brt(date(2026, 9, 30)), [("dg053.sp", "Capinha", 10, 1)],
                 loja="555", situacao="12"),
    ])
    await db.commit()
    avisos: list[str] = []
    v = await vendas_bling.vendas_por_semana(db, "555", SEMANAS, avisos_loja=avisos)
    assert all(v[s]["vendas"] == {"valor": 0.0, "pedidos": 0} for s in SEMANAS)
    assert all(v[s]["avisos"] == [] for s in SEMANAS)
    assert avisos == [
        "Vendas: nenhum pedido da loja do Bling 555 nas semanas da conferência (o último é de "
        "10/07/2026) — confira a loja ligada em Contas e o espelho do Bling"
    ]
    db.add_all(_pedido(4003, _dia_brt(date(2026, 9, 15)), [("dg053.sp", "Capinha", 10, 1)],
                       loja="555"))
    await db.commit()
    avisos = []
    v = await vendas_bling.vendas_por_semana(db, "555", SEMANAS, avisos_loja=avisos)
    assert v[S1]["vendas"] == {"valor": 0.0, "pedidos": 0} and avisos == []
    assert v[S1]["avisos"] == [
        "Vendas: nenhum pedido desta loja do Bling na semana, mas houve nas anteriores — "
        "confira o espelho do Bling"
    ]
    assert v[S3]["vendas"] == {"valor": 10.0, "pedidos": 1} and v[S3]["avisos"] == []


# ───────────────────────────────────────────────────────────── Mercado Livre


async def test_ml_coleta_vendas_ads_e_anuncios(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    dados.confere_coleta_dados(d)
    assert d["versao"] == 1 and "afiliados" not in str(list(_semana(d, S1)))
    s1 = _semana(d, S1)
    assert s1["vendas"] == {"valor": 430.0, "pedidos": 3}
    # Totais: soma dos 7 dias do DAILY; Pedidos Ads = unidades.
    assert s1["ads"] == {"impressoes": 7000, "cliques": 140, "gasto": 70.0, "vendas": 700.0,
                         "pedidos": 7}
    itens = s1["ads_itens"]
    assert [it["item_id"] for it in itens] == ["MLB1", "MLB2", "MLB4", "family:777002", "MLB5"]
    mlb1 = _item(itens, "MLB1")
    assert (mlb1["sku"], mlb1["eletro"], mlb1["tipo"], mlb1["ad_group_id"]) == (
        "uaf001", True, "ITEM", "11")
    assert (mlb1["impressoes"], mlb1["cliques"], mlb1["gasto"], mlb1["vendas"],
            mlb1["pedidos"]) == (2000, 40, 20.0, 200.0, 2)
    # Vínculo vivo da integração vence o morto (eletro) e o de outra conta.
    assert (_item(itens, "MLB2")["sku"], _item(itens, "MLB2")["eletro"]) == ("dg053.sp", False)
    # Família: um item eletro (panela, categoria Eletro) → o grupo é eletro.
    familia = _item(itens, "MLB4")
    assert (familia["sku"], familia["eletro"], familia["nome"]) == ("dg099.sp", True, "Kit Cozinha")
    # Família sem a lista (o /ads falhou) e anúncio sem vínculo: sem SKU,
    # o cálculo decide pelo título.
    assert "sku" not in _item(itens, "family:777002") and "sku" not in _item(itens, "MLB5")
    assert d["avisos"] == [
        "Ads por anúncio: 1 anúncio(s) com variações ou de catálogo sem a lista de itens — o "
        "eletro deles saiu pelo título",
        "Ads por anúncio: 2 anúncio(s) sem vínculo no DaVinci em 28/09–04/10 (R$ 15,00 de Ads) "
        "— o eletro deles saiu pelo título",
    ]
    assert s1["avisos"][:2] == [
        "Vendas: 1 linha de item sem valor no Bling (contadas como R$ 0,00)",
        "Vendas: 1 linha sem SKU no Bling — o eletro dessas sai pelo nome do produto",
    ]
    # anunciante 1 + DAILY 1 + 4 buscas por semana + 2 grupos FAMILY (1 vez cada).
    assert d["chamadas"] == 8
    assert len(fake_ml.chamadas("/ads")) == 2
    diaria = fake_ml.chamadas("/campaigns/search")[0]
    assert (diaria.url.params["date_from"], diaria.url.params["date_to"]) == (
        S4.inicio.isoformat(), S1.fim.isoformat())
    assert diaria.headers["api-version"] == "2"
    assert all(r.method == "GET" for r in fake_ml.feitos)

    # No relatório: Eletro = air fryer + família da panela + família "Air Fryer"
    # (pelo título); Celular = o resto. Afiliados "não coletado".
    rel = await _relatorio(db, [("Kia", "celular", d)])
    eletro = _linha(rel, "eletro", "Kia")["semanas"][0]
    celular = _linha(rel, "celular", "Kia")["semanas"][0]
    assert (eletro["vendas"], celular["vendas"]) == (380.0, 50.0)
    assert (eletro["invest_ads"], celular["invest_ads"]) == (35.0, 35.0)
    assert (eletro["impressoes"], celular["impressoes"]) == (3500, 3500)
    assert (eletro["cliques_ads"], eletro["pedidos_ads"], eletro["conversao_ads"]) == (
        70, 3, round(3 / 70 * 100, 2))
    assert (celular["vendas_ads"], celular["pedidos_ads"]) == (350.0, 4)
    assert celular["vendas_afiliados"] is None and rel["estados"]["vendas_afiliados"] == (
        "nao_coletado")
    # % do ML = só o Ads.
    assert celular["pct"] == 70.0


async def test_ml_vinculo_morto_nao_decide_o_eletro_do_anuncio(db, dono):
    """A regra da Shopee: só vínculo VIVO. Anúncio só com vínculo morto (de
    SKU eletro) fica sem SKU (o título decide); o vivo de outra integração
    vence o morto da própria."""
    integ = await _cenario(db, dono)
    prod = {p.sku: p for p in (await db.execute(select(Product))).scalars()}
    morto = datetime(2026, 9, 1, tzinfo=UTC)

    def link(item: str, sku: str, integracao: Integration, **extra) -> ProductLink:
        return ProductLink(user_id=dono.id, product_id=prod[sku].id,
                           integration_id=integracao.id, platform=IntegrationPlatform.ML,
                           external_id=item, **extra)

    db.add_all([
        link("MLB7", "uaf001", integ["kia"], morto_desde=morto),
        link("MLB8", "uaf001", integ["kia"], morto_desde=morto),
        link("MLB8", "dg053.sp", integ["outra"]),
    ])
    await db.commit()
    mapa = await coletor_ml._skus_dos_itens(db, integ["kia"].id, {"MLB2", "MLB7", "MLB8"})
    assert mapa == {"MLB2": ("dg053.sp", False), "MLB8": ("dg053.sp", False)}
    # O grupo ITEM só com vínculo morto vai pelo título (aqui, "Air Fryer" = eletro).
    grupo = coletor_ml._Grupo(id="17", tipo="ITEM", externo="MLB7", titulo="Air Fryer 5L")
    assert coletor_ml._representante(grupo, [("MLB7", None)], mapa) == ("MLB7", None, None)


async def test_ml_conta_de_mala_nao_busca_anuncio_por_anuncio(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    d = await coletor_ml.collect(
        _conta(integ["kfa"], nome="KFA", grupo="mala", loja=LOJA_KFA), SEMANAS, session=db
    )
    s1 = _semana(d, S1)
    assert s1["vendas"] == {"valor": 300.0, "pedidos": 1} and "eletro" not in str(s1)
    assert s1["ads"]["gasto"] == 70.0 and "ads_itens" not in s1
    assert fake_ml.chamadas("ad_groups") == [] and d["avisos"] == []


async def test_ml_sem_permissao_de_publicidade_fica_sem_ads(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    fake_ml.anunciante = (404, {"message": "No permissions found for user_id 123",
                                "error": "not_found", "status": 404})
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert all("ads" not in s and "ads_itens" not in s for s in d["semanas"])
    assert _semana(d, S1)["vendas"]["valor"] == 430.0
    assert d["avisos"] == [
        "Ads: a conta não tem permissão de Publicidade no Mercado Livre (sem_permissao) — Ads não "
        "coletados"
    ]
    # No relatório: "—" (None), nunca zero.
    rel = await _relatorio(db, [("Kia", "celular", d)])
    assert _linha(rel, "celular", "Kia")["semanas"][0]["invest_ads"] is None


@pytest.mark.parametrize(
    ("erro", "motivo"),
    [
        ((404, {"message": "resource not found", "error": "not_found"}), "404 not_found"),
        ((200, {"paging": {"total": 1}, "results": [{"date": "2026-09-07", "prints": 1}]}),
         "502 metricas_ausentes"),
    ],
)
async def test_ml_api_fora_nunca_vira_zero(db, dono, fake_ml, erro, motivo):
    integ = await _cenario(db, dono)
    fake_ml.diario_erro = erro
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert all("ads" not in s for s in d["semanas"])
    assert d["avisos"] == [
        f"Ads: a API de Anúncios do Mercado Livre falhou ({motivo}) — Ads não coletados"
    ]
    assert fake_ml.chamadas("ad_groups") == []


async def test_ml_token_recusado_avisa_sem_mostrar_a_resposta(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    fake_ml.creds = {"access_token": "velho", "refresh_token": "rt", "expires_at": 1,
                     "client_id": "app", "client_secret": "segredo"}
    fake_ml.token = (400, {"error": "invalid_grant", "message": "segredo-que-nao-pode-vazar"})
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert all("ads" not in s for s in d["semanas"])
    assert d["avisos"] == [
        "Ads: a API de Anúncios do Mercado Livre falhou (o Mercado Livre recusou a renovação do "
        "token — reconectar a conta) — Ads não coletados"
    ]
    assert [r.method for r in fake_ml.chamadas("/oauth/token")] == ["POST"]
    assert "segredo" not in str(d)


async def test_ml_dia_fechado_faltando_deixa_a_semana_sem_ads(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    del fake_ml.diario["2026-09-23"]
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    s2 = _semana(d, S2)
    assert "ads" not in s2 and "ads_itens" not in s2
    assert s2["avisos"][-1] == "Ads: o Mercado Livre não trouxe 23/09 — Ads da semana sem número"
    assert _semana(d, S1)["ads"]["gasto"] == 70.0 and _semana(d, S3)["ads"]["gasto"] == 70.0
    # A busca por anúncio nem passa pela semana sem total.
    buscas = [r.url.params["date_from"] for r in fake_ml.chamadas("ad_groups/search")]
    assert S2.inicio.isoformat() not in buscas


async def test_ml_busca_por_anuncio_falha_numa_semana_so(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    fake_ml.grupos_erro[S3.inicio.isoformat()] = (400, {"message": "bad", "error": "bad_request"})
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    s3 = _semana(d, S3)
    assert s3["ads"]["gasto"] == 70.0 and "ads_itens" not in s3
    assert s3["avisos"] == [
        "Ads por anúncio: a busca dos anúncios falhou (400 bad_request) — o Ads da semana ficou "
        "inteiro em Celular"
    ]
    assert "ads_itens" in _semana(d, S1)
    # O cálculo põe o Ads inteiro da S3 em Celular (e avisa).
    rel = await _relatorio(db, [("Kia", "celular", d)])
    kia = _linha(rel, "celular", "Kia")
    assert kia["semanas"][2]["invest_ads"] == 70.0 and kia["semanas"][0]["invest_ads"] == 35.0
    assert any("sem os itens de Ads" in a for a in kia["avisos"])


async def test_ml_metricas_em_maiusculas_ignoradas_tenta_minusculas(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    fake_ml.maiusculas_ignoradas = True
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert len(_semana(d, S1)["ads_itens"]) == 5
    metricas = [r.url.params["metrics"] for r in fake_ml.chamadas("ad_groups/search")]
    # A primeira em maiúsculas, as outras já em minúsculas.
    assert metricas[0].isupper() and all(m.islower() for m in metricas[1:])
    assert len(metricas) == 5


async def test_ml_sem_tempo_fica_sem_os_itens_e_com_os_totais(db, dono, fake_ml, monkeypatch):
    integ = await _cenario(db, dono)
    monkeypatch.setattr(coletor_ml, "PRAZO_ITENS_S", -1.0)
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    for s in d["semanas"]:
        assert s["ads"]["gasto"] == 70.0 and "ads_itens" not in s
        assert s["avisos"][-1].startswith("Ads por anúncio: sem tempo")
    assert fake_ml.chamadas("ad_groups") == []


async def test_ml_familias_param_de_pedir_quando_a_api_cai(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    familias = [_grupo(100 + i, "FAMILY", f"88{i}", f"Família {i}", _metr(10, 1, 1.0, 0.0, 0))
                for i in range(8)]
    fake_ml.grupos = {s.inicio.isoformat(): familias for s in SEMANAS}
    fake_ml.anuncios = {str(100 + i): (500, {"error": "x"}) for i in range(8)}
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert len(fake_ml.chamadas("/ads")) == 5  # para depois de 5 falhas seguidas
    assert d["avisos"][0].startswith("Ads por anúncio: 8 anúncio(s) com variações")
    assert {it["item_id"] for it in _semana(d, S1)["ads_itens"]} == {f"family:88{i}"
                                                                       for i in range(8)}


async def test_ml_fora_dos_90_dias_nem_chama(db, dono, fake_ml, monkeypatch):
    integ = await _cenario(db, dono)
    monkeypatch.setattr(coletor_ml, "_hoje", lambda: S1.inicio + timedelta(days=95))
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert fake_ml.feitos == []
    assert all("ads" not in s for s in d["semanas"])
    assert _semana(d, S4)["avisos"] == [
        "Ads: semana fora dos 90 dias que o Mercado Livre guarda — sem Ads"
    ]
    assert _semana(d, S1)["vendas"]["valor"] == 430.0


async def test_ml_sem_ligacoes(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    with pytest.raises(coletores.ColetorError, match="sem loja do Bling|não tem loja"):
        await coletor_ml.collect(_conta(None, loja=None), SEMANAS, session=db)
    # Sem loja: só o Ads (a coleta vira parcial pelo job).
    d = await coletor_ml.collect(_conta(integ["kia"], loja=None), SEMANAS, session=db)
    assert all("vendas" not in s and "ads" in s for s in d["semanas"])
    assert d["avisos"][0] == coletor_ml.SEM_LOJA
    # Sem integração: só as Vendas.
    d = await coletor_ml.collect(_conta(None), SEMANAS, session=db)
    assert all("vendas" in s and "ads" not in s for s in d["semanas"])
    assert d["avisos"] == [coletor_ml.SEM_INTEGRACAO]
    # Integração arquivada: não chama a API.
    integ["kia"].archived_at = AGORA
    await db.commit()
    feitos = len(fake_ml.feitos)
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert len(fake_ml.feitos) == feitos
    assert d["avisos"] == ["Ads: a integração está arquivada (Lojas) — Ads não coletados"]


# ───────────────────────────────────────────────────────────── ponta a ponta


async def _contas_db(db: AsyncSession, plataforma: str, integ: dict | None) -> None:
    db.add_all([
        ConferenciaShopeeConta(
            plataforma=plataforma, nome="Kia", grupo="celular", conta_key="kia", ordem=0,
            integration_id=integ["kia"].id if integ else None, bling_loja_id=LOJA_KIA),
        ConferenciaShopeeConta(
            plataforma=plataforma, nome="KFA", grupo="mala", conta_key="kfa", ordem=1,
            integration_id=integ["kfa"].id if integ else None, bling_loja_id=LOJA_KFA),
    ])
    await db.commit()


async def test_job_do_servidor_com_o_coletor_do_ml(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    await _contas_db(db, "ml", integ)
    fake_ml.anuncios["14"] = [{"item_id": "MLB9", "title": "Fritadeira"}]  # sem vínculo
    ml = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA, plataforma="ml")
    await db.commit()
    resumo = await servidor.coletar_execucao(ml.id, relogio=lambda: AGORA)
    assert resumo == {"execucao": str(ml.id), "contas": 2, "fechou": True}
    kia, kfa = sorted(await coletas_de(db, ml.id), key=lambda c: c.nome, reverse=True)
    assert (kia.nome, kia.status, kfa.status) == ("Kia", "ok", "ok")
    await db.refresh(ml)
    rel = ml.relatorio
    assert (ml.status, rel["plataforma"]) == ("pronto", "ml")
    eletro = _linha(rel, "eletro", "Kia")["semanas"][0]
    assert (eletro["vendas"], eletro["invest_ads"]) == (380.0, 35.0)
    assert _linha(rel, "mala", "KFA")["semanas"][0]["vendas"] == 300.0
    assert rel["geral"]["semanas"][0]["invest_ads"] == 140.0


async def test_job_do_servidor_ml_sem_permissao_fica_parcial(db, dono, fake_ml):
    integ = await _cenario(db, dono)
    await _contas_db(db, "ml", integ)
    fake_ml.anunciante = (404, {"message": "No permissions found for user_id 1"})
    ml = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA, plataforma="ml")
    await db.commit()
    await servidor.coletar_execucao(ml.id, relogio=lambda: AGORA)
    assert {c.status for c in await coletas_de(db, ml.id)} == {"parcial"}
    await db.refresh(ml)
    kia = _linha(ml.relatorio, "celular", "Kia")
    assert kia["semanas"][0]["invest_ads"] is None and kia["semanas"][0]["vendas"] == 50.0
    assert any("permissão de Publicidade" in a for a in kia["avisos"])


async def test_job_do_servidor_com_o_coletor_da_amazon(db, dono):
    await _cenario(db, dono)
    await _contas_db(db, "amazon", None)
    db.add(ConferenciaShopeeConta(plataforma="amazon", nome="Poofy", grupo="mala",
                                  conta_key="poofy", ordem=2))  # ativa sem loja: erro
    await db.commit()
    amz = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA, plataforma="amazon")
    await db.commit()
    await servidor.coletar_execucao(amz.id, relogio=lambda: AGORA)
    status = {c.nome: (c.status, c.erro) for c in await coletas_de(db, amz.id)}
    assert status["Kia"] == ("ok", None) and status["KFA"] == ("ok", None)
    assert status["Poofy"] == ("erro", coletor_amazon.SEM_LOJA)
    await db.refresh(amz)
    rel = amz.relatorio
    assert rel["plataforma"] == "amazon" and rel["estados"]["invest_ads"] == "aguardando_acesso"
    eletro = _linha(rel, "eletro", "Kia")["semanas"][0]
    celular = _linha(rel, "celular", "Kia")["semanas"][0]
    assert (eletro["vendas"], celular["vendas"]) == (380.0, 50.0)
    assert celular["invest_ads"] is None and celular["pct"] is None
    assert _linha(rel, "mala", "KFA")["semanas"][0]["vendas"] == 300.0
    assert [c["conta"] for c in rel["contas_sem_dados"]] == ["Poofy"]


async def test_amazon_coletor_direto(db, dono):
    await _cenario(db, dono)
    d = await coletor_amazon.collect(
        _conta(None, plataforma="amazon"), SEMANAS, session=db
    )
    dados.confere_coleta_dados(d)
    assert [list(s) for s in d["semanas"]] == [
        ["inicio", "fim", "vendas", "vendas_itens", "avisos"]] * 4
    assert _semana(d, S2)["vendas"] == {"valor": 200.0, "pedidos": 1}
    with pytest.raises(coletores.ColetorError):
        await coletor_amazon.collect(_conta(None, plataforma="amazon", loja=""), SEMANAS,
                                     session=db)


async def test_ml_erro_de_banco_no_detalhe_nao_derruba_a_loja(db, dono, fake_ml, monkeypatch):
    """O SAVEPOINT: erro de banco no vínculo dos anúncios só tira os itens."""
    from sqlalchemy import text

    integ = await _cenario(db, dono)

    async def quebra(session, *a, **k):
        await session.execute(text("SELECT * FROM tabela_que_nao_existe"))

    monkeypatch.setattr(coletor_ml, "_skus_dos_itens", quebra)
    d = await coletor_ml.collect(_conta(integ["kia"]), SEMANAS, session=db)
    assert all(s["ads"]["gasto"] == 70.0 and "ads_itens" not in s for s in d["semanas"])
    assert d["avisos"][-1] == (
        "Ads por anúncio: falhou (banco: ProgrammingError) — o Ads inteiro ficou em Celular"
    )
    assert _semana(d, S1)["vendas"]["valor"] == 430.0
    assert (await db.execute(text("SELECT 1"))).scalar() == 1  # a sessão segue boa
