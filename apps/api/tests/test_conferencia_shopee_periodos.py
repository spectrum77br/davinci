"""Conferência Shopee — as 4 semanas e os prazos de uma rodada.

services/conferencia_shopee/periodos.

A semana vai de domingo a sábado (30/10/2026). Os exemplos do contrato
(docs/conferencia-shopee.md §1): segunda 02/11/2026 semanal → S1 25/10–31/10 …;
quarta 04/11/2026 parcial → S1 01/11–03/11 ….
Prazos em Brasília: espera dos afiliados até 15:00, corte 17:30, prazo =
corte + 30 min; rodada manual depois desses horários ganha folga a partir de
agora.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.conferencia_shopee import periodos
from app.services.conferencia_shopee.periodos import FUSO


def _brt(dia: int, h: int, m: int = 0, mes: int = 10) -> datetime:
    return datetime(2026, mes, dia, h, m, tzinfo=FUSO)


def _datas(semanas: list[dict]) -> list[tuple[str, str]]:
    return [(s["inicio"], s["fim"]) for s in semanas]


def test_segunda_semanal_e_a_semana_fechada_domingo_a_sabado_e_as_3_antes():
    assert _datas(periodos.semanas("semanal", date(2026, 10, 5))) == [
        ("2026-09-27", "2026-10-03"),
        ("2026-09-20", "2026-09-26"),
        ("2026-09-13", "2026-09-19"),
        ("2026-09-06", "2026-09-12"),
    ]


def test_quarta_parcial_e_dom_a_ter_contra_dom_a_ter():
    assert _datas(periodos.semanas("parcial", date(2026, 10, 7))) == [
        ("2026-10-04", "2026-10-06"),
        ("2026-09-27", "2026-09-29"),
        ("2026-09-20", "2026-09-22"),
        ("2026-09-13", "2026-09-15"),
    ]


def test_exemplos_da_doc_primeira_semana_da_regra_nova():
    # docs/conferencia-shopee.md §1: as duas primeiras rodadas depois da troca.
    assert _datas(periodos.semanas("semanal", date(2026, 11, 2))) == [
        ("2026-10-25", "2026-10-31"),
        ("2026-10-18", "2026-10-24"),
        ("2026-10-11", "2026-10-17"),
        ("2026-10-04", "2026-10-10"),
    ]
    assert _datas(periodos.semanas("parcial", date(2026, 11, 4))) == [
        ("2026-11-01", "2026-11-03"),
        ("2026-10-25", "2026-10-27"),
        ("2026-10-18", "2026-10-20"),
        ("2026-10-11", "2026-10-13"),
    ]


def test_domingo_da_semana():
    assert periodos.domingo_da_semana(date(2026, 10, 4)) == date(2026, 10, 4)  # domingo
    assert periodos.domingo_da_semana(date(2026, 10, 5)) == date(2026, 10, 4)  # segunda
    assert periodos.domingo_da_semana(date(2026, 10, 10)) == date(2026, 10, 4)  # sábado
    assert periodos.domingo_da_semana(date(2026, 10, 11)) == date(2026, 10, 11)
    # Virada de mês e de ano.
    assert periodos.domingo_da_semana(date(2026, 11, 2)) == date(2026, 11, 1)
    assert periodos.domingo_da_semana(date(2027, 1, 1)) == date(2026, 12, 27)


def test_parcial_num_domingo_vira_semanal():
    domingo = date(2026, 10, 4)
    assert periodos.tipo_efetivo("parcial", domingo) == "semanal"
    assert periodos.tipo_efetivo("parcial", date(2026, 10, 5)) == "parcial"
    assert _datas(periodos.semanas("parcial", domingo)) == _datas(
        periodos.semanas("semanal", date(2026, 10, 5))
    )
    plano = periodos.planejar("parcial", _brt(4, 20, 30))
    assert plano["tipo"] == "semanal"
    assert plano["semanas"][0] == {"inicio": "2026-09-27", "fim": "2026-10-03"}


def test_parcial_na_segunda_e_so_o_domingo():
    s = periodos.semanas("parcial", date(2026, 10, 5))
    assert s[0] == {"inicio": "2026-10-04", "fim": "2026-10-04"}
    assert s[1] == {"inicio": "2026-09-27", "fim": "2026-09-27"}


def test_semanal_no_sabado_ainda_nao_fechou_a_semana_corrente():
    # Sábado 03/10: a semana 27/09–03/10 ainda está aberta → S1 é a anterior.
    assert periodos.semanas("semanal", date(2026, 10, 3))[0] == {
        "inicio": "2026-09-20",
        "fim": "2026-09-26",
    }
    # Domingo 04/10: a de 27/09–03/10 acabou ontem.
    assert periodos.semanas("semanal", date(2026, 10, 4))[0]["fim"] == "2026-10-03"


def test_parcial_no_sabado_vai_de_domingo_a_sexta():
    s = periodos.semanas("parcial", date(2026, 10, 10))
    assert s[0] == {"inicio": "2026-10-04", "fim": "2026-10-09"}
    assert s[3] == {"inicio": "2026-09-13", "fim": "2026-09-18"}


def test_tipo_da_agenda_so_segunda_e_quarta():
    assert periodos.tipo_da_agenda(date(2026, 10, 5)) == "semanal"
    assert periodos.tipo_da_agenda(date(2026, 10, 7)) == "parcial"
    for dia in (4, 6, 8, 9, 10):
        assert periodos.tipo_da_agenda(date(2026, 10, dia)) is None


def test_tipo_desconhecido_e_erro():
    with pytest.raises(ValueError):
        periodos.semanas("mensal", date(2026, 10, 6))


def test_rotulo_e_dia_da_semana():
    assert periodos.rotulo("2026-09-28", "2026-10-04") == "28/09–04/10"
    assert periodos.rotulo(date(2026, 12, 28), date(2027, 1, 3)) == "28/12–03/01"
    assert periodos.dia_da_semana("2026-10-05") == "segunda"
    assert periodos.dia_da_semana(date(2026, 10, 7)) == "quarta"
    assert periodos.dia_da_semana("2026-10-04") == "domingo"


def test_prazos_da_agenda_13h30():
    p = periodos.prazos(_brt(6, 13, 30))
    assert p["esperar_afiliados_ate"] == _brt(6, 15, 0)
    assert p["corte"] == _brt(6, 17, 30)
    assert p["prazo"] == _brt(6, 18, 0)
    # Guardados em UTC (o timestamptz não liga, mas o JSON do job sai em UTC).
    assert p["corte"].utcoffset() == timedelta(0)
    assert p["corte"] == datetime(2026, 10, 6, 20, 30, tzinfo=UTC)


def test_prazos_de_rodada_manual_tarde():
    # Depois das 15h: espera 30 min a partir de agora; corte continua 17:30.
    p = periodos.prazos(_brt(6, 15, 20))
    assert p["esperar_afiliados_ate"] == _brt(6, 15, 50)
    assert p["corte"] == _brt(6, 17, 30)
    # Exatamente 15:00 já é "depois".
    assert periodos.prazos(_brt(6, 15, 0))["esperar_afiliados_ate"] == _brt(6, 15, 30)
    # À noite: corte = agora + 3 h, prazo = corte + 30 min.
    p = periodos.prazos(_brt(6, 18, 10))
    assert p["esperar_afiliados_ate"] == _brt(6, 18, 40)
    assert p["corte"] == _brt(6, 21, 10)
    assert p["prazo"] == _brt(6, 21, 40)
    assert periodos.prazos(_brt(6, 17, 30))["corte"] == _brt(6, 20, 30)


def test_prazos_usam_o_dia_de_brasilia_nao_o_de_utc():
    # 23:30 BRT de sábado = 02:30 UTC de domingo: o "dia" é sábado, e a semana
    # 27/09–03/10 ainda não fechou.
    t = datetime(2026, 10, 4, 2, 30, tzinfo=UTC)
    p = periodos.prazos(t)
    assert p["corte"] == t + timedelta(hours=3)
    plano = periodos.planejar("semanal", t)
    assert plano["semanas"][0] == {"inicio": "2026-09-20", "fim": "2026-09-26"}


def test_planejar_junta_tudo_e_aceita_hora_sem_fuso_como_utc():
    plano = periodos.planejar("parcial", datetime(2026, 10, 7, 16, 30))
    assert plano["tipo"] == "parcial"
    assert plano["afiliados_ate"] == date(2026, 10, 6)
    assert plano["semanas"][0] == {"inicio": "2026-10-04", "fim": "2026-10-06"}
    assert plano["esperar_afiliados_ate"] == _brt(7, 15, 0)
    assert plano["corte"] == _brt(7, 17, 30)
    assert plano["prazo"] == _brt(7, 18, 0)
