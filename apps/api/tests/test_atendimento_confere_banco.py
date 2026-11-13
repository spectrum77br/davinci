"""O "BATEU COM A DO BANCO" pelo SENTIDO (13/11/2026, `agente/confere_banco.py`).

O agente não copia a resposta da equipe (escreve com as palavras dele): a conferência é pelo
sentido, com o modelo; só quando vale (régua e banco ligados, sem ação de pessoa, fora dos
sensíveis e da cortesia, com resposta do banco do mesmo tipo) e com os textos mascarados.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.atendimento import ia
from app.services.atendimento.agente import confere_banco
from app.services.atendimento.agente import contexto as agente_ctx


def _ctx(**campos) -> agente_ctx.ContextoAgente:
    ctx = agente_ctx.ContextoAgente(
        conversa_id="c",
        gatilho_id=None,
        canal_id=None,
        integration_id=None,
        plataforma="shopee",
        canal="chat",
        modo="auto",
        nomes=("Maria Souza",),
    )
    ctx.regua = True
    ctx.banco_exigido = True
    ctx.registrar_pares(
        [
            {
                "id": "par:b1",
                "tipo": "garantia",
                "pergunta": "p",
                "resposta": "A garantia é pela Uranyx.",
            },
            {"id": "par:b2", "tipo": "rastreio", "pergunta": "p", "resposta": "Saiu pela J&T."},
        ]
    )
    for k, v in campos.items():
        setattr(ctx, k, v)
    return ctx


class _Cliente:
    def __init__(self, veredito: str):
        self.messages = self
        self.veredito = veredito
        self.pedidos: list[dict] = []

    async def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=f'{{"veredito": "{self.veredito}", "motivo": "mesma política"}}',
                )
            ],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(
                input_tokens=400,
                output_tokens=30,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def test_so_confere_quando_vale():
    ctx = _ctx()
    assert confere_banco.vale_conferir(ctx, "garantia", "texto")
    assert not confere_banco.vale_conferir(ctx, "reembolso", "texto")  # sensível
    assert not confere_banco.vale_conferir(ctx, "agradecimento", "texto")  # cortesia
    assert not confere_banco.vale_conferir(ctx, "garantia", "")
    assert not confere_banco.vale_conferir(
        _ctx(acoes_codigo=["reclamacao_aberta"]), "garantia", "x"
    )
    assert not confere_banco.vale_conferir(_ctx(regua=False), "garantia", "x")
    assert confere_banco.candidatas(ctx, "garantia") == [("par:b1", "A garantia é pela Uranyx.")]
    assert confere_banco.candidatas(ctx, "defeito") == []


async def test_mesma_grava_o_id_e_o_pedido_vai_mascarado(db):
    cliente = _Cliente("mesma")
    fatos = await confere_banco.conferir(
        db,
        _ctx(),
        categoria="garantia",
        texto="Oi Maria Souza! Sua garantia é com a Uranyx, é só nos chamar.",
        pergunta="Sou a Maria Souza, qual a garantia?",
        cliente=cliente,
        modelo="claude-opus-5-5",
    )
    assert (
        fatos["banco_confere"]["veredito"] == "mesma" and fatos["banco_confere"]["id"] == "par:b1"
    )
    (pedido,) = cliente.pedidos
    assert "Maria Souza" not in str(pedido["messages"])
    assert not {"thinking", "temperature", "top_p", "top_k"} & set(pedido)
    assert pedido["output_config"]["format"]["type"] == "json_schema"


async def test_diferente_nao_solta_e_sem_banco_nem_chama(db):
    cliente = _Cliente("diferente")
    fatos = await confere_banco.conferir(
        db,
        _ctx(),
        categoria="garantia",
        texto="Outra coisa.",
        pergunta="?",
        cliente=cliente,
        modelo="m",
    )
    assert fatos == {
        "banco_confere": {
            "id": None,
            "veredito": "diferente",
            "custo_usd": fatos["banco_confere"]["custo_usd"],
        }
    }
    nada = _Cliente("mesma")
    assert (
        await confere_banco.conferir(
            db, _ctx(), categoria="defeito", texto="x", pergunta="?", cliente=nada, modelo="m"
        )
        == {}
    )
    assert nada.pedidos == []


def test_o_portao_le_a_conferencia():
    agente = {
        "regua": True,
        "banco_exigido": True,
        "banco_tipos": {"par:b1": "garantia"},
        "banco_confere": {"id": "par:b1", "veredito": "mesma"},
    }
    assert ia.bateu_com_o_banco(agente, "garantia", "texto qualquer")
    assert not ia.bateu_com_o_banco(
        {**agente, "banco_confere": {"id": "par:b1", "veredito": "diferente"}}, "garantia", "x"
    )
    # O id tem de ser do mesmo tipo do rascunho.
    assert not ia.bateu_com_o_banco(agente, "rastreio", "x")
    assert not ia.bateu_com_o_banco({**agente, "regua": False}, "garantia", "x")
