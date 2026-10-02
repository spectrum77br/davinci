"""Chaves `flex_*`: padrões seguros e valor errado vira o lado seguro."""

from __future__ import annotations

import uuid

from app.config import Settings, get_settings
from app.services import flex_config


def test_padroes_das_decisoes_de_02_10(monkeypatch):
    # O padrão do código (não o .env de quem roda o teste).
    padrao = {nome: campo.default for nome, campo in Settings.model_fields.items()}
    assert padrao["flex_modo"] == "desligado"
    assert padrao["flex_contas"] == ""
    assert (padrao["flex_n_liga"], padrao["flex_n_desliga"]) == (3, 1)
    assert padrao["flex_kits"] is False
    assert padrao["flex_max_anuncios_por_familia"] == 2
    assert padrao["flex_teto_escritas_por_rodada"] == 50
    assert padrao["flex_shopee_canais"] == "90022"
    assert padrao["flex_shopee_escrita"] is False
    assert padrao["flex_intervalo_min"] == 15
    # Pedido Flex vai para o .sp (etapa 2) — ligado, independe do modo.
    assert padrao["flex_pedido_no_sp"] is True
    # Com o padrão: não faz nada e não escreve em conta nenhuma.
    monkeypatch.setattr(get_settings(), "flex_modo", padrao["flex_modo"])
    monkeypatch.setattr(get_settings(), "flex_contas", padrao["flex_contas"])
    assert flex_config.modo() == "desligado"
    assert flex_config.contas() == frozenset()
    assert flex_config.pode_escrever() is False


def test_modo_normaliza_e_desconhecido_vira_desligado():
    assert flex_config.modo(" Observar ") == "observar"
    assert flex_config.modo("ATIVO") == "ativo"
    assert flex_config.modo("ligado") == "desligado"  # erro de digitação
    assert flex_config.modo("") == "desligado"
    assert flex_config.pode_escrever("observar") is False
    assert flex_config.pode_escrever("piloto") is True
    assert flex_config.pode_escrever("ativo") is True


def test_contas_ignora_id_invalido(monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    assert flex_config.contas(f" {a}, ,{b},nao-e-uuid") == frozenset({a, b})
    monkeypatch.setattr(get_settings(), "flex_contas", str(a))
    assert flex_config.contas() == frozenset({a})
