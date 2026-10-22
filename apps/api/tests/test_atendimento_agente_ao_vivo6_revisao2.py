"""Revisão 2 da 6ª rodada ao vivo (22/10/2026) — os achados do caminho AUTO e da robustez.

1. Item 23: o "sem previsão de reposição / data de chegada" passava todas as camadas de
   código com a variação DISPONÍVEL (a `variacoes._RESSALVA` o tira da conta e o "sem
   previsão" não é negação): só o modelo conferia de qual variação a frase falava. A
   regra SUAVE `checagens.conferir_sem_previsao` confere; e a POLÍTICA DE ESTOQUE do
   verificador só vai na entrada com a variação esgotada E confiável citada no texto ou
   na pergunta (ou o produto inteiro esgotado), nunca no esgotado do pedido já feito.
2. A promessa depois do "sem previsão, mas…": a negação da janela de 45 caracteres
   atravessava a vírgula e o "mas" — agora só a da MESMA oração livra; e as formas novas
   ("repomos", "costuma voltar rápido", "próxima remessa", "temporariamente esgotada").
   Os pares da equipe com a promessa de reposição ou a recusa da entrega não vão ao
   agente (`pares.ensina_o_que_o_agente_nao_faz`).
3. A recusa da entrega e o "ao receber" a quem reclama que não chegou e pede o dinheiro:
   regras SUAVES (só o modelo segurava).
4. Robustez: o cartão da plataforma nunca derruba a tela nem o agente; a régua do
   "demorando" volta a ser linear.

Sem modelo, sem rede: o caminho do agente roda até a trava, sem o verificador.
"""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.routers import atendimento as router_atendimento
from app.services.atendimento import cartoes_plataforma, ia, variacoes
from app.services.atendimento.agente import acao_pessoa, checagens, pares
from app.services.atendimento.agente import verificador as v
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_ao_vivo5 import KIA, _caminho, _proposta, _rascunho
from tests.test_atendimento_agente_pares import _buscar, _cenario, _par

ITEM = "58258056732"
PERGUNTA = "Quando chega o azul? Quero só se for dessa cor!"


def _var(nome: str, disponivel: bool | None, confiavel: bool = True) -> dict:
    return {"nome": nome, "disponivel": disponivel, "confiavel": confiavel, "fonte": "loja"}


AZUL_ESGOTADA = [_var("Azul", False), _var("Preto", True), _var("Dourado", True)]
AZUL_DISPONIVEL = [_var("Azul", True), _var("Preto", False), _var("Dourado", True)]
AZUL_NAO_CONFIAVEL = [_var("Azul", False, False), _var("Preto", False), _var("Dourado", True)]
SEM_A_AZUL = [_var("Preto", False), _var("Dourado", True)]
TODAS_ESGOTADAS = [_var("Azul", False), _var("Preto", False), _var("Dourado", False)]


def _ficha(variacoes_: list[dict], completas: bool = True) -> list[dict]:
    return [
        {
            "de_onde": "cartão de produto que o cliente mandou",
            "item_id": ITEM,
            "titulo": "Uranyx F112 Pro 5g 256 Gb Memoria 24gb NFC Resistente",
            "variacoes": variacoes_,
            "variacoes_completas": completas,
        }
    ]


def _codigos(pendencias: list) -> list[tuple[str, bool, bool]]:
    return [(p.codigo, p.so_auto, p.bloqueia) for p in pendencias]


CONTRADIZ = (checagens.VARIACAO_CONTRADIZ, False, False)
SUAVE = (checagens.VARIACAO_SEM_DADO, True, False)

# O 0acd80a6 (Kia, 22/10 01:39): o rascunho real, bom com a Azul esgotada.
RASCUNHO_0ACD = (
    "Olá! No momento a cor Azul está esgotada neste anúncio e não temos previsão de reposição, "
    "então não há data de chegada para essa cor. Você pode acompanhar o anúncio na Shopee caso "
    "a disponibilidade mude."
)


# ═══════════════ 1. o "sem previsão" da variação de que a frase fala ═══════════════


