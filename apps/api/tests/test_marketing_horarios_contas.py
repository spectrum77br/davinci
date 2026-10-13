"""Horários dos anúncios — GET /api/marketing/horarios/contas (13/10/2026).

O que fica travado:
  • o segmento de cada conta de Ads: o grupo da conta da Conferência (vínculo
    gravado → senão ML pela integração / Shopee pelo nome, com JLAS = Atlas e
    KIA = Fiore só quando não há conta com o próprio nome; só um-para-um) →
    senão o `department` se for mala/celular → senão celular;
  • o saldo de Ads só na Shopee: a leitura mais nova do perfil do AdsPower da
    conta da Conferência (0 é 0; sem leitura = null); no Mercado Livre os
    campos nem existem (o `credit_balance` do ML não quer dizer nada);
  • o eletro: quem tem vendas/Ads de eletro na ÚLTIMA rodada pronta da
    plataforma, refeito dos `dados` das coletas com a mesma separação do
    relatório (o relatório congelado não muda); 10 min em memória; cálculo que
    falha cai no relatório congelado; sem rodada = eletro_execucao null;
  • conta arquivada fora (como /accounts); marketing:view; plataforma só
    shopee | ml.

Números e nomes INVENTADOS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
    ConferenciaShopeeSaldo,
    Integration,
    IntegrationPlatform,
    MarketingAccount,
    UserRole,
)
from app.services.conferencia_shopee import fila
from app.services.marketing import horarios_contas as hc
from tests.test_conferencia_shopee_fila import dados_loja

pytestmark = pytest.mark.asyncio

API = "/api/marketing/horarios/contas"
AGORA = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
SEMANAS = [
    {"inicio": "2026-09-28", "fim": "2026-10-04"},
    {"inicio": "2026-09-21", "fim": "2026-09-27"},
    {"inicio": "2026-09-14", "fim": "2026-09-20"},
    {"inicio": "2026-09-07", "fim": "2026-09-13"},
]


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui o router de Marketing com `enable_marketing`
    ligado (o ambiente de teste não liga): sem isto as rotas seriam 404."""
    from app.main import app
    from app.routers import marketing as marketing_router

    if not any(getattr(r, "path", "") == API for r in app.routes):
        app.include_router(marketing_router.router)


@pytest.fixture(autouse=True)
async def _limpa(db):
    async def wipe():
        for tbl in (
            "marketing_decisions", "marketing_patterns", "marketing_metrics",
            "marketing_commands", "marketing_campaigns", "marketing_schedules",
            "marketing_accounts",
        ):
            await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
        await db.commit()
        hc.limpar_cache()

    await wipe()
    yield
    await wipe()


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


# ───────────────────────────────────────────────────────────── helpers


async def _conf(db, plataforma="shopee", **contas) -> dict[str, ConferenciaShopeeConta]:
    """nome → (grupo, perfil do AdsPower | integration_id, ativo)."""
    saida = {}
    for nome, (grupo, chave, ativo) in contas.items():
        c = ConferenciaShopeeConta(
            plataforma=plataforma, nome=nome, grupo=grupo, ativo=ativo, conta_key=nome.lower(),
            adspower_user_id=chave if plataforma == "shopee" else None,
            integration_id=chave if plataforma != "shopee" else None,
        )
        db.add(c)
        saida[nome] = c
    await db.commit()
    return saida


async def _ads(db, nome, platform="shopee", department="geral", **kw) -> MarketingAccount:
    a = MarketingAccount(
        name=nome, platform=platform, department=department, acos_target=7.0,
        agent_enabled=False, **kw,
    )
    db.add(a)
    await db.commit()
    return a


async def _integracao(db, make_user, nome) -> Integration:
    user = await make_user()
    integ = Integration(
        user_id=user.id, platform=IntegrationPlatform.ML, name=nome,
        credentials=b"x", status="active",
    )
    db.add(integ)
    await db.commit()
    return integ


async def _rodada(
    db, plataforma, coletas, *, status="pronto", criado_em=AGORA, relatorio=None
) -> ConferenciaShopeeExecucao:
    """Uma rodada com as coletas [(conta, dados)] já guardadas."""
    ex = ConferenciaShopeeExecucao(
        plataforma=plataforma, tipo="semanal", origem="agenda", semanas=SEMANAS,
        afiliados_ate=datetime(2026, 10, 4).date(), esperar_afiliados_ate=criado_em,
        corte=criado_em, prazo=criado_em, status=status, criado_em=criado_em,
        relatorio=relatorio if relatorio is not None else ({} if status == "pronto" else None),
        finalizado_em=criado_em + timedelta(hours=1) if status == "pronto" else None,
    )
    db.add(ex)
    await db.flush()
    for conta, dados in coletas:
        db.add(ConferenciaShopeeColeta(
            execucao_id=ex.id, conta_id=conta.id, adspower_user_id=conta.adspower_user_id,
            nome=conta.nome, grupo=conta.grupo, status="ok", dados=dados,
        ))
    await db.commit()
    return ex


