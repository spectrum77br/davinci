# ruff: noqa: E501, F401, F811 — as frases do eval ficam inteiras; fixtures importadas
"""Rodada 5 — os dados reais (o eval da Kia de 21/10/2026): a integração dos dois ajustes.

O eval real (80 conversas da Kia na Shopee, braço A2, nota dupla às cegas) deu
dois ajustes feitos em paralelo — o prompt (`agente.prompt`, o rascunho de
pessoa e a orientação) e os 6 bugs do código (`test_atendimento_agente_eval_real`).
Aqui, o que faltava para os dois caberem juntos (só os itens do conjunto `dev`
foram abertos; as frases dos testes são as do dev e variações escritas a partir
dos comentários dele):

0. A integração: o `_FIM_DA_FRASE` da lacuna (bugs) SOMBREAVA o da promessa no
   módulo inteiro — o ";" deixou de fechar a frase e "O seu dinheiro volta
   amanhã; o estorno é feito pela Shopee" saía.
1. A espera no rascunho de PESSOA (32 dos 34 erros críticos): aviso (regra
   suave), a ação da pessoa não é espera.
2. O texto vazio de pessoa (o prompt manda): sem o "texto vazio" do validador —
   a ação é o motivo.
3. A lacuna crua no `motivo_pessoa`.
4. O manual antigo: as respostas prontas sem a frase de espera; a regra com a
   espera ganha o aviso da regra 16; o estoque sem dado confiável segue o prompt.
5. A orientação errada: "a loja não consegue cancelar" e a troca no lugar da
   devolução (avisos).
6. Os falsos positivos do dev que ficaram fora dos 6 bugs: o parêntese das
   variações (a6ff), a `regra:<assunto>` do sistema (f0537), o "que bom saber
   que está tudo certo" (5812), a exceção da UF restrita no pedido cancelado
   (b546), a resposta por versão no anúncio de 2+ modelos (o NFC por
   armazenamento na descrição).
7. Ponta a ponta (`ia.gerar_rascunho`, o SDK sobre o transporte falso, sem
   LLM), um caso por sintoma real: a espera de pessoa, o texto vazio, a lacuna
   no motivo, o manual sem a espera, os casos que iam para pessoa com o dado na
   mão (postado, prazo pelo CEP, a lista de variações, a retirada, a decisão já
   tomada), a orientação errada, os números do título e a ficha no estoque.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento import ficha, humano, ia
from app.services.atendimento.agente import acao_pessoa, checagens, ferramentas, prompt, saida
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_DESCARTADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_e2e import (
    ITEM_A18,
    _decisao,
    _do_agente,
    _final,
    _kia,
    _ligar,
    _sem_limites,
    _usa,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    leitura,
    lojas,
    redis_mem,
    sem_busca_pendurada,
    telegram,
)
from tests.test_atendimento_agente_furos import _completo, _sai
from tests.test_atendimento_agente_pacote import _cliente, _gat
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    RASTREIO,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
)

# ═══════════════ 0. a frase da promessa e a da lacuna ═══════════════


def test_0_a_frase_da_promessa_fecha_no_ponto_e_virgula():
    """O sintoma da integração: a lacuna (bugs) redefinia `_FIM_DA_FRASE` no fim do
    módulo; a promessa (seção 5) passou a ler o padrão dela."""
    assert checagens._FIM_DA_FRASE.pattern == r"[.!?;\n]"
    assert checagens._ENTRE_FRASES.pattern != checagens._FIM_DA_FRASE.pattern


@pytest.mark.parametrize(
    ("texto", "assunto"),
    [
        # O "pela Shopee" depois do ";" livrava o "dinheiro volta" de antes.
        ("O seu dinheiro volta amanhã mesmo; o estorno é feito pela Shopee.", "reembolso"),
        # O "prazo de envio do anúncio" depois do ";" livrava o "será enviado amanhã".
        (
            "O seu pedido será enviado amanhã; o prazo de envio do anúncio continua valendo.",
            "prazo_envio",
        ),
    ],
)
def test_0_a_promessa_antes_do_ponto_e_virgula_continua_pega(texto, assunto):
    (p,) = checagens.conferir_promessa_de_equipe(
        texto, acao="responder", acoes_pessoa=[], assunto=assunto, textos_cliente=["e aí?"]
    )
    assert p.codigo == checagens.PROMESSA_DE_EQUIPE and p.suave


def test_0_a_lacuna_continua_tirando_so_a_frase_dela():
    assert (
        checagens.sem_as_frases_das_lacunas(
            "Seu pedido já saiu. O rastreio é {rastreio}. Acompanhe em Minhas Compras.",
            ["rastreio"],
        )
        == "Seu pedido já saiu. Acompanhe em Minhas Compras."
    )


# ═══════════════ 1. a espera no rascunho de PESSOA ═══════════════

# Os rascunhos de pessoa do dev com a frase de espera (o texto do modelo; o
# número do pedido no lugar da lacuna mascarada do eval).
ESPERAS_DO_DEV = [
    (
        "29b294540edd",
        "Olá! Sobre a cor roxa: este anúncio não tem essa opção. As cores disponíveis no momento são branco e laranja (A18). Sobre o 5G, a ficha deste anúncio não traz essa informação, então nossa equipe vai confirmar e responder por aqui.",
    ),
    (
        "a0cee7b7a4fd",
        "Entendemos a sua dúvida sobre originalidade e garantia. O anúncio informa garantia Apple e condição nova, e a compra também é protegida pela plataforma. Vimos que o pedido 250925ABCDEF12 está com devolução em andamento, então a nossa equipe vai analisar a sua dúvida sobre a garantia após a ativação e responder por aqui.",
    ),
    (
        "2ab0481ece85",
        "No anúncio desta conversa, os iPhone 17 Pro e 17 Pro Max estão esgotados no momento. Não consigo confirmar agora se temos iPhone 18 Pro Max para venda. Nossa equipe acompanha a conversa e responde por aqui.",
    ),
    (
        "57ee0b31220d",
        "Entendemos que você comprou o modelo errado, e nossa equipe vai analisar o seu pedido 250925ABCDEF12 e o pedido de troca pelo modelo que você indicou, respondendo por aqui. Enquanto isso, se o aparelho já estiver com você, pode solicitar a devolução pela opção do próprio pedido em Minhas Compras, e a Shopee analisa e devolve pela mesma forma de pagamento.",
    ),
    (
        "cc95e95879e8",
        "Olá! O anúncio informa a marca Apple e a garantia Apple de 1 ano. A compra é protegida pela plataforma. Sobre quando a garantia começa a contar após a ativação, nossa equipe confirma para você o prazo e como acionar, aqui mesmo.",
    ),
    (
        "d3f096c95058",
        "Olá! Lamentamos o transtorno. Nossa equipe vai conferir o modelo do aparelho do seu pedido e a foto que você enviou, já que o anúncio traz duas versões (F109 5G e F109 S). Assim que a análise terminar, respondemos por aqui. Enquanto isso, se preferir a devolução, ela pode ser solicitada no próprio pedido em Minhas Compras, e a Shopee analisa o pedido.",
    ),
    (
        "683d991b745c",
        "Olá! Obrigado pela paciência. Não temos aqui o nome da transportadora nem uma data de entrega mais exata para o pedido 250925ABCDEF12. Você pode acompanhar o código de rastreio e o envio em Minhas Compras, no pedido, em Informações do envio. Nossa equipe vai analisar o seu pedido e responder por aqui.",
    ),
]
# A espera por outras palavras (escritas a partir dos comentários do dev).
ESPERAS_PARAFRASE = [
    "A equipe acompanha o caso.",
    "Assim que tivermos retorno, te avisamos.",
    "Aguarde o nosso retorno, por favor.",
    "Vamos verificar com a transportadora e te retornamos.",
    "Olá! Anotamos a sua escolha. Nossa equipe confirma os detalhes da nova opção e responde por aqui.",
    "Já estamos verificando o pedido com prioridade e voltamos por aqui com uma resposta.",
    "Olá! Recebemos a foto. Assim que a análise terminar, a resposta vem por aqui.",
    "Olá! Recebemos a sua mensagem e vamos encaminhá-la ao responsável da empresa.",
]
# O rascunho de pessoa BOM: a resposta depois da ação (a ação dela não é espera).
PESSOA_BOAS = [
    "Olá! Segue o arquivo da nota fiscal do pedido 250925ABCDEF12.",
    "Olá! Como o pedido 250925ABCDEF12 ainda não foi enviado, o cancelamento é pedido no próprio pedido, em Minhas Compras.",
    "Olá! O envio do seu pedido foi segurado. O cancelamento é pedido no próprio pedido, em Minhas Compras.",
    "Olá! Podemos trocar pelo modelo Azul, que está disponível; se preferir, a devolução é pedida em Minhas Compras.",
    "Já conferimos o modelo: o seu pedido é do F109 5G.",
    "Sentimos muito pelo transtorno! A devolução pode ser pedida no próprio pedido, em Minhas Compras, e a Shopee analisa e faz o reembolso.",
    "Olá! Anotamos a sua escolha para o pedido 250925ABCDEF12.",
    "Olá! A nota fiscal do seu pedido segue em anexo. Qualquer dúvida, é só chamar por aqui.",
    "Seguimos à disposição por aqui.",
    "Olá! O endereço do pedido foi alterado para o novo endereço informado.",
]


@pytest.mark.parametrize(("item", "texto"), ESPERAS_DO_DEV)
def test_1_a_espera_do_rascunho_de_pessoa_do_dev_e_aviso(item, texto):
    (p,) = checagens.conferir_espera_no_rascunho_de_pessoa(texto, acao="pessoa")
    assert p.codigo == checagens.ESPERA_NO_RASCUNHO_DE_PESSOA and p.suave, item
    assert "quem envia já agiu" in p.motivo
    # Na resposta e na pergunta, quem lê é a regra da promessa (a de sempre).
    assert checagens.conferir_espera_no_rascunho_de_pessoa(texto, acao="responder") == []


@pytest.mark.parametrize("texto", ESPERAS_PARAFRASE)
def test_1_a_espera_por_outras_palavras(texto):
    assert checagens.conferir_espera_no_rascunho_de_pessoa(texto, acao="pessoa"), texto


@pytest.mark.parametrize("texto", PESSOA_BOAS)
def test_1_a_resposta_depois_da_acao_nao_e_espera(texto):
    assert checagens.conferir_espera_no_rascunho_de_pessoa(texto, acao="pessoa") == [], texto


def test_1_sem_a_excecao_do_pedido_de_video_no_rascunho_de_pessoa():
    """O prompt: "A nossa equipe analisa e responde por aqui" só vale numa resposta
    que pede vídeo e fotos; num rascunho de pessoa, nunca."""
    texto = (
        "Pode mandar um vídeo curto e fotos por aqui? A nossa equipe analisa e responde por aqui."
    )
    assert checagens.conferir_espera_no_rascunho_de_pessoa(texto, acao="pessoa")
    assert (
        checagens.conferir_promessa_de_equipe(
            texto,
            acao="responder",
            acoes_pessoa=[],
            assunto="defeito",
            textos_cliente=["o celular não liga"],
        )
        == []
    )


# ═══════════════ 2. o texto vazio de pessoa ═══════════════


def _vazia(**kw: Any) -> ia.SaidaConferida:
    base = {
        "texto": "",
        "categoria": "troca_devolucao",
        "precisa_humano": True,
        "motivos": ["validador: texto vazio"],
        "erros": ["texto vazio"],
        "status": RASCUNHO_BLOQUEADO,
        "motivo_modelo": "ação de pessoa: Aprovar fora da plataforma — decidir a troca por fora",
        "sem_auto": ["validador: texto vazio"],
    }
    base.update(kw)
    return ia.SaidaConferida(**base)


def test_2_o_texto_vazio_de_pessoa_nao_e_erro_do_validador():
    f = checagens.sem_o_texto_vazio_de_pessoa(_vazia(), acao="pessoa")
    assert f.texto == "" and f.status == RASCUNHO_BLOQUEADO and f.precisa_humano
    assert f.erros == [] and f.motivos == [] and f.sem_auto == []
    assert f.motivo == "ação de pessoa: Aprovar fora da plataforma — decidir a troca por fora"
    # O resto do motivo do validador fica (só o "texto vazio" sai).
    junto = _vazia(
        motivos=["validador: texto vazio; outro erro"], erros=["texto vazio", "outro erro"]
    )
    g = checagens.sem_o_texto_vazio_de_pessoa(junto, acao="pessoa")
    assert g.motivos == ["validador: outro erro"] and g.erros == ["outro erro"]


def test_2_o_responder_vazio_e_o_texto_de_pessoa_nao_mudam():
    vazia = _vazia(motivo_modelo="")
    # O "responder" sem texto: o "não pede resposta" do agente lê o "texto vazio".
    assert checagens.sem_o_texto_vazio_de_pessoa(vazia, acao="responder") is vazia
    com_texto = _vazia(
        texto="Olá! Segue o arquivo.", erros=[], motivos=[], status=RASCUNHO_PENDENTE
    )
    assert checagens.sem_o_texto_vazio_de_pessoa(com_texto, acao="pessoa") is com_texto


# ═══════════════ 3. a lacuna no motivo_pessoa ═══════════════


def test_3_a_lacuna_do_motivo_vira_o_valor_ou_sai():
    """O dev a0cee7b7a4fd gravou "Pedido {numero_pedido} consta como devolvido…"."""
    motivo = (
        "ação de pessoa: Abrir chamado (entrega) — Pedido {numero_pedido} consta como devolvido"
    )
    assert (
        checagens.motivo_sem_lacuna(motivo, {"numero_pedido": PEDIDO_MKT})
        == f"ação de pessoa: Abrir chamado (entrega) — Pedido {PEDIDO_MKT} consta como devolvido"
    )
    assert (
        checagens.motivo_sem_lacuna(motivo, {"numero_pedido": None})
        == "ação de pessoa: Abrir chamado (entrega) — Pedido consta como devolvido"
    )
    assert checagens.motivo_sem_lacuna("sem lacuna", {}) == "sem lacuna"
    assert checagens.motivo_sem_lacuna("", None) == ""


# ═══════════════ 4. o manual antigo sem a frase de espera ═══════════════


def _manual_base() -> dict:
    from pathlib import Path

    caminho = Path(__file__).resolve().parents[1] / "scripts" / "atendimento_manual_base.json"
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_4_as_respostas_prontas_chegam_sem_a_espera():
    prontas = [
        {"id": f"modelo:{i}", "texto": m["texto"], "categoria": m.get("categoria")}
        for i, m in enumerate(_manual_base()["respostas_prontas"])
    ]
    com_espera = [p for p in prontas if checagens.tem_espera(p["texto"])]
    assert len(com_espera) >= 12  # as "(sugestão para pessoa)" do manual base
    limpas = ferramentas._prontas_sem_espera(prontas)
    assert not [p for p in limpas if checagens.tem_espera(p["texto"])]
    por_id = {p["id"]: p for p in limpas}
    # O que só tinha a espera (e a cortesia) sai inteiro; o resto fica sem a oração.
    assert not [
        p
        for p in com_espera
        if p["id"] in por_id and checagens.so_cortesia_ou_recebimento(por_id[p["id"]]["texto"])
    ]
    defeito = [por_id[p["id"]]["texto"] for p in com_espera if p["categoria"] == "defeito"]
    assert defeito and all("vídeo" in t or "fotos" in t for t in defeito)
    assert all("Retornamos" not in t for t in defeito)
    # As que não tinham espera chegam iguais.
    assert [p for p in prontas if not checagens.tem_espera(p["texto"])] == [
        p for p in limpas if p["id"] not in {q["id"] for q in com_espera}
    ]


def test_4_a_frase_que_fica_e_a_que_sai():
    texto = "Olá! O valor do anúncio já é o praticado, e os cupons ativos aparecem na própria página do anúncio. Para condições de quantidade, nossa equipe verifica e responde por aqui."
    assert (
        checagens.sem_frases_de_espera(texto)
        == "Olá! O valor do anúncio já é o praticado, e os cupons ativos aparecem na própria página do anúncio."
    )
    sem = "Olá! O reembolso é feito pela própria plataforma da compra. Seguimos à disposição!"
    assert checagens.sem_frases_de_espera(sem) == sem
    assert checagens.so_cortesia_ou_recebimento("Olá! Recebemos sua mensagem sobre o pedido 1.")
    assert not checagens.so_cortesia_ou_recebimento("Olá! Anotamos a sua escolha para o pedido 1.")


def test_4_o_estoque_sem_dado_confiavel_segue_o_prompt():
    """O `POR_CIMA_DA_REGRA` de estoque dizia "dado_indisponivel" sem o dado
    confiável; o prompt diz "as opções disponíveis aparecem para seleção"."""
    for assunto in ("estoque_variacao", "confirmar_variacao"):
        texto = ferramentas.POR_CIMA_DA_REGRA[assunto]
        assert "dado_indisponivel" not in texto
        assert "aparecem para seleção no próprio anúncio" in texto
    assert "aparecem para seleção no próprio anúncio" in prompt.bloco_geral()


def test_4_a_saida_do_modelo_descreve_o_contrato_novo():
    props = saida.esquema()["properties"]
    assert "depois que a pessoa agir" in props["texto"]["description"]
    assert "vazio quando não há nada verdadeiro" in props["texto"]["description"]
    assert "o que a pessoa precisa fazer ou decidir" in props["motivo_pessoa"]["description"]
    assert "confere antes de enviar" not in json.dumps(props, ensure_ascii=False)


# ═══════════════ 5. a orientação errada (avisos) ═══════════════

CANCELAR_PELA_LOJA = [
    # o dev f0537c2d95ac
    "Como o pedido 250925ABCDEF12 já foi postado, a loja não consegue mais cancelá-lo.",
    "A loja não consegue cancelar o pedido depois do envio.",
    "Infelizmente a loja não pode cancelar o pedido já postado.",
    "Não conseguimos mais cancelar, mas você pode pedir a devolução.",
    "Não dá mais para cancelar o pedido.",
    "Não podemos cancelar o seu pedido, pois ele já foi enviado.",
    "A gente não consegue cancelar depois do envio.",
    # A revisão do r5i (21/10/2026): a mesma negativa pela plataforma e no tom
    # formal avisa junto (a orientação proposta não tem negativa).
    "Como o pedido já foi enviado, a Shopee não permite cancelar um pedido já enviado. Ao receber, você pode pedir a devolução.",
    "Não é possível cancelar o pedido, pois ele já foi enviado.",
]
CANCELAR_CERTO = [
    "Como o pedido já foi enviado, para cancelar agora o caminho é a devolução: ao receber, você pode pedir a devolução.",
    # A lente utilidade do r4 (rodada4b R-casos): o cancelamento de PARTE do pedido fica.
    "O pedido já foi postado com a mala e os 2 cadeados, então não é possível cancelar um item agora.",
    "Você não precisa cancelar o pedido.",
    "Como o pedido ainda não foi enviado, o cancelamento é pedido no próprio pedido, em Minhas Compras.",
    "Se não quiser cancelar, é só aguardar.",
    "A loja não pode alterar o endereço depois do envio.",
    "Não cancelamos pedidos por conta própria; o cancelamento é pedido no app.",
]


@pytest.mark.parametrize("texto", CANCELAR_PELA_LOJA)
def test_5_a_loja_que_nao_consegue_cancelar_e_aviso(texto):
    (p,) = checagens.conferir_cancelar_pela_loja(texto)
    assert p.codigo == checagens.CANCELAR_PELA_LOJA and p.suave
    assert "para cancelar agora o caminho é a devolução" in p.motivo


@pytest.mark.parametrize("texto", CANCELAR_CERTO)
def test_5_a_orientacao_aprovada_do_cancelamento_passa(texto):
    assert checagens.conferir_cancelar_pela_loja(texto) == [], texto


@pytest.mark.parametrize(
    ("texto", "cliente", "pega"),
    [
        (
            "A troca é feita pela devolução do próprio pedido, em Minhas Compras.",
            "quero devolver",
            True,
        ),
        ("Para trocar, abra a devolução em Minhas Compras.", "quero meu dinheiro de volta", True),
        (
            "A devolução é pedida em Minhas Compras, e a Shopee analisa e faz o reembolso.",
            "quero devolver",
            False,
        ),
        (
            "A troca é feita pela devolução do próprio pedido, em Minhas Compras.",
            "quero trocar",
            False,
        ),
        ("A troca é feita pela devolução do próprio pedido.", "quero trocar ou devolver", False),
        # A frase aprovada do defeito (sem escolher um).
        (
            "Pode mandar um vídeo curto e fotos por aqui? A equipe analisa e responde por aqui; troca, devolução ou reembolso seguem depois da análise.",
            "veio com defeito, quero devolver",
            False,
        ),
        ("A troca é feita pela devolução do próprio pedido.", "cadê meu pedido", False),
    ],
)
def test_5_a_troca_no_lugar_da_devolucao(texto, cliente, pega):
    achou = checagens.conferir_troca_no_lugar_da_devolucao(
        texto, acao="responder", textos_cliente=[cliente]
    )
    assert bool(achou) is pega, (texto, cliente)
    if pega:
        assert achou[0].suave and achou[0].codigo == checagens.TROCA_NO_LUGAR_DA_DEVOLUCAO


async def test_5_as_duas_nao_saem_sozinhas_e_ficam_limpas_de_bloqueio():
    for texto, cliente in (
        ("A loja não consegue cancelar o pedido depois do envio.", "quero cancelar o pedido"),
        (
            "Olá! A troca é feita pela devolução do próprio pedido, em Minhas Compras.",
            "quero devolver",
        ),
    ):
        auto = await _completo(texto, categoria="cancelamento", modo=MODO_AUTO, cliente=cliente)
        assert not _sai(auto) and auto.status == RASCUNHO_PENDENTE, auto.motivos
        copiloto = await _completo(
            texto, categoria="cancelamento", modo=MODO_COPILOTO, cliente=cliente
        )
        assert copiloto.status == RASCUNHO_PENDENTE and not copiloto.precisa_humano, (
            copiloto.motivos
        )
        assert copiloto.avisos and copiloto.sem_auto


# ═══════════════ 6. os falsos positivos do dev ═══════════════

# O anúncio do dev a6ff4d22739b (as variações como o pacote as mostrou).
RELOGIO_DO_DEV = {
    "item_id": "22299473628",
    "titulo": "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple",
    "variacoes": [
        {"confiavel": True, "disponivel": False, "fonte": "loja", "nome": "Preto, 42mm"},
        {"confiavel": True, "disponivel": True, "fonte": "loja", "nome": "Cinza, 42mm"},
        {"confiavel": True, "disponivel": True, "fonte": "loja", "nome": "Rosa, 42mm"},
        {"confiavel": True, "disponivel": False, "fonte": "loja", "nome": "Cinza, 46mm"},
        {"confiavel": True, "disponivel": True, "fonte": "loja", "nome": "Prata, 46mm"},
    ],
    "variacoes_completas": True,
}
RESPOSTA_A6FF = "O anúncio oferece o Apple Watch Series 11 nos tamanhos 42mm e 46mm, mas não temos uma comparação de medidas entre os dois para indicar qual fica melhor no seu pulso, então a escolha fica a seu critério. Hoje estão disponíveis, em 42mm, as cores Cinza e Rosa, e, em 46mm, a cor Prata (Preto 42mm e Cinza 46mm estão esgotados)."


def test_6_o_parentese_das_variacoes_e_outra_oracao():
    assert checagens.conferir_variacoes(RESPOSTA_A6FF, [RELOGIO_DO_DEV], ["42 ou 46mm?"]) == []
    # O que continua pego: o contrário da lista, fora e dentro do parêntese.
    for errado in (
        "A cor Cinza 46mm está disponível.",
        "A cor Prata está esgotada (o Preto 42mm também).",
        "O Preto 42mm está disponível (o Cinza 46mm está esgotado).",
    ):
        assert checagens.conferir_variacoes(errado, [RELOGIO_DO_DEV], ["42 ou 46mm?"]), errado


def test_6_o_tamanho_da_lista_e_variacao_e_o_nfc_do_nome_continua_caracteristica():
    """ "tem 46mm?" com a lista "Prata, 46mm"… pergunta da VARIAÇÃO (a parte inteira
    do nome); o "tem nfc?" com "A18, Branco NFC" continua pergunta de característica
    (a rodada 4b, achado 3 da lente gate)."""
    assert not checagens.pede_caracteristica(["tem 46mm?"], [RELOGIO_DO_DEV], so_vocabulario=True)
    assert checagens.pede_caracteristica(["tem 46mm?"], [], so_vocabulario=True)
    a18 = {
        "item_id": "1",
        "titulo": "Uranyx A18 Pro Max NFC 16.128 Gb",
        "variacoes": [
            {"nome": "A18, Branco NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
            {"nome": "A18, Laranja NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
        ],
        "variacoes_completas": True,
    }
    assert checagens.pede_caracteristica(["a versão branca tem nfc?"], [a18], so_vocabulario=True)


def test_6_nao_ter_a_comparacao_nao_afirma_medida():
    texto = "mas não temos uma comparação de medidas entre os dois para indicar qual fica melhor"
    assert not checagens.afirma_caracteristica(texto, [RELOGIO_DO_DEV])
    assert checagens.caracteristicas_fora_do_dado(texto, [RELOGIO_DO_DEV]) == []
    assert checagens.afirma_caracteristica("As medidas são 46 x 39 mm.", [RELOGIO_DO_DEV])


def test_6_a_regra_do_assunto_que_o_sistema_traz_prova_se_sozinha():
    """O dev f0537c2d95ac: "evidência citada sem prova (regra:cancelamento)" — a
    orientação do cancelamento está no bloco 1 do sistema."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services.atendimento.agente.contexto import ContextoAgente

    conversa = SimpleNamespace(
        id=uuid4(),
        plataforma="shopee",
        canal="chat",
        pedido_marketplace=None,
        anuncio_id=None,
        dados={},
    )
    ctx = ContextoAgente.da_conversa(conversa, None, modo=MODO_AUTO)
    assert "cancelamento" in prompt.ORIENTACAO and "rastreio" not in prompt.ORIENTACAO
    assert checagens.conferir_evidencias(["regra:cancelamento", "regra:troca_devolucao"], ctx) == []
    (p,) = checagens.conferir_evidencias(["regra:cancelamento", "regra:rastreio", "fato:9"], ctx)
    assert p.motivo == "evidência citada sem prova (regra:rastreio, fato:9)"


