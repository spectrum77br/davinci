# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos
"""Revisão cruzada do an0311 sobre wip/0311-integra (03/11/2026): o que as 4 frentes juntas
deixavam sair errado (sem chamada a LLM, à loja ou à produção; paráfrases, sem dado de cliente).

1. A etiqueta continua até a PESSOA agir: o Abrir chamado (entrega) da marca no turno seguinte
   (`acao_pessoa.etiquetas_que_continuam`) e o turno depois do recibo da evidência
   (`acao_pessoa.depois_do_recibo`) — o "Ok, obrigado" e a legenda atrasada não apagam mais.
3. A fala atrasada que é só reconhecimento/presença não reabre a vez (`gravar.
   fala_que_a_resposta_cobre`): o "Falta responder" fica igual ao Duoke.
4. A política geral da garantia que o agente explicou não é a "decisão" que o "que absurdo"
   do atraso contesta (`acao_pessoa.contesta_a_decisao_da_loja`).
(O 2 — o corte da frase sem lastro que perdia a etiqueta do modelo — está em
`test_atendimento_agente_0311_cruzada_corte.py`, com a API falsa do verificador.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as _db
from app.models import AtendimentoConversa
from app.services.atendimento import constantes, gravar, humano, ia, pendente
from app.services.atendimento.agente import acao_pessoa
from tests.test_atendimento_agente_0311_holds import (
    PEDE_VIDEO,
    RECUSA_DA_EQUIPE,
    _anexo,
    _em_ordem,
    _ia,
    _triar,
)
from tests.test_atendimento_agente_decisoes_dono import (
    AGORA,
    CTX_POSTADO,
    PAR_PRAZO,
    TEMPLATE,
    VALORES,
    _auto,
    _bloco,
    _cli,
    _conv,
    _menu,
    _p,
    _retrato,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
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
from tests.test_atendimento_agente_redondo import SHOPEE_REAL, VAI_AO_VERIFICADOR
from tests.test_atendimento_ia import PEDIDO_MKT, _importar_manual_base, _pedido_completo
from tests.test_atendimento_ia import _conversa as _conversa_ia
from tests.test_atendimento_ia import _msg as _msg_ia

VAI = VAI_AO_VERIFICADOR
RECIBO = "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui."
ENVIADO = TEMPLATE.replace("{data_envio}", VALORES["data_envio"])
OBRIGADO = "Nós que agradecemos! Qualquer dúvida, é só chamar."


def test_a_chave_da_marca_e_a_mesma():
    assert acao_pessoa.CHAVE_POS_ENVIO == humano.CHAVE_POS_ENVIO


# ═══════════════ 1A: o Abrir chamado (entrega) atravessa o "Ok, obrigado" ═══════════════


def _turnos(*depois: str) -> list:
    """O rastreio parado (63678840/84f6468f, paráfrase), a resposta do agente que saiu com a
    etiqueta e o turno seguinte do cliente."""
    t1 = [_menu(), *_cli("Meu pedido está sem atualização desde quarta")]
    return [*t1, _ia(ENVIADO), *_cli(*depois)]


def _marcada(msgs: list, **campos) -> SimpleNamespace:
    """A conversa com a marca que o envio do turno 1 gravou (`humano.marcar_pos_envio`)."""
    turno = next(m for m in msgs if m.autor == "cliente" and m.texto and "quarta" in m.texto)
    resposta = next(m for m in msgs if m.texto == ENVIADO)
    marca = {
        "turno": turno.enviada_em.timestamp(),
        "ate": (resposta.enviada_em + humano.FOLGA_POS_ENVIO).timestamp(),
        "acoes": [constantes.ACAO_ABRIR_CHAMADO],
        "rascunho": "x",
    }
    base = {
        "dados": {**_retrato(1), humano.CHAVE_POS_ENVIO: marca},
        "ultima_da_loja_em": resposta.enviada_em,
        "ultima_do_cliente_em": msgs[-1].enviada_em,
        "sem_resposta_necessaria": False,
    }
    return _conv(**{**base, **campos})


@pytest.mark.parametrize(
    "falas", [["Ok, obrigado"], ["obrigada, fico no aguardo"], ["e a nota fiscal vem junto?"]]
)
async def test_1a_a_etiqueta_do_chamado_continua_no_turno_seguinte(falas):
    msgs = _turnos(*falas)
    conv = _marcada(msgs)
    assert acao_pessoa.etiquetas_que_continuam(conv, msgs[-1]) == {constantes.ACAO_ABRIR_CHAMADO}
    # A Caixa Humano: a conversa continua com o rótulo, como etiqueta (a marca a cobre).
    t = _triar(msgs, CTX_POSTADO, conversa=conv)
    assert t.motivos == (constantes.HUMANO_ACAO_PESSOA,)
    assert constantes.ACAO_ABRIR_CHAMADO in t.assuntos and t.marca == t.motivos
    # O agente: a resposta do turno seguinte leva a etiqueta (e o envio grava a marca de novo).
    r = await _auto(_p(OBRIGADO, "agradecimento", []), msgs, ctx_pedido=CTX_POSTADO,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    conversa=conv)  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO in r.ctx.acoes_codigo
    assert r.ctx.acoes_pos_envio == [constantes.ACAO_ABRIR_CHAMADO] and r.ctx.acoes_pessoa == []
    agente = r.rascunho.fatos["agente"]
    assert agente["etiquetas_pos_envio"] == [constantes.ACAO_ABRIR_CHAMADO]
    assert ia.responde_e_marca_no_rascunho(r.rascunho)


def test_1a_sem_a_marca_o_turno_seguinte_e_como_antes():
    msgs = _turnos("Ok, obrigado")
    conv = _conv(dados=_retrato(1))
    assert acao_pessoa.etiquetas_que_continuam(conv, msgs[-1]) == frozenset()
    assert _triar(msgs, CTX_POSTADO, conversa=conv).motivos == ()


@pytest.mark.parametrize(
    ("campos", "ctx"),
    [
        # Uma pessoa da loja respondeu depois da marca: ela agiu, a etiqueta sai.
        ({"ultima_da_loja_em": AGORA + timedelta(hours=1)}, CTX_POSTADO),
        # Marcada "não precisa responder".
        ({"sem_resposta_necessaria": True}, CTX_POSTADO),
        # O chamado já está no pedido.
        ({}, {**CTX_POSTADO, "chamados": [{"status": "aberto"}]}),
    ],
)
def test_1a_a_etiqueta_sai_quando_a_pessoa_age(campos, ctx):
    msgs = _turnos("Ok, obrigado")
    conv = _marcada(msgs, **campos)
    lista = acao_pessoa.gatilhos(conv, msgs, ctx, gatilho=msgs[-1], postado=acao_pessoa.POSTADO_SIM,
                                 chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO not in [g.codigo for g in lista]


def test_1a_o_mesmo_turno_continua_com_a_marca_de_sempre():
    """O turno marcado não ganha o gatilho de novo (é o `pos_envio_vale`, que já existia)."""
    msgs = _turnos("Ok, obrigado")
    conv = _marcada(msgs)
    marca = conv.dados[humano.CHAVE_POS_ENVIO]
    mesmo = next(m for m in msgs if m.texto and "quarta" in m.texto)
    assert mesmo.enviada_em.timestamp() == marca["turno"]
    assert acao_pessoa.etiquetas_que_continuam(conv, mesmo) == frozenset()


@pytest.mark.parametrize(
    "falas",
    [
        # O dinheiro ou o cancelamento pedido pelo atraso: trava (a régua do turno).
        ["então quero o reembolso, cancela"],
        # O extravio dito agora: a trava do extravio.
        ["acho que foi extraviado, sumiu"],
    ],
)
async def test_1a_no_turno_seguinte_a_regua_da_etiqueta_vale(falas):
    msgs = _turnos(*falas)
    conv = _marcada(msgs)
    r = await _auto(_p(OBRIGADO, "rastreio", ["pedido"]), msgs, ctx_pedido=CTX_POSTADO,
                    bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
                    pares_do_pacote=[PAR_PRAZO], conversa=conv)  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO in r.ctx.acoes_pessoa, (falas, r.ctx.acoes_codigo)
    assert r.decisao != VAI


# ═══════════════ 1B: depois do recibo da evidência, o turno é da pessoa ═══════════════


def _depois_do_recibo(*depois) -> list:
    return _em_ordem([*_cli("O celular chegou ontem e a bateria zera em minutos"),
                      _ia(PEDE_VIDEO), _anexo("video"), _ia(RECIBO), *depois])  # fmt: skip


@pytest.mark.parametrize(
    "depois",
    [
        # A legenda que o sync trouxe atrasada (reordenada para depois do recibo).
        _cli("segue o vídeo, a bateria descarrega sozinha"),
        _cli("Ok, obrigado, fico no aguardo"),
        _cli("e quanto tempo demora a análise?"),
    ],
)
async def test_1b_depois_do_recibo_o_turno_e_da_pessoa(depois):
    msgs = _depois_do_recibo(*depois)
    assert acao_pessoa.depois_do_recibo(msgs, msgs[-1])
    antes = acao_pessoa.gatilhos_antes_do_modelo(msgs, msgs[-1])
    assert [g.codigo for g in antes.gatilhos] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    assert acao_pessoa.recibo_da_evidencia(_conv(), CTX_POSTADO, msgs, msgs[-1]) is None
    t = _triar(msgs, CTX_POSTADO)
    assert t.motivos == (constantes.HUMANO_ACAO_PESSOA,) and t.prioridade and t.marca == ()
    # Pedir o vídeo de novo nunca sai (nem nada do agente: a pessoa analisa).
    r = await _auto(_p("Sinto muito! Poderia nos enviar um vídeo curto mostrando a bateria, por favor?",
                       "defeito", []), msgs, ctx_pedido=CTX_POSTADO, bloco=_bloco(),
                    postado=acao_pessoa.POSTADO_SIM, valores=VALORES)  # fmt: skip
    assert r.decisao != VAI and constantes.ACAO_ANALISAR_EVIDENCIA in r.ctx.acoes_pessoa


def test_1b_a_pessoa_respondeu_depois_do_recibo():
    msgs = _depois_do_recibo(
        _ia("Vimos o vídeo: é defeito, pode abrir a devolução pela Shopee."),
        *_cli("Ok, obrigado"),
    )
    msgs[-2].origem = "externo"  # a resposta da pessoa (Duoke)
    assert not acao_pessoa.depois_do_recibo(msgs, msgs[-1])
    assert acao_pessoa.gatilhos_antes_do_modelo(msgs, msgs[-1]).gatilhos == ()


async def test_1b_e2e_a_legenda_atrasada_depois_do_recibo_fica_com_a_pessoa(
    db, make_user, agente_ligado, api, envios
):
    """P2c da revisão cruzada: o recibo sai; a legenda que o cliente mandou com o vídeo (4 min
    antes, pelo relógio da plataforma) chega atrasada e vai para depois do recibo — a conversa
    continua no topo da Caixa Humano com "Analisar foto ou vídeo", e o agente não roda."""
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
    api.roteiro = []
    r = await ia.gerar_rascunho(db, conversa)
    assert r.texto == RECIBO and _decisao(r) == "enviado"
    agora = datetime.now(UTC)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="ia-recibo", autor="loja", texto=r.texto,
        enviada_em=agora, origem=constantes.ORIGEM_IA,
    )  # fmt: skip
    await db.commit()
    m, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="legenda", autor="cliente",
        texto="segue o vídeo, a bateria descarrega sozinha", enviada_em=agora - timedelta(minutes=4),
    )  # fmt: skip
    await db.commit()
    await db.refresh(conversa)
    await db.refresh(m)
    assert m.enviada_em > agora and conversa.aguardando_resposta  # a reordenação (saudação)
    await humano.recalcular(orcamento_s=None)
    dados = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == conversa.id)
    )
    assert dados["humano"]["motivos"] == [constantes.HUMANO_ACAO_PESSOA]
    assert dados["humano"]["assuntos"] == [constantes.ACAO_ANALISAR_EVIDENCIA]
    assert dados["humano"]["prioridade"] is True
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
    # O turno da legenda: sem o agente (sem custo) e sem nada que saia sozinho.
    antes = len(envios)
    r2 = await ia.gerar_rascunho(db, conversa)
    assert len(envios) == antes and api.clientes == []
    assert r2 is not None and r2.precisa_humano and not r2.texto


# ═══════════════ 3: a fala atrasada que só agradece não reabre a vez ═══════════════


@pytest.mark.parametrize(
    ("texto", "cobre"),
    [
        ("ok obrigado", True), ("?", True), ("alguém?", True), ("oi?", True),
        ("tem alguém aí?", True), ("fico no aguardo, obrigado!", True),
        ("tem na cor preta?", False), ("quero cancelar", False), ("6", False), ("tem?", False),
        ("e a nota?", False), (None, False),
    ],
)  # fmt: skip
def test_3_o_que_a_resposta_cobre(texto, cobre):
    assert gravar.fala_que_a_resposta_cobre(texto) is cobre


def test_3_anexo_e_cartao_nunca_sao_so_cortesia():
    assert not gravar.fala_que_a_resposta_cobre(None, tipo="video")
    assert not gravar.fala_que_a_resposta_cobre("ok", tipo="imagem")
    assert not gravar.fala_que_a_resposta_cobre("ok", anexos=[{"tipo": "image/jpeg"}])


@pytest.mark.parametrize(
    ("atrasada", "reabre"), [("ok obrigado", False), ("?", False), ("e vem com nota fiscal?", True)]
)
async def test_3_a_fala_atrasada_e_o_falta_responder(db, make_user, atrasada, reabre):
    conversa = await _conversa_ia(db, make_user)
    agora = datetime.now(UTC)
    await gravar.gravar_mensagem(db, conversa, externo_id="q", autor="cliente",
                                 texto="tem na cor preta?", enviada_em=agora - timedelta(minutes=5))  # fmt: skip
    await gravar.gravar_mensagem(db, conversa, externo_id="r", autor="loja", texto="Temos sim, na cor preta.",
                                 enviada_em=agora - timedelta(minutes=1), origem=constantes.ORIGEM_IA)  # fmt: skip
    await db.commit()
    # Escrita ANTES da resposta (o relógio da plataforma), entregue atrasada pelo sync.
    m, _ = await gravar.gravar_mensagem(db, conversa, externo_id="l", autor="cliente", texto=atrasada,
                                        enviada_em=agora - timedelta(minutes=2))  # fmt: skip
    await db.commit()
    await db.refresh(conversa)
    assert (gravar.CHAVE_FORA_DE_ORDEM in (m.payload or {})) is reabre
    assert conversa.aguardando_resposta is reabre
    assert pendente.falta_responder(conversa) is reabre


# ═══════════════ 4: a política geral da garantia não é a decisão contestada ═══════════════

POLITICA = (
    "Tem garantia, que cobre defeito de fabricação; não cobre aparelho quebrado, molhado ou com "
    "mau uso. A devolução é pela Shopee."
)


def _codigos(msgs: list) -> list[str]:
    return [g.codigo for g in acao_pessoa.gatilhos(_conv(), msgs, CTX_POSTADO, gatilho=msgs[-1],
            postado=acao_pessoa.POSTADO_SIM, chaves_ativas=frozenset(), agora=AGORA)]  # fmt: skip


def test_4_o_absurdo_do_atraso_depois_da_politica_nao_e_contestacao():
    msgs = _em_ordem([*_cli("Esse celular tem garantia?"), _ia(POLITICA),
                      *_cli("Que absurdo, comprei e meu pedido ainda nem saiu")])  # fmt: skip
    assert not acao_pessoa.contesta_a_decisao_da_loja(msgs, msgs[-1])
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA not in _codigos(msgs)


@pytest.mark.parametrize(
    "fala",
    [
        "Que absurdo, então a garantia não cobre nada?",
        "Não foi mau uso, a tela trincou sozinha",
        "Absurdo, quero a troca, isso é defeito",
    ],
)
def test_4_contestar_a_garantia_depois_da_politica_continua_com_pessoa(fala):
    msgs = _em_ordem([*_cli("Esse celular tem garantia?"), _ia(POLITICA), *_cli(fala)])
    assert acao_pessoa.contesta_a_decisao_da_loja(msgs, msgs[-1])
    assert constantes.ACAO_JURIDICO_FRAUDE_OFENSA in _codigos(msgs)


@pytest.mark.parametrize("texto", [RECUSA_DA_EQUIPE, POLITICA])
def test_4_a_decisao_da_pessoa_continua_contestavel_por_qualquer_fala(texto):
    """A recusa/a regra que a PESSOA da loja escreveu conta como antes: qualquer contestação."""
    msgs = _em_ordem([*_cli("A tela trincou"), _ia(texto), *_cli("Que absurdo")])
    msgs[1].origem = "externo"  # a resposta da pessoa (Duoke)
    assert acao_pessoa.contesta_a_decisao_da_loja(msgs, msgs[-1])