@pytest.mark.parametrize(
    ("texto", "variacoes_", "completas", "cliente", "esperado"),
    [
        # A Azul DISPONÍVEL citada na oração: contradiz (pessoa nos dois modos).
        (
            "Olá! No momento não temos previsão de reposição da cor Azul; acompanhe o anúncio.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            CONTRADIZ,
        ),
        (
            "Olá! Ainda não há data de chegada da Azul, sem previsão de reposição.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            CONTRADIZ,
        ),
        (
            "Olá! A Azul não está mais à venda neste anúncio e não temos previsão de reposição.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            CONTRADIZ,
        ),
        (
            "Olá! A Azul acabou de esgotar e ainda não há previsão de reposição.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            CONTRADIZ,
        ),
        # "Essa cor"/"dessa cor" é a do cliente (a Azul disponível): suave.
        (
            "Olá! Essa cor não tem previsão de reposição; você pode acompanhar o anúncio.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            SUAVE,
        ),
        (
            "Olá! Não há previsão de reposição dessa cor no momento; acompanhe o anúncio.",
            AZUL_DISPONIVEL,
            True,
            "tem azul?",
            SUAVE,
        ),
        (
            "Olá! Para essa cor não temos data de chegada nem previsão de reposição.",
            AZUL_DISPONIVEL,
            True,
            PERGUNTA,
            SUAVE,
        ),
        # A Azul com confiavel=false (só aparece indisponível no anúncio): suave.
        (
            "Olá! Não há previsão de reposição para a Azul; acompanhe o anúncio.",
            AZUL_NAO_CONFIAVEL,
            True,
            PERGUNTA,
            SUAVE,
        ),
        # A Azul fora da lista (incompleta ou completa): suave.
        ("Olá! Não temos previsão de reposição da Azul.", SEM_A_AZUL, False, PERGUNTA, SUAVE),
        (
            "Olá! A Azul está esgotada e sem previsão de reposição.",
            SEM_A_AZUL,
            True,
            PERGUNTA,
            SUAVE,
        ),
        # O assunto que não é a variação: a película, a capinha, o carregador.
        (
            "Olá! A película está esgotada e sem previsão de reposição; acompanhe o anúncio.",
            AZUL_ESGOTADA,
            True,
            "Vocês vendem a película desse modelo?",
            SUAVE,
        ),
        (
            "Olá! A capinha avulsa está esgotada, sem previsão de reposição.",
            AZUL_ESGOTADA,
            True,
            "Tem capinha pra vender separado?",
            SUAVE,
        ),
        (
            "Olá! O carregador está esgotado e não há previsão de reposição.",
            AZUL_ESGOTADA,
            True,
            "Vende o carregador sozinho?",
            SUAVE,
        ),
    ],
)
def test_o_sem_previsao_de_uma_variacao_sem_o_esgotado_confiavel(
    texto, variacoes_, completas, cliente, esperado
):
    r = checagens.conferir_sem_previsao(texto, _ficha(variacoes_, completas), [cliente])
    assert _codigos(r)[:1] == [esperado], texto