def _air_fryer() -> list[dict]:
    """Ads com um item eletro pelo título (sem vínculo nem categoria)."""
    return [
        {"item_id": "900", "nome": "Capinha", "tipo": "product_manual", "impressoes": 4000,
         "cliques": 80, "gasto": 40.0, "vendas": 600.0, "pedidos": 4},
        {"item_id": "700", "nome": "Air Fryer 4L", "tipo": "product_manual", "impressoes": 1000,
         "cliques": 20, "gasto": 20.0, "vendas": 300.0, "pedidos": 2},
    ]


def _por_nome(corpo: dict) -> dict[str, dict]:
    return {c["name"]: c for c in corpo["contas"]}


# ───────────────────────────────────────────────────────────── segmento + saldo


async def test_shopee_segmento_do_grupo_apelidos_saldo_e_arquivada(client, db, admin):
    conf = await _conf(
        db,
        Atlas=("celular", "k-atlas", True),
        Fiore=("celular", "k-fiore", True),
        KFA=("mala", "k-kfa", True),
        Mega=("celular", "k-mega", True),
        **{"Lucas MEI": ("celular", "k-lucas", False)},  # inativa na Conferência: vale igual
        Eron=("celular", "k-eron", False),  # sem conta de Ads
    )
    await _ads(db, "JLAS")
    await _ads(db, "KIA")
    await _ads(db, "KFA", credit_balance=999.0)  # o saldo NÃO vem daqui
    await _ads(db, "Mega")
    await _ads(db, " \u200bLucas  MEI")
    await _ads(db, "Luno")  # sem conta na Conferência, department geral → celular
    await _ads(db, "Poofy", department="mala")  # sem conta → o department
    await _ads(db, "Velha", arquivada_em=AGORA)  # arquivada: fora
    await _ads(db, "Mega ML", platform="ml")  # outra plataforma: fora
    db.add_all([
        ConferenciaShopeeSaldo(adspower_user_id="k-atlas", lido_em=AGORA - timedelta(days=7),
                               valor=Decimal("100.00")),
        ConferenciaShopeeSaldo(adspower_user_id="k-atlas", lido_em=AGORA, valor=Decimal("365.41")),
        ConferenciaShopeeSaldo(adspower_user_id="k-fiore", lido_em=AGORA + timedelta(minutes=5),
                               valor=Decimal("3400.33")),
        ConferenciaShopeeSaldo(adspower_user_id="k-kfa", lido_em=AGORA, valor=Decimal("0.00")),
        # de um perfil sem conta de Ads ligada: não aparece em ninguém
        ConferenciaShopeeSaldo(adspower_user_id="k-eron", lido_em=AGORA, valor=Decimal("5")),
    ])
    await db.commit()

    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["plataforma"] == "shopee"
    assert corpo["eletro_execucao"] is None
    assert [c["name"] for c in corpo["contas"]] == [
        "JLAS", "KFA", "KIA", "Lucas MEI", "Luno", "Mega", "Poofy",
    ], "A–Z, nome limpo, sem a arquivada nem a do ML"
    c = _por_nome(corpo)
    assert {n: x["segmento"] for n, x in c.items()} == {
        "JLAS": "celular", "KFA": "mala", "KIA": "celular", "Lucas MEI": "celular",
        "Luno": "celular", "Mega": "celular", "Poofy": "mala",
    }
    assert all(x["eletro"] is False for x in c.values())
    assert c["JLAS"]["saldo"] == 365.41, "a leitura mais nova do perfil da Atlas"
    assert c["JLAS"]["saldo_em"].startswith("2026-10-06T18:00")
    assert c["KIA"]["saldo"] == 3400.33
    assert c["KFA"]["saldo"] == 0.0, "zero é zero (não o credit_balance 999)"
    assert c["Mega"]["saldo"] is None and c["Mega"]["saldo_em"] is None, "sem leitura"
    assert c["Luno"]["saldo"] is None
    assert set(c["Mega"]) == {"id", "name", "segmento", "eletro", "acos_target", "saldo",
                              "saldo_em"}
    assert conf["Eron"].id not in {x["id"] for x in corpo["contas"]}


