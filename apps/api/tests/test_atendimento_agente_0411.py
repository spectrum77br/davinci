"""A rodada de 04/11/2026 da leitura ao vivo: dois erros e os travamentos sem decisão do dono.

- 76417da4: o agente disse "Não enviamos links", com a cliente sem achar o anúncio;
- 40a0d370: "está tudo certo" com o rastreio parado há 11 dias, numa simples pergunta da entrega;
- 340848b7: "número de registro" abria a ação de enviar o arquivo da NF na pré-venda;
- b2153054: "não conseguimos garantir entrega", sem o artigo, virava fala de garantia.

O agradecimento na NF (9be5a62b) está em `test_atendimento_nf_automatica.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import ia, validador
from app.services.atendimento.agente import acao_pessoa, catalogo, ferramentas, pacote

AGORA = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PEDIDO = "261020ABCDEF"


@pytest.mark.parametrize(
    "texto",
    [
        "Não enviamos links por aqui.",
        "Não conseguimos enviar link por este chat.",
        "nao podemos mandar o link",
        "Não mandamos link.",
        "Não posso enviar link por aqui, mas você encontra pelo nome.",
        "Não podemos te enviar o link por aqui.",
        "Não é possível enviar links por este chat.",
        "A plataforma não permite enviar links.",
        "Não enviamos um link.",
        "O Mercado Livre não permite enviar links por aqui.",
        "Não consigo enviar o link.",
        "Links não são permitidos aqui.",
        "O Mercado Livre não permite links aqui.",
        "O envio de links não é permitido.",
    ],
)
def test_o_agente_nao_nega_o_link(texto):
    assert validador._NEGA_LINK.search(validador.plano_de(texto))


@pytest.mark.parametrize(
    "texto",
    [
        "O anúncio aparece nos anúncios da loja, pelo nome.",
        "Não enviamos brinde por aqui.",
        "Temos sim o cesto de vidro.",
        "Não enviamos link de pagamento, cuidado com golpes.",
    ],
)
def test_a_resposta_normal_nao_cai_na_trava_do_link(texto):
    assert not validador._NEGA_LINK.search(validador.plano_de(texto))


@pytest.mark.parametrize(
    "texto,exige",
    [
        ("Tem nota fiscal? E o número de registro da Anatel?", False),
        ("vem com nota?", False),
        ("Qual o número da nota fiscal?", True),
        ("me passa o numero da minha nf", True),
        ("preciso do numero da chave da nota fiscal", True),
        ("não recebi a nota fiscal", True),
        ("qual o numero do NF?", True),
        ("Preciso da nota fiscal. Qual o número dela?", True),
        ("manda o arquivo da nota fiscal", True),
    ],
)
def test_so_o_numero_do_documento_abre_a_acao_da_nf(texto, exige):
    assert ia.nf_exige_pessoa([texto]) is exige


@pytest.mark.parametrize(
    "texto,sai",
    [
        ("não conseguimos garantir entrega até domingo", True),
        ("não conseguimos garantir a entrega até domingo", True),
        ("não conseguimos garantir prazo", True),
        ("não conseguimos garantir data", True),
        ("não conseguimos garantir a troca", False),
        ("não conseguimos garantir o conserto", False),
        ("podemos garantir entrega no prazo", False),
        ("garantir data para o reparo", False),
        ("não conseguimos garantir prazo para defeito", False),
    ],
)
def test_garantir_a_entrega_com_e_sem_o_artigo(texto, sai):
    assert bool(validador._GARANTIR_A_ENTREGA.search(validador.plano_de(texto))) is sai


@pytest.mark.parametrize(
    "bruto,limpo",
    [
        (
            "Está tudo certo com o seu pedido, ele está a caminho do próximo centro logístico",
            "Está a caminho do próximo centro logístico",
        ),
        ("Pedido postado", "Pedido postado"),
    ],
)
def test_o_evento_da_shopee_vai_ao_modelo_sem_o_tudo_certo(bruto, limpo):
    assert pacote.status_do_evento_sem_cidade(bruto) == limpo


def _conversa(dias_do_evento: float, descricao: str = "Seu pacote a caminho") -> SimpleNamespace:
    evento = AGORA - timedelta(days=dias_do_evento)
    return SimpleNamespace(
        plataforma="shopee",
        pedido_marketplace=PEDIDO,
        dados={
            "pedido_mkt": {
                "pedido": PEDIDO,
                "fonte": "shopee",
                "atualizado_em": (AGORA - timedelta(hours=1)).isoformat(),
                "logistica": {"descricao": descricao, "atualizado_em": evento.isoformat()},
            }
        },
    )


@pytest.mark.parametrize(
    "turno,dias,marca",
    [
        ("previsao de entrega", 11, True),  # só pergunta, com o evento velho: 40a0d370
        ("o pedido chega hoje?", 11, True),
        ("previsao de entrega", 0.5, False),  # andando: segue sem chamado
        ("tem nfc?", 11, False),  # não é da entrega
        ("obrigada", 11, False),
        ("Chegou sim, obrigada!", 11, False),
        ("Já recebi, obrigado", 11, False),
        ("Chegou! Como configuro o aparelho?", 11, False),
        ("Qual o endereço de entrega do meu pedido?", 11, False),
        ("O pedido vem com capinha?", 11, False),
        ("Comprei pela Shopee, tem garantia?", 11, False),
        ("Manda a nota fiscal do meu pedido", 11, False),
        ("Como cancelo o pedido?", 11, False),
        ("cadê meu pedido?", 11, True),
        ("qual o prazo de entrega", 11, True),
        ("quando vai chegar?", 11, True),
    ],
)
def test_pergunta_da_entrega_com_o_rastreio_parado_marca_o_chamado(turno, dias, marca):
    plano = validador.plano_de(turno)
    obtido = acao_pessoa.chamado_de_entrega_shopee(_conversa(dias), {}, plano, agora=AGORA)
    assert obtido is marca


def test_pergunta_da_entrega_com_falha_no_evento_fresco_segue_sem_marcar():
    """A falha no evento só conta para quem fala da entrega (a régua de antes)."""
    conversa = _conversa(0.5, "Falha na entrega, tentaremos de novo")
    plano = validador.plano_de("previsao de entrega")
    assert acao_pessoa.chamado_de_entrega_shopee(conversa, {}, plano, agora=AGORA) is False


def test_a_resposta_pronta_que_nega_o_link_nao_vai_ao_agente():
    prontas = [
        {"id": "1", "texto": "Olá! Não enviamos links por aqui, mas você encontra o anúncio."},
        {"id": "2", "texto": "O anúncio aparece nos anúncios da loja, pelo nome."},
    ]
    assert [p["id"] for p in ferramentas._prontas_sem_espera(prontas)] == ["2"]


@pytest.mark.parametrize(
    "titulo,nome",
    [
        ("Caap ** Uranyx Oukitel C3 5g Nfc 128gb", "Uranyx Oukitel C3"),
        ("Cappa **Uranyx Fossibot F109 5g 256gb", "Uranyx Fossibot F109"),
        ("Cappa**Uranyx Fossibot F109 5g", "Uranyx Fossibot F109"),
        ("Caap ***Uranyx Oukitel C3 5g", "Uranyx Oukitel C3"),
        ("Uranyx A18 Pro Max NFC 16.128 Gb", "Uranyx A18 Pro Max"),
    ],
)
def test_o_nome_oferecido_nao_leva_o_prefixo_dos_titulos_do_ml(titulo, nome):
    assert catalogo.nome_curto(titulo) == nome


def test_status_nao_lido_do_ml_nao_e_oferecido():
    v = catalogo.Vinculo(
        item_id="1",
        variation_id=None,
        titulo="Uranyx A18 NFC",
        estoque=5,
        sincronizado=True,
        anuncio_status=catalogo.STATUS_NAO_LIDO,
    )
    assert catalogo.disponivel([v]) is None