@pytest.mark.parametrize(
    ("texto", "variacoes_", "cliente"),
    [
        (RASCUNHO_0ACD, AZUL_ESGOTADA, PERGUNTA),
        (
            "Olá! A Azul está esgotada no momento e ainda não temos data para ela voltar; vale "
            "acompanhar o anúncio.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! Essa cor está esgotada e sem previsão de reposição; acompanhe o anúncio.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! A Azul está esgotada. Não há previsão de reposição; as cores disponíveis são "
            "Preto e Dourado.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        # O produto inteiro esgotado (todas as variações, confiáveis): sem citar a cor.
        (
            "Olá! No momento não temos previsão de reposição deste produto, e as três cores "
            "aparecem como indisponíveis no anúncio.",
            TODAS_ESGOTADAS,
            "Vai voltar?",
        ),
        (
            "Olá! A cor Azul está esgotada; ainda não sabemos quando volta. Você pode acompanhar "
            "o anúncio.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! A Preta e a Dourada estão disponíveis, e a Azul está esgotada, sem previsão.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! A Azul está esgotada, mas a Preta e a Dourada estão disponíveis; não há "
            "previsão de reposição da Azul.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! Ela está esgotada e sem previsão de reposição. Você pode acompanhar o anúncio.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        (
            "Olá! A Azul acabou no momento e a loja não tem uma data para ela voltar; vale ficar "
            "de olho no anúncio.",
            AZUL_ESGOTADA,
            PERGUNTA,
        ),
        # A previsão de chegada do PEDIDO não é estoque.
        (
            "Olá! O seu pedido foi postado e a previsão de chegada aparece em Minhas Compras > o "
            "pedido.",
            AZUL_DISPONIVEL,
            "Quando chega meu pedido?",
        ),
        (
            "Olá! O pedido segue em trânsito, sem previsão de entrega atualizada; acompanhe em "
            "Minhas Compras.",
            AZUL_DISPONIVEL,
            "Cadê meu pedido?",
        ),
        (
            "Olá! O prazo para chegar aparece no anúncio ao informar o CEP.",
            [],
            "Chega em quanto tempo?",
        ),
        # Sem tema: nada a conferir.
        ("Olá! A cor Azul está disponível.", AZUL_DISPONIVEL, PERGUNTA),
    ],
)
def test_o_sem_previsao_da_esgotada_confiavel_ou_fora_do_estoque_passa(texto, variacoes_, cliente):
    assert checagens.conferir_sem_previsao(texto, _ficha(variacoes_), [cliente]) == [], texto


def test_sem_a_lista_de_variacoes_o_sem_previsao_e_suave():
    texto = "Olá! Este produto está esgotado e sem previsão de reposição."
    assert _codigos(checagens.conferir_sem_previsao(texto, [], ["tem?"])) == [SUAVE]
    sem_lista = [{"item_id": ITEM, "titulo": "Air Fryer", "variacoes": None}]
    assert _codigos(checagens.conferir_sem_previsao(texto, sem_lista, ["tem?"])) == [SUAVE]


def _permitir(fichas: list[dict]) -> tuple[list[str], list[str]]:
    return [f"ficha:{ITEM}"], [json.dumps(fichas, ensure_ascii=False)]


PISTAS_ESTOQUE = {"pistas_turno": ["estoque_variacao"], "pistas_orientacao": []}
KIA_ESTOQUE = SimpleNamespace(
    auto_categorias=[*KIA.auto_categorias, "estoque_variacao"],
    auto_agente=[*KIA.auto_agente, "estoque_variacao"],
    auto_orientacao=KIA.auto_orientacao,
)


@pytest.mark.parametrize(
    ("texto", "variacoes_"),
    [
        (
            "Olá! No momento não temos previsão de reposição da cor Azul; acompanhe o anúncio.",
            AZUL_DISPONIVEL,
        ),
        (
            "Olá! Essa cor não tem previsão de reposição; você pode acompanhar o anúncio.",
            AZUL_DISPONIVEL,
        ),
        ("Olá! Não há previsão de reposição para a Azul; acompanhe o anúncio.", AZUL_NAO_CONFIAVEL),
    ],
)
async def test_no_caminho_auto_o_sem_previsao_errado_nao_sai_sozinho(texto, variacoes_):
    """O caminho inteiro no automático: chegava ao modelo sem pendência; agora pessoa, o
    verificador nem roda e a trava fecha."""
    fichas = _ficha(variacoes_)
    p = _proposta(texto, "estoque_variacao", [f"ficha:{ITEM}"])
    final, chk, ctx, base, conversa = await _caminho(
        p, [PERGUNTA], fichas=fichas, permitir=_permitir(fichas)
    )
    assert any(
        x.codigo in (checagens.VARIACAO_SEM_DADO, checagens.VARIACAO_CONTRADIZ)
        for x in chk.pendencias
    ), texto
    assert final.precisa_humano and final.sem_auto
    assert v.por_que_nao_verifica(final, p, ctx, base, conversa) == v.NAO_RODA_PESSOA
    assert ia.trava_do_agente(_rascunho(final, ctx, **PISTAS_ESTOQUE), KIA_ESTOQUE) == "sem_auto"


async def test_no_caminho_auto_o_0acd80a6_continua_indo_ao_verificador():
    fichas = _ficha(AZUL_ESGOTADA)
    p = _proposta(RASCUNHO_0ACD, "estoque_variacao", [f"ficha:{ITEM}"])
    final, chk, ctx, base, conversa = await _caminho(
        p, [PERGUNTA], fichas=fichas, permitir=_permitir(fichas)
    )
    assert chk.pendencias == [] and not final.precisa_humano and final.sem_auto == []
    assert v.por_que_nao_verifica(final, p, ctx, base, conversa) is None


# ═══════════════ 1b. a POLÍTICA DE ESTOQUE só da esgotada confiável citada ═══════════════


def _evid(variacoes_: list[dict]) -> list[str]:
    return [json.dumps({"variacoes": {"completas": True, "variacoes": variacoes_}})]


@pytest.mark.parametrize(
    ("variacoes_", "texto", "perguntas", "vale"),
    [
        # A esgotada confiável citada no texto ou na pergunta.
        (AZUL_ESGOTADA, RASCUNHO_0ACD, [PERGUNTA], True),
        (
            AZUL_ESGOTADA,
            "Olá! Essa cor está esgotada e sem previsão de reposição.",
            [PERGUNTA],
            True,
        ),
        (AZUL_ESGOTADA, "Olá! Ela está esgotada.", ["tem a azul?"], True),
        # O produto inteiro esgotado: sem citar.
        (TODAS_ESGOTADAS, "Olá! Sem previsão de reposição deste produto.", ["vai voltar?"], True),
        # A Azul disponível (o Preto é o esgotado): a política não vale para ela.
        (AZUL_DISPONIVEL, RASCUNHO_0ACD, [PERGUNTA], False),
        (AZUL_DISPONIVEL, "Olá! Essa cor não tem previsão de reposição.", [PERGUNTA], False),
        # O confiavel=false não é a esgotada da política.
        (AZUL_NAO_CONFIAVEL, "Olá! Não há previsão de reposição para a Azul.", [PERGUNTA], False),
        # A película (a Azul esgotada é a do aparelho): ninguém cita a Azul.
        (
            AZUL_ESGOTADA,
            "Olá! A película está esgotada e sem previsão de reposição.",
            ["Vocês vendem a película desse modelo?"],
            False,
        ),
        # O esgotado do pedido já feito é decisão de pessoa.
        (
            AZUL_ESGOTADA,
            "Olá! A cor Azul do seu pedido está esgotada e sem previsão de reposição.",
            ["Comprei o azul, quando vocês vão enviar?"],
            False,
        ),
        # A pergunta "ainda tem?" sem citar o Preto esgotado (o fresco-L08).
        (
            [_var("Preto", False), _var("Azul", True), _var("Branco", True)],
            "Olá! Esse é o último em estoque.",
            ["ainda tem?"],
            False,
        ),
        # O boa-17: "tem preto?" e o Preto esgotado.
        (
            [_var("Preto", False), _var("Azul", True), _var("Branco", True)],
            "Olá! No momento a cor preta está esgotada. Temos azul e branco disponíveis.",
            ["tem preto?"],
            True,
        ),
    ],
)
def test_a_politica_so_com_a_esgotada_confiavel_citada(variacoes_, texto, perguntas, vale):
    assert (
        v.politica_de_estoque_vale(
            _evid(variacoes_),
            texto=texto,
            assunto="estoque_variacao",
            declarado="estoque_variacao",
            perguntas=perguntas,
        )
        is vale
    ), texto


def test_a_politica_na_entrada_usa_as_perguntas_do_turno():
    def entrada(variacoes_: list[dict], pergunta: str) -> str:
        resumo = v.ResumoDoPacote(
            plataforma="Shopee",
            agora="22/10/2026 01:39",
            perguntas=[pergunta],
            conversa=[f"[22/10 01:38] Cliente: {pergunta}"],
            evidencias=_evid(variacoes_),
            plataforma_codigo="shopee",
        )
        return v.montar_entrada(
            acao="responder",
            acao_pessoa=None,
            texto="Olá! Essa cor está esgotada e sem previsão de reposição.",
            resumo=resumo,
            assunto="estoque_variacao",
        )

    assert v.POLITICA_DE_ESTOQUE in entrada(AZUL_ESGOTADA, PERGUNTA)
    assert v.POLITICA_DE_ESTOQUE not in entrada(AZUL_DISPONIVEL, PERGUNTA)
    assert v.POLITICA_DE_ESTOQUE not in entrada(AZUL_ESGOTADA, "tem o preto?")
    # O SISTEMA diz o mesmo escopo.
    assert "disponivel=false e confiavel=true, citada pelo nome no texto ou na pergunta" in (
        " ".join(v.SISTEMA.split())
    )
    assert "confiavel=true" in v.POLITICA_DE_ESTOQUE


# ═══════════════ 2. a promessa depois do "sem previsão, mas…" ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        # A promessa depois do "mas": a negação de antes não a livra.
        "Olá! A Azul está esgotada; não há previsão, mas ela volta ao estoque logo.",
        "Olá! Sem previsão exata, mas a Azul volta ao estoque já já.",
        "Olá! A Azul está esgotada. Não temos previsão de reposição, mas vamos repor a Azul "
        "assim que possível.",
        "Olá! A Azul está esgotada; não temos data, mas a reposição já está programada.",
        "Olá! A Azul está esgotada no momento e não há previsão de reposição, mas chegam mais "
        "ainda nesta quinzena.",
        "Olá! A Azul está esgotada; ainda não temos previsão, mas a Azul volta para o estoque "
        "rapidinho.",
        # As formas novas.
        "Olá! A cor Azul está esgotada no momento, mas repomos logo.",
        "Olá! A Azul esgotou; ainda sem previsão, mas costuma voltar rápido.",
        "Olá! A cor Azul está esgotada, sem previsão de reposição por enquanto, mas geralmente a "
        "gente repõe toda semana.",
        "Olá! A Azul está temporariamente esgotada, sem previsão de reposição.",
        "Olá! A Azul está esgotada. Não temos previsão de reposição, mas normalmente ela "
        "reaparece no anúncio em seguida.",
        "Olá! A Azul está esgotada; não temos previsão de reposição, mas a próxima remessa já "
        "foi pedida.",
        "Olá! A Azul está esgotada no momento. Sem previsão de reposição, mas é questão de dias.",
        "Olá! A Azul está esgotada; sem previsão de reposição, mas ela sempre volta.",
        "Olá! A Azul está esgotada e sem previsão oficial de reposição, mas a expectativa é que "
        "volte logo.",
        "Olá! O Azul acabou por enquanto: estamos aguardando o fornecedor.",
        # As respostas reais da equipe (60 dias): o mês por extenso.
        "Não temos, reposição apenas em novembro",
        "Não, somente novembro teremos reposição",
        "Não, teremos reposição a partir de novembro",
        "no momento não temos, logo chegara reposição!",
    ],
)
def test_a_promessa_de_reposicao_depois_da_negacao_de_outra_oracao(texto):
    assert variacoes._promete_estoque(variacoes._normal(texto)) is True, texto
    assert variacoes.promete_reposicao(texto) is True, texto


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! No momento a cor Azul está esgotada e não temos previsão de reposição, então não "
        "há data de chegada para essa cor.",
        "Infelizmente não, e a reposição ainda não tem data.",
        "Não, a reposição não tem previsão.",
        "Não temos a Azul e nem previsão de reposição.",
        "Não conseguimos informar quando haverá reposição.",
        "A Azul está esgotada. Quando houver reposição, ela aparece no anúncio.",
        "Quando essa opção voltar ao estoque, ela aparece de novo para seleção no próprio anúncio.",
        "Não temos estoque dessa cor e não há previsão de volta.",
        # Fora do estoque: o pacote que volta, o aparelho que volta a funcionar.
        "Se a entrega falhar, normalmente o pacote volta para a loja.",
        "Desligue e ligue, que ele volta a funcionar rapidinho.",
        "O seu pedido geralmente chega em poucos dias úteis.",
    ],
)
def test_a_negacao_da_mesma_oracao_e_o_que_nao_e_estoque_nao_prometem(texto):
    assert variacoes._promete_estoque(variacoes._normal(texto)) is False, texto
    assert variacoes.promete_reposicao(texto) is False, texto