async def test_vinculo_gravado_vence_e_conta_com_dono_nao_serve_a_outra(client, db, admin):
    conf = await _conf(
        db, Minas=("mala", "k-minas", True), Mini=("celular", "k-mini", True),
    )
    # "Outra" está ligada à Minas pela coluna: vale a gravada, mesmo com outro nome.
    await _ads(db, "Outra", conferencia_conta_id=conf["Minas"].id)
    # "Minas" (sem vínculo) não pega a Minas, que já tem dono → department.
    await _ads(db, "Minas", department="celular")
    # Gravada para conta de OUTRA plataforma: ignorada, acha pelo nome.
    ml = await _conf(db, "ml", Mini=("mala", None, True))
    await _ads(db, "Mini", conferencia_conta_id=ml["Mini"].id)
    db.add(ConferenciaShopeeSaldo(adspower_user_id="k-minas", lido_em=AGORA,
                                  valor=Decimal("2051.93")))
    await db.commit()

    c = _por_nome((await client.get(API, params={"platform": "shopee"})).json())
    assert c["Outra"]["segmento"] == "mala" and c["Outra"]["saldo"] == 2051.93
    assert c["Minas"]["segmento"] == "celular" and c["Minas"]["saldo"] is None
    assert c["Mini"]["segmento"] == "celular", "a Mini da Shopee, não a do ML"


async def test_apelido_so_sem_conta_com_o_proprio_nome_e_ambiguo_fica_sem(client, db, admin):
    await _conf(
        db,
        JLAS=("mala", "k-jlas", True),
        Atlas=("celular", "k-atlas", True),
        Fiore=("mala", "k-fiore", True),
        Vita=("mala", "k-vita", True),
    )
    await _ads(db, "jlas")  # tem conta com o próprio nome: não vira Atlas
    await _ads(db, "K.I.A")  # "kia" → Fiore
    # Duas contas de Ads com o mesmo nome normalizado: nenhuma fica com a Vita.
    await _ads(db, "Vita")
    await _ads(db, "VITA ")
    c = (await client.get(API, params={"platform": "shopee"})).json()["contas"]
    seg = {(x["name"], x["segmento"]) for x in c}
    assert ("jlas", "mala") in seg
    assert ("K.I.A", "mala") in seg
    assert ("Vita", "celular") in seg and ("VITA", "celular") in seg


