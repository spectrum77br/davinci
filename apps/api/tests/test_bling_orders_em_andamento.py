"""Regra de em_andamento_data (data operacional na aba Pedidos).

Decidida em _next_em_andamento_data (services/bling_orders.py):
- transição PARA 15 (agência confirmou AGORA) carimba o DIA DA CONFIRMAÇÃO,
  sobrescrevendo o provisório do 83965 (etiqueta 30 + confirmação 31 => 31);
- já em 15 (re-disparo taxa/endereço) PRESERVA — não empurra pra hoje (bug
  2026-05-28);
- 83965 sem data recebe PROVISÓRIO = dia da etiqueta (pra não flutuar);
- situacao 6 e demais ficam sem data.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.bling_orders import _next_em_andamento_data, _row_from_item


def _hoje_op() -> date:
    from app.services.marketplace_shipment_check import _operational_ship_date
    return _operational_ship_date(datetime.now(UTC))


def _next(nova, antiga, data_existente):
    return _next_em_andamento_data(
        nova_situacao=nova, situacao_antiga=antiga,
        data_existente=data_existente, agora=datetime.now(UTC),
    )


def test_transicao_83965_para_15_vai_pro_dia_da_confirmacao():
    # etiqueta provisória em 26/05; confirma HOJE => passa a ser HOJE, não 26/05
    assert _next("15", "83965", date(2026, 5, 26)) == _hoje_op()


def test_transicao_6_para_15_carimba_hoje():
    assert _next("15", "6", None) == _hoje_op()
    assert _next("15", None, None) == _hoje_op()


def test_ja_em_15_redisparo_preserva():
    assert _next("15", "15", date(2026, 5, 26)) == date(2026, 5, 26)


def test_oscilacao_83953_para_15_preserva():
    """Bling oscila reportando situacao (ex.: 15 → 83953 → 15 a cada sync).
    A volta 83953→15 NÃO é confirmação real — preserva a data já gravada
    em vez de sobrescrever pro dia do sync. Só 83965→15 sobrescreve."""
    assert _next("15", "83953", date(2026, 5, 28)) == date(2026, 5, 28)


def test_83965_sem_data_carimba_provisorio_hoje():
    assert _next("83965", "6", None) == _hoje_op()
    assert _next("83965", None, None) == _hoje_op()


def test_83965_com_data_preserva():
    assert _next("83965", "83965", date(2026, 5, 26)) == date(2026, 5, 26)


def test_6_sem_data_fica_none():
    assert _next("6", None, None) is None


def test_6_com_data_mantem_historico():
    assert _next("6", "83965", date(2026, 5, 26)) == date(2026, 5, 26)


def test_row_from_item_repassa_data_final():
    raw = {"situacao": {"id": 15}, "loja": {}, "itens": []}
    row = _row_from_item(raw, {}, item_index=0, store_id=None,
                         em_andamento_data_final=date(2026, 5, 30))
    assert row["em_andamento_data"] == date(2026, 5, 30)


def test_row_from_item_sem_data_fica_none():
    raw = {"situacao": {"id": 6}, "loja": {}, "itens": []}
    row = _row_from_item(raw, {}, item_index=0, store_id=None)
    assert row["em_andamento_data"] is None