def test_as_ultimas_unidades_e_a_reserva_nao_sao_reposicao_para_os_pares():
    """A promessa da conferência inteira continua (a reserva, as últimas unidades); o
    filtro dos pares só tira a reposição."""
    texto = "São as últimas unidades do branco, reservamos uma para você."
    assert variacoes._promete_estoque(variacoes._normal(texto)) is True
    assert variacoes.promete_reposicao(texto) is False


async def test_no_caminho_auto_a_promessa_depois_do_mas_nao_sai_sozinha():
    fichas = _ficha(AZUL_ESGOTADA)
    texto = "Olá! A Azul está esgotada; não há previsão, mas ela volta ao estoque logo."
    p = _proposta(texto, "estoque_variacao", [f"ficha:{ITEM}"])
    final, chk, ctx, base, conversa = await _caminho(
        p, [PERGUNTA], fichas=fichas, permitir=_permitir(fichas)
    )
    motivo = "promessa de estoque (últimas unidades, reserva ou reposição)"
    assert motivo in [x.motivo for x in chk.pendencias]
    assert final.precisa_humano and motivo in final.sem_auto
    assert ia.trava_do_agente(_rascunho(final, ctx, **PISTAS_ESTOQUE), KIA_ESTOQUE) == "sem_auto"


# ═══════════════ 2b. os pares da equipe que ensinam o que o agente não faz ═══════════════

