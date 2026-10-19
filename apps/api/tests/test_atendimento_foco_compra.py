"""A compra confirmada supera estoque para planejamento, nunca direitos pendentes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import ia


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
