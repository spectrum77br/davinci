# ruff: noqa: F811, E501 — fixtures importadas
"""A paridade do A/B do an0311 (03/11/2026): o replay (`agente.replay`) e o re-gatilho
(`scripts.agente_eval.regatilho`) medem o que a produção de wip/0311-integra decidiria — o recibo
da evidência, o cartão sozinho, o corte da frase sem lastro, O QUE O ATENDIMENTO JÁ ENVIOU, as
etiquetas do turno gravadas, o teto do verificador que não corta calado e a NF sozinha à parte.
API falsa (httpx MockTransport dos testes); sem banco de produção.
"""

from __future__ import annotations

import json

import pytest

from app.services.atendimento import agente as agente_svc
from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import acao_pessoa, replay
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from scripts.agente_eval import regatilho
from tests.test_atendimento_agente_0311_holds import _anexo, _evidencia, _ia
from tests.test_atendimento_agente_decisoes_dono import (
    AGORA,
    CTX_POSTADO,
    TEMPLATE,
    _bloco,
    _cli,
    _conv,
    _menu,
    _retrato,
)
from tests.test_atendimento_agente_laco import _categorias_do_manual
from tests.test_atendimento_agente_portao import PACOTE, _registro
from tests.test_atendimento_agente_verificador import (  # noqa: F401 — fixtures
    Api,
    _cliente,
    _ok,
    teto_limpo,
)

CANAL = {
    "modo": "auto",
    "auto_categorias": ["rastreio", "defeito", "garantia", "saudacao_ou_vaga"],
    "auto_agente": ["rastreio", "defeito", "garantia", "saudacao_ou_vaga"],
    "auto_orientacao": [],
}
TEXTO_CORTE = (
    "O seu pedido {numero_pedido} já foi postado; acompanhe em Minhas Compras. "
    "Ele foi embrulhado com papel de presente."
)
RECIBO_FIXO = "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui."
SEM_LASTRO = {
    "afirma_sem_lastro": ["Ele foi embrulhado com papel de presente"],
    "ok_para_envio_automatico": False,
}


def _base(msgs, gatilho, ctx):
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    return agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=rajada, foco=foco,
        textos_cliente=[m.texto for m in rajada if m.texto],
        textos_rajada=[m.texto or "" for m in (foco or rajada)], ctx=ctx, valores={},
        fatos={"anuncio": None}, fatos_gravados={}, categorias=_categorias_do_manual(),
        regras=[], sinais=[], motivos=[], opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(),
        modo=constantes.MODO_AUTO, forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip


@pytest.fixture
def sem_agente(monkeypatch):
    async def _nao(*a, **k):
        raise AssertionError("o agente não deveria rodar")

    monkeypatch.setattr(agente_svc, "responder", _nao)


EXTRA = {"as_of": AGORA, "modo_replay": True, "sem_ida_a_loja": True}


# ── O recibo da evidência (item 9) ────────────────────────────────────────────


async def test_o_recibo_sai_no_replay_e_conta_como_sozinho_no_regatilho(sem_agente):
    msgs = _evidencia(_anexo("video"))
    gat, conv = msgs[-1], _conv()
    r = await replay.como_o_gerar(None, conv, gat, _base(msgs, gat, CTX_POSTADO), EXTRA)
    assert r.texto in acao_pessoa.TEXTOS_DO_RECIBO and r.fatos_agente.get(ia.FATO_RECIBO)
    x = await regatilho.regatilhar(
        {"id": "f1", "parada": r.parada, "proposta": {}, "texto_final": r.texto,
         "decisao": {"recibo": True}},
        CANAL,
    )  # fmt: skip
    assert x["decisao"] == regatilho.RECIBO and x["rascunho"] == r.texto
    # Sem a marca do rodar (o braço de antes), ou com outro texto: o de sempre (pessoa).
    y = await regatilho.regatilhar(
        {"id": "f1b", "parada": r.parada, "proposta": {}, "texto_final": "outro",
         "decisao": {"recibo": True}},
        CANAL,
    )  # fmt: skip
    assert y["decisao"] == "pessoa:parada_sem_agente"


async def test_depois_do_recibo_o_replay_vai_para_pessoa_sem_o_agente(sem_agente):
    msgs = _evidencia(_anexo("video"), _ia(RECIBO_FIXO), *_cli("segue o vídeo, descarrega sozinha"))
    gat = msgs[-1]
    r = await replay.como_o_gerar(None, _conv(), gat, _base(msgs, gat, CTX_POSTADO), EXTRA)
    assert r.parada == agente_svc.PARADA_SEM_AGENTE and not r.texto
    assert r.acoes_pessoa == [constantes.ACAO_ANALISAR_EVIDENCIA]


