# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos reais
"""Rodada "fechar o eval" (23/10/2026): o eval REAL do Sonnet 5.5 (braço S1, modo auto, 27
turnos reais das 3 Shopee do agente) deu 3/27 "enviaria" — a simulação prometia 53%. As
causas, nesta ordem de impacto, e cada correção com os casos bons e os ruins:

1. O laço (4/27 sem texto): o `consultar_manual` 3–4 vezes com quase os mesmos argumentos
   e o 5º pedido ainda pedindo ferramenta. Agora: 8 pedidos (`atendimento_agente_max_turnos`),
   o último sem ferramentas (`tool_choice` none — o laço sempre fecha com a Proposta), a
   consulta repetida volta curta ("já consultado") e o `consultar_manual` diz se o manual
   fala da pergunta (`sobre_a_pergunta`).
2. O eval cortava no 0,85 do legado (o `ResultadoAgente` não tem `prompt_versao`).
3. D3: a frase da política da entrega sem o par citado, com o par da MESMA loja no pacote
   → o par entra nas evidências; o turno só com a imagem depois de "ontem era o dia" traz a
   resposta-padrão ao pacote; o acolhimento ("Sentimos o transtorno de não ver atualização
   do seu pedido") não afirma o andamento; o MEDO de extravio não abre chamado.
4. D1: o turno só com o cartão "Cliente abriu pedido de devolução — motivo: Mudei de ideia"
   é informativo; a `reclamacao_aberta` que o modelo ainda pede vira a ação de depois do
   envio.
5. L3: a restrição em uma frase (`resumo`: "RJ: depende do valor do pedido; todos os outros
   estados recebem").
6. O agradecimento: "Eu quem agradeço…" não é pergunta pendente (só o agente).
7. Falsos alarmes: o "Sim" que responde à última fala não confirma a cor de antes; "Ele
   chegou ontem" do cliente (sem o pedido na conversa) é o lastro de "já foi entregue".

Os casos têm a forma dos turnos reais (sem dado de cliente). Nenhuma chamada a LLM, à loja
ou à produção.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.services.atendimento import constantes, ia, restricao_envio, variacoes
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    ferramentas,
    pacote,
    prompt,
    replay,
    saida,
)
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from tests.test_atendimento_agente_decisoes_dono import (
    AGORA,
    BLOCO_DEV,
    CTX_POSTADO,
    PAR_OUTRA_LOJA,
    PAR_PRAZO,
    VAI,
    VALORES,
    _bloco,
    _cli,
    _conv,
    _ctx_dev,
    _loja,
    _m,
    _menu,
    _p,
    _reclamacao,
)
from tests.test_atendimento_agente_decisoes_dono import _auto as _auto_d123
from tests.test_atendimento_agente_laco import (  # noqa: F401 — fixtures
    _Api,
    _base,
    _conversa,
    _ferramenta,
    _final,
    _gatilho,
    _resposta,
    _rodar,
    _texto,
    ajustes,
    pecas,
    redis_falso,
)
from tests.test_atendimento_agente_replay import _AUTO, _CONV, _resultado

# ═══════════════ 1. o laço ═══════════════


def test_1_a_configuracao_padrao_e_8_pedidos():
    padrao = {nome: campo.default for nome, campo in Settings.model_fields.items()}
    assert padrao["atendimento_agente_max_turnos"] == 8


async def test_1_o_ultimo_pedido_vai_sem_ferramentas_e_fecha_com_a_proposta(pecas):
    respostas = [
        _resposta(
            [_ferramenta("consultar_manual", assunto="conteudo_da_caixa", pergunta="película")],
            "tool_use",
        ),
        _resposta([_ferramenta("ver_anuncio", item_id="58265493964")], "tool_use"),
        _final(),
    ]
    r, api, _ = await _rodar(pecas, respostas, extra={"max_turnos": 3})
    assert r.parada == "fim" and r.texto
    assert len(api.pedidos) == 3
    # Só o último permitido vai sem ferramentas (o 1º e o 2º, como sempre).
    assert "tool_choice" not in api.pedidos[0] and "tool_choice" not in api.pedidos[1]
    assert api.pedidos[2]["tool_choice"] == {"type": "none"}
    # As ferramentas do 2º turno rodaram (o modelo responde com o que leu).
    assert [n for n, _a in pecas.chamadas_ferramenta] == ["consultar_manual", "ver_anuncio"]
    assert r.fatos_agente["fechou_sem_ferramentas"] is True


async def test_1_sem_chegar_ao_limite_nada_muda(pecas):
    r, api, _ = await _rodar(
        pecas, [_resposta([_ferramenta()], "tool_use"), _final()], extra={"max_turnos": 8}
    )
    assert r.parada == "fim" and all("tool_choice" not in p for p in api.pedidos)
    assert "fechou_sem_ferramentas" not in r.fatos_agente


async def test_1_o_modelo_que_ainda_pede_ferramenta_vai_para_pessoa_como_antes(pecas):
    # O transporte falso não obedece o tool_choice: a saída sem texto continua pessoa.
    respostas = [_resposta([_ferramenta(item_id=str(i))], "tool_use") for i in range(3)]
    r, api, _ = await _rodar(pecas, respostas, extra={"max_turnos": 3})
    assert r.parada == "max_iteracoes" and r.precisa_humano and r.texto is None
    assert api.pedidos[2]["tool_choice"] == {"type": "none"}


async def test_1_perto_do_prazo_o_proximo_pedido_ja_fecha(pecas, monkeypatch):
    from app.services.atendimento import agente
    from tests.test_atendimento_agente_laco import _Relogio

    relogio = _Relogio(passo=35.0)  # prazo de 45 s: depois do 1º pedido sobram 10 s
    monkeypatch.setattr(agente, "_relogio", relogio)
    api = _Api([_resposta([_ferramenta()], "tool_use"), _final()], pecas.eventos)
    api.ao_pedir = relogio.andar
    r, _, _ = await _rodar(pecas, [], api=api, extra={"max_turnos": 8})
    assert r.parada == "fim"
    assert "tool_choice" not in api.pedidos[0] and api.pedidos[1]["tool_choice"] == {"type": "none"}


def test_1_fechar_na_proxima():
    from app.services.atendimento import agente

    assert agente.fechar_na_proxima(7, 8, agente._relogio() + 60)
    assert not agente.fechar_na_proxima(5, 8, agente._relogio() + 60)
    assert agente.fechar_na_proxima(1, 8, agente._relogio() + 5)
    assert agente._sem_ferramentas({"model": "x"}) == {
        "model": "x",
        "tool_choice": {"type": "none"},
    }


def _ctx_de_teste() -> agente_ctx.ContextoAgente:
    conversa = _conversa()
    base = _base()
    return agente_ctx.ContextoAgente.da_conversa(
        conversa, _gatilho(), modo=base.modo, nomes=base.nomes, extra={"sem_ida_a_loja": True}
    )


# Os casos reais do eval: a mesma busca com a pergunta quase igual (3f075ee443c4,
# dedf499cea8f, 93d6cc5b943d) — e as que NÃO são a mesma.
REPETIDAS = [
    ("consultar_manual", {"assunto": "conteudo_da_caixa", "pergunta": "vem com película?"},
     {"assunto": "conteudo_da_caixa", "pergunta": "película incluída"}),
    ("consultar_manual", {"assunto": "conteudo_da_caixa", "pergunta": "película"},
     {"assunto": "conteudo_da_caixa", "pergunta": "película"}),
    ("consultar_manual", {"assunto": "espec_celular", "pergunta": "funciona no Brasil igual Samsung, operadoras, bandas"},
     {"assunto": "espec_celular", "pergunta": "x"}),
    ("consultar_manual", {"assunto": "originalidade_condicao", "pergunta": "original lacrado garantia Apple"},
     {"assunto": "originalidade_condicao", "pergunta": "novo lacrado garantia Apple 1 ano"}),
    ("consultar_manual", {"assunto": "garantia", "pergunta": None}, {"assunto": "garantia", "pergunta": "prazo"}),
    ("buscar_anuncios_da_loja", {"termo": "capa A18"}, {"termo": "Capa  a18"}),
]  # fmt: skip
DIFERENTES = [
    ("consultar_manual", {"assunto": "originalidade_condicao", "pergunta": "original lacrado"},
     {"assunto": "garantia", "pergunta": "original lacrado"}),
    ("consultar_manual", {"assunto": "espec_celular", "pergunta": "tem NFC"},
     {"assunto": "espec_celular", "pergunta": "bateria dura quanto"}),
    # O catálogo só repete o MESMO termo ("capa A18" não é a busca do "A18").
    ("buscar_anuncios_da_loja", {"termo": "A18"}, {"termo": "capa A18"}),
    ("buscar_anuncios_da_loja", {"termo": "capa A18 Pro Max"}, {"termo": "película A18"}),
    # As leituras de um dado (ver_anuncio, ver_pedido…) nunca contam como repetidas.
    ("ver_anuncio", {"item_id": "1"}, {"item_id": "1"}),
    ("ver_pedido", {"numero": "123"}, {"numero": "123"}),
]  # fmt: skip


@pytest.mark.parametrize(("nome", "antes", "depois"), REPETIDAS)
def test_1_a_consulta_repetida(nome, antes, depois):
    ctx = _ctx_de_teste()
    assert not ctx.consulta_repetida(nome, antes)
    ctx.registrar_consulta(nome, antes)
    assert ctx.consulta_repetida(nome, depois)


@pytest.mark.parametrize(("nome", "antes", "depois"), DIFERENTES)
def test_1_o_que_nao_e_a_mesma_consulta(nome, antes, depois):
    ctx = _ctx_de_teste()
    ctx.registrar_consulta(nome, antes)
    assert not ctx.consulta_repetida(nome, depois)


async def test_1_a_repetida_volta_curta_sem_rodar_de_novo():
    ctx = _ctx_de_teste()
    chamadas = []

    async def _manual():
        chamadas.append(1)
        return ferramentas.Resultado(
            dados={"fatos": [{"id": "fato:9", "texto": "x"}]}, ids=("fato:9",)
        )

    args = {"assunto": "conteudo_da_caixa", "pergunta": "película"}
    primeira = await ferramentas.executar(ctx, "consultar_manual", args, _manual)
    assert "fato:9" in primeira and len(chamadas) == 1
    segunda = await ferramentas.executar(
        ctx,
        "consultar_manual",
        {"assunto": "conteudo_da_caixa", "pergunta": "película incluída"},
        _manual,
    )
    assert len(chamadas) == 1 and "ja_consultado" in segunda
    assert ferramentas.JA_CONSULTADO.split(":")[0] in segunda
    assert ctx.rastro[-1].repetida is True and ctx.rastro[-1].para_fatos()["repetida"] is True
    # Outro assunto roda.
    await ferramentas.executar(
        ctx, "consultar_manual", {"assunto": "garantia", "pergunta": "película"}, _manual
    )
    assert len(chamadas) == 2


async def test_1_a_consulta_que_falhou_nao_conta_como_feita():
    ctx = _ctx_de_teste()

    async def _falha():
        raise RuntimeError("banco fora")

    args = {"assunto": "garantia", "pergunta": "prazo"}
    for _ in range(2):
        with pytest.raises(ferramentas.ToolError):
            await ferramentas.executar(ctx, "consultar_manual", args, _falha)
    assert not ctx.consulta_repetida("consultar_manual", args)


def test_1_a_2a_passada_esquece_as_consultas():
    ctx = _ctx_de_teste()
    ctx.registrar_consulta("consultar_manual", {"assunto": "garantia", "pergunta": "prazo"})
    ctx.fechou_sem_ferramentas = True
    ctx.voltar_ferramentas(ctx.estado_das_ferramentas())
    assert ctx.consultas == [] and ctx.fechou_sem_ferramentas is False


FATO_25 = {
    "id": "fato:25",
    "texto": "Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano a partir da ativação.",
}
FATO_1 = {"id": "fato:1", "texto": "O celular é desbloqueado: não vem preso a nenhuma operadora."}
FATO_28 = {
    "id": "fato:28",
    "texto": "No momento não trabalhamos com capinha nem película avulsas nesta loja.",
}


def test_1_a_nota_do_manual_diz_o_que_fala_da_pergunta():
    nota = ferramentas.nota_da_pergunta(
        "original lacrado garantia Apple", {"fatos": [FATO_1, FATO_25]}
    )
    assert "fato:25" in nota and "fato:1" not in nota and "não consulte de novo" in nota
    # O fato que só tem a palavra (película avulsa) aparece, com o aviso de conferir.
    nota = ferramentas.nota_da_pergunta(
        "vem com película?", {"fatos": [FATO_28], "respostas_prontas": []}
    )
    assert "fato:28" in nota and "Confira se respondem exatamente" in nota


def test_1_a_nota_do_manual_diz_quando_ele_nao_tem_o_dado():
    nota = ferramentas.nota_da_pergunta(
        "funciona no Brasil", {"fatos": [FATO_1], "respostas_prontas": []}
    )
    assert "nenhum fato aprovado" in nota and "brasil" in nota and "funciona" in nota
    assert ferramentas.nota_da_pergunta(None, {"fatos": [FATO_1]}) is None
    assert ferramentas.nota_da_pergunta("x", {"fatos": [FATO_1]}) is None


async def test_1_a_nota_nunca_e_lastro():
    ctx = _ctx_de_teste()

    async def _manual():
        dados = {"fatos": [FATO_1], "respostas_prontas": []}
        dados["sobre_a_pergunta"] = ferramentas.nota_da_pergunta(
            "funciona no Brasil igual Samsung", dados
        )
        return ferramentas.Resultado(dados=dados, ids=("fato:1",))

    texto = await ferramentas.executar(
        ctx,
        "consultar_manual",
        {"assunto": "espec_celular", "pergunta": "funciona no Brasil igual Samsung"},
        _manual,
    )
    assert "samsung" in texto.lower()
    assert not any("samsung" in t.lower() for t in ctx.registro.textos)
    assert any("desbloqueado" in t for t in ctx.registro.textos)


# ═══════════════ 2. o corte do eval ═══════════════


@pytest.mark.parametrize(
    ("confianca", "motivo"),
    [
        # O portão do dono (27/10/2026): a confiança só segura abaixo de 0,50 (o piso
        # duro); de 0,50 a 0,80 é aviso ao verificador.
        (0.80, replay.ENVIARIA),
        (0.85, replay.ENVIARIA),
        (0.79, replay.ENVIARIA),
        (0.5, replay.ENVIARIA),
        (0.49, replay.CONFIANCA_BAIXA),
    ],
)
def test_2_o_eval_corta_no_0_80_do_agente(confianca, motivo):
    d = replay.decisao_simulada(_resultado(confianca=confianca), conversa=_CONV, canal=_AUTO)
    assert d["motivo"] == motivo
    assert ia.confianca_minima(True) == 0.50 and ia.confianca_minima(False) == 0.85


# ═══════════════ 3. D3 ═══════════════

POLITICA = (
    'Seu pedido {numero_pedido} foi postado em {data_envio} e o último status é "Pedido postado". Depois do '
    "envio a entrega fica com a Shopee e não temos acesso ao transporte, então vale o prazo e o andamento em "
    "Minhas Compras > o pedido > Informações do envio."
)


def _ctx_com_pares(*pares) -> agente_ctx.ContextoAgente:
    ctx = _ctx_de_teste()
    ctx.registrar_pares(list(pares))
    return ctx


def test_3_o_par_da_politica_no_pacote_entra_nas_evidencias():
    p = _p(POLITICA, "rastreio", ["pedido", "regra:rastreio"])
    nova = checagens.com_par_da_politica(p, _ctx_com_pares(PAR_PRAZO))
    assert nova.evidencias == ["pedido", "regra:rastreio", "par:8440"]
    assert (
        checagens.conferir_politica_da_entrega(
            nova.texto, evidencias_=nova.evidencias, ctx=_ctx_com_pares(PAR_PRAZO)
        )
        == []
    )


@pytest.mark.parametrize(
    ("texto", "evidencias", "pares_"),
    [
        (POLITICA, ["pedido"], []),  # sem o par no pacote: segura como antes
        (POLITICA, ["pedido"], [PAR_OUTRA_LOJA]),  # o par de outra loja nunca
        ("Seu pedido foi postado em {data_envio}; acompanhe em Minhas Compras > o pedido.", ["pedido"], [PAR_PRAZO]),
        (POLITICA, ["pedido", "par:8440"], [PAR_PRAZO]),  # já citado: nada muda
    ],
)  # fmt: skip
def test_3_o_que_nao_ganha_o_par(texto, evidencias, pares_):
    p = _p(texto, "rastreio", evidencias)
    assert checagens.com_par_da_politica(p, _ctx_com_pares(*pares_)) == p


def test_3_o_turno_so_com_a_imagem_herda_a_fala_da_entrega():
    imagem = _m("cliente", None, tipo="imagem")
    entrega = [
        _cli("Ontem era o dia de receber e nada")[0],
        _loja(
            "Pode seguir o prazo informado na hora da compra pela plataforma, não temos acesso ao transporte!"
        ),
        imagem,
    ]
    base = SimpleNamespace(recentes=entrega)
    assert pacote._turno_da_entrega(base, _bloco(), imagem)
    # Depois de uma pergunta de produto, a imagem não é turno da entrega.
    produto = [_cli("Esse celular tem NFC?")[0], _loja("tem sim!"), imagem]
    assert not pacote._turno_da_entrega(SimpleNamespace(recentes=produto), _bloco(), imagem)
    # O pedido entregue nunca.
    assert not pacote._turno_da_entrega(base, _bloco(entregue=True), imagem)


@pytest.mark.parametrize(
    "oracao",
    [
        "sentimos o transtorno de nao ver atualizacao do seu pedido",
        "entendemos a sua preocupacao com a demora do seu pedido",
        "lamentamos a demora do seu pedido",
    ],
)
def test_3_o_acolhimento_nao_afirma_o_andamento(oracao):
    assert checagens.so_acolhimento(oracao)


@pytest.mark.parametrize(
    "oracao",
    [
        "sentimos a demora do seu pedido que ja saiu para entrega",
        "entendemos a preocupacao, o seu pedido chega amanha",
        "lamentamos a demora, o pedido esta parado no centro",
        "sentimos o transtorno do pedido extraviado",
        "o seu pedido saiu para entrega",
    ],
)
def test_3_o_acolhimento_com_estado_continua_conferido(oracao):
    assert not checagens.so_acolhimento(oracao)


def test_3_a_frase_real_do_29a36129ce61_tem_lastro():
    bloco = _bloco(status="Pedido postado")
    ctx = _ctx_de_teste()
    ctx.permitir(ids=["pedido"], textos=[json.dumps(bloco, ensure_ascii=False)])
    texto = (
        'Sentimos o transtorno de não ver atualização do seu pedido. O último status é "Pedido postado", com '
        "envio em {data_envio}."
    )
    assert (
        checagens.conferir_andamento_sem_lastro(
            texto, bloco, ctx, assunto="rastreio", valores=VALORES
        )
        == []
    )
    # O andamento inventado depois do acolhimento continua segurando.
    ruim = (
        "Sentimos o transtorno de não ver atualização do seu pedido. Ele já saiu para entrega hoje."
    )
    assert checagens.conferir_andamento_sem_lastro(
        ruim, bloco, ctx, assunto="rastreio", valores=VALORES
    )


@pytest.mark.parametrize(
    "plano",
    [
        "fiquei com medo de extravio tambem, mas enfim, saiu hoje de piracicaba",
        "tenho receio de extraviar",
        "estou preocupada com o risco de ser extraviado",
    ],
)
def test_3_o_medo_de_extravio_nao_e_o_relato(plano):
    assert not acao_pessoa.extravio_relatado(plano)


@pytest.mark.parametrize(
    "plano",
    [
        "meu pedido foi extraviado",
        "acho que extraviaram meu pacote",
        "consta extraviado no rastreio",
        "roubaram meu pacote",
        "fiquei com medo e agora consta extraviado",
    ],
)
def test_3_o_relato_de_extravio_continua_chamado(plano):
    assert acao_pessoa.extravio_relatado(plano)


def test_3_a421f4642d0c_sem_o_chamado_pelo_medo():
    falas = [
        "Depois de muita encheção de saco minha com eles, rs… pq não é barato, eu já queria que me devolvesse o "
        "dinheiro pra comprar em outro lugar, fiquei com medo de extravio também",
        "Mas enfim, saiu hoje de Piracicaba",
    ]
    gatilho_msgs = [_menu(), *_cli(*falas)]
    g = gatilho_msgs[-1]
    codigos = [
        x.codigo
        for x in acao_pessoa.gatilhos(
            _conv(),
            gatilho_msgs,
            CTX_POSTADO,
            gatilho=g,
            postado=acao_pessoa.POSTADO_SIM,
            chaves_ativas=frozenset(),
            agora=AGORA,
        )
    ]
    assert constantes.ACAO_ABRIR_CHAMADO not in codigos
    relato = [_menu(), *_cli("Meu pedido foi extraviado, o rastreio diz extravio")]
    codigos = [
        x.codigo
        for x in acao_pessoa.gatilhos(
            _conv(),
            relato,
            CTX_POSTADO,
            gatilho=relato[-1],
            postado=acao_pessoa.POSTADO_SIM,
            chaves_ativas=frozenset(),
            agora=AGORA,
        )
    ]
    assert constantes.ACAO_ABRIR_CHAMADO in codigos


# ═══════════════ 4. D1 ═══════════════


def _cartao(
    motivo: str, solucao: str = "Devolução e Reembolso", status: int = 1
) -> SimpleNamespace:
    m = _m("cliente", "[Mensagem]", tipo="outro")
    m.payload = {
        "message_type": "return_refund_card",
        "content": {"rr_detail": {"status": status, "return_id": 1, "translated_detail": [
            {"reason": motivo, "language": "pt-BR", "solution": solucao}]}},
    }  # fmt: skip
    return m


def _ctx_d1() -> dict:
    return _ctx_dev(
        reclamacoes=[_reclamacao(status="Em andamento", acao=None)],
        devolucoes=[{"status": "PROCESSING"}],
    )


def test_4_o_cartao_sozinho_entra_no_d1():
    if acao_pessoa.cartoes_plataforma.tipo_do_cartao(_cartao("Mudei de ideia").payload) is None:
        pytest.skip("o cartão de teste não é do tipo de devolução")
    recentes = [
        _loja("Olá! Para pedir a devolução, acesse Minhas Compras > o pedido."),
        _cartao("Mudei de ideia"),
    ]
    assert acao_pessoa.responde_e_marca_do_turno(
        _conv(), _ctx_d1(), recentes, recentes[-1], acoes=["reclamacao_aberta"], agora=AGORA
    )


@pytest.mark.parametrize(
    "motivo",
    [
        "Produto com defeito",
        "Produto danificado",
        "Recebi o item errado",
        "Faltando peças",
        "Não funciona",
    ],
)
def test_4_o_cartao_com_problema_continua_com_pessoa(motivo):
    recentes = [_loja("Olá!"), _cartao(motivo)]
    assert not acao_pessoa.responde_e_marca_do_turno(
        _conv(), _ctx_d1(), recentes, recentes[-1], acoes=["reclamacao_aberta"], agora=AGORA
    )


def test_4_o_texto_do_cartao_no_turno():
    assert acao_pessoa._texto_do_turno_d1(_cartao("Mudei de ideia")).startswith(
        "pedido de devolução aberto"
    )
    assert "defeito" in acao_pessoa._texto_do_turno_d1(_cartao("Produto com defeito"))
    assert (
        acao_pessoa._texto_do_turno_d1(_cartao("Mudei de ideia", status=2)) == "[Mensagem]"
    )  # encerrada
    assert acao_pessoa._texto_do_turno_d1(_cli("oi")[0]) == "oi"


def _proposta_pessoa(
    texto: str = "Recebemos a sua solicitação de devolução do pedido {numero_pedido}. Você pode acompanhar o andamento em Minhas Compras > o pedido.",
    acao_pessoa_: str | None = "reclamacao_aberta",
    acao: str = "pessoa",
) -> Proposta:
    return Proposta(
        acao=acao,
        texto=texto,
        assunto="troca_devolucao",
        confianca=0.85,
        acao_pessoa=acao_pessoa_,
        motivo_pessoa="O cliente abriu devolução",
        evidencias=["pedido"],
    )


def test_4_a_reclamacao_do_modelo_vira_depois_do_envio():
    ctx = _ctx_de_teste()
    ctx.responde_e_marca = True
    nova, depois = checagens.responde_e_marca_da_proposta(_proposta_pessoa(), ctx)
    assert nova.acao == "responder" and nova.acao_pessoa is None and depois == ["reclamacao_aberta"]
    assert not saida.precisa_pessoa(nova)
    ctx.acoes_codigo, ctx.acoes_modelo = (
        ["reclamacao_aberta"],
        [*saida.acoes_do_modelo(nova), *depois],
    )
    assert ctx.acoes_pessoa == [] and ctx.acoes_pos_envio == ["reclamacao_aberta"]


@pytest.mark.parametrize(
    ("marca", "proposta"),
    [
        (False, _proposta_pessoa()),  # sem a marca: pessoa como antes
        (True, _proposta_pessoa(acao_pessoa_="dado_indisponivel")),  # outra ação: pessoa
        (True, _proposta_pessoa(acao_pessoa_="aprovar_fora_da_plataforma")),
        (True, _proposta_pessoa(texto="")),  # sem texto
        (True, _proposta_pessoa(acao="perguntar", acao_pessoa_=None)),
    ],
)
def test_4_o_que_nao_vira_resposta(marca, proposta):
    ctx = _ctx_de_teste()
    ctx.responde_e_marca = marca
    assert checagens.responde_e_marca_da_proposta(proposta, ctx) == (proposta, [])


async def _auto(p: Proposta, msgs: list, **kw: Any) -> SimpleNamespace:
    """O caminho AUTO do D1–D3 (`tests.test_atendimento_agente_decisoes_dono._auto`)."""
    return await _auto_d123(p, msgs, **kw)


async def test_4_o_0e1493a92afe_vai_ao_verificador_e_marca():
    # O D1 do turno só com o cartão (o "Recebemos a sua solicitação" do modelo, "pessoa" +
    # reclamacao_aberta convertido pelo laço).
    recentes = [
        _menu(),
        *_cli("Gostaria de fazer a devolução"),
        _loja("Olá! Para pedir a devolução, acesse Minhas Compras > o pedido."),
        _cartao("Mudei de ideia"),
    ]
    if acao_pessoa.cartoes_plataforma.tipo_do_cartao(recentes[-1].payload) is None:
        pytest.skip("o cartão de teste não é do tipo de devolução")
    ctx = _ctx_de_teste()
    ctx.responde_e_marca = acao_pessoa.responde_e_marca_do_turno(
        _conv(), _ctx_d1(), recentes, recentes[-1], acoes=["reclamacao_aberta"], agora=AGORA
    )
    assert ctx.responde_e_marca
    nova, depois = checagens.responde_e_marca_da_proposta(_proposta_pessoa(), ctx)
    r = await _auto(
        nova,
        recentes,
        ctx_pedido=_ctx_d1(),
        bloco=BLOCO_DEV,
        postado=acao_pessoa.POSTADO_SIM,
        valores=VALORES,
    )
    assert r.ctx.responde_e_marca and r.decisao == VAI, (r.codigos, r.motivos)


# ═══════════════ 5. L3: a restrição em uma frase ═══════════════


def test_5_o_resumo_da_restricao():
    assert restricao_envio.resumo_da_restricao([], ["RJ"]) == (
        "RJ: depende do valor do pedido (não diga sim nem não para esse estado); todos os outros estados recebem"
    )
    assert (
        restricao_envio.resumo_da_restricao(["RJ"], [])
        == "não envia para RJ; todos os outros estados recebem"
    )
    assert restricao_envio.resumo_da_restricao(["RS"], ["RJ", "SP"]).startswith(
        "não envia para RS; RJ, SP: depende"
    )
    assert restricao_envio.resumo_da_restricao([], []) == restricao_envio.RESUMO_NENHUMA
    # O valor do limite nunca vai.
    assert "1.500" not in restricao_envio.resumo_da_restricao([], ["RJ"])


def test_5_o_bloco_2_manda_seguir_o_resumo():
    shopee = prompt.bloco_da_plataforma("shopee", _categorias())
    assert 'siga o "resumo" dela (fonte "loja" também)' in shopee
    assert '"depende do valor do pedido" nunca é sim nem não' in shopee


# ═══════════════ 6. o agradecimento ═══════════════


@pytest.mark.parametrize(
    "fala",
    [
        "Eu quem agradeço mto obrigado conserteza voltarei sim 🙏🙏🙏",
        "Muito obrigada",
        "valeu, vou indicar",
    ],
)
def test_6_so_agradecimento(fala):
    assert ia.foco_so_de_agradecimento([SimpleNamespace(texto=fala)])


@pytest.mark.parametrize(
    "fala",
    [
        "Obrigado! E a nota fiscal?",
        "obrigado, quem vai me entregar?",
        "Obrigada, tem garantia?",
        "obrigado, quando chega?",
    ],
)
def test_6_o_agradecimento_com_pergunta_continua_pendente(fala):
    assert not ia.foco_so_de_agradecimento([SimpleNamespace(texto=fala)])


async def test_6_o_11a4570bdfb5_e_so_do_agente():
    msgs = [
        _m("sistema", "Obrigado pela confiança! Volte sempre que precisar!", origem="externo"),
        *_cli("Eu quem agradeço mto obrigado conserteza voltarei sim 🙏🙏🙏"),
    ]
    p = _p(
        "Que bom, ficamos muito felizes! Agradecemos a compra e ficamos à disposição.",
        "agradecimento",
        [],
    )
    r = await _auto(p, msgs)
    assert "há pergunta concreta pendente — a resposta genérica não a resolve" not in r.motivos
    # O legado continua igual (a regra é do agente).
    g = msgs[-1]
    legado = await ia.conferir_saida(
        None, _conv(), g, texto=p.texto, categoria="agradecimento", categoria_valida=True, precisa_humano=False,
        motivo_modelo="", motivos=[], ctx={}, fatos={}, valores={}, categorias=_categorias(), regras=[],
        textos_cliente=[g.texto], textos_rajada=[g.texto], foco=[g], fichas=[], garantias_conferidas=None,
        modo=constantes.MODO_AUTO, forcar=True,
    )  # fmt: skip
    assert "há pergunta concreta pendente — a resposta genérica não a resolve" in legado.motivos


def test_6_o_bloco_2_no_agradecimento():
    assert "Só agradecimento: agradeça, sem repetir nada do produto" in prompt.COMO_PENSAR


# ═══════════════ 7. os falsos alarmes ═══════════════

FICHA_A17 = {
    "item_id": "22499563693", "titulo": "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC",
    "de_onde": "item do pedido da conversa", "variacoes_completas": True,
    "variacoes": [
        {"nome": "Laranja, 12 GB / 64", "disponivel": True, "confiavel": True, "fonte": "loja"},
        {"nome": "Prata, 12 GB / 128 GB", "disponivel": True, "confiavel": True, "fonte": "loja"},
        {"nome": "Preto, 12 GB / 128 GB", "disponivel": False, "confiavel": True, "fonte": "loja"},
    ],
}  # fmt: skip


def test_7_o_sim_da_ultima_fala_nao_confirma_a_cor_de_antes():
    texto = "Olá! Sim, o aparelho Uranyx tem 1 ano de garantia a partir da entrega."
    clientes = ["Traz um branco", "O telefone tem garantia"]
    achadas = checagens.conferir_variacoes(
        texto, [FICHA_A17], clientes, com_pedido=True, ultima="O telefone tem garantia"
    )
    # O "Sim" não diz que tem o branco (o falso alarme do 13d675aa6186) — mas o pedido do
    # branco ficou sem resposta: pessoa, com o motivo certo.
    assert [p.motivo for p in achadas] == ["o pedido de 'branco' ficou sem resposta"]
    assert all(p.codigo == checagens.VARIACAO_SEM_DADO for p in achadas)
    # Tratando as duas falas, nada.
    completo = texto + " A cor branca não está entre as opções deste anúncio."
    assert (
        checagens.conferir_variacoes(
            completo, [FICHA_A17], clientes, com_pedido=True, ultima="O telefone tem garantia"
        )
        == []
    )
    # Sem a última fala (quem não passa): como antes.
    antes = checagens.conferir_variacoes(texto, [FICHA_A17], clientes, com_pedido=True)
    assert any("diz que tem 'branco'" in p.motivo for p in antes)


def test_7_a_recusa_que_a_propria_resposta_estende_ao_defeito():
    clientes = [
        "Queria mandar ele Paga a diferença e pega outro",
        "Mesmo se não tivesse com problema vocês não ia poder trocar?",
    ]
    ruim = (
        "Como o pedido já foi entregue, não fazemos troca com pagamento da diferença, nem com ou sem problema no "
        "aparelho. O caminho é a devolução."
    )
    assert [
        p.codigo for p in checagens.conferir_troca_negada_no_defeito(ruim, textos_cliente=clientes)
    ] == [checagens.TROCA_NEGADA_NO_DEFEITO]
    bom = (
        "Como você já recebeu o aparelho, a troca por outro modelo pagando a diferença não é feita; o caminho é a "
        "devolução do pedido em Minhas Compras > o pedido."
    )
    assert checagens.conferir_troca_negada_no_defeito(bom, textos_cliente=clientes) == []
    assert checagens.conferir_troca_negada_no_defeito(
        "Não fazemos troca, mesmo com defeito.", textos_cliente=["quero trocar de cor"]
    )


@pytest.mark.parametrize(
    ("texto", "clientes", "ultima"),
    [
        # A última fala é a da cor: o "Sim" a confirma.
        ("Sim! Pode comprar.", ["O telefone tem garantia", "Tem o branco?"], "Tem o branco?"),
        # O "Sim" sozinho não fala da última pergunta.
        (
            "Sim! Pode comprar.",
            ["Traz um branco", "O telefone tem garantia"],
            "O telefone tem garantia",
        ),
        # A cor na própria última fala.
        (
            "Sim, tem garantia e tem o branco.",
            ["Tem garantia? E o branco?"],
            "Tem garantia? E o branco?",
        ),
    ],
)
def test_7_o_sim_que_confirma_a_cor_continua_conferido(texto, clientes, ultima):
    assert checagens.conferir_variacoes(
        texto, [FICHA_A17], clientes, com_pedido=True, ultima=ultima
    )


@pytest.mark.parametrize(
    "falas",
    [
        ["Não gostei. Do aparelho Ele chegou ontem"],
        ["recebi hoje"],
        ["Já está aqui comigo"],
        ["veio tudo certo"],
    ],
)
def test_7_o_cliente_disse_que_recebeu(falas):
    assert checagens.cliente_disse_que_recebeu(falas)


@pytest.mark.parametrize(
    "falas",
    [
        ["ainda não chegou"],
        ["não recebi"],
        ["chegou?"],
        ["nunca veio"],
        ["meu pedido n chegou"],
        ["quando chega?"],
    ],
)
def test_7_o_que_nao_e_o_relato_de_recebimento(falas):
    assert not checagens.cliente_disse_que_recebeu(falas)


def test_7_entregue_pelo_relato_do_cliente_so_sem_o_pedido():
    texto = "Como o pedido já foi entregue, o caminho é a devolução em Minhas Compras > o pedido."
    assert checagens.conferir_estado_do_pedido(texto, None, None, cliente_recebeu=True) == []
    assert checagens.conferir_estado_do_pedido(texto, None, None, cliente_recebeu=False)
    # Com o pedido não postado, o relato não apaga a contradição.
    nao_saiu = _bloco(
        postado=acao_pessoa.POSTADO_NAO, enviado=False, data_envio=None, ultimo_status=None
    )
    assert checagens.conferir_estado_do_pedido(texto, nao_saiu, None, cliente_recebeu=True)
    # A situação Finalizado/Concluído do bloco prova a entrega.
    assert checagens.conferir_estado_do_pedido(texto, _bloco(situacao="Concluído"), None) == []
    assert checagens.conferir_estado_do_pedido(texto, _bloco(situacao="Em andamento"), None)


# ═══════════════ 8. o verificador v13 ═══════════════


def test_8_o_sistema_v13_diz_o_lastro_que_ja_esta_na_entrada():
    assert v.VERSAO == "v16" and v.MODELO == "claude-haiku-5-5"
    s = " ".join(v.SISTEMA.split())
    for trecho in (
        "a linha que termina em «os demais estados recebem» sustenta «enviamos para <estado que não está na linha>, sim»",
        'com uma lista de variações de "completas": true, dizer as opções dela',
        'o "ultimo_status" do PEDIDO dito com as palavras dele, inteiro ou só a parte principal',
        "uma política só: a linha que diz uma dessas formas sustenta as outras",
        "Dizer que o anúncio não informa o dado perguntado, quando nenhum campo das EVIDÊNCIAS o traz, trata a pergunta",
        "(true; se um campo o traz, é o item 6)",
        "a pergunta do que o anúncio vende",
        "o turno que só conta o que o cliente já fez",
    ):
        assert trecho in s, trecho
    # O que bloqueava continua: o campo que contradiz, a decisão da devolução, o prazo sem campo.
    for trecho in (
        "Continua contando: o prazo com número, dia ou data que nenhum campo traz",
        "dizer que a devolução ou o reembolso foi aprovado, recusado, aceito ou pago",
        "Seja rígido.",
    ):
        assert trecho in s, trecho


def _categorias():
    from tests.test_atendimento_agente_laco import _categorias_do_manual

    return _categorias_do_manual()


def test_8_o_bloco_2_cabe_e_diz_as_correcoes():
    shopee = prompt.bloco_da_plataforma("shopee", _categorias())
    assert len(shopee) / 3.5 <= 5_250  # garantia da equipe (o dono, 27/10/2026)
    assert "Cada consulta uma vez (o resultado não muda)" in shopee
    assert 'acao "responder" (nem "pessoa" nem acao_pessoa' in shopee
    assert "o cartão sozinho: confirme que recebeu a solicitação" in shopee
    assert "nem a data que o cliente citou ('neste fim de semana')" in shopee
    ml = prompt.bloco_da_plataforma("ml", _categorias())
    assert "Cada consulta uma vez" in ml and "responde_e_marca" not in ml


def test_8_tempo_do_pedido():
    assert timedelta(seconds=agente_ctx.MIN_COMUM_REPETIDA) == timedelta(seconds=0.5)
    assert variacoes._sim_da_ultima("sim, tem garantia", None, None, frozenset()) is False


# ═══════════════ 9. a calibração v13 ═══════════════

FECHAR_BOAS = {
    "fechar-d3-par-anexado-boa", "fechar-d3-acolhimento-boa", "fechar-d1-cartao-boa", "fechar-agradecimento-boa",
    "fechar-sim-garantia-e-branco-boa", "fechar-recebido-troca-boa", "fechar-l3-resumo-boa",
}  # fmt: skip
FECHAR_RUINS = {
    "fechar-d3-par-anexado-prazo-ruim", "fechar-d3-par-de-outra-loja-ruim", "fechar-d3-acolhimento-cidade-ruim",
    "fechar-d1-cartao-aceita-ruim", "fechar-agradecimento-produto-ruim", "fechar-sim-garantia-ignora-branco-ruim",
    "fechar-recebido-troca-com-defeito-ruim", "fechar-l3-resumo-rj-ruim", "fechar-nao-informa-mas-informa-ruim",
    "fechar-o-que-e-pulseira-ruim", "fechar-variacoes-incompletas-branco-ruim", "fechar-status-inventado-ruim",
}  # fmt: skip


def test_9_a_calibracao_tem_o_grupo_fechar_eval():
    from scripts.agente_eval import calibrar_verificador as calibrar

    casos = {
        c["id"]: c
        for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)
        if c.get("grupo") == "fechar_eval"
    }
    assert set(casos) == FECHAR_BOAS | FECHAR_RUINS
    for i, c in casos.items():
        assert c["esperado"]["ok_para_envio_automatico"] is (i in FECHAR_BOAS), i
        assert not calibrar.contaminado(c), i
        # Sem dado de cliente: nenhum número de pedido, rastreio ou telefone de verdade.
        bruto = json.dumps(c, ensure_ascii=False)
        assert "26101" not in bruto and "BR0" not in bruto, i
    # Só as do D1 trazem a ação que continua com a pessoa; a do resumo traz a restrição.
    for i, c in casos.items():
        assert bool(c.get("pos_envio")) is i.startswith("fechar-d1"), i
    entrada = calibrar.entrada_do_caso(casos["fechar-l3-resumo-boa"])
    assert (
        v.CABECALHO_RESTRICAO in entrada
        and "depende do valor do pedido: RJ; os demais estados recebem" in entrada
    )
