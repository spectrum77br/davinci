# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos reais
"""Itens 2, 7, 9, 10 e 11 da análise de 31/10–02/11/2026 (Barbosa, Kia, Mini, Vita e Vortan
no automático; o dono, 03/11/2026: "aplique as correções").

2.  A contestação da decisão que a loja já deu e a ameaça de escalar vão para a pessoa
    (`acao_pessoa.contesta_a_decisao_da_loja`, `juridico_fraude_ofensa`); a tela quebrada
    nunca ganha análise, vídeo, devolução nem troca do agente
    (`checagens.conferir_tela_quebrada`, DURA). O 7fcf8f73 da Barbosa.
7.  A devolução JÁ aberta: o D1 vale também na disputa (o reembolso só com a condição,
    `checagens.conferir_responde_e_marca(disputa=True)`), no defeito que só repete o motivo
    dela e no "6" sozinho (838b1125, e9a12d8f, 626c2374, 0ab228be).
9.  A evidência que a loja pediu chegou: o recibo fixo sai na hora e a conversa vai para o
    topo da Caixa Humano (`acao_pessoa.recibo_da_evidencia`, `humano.Triagem.prioridade`;
    235a1bbc, d9b95343, 08153ae8, 9b119731, 0e119859).
10. O Abrir chamado (entrega) e o Ag. cancelamento como ETIQUETA (`acao_pessoa.
    etiquetas_do_turno`; 63678840, 84f6468f, 4bf99127, b4e69bf2).
11. A variação por sim/não e o "6" com a dúvida na conversa (o bloco 2 e o D1).

Os turnos têm a forma dos reais (paráfrases, sem dado de cliente: nenhum nome, telefone, CPF,
endereço, rastreio ou número de pedido real). Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

import app.db as _db
from app.models import AtendimentoConversa, AtendimentoMensagem, AtendimentoRascunho
from app.services.atendimento import agente as agente_svc
from app.services.atendimento import constantes, gravar, humano, ia
from app.services.atendimento.agente import acao_pessoa, checagens, prompt
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import regatilho
from tests.test_atendimento_agente_decisoes_dono import (
    AGORA,
    BLOCO_DEV,
    CTX_POSTADO,
    D1_TEXTO,
    PAR_PRAZO,
    PAR_REEMBOLSO,
    TEMPLATE,
    VALORES,
    _auto,
    _bloco,
    _cli,
    _conv,
    _ctx_dev,
    _loja,
    _m,
    _menu,
    _p,
    _reclamacao,
    _retrato,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _ligar,
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
from tests.test_atendimento_agente_furos import SAIU
from tests.test_atendimento_agente_laco import _categorias_do_manual
from tests.test_atendimento_agente_portao import PACOTE as PACOTE_REGATILHO
from tests.test_atendimento_agente_portao import _registro as _registro_regatilho
from tests.test_atendimento_agente_redondo import SHOPEE_REAL, VAI_AO_VERIFICADOR, VAI_COM_AVISO
from tests.test_atendimento_humano import (  # noqa: F401 — fixtures
    URL,
    _conversa,
    _ids,
    _loja_db,
    _recalcular,
    ctx_falso,
    pessoa,
)
from tests.test_atendimento_ia import PEDIDO_MKT, _importar_manual_base, _pedido_completo
from tests.test_atendimento_ia import _conversa as _conversa_ia
from tests.test_atendimento_ia import _msg as _msg_ia

VAI = VAI_AO_VERIFICADOR


def _ia(texto: str) -> SimpleNamespace:
    """A resposta do agente que saiu (origem davinci_ia)."""
    return _m("loja", texto, origem=constantes.ORIGEM_IA)


def _anexo(tipo: str = "video") -> SimpleNamespace:
    return _m("cliente", None, tipo=tipo)


def _codigos(msgs: list, *, ctx: dict | None = None, conversa: Any = None, postado=None) -> list:
    gatilho = next(m for m in reversed(msgs) if m.autor == "cliente")
    return [
        g.codigo
        for g in acao_pessoa.gatilhos(
            conversa or _conv(), msgs, ctx if ctx is not None else CTX_POSTADO, gatilho=gatilho,
            postado=postado or acao_pessoa.POSTADO_SIM, chaves_ativas=frozenset(), agora=AGORA,
        )
    ]  # fmt: skip


def _triar(msgs: list, ctx: dict, conversa: Any = None, **kw: Any) -> humano.Triagem:
    return humano.triar(
        conversa or _conv(), msgs, ctx, so_humano=(), gatilho=msgs[-1], agente=True,
        postado=kw.pop("postado", acao_pessoa.POSTADO_SIM), agora=AGORA, **kw,
    )  # fmt: skip


@pytest.fixture
def sem_etiquetas(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "atendimento_agente_etiquetas_pos_envio", False)


# ═══════════════ Item 2: a contestação da decisão e a tela quebrada ═══════════════

# 7fcf8f73 (Barbosa, 31/10, paráfrase): a equipe recusou a garantia da tela trincada por mau
# uso; a cliente contesta e diz que vai à central da Shopee.
RECUSA_DA_EQUIPE = "Infelizmente a garantia não cobre mau uso, a tela não quebra sozinha."
CONTESTA = [
    "Não foi mau uso, a tela trincou sozinha dentro da capinha",
    "Se não resolverem vou na central da Shopee",
]
OFERTA_DO_AGENTE = (
    "Sentimos muito, não houve intenção de duvidar de você. Envie um vídeo curto e fotos do "
    "aparelho e a equipe analisa e responde por aqui."
)


def test_2_contestar_a_recusa_da_equipe_vai_para_pessoa():
    msgs = [*_cli("a tela do meu celular trincou"), _loja(RECUSA_DA_EQUIPE), *_cli(*CONTESTA)]
    assert acao_pessoa.contesta_a_decisao_da_loja(msgs, msgs[-1])
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in _codigos(msgs)
    # Só a contestação, sem a ameaça, também (a recusa veio antes).
    so_contesta = [
        *_cli("a tela trincou"),
        _loja(RECUSA_DA_EQUIPE),
        *_cli("Que absurdo, não aceito isso"),
    ]
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in _codigos(so_contesta)
    # A recusa do próprio agente também é decisão da loja.
    do_agente = [
        *_cli("a tela quebrou"),
        _ia("A nossa garantia não cobre aparelho quebrado, molhado ou com mau uso."),
        *_cli("Isso é injusto, eu não derrubei o celular"),
    ]
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in _codigos(do_agente)


@pytest.mark.parametrize(
    "falas",
    [
        ["Vou acionar a Shopee se isso não for resolvido"],
        ["Vou abrir uma reclamação na Shopee"],
        ["Vou denunciar vocês"],
        ["Vou entrar em contato com a central da Shopee"],
    ],
)
def test_2_a_ameaca_de_escalar_vai_para_pessoa_sozinha(falas):
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in _codigos([_menu(), *_cli(*falas)])


@pytest.mark.parametrize(
    "msgs",
    [
        # A contestação sem decisão da loja antes (a reclamação do atraso responde-se).
        [_menu(), *_cli("Isso é um absurdo, meu pedido ainda não chegou")],
        # Depois da recusa, a pergunta de como seguir não é contestação.
        [
            *_cli("a tela trincou"),
            _loja(RECUSA_DA_EQUIPE),
            *_cli("Entendi. E como faço para devolver pela Shopee?"),
        ],
        # "Vou acompanhar pela Shopee" não é ameaça.
        [_menu(), *_cli("Ok, vou acompanhar pelo app da Shopee")],
        # Levar o produto na agência da Shopee é postar a devolução.
        [_menu(), *_cli("Vou levar na agência da Shopee amanhã")],
    ],
)
def test_2_o_que_nao_e_contestacao_nao_trava(msgs):
    assert not acao_pessoa.contesta_a_decisao_da_loja(msgs, msgs[-1])
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA not in _codigos(msgs)


async def test_2_o_7fcf8f73_nao_reabre_a_analise():
    msgs = [*_cli("a tela do meu celular trincou"), _loja(RECUSA_DA_EQUIPE), *_cli(*CONTESTA)]
    r = await _auto(_p(OFERTA_DO_AGENTE, "defeito", ["pedido"]), msgs, ctx_pedido=CTX_POSTADO,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES)  # fmt: skip
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in r.ctx.acoes_pessoa
    assert checagens.TELA_QUEBRADA in r.codigos
    assert r.decisao != VAI
    # A Caixa Humano decide igual (a mesma régua).
    t = _triar(msgs, CTX_POSTADO)
    assert constantes.HUMANO_ACAO_PESSOA in t.motivos
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in t.assuntos and t.marca == ()


@pytest.mark.parametrize(
    "texto",
    [
        OFERTA_DO_AGENTE,
        "Mande uma foto da tela, por favor.",
        "Você pode solicitar a devolução pela Shopee em Minhas Compras.",
        "A equipe vai analisar o seu caso.",
        "A garantia cobre esse tipo de problema.",
        "Podemos trocar o aparelho.",
    ],
)
def test_2_a_tela_quebrada_nunca_ganha_oferta(texto):
    p = checagens.conferir_tela_quebrada(
        texto, acao="responder", textos_cliente=["a tela do meu celular trincou sozinha"]
    )
    assert [x.codigo for x in p] == [checagens.TELA_QUEBRADA]
    assert checagens.pendencia_dura(p[0])


def test_2_a_tela_quebrada_sem_oferta_e_fora_dela():
    trincou = ["a tela trincou"]
    # A regra do dono dita: sem oferta.
    assert not checagens.conferir_tela_quebrada(
        "Sentimos muito. A nossa garantia não cobre aparelho quebrado, molhado ou com mau uso.",
        acao="responder", textos_cliente=trincou,
    )  # fmt: skip
    # Sem a tela quebrada na conversa, o pedido de vídeo do defeito é a regra aprovada.
    assert not checagens.conferir_tela_quebrada(
        OFERTA_DO_AGENTE, acao="responder", textos_cliente=["o celular trava toda hora"]
    )
    # A regra dita com o "apenas" também não é oferta.
    assert not checagens.conferir_tela_quebrada(
        "A garantia cobre apenas defeito de fabricação; tela quebrada não é coberta.",
        acao="responder", textos_cliente=trincou,
    )  # fmt: skip
    # A devolução que o cliente JÁ abriu (D1/rotina) só se informa.
    assert not checagens.conferir_tela_quebrada(
        "A sua solicitação de devolução está aberta na Shopee.",
        acao="responder", textos_cliente=trincou, informa_a_solicitacao=True,
    )  # fmt: skip


# ═══════════════ Item 7: a devolução já aberta ═══════════════

D1_COM_CONDICAO = (
    "A sua solicitação está em análise pela Shopee. Se a Shopee aprovar, o valor volta pela "
    "mesma forma de pagamento, e o andamento aparece em Minhas Compras > o pedido."
)


@pytest.mark.parametrize(
    ("falas", "status"),
    [
        # 0ab228be (SELLER_DISPUTE, paráfrase): "quando sai o reembolso?".
        (["Quando sai o meu reembolso?"], "Loja contestou"),
        # 626c2374 (a disputa, paráfrase): a cobrança do reembolso.
        (["Preciso do reembolso urgente"], "Em análise pela Shopee (disputa)"),
        # e9a12d8f (JUDGING, paráfrase): o aviso de que pediu a devolução pelo defeito.
        (
            ["Eu já pedi a devolução, o aparelho veio com vários defeitos desde o início"],
            "Em análise pela Shopee (disputa)",
        ),
    ],
)
async def test_7_na_disputa_o_d1_so_com_a_condicao(falas, status):
    ctx = _ctx_dev(reclamacoes=[_reclamacao(status=status)])
    msgs = [_menu(), *_cli(*falas)]
    # O texto que dá o reembolso como certo: DURO na disputa.
    certo = await _auto(_p(D1_TEXTO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=ctx,
                        bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                        pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert certo.ctx.responde_e_marca, falas
    assert certo.decisao != VAI
    assert any("certo na disputa" in a for a in [*certo.motivos, *certo.avisos]), certo.motivos
    # O texto com a condição vai ao verificador; a reclamação fica para depois do envio.
    ok = await _auto(_p(D1_COM_CONDICAO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=ctx,
                     bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                     pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert ok.decisao in (VAI, VAI_COM_AVISO), (ok.codigos, ok.motivos)
    assert ok.ctx.acoes_pos_envio == [constantes.ACAO_RECLAMACAO_ABERTA]


def test_7_a_condicao_do_reembolso_na_disputa_pura():
    assert checagens._reembolso_sem_condicao(checagens._plano(D1_TEXTO))
    assert not checagens._reembolso_sem_condicao(checagens._plano(D1_COM_CONDICAO))
    assert not checagens._reembolso_sem_condicao(
        checagens._plano("A solicitação está em análise pela Shopee; acompanhe em Minhas Compras.")
    )
    fora = checagens.conferir_responde_e_marca(D1_TEXTO, acao="responder", marca=True)
    assert fora == []  # fora da disputa, o processo da plataforma é a orientação aprovada


@pytest.mark.parametrize(
    "falas",
    [
        # 838b1125 (Vortan, 31/10, paráfrase): "não liga, quantos dias espero o reembolso?".
        ["O celular não liga", "Quantos dias tenho que esperar o reembolso?"],
        # 838b1125 (01/11): onde está o código para postar.
        ["Qual é o código para postar a devolução?"],
        # e9a12d8f fora da disputa.
        ["Já pedi a devolução, veio com defeito"],
        ["Parou de funcionar, já abri a devolução, e agora?"],
        # 626c2374 (01/11): o "6" sozinho com a devolução aberta — o estado da solicitação.
        ["6"],
    ],
)
async def test_7_o_defeito_da_devolucao_aberta_e_o_6_informam(falas):
    msgs = [_menu(), *_cli(*falas)]
    r = await _auto(_p(D1_TEXTO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=_ctx_dev(),
                    bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert r.ctx.responde_e_marca, falas
    # Vai ao verificador (com o aviso do defeito além da orientação, quando há: ele decide).
    assert r.decisao in (VAI, VAI_COM_AVISO), (falas, r.codigos, r.motivos)
    assert r.ctx.acoes_pos_envio == [constantes.ACAO_RECLAMACAO_ABERTA]
    t = _triar(msgs, _ctx_dev())
    assert set(t.motivos) <= set(t.marca), (t.motivos, t.marca)


@pytest.mark.parametrize(
    ("falas", "ctx_kw"),
    [
        # Um pedido de reembolso de verdade (sem devolver, parcial, Pix): pessoa.
        (["Veio com defeito, quero o reembolso sem devolver"], {}),
        (["Não funciona, quero o reembolso no pix"], {}),
        (["Não liga, vocês podem aprovar logo a devolução?"], {}),
        # O problema que não é o defeito: quebrado, usado, errado, falsificado.
        (["O reembolso não caiu e o produto veio quebrado"], {}),
        (["Quero o reembolso, o produto veio usado"], {}),
        # ced31bbd (02/11, paráfrase): cancelar e devolver pela cor errada, na disputa.
        (
            ["Quero cancelar e devolver, a cor veio diferente"],
            {"reclamacoes": [_reclamacao(status="Em análise pela Shopee (disputa)")]},
        ),
        # O chamado no pedido continua (0ab228be também tinha um).
        (["Quando sai o meu reembolso?"], {"chamados": [{"status": "aberto"}]}),
        # A ameaça na disputa.
        (
            ["Se não devolverem vou no Procon"],
            {"reclamacoes": [_reclamacao(status="Loja contestou")]},
        ),
    ],
)
async def test_7_o_pedido_e_o_problema_continuam_com_pessoa(falas, ctx_kw):
    msgs = [_menu(), *_cli(*falas)]
    r = await _auto(_p(D1_COM_CONDICAO, "reembolso", ["pedido"]), msgs, ctx_pedido=_ctx_dev(**ctx_kw),
                    bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES)  # fmt: skip
    assert not r.ctx.responde_e_marca, falas
    assert r.decisao != VAI


def test_7_o_defeito_com_a_evidencia_continua_com_pessoa():
    msgs = [_menu(), *_cli("Não liga, segue o vídeo"), _anexo("video")]
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), msgs, msgs[-1])


def test_7_a_chave_desliga_a_regra_nova(sem_etiquetas):
    msgs = [_menu(), *_cli("O celular não liga", "Quantos dias tenho que esperar o reembolso?")]
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), msgs, msgs[-1])
    disputa = _ctx_dev(reclamacoes=[_reclamacao(status="Loja contestou")])
    quando = [_menu(), *_cli("Quando sai o meu reembolso?")]
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), disputa, quando, quando[-1])
    assert not acao_pessoa.responde_e_marca_do_turno(
        _conv(), _ctx_dev(), [_menu(), *_cli("6")], None
    )


# ═══════════════ Item 9: o recibo da evidência ═══════════════

PEDE_VIDEO = (
    "Sentimos muito pelo transtorno. Descreva o que acontece e mande um vídeo curto mostrando "
    "a bateria acabando; a equipe analisa e responde por aqui."
)


def _em_ordem(msgs: list) -> list:
    """As mensagens no relógio da ordem da lista (os anexos do `parametrize` nascem antes)."""
    inicio = AGORA - timedelta(hours=1)
    saida = []
    for i, m in enumerate(msgs):
        novo = SimpleNamespace(**vars(m))
        novo.enviada_em = novo.created_at = inicio + timedelta(seconds=i)
        saida.append(novo)
    return saida


def _evidencia(*depois: SimpleNamespace, pedido: str = PEDE_VIDEO) -> list:
    """235a1bbc (Vortan, paráfrase): a bateria zera em minutos, o agente pediu o vídeo."""
    return _em_ordem(
        [*_cli("O celular chegou ontem e a bateria zera em minutos"), _ia(pedido), *depois]
    )


@pytest.mark.parametrize(
    ("depois", "esperado"),
    [
        ([_anexo("video")], "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui."),
        # 235a1bbc (01/11): o vídeo junto com o "6".
        (
            [_anexo("video"), *_cli("6")],
            "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui.",
        ),
        # 9b119731 (01/11): as fotos do que veio na caixa.
        (
            [_anexo("imagem"), _anexo("imagem"), _anexo("imagem")],
            "Recebemos as fotos, obrigado! A equipe analisa e responde por aqui.",
        ),
        ([_anexo("imagem")], "Recebemos a foto, obrigado! A equipe analisa e responde por aqui."),
        (
            [_anexo("video"), _anexo("imagem")],
            "Recebemos o vídeo e as fotos, obrigado! A equipe analisa e responde por aqui.",
        ),
        # 0e119859 (01/11): as fotos com o texto do que está errado.
        (
            [_anexo("imagem"), *_cli("o cabo é USB e a entrada é tipo C")],
            "Recebemos a foto, obrigado! A equipe analisa e responde por aqui.",
        ),
    ],
)
def test_9_a_evidencia_pedida_ganha_o_recibo(depois, esperado):
    msgs = _evidencia(*depois)
    texto = acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1])
    assert texto == esperado
    assert texto in acao_pessoa.TEXTOS_DO_RECIBO and acao_pessoa.e_recibo_da_evidencia(texto)
    # A Caixa Humano: a evidência com a etiqueta, no topo, e a marca cobre a ação.
    t = _triar(msgs, CTX_POSTADO)
    assert t.motivos == (constantes.HUMANO_ACAO_PESSOA,)
    assert t.assuntos == (constantes.ACAO_ANALISAR_EVIDENCIA,)
    assert t.marca == (constantes.HUMANO_ACAO_PESSOA,) and t.prioridade


def test_9_o_recibo_nao_promete_nada():
    for texto in acao_pessoa.TEXTOS_DO_RECIBO:
        assert checagens.conferir_promete_resultado(texto) == []
        assert checagens.conferir_pix_ou_banco(texto) == []
        assert not acao_pessoa.pede_evidencia(texto)


@pytest.mark.parametrize(
    ("msgs", "ctx"),
    [
        # O pedido de dinheiro junto (aprovar fora da plataforma): pessoa, sem recibo.
        (_evidencia(_anexo("video"), *_cli("me devolve o dinheiro no pix")), CTX_POSTADO),
        # A ameaça junto.
        (_evidencia(_anexo("video"), *_cli("se não resolverem vou no Procon")), CTX_POSTADO),
        # A devolução aberta na plataforma: a régua do D1 (com anexo, pessoa).
        (_evidencia(_anexo("video")), _ctx_dev()),
        # O cancelamento pedido junto, com o estado de postagem desconhecido: segura.
        (_evidencia(_anexo("video"), *_cli("quero cancelar")), CTX_POSTADO),
    ],
)
def test_9_sem_recibo_quando_a_pessoa_precisa_de_mais(msgs, ctx):
    assert acao_pessoa.recibo_da_evidencia(_conv(), ctx, msgs, msgs[-1]) is None
    t = _triar(msgs, ctx)
    assert set(t.motivos) - set(t.marca), (t.motivos, t.marca)


def test_9_a_tela_quebrada_nao_ganha_o_recibo():
    """7fcf8f73 (01/11): as fotos da tela trincada depois do pedido — sem o "a equipe analisa"
    (a garantia não cobre: a pessoa responde)."""
    msgs = _em_ordem([
        *_cli("A tela do meu celular trincou sozinha"),
        _ia("Sentimos muito. Envie fotos da tela, por favor."),
        _anexo("imagem"),
    ])  # fmt: skip
    assert acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1]) is None
    assert _codigos(msgs) == [constantes.ACAO_ANALISAR_EVIDENCIA]


def test_9_sem_o_pedido_da_loja_nao_ha_recibo():
    """O anexo sem o pedido antes: o agente pergunta como ajudar (a régua de sempre)."""
    msgs = _em_ordem([*_cli("oi"), _ia("Olá! Como podemos ajudar?"), _anexo("video")])
    assert acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1]) is None
    assert _codigos(msgs) == []


def test_9_o_recibo_sai_uma_vez_e_a_foto_seguinte_vai_para_a_pessoa():
    recibo = "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui."
    msgs = _evidencia(_anexo("video"), _ia(recibo), _anexo("imagem"))
    assert acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1]) is None
    # O recibo não corta o caso: a foto de depois ainda responde ao pedido de antes.
    assert acao_pessoa.pediu_evidencia(msgs, msgs[-1])
    assert _codigos(msgs) == [constantes.ACAO_ANALISAR_EVIDENCIA]
    antes = acao_pessoa.gatilhos_antes_do_modelo(msgs, msgs[-1])
    assert [g.codigo for g in antes.gatilhos] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    t = _triar(msgs, CTX_POSTADO)
    assert t.prioridade and t.marca == ()


def test_9_fora_da_shopee_e_com_a_chave_desligada(monkeypatch):
    msgs = _evidencia(_anexo("video"))
    assert (
        acao_pessoa.recibo_da_evidencia(_conv(plataforma="ml"), CTX_POSTADO, msgs, msgs[-1]) is None
    )
    assert (
        acao_pessoa.recibo_da_evidencia(
            _conv(canal=constantes.CANAL_PERGUNTA), CTX_POSTADO, msgs, msgs[-1]
        )
        is None
    )
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "atendimento_agente_etiquetas_pos_envio", False)
    assert acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1]) is None


def test_9_o_rascunho_do_recibo():
    msgs = _evidencia(_anexo("video"))
    texto = acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1])
    conv = _conv()
    resultado = ia.recibo_do_agente(conv, msgs[-1], texto, modo=constantes.MODO_AUTO)
    agente = resultado.fatos_agente
    assert resultado.precisa_humano is False and resultado.status == constantes.RASCUNHO_PENDENTE
    assert agente["acoes_pessoa"] == [] and agente["acoes_codigo"] == [
        constantes.ACAO_ANALISAR_EVIDENCIA
    ]
    assert agente["acoes_pos_envio"] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    assert agente["etiquetas_pos_envio"] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    assert "responde_e_marca" not in agente  # as isenções da devolução não ligam
    rascunho = SimpleNamespace(
        prompt_versao=constantes.PROMPT_VERSAO_AGENTE, texto=texto, fatos={"agente": agente}
    )
    assert ia.recibo_no_rascunho(rascunho) and ia.responde_e_marca_no_rascunho(rascunho)
    # O texto mexido não é mais o recibo (não pula o verificador).
    mexido = SimpleNamespace(**{**vars(rascunho), "texto": texto + " Vamos trocar o aparelho."})
    assert not ia.recibo_no_rascunho(mexido)
    assert ia.trava_do_agente(rascunho, SHOPEE_REAL) in (
        None,
        "orientacao_nao_liberada",
        "assunto_nao_liberado",
    )


async def test_9_e2e_o_recibo_sai_e_fica_no_topo_da_caixa_humano(
    db, make_user, agente_ligado, api, envios
):
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa_ia(db, make_user, pedido=PEDIDO_MKT)
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO, auto_categorias=SHOPEE_REAL.auto_categorias,
        auto_orientacao=SHOPEE_REAL.auto_orientacao, auto_agente=SHOPEE_REAL.auto_agente,
    )  # fmt: skip
    await _msg_ia(
        db, conversa, "O celular chegou e a bateria zera em minutos", ha=timedelta(minutes=30)
    )
    await _msg_ia(
        db,
        conversa,
        PEDE_VIDEO,
        autor="loja",
        origem=constantes.ORIGEM_IA,
        ha=timedelta(minutes=25),
    )
    await _msg_ia(db, conversa, None, tipo="video", ha=timedelta(minutes=5))
    await db.refresh(conversa)
    api.roteiro = []  # o modelo não é chamado

    r = await ia.gerar_rascunho(db, conversa)

    assert (
        r is not None
        and r.texto == "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui."
    )
    assert api.clientes == []  # sem custo: nem o agente nem o verificador
    assert _do_agente(r)["acoes_pos_envio"] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.fatos
    # A resposta chega como a do envio real; a conversa continua na Caixa Humano, no topo.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="ia-recibo", autor="loja", texto=r.texto,
        enviada_em=datetime.now(UTC), origem=constantes.ORIGEM_IA,
    )  # fmt: skip
    await db.commit()
    await db.refresh(conversa)
    assert not conversa.aguardando_resposta and humano.pos_envio_vale(conversa)
    await humano.recalcular(orcamento_s=None)
    dados = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == conversa.id)
    )
    assert dados["humano"]["prioridade"] is True and dados["humano"]["pos_envio"] is True
    assert constantes.ACAO_ANALISAR_EVIDENCIA in dados["humano"]["assuntos"]
    async with _db.SessionLocal() as s:
        lojas_ = await humano.lojas_da_caixa(s, humano.modos_da_caixa())
        linha = (
            await s.execute(
                select(humano.caixa_humano_sql(lojas_), humano.prioridade_sql()).where(
                    AtendimentoConversa.id == conversa.id
                )
            )
        ).one()
    assert tuple(linha) == (True, True)


# ═══════════════ Item 10: o Abrir chamado (entrega) e o Ag. cancelamento como etiqueta ═══════════════


@pytest.mark.parametrize(
    "falas",
    [
        # 63678840 / 84f6468f (paráfrases): o pedido parado, o cliente pede para a loja ver.
        ["Meu pedido está sem atualização desde quarta"],
        ["já tem 8 dias e nem saiu de SP, vocês podem ver junto com a Shopee?"],
        ["Meu pedido está atrasado, verifica com a transportadora por favor"],
    ],
)
async def test_10_o_rastreio_sai_e_o_chamado_fica_como_etiqueta(falas):
    msgs = [_menu(), *_cli(*falas)]
    conv = _conv(dados=_retrato(1))
    r = await _auto(_p(TEMPLATE, "rastreio", ["pedido", "par:8440"]), msgs, ctx_pedido=CTX_POSTADO,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_PRAZO], conversa=conv)  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO in r.ctx.acoes_codigo, falas
    assert r.ctx.acoes_pos_envio == [constantes.ACAO_ABRIR_CHAMADO]
    assert r.decisao == VAI, (falas, r.codigos, r.motivos)
    agente = r.rascunho.fatos["agente"]
    assert agente["etiquetas_pos_envio"] == [constantes.ACAO_ABRIR_CHAMADO]
    assert ia.responde_e_marca_no_rascunho(r.rascunho)
    assert not ia.andamento_no_rascunho(r.rascunho)
    t = _triar(msgs, CTX_POSTADO, conversa=conv)
    assert t.motivos == (constantes.HUMANO_ACAO_PESSOA,) and t.marca == t.motivos


@pytest.mark.parametrize(
    ("falas", "ctx"),
    [
        # O dinheiro ou o cancelamento pedido pelo atraso: trava.
        (["Não chegou e a previsão já passou, quero o reembolso"], CTX_POSTADO),
        # O extravio, o roubo, a entrega errada: trava.
        (["Meu pedido foi extraviado, sumiu"], CTX_POSTADO),
        (["Entregaram no endereço errado, para outra pessoa"], CTX_POSTADO),
        # Consta entregue e não recebi: trava.
        (
            ["Consta entregue mas eu não recebi nada"],
            {**CTX_POSTADO, "logistica": {**CTX_POSTADO["logistica"], "status": "Entregue"}},
        ),
        # O chamado já aberto no pedido: a régua de antes.
        (
            ["Meu pedido está sem atualização desde quarta"],
            {**CTX_POSTADO, "chamados": [{"status": "aberto"}]},
        ),
    ],
)
async def test_10_o_que_continua_trava(falas, ctx):
    msgs = [_menu(), *_cli(*falas)]
    conv = _conv(dados=_retrato(1))
    r = await _auto(_p(TEMPLATE, "rastreio", ["pedido", "par:8440"]), msgs, ctx_pedido=ctx,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_PRAZO], conversa=conv)  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO not in r.ctx.acoes_pos_envio, falas
    assert r.decisao != VAI


async def test_10_a_promessa_do_chamado_nao_sai_com_a_etiqueta():
    msgs = [_menu(), *_cli("Meu pedido está sem atualização desde quarta")]
    texto = "O último status é: em separação no centro logístico. Vamos abrir um chamado com a Shopee e te avisamos."
    r = await _auto(_p(texto, "rastreio", ["pedido", "par:8440"]), msgs, ctx_pedido=CTX_POSTADO,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_PRAZO], conversa=_conv(dados=_retrato(1)))  # fmt: skip
    assert r.ctx.acoes_pos_envio == [constantes.ACAO_ABRIR_CHAMADO]
    assert r.decisao != VAI and checagens.PROMESSA_DE_EQUIPE in r.codigos


def test_10_o_chamado_que_so_o_modelo_escolheu_vira_etiqueta():
    """63678840 (02/11): o código não marcou; o modelo escolheu abrir_chamado e escreveu o
    rastreio (a equipe mandou igual, 5,5 h depois)."""
    msgs = [_menu(), *_cli("A entrega passou da data e não chegou")]
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), msgs[-1], modo=constantes.MODO_AUTO)
    ctx.etiquetas_pos_envio = acao_pessoa.etiquetas_do_turno(
        _conv(), CTX_POSTADO, msgs, msgs[-1], []
    )
    assert constantes.ACAO_ABRIR_CHAMADO in ctx.etiquetas_pos_envio
    p = Proposta(acao="pessoa", texto=TEMPLATE, assunto="rastreio", confianca=0.85,
                 acao_pessoa="abrir_chamado", motivo_pessoa="previsão vencida", evidencias=["pedido"])  # fmt: skip
    nova, depois = checagens.responde_e_marca_da_proposta(p, ctx)
    assert nova.acao == "responder" and nova.acao_pessoa is None
    assert depois == [constantes.ACAO_ABRIR_CHAMADO]
    ctx.acoes_modelo = depois
    assert ctx.acoes_pessoa == [] and ctx.acoes_pos_envio == [constantes.ACAO_ABRIR_CHAMADO]
    # Outra ação do modelo (o dado que falta, a exceção) continua pessoa.
    for outra in ("dado_indisponivel", "excecao", "analisar_evidencia"):
        q = p.model_copy(update={"acao_pessoa": outra})
        assert checagens.responde_e_marca_da_proposta(q, ctx) == (q, [])


def test_10_a_etiqueta_deixada_continua_na_caixa_humano():
    """A ação que o agente deixou para depois do envio (o abrir_chamado do modelo, que o
    código não vê) segura a conversa na Caixa Humano enquanto a marca vale."""
    turno = AGORA - timedelta(minutes=20)
    marca = {"turno": turno.timestamp(), "ate": (AGORA + timedelta(minutes=2)).timestamp(),
             "acoes": [constantes.ACAO_ABRIR_CHAMADO]}  # fmt: skip
    conv = _conv(aguardando_resposta=False, ultima_do_cliente_em=turno, ultima_da_loja_em=AGORA,
                 sem_resposta_necessaria=False, dados={humano.CHAVE_POS_ENVIO: marca})  # fmt: skip
    msgs = [_menu(), *_cli("A entrega passou da data e não chegou")]
    t = _triar(msgs, CTX_POSTADO, conversa=conv)
    assert t.pos_envio and constantes.HUMANO_ACAO_PESSOA in t.motivos
    assert constantes.ACAO_ABRIR_CHAMADO in t.assuntos and not t.prioridade
    # Uma pessoa respondeu depois: sai.
    respondida = _conv(**{**vars(conv), "ultima_da_loja_em": AGORA + timedelta(minutes=30)})
    assert _triar(msgs, CTX_POSTADO, conversa=respondida).motivos == ()


def _ctx_ag(**kw: Any) -> dict:
    """O pedido em Ag. cancelamento (o 83955 da falta de estoque, como no b4e69bf2)."""
    pedido = {**CTX_POSTADO["pedido"], "situacao": "Aguardando Cancelamento",
              "ag_cancelamento": {"codigo": "sem_estoque"}}  # fmt: skip
    return {**CTX_POSTADO, "pedido": pedido, **kw}


@pytest.mark.parametrize(
    ("falas", "etiqueta"),
    [
        # A pergunta de produto (o 4bf99127 de 01/11 sem o "enviado"): etiqueta.
        (["Esse celular tem NFC?"], True),
        (["Boa tarde"], True),
        (["Muito obrigada pela atenção"], True),
        # b4e69bf2 (02/11): a previsão de entrega do pedido sem estoque — pessoa.
        (["Qual a previsão de entrega?"], False),
        # 4bf99127 (01/11 e 02/11): o aparelho que será enviado; cancelar — pessoa.
        (["Qual aparelho vai ser enviado?"], False),
        (["Quero cancelar o pedido"], False),
        (["Quando chega?"], False),
        (["Me manda a nota fiscal"], False),
    ],
)
def test_10_o_ag_cancelamento_so_na_fala_que_nao_toca_o_pedido(falas, etiqueta):
    msgs = [_menu(), *_cli(*falas)]
    lista = acao_pessoa.gatilhos(_conv(), msgs, _ctx_ag(), gatilho=msgs[-1],
                                 postado=acao_pessoa.POSTADO_NAO, chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    assert constantes.ACAO_AG_CANCELAMENTO in [g.codigo for g in lista]
    got = acao_pessoa.etiquetas_do_turno(_conv(), _ctx_ag(), msgs, msgs[-1], lista)
    assert (constantes.ACAO_AG_CANCELAMENTO in got) is etiqueta, falas
    t = _triar(msgs, _ctx_ag(), postado=acao_pessoa.POSTADO_NAO)
    assert constantes.HUMANO_AG_CANCELAMENTO in t.motivos
    assert (constantes.HUMANO_AG_CANCELAMENTO in t.marca) is etiqueta


async def test_10_o_ag_cancelamento_como_etiqueta_na_resposta():
    msgs = [_menu(), *_cli("Muito obrigada pela atenção")]
    ok = await _auto(_p("Nós que agradecemos! Qualquer dúvida, é só chamar.", "agradecimento", []),
                     msgs, ctx_pedido=_ctx_ag(), bloco=_bloco(), postado=acao_pessoa.POSTADO_NAO,
                     valores=VALORES)  # fmt: skip
    assert ok.ctx.acoes_pos_envio == [constantes.ACAO_AG_CANCELAMENTO]
    assert ia.MOTIVO_AG_CANCELAMENTO not in ok.motivos
    assert ok.decisao == VAI, (ok.codigos, ok.motivos)
    # A resposta que fala do destino do pedido: DURA.
    ruim = await _auto(_p("Nós que agradecemos! O seu pedido será enviado em breve.", "agradecimento", []),
                       msgs, ctx_pedido=_ctx_ag(), bloco=_bloco(), postado=acao_pessoa.POSTADO_NAO,
                       valores=VALORES)  # fmt: skip
    assert checagens.AG_CANCELAMENTO_NA_RESPOSTA in ruim.codigos and ruim.decisao != VAI
    # Sem a etiqueta (a pergunta do envio), o 83955 segura como sempre.
    envio = await _auto(_p("O pedido está em processamento.", "prazo_envio", ["pedido"]),
                        [_menu(), *_cli("Qual a previsão de entrega?")], ctx_pedido=_ctx_ag(),
                        bloco=_bloco(), postado=acao_pessoa.POSTADO_NAO, valores=VALORES)  # fmt: skip
    assert envio.ctx.acoes_pos_envio == [] and envio.decisao != VAI


def test_10_a_chave_desliga_as_etiquetas(sem_etiquetas):
    msgs = [_menu(), *_cli("Meu pedido está sem atualização desde quarta")]
    lista = acao_pessoa.gatilhos(_conv(dados=_retrato(1)), msgs, CTX_POSTADO, gatilho=msgs[-1],
                                 postado=acao_pessoa.POSTADO_SIM, chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    assert (
        acao_pessoa.etiquetas_do_turno(_conv(dados=_retrato(1)), CTX_POSTADO, msgs, msgs[-1], lista)
        == frozenset()
    )


# ═══════════════ Item 11: a variação e o "6" ═══════════════


def test_11_o_bloco_2_diz_a_variacao_e_as_etiquetas_e_cabe():
    shopee = prompt.bloco_da_plataforma("shopee", _categorias_do_manual())
    assert 'nunca só "confira no anúncio"' in shopee
    assert '"em análise pela Shopee", reembolso só "se aprovado"' in shopee
    assert "sem prometer o chamado" in shopee
    assert len(shopee) / 3.5 <= 5_250


# ═══════════════ A Caixa Humano: a prioridade no topo ═══════════════


def test_9_a_prioridade_na_tela_e_no_cursor():
    from app.routers import atendimento as rota

    tela = humano.para_tela({"motivos": ["acao_pessoa"], "prioridade": True}, ia_pausada=False)
    assert tela["prioridade"] is True
    assert "prioridade" not in humano.para_tela({"motivos": ["acao_pessoa"]}, ia_pausada=False)
    assert "prioridade" not in humano.para_tela(
        {"motivos": [], "prioridade": True}, ia_pausada=False
    )
    quando = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    prio = {"id": "b", "turno_em": quando, "humano": {"prioridade": True}}
    comum = {"id": "a", "turno_em": quando + timedelta(hours=1), "humano": {}}
    assert sorted([comum, prio], key=rota._chave_do_turno, reverse=True) == [prio, comum]
    assert rota._cursor_do_turno(prio) == f"turno:prio:{quando.isoformat()}|b"
    assert rota._cursor(rota._cursor_do_turno(prio), pelo_prazo=False, pelo_turno=True)[3] == 0
    assert rota._cursor(rota._cursor_do_turno(comum), pelo_prazo=False, pelo_turno=True)[3] == 1


async def test_9_a_caixa_humano_lista_a_prioridade_primeiro(
    client, db, make_user, pessoa, ctx_falso
):
    dono = await make_user()
    integ, canal = await _loja_db(db, dono)
    convs = [
        await _conversa(db, integ, canal, k, "quero trocar", ha=timedelta(hours=h))
        for k, h in (("A", 5), ("B", 1), ("C", 3), ("D", 2))
    ]
    await _recalcular()
    # A mais velha (A) e a C com a evidência recebida: as duas no topo, cada grupo pelo turno.
    for c in (convs[0], convs[2]):
        await db.execute(
            update(AtendimentoConversa)
            .where(AtendimentoConversa.id == c.id)
            .values(dados=AtendimentoConversa.dados.op("||")({"humano": {
                **(await db.scalar(select(AtendimentoConversa.dados["humano"]).where(AtendimentoConversa.id == c.id))),
                "prioridade": True,
            }}))
        )  # fmt: skip
    await db.commit()
    esperado = [str(convs[2].id), str(convs[0].id), str(convs[1].id), str(convs[3].id)]
    r = await client.get(f"{URL}/conversas", params={"caixa": "humano"})
    assert r.status_code == 200, r.text
    assert _ids(r) == esperado
    ids: list[str] = []
    cursor = None
    for _ in range(6):
        pedido = {"caixa": "humano", "limite": 1, **({"antes_de": cursor} if cursor else {})}
        r = await client.get(f"{URL}/conversas", params=pedido)
        assert r.status_code == 200, r.text
        ids += _ids(r)
        cursor = r.json()["proximo"]
        if cursor is None:
            break
    assert ids == esperado


# ═══════════════ A revisão de 03/11/2026: os achados do branch ═══════════════

# Achado 1 (item 7): na disputa, o reembolso dado como certo no futuro, no infinitivo ou com
# outro verbo também segura — antes, só "o reembolso é feito pela Shopee" segurava.
DISPUTA_CERTO = [
    "A Shopee vai devolver o seu dinheiro pela mesma forma de pagamento.",
    "A Shopee vai reembolsar você pela mesma forma de pagamento.",
    "A Shopee vai estornar o valor na mesma forma de pagamento.",
    "A Shopee devolverá o valor pela mesma forma de pagamento.",
    "A Shopee irá reembolsar o valor pela mesma forma de pagamento.",
    "Você vai receber o reembolso pela mesma forma de pagamento.",
    "O reembolso vai ser feito pela mesma forma de pagamento.",
    "Seu dinheiro será estornado na mesma forma de pagamento.",
    "O reembolso será liberado pela mesma forma de pagamento.",
    "O valor vai ser devolvido pela mesma forma de pagamento.",
    "O reembolso sai pela mesma forma de pagamento.",
    "A Shopee está analisando e vai devolver o seu dinheiro.",
    "Após a análise, o reembolso é feito pela mesma forma de pagamento.",
    "A Shopee fará o reembolso pela mesma forma de pagamento.",
    "Pode aguardar: você fica com o reembolso garantido.",
]
# O que só informa a disputa (a condição, a incerteza, o nome da solicitação): sai.
DISPUTA_INFORMA = [
    D1_COM_CONDICAO,
    "A sua solicitação de reembolso está em análise pela Shopee.",
    "O pedido de devolução e reembolso está em análise pela Shopee; acompanhe em Minhas Compras.",
    "O reembolso depende da decisão da Shopee.",
    "O reembolso, se aprovado, é feito pela mesma forma de pagamento.",
    "Caso a decisão seja a seu favor, o reembolso é feito pela Shopee.",
    "Compreendemos a sua preocupação com o reembolso. A Shopee está analisando a disputa.",
    "Não conseguimos informar quando sai o reembolso: a decisão é da Shopee.",
    "Para receber o reembolso, é preciso aguardar a análise da Shopee.",
]


def test_rev_7_a_disputa_pela_frase_pura():
    for texto in DISPUTA_CERTO:
        assert checagens._reembolso_sem_condicao(checagens._plano(texto)), texto
        # Fora da disputa a régua do D1 de antes não muda (a frase da disputa não entra).
        assert not any(
            "certo na disputa" in x.motivo
            for x in checagens.conferir_responde_e_marca(texto, acao="responder", marca=True)
        ), texto
    for texto in DISPUTA_INFORMA:
        assert not checagens._reembolso_sem_condicao(checagens._plano(texto)), texto


@pytest.mark.parametrize("texto", DISPUTA_CERTO)
async def test_rev_7_na_disputa_o_reembolso_certo_em_qualquer_tempo_segura(texto):
    """0ab228be/626c2374 (paráfrase): o cliente cobra o reembolso na disputa que a loja
    contestou; o rascunho dá o reembolso como certo — antes da revisão, ia ao verificador."""
    ctx = _ctx_dev(reclamacoes=[_reclamacao(status="Loja contestou")])
    msgs = [_menu(), *_cli("Quando sai o meu reembolso?")]
    r = await _auto(_p(f"A solicitação está em análise pela Shopee. {texto}", "reembolso", ["pedido", "par:2003"]),
                    msgs, ctx_pedido=ctx, bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM,
                    valores=VALORES, pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert r.ctx.responde_e_marca
    assert r.decisao not in (VAI, VAI_COM_AVISO), (texto, r.decisao)
    assert any("certo na disputa" in m for m in r.motivos), r.motivos


async def test_rev_7_o_verificador_le_a_linha_da_disputa():
    msgs = [_menu(), *_cli("Quando sai o meu reembolso?")]
    disputa = _ctx_dev(reclamacoes=[_reclamacao(status="Loja contestou")])
    r = await _auto(_p(D1_COM_CONDICAO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=disputa,
                    bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert r.decisao in (VAI, VAI_COM_AVISO)
    resumo = v.resumo_do_pacote(r.ctx, r.base, msgs[-1])
    assert resumo.disputa and resumo.pos_envio == (constantes.ACAO_RECLAMACAO_ABERTA,)
    entrada = v.montar_entrada(acao="responder", acao_pessoa=None, texto=D1_COM_CONDICAO,
                               resumo=resumo, consultas=[])  # fmt: skip
    assert v.CABECALHO_POS_ENVIO in entrada and v.LINHA_DA_DISPUTA in entrada
    # Sem disputa (a devolução aberta de sempre): a linha não vai.
    fora = await _auto(_p(D1_TEXTO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=_ctx_dev(),
                       bloco=BLOCO_DEV, postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                       pares_do_pacote=[PAR_REEMBOLSO])  # fmt: skip
    assert fora.ctx.acoes_pos_envio == [constantes.ACAO_RECLAMACAO_ABERTA]
    resumo2 = v.resumo_do_pacote(fora.ctx, fora.base, msgs[-1])
    assert not resumo2.disputa
    assert v.LINHA_DA_DISPUTA not in v.montar_entrada(
        acao="responder", acao_pessoa=None, texto=D1_TEXTO, resumo=resumo2, consultas=[]
    )
    assert v.linhas_do_pos_envio((constantes.ACAO_ABRIR_CHAMADO,), disputa=True)[-1].endswith(
        "verificação nem data"
    )


# Achado 2 (item 10): com o Ag. cancelamento como etiqueta, a resposta que dá o pedido como
# confirmado/garantido/"tudo certo", fala do estoque da variação ou oferece outra cor/troca
# segura (o b4e69bf2: a loja ofereceu outra cor no dia seguinte — trabalho da pessoa).
AG_ESTADO_OU_TROCA = [
    (
        "Muito obrigada pela atenção",
        "Nós que agradecemos! Seu pedido está confirmado e segue normalmente.",
        "agradecimento",
    ),
    (
        "Muito obrigada pela atenção",
        "Nós que agradecemos! Pode ficar tranquila, está tudo certo com o seu pedido.",
        "agradecimento",
    ),
    (
        "Muito obrigada pela atenção",
        "Nós que agradecemos! Seu pedido está garantido, qualquer dúvida é só chamar.",
        "agradecimento",
    ),
    (
        "Esse celular tem NFC?",
        "Sim, o A17 Pro Max tem NFC. Seu pedido está confirmado e segue normalmente.",
        "espec_celular",
    ),
    (
        "Esse celular tem NFC?",
        "Sim, tem NFC. Se preferir, temos outra cor disponível no mesmo anúncio.",
        "espec_celular",
    ),
    (
        "Boa tarde",
        "Boa tarde! A cor Azul acabou no estoque. Você aceita a Prata no lugar?",
        "saudacao_ou_vaga",
    ),
]


@pytest.mark.parametrize("situacao", [None, "Aguardando Cancelamento"])
@pytest.mark.parametrize(("fala", "texto", "assunto"), AG_ESTADO_OU_TROCA)
async def test_rev_10_o_ag_cancelamento_nao_confirma_o_pedido_nem_oferece_troca(
    fala, texto, assunto, situacao
):
    bloco = _bloco() if situacao is None else _bloco(situacao=situacao)
    r = await _auto(_p(texto, assunto, ["pedido"]), [_menu(), *_cli(fala)], ctx_pedido=_ctx_ag(),
                    bloco=bloco, postado=acao_pessoa.POSTADO_NAO, valores=VALORES)  # fmt: skip
    assert r.ctx.acoes_pos_envio == [constantes.ACAO_AG_CANCELAMENTO], fala
    assert checagens.AG_CANCELAMENTO_NA_RESPOSTA in r.codigos, (texto, r.codigos)
    assert r.decisao not in (VAI, VAI_COM_AVISO)


def test_rev_10_a_resposta_do_produto_e_o_cumprimento_continuam():
    for texto in (
        "Sim, o A17 Pro Max tem NFC.",
        "Nós que agradecemos! Qualquer dúvida, é só chamar.",
        "Boa tarde! Em que podemos ajudar?",
        "A bateria é de 5000 mAh, conforme o anúncio.",
        "Nós que agradecemos! Ficamos disponíveis para qualquer dúvida.",
    ):
        assert (
            checagens.conferir_ag_cancelamento_na_resposta(texto, acao="responder", etiqueta=True)
            == []
        ), texto


@pytest.mark.parametrize(
    "fala",
    [
        "Ainda tem o azul em estoque? Foi o que eu comprei",
        "Tem na cor preta?",
        "Posso trocar pela cor preta?",
        "A cor que eu comprei acabou?",
        "Vai ter reposição?",
    ],
)
async def test_rev_10_a_fala_do_estoque_ou_da_troca_no_83955_trava(fala):
    msgs = [_menu(), *_cli(fala)]
    lista = acao_pessoa.gatilhos(_conv(), msgs, _ctx_ag(), gatilho=msgs[-1],
                                 postado=acao_pessoa.POSTADO_NAO, chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    assert constantes.ACAO_AG_CANCELAMENTO in [g.codigo for g in lista]
    got = acao_pessoa.etiquetas_do_turno(_conv(), _ctx_ag(), msgs, msgs[-1], lista)
    assert constantes.ACAO_AG_CANCELAMENTO not in got, fala
    assert (
        constantes.HUMANO_AG_CANCELAMENTO
        not in _triar(msgs, _ctx_ag(), postado=acao_pessoa.POSTADO_NAO).marca
    )
    # A oferta de outra cor (o que o `sugerir_troca` daria) segura como antes do item 10.
    r = await _auto(_p("Infelizmente a cor Azul está em falta. Temos a Preta disponível no mesmo anúncio, você prefere essa?",
                       "estoque_variacao", ["pedido"]), msgs, ctx_pedido=_ctx_ag(), bloco=_bloco(),
                    postado=acao_pessoa.POSTADO_NAO, valores=VALORES)  # fmt: skip
    assert r.ctx.acoes_pos_envio == [] and ia.MOTIVO_AG_CANCELAMENTO in r.motivos
    assert r.decisao not in (VAI, VAI_COM_AVISO)


# Achado 3 (item 10): a ação que entra DEPOIS da régua do turno (o OUTRO pedido lido pela
# ferramenta, a ferramenta que marca) nunca vira etiqueta: trava, como antes.
OUTRO_ENTREGUE = {
    "pedido": {"numero": "90009", "numeroloja": "261010CCCC0009", "situacao": "Entregue",
               "itens": [{"descricao": "Fone sem fio", "quantidade": 1}]},
    "logistica": {"status": "Entregue", "data_envio": "2026-10-12"},
    "chamados": [], "devolucoes": [],
}  # fmt: skip
OUTRO_TEXTO = (
    "Sentimos muito! Confira com a portaria ou com os vizinhos; se não encontrar, abra a "
    "solicitação de não recebimento em Minhas Compras > o pedido."
)


async def _conferir_com_outro(p: Proposta, msgs: list, *, pedidos_lidos: list) -> tuple:
    """O `agente.conferir_proposta` de verdade (sem banco, sem modelo), com os outros pedidos
    que a ferramenta leu (`ctx.pedidos_lidos`)."""
    conversa = _conv()
    gatilho = msgs[-1]
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    base = agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=rajada, foco=foco, textos_cliente=[x.texto or "" for x in (foco or rajada)],
        textos_rajada=[x.texto or "" for x in rajada], ctx=CTX_POSTADO, valores=dict(VALORES),
        fatos={"anuncio": None}, fatos_gravados={}, categorias=_categorias_do_manual(), regras=[],
        sinais=[], motivos=[], opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(),
        modo=constantes.MODO_AUTO, forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip
    lista = acao_pessoa.gatilhos(conversa, msgs, CTX_POSTADO, gatilho=gatilho, postado=acao_pessoa.POSTADO_SIM,
                                 chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, gatilho, modo=constantes.MODO_AUTO)
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.etiquetas_pos_envio = acao_pessoa.etiquetas_do_turno(
        conversa, CTX_POSTADO, msgs, gatilho, lista
    )
    ctx.pistas_orientacao = acao_pessoa.pistas_de_orientacao(msgs, gatilho)
    ctx.pistas_turno = acao_pessoa.pistas_do_turno(msgs, gatilho, com_pedido=True)
    bloco = _bloco()
    ctx.permitir(ids=[agente_ctx.ID_PEDIDO], textos=[json.dumps(bloco, ensure_ascii=False)])
    ctx.pedidos_lidos = list(pedidos_lidos)
    antes = ctx.etiquetas_pos_envio
    pac = SimpleNamespace(fatos={"anuncio": None, "agente_pedido": bloco}, fichas=[], pedido=bloco)
    c = await agente_svc.conferir_proposta(
        None, conversa, gatilho, base, ctx, p, pac, forcar_conferencia=True
    )
    return c, ctx, antes


@pytest.mark.parametrize(
    "p",
    [
        _p(OUTRO_TEXTO, "rastreio", ["pedido"]),
        # O modelo também escolheu o chamado: sem a etiqueta, continua "pessoa".
        Proposta(acao="pessoa", texto=OUTRO_TEXTO, assunto="rastreio", confianca=0.85,
                 acao_pessoa="abrir_chamado", motivo_pessoa="consta entregue", evidencias=["pedido"]),
    ],
)  # fmt: skip
async def test_rev_10_o_outro_pedido_entregue_e_nao_recebido_trava(p):
    msgs = [_menu(), *_cli("Não recebi o meu outro pedido, o do fone")]
    c, ctx, antes = await _conferir_com_outro(
        p, msgs, pedidos_lidos=[(OUTRO_ENTREGUE, acao_pessoa.POSTADO_SIM)]
    )
    # A régua do turno (só a conversa) tinha liberado o chamado; o do OUTRO pedido o tira.
    assert constantes.ACAO_ABRIR_CHAMADO in antes
    assert constantes.ACAO_ABRIR_CHAMADO in ctx.acoes_codigo
    assert constantes.ACAO_ABRIR_CHAMADO not in ctx.etiquetas_pos_envio
    assert constantes.ACAO_ABRIR_CHAMADO in ctx.acoes_pessoa and ctx.acoes_pos_envio == []
    assert c.final is not None and c.final.precisa_humano


async def test_rev_10_sem_o_outro_pedido_o_chamado_do_modelo_continua_etiqueta():
    """O controle: o mesmo turno sem o outro pedido lido — o chamado que só o modelo escolheu
    (o atraso, item 10) continua etiqueta."""
    msgs = [_menu(), *_cli("A entrega passou da data e não chegou")]
    p = Proposta(acao="pessoa", texto=TEMPLATE, assunto="rastreio", confianca=0.85,
                 acao_pessoa="abrir_chamado", motivo_pessoa="previsão vencida", evidencias=["pedido"])  # fmt: skip
    _c, ctx, _antes = await _conferir_com_outro(p, msgs, pedidos_lidos=[])
    assert ctx.acoes_pos_envio == [constantes.ACAO_ABRIR_CHAMADO] and ctx.acoes_pessoa == []


def test_rev_10_o_regatilho_tira_a_etiqueta_da_acao_de_depois_do_modelo():
    # O "não recebi" do outro pedido gravado no eval (`decisao.acoes_codigo`) que a régua da
    # conversa não dá: trava no re-gatilho também.
    def _montar(
        acoes: list, fala: str, ferramentas_: list | None = None, pacote: str = PACOTE_REGATILHO
    ):
        r = _registro_regatilho(
            "Confira com a portaria.", decisao={"acoes_codigo": acoes},
            rastro={"pacote": pacote, "transcricao": [f"[26/10 09:59] Cliente: {fala}"],
                    "ferramentas": ferramentas_ or []},
        )  # fmt: skip
        return regatilho.montar(r).ctx

    fala = "Não recebi o meu outro pedido, o do fone"
    assert constantes.ACAO_ABRIR_CHAMADO in _montar([], fala).etiquetas_pos_envio
    ctx = _montar([constantes.ACAO_ABRIR_CHAMADO], fala)
    assert constantes.ACAO_ABRIR_CHAMADO not in ctx.etiquetas_pos_envio
    assert ctx.acoes_pessoa == [constantes.ACAO_ABRIR_CHAMADO]
    # O `sugerir_troca` com a opção gravado no rastro: o Ag. cancelamento trava.
    ag = PACOTE_REGATILHO.replace(
        json.dumps({**SAIU, "numero": "{numero_pedido}", "itens": []}, ensure_ascii=False),
        json.dumps({**SAIU, "numero": "{numero_pedido}", "itens": [], "situacao": "Aguardando Cancelamento",
                    "postado": "nao", "ag_cancelamento": {"codigo": "sem_estoque"}}, ensure_ascii=False),
    )  # fmt: skip
    assert ag != PACOTE_REGATILHO
    assert (
        constantes.ACAO_AG_CANCELAMENTO in _montar([], "Boa tarde", pacote=ag).etiquetas_pos_envio
    )
    troca = {"ferramenta": "sugerir_troca", "args": {"numero": None},
             "resultado": '<dados fonte="sugerir_troca">' + json.dumps(
                 {"pedido": "{numero_pedido}", "opcoes": [{"nome": "X", "nivel": 1, "mesmo_produto": False}],
                  "evidencias": ["troca:{numero_pedido}"]}) + "</dados>"}  # fmt: skip
    ctx2 = _montar([], "Boa tarde", [troca], pacote=ag)
    assert constantes.ACAO_AG_CANCELAMENTO not in ctx2.etiquetas_pos_envio
    assert constantes.ACAO_AG_CANCELAMENTO in ctx2.acoes_pessoa


# Achado 4 (item 9): o recibo que ficou para depois da pausa humana (a PESSOA da equipe pediu
# o vídeo) sai no reenvio do cron quando a pausa acaba e ninguém respondeu.
PEDE_VIDEO_EQUIPE = "Pode mandar um vídeo mostrando o defeito, por favor?"


async def _recibo_na_pausa(db, make_user) -> tuple:
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa_ia(db, make_user, pedido=PEDIDO_MKT)
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO, auto_categorias=SHOPEE_REAL.auto_categorias,
        auto_orientacao=SHOPEE_REAL.auto_orientacao, auto_agente=SHOPEE_REAL.auto_agente,
    )  # fmt: skip
    await _msg_ia(db, conversa, "O celular chegou e não carrega", ha=timedelta(minutes=30))
    pedido = await _msg_ia(
        db, conversa, PEDE_VIDEO_EQUIPE, autor="loja", origem=constantes.ORIGEM_EXTERNO,
        ha=timedelta(minutes=20),
    )  # fmt: skip
    await _msg_ia(db, conversa, None, tipo="video", ha=timedelta(minutes=5))
    await db.refresh(conversa)
    r = await ia.gerar_rascunho(db, conversa)
    assert r is not None and ia.recibo_no_rascunho(r)
    return conversa, pedido, r


async def _a_pausa_acabou(db, conversa, pedido, r) -> None:
    """A mensagem da pessoa ficou há 45 min (a pausa de 30 min acabou) e o rascunho, há 4."""
    await db.execute(
        update(AtendimentoMensagem).where(AtendimentoMensagem.id == pedido.id).values(
            enviada_em=datetime.now(UTC) - timedelta(minutes=45),
            created_at=datetime.now(UTC) - timedelta(minutes=45),
        )
    )  # fmt: skip
    await db.execute(
        update(AtendimentoRascunho).where(AtendimentoRascunho.id == r.id).values(
            created_at=datetime.now(UTC) - timedelta(minutes=4)
        )
    )  # fmt: skip
    await db.commit()
    assert await ia._pausa_humana_ate(db, conversa) is None


async def test_rev_9_o_recibo_da_pausa_humana_sai_no_reenvio(
    db, make_user, agente_ligado, api, envios
):
    api.roteiro = []
    conversa, pedido, r = await _recibo_na_pausa(db, make_user)
    # Na pausa: nem o envio na hora, nem o reenvio.
    assert _decisao(r) == "pausa_humana" and envios == []
    assert await ia.reenviar_pendentes(db) == 0 and envios == []
    await _a_pausa_acabou(db, conversa, pedido, r)
    for _ in range(2):  # a rodada inteira do cron, duas vezes: sai uma vez só
        await ia.gerar_pendentes(db)
    await db.refresh(r)
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.fatos
    assert api.clientes == []  # sem modelo nem verificador


async def test_rev_9_o_recibo_nao_sai_se_a_pessoa_respondeu(
    db, make_user, agente_ligado, api, envios
):
    api.roteiro = []
    conversa, pedido, r = await _recibo_na_pausa(db, make_user)
    await _a_pausa_acabou(db, conversa, pedido, r)
    # A equipe respondeu depois do vídeo: a conversa não espera mais resposta.
    await _msg_ia(db, conversa, "Recebemos, vamos analisar.", autor="loja",
                  origem=constantes.ORIGEM_EXTERNO, ha=timedelta(minutes=1))  # fmt: skip
    await db.refresh(conversa)
    assert await ia.reenviar_pendentes(db) == 0 and envios == []