# ── O cartão sozinho (item 1) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("abre", "esperado"), [(True, regatilho.DESCARTADO), (False, "pessoa:cartao_sem_texto")]
)
async def test_o_cartao_sozinho_nao_conta_como_sozinho(abre, esperado):
    x = await regatilho.regatilhar(
        {"id": "f2", "pulado": replay.PULADO_ATALHO, "atalho": "saudacao_ou_vaga",
         "parada": None, "texto_final": ia.TEXTO_CARTAO_APROVADO,
         "decisao": {"atalho": "saudacao_ou_vaga", "cartao_sem_texto": True,
                     "cartao_abre_visita": abre}},
        CANAL,
    )  # fmt: skip
    assert x["decisao"] == esperado


# ── O corte da frase sem lastro (v17) ─────────────────────────────────────────


async def test_o_corte_no_regatilho(teto_limpo):
    falsa = Api()
    falsa.veredito = [_ok(**SEM_LASTRO), _ok()]
    async with _cliente(falsa) as cliente:
        x = await regatilho.regatilhar(_registro(TEXTO_CORTE), CANAL, cliente=cliente)
    assert x["decisao"] == regatilho.AUTO, x
    assert len(falsa.pedidos) == 2 and x["corte_sem_lastro"]["saiu"] is True
    assert "papel de presente" not in x["texto_enviado"]
    assert x["custo_usd"] > x["verificador"]["custo_usd"]  # os dois pedidos contam
    # O 2º "não": fica o de antes, para pessoa (e os dois pedidos pagos).
    falsa2 = Api()
    falsa2.veredito = [
        _ok(**SEM_LASTRO),
        _ok(responde_o_que_foi_perguntado=False, ok_para_envio_automatico=False),
    ]
    async with _cliente(falsa2) as cliente:
        y = await regatilho.regatilhar(_registro(TEXTO_CORTE), CANAL, cliente=cliente)
    assert y["decisao"] == "pessoa:verificador" and len(falsa2.pedidos) == 2
    assert y["corte_sem_lastro"]["saiu"] is False and "texto_enviado" not in y


async def test_o_corte_no_regatilho_mantem_a_etiqueta_do_modelo(teto_limpo):
    """Como o `responder` (revisão cruzada): o resto parte da Proposta do MODELO — o Abrir
    chamado (entrega) que ele escolheu continua (sem isso, o resto não sairia)."""
    r = _registro(TEXTO_CORTE)
    r["proposta"] = {**r["proposta"], "acao": "pessoa", "acao_pessoa": "abrir_chamado",
                     "motivo_pessoa": "a entrega passou da previsão"}  # fmt: skip
    r["decisao"] = {"acoes_codigo": [], "etiquetas_do_turno": [constantes.ACAO_ABRIR_CHAMADO]}
    falsa = Api()
    falsa.veredito = [_ok(**SEM_LASTRO), _ok()]
    async with _cliente(falsa) as cliente:
        x = await regatilho.regatilhar(r, CANAL, cliente=cliente)
    assert x["decisao"] == regatilho.AUTO and x["corte_sem_lastro"]["saiu"] is True, x
    assert len(falsa.pedidos) == 2


# ── O QUE O ATENDIMENTO JÁ ENVIOU (v17) ───────────────────────────────────────


async def test_o_ja_enviado_do_rastro_vai_ao_verificador(teto_limpo):
    linha = "[26/10 09:40] O de 6,9 polegadas é o A18 Pro Max."
    r = _registro(
        "O seu pedido {numero_pedido} já foi postado; acompanhe em Minhas Compras.",
        rastro={"pacote": PACOTE, "transcricao": ["[26/10 09:59] Cliente: cadê meu pedido?"],
                "ja_enviado": [linha]},
    )  # fmt: skip
    falsa = Api()
    falsa.veredito = [_ok()]
    async with _cliente(falsa) as cliente:
        x = await regatilho.regatilhar(r, CANAL, cliente=cliente)
    assert x["decisao"] == regatilho.AUTO
    conteudo = falsa.pedidos[0]["messages"][0]["content"]
    assert v.CABECALHO_JA_ENVIADO in conteudo and linha in conteudo
    # O rastro de antes (sem a chave): como era, sem a seção.
    falsa2 = Api()
    falsa2.veredito = [_ok()]
    async with _cliente(falsa2) as cliente:
        await regatilho.regatilhar(_registro(r["texto_final"]), CANAL, cliente=cliente)
    assert v.CABECALHO_JA_ENVIADO not in falsa2.pedidos[0]["messages"][0]["content"]


# ── As etiquetas do turno: as da produção, não a régua reconstruída ───────────


def _pacote_com(bloco):
    return "\n".join([
        "<dados>",
        'CABEÇALHO: {"agora_sao_paulo":"22/10/2026 15:00","canal":"chat","plataforma":"Shopee"}',
        "PEDIDO DA CONVERSA: " + json.dumps(bloco, ensure_ascii=False),
        'EVIDÊNCIAS DESTES DADOS (ids): ["pedido"]',
        "</dados>",
    ])  # fmt: skip