RECUSAS_DA_EQUIPE = [
    "pode recusar o pedido quando chegar!",
    "Agora o único jeito é recusar a entrega",
    "Para cancelar, deve-se recusar o recebimento do produto no ato da entrega.",
]


@pytest.mark.parametrize(
    "resposta",
    [
        *RECUSAS_DA_EQUIPE,
        "Não temos, reposição apenas em novembro",
        "No momento não temos, logo chegará reposição!",
    ],
)
def test_o_par_que_ensina_a_recusa_ou_promete_reposicao_fica_fora(resposta):
    assert pares.ensina_o_que_o_agente_nao_faz(resposta) is True
    contagem: dict[str, int] = {}
    cliente = "Quero cancelar o pedido, ainda dá tempo de cancelar?"
    assert (
        pares._filtro(cliente, resposta, plataforma="shopee", canal="chat", contagem=contagem)
        is False
    )
    assert contagem.get("proibidos") == 1


@pytest.mark.parametrize(
    "resposta",
    [
        "Olá! No momento não temos previsão de reposição dessa cor; acompanhe o anúncio.",
        "Por favor, não recuse a entrega: ao receber, peça a devolução em Minhas Compras.",
        "Se você não receber o pedido até a data prevista, abra uma solicitação.",
    ],
)
def test_o_par_de_sempre_continua(resposta):
    assert pares.ensina_o_que_o_agente_nao_faz(resposta) is False


async def test_a_busca_nao_serve_o_par_ja_indexado_que_ensina_a_recusa(db: AsyncSession, make_user):
    cen = await _cenario(db, make_user)
    bom = await _par(
        db, cen, "kia", 0, "quero cancelar o pedido", "O cancelamento é pedido em Minhas Compras."
    )
    await _par(
        db,
        cen,
        "kia",
        1,
        "quero cancelar o pedido",
        "Para cancelar o pedido, deve-se recusar o recebimento do produto no ato da entrega.",
    )
    await _par(
        db, cen, "kia", 2, "quero cancelar o pedido", "Não temos, reposição apenas em novembro."
    )
    achados = await _buscar(db, cen, "quero cancelar o pedido")
    assert [e["id"] for e in achados] == [bom.id]


# ═══════════════ 3. a recusa da entrega e o "ao receber" a quem reclama ═══════════════

