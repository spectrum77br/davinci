"""logistica_datas — o carimbo de "desde quando" de cada campo do Status
Plataforma. Sem HTTP/DB: trava a normalização das datas (cada canal manda num
formato) e a regra de prioridade plataforma → aproximação → change detection.

O que NÃO pode quebrar nunca: campo cujo valor não mudou mantém a data antiga.
Se ela andasse a cada 🔄, o operador perderia justamente a informação que quer
(há 8 dias parado na alfândega)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import logistica_datas as ld


def test_iso_entende_os_formatos_dos_canais():
    # ML manda ISO com offset; Amazon manda com Z; Shopee/TikTok mandam epoch
    # (segundos) e os eventos de tracking do TikTok, milissegundos.
    assert ld.iso("2026-08-12T08:05:00.000-04:00") == "2026-08-12T12:05:00+00:00"
    assert ld.iso("2026-08-12T12:05:00Z") == "2026-08-12T12:05:00+00:00"
    assert ld.iso(1_755_000_000) == datetime.fromtimestamp(1_755_000_000, tz=UTC).isoformat()
    assert ld.iso(1_755_000_000_000) == ld.iso(1_755_000_000)  # millis == segundos
    assert ld.iso("1755000000") == ld.iso(1_755_000_000)  # epoch em string
    assert ld.iso(datetime(2026, 8, 12, 12, 5, tzinfo=UTC)) == "2026-08-12T12:05:00+00:00"
    # Sem data legível não inventa nada — o chamador cai pra fonte seguinte.
    for lixo in (None, "", "ontem", 0, -5, True):
        assert ld.iso(lixo) is None


def test_propor_nao_deixa_estimativa_sobrescrever_data_oficial():
    datas: dict[str, dict[str, str]] = {}
    ld.propor(datas, "ship_status", "2026-08-10T00:00:00Z", ld.FONTE_PLATAFORMA)
    ld.propor(datas, "ship_status", "2026-08-19T00:00:00Z", ld.FONTE_APROX)
    assert datas["ship_status"] == {"em": "2026-08-10T00:00:00+00:00", "fonte": "plataforma"}
    # O contrário vale: a oficial entra por cima da estimativa.
    ld.propor(datas, "ship_substatus", "2026-08-19T00:00:00Z", ld.FONTE_APROX)
    ld.propor(datas, "ship_substatus", "2026-08-10T00:00:00Z", ld.FONTE_PLATAFORMA)
    assert datas["ship_substatus"]["fonte"] == "plataforma"


def test_valor_que_nao_mudou_congela_a_data():
    antes = {"ship_substatus": "confiscated", "order_status": "cancelled"}
    velhas = {
        "ship_substatus": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"},
        "order_status": {"em": "2026-08-12T09:00:00+00:00", "fonte": "plataforma"},
    }
    # Mesmíssimos valores 8 dias depois, e o canal só sabe dizer "mexi agora".
    out = ld.merge_datas(
        anterior_status=antes,
        novo_status=dict(antes),
        propostas={"ship_substatus": {"em": "2026-08-20T10:00:00+00:00", "fonte": "aprox"}},
        anteriores=velhas,
    )
    assert out["ship_substatus"] == velhas["ship_substatus"]  # não andou
    assert out["order_status"] == velhas["order_status"]


def test_data_oficial_entra_mesmo_sem_o_valor_mudar():
    # Primeiro carimbo foi estimado; depois o canal passou a datar o campo.
    out = ld.merge_datas(
        anterior_status={"ship_status": "shipped"},
        novo_status={"ship_status": "shipped"},
        propostas={"ship_status": {"em": "2026-08-11T19:22:00+00:00", "fonte": "plataforma"}},
        anteriores={"ship_status": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"}},
    )
    assert out["ship_status"] == {"em": "2026-08-11T19:22:00+00:00", "fonte": "plataforma"}


def test_valor_mudou_usa_a_estimativa_do_canal():
    out = ld.merge_datas(
        anterior_status={"ship_substatus": "delayed"},
        novo_status={"ship_substatus": "confiscated"},
        propostas={"ship_substatus": {"em": "2026-08-18T03:11:00+00:00", "fonte": "aprox"}},
        anteriores={"ship_substatus": {"em": "2026-08-01T00:00:00+00:00", "fonte": "aprox"}},
    )
    assert out["ship_substatus"] == {"em": "2026-08-18T03:11:00+00:00", "fonte": "aprox"}


def test_sem_data_nenhuma_carimba_agora_como_davinci():
    agora = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
    out = ld.merge_datas(
        anterior_status={},
        novo_status={"order_status": "cancelled"},
        propostas={},
        anteriores={},
        agora=agora,
    )
    assert out["order_status"] == {"em": agora.isoformat(), "fonte": "davinci"}


def test_campo_que_saiu_da_assinatura_perde_o_carimbo():
    out = ld.merge_datas(
        anterior_status={"order_status": "paid", "ship_substatus": "delayed"},
        novo_status={"order_status": "paid"},
        anteriores={
            "order_status": {"em": "2026-08-01T00:00:00+00:00", "fonte": "plataforma"},
            "ship_substatus": {"em": "2026-08-02T00:00:00+00:00", "fonte": "aprox"},
        },
    )
    assert set(out) == {"order_status"}


def test_aplicar_le_a_linha_antes_da_troca():
    class LinhaFake:
        meli_status = {"ship_status": "shipped", "ship_substatus": "delayed"}
        status_datas = {
            "ship_status": {"em": "2026-08-10T00:00:00+00:00", "fonte": "plataforma"},
            "ship_substatus": {"em": "2026-08-10T00:00:00+00:00", "fonte": "aprox"},
        }

    agora = datetime.now(tz=UTC) - timedelta(seconds=1)
    novo = ld.aplicar(
        LinhaFake(),
        {"ship_status": "shipped", "ship_substatus": "confiscated"},
        {},
        agora=agora,
    )
    # ship_status não mudou → congelado; substatus mudou e o canal não datou →
    # carimba o instante em que o DaVinci viu.
    assert novo["ship_status"]["em"] == "2026-08-10T00:00:00+00:00"
    assert novo["ship_substatus"] == {"em": agora.isoformat(), "fonte": "davinci"}
