"""Conferência Shopee — o relatório (services/conferencia_shopee/calculo), função pura.

Números INVENTADOS com a forma do ColetaDados (contrato §4); nada de conta real.
O que fica travado: a linha de Mala; Celular = total − eletro e Eletro = só os
itens eletro (seção vazia continua vazia); quem entra em Eletro; totais com %
das somas; Geral; saldo das semanas passadas pela leitura mais perto do dia
seguinte ao fim (±1 dia, empate = a mais cedo; Eletro sempre vazio); contas sem
dados; afiliados incompletos; divergências; Ads sem item; e as variações
("▲ 12,3%", "▼ 0,8 p.p.", "novo", "=", "—") iguais às da tela.

`relatorio_exemplo()` serve também aos testes dos arquivos (saída).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.services.conferencia_shopee import calculo, periodos
from app.services.conferencia_shopee.calculo import montar_relatorio, variacao

SEMANAS = periodos.semanas("semanal", date(2026, 10, 6))
GERADO = datetime(2026, 10, 6, 19, 0, tzinfo=UTC)


def execucao(**extra) -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "tipo": "semanal",
        "origem": "agenda",
        "semanas": SEMANAS,
        "afiliados_ate": "2026-10-04",
        "criado_em": datetime(2026, 10, 6, 16, 30, tzinfo=UTC),
        **extra,
    }


def _afiliados(vendas, comissao, pedidos, cliques=400):
    bloco = {"vendas": vendas, "comissao": comissao, "pedidos": pedidos}
    if cliques is not None:  # None = coleta antiga, sem o campo
        bloco["cliques"] = cliques
    return bloco


def _afiliado_item(item, nome, cat, vendas, comissao, cliques=20, pedidos=1):
    it = {"item_id": item, "nome": nome, "categoria_id": cat, "vendas": vendas,
          "comissao": comissao, "pedidos": pedidos}
    if cliques is not None:
        it["cliques"] = cliques
    return it


def _ads(impressoes, gasto, vendas, cliques=10, pedidos=3):
    return {"impressoes": impressoes, "cliques": cliques, "gasto": gasto, "vendas": vendas,
            "pedidos": pedidos}


def _ads_item(item, nome, imp, gasto, vendas, cliques=1, pedidos=1):
    return {"item_id": item, "nome": nome, "tipo": "product_manual", "impressoes": imp,
            "cliques": cliques, "gasto": gasto, "vendas": vendas, "pedidos": pedidos}


def semana(
    i: int,
    *,
    afiliados=(1000.0, 40.0, 10),
    afiliados_itens=(),
    ads=(5000, 100.0, 800.0),
    ads_itens=(),
    vendas=(2000.0, 20),
    vendas_itens=(),
    avisos=(),
) -> dict:
    """Uma semana do ColetaDados. `afiliados` = (vendas, comissão, pedidos[,
    cliques — padrão 400; None = coleta antiga sem o campo]); `ads` =
    (impressões, gasto, vendas[, cliques = 10, pedidos = 3]); `vendas` =
    (valor, pedidos); None = a seção falhou. Itens: tuplas curtas (ver
    abaixo)."""
    s = SEMANAS[i]

    def lista(itens, montar):
        return None if itens is None else [montar(*it) for it in itens]

    return {
        "inicio": s["inicio"],
        "fim": s["fim"],
        "afiliados": None if afiliados is None else _afiliados(*afiliados),
        # (item_id, nome, categoria_id, vendas, comissão[, cliques = 20, pedidos = 1])
        "afiliados_itens": lista(afiliados_itens, _afiliado_item),
        "ads": None if ads is None else _ads(*ads),
        # (item_id, nome, impressões, gasto, vendas[, cliques = 1, pedidos = 1])
        "ads_itens": lista(ads_itens, _ads_item),
        "vendas": None if vendas is None else {"valor": vendas[0], "pedidos": vendas[1]},
        # (item_id, nome, valor)
        "vendas_itens": lista(
            vendas_itens,
            lambda item, nome, valor: {"item_id": item, "nome": nome, "valor": valor,
                                       "pedidos": 1},
        ),
        "avisos": list(avisos),
    }


def dados(semanas: list[dict], *, saldo=250.0, ultimo="2026-10-04", usuario="loja_teste",
          avisos=()) -> dict:
    return {
        "versao": 1,
        "coletado_em": "2026-10-06T17:00:00Z",
        "duracao_s": 80.0,
        "chamadas": 50,
        "login": {"username": usuario, "shopid": 1, "shop_name": "Loja"},
        "login_auto_usado": False,
        "saldo_ads": saldo,
        "afiliados_ultimo_dia": ultimo,
        "semanas": semanas,
        "avisos": list(avisos),
    }


def coleta(nome: str, grupo: str, d: dict | None, *, status="ok", erro=None, chave=None,
           uid=None) -> dict:
    return {
        "conta_id": f"id-{nome.lower()}",
        "conta_key": chave or nome.lower(),
        "adspower_user_id": uid or f"k-{nome.lower()}",
        "nome": nome,
        "grupo": grupo,
        "status": status,
        "erro": erro,
        "dados": d,
    }


def _quatro(**kw) -> list[dict]:
    return [semana(i, **kw) for i in range(4)]


# Barbosa: celular com air fryer (eletro pela categoria) e capinha (celular).
# Afiliados: 400 cliques (120 + 280); Ads: 10 cliques e 3 pedidos (4/1 + 5/2 + 1/0).
_AF_ITENS = (("1", "Air Fryer 5L", 100010, 300.0, 15.0, 120),
             ("2", "Capinha", 100013, 700.0, 25.0, 280, 9))
_ADS_ITENS = (("1", "Air Fryer anúncio", 2000, 40.0, 300.0, 4, 1),
              ("2", "Capinha", 2500, 50.0, 450.0, 5, 2),
              (None, "Anúncio da loja", 500, 10.0, 50.0, 1, 0))
_VENDAS_ITENS = (("1", "Air Fryer 5L", 600.0), ("2", "Capinha", 1400.0))


def relatorio_exemplo() -> dict:
    """Mala (Inova), Celular+Eletro (Barbosa), Celular sem eletro (atv), sem
    dados (Luno) — o mesmo cenário dos testes de saída."""
    barbosa = _quatro(afiliados_itens=_AF_ITENS, ads_itens=_ADS_ITENS,
                      vendas_itens=_VENDAS_ITENS)
    return montar_relatorio(
        execucao(),
        [
            coleta("Inova", "mala", dados(_quatro(afiliados_itens=_AF_ITENS), saldo=120.0)),
            coleta("Barbosa", "celular", dados(barbosa, ultimo="2026-10-03")),
            coleta("atv", "celular", dados(_quatro(), saldo=None)),
            coleta("Luno", "celular", None, status="sem_automacao", erro="perfil Firefox"),
        ],
        mapa_davinci={},
        saldos={"k-barbosa": [("2026-09-29T15:00:00Z", 400.0)]},
        gerado_em=GERADO,
    )


def _grupo(rel: dict, chave: str) -> dict:
    return next(g for g in rel["grupos"] if g["chave"] == chave)


def _linha(rel: dict, grupo: str, conta: str) -> dict:
    return next(lin for lin in _grupo(rel, grupo)["linhas"] if lin["conta"] == conta)


# ───────────────────────────────────────────────────────────── forma


def test_forma_do_relatorio_e_a_do_contrato():
    rel = relatorio_exemplo()
    assert set(rel) == {
        "versao", "execucao_id", "tipo", "origem", "gerado_em", "criado_em", "semanas",
        "metricas", "grupos", "geral", "notas", "contas_sem_dados", "afiliados_incompletos",
        "divergencias", "nao_atribuido_ads",
    }
    assert rel["versao"] == 1
    assert rel["execucao_id"] == "11111111-1111-1111-1111-111111111111"
    assert rel["gerado_em"] == "2026-10-06T19:00:00+00:00"
    assert rel["criado_em"] == "2026-10-06T16:30:00+00:00"
    assert rel["semanas"][0] == {"inicio": "2026-09-28", "fim": "2026-10-04",
                                 "rotulo": "28/09–04/10"}
    # As 8 do contrato no mesmo lugar e, depois, as 6 de 07/10/2026.
    assert [m["chave"] for m in rel["metricas"]] == [
        "vendas_afiliados", "vendas_ads", "saldo_ads", "impressoes", "invest_afiliados",
        "invest_ads", "pct", "vendas", "cliques_afiliados", "pedidos_afiliados",
        "conversao_afiliados", "cliques_ads", "pedidos_ads", "conversao_ads",
    ]
    assert rel["metricas"][6] == {"chave": "pct", "rotulo": "% s/ vendas",
                                  "tipo": "percentual", "bom": "desce"}
    assert {m["chave"]: (m["tipo"], m["bom"]) for m in rel["metricas"][8:]} == {
        "cliques_afiliados": ("inteiro", "neutro"),
        "pedidos_afiliados": ("inteiro", "sobe"),
        "conversao_afiliados": ("percentual", "sobe"),
        "cliques_ads": ("inteiro", "neutro"),
        "pedidos_ads": ("inteiro", "sobe"),
        "conversao_ads": ("percentual", "sobe"),
    }
    assert [g["chave"] for g in rel["grupos"]] == ["mala", "celular", "eletro"]
    assert [g["rotulo"] for g in rel["grupos"]] == ["Mala", "Celular", "Eletro"]
    lin = _linha(rel, "celular", "Barbosa")
    assert set(lin) == {"conta_id", "conta", "usuario", "status", "erro", "avisos", "semanas"}
    assert len(lin["semanas"]) == 4
    assert list(lin["semanas"][0]) == [m["chave"] for m in rel["metricas"]]
    assert set(_grupo(rel, "mala")["total"]) == {"contas", "sem_dados", "semanas"}


# ───────────────────────────────────────────────────────────── linhas


def test_linha_de_mala_e_o_total_da_conta():
    rel = relatorio_exemplo()
    lin = _linha(rel, "mala", "Inova")
    assert lin["usuario"] == "loja_teste"
    assert lin["status"] == "ok"
    assert lin["semanas"][0] == {
        "vendas_afiliados": 1000.0,
        "vendas_ads": 800.0,
        "saldo_ads": 120.0,
        "impressoes": 5000,
        "invest_afiliados": 40.0,
        "invest_ads": 100.0,
        "pct": 7.0,  # (40 + 100) / 2000
        "vendas": 2000.0,
        "cliques_afiliados": 400,
        "pedidos_afiliados": 10,
        "conversao_afiliados": 2.5,  # 10 / 400
        "cliques_ads": 10,
        "pedidos_ads": 3,
        "conversao_ads": 30.0,
    }
    # Mala não se divide: air fryer dentro da Inova não vira Eletro.
    assert "Inova" not in [x["conta"] for x in _grupo(rel, "eletro")["linhas"]]


def test_celular_e_total_menos_eletro_e_eletro_so_os_itens():
    rel = relatorio_exemplo()
    cel = _linha(rel, "celular", "Barbosa")["semanas"][0]
    ele = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    assert ele == {
        "vendas_afiliados": 300.0,
        "vendas_ads": 300.0,
        "saldo_ads": None,  # o saldo é da conta inteira
        "impressoes": 2000,
        "invest_afiliados": 15.0,
        "invest_ads": 40.0,
        "pct": round((15 + 40) / 600 * 100, 2),
        "vendas": 600.0,
        "cliques_afiliados": 120,
        "pedidos_afiliados": 1,
        "conversao_afiliados": 0.83,  # 1 / 120
        "cliques_ads": 4,
        "pedidos_ads": 1,
        "conversao_ads": 25.0,
    }
    assert cel == {
        "vendas_afiliados": 700.0,
        "vendas_ads": 500.0,  # 800 − 300 (o anúncio da loja fica em Celular)
        "saldo_ads": 250.0,
        "impressoes": 3000,
        "invest_afiliados": 25.0,
        "invest_ads": 60.0,
        "pct": round((25 + 60) / 1400 * 100, 2),
        "vendas": 1400.0,
        "cliques_afiliados": 280,  # 400 − 120
        "pedidos_afiliados": 9,  # 10 − 1
        "conversao_afiliados": 3.21,  # 9 / 280
        "cliques_ads": 6,  # 10 − 4 (o anúncio da loja fica em Celular)
        "pedidos_ads": 2,
        "conversao_ads": 33.33,
    }


def test_secao_vazia_continua_vazia_e_sem_itens_fica_em_celular():
    semanas = [
        semana(0, ads=None, ads_itens=None, afiliados_itens=None,
               vendas_itens=_VENDAS_ITENS),
        *[semana(i, afiliados_itens=_AF_ITENS) for i in (1, 2, 3)],
    ]
    rel = montar_relatorio(
        execucao(), [coleta("Barbosa", "celular", dados(semanas), status="parcial")],
        gerado_em=GERADO,
    )
    cel = _linha(rel, "celular", "Barbosa")
    ele = _linha(rel, "eletro", "Barbosa")
    assert cel["status"] == "parcial"
    s0c, s0e = cel["semanas"][0], ele["semanas"][0]
    # Ads falhou: vazio nos dois lados.
    assert s0c["vendas_ads"] is None and s0e["vendas_ads"] is None
    assert s0c["impressoes"] is None and s0e["invest_ads"] is None
    # Afiliados vieram sem os itens: não dá para separar → tudo em Celular.
    assert s0c["vendas_afiliados"] == 1000.0
    assert s0e["vendas_afiliados"] is None
    assert any("sem os itens de afiliados" in a for a in cel["avisos"])
    # Vendas vieram com itens: separa normalmente.
    assert (s0c["vendas"], s0e["vendas"]) == (1400.0, 600.0)
    # % com um investimento vazio: o outro conta sozinho.
    assert s0c["pct"] == round(40.0 / 1400 * 100, 2)


def test_secao_que_falhou_com_itens_que_vieram_fica_vazia_nos_dois_lados():
    """Ads (get_time_graph) falhou e os anúncios (homepage/query) vieram — são
    chamadas separadas no executor. Antes a parte eletro saía dos itens e
    Eletro e Geral ganhavam um Ads pela metade (sem a parte de Celular)."""
    semanas = [
        semana(0, ads=None, ads_itens=(("1", "Air Fryer", 900, 90.0, 900.0),
                                       ("2", "Capa", 100, 10.0, 100.0)),
               afiliados_itens=_AF_ITENS, vendas_itens=_VENDAS_ITENS),
        *[semana(i, afiliados_itens=_AF_ITENS, ads_itens=_ADS_ITENS,
                 vendas_itens=_VENDAS_ITENS) for i in (1, 2, 3)],
    ]
    rel = montar_relatorio(
        execucao(), [coleta("Barbosa", "celular", dados(semanas), status="parcial")],
        gerado_em=GERADO,
    )
    s0c = _linha(rel, "celular", "Barbosa")["semanas"][0]
    s0e = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    for chave in ("vendas_ads", "invest_ads", "impressoes"):
        assert s0c[chave] is None and s0e[chave] is None, chave
        assert _grupo(rel, "eletro")["total"]["semanas"][0][chave] is None, chave
        assert rel["geral"]["semanas"][0][chave] is None, chave
    # As outras seções separam normalmente.
    assert (s0c["vendas_afiliados"], s0e["vendas_afiliados"]) == (700.0, 300.0)
    # E nas semanas em que o Ads veio, a parte eletro continua lá.
    assert _linha(rel, "eletro", "Barbosa")["semanas"][1]["vendas_ads"] == 300.0


def test_dados_tortos_nao_derrubam_o_relatorio():
    """Executor com defeito (a rota recusa com 422, mas o que já está guardado
    é recalculado): item ou semana que não é objeto, lista que não é lista,
    avisos que não é lista e nome que não é texto são ignorados."""
    semanas = [semana(i, afiliados_itens=_AF_ITENS, ads_itens=_ADS_ITENS) for i in range(4)]
    semanas[0]["afiliados_itens"].append("item-que-nao-e-objeto")
    semanas[0]["ads_itens"].append({"item_id": "77", "nome": 123, "gasto": 1.0,
                                    "vendas": 2.0, "impressoes": 3})
    semanas[1]["vendas_itens"] = 7
    semanas[2]["avisos"] = 5
    d = dados(semanas)
    d["semanas"].append("semana-que-nao-e-objeto")
    d["avisos"] = 3
    d["login"] = {"username": 42}
    rel = montar_relatorio(execucao(), [coleta("Barbosa", "celular", d, status="parcial")],
                           gerado_em=GERADO)
    lin = _linha(rel, "celular", "Barbosa")
    assert lin["usuario"] == "42"
    assert lin["semanas"][0]["vendas_afiliados"] == 700.0
    assert any("sem os itens de vendas" in a for a in lin["avisos"])
    d2 = dados(_quatro())
    d2["semanas"] = 9
    rel = montar_relatorio(execucao(), [coleta("Bia", "celular", d2)], gerado_em=GERADO)
    assert _linha(rel, "celular", "Bia")["semanas"][0]["vendas"] is None


def test_conta_so_entra_em_eletro_com_algum_valor_eletro():
    rel = relatorio_exemplo()
    assert [x["conta"] for x in _grupo(rel, "eletro")["linhas"]] == ["Barbosa"]
    # Eletro só na semana mais antiga já basta (S1 fica com zeros).
    semanas = [semana(i) for i in range(3)] + [
        semana(3, afiliados_itens=(("9", "Cafeteira", None, 50.0, 2.0),))
    ]
    rel = montar_relatorio(execucao(), [coleta("Mini", "celular", dados(semanas))],
                           gerado_em=GERADO)
    ele = _linha(rel, "eletro", "Mini")
    assert ele["semanas"][3]["vendas_afiliados"] == 50.0  # pelo título
    assert ele["semanas"][0]["vendas_afiliados"] == 0.0
    assert ele["semanas"][0]["vendas"] == 0.0
    assert _linha(rel, "celular", "Mini")["semanas"][3]["vendas_afiliados"] == 950.0


def test_vinculo_do_davinci_manda_na_divisao_e_gera_divergencia():
    semanas = _quatro(afiliados_itens=_AF_ITENS, ads_itens=_ADS_ITENS,
                      vendas_itens=_VENDAS_ITENS)
    rel = montar_relatorio(
        execucao(),
        [coleta("Barbosa", "celular", dados(semanas))],
        # O DaVinci diz: o item 1 (categoria Eletrodomésticos) NÃO é eletro;
        # o 2 (Celulares) é.
        mapa_davinci={"barbosa": {"1": False, "2": True}},
        gerado_em=GERADO,
    )
    ele = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    assert ele["vendas_afiliados"] == 700.0
    assert ele["vendas"] == 1400.0
    assert rel["divergencias"] == [
        {"conta": "Barbosa", "item_id": "1", "nome": "Air Fryer 5L", "davinci": "outro",
         "categoria_shopee": 100010},
        {"conta": "Barbosa", "item_id": "2", "nome": "Capinha", "davinci": "eletro",
         "categoria_shopee": 100013},
    ]
    # A chave da conta vem da coleta, em minúsculas e sem espaço: com o item 1
    # fora do eletro e o 2 de celular pela categoria, a Barbosa sai de Eletro.
    rel2 = montar_relatorio(
        execucao(), [coleta("Barbosa", "celular", dados(semanas), chave=" BARBOSA ")],
        mapa_davinci={"barbosa": {"1": False}}, gerado_em=GERADO,
    )
    assert _grupo(rel2, "eletro")["linhas"] == []
    assert _linha(rel2, "celular", "Barbosa")["semanas"][0]["vendas_afiliados"] == 1000.0


def test_ads_sem_item_fica_em_celular_e_aparece_na_lista():
    rel = relatorio_exemplo()
    assert rel["nao_atribuido_ads"] == [{"conta": "Barbosa", "gasto": 10.0}]


# ───────────────────────────────────────────────────────────── totais


def test_total_do_grupo_soma_e_o_pct_sai_das_somas():
    rel = relatorio_exemplo()
    cel = _grupo(rel, "celular")
    assert cel["total"]["contas"] == 3  # Barbosa, atv, Luno
    assert cel["total"]["sem_dados"] == 1  # Luno
    t0 = cel["total"]["semanas"][0]
    # Barbosa (celular) + atv; Luno é vazio (vazio + x = x).
    assert t0["vendas"] == 1400.0 + 2000.0
    assert t0["invest_ads"] == 60.0 + 100.0
    assert t0["saldo_ads"] == 250.0  # atv sem saldo
    assert t0["pct"] == round((25 + 60 + 40 + 100) / 3400 * 100, 2)
    media_dos_pcts = (
        _linha(rel, "celular", "Barbosa")["semanas"][0]["pct"]
        + _linha(rel, "celular", "atv")["semanas"][0]["pct"]
    ) / 2
    assert t0["pct"] != round(media_dos_pcts, 2)
    # Grupo inteiro vazio: tudo vazio, não zero.
    rel = montar_relatorio(execucao(), [coleta("Luno", "celular", None, status="erro")],
                           gerado_em=GERADO)
    assert _grupo(rel, "mala")["total"] == {
        "contas": 0, "sem_dados": 0, "semanas": [dict.fromkeys(calculo.CHAVES)] * 4,
    }
    assert _grupo(rel, "eletro")["linhas"] == []


def test_geral_soma_os_tres_grupos_e_conta_cada_loja_uma_vez():
    rel = relatorio_exemplo()
    g0 = rel["geral"]["semanas"][0]
    somas = [_grupo(rel, g)["total"]["semanas"][0]["vendas"] for g in ("mala", "celular",
                                                                         "eletro")]
    assert g0["vendas"] == sum(somas) == 2000.0 + 3400.0 + 600.0
    assert g0["impressoes"] == 5000 + 3000 + 5000 + 2000
    inv = 140.0 + (25 + 60 + 40 + 100) + (15 + 40)
    assert g0["pct"] == round(inv / g0["vendas"] * 100, 2)
    # Barbosa está em Celular e em Eletro: conta uma vez só.
    assert rel["geral"]["contas"] == 4
    assert rel["geral"]["sem_dados"] == 1


def test_linhas_em_ordem_alfabetica_sem_diferenciar_maiuscula_nem_acento():
    rel = relatorio_exemplo()
    assert [x["conta"] for x in _grupo(rel, "celular")["linhas"]] == ["atv", "Barbosa", "Luno"]
    rel = montar_relatorio(
        execucao(),
        [coleta(n, "mala", dados(_quatro())) for n in ("Zeta", "Évora", "beta", "Alfa")],
        gerado_em=GERADO,
    )
    assert [x["conta"] for x in _grupo(rel, "mala")["linhas"]] == [
        "Alfa", "beta", "Évora", "Zeta",
    ]


def test_conversao_do_grupo_sai_das_somas_de_pedidos_e_cliques():
    rel = relatorio_exemplo()
    t0 = _grupo(rel, "celular")["total"]["semanas"][0]
    # Barbosa (celular) 280 cliques / 9 pedidos + atv 400 / 10; Luno vazio.
    assert (t0["cliques_afiliados"], t0["pedidos_afiliados"]) == (680, 19)
    assert t0["conversao_afiliados"] == 2.79  # 19 / 680 — não a média de 3,21% e 2,5%
    assert (t0["cliques_ads"], t0["pedidos_ads"], t0["conversao_ads"]) == (16, 5, 31.25)
    g0 = rel["geral"]["semanas"][0]
    # Geral = Mala + Celular + Eletro = as contas inteiras (400 × 3 lojas com dados).
    assert (g0["cliques_afiliados"], g0["pedidos_afiliados"]) == (1200, 30)
    assert g0["conversao_afiliados"] == 2.5
    assert (g0["cliques_ads"], g0["pedidos_ads"], g0["conversao_ads"]) == (30, 9, 30.0)


def test_coleta_antiga_sem_cliques_de_afiliados_fica_vazia_nunca_zero():
    """Coleta de antes de 07/10/2026 (executor sem `afiliados.cliques` nem
    `afiliados_itens[].cliques`): cliques e conversão de afiliados vazios na
    linha, em Eletro, nos totais e no Geral; o resto (pedidos, Ads) segue."""
    antigos = tuple(it[:5] + (None,) for it in _AF_ITENS)
    semanas = _quatro(afiliados=(1000.0, 40.0, 10, None), afiliados_itens=antigos,
                      ads_itens=_ADS_ITENS)
    rel = montar_relatorio(execucao(), [coleta("Barbosa", "celular", dados(semanas))],
                           gerado_em=GERADO)
    cel = _linha(rel, "celular", "Barbosa")
    ele = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    for s0 in (cel["semanas"][0], ele, _grupo(rel, "celular")["total"]["semanas"][0],
               rel["geral"]["semanas"][0]):
        assert s0["cliques_afiliados"] is None and s0["conversao_afiliados"] is None, s0
    assert (cel["semanas"][0]["pedidos_afiliados"], ele["pedidos_afiliados"]) == (9, 1)
    assert (cel["semanas"][0]["conversao_ads"], ele["conversao_ads"]) == (33.33, 25.0)
    # Sem total não há o que separar: nenhum aviso de "sem os cliques por produto".
    assert not any("cliques" in a for a in cel["avisos"])
    # O relatório congelado de antes nem tem as chaves: o cálculo das variações aguenta.
    velho = {k: v for k, v in cel["semanas"][0].items() if k not in calculo.CHAVES[8:]}
    v_ant, v_media = calculo.variacoes([velho, velho], "conversao_ads")
    assert (v_ant["texto"], v_media["texto"]) == ("—", "—")


def test_item_eletro_sem_cliques_deixa_o_par_inteiro_em_celular_com_aviso():
    """Total de cliques veio, mas o item eletro não trouxe os dele: não dá para
    separar — os cliques E os pedidos inteiros ficam em Celular e Eletro fica
    vazio (nunca 0). Separar só os pedidos deixaria Celular com os cliques de
    eletro sem os pedidos deles (conversão baixa demais) e Eletro com pedidos
    sem cliques (inflaria a conversão do grupo Eletro)."""
    itens = (("1", "Air Fryer 5L", 100010, 300.0, 15.0, None, 1),
             ("2", "Capinha", 100013, 700.0, 25.0, 280, 9))
    semanas = _quatro(afiliados_itens=itens)
    rel = montar_relatorio(execucao(), [coleta("Barbosa", "celular", dados(semanas))],
                           gerado_em=GERADO)
    cel = _linha(rel, "celular", "Barbosa")
    ele = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    c0 = cel["semanas"][0]
    assert (c0["cliques_afiliados"], c0["pedidos_afiliados"], c0["conversao_afiliados"]) == (
        400, 10, 2.5)  # a conta inteira: 10 / 400
    assert (ele["cliques_afiliados"], ele["pedidos_afiliados"], ele["conversao_afiliados"]) == (
        None, None, None)
    # O resto do eletro (vendas, comissão) separa normalmente.
    assert (c0["vendas_afiliados"], ele["vendas_afiliados"]) == (700.0, 300.0)
    assert ("28/09–04/10: sem os cliques por produto de afiliados — os cliques e os pedidos "
            "de eletro dessa parte ficaram em Celular") in cel["avisos"]
    # Item que não é eletro sem o campo também: os cliques de eletro saem da
    # proporção dos itens, e sem um deles a proporção fica torta → não divide.
    itens2 = (("1", "Air Fryer 5L", 100010, 300.0, 15.0, 120),
              ("2", "Capinha", 100013, 700.0, 25.0, None, 9))
    rel2 = montar_relatorio(
        execucao(), [coleta("Barbosa", "celular", dados(_quatro(afiliados_itens=itens2)))],
        gerado_em=GERADO,
    )
    e2 = _linha(rel2, "eletro", "Barbosa")["semanas"][0]
    c2 = _linha(rel2, "celular", "Barbosa")["semanas"][0]
    assert (e2["cliques_afiliados"], e2["pedidos_afiliados"]) == (None, None)
    assert (c2["cliques_afiliados"], c2["pedidos_afiliados"]) == (400, 10)


def test_item_eletro_de_ads_sem_pedidos_leva_os_cliques_junto_para_celular():
    ads_itens = (("1", "Air Fryer anúncio", 2000, 40.0, 300.0, 4, None),
                 ("2", "Capinha", 2500, 50.0, 450.0, 5, 2))
    rel = montar_relatorio(
        execucao(),
        [coleta("Barbosa", "celular", dados(_quatro(afiliados_itens=_AF_ITENS,
                                                    ads_itens=ads_itens)))],
        gerado_em=GERADO,
    )
    c0 = _linha(rel, "celular", "Barbosa")["semanas"][0]
    e0 = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    assert (c0["cliques_ads"], c0["pedidos_ads"], c0["conversao_ads"]) == (10, 3, 30.0)
    assert (e0["cliques_ads"], e0["pedidos_ads"], e0["conversao_ads"]) == (None, None, None)
    # Os cliques de afiliados (outro par) seguem divididos.
    assert (c0["cliques_afiliados"], e0["cliques_afiliados"]) == (280, 120)


def test_cliques_de_afiliados_que_nao_batem_com_o_total_sao_divididos_na_proporcao():
    """Forma da Barbosa (06/10/2026): o total de cliques do seller_daily é
    MENOR que a soma dos cliques por produto do seller_item_detail (são contas
    diferentes da Shopee); os pedidos batem. Celular = total − Σeletro daria um
    número errado (e negativo quando o eletro pesa mais): o total é dividido na
    proporção dos produtos e Celular + Eletro = total, sempre."""
    # Total 1.000 cliques e 20 pedidos; produtos: 820 (eletro) + 180 + 140 = 1.140.
    itens = (("1", "Air Fryer 5L", 100010, 300.0, 15.0, 820, 8),
             ("2", "Capinha", 100013, 400.0, 15.0, 180, 7),
             ("3", "Película", 100013, 300.0, 10.0, 140, 5))
    semanas = _quatro(afiliados=(1000.0, 40.0, 20, 1000), afiliados_itens=itens)
    rel = montar_relatorio(execucao(), [coleta("Barbosa", "celular", dados(semanas))],
                           gerado_em=GERADO)
    cel = _linha(rel, "celular", "Barbosa")
    for i in range(4):
        c, e = cel["semanas"][i], _linha(rel, "eletro", "Barbosa")["semanas"][i]
        assert c["cliques_afiliados"] + e["cliques_afiliados"] == 1000
    c0 = cel["semanas"][0]
    e0 = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    # 1.000 × 820 ÷ 1.140 = 719,3 → 719; Celular fica com o resto (281), não 180.
    assert (e0["cliques_afiliados"], e0["pedidos_afiliados"], e0["conversao_afiliados"]) == (
        719, 8, 1.11)
    assert (c0["cliques_afiliados"], c0["pedidos_afiliados"], c0["conversao_afiliados"]) == (
        281, 12, 4.27)
    g0 = rel["geral"]["semanas"][0]
    assert (g0["cliques_afiliados"], g0["pedidos_afiliados"], g0["conversao_afiliados"]) == (
        1000, 20, 2.0)
    assert cel["avisos"] == []

    # Eletro com mais cliques por produto que o total da loja inteira: com a
    # subtração Celular sairia −200 e a conversão "-x%". Na proporção, não.
    itens2 = (("1", "Air Fryer 5L", 100010, 300.0, 15.0, 1200, 8),
              ("2", "Capinha", 100013, 700.0, 25.0, 300, 12))
    rel2 = montar_relatorio(
        execucao(),
        [coleta("Barbosa", "celular",
                dados(_quatro(afiliados=(1000.0, 40.0, 20, 1000), afiliados_itens=itens2)))],
        gerado_em=GERADO,
    )
    c2 = _linha(rel2, "celular", "Barbosa")["semanas"][0]
    e2 = _linha(rel2, "eletro", "Barbosa")["semanas"][0]
    assert (e2["cliques_afiliados"], c2["cliques_afiliados"]) == (800, 200)  # 1.200 ÷ 1.500
    assert (c2["conversao_afiliados"], e2["conversao_afiliados"]) == (6.0, 1.0)
    for g in rel2["grupos"]:
        for t in [g["total"]["semanas"][0], *(lin["semanas"][0] for lin in g["linhas"])]:
            assert (t["cliques_afiliados"] or 0) >= 0 and (t["conversao_afiliados"] or 0) >= 0


def test_rateio_dos_cliques():
    r = calculo._rateio
    assert r(1000, [820], [820, 180, 140]) == 719.0
    assert r(1000, [], [180, 140]) == 0.0  # sem item eletro
    assert r(1000, [0], [0, 0]) == 0.0  # produtos sem clique nenhum
    assert r(1000, [5], [5, None]) is None  # um produto sem o número: desconhecida
    assert r(None, [5], [5, 5]) is None
    assert r(3, [1], [2]) == 2.0  # 1,5 → 2 (meio para cima); Celular fica com 1
    assert r(1000, [3000], [3000]) == 1000.0  # nunca passa do total


def test_celular_negativo_fica_vazio_com_aviso_e_eletro_com_o_total():
    """Fontes que não batem em outra métrica (aqui Vendas e os cliques de
    Ads): a parte eletro passou do total da conta. Celular nunca fica
    negativo: vazio ("—") e aviso; Eletro fica com o total (o Geral continua
    o total da conta) e nenhuma conversão sai negativa."""
    ads_itens = (("1", "Air Fryer anúncio", 2000, 40.0, 300.0, 15, 1),
                 ("2", "Capinha", 2500, 50.0, 450.0, 5, 2))
    semanas = _quatro(afiliados_itens=_AF_ITENS, ads_itens=ads_itens,
                      vendas_itens=(("1", "Air Fryer 5L", 2300.0), ("2", "Capinha", 100.0)))
    rel = montar_relatorio(execucao(), [coleta("Barbosa", "celular", dados(semanas))],
                           gerado_em=GERADO)
    cel = _linha(rel, "celular", "Barbosa")
    c0 = cel["semanas"][0]
    e0 = _linha(rel, "eletro", "Barbosa")["semanas"][0]
    assert (c0["vendas"], e0["vendas"]) == (None, 2000.0)
    assert c0["pct"] is None
    assert (c0["cliques_ads"], c0["pedidos_ads"], c0["conversao_ads"]) == (None, 2, None)
    assert (e0["cliques_ads"], e0["pedidos_ads"], e0["conversao_ads"]) == (10, 1, 10.0)
    assert ("28/09–04/10: Vendas de eletro passou do total da conta — Celular ficou sem esse "
            "número e Eletro com o total") in cel["avisos"]
    assert ("28/09–04/10: Cliques Ads de eletro passou do total da conta — Celular ficou sem "
            "esse número e Eletro com o total") in cel["avisos"]
    g0 = rel["geral"]["semanas"][0]
    assert (g0["vendas"], g0["cliques_ads"]) == (2000.0, 10)
    # Conversão do Celular (grupo): a linha sem cliques não entra; Geral: só os pares.
    assert _grupo(rel, "celular")["total"]["semanas"][0]["conversao_ads"] is None
    assert g0["conversao_ads"] == 10.0  # 1 ÷ 10 (o Celular sem cliques ficou de fora)
    # Total da conta negativo SEM parte eletro não é "eletro passou do total": fica
    # em Celular como veio (igual à Mala), sem aviso.
    neg = _quatro(afiliados=(1000.0, -5.0, 10), afiliados_itens=(
        ("2", "Capinha", 100013, 1000.0, -5.0, 400, 10),))
    rel3 = montar_relatorio(execucao(), [coleta("X", "celular", dados(neg))], gerado_em=GERADO)
    lin3 = _linha(rel3, "celular", "X")
    assert lin3["semanas"][0]["invest_afiliados"] == -5.0 and lin3["avisos"] == []


def test_conversao_do_total_so_com_as_linhas_que_tem_pedidos_e_cliques():
    """Uma conta veio sem os cliques de afiliados (coleta antiga) e outra
    com: a conversão do grupo e do Geral usa só a conta com os DOIS números —
    os pedidos da primeira sem os cliques dela inflariam a conversão. Os totais
    de pedidos e cliques continuam a soma de tudo o que veio."""
    antiga = _quatro(afiliados=(1000.0, 40.0, 30, None))
    nova = _quatro(afiliados=(1000.0, 40.0, 10, 500))
    rel = montar_relatorio(
        execucao(),
        [coleta("A", "celular", dados(antiga)), coleta("B", "celular", dados(nova))],
        gerado_em=GERADO,
    )
    t0 = _grupo(rel, "celular")["total"]["semanas"][0]
    assert (t0["pedidos_afiliados"], t0["cliques_afiliados"]) == (40, 500)
    assert t0["conversao_afiliados"] == 2.0  # 10 ÷ 500 — não 40 ÷ 500 = 8%
    assert rel["geral"]["semanas"][0]["conversao_afiliados"] == 2.0
    assert _linha(rel, "celular", "A")["semanas"][0]["conversao_afiliados"] is None


def test_conversao_sem_cliques_ou_sem_pedidos_e_vazia():
    assert calculo.conversao_de(5, 0) is None
    assert calculo.conversao_de(5, -10) is None  # nunca conversão negativa
    assert calculo.conversao_de(-5, 10) is None
    assert calculo.conversao_de(None, 10) is None
    assert calculo.conversao_de(5, None) is None
    assert calculo.conversao_de(0, 10) == 0.0
    assert calculo.conversao_de(3, 7) == pytest.approx(42.857142857)
    semanas = _quatro(afiliados=(1000.0, 40.0, 10, 0), ads=(5000, 100.0, 800.0, 0, 0))
    rel = montar_relatorio(execucao(), [coleta("X", "mala", dados(semanas))], gerado_em=GERADO)
    s0 = _linha(rel, "mala", "X")["semanas"][0]
    assert (s0["cliques_afiliados"], s0["conversao_afiliados"]) == (0, None)
    assert (s0["cliques_ads"], s0["pedidos_ads"], s0["conversao_ads"]) == (0, 0, None)


def test_media3_da_conversao_e_das_somas():
    semanas = [
        {"pedidos_ads": 9, "cliques_ads": 100, "conversao_ads": 9.0},
        {"pedidos_ads": 1, "cliques_ads": 10, "conversao_ads": 10.0},
        {"pedidos_ads": None, "cliques_ads": None, "conversao_ads": None},
        {"pedidos_ads": 1, "cliques_ads": 90, "conversao_ads": 1.11},
    ]
    # Σpedidos ÷ Σcliques de S2..S4 = 2 / 100 — não a média de 10% e 1,11%.
    assert calculo.media3(semanas, "conversao_ads") == pytest.approx(2.0)
    v_ant, v_media = calculo.variacoes(semanas, "conversao_ads")
    assert (v_ant["texto"], v_ant["cor"]) == ("▼ 1,0 p.p.", "vermelho")
    assert (v_media["texto"], v_media["cor"]) == ("▲ 7,0 p.p.", "verde")
    assert calculo.media3(semanas, "conversao_afiliados") is None


# ───────────────────────────────────────────────────────────── saldo


def _leitura(iso: str, valor: float) -> tuple[str, float]:
    return (iso, valor)


def test_saldo_da_semana_passada_regra_do_dia_seguinte():
    fim = date(2026, 9, 27)  # S2; alvo = 28/09
    exato = _leitura("2026-09-28T16:00:00Z", 10.0)
    antes = _leitura("2026-09-27T16:00:00Z", 20.0)
    depois = _leitura("2026-09-29T16:00:00Z", 30.0)
    fora = _leitura("2026-09-30T16:00:00Z", 40.0)
    assert calculo.saldo_da_semana([fora, depois, exato, antes], fim) == 10.0
    # ±1 dia, e no empate fica a mais cedo.
    assert calculo.saldo_da_semana([depois, antes], fim) == 20.0
    assert calculo.saldo_da_semana([depois], fim) == 30.0
    assert calculo.saldo_da_semana([fora], fim) is None
    assert calculo.saldo_da_semana([], fim) is None
    # Duas no mesmo dia: a mais cedo.
    assert calculo.saldo_da_semana(
        [_leitura("2026-09-28T20:00:00Z", 1.0), _leitura("2026-09-28T13:00:00Z", 2.0)], fim
    ) == 2.0
    # O dia é o de Brasília: 02:00 UTC de 29/09 = 23:00 de 28/09 em BRT.
    # Aqui as duas são de 28/09 em Brasília (23:00 e 00:30): fica a mais cedo.
    assert calculo.saldo_da_semana(
        [_leitura("2026-09-29T02:00:00Z", 5.0), _leitura("2026-09-28T03:30:00Z", 6.0)], fim
    ) == 6.0
    assert calculo.saldo_da_semana([_leitura("2026-10-01T02:00:00Z", 7.0)], fim) is None
    assert calculo.saldo_da_semana(
        [(datetime(2026, 9, 29, 2, 0, tzinfo=UTC), 8.0)], fim
    ) == 8.0


def test_saldo_no_relatorio():
    rel = relatorio_exemplo()
    barbosa = _linha(rel, "celular", "Barbosa")["semanas"]
    # S1 = leitura desta coleta; S2 (21/09–27/09) = leitura de 29/09 (fim + 2);
    # S3/S4 sem leitura.
    assert [s["saldo_ads"] for s in barbosa] == [250.0, 400.0, None, None]
    assert all(s["saldo_ads"] is None for s in _linha(rel, "eletro", "Barbosa")["semanas"])
    # Saldo com mais casas do que centavos é arredondado.
    rel = montar_relatorio(execucao(), [coleta("X", "mala", dados(_quatro(), saldo=353.0008))],
                           gerado_em=GERADO)
    assert _linha(rel, "mala", "X")["semanas"][0]["saldo_ads"] == 353.0


# ───────────────────────────────────────────────────────────── avisos


def test_conta_sem_dados_tem_linha_vazia_e_vai_para_a_lista():
    rel = relatorio_exemplo()
    luno = _linha(rel, "celular", "Luno")
    assert luno["status"] == "sem_automacao"
    assert luno["erro"] == "perfil Firefox"
    assert luno["usuario"] is None
    assert luno["semanas"] == [dict.fromkeys(calculo.CHAVES)] * 4
    assert rel["contas_sem_dados"] == [
        {"conta": "Luno", "status": "sem_automacao", "erro": "perfil Firefox"}
    ]
    # Não aparece em Eletro.
    assert "Luno" not in [x["conta"] for x in _grupo(rel, "eletro")["linhas"]]


def test_afiliados_incompletos():
    # `afiliados_ultimo_dia` null = a lista de S1 veio sem nenhum dia (loja
    # sem venda de afiliado na semana): não há o que esperar e não entra na
    # lista — o executor também não espera por ela (contrato §4).
    rel = relatorio_exemplo()
    assert rel["afiliados_incompletos"] == [{"conta": "Barbosa", "ate": "2026-10-03"}]
    rel = montar_relatorio(
        execucao(),
        [coleta("A", "mala", dados(_quatro(), ultimo=None)),
         coleta("B", "mala", dados(_quatro(), ultimo="2026-10-04"))],
        gerado_em=GERADO,
    )
    assert rel["afiliados_incompletos"] == []


def test_avisos_da_coleta_e_da_semana_vao_para_a_linha():
    semanas = [semana(0, avisos=["ads: code 5"]), *[semana(i) for i in (1, 2, 3)]]
    rel = montar_relatorio(
        execucao(),
        [coleta("X", "mala", dados(semanas, avisos=["afiliados só até 03/10"]))],
        gerado_em=GERADO,
    )
    assert _linha(rel, "mala", "X")["avisos"] == [
        "afiliados só até 03/10",
        "28/09–04/10: ads: code 5",
    ]


def test_semana_dos_dados_casa_pela_data():
    # O executor mandou só 3 semanas e fora de ordem: a que falta fica vazia.
    semanas = [semana(2), semana(0), semana(1)]
    rel = montar_relatorio(execucao(), [coleta("X", "mala", dados(semanas))], gerado_em=GERADO)
    s = _linha(rel, "mala", "X")["semanas"]
    assert [x["vendas"] for x in s] == [2000.0, 2000.0, 2000.0, None]


def test_notas_fixas_e_da_parcial():
    rel = relatorio_exemplo()
    texto = " ".join(rel["notas"])
    for trecho in (
        "Vendas afiliados e Vendas Ads contam pedidos feitos; Vendas conta só os pagos — por "
        "isso podem passar de Vendas",
        "Comissão de afiliados é estimada; a semana mais recente ainda pode mudar",
        "Quem liga e desliga o Ads é o robô de horários (18h–22h); 'pausado' na hora da "
        "coleta é normal",
        "% s/ vendas = (Invest. afiliados + Invest. Ads) ÷ Vendas × 100",
        "Saldo Ads",
        "Fonte:",
    ):
        assert trecho in texto
    assert "Semana parcial" not in texto
    parcial = periodos.semanas("parcial", date(2026, 10, 8))
    rel = montar_relatorio(execucao(tipo="parcial", semanas=parcial,
                                    afiliados_ate="2026-10-07"), [], gerado_em=GERADO)
    assert (
        "Semana parcial: segunda a quarta, comparada com segunda a quarta das semanas anteriores"
        in " ".join(rel["notas"])
    )
    assert rel["semanas"][0]["rotulo"] == "05/10–07/10"


# ───────────────────────────────────────────────────────────── variação


@pytest.mark.parametrize(
    ("atual", "anterior", "tipo", "bom", "chave", "texto", "cor"),
    [
        (112.3, 100.0, "dinheiro", "sobe", None, "▲ 12,3%", "verde"),
        (95.9, 100.0, "dinheiro", "sobe", None, "▼ 4,1%", "vermelho"),
        (12345.0, 1000.0, "dinheiro", "sobe", None, "▲ 1.134,5%", "verde"),
        (150.0, 100.0, "dinheiro", "neutro", None, "▲ 50,0%", "cinza"),
        (80.0, 100.0, "inteiro", "sobe", None, "▼ 20,0%", "vermelho"),
        (50.0, -100.0, "dinheiro", "sobe", None, "▲ 150,0%", "verde"),
        (10.0, 0.0, "dinheiro", "sobe", None, "novo", "verde"),
        (10.0, 0.0, "dinheiro", "neutro", "saldo_ads", "—", "cinza"),
        (-5.0, 0.0, "dinheiro", "sobe", None, "—", "cinza"),
        (0.0, 0.0, "dinheiro", "sobe", None, "=", "cinza"),
        (100.0, 100.0, "dinheiro", "sobe", None, "=", "cinza"),
        (None, 100.0, "dinheiro", "sobe", None, "—", "cinza"),
        (100.0, None, "dinheiro", "sobe", None, "—", "cinza"),
        (9.4, 8.2, "percentual", "desce", "pct", "▲ 1,2 p.p.", "vermelho"),
        (7.4, 8.2, "percentual", "desce", "pct", "▼ 0,8 p.p.", "verde"),
        (8.2, 8.2, "percentual", "desce", "pct", "=", "cinza"),
        (8.2, None, "percentual", "desce", "pct", "—", "cinza"),
        (5.0, 0.0, "percentual", "desce", "pct", "▲ 5,0 p.p.", "vermelho"),
    ],
)
def test_variacao_textos_e_cores(atual, anterior, tipo, bom, chave, texto, cor):
    v = variacao(atual, anterior, tipo, bom, chave)
    assert (v["texto"], v["cor"]) == (texto, cor)


def test_media3_ignora_vazios_e_o_pct_e_das_somas():
    semanas = [
        {"vendas": 500.0, "invest_afiliados": 10.0, "invest_ads": None, "pct": 2.0},
        {"vendas": 100.0, "invest_afiliados": 10.0, "invest_ads": 10.0, "pct": 20.0},
        {"vendas": None, "invest_afiliados": None, "invest_ads": None, "pct": None},
        {"vendas": 300.0, "invest_afiliados": 0.0, "invest_ads": 6.0, "pct": 2.0},
    ]
    assert calculo.media3(semanas, "vendas") == 200.0
    # Σinvest ÷ Σvendas de S2..S4 = 26 / 400 — não a média de 20% e 2%.
    assert calculo.media3(semanas, "pct") == pytest.approx(6.5)
    assert calculo.media3(semanas, "saldo_ads") is None
    assert calculo.media3(semanas[:1], "vendas") is None
    v_ant, v_media = calculo.variacoes(semanas, "vendas")
    assert v_ant["texto"] == "▲ 400,0%"
    assert v_media["texto"] == "▲ 150,0%"


def test_formatos_pt_br():
    assert calculo.dinheiro(1234.5) == "R$ 1.234,50"
    assert calculo.dinheiro(-3.0) == "-R$ 3,00"
    assert calculo.dinheiro(None) == "—"
    assert calculo.inteiro(72388) == "72.388"
    # Meio arredonda para longe do zero sobre o número como se escreve — como
    # a tela e como o Excel mostra a célula (o binário de 6,35 é 6,3499…).
    assert calculo.percentual(8.25) == "8,3%"
    assert calculo.percentual(6.35) == "6,4%"
    assert calculo.percentual(-0.05) == "-0,1%"
    assert calculo.dinheiro(1234567.125) == "R$ 1.234.567,13"
    assert calculo.dinheiro(2.675) == "R$ 2,68"
    assert calculo.percentual(None) == "—"
    assert calculo.dinheiro(1e300).startswith("R$ 1.000")  # sem InvalidOperation


_EMPATES = Path(__file__).resolve().parents[3] / "apps" / "web" / "tests" / (
    "conferencia-arredondamento.json"
)


def test_empates_iguais_aos_da_tela():
    """Os MESMOS casos que tests/conferencia-lib.cjs confere na lib da tela:
    se um lado mudar a regra de arredondamento, o outro quebra."""
    if not _EMPATES.exists():
        pytest.skip("web fora do checkout")
    casos = json.loads(_EMPATES.read_text(encoding="utf-8"))
    assert len(casos["formatos"]) > 20 and len(casos["variacoes"]) > 5
    for v, tipo, texto in casos["formatos"]:
        assert calculo.formatar(v, tipo) == texto, (v, tipo)
    for atual, anterior, tipo, bom, chave, texto, cor in casos["variacoes"]:
        r = variacao(atual, anterior, tipo, bom, chave)
        assert (r["texto"], r["cor"]) == (texto, cor), (atual, anterior, tipo)
    # Variação da planilha: p.p. com 2 casas (variacao(..., casas=2) lá e cá).
    assert casos.get("variacoes_2casas")
    for atual, anterior, tipo, bom, chave, texto, cor in casos["variacoes_2casas"]:
        r = variacao(atual, anterior, tipo, bom, chave, casas=calculo.CASAS_PLANILHA)
        assert (r["texto"], r["cor"]) == (texto, cor), (atual, anterior, tipo)
    # % com 2 casas da planilha do Resumo (07/10/2026): percentual(v, 2) lá e cá.
    for v, texto in casos.get("percentual_2casas") or []:
        assert calculo.percentual(v, 2) == texto, v
        assert calculo.formatar_planilha(v, "percentual") == texto, v


def test_formato_da_planilha():
    """Célula do Resumo: % com 2 casas; dinheiro e inteiro como sempre."""
    assert calculo.formatar_planilha(7.5, "percentual") == "7,50%"
    assert calculo.formatar_planilha(6.125, "percentual") == "6,13%"
    assert calculo.formatar_planilha(None, "percentual") == "—"
    assert calculo.formatar_planilha(1234.5, "dinheiro") == "R$ 1.234,50"
    assert calculo.formatar_planilha(72388, "inteiro") == "72.388"
    # O % de 1 casa (Threema, variação) não mudou.
    assert calculo.percentual(7.5) == "7,5%"


def test_numeros_congelados_com_meio_para_cima():
    """O relatório guarda dinheiro e % com 2 casas pela MESMA regra dos
    formatos (o round() do Python levaria 30,125 a 30,12); o % sai dos
    valores já arredondados — os que a tela, a planilha e quem refaz a conta
    veem."""
    assert calculo._arred("invest_ads", 30.125) == 30.13
    assert calculo._arred("invest_ads", 0.625) == 0.63
    assert calculo._arred("vendas", 2.675) == 2.68
    assert calculo._arred("impressoes", 2.5) == 2  # inteiro: round() do Python
    semanas = [semana(i, afiliados=(500.0, 2.5, 5), ads=(100, 0.625, 10.0), vendas=(37.5, 3))
               for i in range(4)]
    rel = montar_relatorio(execucao(), [coleta("Mega", "mala", dados(semanas))],
                           gerado_em=GERADO)
    s1 = _linha(rel, "mala", "Mega")["semanas"][0]
    assert s1["invest_ads"] == 0.63
    assert s1["pct"] == 8.35  # (2,50 + 0,63) ÷ 37,50 × 100 = 8,3466…