DESISTE = "Quero cancelar, me arrependi"


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Como o pedido já foi enviado, você pode recusar a entrega quando ele chegar, e o "
        "valor volta pela Shopee.",
        "Olá! Como o pedido já saiu, basta não aceitar o pacote na hora da entrega.",
        "Olá! Já foi postado; se não quiser mais, é só não receber a encomenda que ela volta.",
        "Olá! Como já foi enviado, na entrega você pode se negar a receber.",
        "Olá! O pedido já foi enviado. Se preferir, recuse o recebimento; o pacote retorna.",
        "Olá! Como o pedido já saiu, você pode devolver ao entregador no ato da entrega.",
        "Olá! Já foi postado; não retire o pacote no ponto de coleta e ele volta.",
        "Olá! O caminho é a devolução: ao receber, peça em Minhas Compras; ou, se preferir, "
        "recuse a entrega.",
        "Olá! Como já foi postado, é só recusar a entrega quando chegar.",
        "Olá! Como o pedido já foi postado, recusar na porta é o jeito mais rápido de cancelar.",
        "Quando o entregador for entregar, você recusa a entrega e o valor volta.",
        "Se preferir, recuse-o na porta.",
        *RECUSAS_DA_EQUIPE,
    ],
)
def test_orientar_a_recusar_a_entrega_e_suave(texto):
    r = checagens.conferir_recusa_da_entrega(texto)
    assert _codigos(r) == [(checagens.RECUSA_DA_ENTREGA, True, False)], texto


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Como o pedido já foi enviado, ao receber, peça a devolução em Minhas Compras.",
        "Se você não receber o pedido até a data prevista, abra uma solicitação em Minhas Compras.",
        "Por favor, não recuse a entrega: ao receber, peça a devolução em Minhas Compras.",
        "Caso não receba o pacote até sexta, nos avise por aqui.",
        "Como você ainda não recebeu o pedido, vamos verificar com a Shopee.",
        "A entrega foi recusada e o pedido está voltando para a loja.",
        "Você pode pegar o pacote no ponto de retirada.",
        "Não é preciso recusar a entrega.",
        "Evite recusar o pacote, pois isso atrasa o reembolso.",
        "Você quer recusar a entrega?",
        "Você não pode recusar a entrega com base nisso.",
        "A transportadora recusa o pacote sem o número da casa.",
    ],
)
def test_o_que_nao_orienta_a_recusa_passa(texto):
    assert checagens.conferir_recusa_da_entrega(texto) == [], texto


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        (
            ["Já era pra estar aqui e nada até agora. Quero meu dinheiro de volta"],
            "Olá! O pedido já foi postado; para o reembolso, ao receber, peça em Minhas Compras.",
        ),
        (
            ["Comprei faz 20 dias e até hoje nada. Quero reembolso"],
            "Olá! O pedido já foi postado; quando ele chegar, o reembolso é pedido em Minhas "
            "Compras > o pedido.",
        ),
        (
            ["O rastreio não anda há uma semana, quero o reembolso"],
            "Olá! O pedido foi postado; quando ele chegar até você, o reembolso é pedido em "
            "Minhas Compras.",
        ),
        (
            ["o entregador nunca apareceu, quero reembolso"],
            "Olá! O pedido foi postado; ao receber, o reembolso é pedido em Minhas Compras.",
        ),
        (
            ["Já passou muito tempo, cadê?", "quero devolver o dinheiro"],
            "Olá! O pedido já saiu; quando receber, peça o reembolso em Minhas Compras.",
        ),
        (
            ["Meu pedido não chegou", "Solicito reembolso"],
            "Olá! O pedido já foi postado; o reembolso é pedido ao receber, em Minhas Compras.",
        ),
    ],
)
def test_o_ao_receber_a_quem_reclama_e_pede_o_dinheiro_e_suave(cliente, texto):
    r = checagens.conferir_ao_receber_a_quem_reclama(texto, textos_cliente=cliente)
    assert _codigos(r) == [(checagens.AO_RECEBER_A_QUEM_RECLAMA, True, False)], texto


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        # Quem só desiste: a orientação de sempre (o modelo confere).
        (
            [DESISTE],
            "Olá! Como o pedido já foi enviado, ao receber, peça a devolução em Minhas Compras.",
        ),
        (
            ["Nada do meu pacote até agora, quero cancelar"],
            "Olá! Como já foi enviado, assim que receber, peça a devolução em Minhas Compras.",
        ),
        # A chegada como condição do próprio cliente.
        (
            ["Assim que chegar, posso devolver e ter o reembolso?"],
            "Olá! Sim: ao receber, peça a devolução ou o reembolso em Minhas Compras.",
        ),
        # O caminho de quem não recebeu já está na frase.
        (
            ["Meu pedido não chegou, quero reembolso"],
            "Olá! Se ele não chegar ou ao recebê-lo, o "
            "pedido de devolução ou reembolso é feito em Minhas Compras.",
        ),
        (
            ["Meu pedido não chegou, quero reembolso"],
            "Olá! Se o pedido não chegar até a data "
            "prevista, peça o reembolso em Minhas Compras > o pedido.",
        ),
    ],
)
def test_o_ao_receber_de_quem_desiste_ou_com_o_caminho_certo_passa(cliente, texto):
    assert checagens.conferir_ao_receber_a_quem_reclama(texto, textos_cliente=cliente) == []


