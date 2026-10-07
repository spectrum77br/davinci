"""Coleta de Ads do Mercado Livre (conserto de 07/10/2026) + robô de demonstração.

O ML desligou em 27/05/2026 o endpoint de métricas que o DaVinci usava; o
404 era engolido e o gasto ficou zerado meses com a coleta dizendo "ok".
Aqui trava-se:
  - o endpoint documentado (campaigns/search, api-version 2, DAILY) e a
    leitura dos campos (cost/prints/clicks/total_amount, R$);
  - 404/4xx/resposta sem métrica = ERRO (estado na conta, falha contada), nunca
    sucesso com zero; os números já gravados não mudam;
  - a revisão de 7 dias não zera dia guardado quando o ML não trouxe o dia;
  - conta sem Publicidade / sem escopo = 'sem_permissao' (sem alarme repetido);
  - loja arquivada não é chamada;
  - backfill (≤ 90 dias) idempotente e simulação que não grava nem renova token;
  - robô de demonstração e /seed desligados de fábrica; contas arquivadas
    fora das telas; a 0381 só arquiva (nenhum DELETE).

Rede: httpx.MockTransport com as respostas de tests/fixtures/ml_ads (formato
da doc oficial do ML; valores inventados).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text

import app.services.marketing.ml_sync as ml_sync_mod
import app.services.ml_ads as ml_ads_mod
from app.config import get_settings
from app.models import IntegrationPlatform, UserRole
from app.models.integration import Integration
from app.models.marketing import (
    MarketingAccount,
    MarketingCampaign,
    MarketingDecision,
    MarketingMetric,
)
from app.services.marketing.bling_revenue import BlingRevenue
from app.services.ml_ads import (
    MLAdsClient,
    MLAdsError,
    parse_metricas_campanhas,
    parse_metricas_diarias,
    validar_janela,
)

_AsyncClientReal = httpx.AsyncClient
_FIX = Path(__file__).resolve().parent / "fixtures" / "ml_ads"
# "Agora" fixo: 07/10/2026 15:00 UTC = 12:00 BRT → janela de 7 dias 01..07/10.
AGORA = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)
HOJE = date(2026, 10, 7)
INICIO = date(2026, 10, 1)
ADV = 900001


def _fx(nome: str) -> dict:
    return json.loads((_FIX / nome).read_text(encoding="utf-8"))


def _ts(d: date) -> datetime:
    return datetime.combine(d, time(12, 0), tzinfo=UTC)


# ─── rede falsa ───────────────────────────────────────────────────────────


class FakeML:
    """Roteia por caminho. Cada rota pode ser trocada por (status, corpo) ou
    por uma função (request) -> (status, corpo)."""

    def __init__(self) -> None:
        self.anunciante = (200, _fx("anunciante.json"))
        self.metadados = (200, _fx("campanhas_metadados.json"))
        self.campanhas = (200, _fx("campanhas_metricas.json"))
        self.diario = (200, _fx("metricas_diarias.json"))
        self.token = (500, {"error": "refresh nao deveria ser chamado no teste"})
        self.feitos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.feitos.append(request)
        path = request.url.path
        q = request.url.params
        if path == "/advertising/advertisers":
            rota = self.anunciante
        elif path.startswith("/marketplace/advertising/"):
            rota = self.metadados
        elif path == f"/advertising/MLB/advertisers/{ADV}/product_ads/campaigns/search":
            rota = self.diario if q.get("aggregation_type") == "DAILY" else self.campanhas
        elif path == "/oauth/token":
            rota = self.token
        else:
            rota = (404, _fx("endpoint_desligado_404.json"))
        st, body = rota(request) if callable(rota) else rota
        return httpx.Response(st, json=body)

    def chamadas(self, trecho: str) -> list[httpx.Request]:
        return [r for r in self.feitos if trecho in r.url.path]

    def diarias(self) -> list[httpx.Request]:
        return [r for r in self.feitos if r.url.params.get("aggregation_type") == "DAILY"]


@pytest.fixture
def fake_ml(monkeypatch) -> FakeML:
    fake = FakeML()

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    monkeypatch.setattr(ml_ads_mod, "_RATE_LIMIT_SECONDS", 0)
    futuro = int((AGORA + timedelta(days=365)).timestamp())
    monkeypatch.setattr(
        ml_sync_mod, "decrypt_json",
        lambda blob: {"access_token": "tok", "refresh_token": "rt", "expires_at": futuro},
    )
    return fake


@pytest.fixture
def bling(monkeypatch):
    """Faturamento Bling por dia (fonte do 'revenue' da linha diária)."""
    by_day = {INICIO + timedelta(days=i): 1000.0 + i for i in range(7)}

    async def fake(session, integration, *, start, end):
        dias = {d: v for d, v in by_day.items() if start <= d <= end}
        return BlingRevenue(total=round(sum(dias.values()), 2), by_day=dias, order_count=len(dias))

    monkeypatch.setattr(ml_sync_mod, "get_bling_revenue", fake)
    return by_day


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui o router de Marketing com `enable_marketing`
    ligado (o ambiente de teste não liga): sem isto as rotas seriam 404."""
    from app.main import app
    from app.routers import marketing as marketing_router

    if not any(getattr(r, "path", "") == "/api/marketing/accounts" for r in app.routes):
        app.include_router(marketing_router.router)


@pytest.fixture(autouse=True)
async def _limpa_marketing(db):
    async def wipe():
        for tbl in (
            "marketing_decisions", "marketing_patterns", "marketing_metrics",
            "marketing_commands", "marketing_campaigns", "marketing_schedules",
            "marketing_accounts",
        ):
            await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
        await db.commit()

    await wipe()
    yield
    await wipe()


async def _integracao(db, make_user, *, nome="marquezini", arquivada=False) -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id, platform=IntegrationPlatform.ML, name=nome,
        credentials=b"ignorado-decrypt-falso", status="active", ads_enabled=True,
        department="mala",
        archived_at=AGORA - timedelta(days=90) if arquivada else None,
    )
    db.add(integ)
    await db.commit()
    return integ