async def test_ml_pela_integracao_sem_saldo(client, db, admin, make_user):
    i_kfa = await _integracao(db, make_user, "kfa")
    i_couh = await _integracao(db, make_user, "counhago")
    i_nexus = await _integracao(db, make_user, "nexus")
    await _conf(
        db, "ml",
        KFA=("mala", i_kfa.id, True),
        Counhago=("celular", i_couh.id, True),
    )
    await _ads(db, "kfa", "ml", "celular", integration_id=i_kfa.id, credit_balance=50.0)
    await _ads(db, " counhago", "ml", "geral", integration_id=i_couh.id)
    await _ads(db, "nexus", "ml", "mala", integration_id=i_nexus.id)  # sem conta na Conferência
    await _ads(db, "mega", "ml", "geral")  # sem nada → celular
    await _ads(db, "KFA", "shopee", "geral")  # da Shopee: fora

    r = await client.get(API, params={"platform": "ml"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["plataforma"] == "ml" and corpo["eletro_execucao"] is None
    c = _por_nome(corpo)
    assert list(c) == ["counhago", "kfa", "mega", "nexus"]
    assert {n: x["segmento"] for n, x in c.items()} == {
        "counhago": "celular", "kfa": "mala", "mega": "celular", "nexus": "mala",
    }, "kfa: o grupo da Conferência (mala) vence o department (celular)"
    for x in c.values():
        assert "saldo" not in x and "saldo_em" not in x, "ML não tem saldo (cobra depois)"


# ───────────────────────────────────────────────────────────── eletro


async def test_eletro_da_ultima_rodada_pronta_refeito_das_coletas(client, db, admin, monkeypatch):
    conf = await _conf(
        db,
        Bia=("celular", "k-bia", True),
        Caio=("celular", "k-caio", True),
        Ana=("mala", "k-ana", True),
    )
    bia = await _ads(db, "Bia")
    await _ads(db, "Caio")
    await _ads(db, "Ana")
    sem_eletro = dados_loja(SEMANAS)
    com_eletro = dados_loja(SEMANAS, itens_ads=_air_fryer())
    # Rodada velha: o Caio tinha eletro. Não conta.
    await _rodada(db, "shopee", [(conf["Caio"], com_eletro)], criado_em=AGORA - timedelta(days=7))
    # A última pronta: Bia com air fryer; Ana (mala) também, mas mala não separa eletro.
    ultima = await _rodada(
        db, "shopee",
        [(conf["Bia"], com_eletro), (conf["Caio"], sem_eletro), (conf["Ana"], com_eletro)],
    )
    # Mais novas que não contam: uma coletando (com `relatorio` JSON null) e
    # uma cancelada.
    await _rodada(db, "shopee", [], status="coletando", criado_em=AGORA + timedelta(days=1))
    await _rodada(db, "shopee", [], status="cancelado", criado_em=AGORA + timedelta(hours=2))
    # Uma pronta do ML mais nova: é de outra plataforma.
    await _rodada(db, "ml", [], criado_em=AGORA + timedelta(days=2))

    chamadas = []
    original = fila.montar

    async def espiao(session, execucao, agora):
        chamadas.append(execucao.id)
        return await original(session, execucao, agora)

    monkeypatch.setattr(fila, "montar", espiao)

    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["eletro_execucao"]["id"] == str(ultima.id)
    assert corpo["eletro_execucao"]["gerado_em"].startswith("2026-10-06T19:00")
    c = _por_nome(corpo)
    assert c["Bia"]["eletro"] is True, "relatório congelado vazio: veio do cálculo das coletas"
    assert c["Bia"]["segmento"] == "celular", "aparece em Celular E em Eletro"
    assert c["Caio"]["eletro"] is False
    assert c["Ana"]["eletro"] is False
    assert chamadas == [ultima.id]

    # 10 min em memória: a segunda chamada não refaz a conta.
    r = await client.get(API, params={"platform": "shopee"})
    assert _por_nome(r.json())["Bia"]["eletro"] is True
    assert chamadas == [ultima.id]
    # O relatório guardado não mudou (nada de recalcular).
    await db.refresh(ultima)
    assert ultima.relatorio == {} and ultima.status == "pronto"
    await db.refresh(bia)
    assert bia.conferencia_conta_id is None, "a rota não grava vínculo"


async def test_eletro_cai_no_relatorio_congelado_se_o_calculo_falhar(
    client, db, admin, monkeypatch
):
    conf = await _conf(db, Caio=("celular", "k-caio", True), Bia=("celular", "k-bia", True))
    await _ads(db, "Caio")
    await _ads(db, "Bia")

    def linha(conta, **semana):
        vazia = dict.fromkeys(hc.METRICAS_ELETRO)
        return {"conta_id": str(conta.id), "conta": conta.nome,
                "semanas": [{**vazia, **semana}, vazia, vazia, vazia]}

    relatorio = {"grupos": [
        {"chave": "celular", "linhas": []},
        {"chave": "eletro", "linhas": [
            linha(conf["Caio"], vendas=120.0),
            # Só comissão/cliques de afiliados, sem venda: não conta.
            {**linha(conf["Bia"]), "semanas": [
                {"invest_afiliados": 5.0, "cliques_afiliados": 30}, {}, {}, {}]},
        ]},
    ]}
    await _rodada(db, "shopee", [], relatorio=relatorio)

    async def quebra(session, execucao, agora):
        raise RuntimeError("dados tortos")

    monkeypatch.setattr(fila, "montar", quebra)
    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 200, r.text
    c = _por_nome(r.json())
    assert c["Caio"]["eletro"] is True
    assert c["Bia"]["eletro"] is False


async def test_ultima_execucao_nao_traz_o_relatorio(db):
    """O `relatorio` (JSONB grande) fica de fora da leitura de toda chamada;
    só o caminho de falha o lê, à parte."""
    from sqlalchemy import inspect as sa_inspect

    relatorio = {"grupos": [{"chave": "eletro", "linhas": []}], "notas": ["x" * 1000]}
    ex = await _rodada(db, "shopee", [], relatorio=relatorio)
    db.expunge_all()
    ultima = await hc.ultima_execucao(db, "shopee")
    assert ultima is not None and ultima.id == ex.id
    assert "relatorio" in sa_inspect(ultima).unloaded, "relatorio adiado"
    assert ultima.semanas == SEMANAS and ultima.finalizado_em is not None
    assert await hc._relatorio_congelado(db, ex.id) == relatorio


async def test_eletro_sem_calculo_nem_congelado_nao_guarda(client, db, admin, monkeypatch):
    """Cálculo e leitura do congelado falham: a lista sai (sem eletro) e nada
    fica 10 min em memória — a próxima chamada tenta de novo."""
    conf = await _conf(db, Bia=("celular", "k-bia", True))
    await _ads(db, "Bia")
    await _rodada(db, "shopee", [(conf["Bia"], dados_loja(SEMANAS, itens_ads=_air_fryer()))])

    async def quebra(session, execucao, agora):
        raise RuntimeError("dados tortos")

    async def sem_congelado(session, execucao_id):
        raise RuntimeError("banco caiu")

    original = fila.montar
    monkeypatch.setattr(fila, "montar", quebra)
    monkeypatch.setattr(hc, "_relatorio_congelado", sem_congelado)
    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 200, r.text
    assert _por_nome(r.json())["Bia"]["eletro"] is False
    assert hc._cache_eletro == {}, "falha dupla não fica guardada"

    monkeypatch.setattr(fila, "montar", original)
    r = await client.get(API, params={"platform": "shopee"})
    assert _por_nome(r.json())["Bia"]["eletro"] is True, "a próxima chamada refaz"


async def test_eletro_do_mercado_livre(client, db, admin, make_user):
    i_a = await _integracao(db, make_user, "loja a")
    i_b = await _integracao(db, make_user, "loja b")
    conf = await _conf(db, "ml", LojaA=("celular", i_a.id, True), LojaB=("celular", i_b.id, True))
    await _ads(db, "loja a", "ml", "celular", integration_id=i_a.id)
    await _ads(db, "loja b", "ml", "celular", integration_id=i_b.id)

    def dados(eletro: bool) -> dict:
        d = dados_loja(SEMANAS)
        for s in d["semanas"]:
            s["vendas_itens"] = [
                {"item_id": "MLB1", "nome": "Produto", "valor": s["vendas"]["valor"],
                 "pedidos": 3, "eletro": eletro},
            ]
            s["ads_itens"] = []
            s["ads"] = {"impressoes": 0, "cliques": 0, "gasto": 0.0, "vendas": 0.0, "pedidos": 0}
        d["saldo_ads"] = None
        return d

    await _rodada(db, "ml", [(conf["LojaA"], dados(True)), (conf["LojaB"], dados(False))])
    c = _por_nome((await client.get(API, params={"platform": "ml"})).json())
    assert c["loja a"]["eletro"] is True
    assert c["loja b"]["eletro"] is False


async def test_contas_eletro_do_relatorio_aguenta_lixo():
    assert hc.contas_eletro_do_relatorio(None) == frozenset()
    assert hc.contas_eletro_do_relatorio({"grupos": "x"}) == frozenset()
    rel = {"grupos": [{"chave": "eletro", "linhas": [
        {"conta_id": "nao-e-uuid", "semanas": [{"vendas": 1}]},
        {"conta_id": None, "semanas": [{"vendas": 1}]},
        "lixo",
        {"conta_id": "6624133e-4a8d-4ecc-ab78-1594330e38f6",
         "semanas": [{"vendas": True, "impressoes": "12"}]},
    ]}]}
    assert {str(u) for u in hc.contas_eletro_do_relatorio(rel)} == {
        "6624133e-4a8d-4ecc-ab78-1594330e38f6"
    }


async def test_chave_nome_e_nome_limpo():
    assert hc.chave_nome(" \u200bLucas  MEI") == "lucasmei"
    assert hc.chave_nome("Atlás-2") == "atlas2"
    assert hc.nome_limpo(" \u200b barbosa\u00a0 ") == "barbosa"


# ───────────────────────────────────────────────────────────── acesso


async def test_permissao_e_plataforma(client, db, make_user, auth_as):
    await _ads(db, "Mega")
    sem = await make_user(permissions={"marketing": {"view": False}})
    auth_as(sem)
    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 403
    ve = await make_user(permissions={"marketing": {"view": True}})
    auth_as(ve)
    r = await client.get(API, params={"platform": "shopee"})
    assert r.status_code == 200 and [c["name"] for c in r.json()["contas"]] == ["Mega"]
    assert (await client.get(API, params={"platform": "amazon"})).status_code == 422
    assert (await client.get(API)).status_code == 422
    auth_as(None)
    assert (await client.get(API, params={"platform": "shopee"})).status_code == 401


async def test_rota_nao_grava_nada(client, db, admin):
    conf = await _conf(db, Mega=("celular", "k-mega", True))
    a = await _ads(db, "Mega")
    await client.get(API, params={"platform": "shopee"})
    gravado = (
        await db.execute(
            select(MarketingAccount.conferencia_conta_id).where(MarketingAccount.id == a.id)
        )
    ).scalar_one()
    assert gravado is None
    assert conf["Mega"].id is not None