async def test_no_caminho_auto_a_recusa_nao_sai_sozinha():
    texto = "Olá! Como já foi postado, é só recusar a entrega quando chegar."
    p = _proposta(texto, "cancelamento", ["regra:cancelamento"])
    final, chk, ctx, base, conversa = await _caminho(p, ["Não chegou ainda, quero cancelar"])
    assert checagens.RECUSA_DA_ENTREGA in [x.codigo for x in chk.pendencias]
    assert final.precisa_humano and checagens.MOTIVO_RECUSA_DA_ENTREGA in final.sem_auto
    pistas = {"pistas_turno": ["cancelamento"], "pistas_orientacao": ["cancelamento"]}
    assert ia.trava_do_agente(_rascunho(final, ctx, **pistas), KIA) == "sem_auto"


def _fichas_do_caso(caso: dict) -> list[dict]:
    saida = []
    for ev in caso.get("evidencias") or []:
        if isinstance(ev, dict) and isinstance(ev.get("variacoes"), dict):
            saida.append(
                {
                    "item_id": ev.get("item_id"),
                    "titulo": ev.get("titulo"),
                    "variacoes": ev["variacoes"].get("variacoes"),
                    "variacoes_completas": ev["variacoes"].get("completas") is True,
                }
            )
    return saida


def test_as_regras_novas_nao_seguram_nenhum_bom_da_calibracao():
    """As 3 regras suaves novas em TODA resposta boa da calibração: nenhuma pendência. E
    nos ruins que elas pegam, só ruins (o proibida-01, o 68f6992d, o 440a83e7…)."""
    pegos = set()
    for c in calibrar.ler_casos(calibrar.CASOS_PADRAO):
        perguntas = c.get("perguntas") or []
        r = [
            *checagens.conferir_sem_previsao(c["texto"], _fichas_do_caso(c), perguntas),
            *checagens.conferir_recusa_da_entrega(c["texto"]),
            *checagens.conferir_ao_receber_a_quem_reclama(c["texto"], textos_cliente=perguntas),
        ]
        if c["esperado"]["ok_para_envio_automatico"]:
            assert r == [], c["id"]
        elif r:
            pegos.add(c["id"])
    assert {"proibida-01", "boa-aj12-68f6992d", "atraso-440a83e7"} <= pegos
    # O "sem previsão" sem a esgotada confiável (os da revisão 2; os 14..16, a promessa
    # depois do "mas", são da `conferir_variacoes`).
    assert {f"reposicao-{n:02d}" for n in range(9, 14)} <= pegos


# ═══════════════ 4. robustez: o cartão da plataforma nunca derruba ═══════════════

RR = cartoes_plataforma.CARTAO_DEVOLUCAO
LIXO = {
    "amount_sobrescrito": {"message_type": RR, "content": {"rr_detail": {"amount": "²"}}},
    "return_id_circulado": {"message_type": RR, "content": {"rr_detail": {"return_id": "①"}}},
    "status_sobrescrito": {"message_type": RR, "content": {"rr_detail": {"status": "³"}}},
    "amount_4301_digitos": {"message_type": RR, "content": {"rr_detail": {"amount": "1" * 4301}}},
    "amount_enorme": {"message_type": RR, "content": {"rr_detail": {"amount": 10**314}}},
    "rr_status_numero": {
        "message_type": RR,
        "content": {"rr_detail": {}, "tracking_info": {"rr_status": 10**5000}},
    },
    "variation_modelos_int": {
        "message_type": "variation_card",
        "content": {"item_card_v2": {"item_model_v2": 7}},
    },
    "variation_modelos_bool": {
        "message_type": "variation_card",
        "content": {"item_card_v2": {"item_model_v2": True}},
    },
    "variation_model_id_enorme": {
        "message_type": "variation_card",
        "content": {
            "model_id": 10**5000,
            "item_card_v2": {"item_model_v2": [{"model_id": 10**5000, "name": {"text": "Azul"}}]},
        },
    },
    "message_type_numero": {"message_type": 10**5000, "content": {}},
}


def _mensagem(payload: object, *, texto: str = "[Mensagem]") -> AtendimentoMensagem:
    return AtendimentoMensagem(
        id=uuid4(),
        conversa_id=uuid4(),
        autor="cliente",
        origem="plataforma",
        tipo="outro",
        texto=texto,
        anexos=[],
        payload=payload,
        status="recebida",
        enviada_em=datetime.now(UTC),
    )


@pytest.mark.parametrize("nome", list(LIXO))
def test_o_cartao_com_lixo_nao_derruba_a_tela_nem_o_agente(nome):
    m = _mensagem(LIXO[nome])
    conversa = AtendimentoConversa(
        id=m.conversa_id,
        plataforma="shopee",
        canal="chat",
        comprador_nome="F",
        comprador_id="1",
        dados={},
    )
    saida = router_atendimento._mensagem_out(m, conversa=conversa, nomes={})
    assert isinstance(saida.texto, str) and saida.texto
    assert isinstance(cartoes_plataforma.texto_para_exibir(m, para_o_modelo=True), str)
    cartoes_plataforma.campos_do_cartao(m)
    acao_pessoa.devolucao_na_plataforma(conversa, [m], {})