@pytest.mark.parametrize(
    ("texto", "pega"),
    [
        # o dev 5812ff61b1e4 (o cliente: "Acredito que acertei com a compra")
        (
            "Que bom saber que está tudo certo com a sua compra! Fico à disposição por aqui se precisar de algo.",
            False,
        ),
        ("Fico feliz que está tudo certo com o seu pedido.", False),
        ("Está tudo certo com o seu pedido.", True),
        ("Ótimo, está tudo certo com seu pedido.", True),
        ("Pode ficar tranquila, está tudo certo com a entrega.", True),
    ],
)
def test_6_o_tudo_certo_que_repete_o_cliente(texto, pega):
    achou = [
        p
        for p in checagens.conferir_estado_do_pedido(texto, None, None)
        if "tudo certo" in p.motivo
    ]
    assert bool(achou) is pega, texto


def test_6_a_uf_restrita_no_pedido_cancelado_nao_e_excecao():
    """O dev b546657edb4d: o pedido cancelado pela restrição; o cliente pergunta do
    estorno — a orientação do reembolso é a resposta."""
    estorno = [_cliente("Qual o tempo para estorno? Ainda nao consta no meu cartao", 5)]
    assert _gat(estorno, uf_restrita=True, postado=acao_pessoa.POSTADO_CANCELADO) == []
    (g,) = _gat(estorno, uf_restrita=True, postado=acao_pessoa.POSTADO_NAO)
    assert g.codigo == "excecao"
    (g,) = _gat(estorno, uf_restrita=True, postado=None)
    assert g.codigo == "excecao"