def test_as_etiquetas_gravadas_valem_no_regatilho():
    """O retrato da Shopee (4 dias sem evento) dá o chamado como etiqueta na produção; o bloco
    sem a data de envio, sem o retrato, não o reconstrói — a etiqueta gravada vale."""
    fala = "cadê meu pedido? não chegou ainda"
    msgs = [_menu(), *_cli(fala)]
    conv = _conv(dados=_retrato(4))
    lista = acao_pessoa.gatilhos(conv, msgs, CTX_POSTADO, gatilho=msgs[-1], postado=acao_pessoa.POSTADO_SIM,
                                 chaves_ativas=frozenset(), agora=AGORA)  # fmt: skip
    prod = [g.codigo for g in lista]
    etiquetas = acao_pessoa.etiquetas_do_turno(conv, CTX_POSTADO, msgs, msgs[-1], lista)
    assert prod == [constantes.ACAO_ABRIR_CHAMADO] and etiquetas == {constantes.ACAO_ABRIR_CHAMADO}
    rastro = {"pacote": _pacote_com({**_bloco(), "data_envio": None}),
              "transcricao": [f"[22/10 14:59] Cliente: {fala}"]}  # fmt: skip
    gravada = _registro(TEMPLATE, decisao={"acoes_codigo": prod, "etiquetas_do_turno": sorted(etiquetas)},
                        rastro=rastro)  # fmt: skip
    ctx = regatilho.montar(gravada).ctx
    assert ctx.etiquetas_pos_envio == {constantes.ACAO_ABRIR_CHAMADO} and ctx.acoes_pessoa == []
    # O registro de antes (sem a chave): a régua reconstruída, como era (o chamado trava).
    antiga = _registro(TEMPLATE, decisao={"acoes_codigo": prod}, rastro=rastro)
    assert regatilho.montar(antiga).ctx.acoes_pessoa == [constantes.ACAO_ABRIR_CHAMADO]


def test_o_contexto_grava_as_etiquetas_do_turno_so_quando_ha():
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), _cli("oi")[0], modo=constantes.MODO_AUTO)
    assert "etiquetas_do_turno" not in ctx.para_fatos()
    ctx.etiquetas_pos_envio = frozenset({constantes.ACAO_ABRIR_CHAMADO})
    assert ctx.para_fatos()["etiquetas_do_turno"] == [constantes.ACAO_ABRIR_CHAMADO]


# ── O teto do verificador não corta calado ────────────────────────────────────


async def test_o_teto_do_verificador_marca_o_que_ficou_sem_verificar(teto_limpo):
    registros = [{**_registro(TEMPLATE), "id": f"r{i}"} for i in range(3)]
    falsa = Api()
    falsa.veredito = [_ok(), _ok(), _ok()]
    async with _cliente(falsa) as cliente:
        linhas = await regatilho.regatilhar_todos(
            registros, lambda r: CANAL, cliente=cliente, teto_usd=1e-9
        )
    # O 1º passa (o gasto começa em 0); os outros ficam sem o verificador, marcados.
    assert [x["decisao"] for x in linhas] == [regatilho.AUTO, regatilho.CHEGA, regatilho.CHEGA]
    assert [x.get("teto") for x in linhas] == [None, True, True] and len(falsa.pedidos) == 1
    res = regatilho.resumo(linhas)
    assert res["nao_verificados_pelo_teto"] == 2
    assert regatilho.codigo_de_saida(res) == regatilho.SAIDA_TETO
    # Sem o --verificar (sem cliente), o "chega ao verificador" é o resultado, não o teto.
    sem = await regatilho.regatilhar_todos(registros, lambda r: CANAL)
    assert all(x.get("teto") is None for x in sem)
    assert regatilho.codigo_de_saida(regatilho.resumo(sem)) == 0


# ── A NF sozinha: a coluna à parte, fora do "sozinho" ─────────────────────────


@pytest.mark.parametrize(
    ("fala", "claro"), [("me manda a nota fiscal do pedido por favor", True),
                        ("não veio a nota fiscal nem a caixa", False)],
)  # fmt: skip
async def test_a_nf_sozinha_provavel_fica_a_parte(fala, claro):
    r = _registro(
        "Vou separar a nota fiscal do seu pedido.",
        decisao={"acoes_codigo": [constantes.ACAO_ENVIAR_ARQUIVO_NF]},
        rastro={"pacote": PACOTE, "transcricao": [f"[26/10 09:59] Cliente: {fala}"]},
    )
    r["proposta"] = {**r["proposta"], "assunto": "nota_fiscal"}
    x = await regatilho.regatilhar(r, CANAL)
    assert x["decisao"].startswith("pessoa:") and x["nf_auto_provavel"] is claro
    assert regatilho.resumo([x])["nf_auto_provavel"] == int(claro)