def test_o_valor_e_o_id_so_com_digitos_ascii_curtos():
    assert cartoes_plataforma._inteiro("71998000") == 71998000
    assert cartoes_plataforma._inteiro(" 42 ") == 42
    for lixo in ("²", "①", "1" * 19, 10**18, -(10**18), True, 1.5, None, "12a"):
        assert cartoes_plataforma._inteiro(lixo) is None, lixo
    d = cartoes_plataforma.devolucao_do_cartao(LIXO["amount_enorme"])
    assert d is not None and d.valor is None


def test_a_lista_de_modelos_grande_nao_e_percorrida_letra_a_letra():
    payload = {
        "message_type": "variation_card",
        "content": {"item_card_v2": {"item_model_v2": "x" * 1_000_000}},
    }
    m = _mensagem(payload)
    inicio = time.perf_counter()
    for _ in range(5):
        cartoes_plataforma.texto_para_exibir(m)
    assert time.perf_counter() - inicio < 0.05


def test_a_falha_do_cartao_cai_no_texto_gravado_e_vai_ao_log_uma_vez(monkeypatch):
    avisos: list[dict] = []

    def _quebra(*_a, **_k):
        raise RuntimeError("cartão ilegível")

    monkeypatch.setattr(cartoes_plataforma, "texto_do_cartao", _quebra)
    monkeypatch.setattr(cartoes_plataforma, "_FALHAS_LOGADAS", set())
    monkeypatch.setattr(
        cartoes_plataforma.logger, "warning", lambda evento, **kw: avisos.append({evento: kw})
    )
    m = _mensagem({"message_type": "logistics_card", "content": {}})
    assert cartoes_plataforma.texto_para_exibir(m) == "[Mensagem]"
    assert cartoes_plataforma.texto_para_exibir(m) == "[Mensagem]"
    assert len(avisos) == 1
    (aviso,) = avisos
    assert aviso["cartao_plataforma_ilegivel"]["onde"] == "texto"
    assert aviso["cartao_plataforma_ilegivel"]["erro"] == "RuntimeError"


def test_a_devolucao_que_nao_se_le_conta_como_aberta(monkeypatch):
    def _quebra(_payload):
        raise ValueError("ilegível")

    monkeypatch.setattr(cartoes_plataforma, "devolucao_do_cartao", _quebra)
    rr = _mensagem({"message_type": RR, "content": {"rr_detail": {}}})
    outro = _mensagem({"message_type": "logistics_card", "content": {}})
    assert cartoes_plataforma.devolucoes_abertas_nos_cartoes([rr]) == [rr.enviada_em]
    assert cartoes_plataforma.devolucoes_abertas_nos_cartoes([outro]) == []
    assert cartoes_plataforma.campos_do_cartao(rr) == []


# ═══════════════ 4b. a régua do "demorando" é linear ═══════════════


def _demora_de_antes(plano: str, m) -> bool:
    antes = " ".join(plano[: m.start()].split()[-3:])
    depois = " ".join(plano[m.end() :].split()[:3])
    if acao_pessoa._SUJEITO_DA_ENTREGA.search(antes):
        return True
    return not acao_pessoa._OBJETO_DA_DEMORA.search(f"{antes} {depois}")


def test_a_demora_da_entrega_le_o_mesmo_que_antes():
    vocab = [
        "meu",
        "pedido",
        "esta",
        "demorando",
        "atrasado",
        "atrasou",
        "reembolso",
        "devolucao",
        "a",
        "o",
        "entrega",
        "pacote",
        "ligar",
        "  ",
        "\n",
        "resposta",
        "para",
        "chegar",
    ]
    rnd = random.Random(23)  # noqa: S311 — só sorteia textos de teste
    for _ in range(3000):
        plano = " ".join(rnd.choice(vocab) for _ in range(rnd.randint(1, 14)))
        for m in acao_pessoa._DEMORA.finditer(plano):
            assert acao_pessoa._demora_da_entrega(plano, m) is _demora_de_antes(plano, m), plano


def test_a_reclamacao_de_atraso_e_linear_no_texto_longo():
    def tempo(n: int) -> float:
        plano = ("reembolso atrasou " * (n // 18 + 1))[:n]
        melhor = 1e9
        for _ in range(3):
            inicio = time.perf_counter()
            acao_pessoa.reclama_que_nao_chegou(plano)
            melhor = min(melhor, time.perf_counter() - inicio)
        return melhor

    curto, longo = tempo(10_000), tempo(40_000)
    # Linear: ~4x; o quadrático de antes dava ~15x (11,6 ms → 171 ms).
    assert longo < max(curto * 9, 0.01), (curto, longo)


def test_o_texto_dos_achados_nao_tem_dado_pessoal():
    """Os textos daqui são sintéticos ou as frases curtas da equipe (sem nome, número)."""
    assert not any(pares.tem_dado_pessoal(t) for t in RECUSAS_DA_EQUIPE)
