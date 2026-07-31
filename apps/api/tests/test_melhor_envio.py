"""Confere frete do Melhor Envio (item 3, impressão tipo "próprio").

Cobre a camada PURA: normaliza a resposta do /calculate, escolhe o menor frete
válido e decide libera/bloqueia vs o frete projetado da Tabela de Preços. Sem
rede — o cliente HTTP é exercitado só na inertização do token.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.melhor_envio import (
    Cotacao,
    MelhorEnvioClient,
    MelhorEnvioConfigError,
    conferir_frete,
    escolher_menor,
    parse_cotacoes,
)

# Resposta típica do POST /api/v2/me/shipment/calculate: lista de opções, uma por
# serviço; serviço que não atende vem com `error` e sem `price`.
_PAYLOAD = [
    {
        "id": 1,
        "name": "PAC",
        "price": "24.50",
        "delivery_time": 6,
        "company": {"id": 1, "name": "Correios"},
    },
    {
        "id": 2,
        "name": "SEDEX",
        "price": "39.90",
        "delivery_time": 2,
        "company": {"id": 1, "name": "Correios"},
    },
    {
        "id": 17,
        "name": ".Package",
        "error": "Serviço indisponível para a rota informada.",
    },
]


def test_parse_cotacoes_normaliza():
    cots = parse_cotacoes(_PAYLOAD)
    assert len(cots) == 3
    pac = cots[0]
    assert pac.servico_id == 1
    assert pac.servico_nome == "PAC"
    assert pac.empresa == "Correios"
    assert pac.preco == Decimal("24.50")
    assert pac.prazo_dias == 6
    assert pac.erro is None
    # a opção com erro fica na lista mas sem preço
    assert cots[2].erro is not None
    assert cots[2].preco is None


def test_parse_cotacoes_payload_invalido():
    assert parse_cotacoes({"error": "token"}) == []
    assert parse_cotacoes(None) == []


def test_escolher_menor_ignora_erro_e_zero():
    cots = parse_cotacoes(_PAYLOAD)
    menor = escolher_menor(cots)
    assert menor is not None
    assert menor.servico_nome == "PAC"
    assert menor.preco == Decimal("24.50")


def test_escolher_menor_sem_validas():
    cots = [
        Cotacao(17, ".Package", "Jadlog", None, None, "indisponível"),
        Cotacao(3, "Mini", "Correios", Decimal("0"), 5, None),
    ]
    assert escolher_menor(cots) is None


def test_conferir_libera_dentro_do_projetado():
    cots = parse_cotacoes(_PAYLOAD)
    r = conferir_frete(cots, Decimal("30.00"))
    assert r.libera is True
    assert r.motivo == "dentro_do_projetado"
    assert r.menor_frete == Decimal("24.50")
    assert r.servico_escolhido == "PAC"
    assert r.diferenca == Decimal("5.50")


def test_conferir_bloqueia_acima_do_projetado():
    cots = parse_cotacoes(_PAYLOAD)
    r = conferir_frete(cots, Decimal("20.00"))
    assert r.libera is False
    assert r.motivo == "acima_do_projetado"
    assert r.menor_frete == Decimal("24.50")
    assert r.diferenca == Decimal("-4.50")


def test_conferir_libera_exatamente_no_limite():
    cots = parse_cotacoes(_PAYLOAD)
    r = conferir_frete(cots, Decimal("24.50"))
    assert r.libera is True
    assert r.diferenca == Decimal("0.00")


def test_conferir_sem_cotacao():
    r = conferir_frete([], Decimal("30.00"))
    assert r.libera is False
    assert r.motivo == "sem_cotacao"
    assert r.menor_frete is None


def test_conferir_sem_frete_projetado():
    cots = parse_cotacoes(_PAYLOAD)
    r = conferir_frete(cots, None)
    assert r.libera is False
    assert r.motivo == "sem_frete_projetado"
    # ainda reporta a cotação encontrada pra referência
    assert r.menor_frete == Decimal("24.50")


@pytest.mark.asyncio
async def test_client_sem_token_levanta():
    cli = MelhorEnvioClient(token="")
    with pytest.raises(MelhorEnvioConfigError):
        await cli.calcular_frete(
            from_cep="01001000", to_cep="20050000",
            produtos=[{"id": "1", "width": 11, "height": 17, "length": 11,
                       "weight": 0.3, "insurance_value": 100.0, "quantity": 1}],
        )