# O anúncio de 2+ modelos com o NFC por armazenamento na descrição.
DOIS_MODELOS = {
    "item_id": "1",
    "titulo": "Uranyx S5 4.128 ou 8.256 NFC",
    "descricao": "Celular Uranyx.\nVersão 8GB+256GB: com NFC\nVersão 4GB+128GB: sem NFC\nTela 6.5",
    "especificacoes": {
        "campos": [
            {
                "campo": "nfc",
                "valor": "sim",
                "fonte": "anuncio",
                "varia_por_modelo": True,
                "nota": "varia conforme o modelo do anúncio",
            }
        ],
        "conflitos": [],
        "conflito_no_anuncio": False,
    },
}


@pytest.mark.parametrize(
    ("texto", "passa"),
    [
        ("Olá! A versão de 8+256 GB tem NFC.", True),
        ("Olá! A versão de 8 GB de RAM e 256 GB tem NFC; a de 4+128 GB não tem.", True),
        # O valor de OUTRA versão, a versão nenhuma e as duas juntas continuam com o aviso.
        ("Olá! A versão de 4+128 GB tem NFC.", False),
        ("Olá! O aparelho tem NFC.", False),
        ("Olá! As versões 8+256 e 4+128 têm NFC.", False),
    ],
)
def test_6_a_resposta_por_versao_que_a_descricao_da(texto, passa):
    pendencias = checagens.conferir_fonte_produto(texto, [DOIS_MODELOS])
    varia = [p for p in pendencias if "varia conforme o modelo" in p.motivo]
    assert (not varia) is passa, (texto, [p.motivo for p in pendencias])