async def _linhas(db, account_id) -> dict[date, MarketingMetric]:
    rows = (
        await db.execute(
            select(MarketingMetric).where(MarketingMetric.account_id == account_id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    return {r.timestamp.astimezone(UTC).date(): r for r in rows}


# ─── leitura das respostas (sem banco) ────────────────────────────────────


def test_parse_diario_soma_por_dia_e_le_os_campos():
    dias = parse_metricas_diarias(_fx("metricas_diarias.json")["results"])
    assert sorted(dias) == [INICIO + timedelta(days=i) for i in range(7)]
    d1 = dias[INICIO]
    assert (d1.spend, d1.impressions, d1.clicks) == (95.12, 11020, 260)
    assert d1.revenue == 1190.0  # total_amount = direct + indirect
    assert d1.units == 6
    assert d1.acos == round(95.12 / 1190.0 * 100, 2)
    # Duas linhas do MESMO dia (uma por campanha) somam.
    dup = parse_metricas_diarias([
        {"date": "2026-10-01", "cost": 1.5, "prints": 10, "clicks": 1, "total_amount": 3.0},
        {"date": "2026-10-01", "cost": 2.0, "prints": 5, "clicks": 2, "total_amount": 0.0},
    ])
    assert dup[INICIO].spend == 3.5 and dup[INICIO].impressions == 15


def test_parse_sem_cost_ou_sem_data_e_erro_nao_zero():
    with pytest.raises(MLAdsError) as e:
        parse_metricas_diarias([{"date": "2026-10-01", "prints": 10}])
    assert e.value.code == "metricas_ausentes"
    with pytest.raises(MLAdsError) as e:
        parse_metricas_diarias([{"cost": 1.0}])
    assert e.value.code == "resposta_inesperada"
    with pytest.raises(MLAdsError):
        parse_metricas_campanhas([{"id": 1, "name": "x", "status": "active"}])


def test_parse_cost_null_ou_nao_numero_e_erro_menos_no_dia_aberto():
    """`cost: null` virava gasto 0 gravado por cima do dia guardado."""
    for linha in (
        {"date": "2026-10-03", "cost": None, "prints": 1, "clicks": 0},
        {"date": "2026-10-03", "cost": 1.0, "prints": None, "clicks": 0},
        {"date": "2026-10-03", "cost": 1.0, "prints": 1},
    ):
        with pytest.raises(MLAdsError) as e:
            parse_metricas_diarias([linha], hoje=HOJE)
        assert e.value.code == "metricas_ausentes"
    with pytest.raises(MLAdsError) as e:
        parse_metricas_diarias([{"date": "2026-10-03", "cost": "abc", "prints": 1, "clicks": 0}])
    assert e.value.code == "resposta_inesperada"
    with pytest.raises(MLAdsError):
        parse_metricas_campanhas([{"id": 1, "metrics": {"cost": None, "prints": 1, "clicks": 1}}])
    # Hoje (o ML ainda não fechou): o dia inteiro fica sem dado, sem erro e
    # sem soma parcial; os dias fechados seguem valendo.
    dias = parse_metricas_diarias([
        {"date": "2026-10-06", "cost": 2.0, "prints": 3, "clicks": 1},
        {"date": "2026-10-07", "cost": 5.0, "prints": 3, "clicks": 1},
        {"date": "2026-10-07", "cost": None, "prints": None, "clicks": None},
    ], hoje=HOJE)
    assert sorted(dias) == [date(2026, 10, 6)]


def test_parse_campanhas_le_metricas_e_orcamento():
    m = parse_metricas_campanhas(_fx("campanhas_metricas.json")["results"])
    assert m["111111"]["spend"] == 510.4
    assert m["111111"]["revenue"] == 6200.5
    assert m["111111"]["impressions"] == 61234
    assert m["222222"]["budget"] == 30.0


def test_janela_maxima_de_90_dias():
    validar_janela(HOJE - timedelta(days=89), HOJE, hoje=HOJE)  # 90 dias: ok
    with pytest.raises(ValueError):
        validar_janela(HOJE - timedelta(days=90), HOJE, hoje=HOJE)
    with pytest.raises(ValueError):
        validar_janela(HOJE, HOJE - timedelta(days=1), hoje=HOJE)


async def test_cliente_chama_endpoint_documentado_com_api_version_2(fake_ml):
    c = MLAdsClient({"access_token": "tok", "expires_at": 0})
    dias = await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert dias[INICIO].spend == 95.12
    anunciante, diario = fake_ml.feitos
    assert anunciante.url.path == "/advertising/advertisers"
    assert anunciante.url.params["product_id"] == "PADS"
    assert anunciante.headers["api-version"] == "1"
    assert diario.url.path == f"/advertising/MLB/advertisers/{ADV}/product_ads/campaigns/search"
    assert diario.headers["api-version"] == "2"
    assert diario.url.params["aggregation_type"] == "DAILY"
    assert diario.url.params["date_from"] == "2026-10-01"
    assert diario.url.params["date_to"] == "2026-10-07"
    for metrica in ("cost", "prints", "clicks", "total_amount"):
        assert metrica in diario.url.params["metrics"].split(",")
    # Nunca mais o endpoint desligado.
    assert not any(r.url.path.endswith("/product_ads/campaigns") for r in fake_ml.feitos)


def _linhas_campanha_dia(n_camp: int, valor: float, dias: int = 7) -> list[dict]:
    """Uma linha por campanha e dia (o formato que a doc deixa em aberto)."""
    return [
        {"campaign_id": 1000 + c, "date": (INICIO + timedelta(days=i)).isoformat(),
         "cost": valor, "prints": 1, "clicks": 0, "total_amount": 0.0}
        for c in range(n_camp) for i in range(dias)
    ]


def _paginado(linhas: list[dict], *, tam: int = 50, total: int | None = -1,
              ignora_offset: bool = False):
    """Rota que pagina `linhas` como o ML (offset/limit); `tam` = teto de linhas
    que o ML manda por página; `total=None` tira o paging.total."""
    def rota(request: httpx.Request):
        off = 0 if ignora_offset else int(request.url.params.get("offset", 0))
        paging = {"offset": off, "limit": tam}
        if total is not None:
            paging["total"] = len(linhas) if total == -1 else total
        return 200, {"paging": paging, "results": linhas[off:off + tam]}
    return rota


async def test_pagina_repetida_e_erro_nao_dado_parcial(fake_ml):
    """O ML ignorando o offset (sempre a 1ª página) não pode virar 'as 50
    linhas que vieram' gravadas como o dia: é erro."""
    fake_ml.diario = _paginado(_linhas_campanha_dia(15, 10.0), ignora_offset=True)
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(MLAdsError) as e:
        await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert e.value.code == "paginacao_incompleta"
    assert "repetiu" in e.value.message
    assert len(fake_ml.diarias()) == 2


async def test_pagina_menor_que_o_limite_segue_ate_o_total(fake_ml):
    """O ML mandando 20 por página (pedimos 50): segue o offset pelo que veio
    até o paging.total — nada de parar na 1ª página achando que acabou."""
    fake_ml.diario = _paginado(_linhas_campanha_dia(15, 10.0), tam=20)
    c = MLAdsClient({"access_token": "tok"})
    dias = await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert {d: m.spend for d, m in dias.items()} == {
        INICIO + timedelta(days=i): 150.0 for i in range(7)
    }
    assert [int(r.url.params["offset"]) for r in fake_ml.diarias()] == [0, 20, 40, 60, 80, 100]


async def test_pagina_vazia_antes_do_total_e_erro(fake_ml):
    linhas = _linhas_campanha_dia(15, 10.0)
    fake_ml.diario = _paginado(linhas[:60], total=105)  # o ML "perdeu" 45 linhas
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(MLAdsError) as e:
        await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert e.value.code == "paginacao_incompleta"
    assert "60 de 105" in e.value.message


async def test_total_que_muda_no_meio_da_leitura_e_erro(fake_ml):
    linhas = _linhas_campanha_dia(15, 10.0)

    def rota(request):
        off = int(request.url.params.get("offset", 0))
        total = 105 if off == 0 else 110
        return 200, {"paging": {"total": total}, "results": linhas[off:off + 50]}

    fake_ml.diario = rota
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(MLAdsError) as e:
        await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert e.value.code == "paginacao_incompleta"


async def test_total_menor_que_as_linhas_segue_pelas_paginas_cheias(fake_ml):
    """Se o `paging.total` contar outra coisa (ex.: campanhas) e vierem mais
    linhas que ele, não para no total: segue enquanto a página vier cheia."""
    fake_ml.diario = _paginado(_linhas_campanha_dia(15, 10.0), total=15)
    c = MLAdsClient({"access_token": "tok"})
    dias = await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert dias[INICIO].spend == 150.0 and dias[HOJE].spend == 150.0
    assert len(fake_ml.diarias()) == 3


async def test_sem_paging_total_pagina_curta_e_a_ultima(fake_ml):
    fake_ml.diario = _paginado(_linhas_campanha_dia(10, 1.0), total=None)  # 70 linhas
    c = MLAdsClient({"access_token": "tok"})
    dias = await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert dias[INICIO].spend == 10.0
    assert len(fake_ml.diarias()) == 2


async def test_sync_com_paginacao_incompleta_nao_mexe_no_dia_bom(db, make_user, fake_ml, bling):
    """Antes: o dia bom de R$ 95,12 virava R$ 80,00 (só as linhas da 1ª
    página) com status 'ok'. Agora é erro e o número guardado fica."""
    integ = await _integracao(db, make_user)
    assert (await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA))["status"] == "ok"
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()

    fake_ml.diario = _paginado(_linhas_campanha_dia(15, 10.0), ignora_offset=True)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "erro" and r["code"] == "paginacao_incompleta"
    assert (await _linhas(db, acc.id))[INICIO].spend == 95.12

    fake_ml.diario = _paginado(_linhas_campanha_dia(15, 10.0), tam=20)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok"
    assert (await _linhas(db, acc.id))[INICIO].spend == 150.0


async def test_backfill_de_90_dias_vai_em_pedacos_de_30(fake_ml):
    c = MLAdsClient({"access_token": "tok"})
    inicio = HOJE - timedelta(days=89)
    fake_ml.diario = (200, {"paging": {"total": 0}, "results": []})
    assert await c.fetch_daily_metrics(date_from=inicio, date_to=HOJE, hoje=HOJE) == {}
    janelas = [(r.url.params["date_from"], r.url.params["date_to"]) for r in fake_ml.diarias()]
    assert janelas == [
        ("2026-07-10", "2026-08-08"), ("2026-08-09", "2026-09-07"), ("2026-09-08", "2026-10-07"),
    ]


async def test_dia_fora_da_janela_pedida_e_erro(fake_ml):
    fake_ml.diario = (200, {"paging": {"total": 1}, "results": [
        {"date": "2026-09-30", "cost": 1.0, "prints": 1, "clicks": 0},
    ]})
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(MLAdsError) as e:
        await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert e.value.code == "resposta_inesperada"


async def test_campanhas_vem_do_endpoint_documentado_sem_o_caminho_antigo(fake_ml):
    c = MLAdsClient({"access_token": "tok"})
    camps = await c.list_campaigns_with_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert {(x.campaign_id, x.name, x.status, x.daily_budget, x.spend) for x in camps} == {
        ("111111", "Malas G", "active", 120.0, 510.4),
        ("222222", "Mochilas", "paused", 30.0, 40.1),
    }
    assert fake_ml.chamadas("/marketplace/") == []  # não documentado: nem chamado


async def test_caminho_antigo_so_completa_e_falha_dele_nao_derruba(fake_ml):
    corpo = _fx("campanhas_metricas.json")
    del corpo["results"][1]["name"]
    fake_ml.campanhas = (200, corpo)
    c = MLAdsClient({"access_token": "tok"})
    camps = {x.campaign_id: x for x in await c.list_campaigns_with_metrics(
        date_from=INICIO, date_to=HOJE, hoje=HOJE)}
    assert camps["222222"].name == "Mochilas"  # veio do complemento
    assert len(fake_ml.chamadas("/marketplace/")) == 1

    fake_ml.metadados = (404, _fx("endpoint_desligado_404.json"))
    c = MLAdsClient({"access_token": "tok"})
    camps = {x.campaign_id: x for x in await c.list_campaigns_with_metrics(
        date_from=INICIO, date_to=HOJE, hoje=HOJE)}
    assert camps["222222"].spend == 40.1 and camps["222222"].name == ""


async def test_404_na_busca_do_anunciante_e_sem_permissao(fake_ml):
    fake_ml.anunciante = (404, _fx("sem_permissao_404.json"))
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(ml_ads_mod.MLAdsSemPermissaoError):
        await c.get_advertiser_id()
    fake_ml.anunciante = (200, _fx("anunciante_vazio.json"))
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(ml_ads_mod.MLAdsSemPermissaoError):
        await c.get_advertiser_id()


async def test_simulacao_nao_renova_token(fake_ml):
    c = MLAdsClient({"access_token": "tok", "refresh_token": "rt", "expires_at": 1},
                    permitir_refresh=False)
    with pytest.raises(MLAdsError) as e:
        await c.fetch_daily_metrics(date_from=INICIO, date_to=HOJE, hoje=HOJE)
    assert e.value.code == "token_expirado"
    assert fake_ml.feitos == []  # nem /oauth/token, nem Ads


# ─── sync (banco) ─────────────────────────────────────────────────────────


async def test_sync_ok_grava_um_dia_por_linha_e_estado_ok(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok", r

    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    await db.refresh(acc)
    assert acc.sync_status == "ok" and acc.sync_erro is None
    assert acc.sync_ok_em is not None
    linhas = await _linhas(db, acc.id)
    assert sorted(linhas) == [INICIO + timedelta(days=i) for i in range(7)]
    d1 = linhas[INICIO]
    assert (d1.spend, d1.impressions, d1.clicks) == (95.12, 11020, 260)
    assert d1.revenue == 1000.0  # Bling do dia
    assert d1.acos == round(95.12 / 1000.0 * 100, 2)
    assert d1.intensity == 0
    # Hoje: o ML ainda não fechou o dia (atualiza às 10h BRT do dia seguinte).
    assert linhas[HOJE].spend == 0.0 and linhas[HOJE].acos is None

    camps = {c.external_id: c for c in (await db.execute(
        select(MarketingCampaign).where(MarketingCampaign.account_id == acc.id)
    )).scalars()}
    assert camps["111111"].spend == 510.4 and camps["111111"].revenue == 6200.5
    assert camps["111111"].status == "active" and camps["222222"].status == "paused"

    await db.refresh(integ)
    assert integ.last_ads_sync_at is not None
    assert integ.consecutive_errors == 0


async def test_404_das_metricas_e_erro_e_nao_mexe_nos_numeros(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    acc = MarketingAccount(
        integration_id=integ.id, name=integ.name, platform="ml", department="mala",
        acos_target=7.0, agent_enabled=False, sync_status="ok",
    )
    db.add(acc)
    await db.flush()
    for i in range(7):
        db.add(MarketingMetric(
            account_id=acc.id, timestamp=_ts(INICIO + timedelta(days=i)),
            spend=50.0 + i, revenue=900.0, impressions=1000, clicks=10, orders=0,
            acos=5.0, intensity=0,
        ))
    await db.commit()
    integ_last_test_ok = integ.last_test_ok

    fake_ml.diario = (404, _fx("endpoint_desligado_404.json"))
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "erro"
    assert r["code"] == "not_found"

    await db.refresh(acc)
    assert acc.sync_status == "erro"
    assert "not_found" in (acc.sync_erro or "")
    assert acc.sync_ok_em is None
    await db.refresh(integ)
    assert integ.consecutive_errors == 1  # conta para o alarme da 3ª falha
    assert integ.last_ads_sync_at is None  # não carimba sucesso
    assert integ.last_test_ok == integ_last_test_ok  # não pinta a integração
    linhas = await _linhas(db, acc.id)
    assert [linhas[INICIO + timedelta(days=i)].spend for i in range(7)] == [
        50.0 + i for i in range(7)
    ]


async def test_resposta_sem_metricas_e_erro(db, make_user, fake_ml, bling):
    """O ML devolver as linhas sem 'cost' (ignorou o metrics=) era o outro
    caminho do zero calado."""
    integ = await _integracao(db, make_user)
    fake_ml.diario = (200, {"paging": {"total": 1}, "results": [{"date": "2026-10-01"}]})
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "erro" and r["code"] == "metricas_ausentes"


async def test_revisao_nao_zera_dia_que_o_ml_nao_trouxe(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok"
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()

    # Próxima rodada: o ML só traz 06 e 07 (06 com valor novo).
    fake_ml.diario = (200, {"paging": {"total": 2}, "results": [
        {"date": "2026-10-06", "cost": 99.99, "prints": 1, "clicks": 1, "total_amount": 10.0},
        {"date": "2026-10-07", "cost": 12.34, "prints": 2, "clicks": 1, "total_amount": 0.0},
    ]})
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok"
    linhas = await _linhas(db, acc.id)
    assert linhas[INICIO].spend == 95.12  # ML não trouxe → mantém
    assert linhas[date(2026, 10, 5)].impressions == 10544
    assert linhas[date(2026, 10, 6)].spend == 99.99
    assert linhas[HOJE].spend == 12.34
    assert len(linhas) == 7  # nenhuma linha duplicada


async def test_campanha_que_nao_veio_nas_metricas_fica_como_estava(db, make_user, fake_ml, bling):
    """Antes: campanha listada pelo caminho antigo e ausente da busca de
    métricas ganhava gasto/vendas/impressões 0 por cima do que estava gravado."""
    integ = await _integracao(db, make_user)
    assert (await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA))["status"] == "ok"
    corpo = _fx("campanhas_metricas.json")
    corpo["results"] = corpo["results"][:1]
    corpo["paging"]["total"] = 1
    fake_ml.campanhas = (200, corpo)
    assert (await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA))["status"] == "ok"
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    camps = {c.external_id: c for c in (await db.execute(
        select(MarketingCampaign).where(MarketingCampaign.account_id == acc.id)
        .execution_options(populate_existing=True)
    )).scalars()}
    assert camps["222222"].spend == 40.1 and camps["222222"].revenue == 159.9
    assert camps["222222"].impressions == 5120


async def test_dia_fechado_sem_dado_e_avisado_e_mantido(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    assert (await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA))["status"] == "ok"
    corpo = _fx("metricas_diarias.json")
    corpo["results"] = [r for r in corpo["results"] if r["date"] != "2026-10-03"]
    corpo["results"][-1].update(cost=None, prints=None, clicks=None)  # hoje, aberto
    corpo["paging"]["total"] = len(corpo["results"])
    fake_ml.diario = (200, corpo)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok"
    assert r["avisos"]["dias_sem_dado_ml"] == ["2026-10-03"]  # hoje não conta
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    assert (await _linhas(db, acc.id))[date(2026, 10, 3)].spend == 101.77


async def test_sem_permissao_grava_estado_sem_alarme(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user, nome="eron")
    fake_ml.anunciante = (404, _fx("sem_permissao_404.json"))
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "sem_permissao"
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    assert acc.sync_status == "sem_permissao"
    assert "No permissions found" in acc.sync_erro
    await db.refresh(integ)
    assert integ.consecutive_errors == 0
    assert await _linhas(db, acc.id) == {}


async def test_403_escopo_tambem_e_sem_permissao(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    fake_ml.campanhas = (403, {"message": "forbidden", "error": "forbidden", "status": 403})
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "sem_permissao" and r["code"] == "scope_missing"


async def test_404_generico_no_anunciante_e_erro_nao_sem_permissao(db, make_user, fake_ml, bling):
    """Se o ML desligar/mudar o endereço do anunciante (como fez com o das
    métricas), toda conta ficaria 'sem permissão' (amarelo, sem alarme) e os
    números parariam calados. Só a mensagem da doc é 'sem permissão'."""
    fake_ml.anunciante = (404, _fx("endpoint_desligado_404.json"))
    c = MLAdsClient({"access_token": "tok"})
    with pytest.raises(MLAdsError) as e:
        await c.get_advertiser_id()
    assert not isinstance(e.value, ml_ads_mod.MLAdsSemPermissaoError)
    assert e.value.status == 404

    integ = await _integracao(db, make_user)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "erro" and r["code"] == "not_found"
    await db.refresh(integ)
    assert integ.consecutive_errors == 1  # conta para o alarme


async def test_loja_arquivada_nao_e_chamada(db, make_user, fake_ml, bling):
    ativa = await _integracao(db, make_user, nome="ativa")
    arquivada = await _integracao(db, make_user, nome="nexus", arquivada=True)
    resultados = await ml_sync_mod.sync_all_ml_integrations(db)
    assert [r["integration_id"] for r in resultados] == [str(ativa.id)]
    fake_ml.feitos.clear()
    r = await ml_sync_mod.sync_ml_integration(db, arquivada.id, agora=AGORA)
    assert r == {"status": "skipped", "reason": "integracao_arquivada"}
    assert fake_ml.feitos == []


async def test_conta_marketing_arquivada_nao_e_chamada(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    db.add(MarketingAccount(
        integration_id=integ.id, name=integ.name, platform="ml", department="mala",
        acos_target=7.0, agent_enabled=False, arquivada_em=AGORA,
    ))
    await db.commit()
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r == {"status": "skipped", "reason": "conta_arquivada"}
    assert fake_ml.feitos == []


async def test_adota_conta_orfa_com_o_mesmo_nome(db, make_user, fake_ml, bling):
    """jlas2/aguiar2/forpaper ficaram sem integration_id: ligar o Ads delas
    não pode bater na unique (name, platform, department)."""
    orfa = MarketingAccount(
        integration_id=None, name="jlas2", platform="ml", department="mala",
        acos_target=7.0, agent_enabled=False,
    )
    db.add(orfa)
    await db.commit()
    integ = await _integracao(db, make_user, nome=" jlas2")
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "ok"
    await db.refresh(orfa)
    assert orfa.integration_id == integ.id
    n = (await db.execute(select(func.count()).select_from(MarketingAccount))).scalar_one()
    assert n == 1


async def test_simulacao_do_sync_nao_grava_nada(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, simular=True, agora=AGORA)
    assert r["status"] == "ok" and r["simulacao"] is True
    assert [d["acao"] for d in r["dias"]] == ["criar"] * 7
    assert r["totais"]["gasto"] == 550.5
    n = (await db.execute(select(func.count()).select_from(MarketingAccount))).scalar_one()
    assert n == 0


async def test_simulacao_roda_sem_as_colunas_da_0381(db, make_user, fake_ml, bling):
    """A verificação em produção (só simulação) roda ANTES da migration 0381:
    nada no caminho simulado pode ler as colunas novas de marketing_accounts."""
    integ = await _integracao(db, make_user)
    acc = MarketingAccount(
        integration_id=integ.id, name=integ.name, platform="ml", department="mala",
        acos_target=7.0, agent_enabled=False,
    )
    db.add(acc)
    await db.flush()
    db.add(MarketingMetric(account_id=acc.id, timestamp=_ts(INICIO), spend=1.0, revenue=2.0,
                           impressions=3, clicks=0, orders=0, acos=None, intensity=0))
    await db.commit()
    acc_id = acc.id
    colunas = (
        "sync_status", "sync_erro", "sync_em", "sync_ok_em", "arquivada_em", "arquivada_motivo",
    )
    # DDL dentro da transação: o rollback da própria simulação devolve as colunas.
    await db.execute(text(
        "ALTER TABLE marketing_accounts " + ", ".join(f"DROP COLUMN {c}" for c in colunas)
    ))
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, simular=True, agora=AGORA)
    assert r["status"] == "ok", r
    assert r["conta_nova"] is False
    assert r["dias"][0]["acao"] == "atualizar" and r["dias"][0]["antes"]["spend"] == 1.0

    await db.execute(text(
        "ALTER TABLE marketing_accounts " + ", ".join(f"DROP COLUMN {c}" for c in colunas)
    ))
    r = await ml_sync_mod.backfill_ml_ads(db, dias=7, simular=True, agora=AGORA)
    assert r[0]["status"] == "ok", r
    assert r[0]["conta_nova"] is False
    n = (await db.execute(
        text("select count(*) from marketing_metrics where account_id = :a"), {"a": acc_id}
    )).scalar_one()
    assert n == 1  # nada gravado; e as colunas voltaram (o select de baixo usa o model)
    assert (await db.execute(select(MarketingAccount.sync_status))).all() == [(None,)]


# ─── backfill ─────────────────────────────────────────────────────────────


async def test_backfill_e_idempotente(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    r1 = await ml_sync_mod.backfill_ml_ads(db, dias=10, simular=False, agora=AGORA)
    assert r1[0]["status"] == "ok", r1
    # 10 dias: 7 com dado do ML + 3 que o ML não trouxe (nascem zerados).
    assert r1[0]["totais"]["criar"] == 10
    assert r1[0]["avisos"]["dias_sem_dado_ml"] == ["2026-09-28", "2026-09-29", "2026-09-30"]
    # A chamada pediu os 10 dias numa vez só.
    diario = [r for r in fake_ml.feitos if r.url.params.get("aggregation_type") == "DAILY"]
    assert diario[-1].url.params["date_from"] == "2026-09-28"

    r2 = await ml_sync_mod.backfill_ml_ads(db, dias=10, simular=False, agora=AGORA)
    assert r2[0]["totais"]["iguais"] == 10
    assert r2[0]["totais"]["criar"] == r2[0]["totais"]["atualizar"] == 0
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    linhas = await _linhas(db, acc.id)
    assert len(linhas) == 10
    assert linhas[INICIO].spend == 95.12


async def test_backfill_corrige_historico_inflado(db, make_user, fake_ml, bling):
    integ = await _integracao(db, make_user)
    acc = MarketingAccount(
        integration_id=integ.id, name=integ.name, platform="ml", department="mala",
        acos_target=7.0, agent_enabled=False,
    )
    db.add(acc)
    await db.flush()
    db.add(MarketingMetric(  # somatório de 7 dias gravado num dia só (bug antigo)
        account_id=acc.id, timestamp=_ts(INICIO), spend=4809.99, revenue=0.0,
        impressions=6_940_000, clicks=0, orders=0, acos=None, intensity=0,
    ))
    await db.commit()
    r = await ml_sync_mod.backfill_ml_ads(db, dias=7, simular=False, agora=AGORA)
    assert r[0]["totais"]["atualizar"] == 1 and r[0]["totais"]["criar"] == 6
    linhas = await _linhas(db, acc.id)
    assert linhas[INICIO].spend == 95.12 and linhas[INICIO].impressions == 11020


async def test_backfill_simulado_nao_grava(db, make_user, fake_ml, bling):
    await _integracao(db, make_user)
    r = await ml_sync_mod.backfill_ml_ads(db, dias=30, simular=True, agora=AGORA)
    assert r[0]["status"] == "ok" and r[0]["simulacao"] is True
    assert len(r[0]["dias"]) == 30
    n = (await db.execute(select(func.count()).select_from(MarketingMetric))).scalar_one()
    assert n == 0


async def test_backfill_erro_numa_conta_nao_mexe_e_segue(db, make_user, fake_ml, bling):
    await _integracao(db, make_user)
    fake_ml.diario = (404, _fx("endpoint_desligado_404.json"))
    r = await ml_sync_mod.backfill_ml_ads(db, dias=5, simular=False, agora=AGORA)
    assert r[0]["status"] == "erro"
    n = (await db.execute(select(func.count()).select_from(MarketingMetric))).scalar_one()
    assert n == 0


# ─── token renovado nunca se perde (refresh_token do ML é de uso único) ───


@pytest.fixture
def token_vencido(fake_ml, monkeypatch):
    """Credencial vencida: a 1ª chamada renova (gasta o refresh_token velho)."""
    monkeypatch.setattr(
        ml_sync_mod, "decrypt_json",
        lambda blob: {"access_token": "velho", "refresh_token": "rt-velho", "expires_at": 1,
                      "client_id": "cid", "client_secret": "csec"},
    )
    monkeypatch.setattr(ml_sync_mod, "encrypt_json", lambda d: json.dumps(d).encode())
    fake_ml.token = (200, {"access_token": "novo", "refresh_token": "rt-novo",
                           "expires_in": 21600})
    return fake_ml


async def _rt_gravado(integ_id) -> str:
    """refresh_token CONFIRMADO no banco (outra conexão só vê o que teve commit)."""
    from tests.conftest import _test_session

    async with _test_session() as s:
        blob = (await s.execute(
            select(Integration.credentials).where(Integration.id == integ_id)
        )).scalar_one()
    return json.loads(bytes(blob))["refresh_token"]


async def test_backfill_apply_sem_permissao_guarda_token_renovado(
    db, make_user, token_vencido, bling
):
    """Antes: token vencido → renovou → 404 'No permissions found' → rollback
    → o banco ficava com o refresh_token já gasto (loja a reconectar)."""
    integ = await _integracao(db, make_user, nome="eron")
    token_vencido.anunciante = (404, _fx("sem_permissao_404.json"))
    out = await ml_sync_mod.backfill_ml_ads(
        db, dias=7, integration_ids=[integ.id], simular=False, agora=AGORA,
    )
    assert out[0]["status"] == "sem_permissao"
    assert len(token_vencido.chamadas("/oauth/token")) == 1
    assert await _rt_gravado(integ.id) == "rt-novo"


async def test_excecao_inesperada_depois_da_renovacao_guarda_token(
    db, make_user, token_vencido, bling, monkeypatch
):
    """O `except Exception` do sync_all/backfill faz rollback: o token novo já
    tem de estar confirmado antes disso."""
    integ_id = (await _integracao(db, make_user)).id

    async def explode(*a, **k):
        raise ValueError("bug qualquer depois da chamada ao ML")

    monkeypatch.setattr(ml_sync_mod, "get_bling_revenue", explode)
    resultados = await ml_sync_mod.sync_all_ml_integrations(db)
    assert resultados[0]["status"] == "erro"
    assert await _rt_gravado(integ_id) == "rt-novo"

    await ml_sync_mod.backfill_ml_ads(db, dias=7, simular=False, agora=AGORA)
    token_vencido.token = (200, {"access_token": "n2", "refresh_token": "rt-n2",
                                 "expires_in": 21600})
    out = await ml_sync_mod.backfill_ml_ads(db, dias=7, simular=False, agora=AGORA)
    assert out[0]["status"] == "erro" and out[0]["code"] == "excecao"
    assert len(token_vencido.chamadas("/oauth/token")) == 3
    assert await _rt_gravado(integ_id) == "rt-n2"


async def test_sync_sem_permissao_guarda_token_renovado(db, make_user, token_vencido, bling):
    integ = await _integracao(db, make_user, nome="eron")
    token_vencido.anunciante = (404, _fx("sem_permissao_404.json"))
    r = await ml_sync_mod.sync_ml_integration(db, integ.id, agora=AGORA)
    assert r["status"] == "sem_permissao"
    assert await _rt_gravado(integ.id) == "rt-novo"


# ─── uma linha diária por conta e dia ─────────────────────────────────────


async def test_cron_e_backfill_ao_mesmo_tempo_nao_duplicam_o_dia(db, make_user, fake_ml, bling):
    """Antes: as duas rodadas liam 'não existe' e inseriam → 14 linhas para
    7 dias, gasto contado em dobro nas telas."""
    import asyncio

    from tests.conftest import _test_session

    integ = await _integracao(db, make_user, nome="jlas2")
    db.add(MarketingAccount(integration_id=integ.id, name="jlas2", platform="ml",
                            department="mala", agent_enabled=False, status="active"))
    await db.commit()
    async with _test_session() as s1, _test_session() as s2:
        r = await asyncio.gather(
            ml_sync_mod.sync_ml_integration(s1, integ.id, agora=AGORA),
            ml_sync_mod.backfill_ml_ads(s2, dias=7, integration_ids=[integ.id],
                                        simular=False, agora=AGORA),
        )
    assert r[0]["status"] == "ok" and r[1][0]["status"] == "ok"
    acc = (await db.execute(
        select(MarketingAccount).where(MarketingAccount.integration_id == integ.id)
    )).scalar_one()
    n = (await db.execute(select(func.count()).select_from(MarketingMetric).where(
        MarketingMetric.account_id == acc.id))).scalar_one()
    assert n == 7
    soma = (await db.execute(select(func.sum(MarketingMetric.spend)).where(
        MarketingMetric.account_id == acc.id))).scalar_one()
    assert round(soma, 2) == 550.5


async def test_chave_unica_da_linha_diaria(db, make_user):
    from sqlalchemy.exc import IntegrityError

    acc = MarketingAccount(name="x", platform="ml", department="mala", acos_target=7.0,
                           agent_enabled=False)
    db.add(acc)
    await db.flush()
    for intensity in (0, 50, 50):  # por hora (≠ 0) pode repetir; a diária não
        db.add(MarketingMetric(account_id=acc.id, timestamp=_ts(INICIO), intensity=intensity))
    await db.commit()
    db.add(MarketingMetric(account_id=acc.id, timestamp=_ts(INICIO), intensity=0))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_linha_diaria_repetida_e_reportada(db, make_user, fake_ml, bling):
    """Banco sem a chave única (antes da 0381): o dia repetido sai no
    retorno/aviso em vez de ser pulado calado."""
    integ = await _integracao(db, make_user)
    acc = MarketingAccount(integration_id=integ.id, name=integ.name, platform="ml",
                           department="mala", acos_target=7.0, agent_enabled=False)
    db.add(acc)
    await db.commit()
    # DDL na transação: o rollback da simulação devolve o índice.
    await db.execute(text("DROP INDEX uq_marketing_metrics_conta_dia"))
    for _ in range(2):
        db.add(MarketingMetric(account_id=acc.id, timestamp=_ts(INICIO), spend=5.0,
                               intensity=0))
    await db.flush()
    r = await ml_sync_mod.backfill_ml_ads(db, dias=7, simular=True, agora=AGORA)
    assert r[0]["avisos"]["linhas_repetidas"] == [INICIO.isoformat()]
    assert r[0]["totais"]["linhas_repetidas"] == 1
    assert r[0]["dias"][0]["linha_repetida"] is True


async def test_backfill_limite_de_90_dias(db):
    with pytest.raises(ValueError):
        await ml_sync_mod.backfill_ml_ads(db, dias=91)
    with pytest.raises(ValueError):
        await ml_sync_mod.backfill_ml_ads(db, dias=0)


# ─── Amazon: robô de demonstração desligado + contas arquivadas ──────────


async def _conta_demo_amazon(db, **kw) -> MarketingAccount:
    kw.setdefault("agent_enabled", True)
    kw.setdefault("department", "mala")
    acc = MarketingAccount(
        integration_id=None, name="Kfa", platform="amazon", acos_target=7.0, **kw,
    )
    db.add(acc)
    await db.commit()
    return acc


async def test_robo_demo_desligado_de_fabrica_nao_grava(db):
    from app.services.marketing.agent import agent_decision_cycle

    assert get_settings().marketing_agente_simulado is False
    acc = await _conta_demo_amazon(db)
    assert await agent_decision_cycle(acc.id) is None
    n = (await db.execute(select(func.count()).select_from(MarketingMetric))).scalar_one()
    assert n == 0


async def test_cron_do_robo_demo_nao_roda_sem_a_trava(db, monkeypatch):
    import app.worker as worker

    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    monkeypatch.setattr(worker._settings, "marketing_agente_simulado", False)
    await _conta_demo_amazon(db)
    await worker.marketing_agent_cycle({})
    assert (await db.execute(select(func.count()).select_from(MarketingMetric))).scalar_one() == 0
    assert (await db.execute(select(func.count()).select_from(MarketingDecision))).scalar_one() == 0


async def test_robo_demo_ligado_nao_escreve_em_conta_arquivada(db, monkeypatch):
    from app.services.marketing.agent import agent_decision_cycle

    monkeypatch.setattr(get_settings(), "marketing_agente_simulado", True)
    arquivada = await _conta_demo_amazon(db, arquivada_em=AGORA)
    assert await agent_decision_cycle(arquivada.id) is None
    viva = await _conta_demo_amazon(db, department="celular")
    assert await agent_decision_cycle(viva.id) is not None  # a trava liga de verdade


async def test_seed_desligado_responde_409(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post("/api/marketing/seed")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "seed_desligado"
    r = await client.post("/api/marketing/trigger-all")
    assert r.status_code == 200 and r.json() == []


async def test_conta_arquivada_some_das_telas(db, client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    demo = await _conta_demo_amazon(db, arquivada_em=AGORA, agent_enabled=False)
    real = MarketingAccount(
        name="kfa", platform="ml", department="mala", acos_target=7.0,
        agent_enabled=False, sync_status="erro", sync_erro="not_found: 404",
    )
    db.add(real)
    await db.flush()
    db.add(MarketingMetric(account_id=demo.id, timestamp=AGORA - timedelta(hours=1),
                           spend=123.0, revenue=456.0, intensity=55))
    db.add(MarketingDecision(account_id=demo.id, timestamp=AGORA - timedelta(hours=1),
                             action="no_action", reasoning="mock", params={},
                             market_intensity=50, in_base_window=False))
    await db.commit()

    contas = (await client.get("/api/marketing/accounts")).json()
    assert [c["name"] for c in contas] == ["kfa"]
    assert contas[0]["sync_status"] == "erro" and contas[0]["sync_erro"] == "not_found: 404"
    resumo = (await client.get("/api/marketing/metrics/summary")).json()
    assert str(demo.id) not in resumo["period_1"]["data"]
    serie = (await client.get("/api/marketing/timeseries?platform=amazon")).json()
    assert serie["accounts"] == [] and serie["series"] == {}
    assert (await client.get("/api/marketing/decisions")).json() == []
    # Nada foi apagado.
    assert (await db.execute(select(func.count()).select_from(MarketingMetric))).scalar_one() == 1


async def test_migration_0381_so_arquiva_contas_demo(db, make_user):
    """Roda o UPDATE da 0379 no schema de teste: só as contas da Amazon SEM
    integração são arquivadas (e com o robô desligado); nada é apagado."""
    caminho = Path(__file__).resolve().parents[1] / "alembic/versions/0381_marketing_ads_estado.py"
    spec = importlib.util.spec_from_file_location("m0379", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fonte = caminho.read_text(encoding="utf-8").upper()
    assert "DELETE FROM" not in fonte and "TRUNCATE" not in fonte
    assert mod.SQL_ARQUIVAR_DEMO.strip().upper().startswith("UPDATE")
    # O índice da migration é o mesmo do model (create_all dos testes).
    idx = {i.name: i for i in MarketingMetric.__table__.indexes}[mod.INDICE_DIARIA]
    assert idx.unique and str(idx.dialect_options["postgresql"]["where"]) == "intensity = 0"
    assert [c.name for c in idx.columns] == ["account_id", "timestamp"]
    schema = (await db.execute(text("select current_schema()"))).scalar_one()
    assert (await db.execute(text(mod.SQL_DIARIAS_REPETIDAS.format(schema=schema)))).all() == []

    user = await make_user()
    integ_amz = Integration(user_id=user.id, platform=IntegrationPlatform.AMAZON, name="kfa",
                            credentials=b"x", status="active")
    db.add(integ_amz)
    await db.flush()
    demo1 = await _conta_demo_amazon(db)
    demo2 = await _conta_demo_amazon(db, department="celular")
    real_amz = MarketingAccount(integration_id=integ_amz.id, name="kfa", platform="amazon",
                                department="geral", acos_target=7.0, agent_enabled=False)
    orfa_ml = MarketingAccount(name="jlas2", platform="ml", department="celular",
                               acos_target=7.0, agent_enabled=False)
    db.add_all([real_amz, orfa_ml])
    await db.commit()

    await db.execute(
        text(mod.SQL_ARQUIVAR_DEMO.format(schema=schema)).bindparams(motivo=mod.MOTIVO_DEMO)
    )
    await db.commit()
    for a in (demo1, demo2, real_amz, orfa_ml):
        await db.refresh(a)
    assert demo1.arquivada_em is not None and demo1.agent_enabled is False
    assert demo2.arquivada_em is not None and "demonstração" in demo2.arquivada_motivo
    assert real_amz.arquivada_em is None
    assert orfa_ml.arquivada_em is None
    n = (await db.execute(select(func.count()).select_from(MarketingAccount))).scalar_one()
    assert n == 4
