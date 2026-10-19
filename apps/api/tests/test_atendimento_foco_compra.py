"""A compra confirmada supera estoque para planejamento, nunca direitos pendentes."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import foco_compra, ia


def mensagem(texto=None, *, minuto=0, autor="cliente", payload=None):
    return SimpleNamespace(
        texto=texto,
        autor=autor,
        payload=payload or {},
        origem="cliente" if autor == "cliente" else "externo",
        status="recebida" if autor == "cliente" else "enviada",
        tipo="texto" if texto else "pedido",
        enviada_em=datetime(2026, 10, 19, tzinfo=UTC) + timedelta(minutes=minuto),
        created_at=datetime(2026, 10, 19, tzinfo=UTC) + timedelta(minutes=minuto),
    )


def turno(
    anteriores, *, card=True, compra="Ja realizei o pedido, qual a previsão de entrega/envio?"
):
    mensagens = [mensagem(t, minuto=i) for i, t in enumerate(anteriores)]
    mensagens.extend(
        [
            mensagem(compra, minuto=10),
            mensagem("De onde estão enviando?", minuto=11),
        ]
    )
    if card:
        mensagens.append(
            mensagem(
                autor="loja",
                minuto=12,
                payload={
                    "source": "openapi",
                    "message_type": "order",
                    "content": {"order_sn": "2610191X0P2AWN"},
                },
            )
        )
    mensagens.append(mensagem("1", minuto=13))
    return ia.HistoricoMensagens(
        mensagens, turno_incompleto=False, pedido_marketplace="2610191X0P2AWN"
    )


def test_pedido_feito_supera_planejamento_anterior_de_estoque():
    mensagens = turno(
        [
            "Quantos series 11 cinza vocês ainda tem em estoque?",
            "Só para eu me planejar, quando vc acha que acaba? Estou ainda conseguindo o dinheiro",
        ]
    )
    foco = ia.perguntas_pendentes(mensagens, mensagens[-1])
    assert [m.texto for m in foco] == [
        "Ja realizei o pedido, qual a previsão de entrega/envio?",
        "De onde estão enviando?",
    ]
    prompt = ia.montar_usuario_classificacao({}, mensagens, ())
    bloco = prompt.split("PERGUNTAS AINDA SEM RESPOSTA NESTE TURNO")[1]
    assert "estoque" not in bloco and "De onde estão enviando?" in bloco


@pytest.mark.parametrize(
    "antes",
    [
        "Quantos tem em estoque? E a garantia?",
        "Qual a garantia?",
        "Quero reembolso",
        "Tem em estoque e podem enviar foto?",
        "Quantos tem em estoque? Qual a medida?",
        "Quantas unidades em estoque e vem carregador?",
        "Quantas unidades em estoque, tem NFC?",
        "Emitem nota fiscal?",
        "A pulseira é P/M ou M/G?",
    ],
)
def test_compra_nao_apaga_perguntas_que_continuam_validas(antes):
    mensagens = turno([antes])
    assert antes in [m.texto for m in ia.perguntas_pendentes(mensagens, mensagens[-1])]


@pytest.mark.parametrize(
    "compra,card,incompleto",
    [
        ("Ja realizei o pedido, qual a previsão de entrega/envio?", False, False),
        ("Ainda não realizei o pedido, qual a previsão de entrega/envio?", True, False),
        ("Se eu fizer o pedido, qual a previsão de entrega/envio?", True, False),
        ("Ja realizei o pedido, qual a previsão de entrega/envio?", True, True),
    ],
)
def test_sem_compra_confirmada_ou_contexto_completo_conserva_estoque(compra, card, incompleto):
    mensagens = ia.HistoricoMensagens(
        turno(["Quantos tem em estoque?"], compra=compra, card=card),
        turno_incompleto=incompleto,
        pedido_marketplace="2610191X0P2AWN",
    )
    assert "Quantos tem em estoque?" in [
        m.texto for m in ia.perguntas_pendentes(mensagens, mensagens[-1])
    ]


def test_card_de_outro_pedido_nao_supera_estoque():
    mensagens = turno(["Quantos tem em estoque?"])
    mensagens.pedido_marketplace = "261019ABCDEFGH"
    assert "Quantos tem em estoque?" in [
        m.texto for m in ia.perguntas_pendentes(mensagens, mensagens[-1])
    ]


def test_menu_que_falhou_nao_interpreta_numero_como_assunto_sensivel():
    menu = mensagem(
        "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n"
        "3 - Encerramento da compra\n4 - Troca de endereço\n5 - Garantia",
        autor="loja",
        minuto=1,
    )
    opcao = mensagem("5", minuto=2)
    assert ia.assuntos_sensiveis_do_menu([menu, opcao], opcao) == ["garantia"]
    menu.status = "falhou"
    assert ia.assuntos_sensiveis_do_menu([menu, opcao], opcao) == []


PEDIDO_TIKTOK = "578000111222333444"


def _turno_tiktok():
    mensagens = turno(["Quantos series 11 cinza vocês ainda tem em estoque?"])
    mensagens.pedido_marketplace = PEDIDO_TIKTOK
    mensagens[-2].payload = {
        "type": "ORDER_CARD",
        "sender": {"role": "CUSTOMER_SERVICE"},
        "content": {"order_id": PEDIDO_TIKTOK},
    }
    return mensagens


@pytest.mark.parametrize("json_em_texto", [True, False])
def test_tiktok_card_do_atendimento_do_mesmo_pedido_supera_so_estoque(json_em_texto):
    mensagens = _turno_tiktok()
    if json_em_texto:
        mensagens[-2].payload["content"] = json.dumps(mensagens[-2].payload["content"])

    foco = ia.perguntas_pendentes(mensagens, mensagens[-1])

    assert [m.texto for m in foco] == [
        "Ja realizei o pedido, qual a previsão de entrega/envio?",
        "De onde estão enviando?",
    ]


@pytest.mark.parametrize(
    "alteracao",
    [
        {"sender": {"role": "BUYER"}},
        {"sender": {"role": "ROBOT"}},
        {"sender": {"role": "SYSTEM"}},
        {"sender": {"role": "SHOP"}},
        {"sender": {}},
        {"sender": "CUSTOMER_SERVICE"},
        {"type": "PRODUCT_CARD"},
        {"type": "BUYER_ENTER_FROM_ORDER"},
        {"type": "LOGISTICS_CARD"},
        {"type": "RETURN_REFUND_CARD"},
        {"content": {"order_id": "578000111222333445"}},
        {"content": {"order_id": "2610191X0P2AWN"}},
        {"content": {"order_id": ""}},
        {"content": {}},
        {"content": "não é JSON"},
        {"content": json.dumps([{"order_id": PEDIDO_TIKTOK}])},
        {"content": json.dumps(PEDIDO_TIKTOK)},
        {"is_visible": False},
    ],
)
def test_tiktok_so_o_formato_exato_do_pedido_da_loja_confirma_a_compra(alteracao):
    mensagens = _turno_tiktok()
    mensagens[-2].payload.update(alteracao)
    perguntas = [m for m in mensagens if m.autor == "cliente" and m.texto != "1"]

    assert foco_compra.depois_da_compra(perguntas, mensagens) == perguntas


@pytest.mark.parametrize("falha", ["autor", "falhou", "antes", "depois", "sem_compra"])
def test_tiktok_preserva_autoria_status_janela_e_confirmacao_do_comprador(falha):
    mensagens = _turno_tiktok()
    card = mensagens[-2]
    if falha == "autor":
        card.autor = "cliente"
    elif falha == "falhou":
        card.status = "falhou"
    elif falha == "antes":
        card.enviada_em = mensagens[1].enviada_em - timedelta(minutes=1)
    elif falha == "depois":
        card.enviada_em = mensagens[1].enviada_em + timedelta(days=1, seconds=1)
    else:
        mensagens[1].texto = "Ainda não realizei o pedido"
    perguntas = [m for m in mensagens if m.autor == "cliente" and m.texto and m.texto != "1"]

    assert foco_compra.depois_da_compra(perguntas, mensagens) == perguntas


def test_pedido_ml_vinculado_em_mensagem_textual_nao_e_card_de_compra():
    mensagens = _turno_tiktok()
    mensagens.pedido_marketplace = "2000012345678901"
    mensagens[-2].payload = {
        "text": "Pedido recebido",
        "from": {"user_id": "1234567"},
        "message_resources": [{"name": "orders", "id": mensagens.pedido_marketplace}],
        "data": {"order_id": mensagens.pedido_marketplace},
    }
    perguntas = [m for m in mensagens if m.autor == "cliente" and m.texto != "1"]

    assert foco_compra.depois_da_compra(perguntas, mensagens) == perguntas