# ═══════════════ 7. ponta a ponta (sem LLM) ═══════════════


async def _pedido_postado(db: AsyncSession, make_user, *, auto=None, modo=MODO_AUTO, **ligar):
    await _importar_manual_base(db)
    await _pedido_completo(db)  # situação 9, postado, com rastreio
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=modo, **ligar)
    return conversa


# ─── 7.1 a espera no rascunho de pessoa ───


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
@pytest.mark.parametrize(
    ("cliente", "texto", "assunto", "codigo"),
    [
        (
            "consegue mandar com prioridade? preciso da data exata",
            ESPERAS_DO_DEV[6][1].replace("250925ABCDEF12", "{numero_pedido}"),
            "rastreio",
            "dado_indisponivel",
        ),
        (
            "comprei o modelo errado, quero trocar pelo G1 24/256",
            ESPERAS_DO_DEV[3][1].replace("250925ABCDEF12", "{numero_pedido}"),
            "troca_devolucao",
            "aprovar_fora_da_plataforma",
        ),
    ],
)
async def test_7_a_espera_no_rascunho_de_pessoa_vira_aviso(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo, cliente, texto, assunto, codigo
):
    conversa = await _pedido_postado(
        db, make_user, modo=modo, auto_categorias=[assunto], auto_orientacao=[assunto]
    )
    await _msg(db, conversa, cliente)
    api.roteiro = [
        _final(
            acao="pessoa",
            texto=texto,
            assunto=assunto,
            acao_pessoa=codigo,
            motivo_pessoa="decidir o pedido do cliente",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert checagens.ESPERA_NO_RASCUNHO_DE_PESSOA in fatos["checagens"], fatos["checagens"]
    (aviso,) = [a for a in fatos["avisos"] if "promessa de espera" in a]
    assert "quem envia já agiu" in aviso
    # O rascunho de pessoa nunca sai sozinho; a sugestão fica para a pessoa (sem bloquear).
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano and envios == []
    assert PEDIDO_MKT in (r.texto or "") and "{" not in (r.texto or "")
    assert humano.sugestao_pede_pessoa(r)


async def test_7_a_resposta_depois_da_acao_nao_ganha_aviso(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """enviar_arquivo_nf (o exemplo do prompt): a resposta depois de anexar."""
    conversa = await _pedido_postado(db, make_user, auto_categorias=["nota_fiscal"])
    await _msg(db, conversa, "manda o arquivo da nota fiscal por favor")
    api.roteiro = [
        _final(
            acao="pessoa",
            texto="Olá! Segue o arquivo da nota fiscal do pedido {numero_pedido}.",
            assunto="nota_fiscal",
            acao_pessoa="enviar_arquivo_nf",
            motivo_pessoa="anexar o arquivo da NF",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert checagens.ESPERA_NO_RASCUNHO_DE_PESSOA not in fatos["checagens"]
    assert not [a for a in fatos.get("avisos") or [] if "espera" in a]
    assert r.texto == f"Olá! Segue o arquivo da nota fiscal do pedido {PEDIDO_MKT}."
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano and envios == []


# ─── 7.2 o texto vazio de pessoa e a lacuna no motivo ───


@pytest.mark.parametrize(
    ("acao", "cliente", "codigo"),
    [
        # O modelo marcou a ação (o exemplo do prompt: nada a dizer antes da pessoa).
        (
            "pessoa",
            "posso pagar a diferença por fora e vocês me mandam o outro modelo?",
            "aprovar_fora_da_plataforma",
        ),
        # O código marcou a ação (a NF) e o modelo respondeu "responder" vazio.
        ("responder", "manda o arquivo da nota fiscal por favor", None),
    ],
)
async def test_7_o_texto_vazio_de_pessoa_fica_sem_o_erro_do_validador(
    db: AsyncSession, make_user, agente_ligado, api, envios, acao, cliente, codigo
):
    conversa = await _pedido_postado(db, make_user, auto_orientacao=["troca_devolucao"])
    await _msg(db, conversa, cliente)
    api.roteiro = [
        _final(
            acao=acao,
            texto="",
            assunto="troca_devolucao" if codigo else "nota_fiscal",
            acao_pessoa=codigo,
            motivo_pessoa="decidir a troca por fora pagando a diferença" if codigo else "",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip()
    assert r.status == RASCUNHO_BLOQUEADO and r.precisa_humano and envios == []
    assert r.validador_erros == [] and "texto vazio" not in (r.motivo or "")
    assert (r.motivo or "").startswith("ação de pessoa:"), r.motivo
    fatos = _do_agente(r)
    assert not [m for m in fatos["sem_auto"] if "texto vazio" in m]
    # A Caixa Humano pega (sem texto, para pessoa).
    assert humano.sugestao_pede_pessoa(r)


async def test_7_o_responder_vazio_sem_acao_continua_descartado(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """O "ok" do cliente (nada a responder): o DESCARTADO de sempre."""
    conversa = await _pedido_postado(db, make_user)
    await _msg(db, conversa, "ok")
    api.roteiro = [_final(texto="", assunto="agradecimento")]
    r = await ia.gerar_rascunho(db, conversa)
    assert r.status == RASCUNHO_DESCARTADO and not r.precisa_humano, (r.status, r.motivo)


async def test_7_a_lacuna_do_motivo_pessoa_vira_o_numero(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """O dev a0cee7b7a4fd: "ação de pessoa: … — Pedido {numero_pedido} consta como
    devolvido ao remetente" ficou assim na coluna `motivo`."""
    conversa = await _pedido_postado(db, make_user)
    await _msg(db, conversa, "voltou pro remetente? e agora?")
    api.roteiro = [
        _final(
            acao="pessoa",
            texto="",
            assunto="entrega_nao_recebida",
            acao_pessoa="abrir_chamado",
            motivo_pessoa="Pedido {numero_pedido} consta como devolvido ao remetente; sem o {transportadora}",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert f"Pedido {PEDIDO_MKT} consta como devolvido" in (r.motivo or ""), r.motivo
    assert "{" not in (r.motivo or "")


# ─── 7.3 o manual que o consultar_manual devolve ───


def _dados(api_falsa, i: int) -> dict:
    return json.loads(re.sub(r"^<dados[^>]*>|</dados>$", "", api_falsa.resultado(i)))


@pytest.mark.parametrize(
    ("assunto", "aviso"),
    [("cancelamento", True), ("troca_devolucao", True), ("endereco", True), ("defeito", False)],
)
async def test_7_o_manual_chega_sem_a_espera(
    db: AsyncSession, make_user, agente_ligado, api, envios, assunto, aviso
):
    conversa = await _pedido_postado(db, make_user)
    await _msg(db, conversa, "e agora, como faço?")
    api.roteiro = [
        _usa("consultar_manual", assunto=assunto, pergunta=None),
        _final(texto="Olá! Você acompanha o pedido em Minhas Compras.", assunto=assunto),
    ]

    await ia.gerar_rascunho(db, conversa)

    dados = _dados(api, 1)
    assert dados["assunto"] == assunto
    assert not [p for p in dados["respostas_prontas"] if checagens.tem_espera(p["texto"])]
    assert (ferramentas.SEM_ESPERA_DA_REGRA in (dados.get("por_cima_da_regra") or "")) is aviso
    if assunto == "defeito":
        # O pedido de vídeo fica (sem o "Retornamos por aqui").
        assert any("vídeo" in p["texto"] for p in dados["respostas_prontas"])


# ─── 7.4 o que ia para pessoa com o dado na mão ───


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_postado_com_pedido_de_prioridade_e_a_orientacao(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    """O dev 683d991b745c (a nota: "dava para responder orientando o rastreio pelo
    app"): o pedido postado e o pedido de prioridade/data."""
    conversa = await _pedido_postado(db, make_user, modo=modo, auto_categorias=["rastreio"])
    await _msg(db, conversa, "consegue mandar com prioridade? preciso da data exata")
    texto = (
        "Olá! O pedido {numero_pedido} já foi postado. O rastreio é {rastreio}, e o envio e a "
        "previsão de entrega aparecem em Minhas Compras > o pedido > Informações do envio."
    )
    api.roteiro = [_final(texto=texto, assunto="rastreio", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (r.motivo, fatos["sem_auto"])
    assert RASTREIO in r.texto and not fatos.get("avisos"), fatos.get("avisos")
    if modo == MODO_AUTO:
        assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado"
    else:
        assert envios == []


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_prazo_antes_da_compra_pelo_cep(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, modo
):
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=modo, auto_categorias=["prazo_antes_compra"])
    await _msg(db, conversa, "chega em quantos dias aqui em Campinas?")
    texto = "Olá! O prazo de entrega para o seu CEP aparece no próprio anúncio, ao informar o CEP."
    api.roteiro = [
        _final(texto=texto, assunto="prazo_antes_compra", evidencias=[f"ficha:{ITEM_A18}"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert r.texto == texto and r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (
        r.motivo,
        fatos["sem_auto"],
    )
    assert not fatos.get("avisos"), fatos.get("avisos")
    assert (envios != []) is (modo == MODO_AUTO)


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_a_retirada_e_so_pela_plataforma(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, modo
):
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=modo, auto_categorias=["outro"])
    await _msg(db, conversa, "posso retirar aí na loja?")
    texto = (
        "Olá! Não fazemos retirada na loja: a compra, o pagamento e a entrega são feitos só pela "
        "Shopee, no endereço que você informar no pedido."
    )
    api.roteiro = [_final(texto=texto, assunto="outro")]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert r.status == RASCUNHO_PENDENTE and r.validador_ok, (r.motivo, r.validador_erros)
    assert not [m for m in fatos["sem_auto"] if "fora da plataforma" in m or "caminho" in m], fatos[
        "sem_auto"
    ]
    if modo == MODO_COPILOTO:
        assert not r.precisa_humano and not fatos.get("avisos"), (r.motivo, fatos.get("avisos"))


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_a_decisao_ja_tomada_so_confirma_o_caminho(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    """O dev d3f096c95058 (a nota: "a cliente já decidiu devolver e recomprar e só
    agradeceu; bastava confirmar o caminho da devolução no app")."""
    conversa = await _pedido_postado(db, make_user, modo=modo, auto_orientacao=["troca_devolucao"])
    await _msg(db, conversa, "ok, obrigada! vou devolver pelo app e comprar o outro modelo")
    texto = (
        "Perfeito! A devolução é pedida em Minhas Compras > o pedido, e a Shopee analisa e faz "
        "o reembolso pela mesma forma de pagamento."
    )
    api.roteiro = [_final(texto=texto, assunto="troca_devolucao", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert r.texto == texto and r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (
        r.motivo,
        fatos["sem_auto"],
    )
    assert (envios != []) is (modo == MODO_AUTO), _decisao(r)


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_o_agradecimento_que_repete_o_cliente(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, modo
):
    """O dev 5812ff61b1e4."""
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=modo, auto_categorias=["agradecimento"])
    await _msg(db, conversa, "Acredito que acertei com a compra. Grato. ..Não tenho mais dúvidas.")
    texto = "Que bom saber que está tudo certo com a sua compra! Fico à disposição por aqui se precisar de algo."
    api.roteiro = [_final(texto=texto, assunto="agradecimento")]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert checagens.ESTADO_SEM_DADO not in fatos["checagens"], fatos["checagens"]
    assert r.texto == texto and r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (
        r.motivo,
        fatos["sem_auto"],
    )


# ─── 7.5 a lista de variações (o relógio do dev, de verdade pela loja falsa) ───

ITEM_RELOGIO = "22299473628"
TITULO_RELOGIO = "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple"
# (model_id, cor, tamanho, estoque) — o anúncio do dev a6ff4d22739b.
RELOGIO = [
    (9101, "Preto", "42mm", 0),
    (9102, "Cinza", "42mm", 3),
    (9103, "Rosa", "42mm", 2),
    (9104, "Cinza", "46mm", 0),
    (9105, "Prata", "46mm", 4),
]
# O dev 147a255a2c13 (o nome da variação com a pontuação).
SE2 = [(9201, "Preto", "44mm", 0), (9202, "Preto (Tecido)", "44mm", 0)]


def _modelos(variacoes: list[tuple[int, str, str, int]]) -> dict:
    cores = list(dict.fromkeys(c for _, c, _, _ in variacoes))
    tamanhos = list(dict.fromkeys(t for _, _, t, _ in variacoes))
    return {
        "tier_variation": [
            {"name": "Cor", "option_list": [{"option": c} for c in cores]},
            {"name": "Tamanho", "option_list": [{"option": t} for t in tamanhos]},
        ],
        "model": [
            {
                "model_id": mid,
                "tier_index": [cores.index(c), tamanhos.index(t)],
                "model_sku": f"aw-{mid}",
                "model_status": "MODEL_NORMAL",
                "stock_info_v2": {"summary_info": {"total_available_stock": e}},
                "price_info": [{"current_price": 2999.9, "original_price": 3999.9}],
            }
            for mid, c, t, e in variacoes
        ],
    }


async def _relogio(
    db: AsyncSession, make_user, redis_mem, lojas, variacoes, *, titulo=TITULO_RELOGIO
):
    """A conversa no anúncio do relógio: a loja falsa com as variações e os vínculos
    do cadastro (o estoque coerente: a variação é "confiável")."""
    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Maria")
    conversa.anuncio_id = ITEM_RELOGIO
    conversa.anuncio_titulo = None
    await db.commit()
    integ = await db.get(Integration, conversa.integration_id)
    for mid, cor, tamanho, estoque in variacoes:
        produto = Product(
            user_id=integ.user_id,
            sku=f"aw-{mid}",
            name=f"Apple Watch Series 11 {tamanho} - {cor}",
            stock=estoque,
        )
        db.add(produto)
        await db.flush()
        db.add(
            ProductLink(
                user_id=integ.user_id,
                product_id=produto.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=ITEM_RELOGIO,
                variation_id=str(mid),
                external_sku=f"aw-{mid}",
                listing_title=f"{titulo} - {cor}, {tamanho}",
                stock=estoque,
                last_sync_status=LinkSyncStatus.OK,
            )
        )
    await db.commit()
    item = {
        "item_id": int(ITEM_RELOGIO),
        "item_name": titulo,
        "description": "Apple Watch Series 11.\nTela Retina sempre ativa.",
        "has_model": True,
        "item_status": "NORMAL",
        "attribute_list": [],
        "brand": {"original_brand_name": "Apple"},
    }
    lojas.shopee[ITEM_RELOGIO] = item
    lojas.modelos[ITEM_RELOGIO] = _modelos(variacoes)
    bruta = ficha.ficha_bruta_shopee(item)
    redis_mem.dados[ficha.chave_ficha("shopee", integ.id, ITEM_RELOGIO)] = json.dumps(
        {**bruta, "buscada_em": datetime.now(UTC).isoformat()}, ensure_ascii=False
    )
    return conversa


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
@pytest.mark.parametrize(
    ("variacoes", "cliente", "texto"),
    [
        # a6ff4d22739b: o tamanho pela lista completa, com o parêntese.
        (RELOGIO, "42 ou 46mm?", RESPOSTA_A6FF),
        (
            RELOGIO,
            "tem 46mm?",
            "Olá! Sim, em 46mm temos a cor Prata disponível; o Cinza 46mm está esgotado.",
        ),
        # 147a255a2c13: as duas opções esgotadas, o nome com "(Tecido)".
        (
            SE2,
            "tem branco?",
            "Olá! No anúncio, as opções listadas hoje são Preto, 44mm e Preto (Tecido), 44mm, e as duas estão esgotadas. Não temos previsão de reposição e não há outra cor, como branco, listada neste anúncio no momento.",
        ),
    ],
    ids=["a6ff-parentese", "46mm", "147a-tecido"],
)
async def test_7_a_lista_de_variacoes_responde(
    db: AsyncSession,
    make_user,
    agente_ligado,
    api,
    envios,
    redis_mem,
    lojas,
    modo,
    variacoes,
    cliente,
    texto,
):
    conversa = await _relogio(db, make_user, redis_mem, lojas, variacoes)
    await _ligar(db, conversa, modo=modo, auto_categorias=["estoque_variacao"])
    await _msg(db, conversa, cliente)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_RELOGIO),
        _final(
            texto=texto,
            assunto="estoque_variacao",
            evidencias=[f"variacoes:{ITEM_RELOGIO}", f"ficha:{ITEM_RELOGIO}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    lidas = _dados(api, 1)["variacoes"]
    assert lidas["completas"] and all(v["confiavel"] for v in lidas["variacoes"]), lidas
    fatos = _do_agente(r)
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (r.motivo, fatos["sem_auto"])
    assert not fatos.get("avisos") and not fatos["sem_auto"], (
        fatos.get("avisos"),
        fatos["sem_auto"],
    )
    assert (envios != []) is (modo == MODO_AUTO), _decisao(r)


async def test_7_a_lista_de_variacoes_continua_pegando_o_contrario(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    conversa = await _relogio(db, make_user, redis_mem, lojas, RELOGIO)
    await _ligar(db, conversa, modo=MODO_AUTO, auto_categorias=["estoque_variacao"])
    await _msg(db, conversa, "tem 46mm?")
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_RELOGIO),
        _final(
            texto="Olá! Em 46mm temos a cor Cinza disponível (a Prata está esgotada).",
            assunto="estoque_variacao",
            evidencias=[f"variacoes:{ITEM_RELOGIO}"],
        ),
    ]
    r = await ia.gerar_rascunho(db, conversa)
    assert "variacao_contradiz" in _do_agente(r)["checagens"] and envios == []


# ─── 7.6 a orientação errada do eval ───


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_a_loja_nao_consegue_cancelar_e_aviso_e_a_regra_do_sistema_prova(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    """O dev f0537c2d95ac: a resposta citou "regra:cancelamento" sem chamar o
    `consultar_manual` (o falso positivo) e disse "a loja não consegue cancelar"
    (o erro de verdade)."""
    conversa = await _pedido_postado(db, make_user, modo=modo, auto_orientacao=["cancelamento"])
    await _msg(db, conversa, "poderia cancelar o pedido?")
    texto = (
        "Como o pedido {numero_pedido} já foi postado, a loja não consegue mais cancelá-lo. Ao "
        "receber, você pode pedir a devolução ou o reembolso em Minhas Compras, no próprio "
        "pedido, e a Shopee analisa e devolve pela mesma forma de pagamento."
    )
    api.roteiro = [
        _final(texto=texto, assunto="cancelamento", evidencias=["pedido", "regra:cancelamento"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert checagens.EVIDENCIA_SEM_PROVA not in fatos["checagens"], fatos["checagens"]
    assert checagens.CANCELAR_PELA_LOJA in fatos["checagens"]
    (aviso,) = [a for a in fatos["avisos"] if "não dá para cancelar" in a]
    assert "para cancelar agora o caminho é a devolução" in aviso
    assert r.status == RASCUNHO_PENDENTE and envios == []
    assert r.precisa_humano is (modo == MODO_AUTO)


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_7_a_troca_no_lugar_da_devolucao_e_aviso(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    conversa = await _pedido_postado(db, make_user, modo=modo, auto_orientacao=["troca_devolucao"])
    await _msg(db, conversa, "não gostei, quero devolver")
    api.roteiro = [
        _final(
            texto="Olá! A troca é feita pela devolução do próprio pedido, em Minhas Compras.",
            assunto="troca_devolucao",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert checagens.TROCA_NO_LUGAR_DA_DEVOLUCAO in fatos["checagens"]
    assert r.status == RASCUNHO_PENDENTE and envios == []
    assert r.precisa_humano is (modo == MODO_AUTO)


# ─── 7.7 os números do título e a ficha no estoque (os bugs 2 e 3, pelo laço) ───


async def test_7_a_ram_e_o_armazenamento_do_titulo_nao_sao_inventados(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O título da Kia: "Uranyx A18 Pro Max NFC 16.128 Gb" — "16 GB de RAM e 128 GB"
    era "número que não veio do sistema" (o "16.128" lido junto)."""
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=MODO_COPILOTO)
    await _msg(db, conversa, "quanto de memória ele tem?")
    texto = "Olá! Conforme o título do anúncio, ele tem 16 GB de RAM e 128 GB de armazenamento."
    api.roteiro = [_final(texto=texto, assunto="espec_celular", evidencias=[f"ficha:{ITEM_A18}"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == texto and r.status == RASCUNHO_PENDENTE and r.validador_ok, (
        r.motivo,
        r.validador_erros,
    )
    assert "número que não veio do sistema" not in (r.motivo or "")


async def test_7_a_cor_com_o_nome_da_variacao_sai_no_estoque(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O nome da variação ("Laranja NFC") numa resposta de estoque não é a ficha no
    automático (o bug 3): sai sozinha com o assunto liberado."""
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=MODO_AUTO, auto_categorias=["estoque_variacao"])
    await _msg(db, conversa, "tem o laranja?")
    texto = "Olá! Temos sim a opção Laranja NFC disponível neste anúncio."
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_A18),
        _final(texto=texto, assunto="estoque_variacao", evidencias=[f"variacoes:{ITEM_A18}"]),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    fatos = _do_agente(r)
    assert "ficha_no_automatico" not in fatos["checagens"], fatos["checagens"]
    assert [e["texto"] for e in envios] == [texto] and _decisao(r) == "enviado", (
        r.motivo,
        fatos["sem_auto"],
    )
