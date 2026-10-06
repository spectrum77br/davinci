"""Conferência Shopee — as 4 semanas e os prazos de uma rodada.

services/conferencia_shopee/periodos.

Os exemplos do contrato (docs/conferencia-shopee.md §1): terça 06/10/2026
semanal → S1 28/09–04/10 …; quinta 08/10/2026 parcial → S1 05/10–07/10 ….
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


def test_terca_semanal_e_a_semana_fechada_e_as_3_antes():
    assert _datas(periodos.semanas("semanal", date(2026, 10, 6))) == [
        ("2026-09-28", "2026-10-04"),
        ("2026-09-21", "2026-09-27"),
        ("2026-09-14", "2026-09-20"),
        ("2026-09-07", "2026-09-13"),
    ]


def test_quinta_parcial_e_seg_a_qua_contra_seg_a_qua():
    assert _datas(periodos.semanas("parcial", date(2026, 10, 8))) == [
        ("2026-10-05", "2026-10-07"),
        ("2026-09-28", "2026-09-30"),
        ("2026-09-21", "2026-09-23"),
        ("2026-09-14", "2026-09-16"),
    ]


def test_parcial_numa_segunda_vira_semanal():
    segunda = date(2026, 10, 5)
    assert periodos.tipo_efetivo("parcial", segunda) == "semanal"
    assert _datas(periodos.semanas("parcial", segunda)) == _datas(
        periodos.semanas("semanal", date(2026, 10, 6))
    )
    plano = periodos.planejar("parcial", _brt(5, 20, 30))
    assert plano["tipo"] == "semanal"
    assert plano["semanas"][0] == {"inicio": "2026-09-28", "fim": "2026-10-04"}


def test_semanal_no_domingo_ainda_nao_fechou_a_semana_corrente():
    # Domingo 04/10: a semana 28/09–04/10 ainda está aberta → S1 é a anterior.
    assert periodos.semanas("semanal", date(2026, 10, 4))[0] == {
        "inicio": "2026-09-21",
        "fim": "2026-09-27",
    }
    # Segunda 05/10: a de 28/09–04/10 acabou ontem.
    assert periodos.semanas("semanal", date(2026, 10, 5))[0]["fim"] == "2026-10-04"


def test_parcial_no_domingo_vai_de_segunda_a_sabado():
    s = periodos.semanas("parcial", date(2026, 10, 11))
    assert s[0] == {"inicio": "2026-10-05", "fim": "2026-10-10"}
    assert s[3] == {"inicio": "2026-09-14", "fim": "2026-09-19"}


def test_tipo_da_agenda_so_terca_e_quinta():
    assert periodos.tipo_da_agenda(date(2026, 10, 6)) == "semanal"
    assert periodos.tipo_da_agenda(date(2026, 10, 8)) == "parcial"
    for dia in (5, 7, 9, 10, 11):
        assert periodos.tipo_da_agenda(date(2026, 10, dia)) is None


def test_tipo_desconhecido_e_erro():
    with pytest.raises(ValueError):
        periodos.semanas("mensal", date(2026, 10, 6))


def test_rotulo_e_dia_da_semana():
    assert periodos.rotulo("2026-09-28", "2026-10-04") == "28/09–04/10"
    assert periodos.rotulo(date(2026, 12, 28), date(2027, 1, 3)) == "28/12–03/01"
    assert periodos.dia_da_semana("2026-10-05") == "segunda"
    assert periodos.dia_da_semana(date(2026, 10, 7)) == "quarta"


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
    # 23:30 BRT de segunda = 02:30 UTC de terça: o "dia" é segunda.
    t = datetime(2026, 10, 6, 2, 30, tzinfo=UTC)
    p = periodos.prazos(t)
    assert p["corte"] == t + timedelta(hours=3)
    plano = periodos.planejar("semanal", t)
    assert plano["semanas"][0] == {"inicio": "2026-09-28", "fim": "2026-10-04"}


def test_planejar_junta_tudo_e_aceita_hora_sem_fuso_como_utc():
    plano = periodos.planejar("parcial", datetime(2026, 10, 8, 16, 30))
    assert plano["tipo"] == "parcial"
    assert plano["afiliados_ate"] == date(2026, 10, 7)
    assert plano["semanas"][0] == {"inicio": "2026-10-05", "fim": "2026-10-07"}
    assert plano["esperar_afiliados_ate"] == _brt(8, 15, 0)
    assert plano["corte"] == _brt(8, 17, 30)
    assert plano["prazo"] == _brt(8, 18, 0)
