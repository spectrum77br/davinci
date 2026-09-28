"""Motivo da disputa da Shopee no "Bloqueado" (Vinicius, 28/09/2026 — 290730).

O celular voltou travado com a senha do comprador e a disputa saiu com o id 86,
que o Seller Center BR mostra como "Eu não concordo com o desconto das taxas de
devolução". Loja no Programa Devolução Fácil não paga essa taxa: a Shopee
respondeu sobre a taxa e fechou em 14 min, sem olhar o aparelho. O motivo certo
na tabela oficial é "O produto recebido apresenta sinais de uso indevido" (89).
"""

from __future__ import annotations

import pytest

from app.services.chamados_devolucao import _shopee_reason, _shopee_reason_id

# O que o get_return_dispute_reason devolve numa devolução com o pacote de volta
# (série BR 81–89; a API manda só os ids).
_TODOS = [{"dispute_reason": i, "evidence_module_list": []} for i in (81, 82, 83, 84, 86, 89)]


@pytest.mark.parametrize("motivo", ["bloqueado", "mudou de ideia"])
def test_bloqueado_disputa_por_uso_indevido_e_nao_pela_taxa(motivo):
    assert _shopee_reason_id(_shopee_reason(_TODOS, motivo)) == 89


@pytest.mark.parametrize("motivo", ["bloqueado", "mudou de ideia"])
def test_sem_uso_indevido_na_lista_nao_disputa_com_a_taxa(motivo):
    """Disputa é tiro único: sem o 89, fica pra pessoa (shopee_motivo_indisponivel)
    em vez de queimar o caso com o 86."""
    sem_89 = [r for r in _TODOS if r["dispute_reason"] != 89]
    assert _shopee_reason(sem_89, motivo) is None


def test_demais_motivos_nao_mudaram():
    assert _shopee_reason_id(_shopee_reason(_TODOS, "danificado (outros)")) == 82
    assert _shopee_reason_id(_shopee_reason(_TODOS, "item faltando")) == 83
    assert _shopee_reason_id(_shopee_reason(_TODOS, "golpe")) == 83
    assert _shopee_reason_id(_shopee_reason(_TODOS, "não recebido")) == 81
