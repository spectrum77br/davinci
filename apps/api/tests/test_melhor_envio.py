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


# --- Atendimentos (sonda só-leitura, 13/11/2026) -----------------------------
def test_resumo_atendimento_acha_a_conversa_e_corta_texto():
    from app.services.melhor_envio import resumo_atendimento

    corpo = {
        "id": 2065437,
        "status": "in_progress",
        "subject": "Falha na entrega",
        "order": {"id": "a2d0", "protocol": "ORD-1", "to": {"name": "Fulana"}},
        "messages": [
            {"id": i, "body": "x" * 300, "author": {"name": "Natalia", "type": "agent"}}
            for i in range(5)
        ],
    }
    r = resumo_atendimento(corpo)
    assert r["campos_simples"] == {
        "id": 2065437,
        "status": "in_progress",
        "subject": "Falha na entrega",
    }
    conversa = r["listas"]["messages"]
    assert conversa["total"] == 5
    assert len(conversa["primeiros"]) == 3 and len(conversa["ultimos"]) == 3
    primeiro = conversa["primeiros"][0]
    assert len(primeiro["body"]) == 161  # 160 + "…"
    assert primeiro["author"] == {"name": "Natalia", "type": "agent"}
    assert r["forma"]["order"]["to"] == {"name": "str"}


@pytest.mark.asyncio
async def test_ler_atendimento_recusa_id_estranho_sem_rede():
    from app.services.melhor_envio import ler_atendimento

    http, corpo = await ler_atendimento("tok", "../orders")
    assert http == 400
    assert "inválido" in corpo["message"]


async def test_enviar_no_atendimento_manda_o_formulario_da_tela():
    """Igual ao botão Enviar do painel do ME (13/11): multipart com _method=PUT,
    order_id, description e attachments[]."""
    import respx
    from httpx import Response

    from app.services.melhor_envio import enviar_no_atendimento

    with respx.mock(assert_all_called=True) as rx:
        rota = rx.post("https://melhorenvio.com.br/api/v2/me/tickets/2122992").mock(
            return_value=Response(200, json={"ticket": {"number": 2122992}})
        )
        http, corpo = await enviar_no_atendimento(
            "tok",
            "2122992",
            order_id="a2d0-ord",
            texto="alguma novidade?",
            anexos=[("foto.png", b"PNGDATA", "image/png")],
        )
    assert http == 200 and corpo["ticket"]["number"] == 2122992
    req = rota.calls.last.request
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["content-type"].startswith("multipart/form-data")
    corpo_req = req.content.decode("latin-1")
    campos = (("_method", "PUT"), ("order_id", "a2d0-ord"), ("description", "alguma novidade?"))
    for campo, valor in campos:
        assert f'name="{campo}"\r\n\r\n{valor}\r\n' in corpo_req
    assert 'name="attachments[]"; filename="foto.png"' in corpo_req
    assert "PNGDATA" in corpo_req


async def test_enviar_no_atendimento_recusa_id_estranho_sem_rede():
    from app.services.melhor_envio import enviar_no_atendimento

    http, _ = await enviar_no_atendimento("tok", "1/close", order_id="x", texto="oi")
    assert http == 400
