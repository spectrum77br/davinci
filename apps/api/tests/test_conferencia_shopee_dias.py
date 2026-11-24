"""Conferência Shopee — o dia a dia de afiliados e de Ads (M-36 item 6, 24/11/2026).

O executor do Mac passa a mandar, em cada semana, `dias` = [{dia, afiliados,
ads}] (o investimento de cada dia: comissão de afiliados e gasto de Ads, R$;
null = a Shopee não devolveu o dia) e `dias_motivos`. Quem lê é o Faturamento
(Tabela de Preços). Aqui: o dia a dia chega inteiro em
ConferenciaShopeeColeta.dados.semanas[]; dia a dia torto é jogado fora sem
recusar a loja (422 derrubaria a Conferência); lado que não fecha com o total
da semana vira null; e o relatório da Conferência é o MESMO com e sem o dia a
dia.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from app.models import ConferenciaShopeeExecucao
from app.services.conferencia_shopee import periodos, saida
from app.services.conferencia_shopee.calculo import montar_relatorio
from app.services.conferencia_shopee.dados import confere_coleta_dados, limpar_dias
from tests.test_conferencia_shopee_api import (  # noqa: F401 (fixtures autouse)
    AGENTE,
    API,
    UMA,
    _execucao,
    _lease,
    _monta_router,
    _relogio_e_token,
)
from tests.test_conferencia_shopee_calculo import GERADO, coleta, dados, execucao, semana
from tests.test_conferencia_shopee_fila import coletas_de, dados_loja


def _dias(s: dict, afiliados: list, ads: list) -> list[dict]:
    """Os 7 dias da semana `s` com os valores na ordem."""
    inicio = date.fromisoformat(s["inicio"])
    return [
        {"dia": date.fromordinal(inicio.toordinal() + i).isoformat(), "afiliados": a, "ads": b}
        for i, (a, b) in enumerate(zip(afiliados, ads, strict=True))
    ]


# dados_loja: comissão de afiliados R$ 40 e gasto de Ads R$ 60 em toda semana.
AF_40 = [0.0, 0.0, 10.0, 10.0, 10.0, 10.0, 0.0]
ADS_60 = [10.0, 10.0, 10.0, 10.0, 10.0, 9.99, 0.01]


def _com_dias(semanas: list[dict]) -> dict:
    d = dados_loja(semanas)
    for s in d["semanas"]:
        s["dias"] = _dias(s, AF_40, ADS_60)
        s["dias_motivos"] = []
    return d


# ───────────────────────────────────────────────────────────── limpar_dias


def test_dia_a_dia_certo_passa_inteiro_e_so_com_os_tres_campos():
    sem = periodos.semanas("semanal", date(2026, 10, 5))
    d = _com_dias(sem)
    d["semanas"][0]["dias"][2]["extra"] = "x"  # campo a mais no item sai
    d["semanas"][0]["dias"].reverse()  # fora de ordem volta em ordem
    limpo = limpar_dias(d)
    confere_coleta_dados(limpo)
    s1 = limpo["semanas"][0]
    assert s1["dias"] == _dias(sem[0], AF_40, ADS_60)
    assert s1["dias_motivos"] == []
    # O resto dos dados não mudou.
    for chave in ("afiliados", "ads", "vendas", "afiliados_itens", "ads_itens", "avisos"):
        assert s1[chave] == d["semanas"][0][chave]
    assert {k: v for k, v in limpo.items() if k != "semanas"} == {
        k: v for k, v in d.items() if k != "semanas"
    }


def test_semana_sem_dia_a_dia_fica_como_veio():
    """Coleta de antes de 24/11 (e o ML/Amazon do servidor): sem a chave."""
    sem = periodos.semanas("semanal", date(2026, 10, 5))
    d = dados_loja(sem)
    assert limpar_dias(d) == d
    assert "dias" not in limpar_dias(d)["semanas"][0]
    # dias null (a loja parou antes) também fica.
    d["semanas"][1]["dias"] = None
    assert limpar_dias(d)["semanas"][1]["dias"] is None


@pytest.mark.parametrize(
    ("nome", "mexe", "motivo"),
    [
        ("não é lista", lambda s: s.update(dias={"a": 1}), "não é lista"),
        ("item que não é objeto", lambda s: s["dias"].append(3), "item que não é objeto"),
        ("dia fora da semana", lambda s: s["dias"][0].update(dia="2026-01-01"), "fora da semana"),
        ("dia que não é data", lambda s: s["dias"][0].update(dia="ontem"), "fora da semana"),
        ("dia repetido", lambda s: s["dias"][1].update(dia=s["dias"][0]["dia"]), "repetido"),
        ("texto no lugar de número", lambda s: s["dias"][0].update(ads="10"), "não é número"),
        (
            "verdadeiro no lugar de número",
            lambda s: s["dias"][0].update(afiliados=True),
            "não é número",
        ),
        ("infinito", lambda s: s["dias"][0].update(ads=float("inf")), "não é número"),
    ],
)
def test_dia_a_dia_torto_sai_e_a_semana_continua(nome, mexe, motivo):
    sem = periodos.semanas("semanal", date(2026, 10, 5))
    d = _com_dias(sem)
    mexe(d["semanas"][2])
    limpo = limpar_dias(d)
    confere_coleta_dados(limpo)  # não recusa a loja
    s3 = limpo["semanas"][2]
    assert "dias" not in s3, nome
    assert any(motivo in m for m in s3["dias_motivos"]), (nome, s3["dias_motivos"])
    # As outras semanas e os números da semana ficam.
    assert limpo["semanas"][1]["dias"] == d["semanas"][1]["dias"]
    assert s3["ads"] == d["semanas"][2]["ads"]


def test_lado_que_nao_fecha_com_a_semana_vira_null_e_o_outro_fica():
    """O Faturamento soma os dias achando que dão o Invest. da Conferência."""
    sem = periodos.semanas("semanal", date(2026, 10, 5))
    d = _com_dias(sem)
    d["semanas"][0]["dias"][0]["ads"] = 50.0  # Ads soma R$ 100 ≠ R$ 60
    d["semanas"][1]["afiliados"] = None  # seção falhou e mandou dias de afiliados
    d["semanas"][3]["dias_motivos"] = ["Ads: o gráfico não é por hora (7 horários)", 5]
    limpo = limpar_dias(d)
    confere_coleta_dados(limpo)
    s1, s2, s4 = limpo["semanas"][0], limpo["semanas"][1], limpo["semanas"][3]
    assert [x["ads"] for x in s1["dias"]] == [None] * 7
    assert [x["afiliados"] for x in s1["dias"]] == AF_40
    assert s1["dias_motivos"] == [
        "ads: dia a dia descartado pelo servidor (não fecha com a semana)"
    ]
    assert [x["afiliados"] for x in s2["dias"]] == [None] * 7
    assert [x["ads"] for x in s2["dias"]] == ADS_60
    # Motivo do executor fica (só texto).
    assert s4["dias_motivos"] == ["Ads: o gráfico não é por hora (7 horários)"]


def test_dias_parciais_somam_o_total_do_que_ja_saiu():
    """S1 com o último dia de afiliados ainda não publicado: null nele, e os
    outros somam a comissão (que também ainda não tem esse dia)."""
    sem = periodos.semanas("semanal", date(2026, 10, 5))
    d = _com_dias(sem)
    d["semanas"][0]["dias"][-1]["afiliados"] = None  # era 0
    limpo = limpar_dias(d)
    assert [x["afiliados"] for x in limpo["semanas"][0]["dias"]] == [*AF_40[:6], None]
    assert limpo["semanas"][0]["dias_motivos"] == []


@pytest.mark.parametrize(
    "lixo",
    [
        None,
        3,
        "x",
        [],
        {"semanas": "x"},
        {"semanas": [1, None, {"dias": 5}]},
        {"semanas": [{"inicio": 1, "fim": None, "dias": [{"dia": "2026-10-01"}]}]},
    ],
)
def test_limpar_dias_nunca_levanta(lixo):
    limpar_dias(copy.deepcopy(lixo))


# ───────────────────────────────────────────────────────────── relatório


def test_relatorio_da_conferencia_e_o_mesmo_com_e_sem_dia_a_dia():
    """A tela, o Excel, o CSV, o MD, o HTML e o aviso saem do relatório: o
    dia a dia não pode mexer em nada dele."""
    semanas = [semana(i) for i in range(4)]
    com = copy.deepcopy(semanas)
    for s in com:
        s["dias"] = _dias(s, [0, 0, 10, 10, 10, 10, 0], [10, 10, 20, 20, 20, 10, 10])
        s["dias_motivos"] = ["Ads: motivo qualquer"]
    rel_sem = montar_relatorio(
        execucao(), [coleta("Ana", "celular", dados(semanas))], gerado_em=GERADO
    )
    rel_com = montar_relatorio(execucao(), [coleta("Ana", "celular", dados(com))], gerado_em=GERADO)
    assert rel_com == rel_sem
    assert saida.csv(rel_com) == saida.csv(rel_sem)
    assert saida.markdown(rel_com) == saida.markdown(rel_sem)
    assert "dia a dia" not in saida.markdown(rel_com)


# ───────────────────────────────────────────────────────────── pelo HTTP


async def test_resultado_guarda_o_dia_a_dia_na_coleta(client, db):
    ex = await _execucao(db, UMA)
    job = await _lease(client)
    d = _com_dias(ex.semanas)
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "erro": None, "dados": d},
        headers=AGENTE,
    )
    assert r.status_code == 200, r.text
    (c,) = await coletas_de(db, ex.id)
    assert c.status == "ok"
    for i, s in enumerate(c.dados["semanas"]):
        assert s["dias"] == _dias(ex.semanas[i], AF_40, ADS_60)
        assert s["dias_motivos"] == []
    # A rodada fechou com o relatório de sempre (Investimento = 40 + 60 por semana).
    rel = (await db.get(ConferenciaShopeeExecucao, ex.id, populate_existing=True)).relatorio
    assert rel["geral"]["semanas"][0]["invest_afiliados"] == 40.0
    assert rel["geral"]["semanas"][0]["invest_ads"] == 60.0


async def test_dia_a_dia_torto_nao_recusa_a_loja(client, db):
    """Antes do limpar_dias, um `dias` torto daria 422 e a loja inteira
    fecharia como erro: a Conferência perderia a loja por causa do extra."""
    ex = await _execucao(db, UMA)
    job = await _lease(client)
    d = _com_dias(ex.semanas)
    d["semanas"][0]["dias"][0]["ads"] = "muito"
    d["semanas"][1]["dias"] = "x"
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "erro": None, "dados": d},
        headers=AGENTE,
    )
    assert r.status_code == 200, r.text
    (c,) = await coletas_de(db, ex.id)
    assert c.status == "ok"
    s1, s2, s3 = c.dados["semanas"][:3]
    assert "dias" not in s1 and "dias" not in s2
    assert "não é número" in s1["dias_motivos"][0]
    assert "não é lista" in s2["dias_motivos"][0]
    assert s3["dias"] == _dias(ex.semanas[2], AF_40, ADS_60)
    assert s1["ads"] == d["semanas"][0]["ads"]
