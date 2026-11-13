"""O PORTÃO DO BANCO (13/11/2026): sai sozinho só com a resposta do banco que encaixa no caso.

O Eduardo: "bateu com a do banco, faz sentido, já manda". Com a régua por tipo ligada e o
agente lendo o banco, o rascunho só passa no portão quando o pacote mostrou ao menos uma
resposta da equipe do banco do MESMO tipo que ele declarou. No eval de 13/11 o agente citou
o banco em só 4 de 40 respostas — por isso o encaixe é conferido pelo código, não pela
citação. E-mail e WhatsApp nunca entram (o banco não os cobre).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.config import get_settings
from app.services.atendimento import constantes, duvida, ia
from app.services.atendimento.agente import contexto as agente_ctx


def _canal(**campos: Any) -> SimpleNamespace:
    base = {"auto_categorias": ["rastreio"], "auto_agente": ["rastreio"], "auto_orientacao": []}
    return SimpleNamespace(**{**base, **campos})


def _rascunho(categoria: str, agente: dict) -> SimpleNamespace:
    return SimpleNamespace(
        categoria=categoria,
        prompt_versao=constantes.PROMPT_VERSAO_AGENTE,
        fatos={"agente": {"modo": "auto", "acoes_pessoa": [], **agente}},
    )


def test_sem_a_marca_o_portao_nao_muda_nada():
    """Desligado (a régua ou o banco), o rascunho sai como antes."""
    assert ia.trava_do_agente(_rascunho("rastreio", {}), _canal()) is None
    assert ia.trava_do_agente(_rascunho("rastreio", {"banco_tipos": {}}), _canal()) is None


def test_com_a_marca_precisa_de_resposta_do_banco_do_mesmo_tipo():
    sem = _rascunho(
        "rastreio",
        {"banco_exigido": True, "portao_banco": True, "banco_tipos": {"par:b1": "garantia"}},
    )
    assert ia.trava_do_agente(sem, _canal()) == ia.MOTIVO_SEM_BANCO == "sem_banco"
    vazio = _rascunho("rastreio", {"banco_exigido": True, "portao_banco": True, "banco_tipos": {}})
    assert ia.trava_do_agente(vazio, _canal()) == "sem_banco"
    com = _rascunho(
        "rastreio",
        {
            "banco_exigido": True,
            "portao_banco": True,
            "banco_tipos": {"par:b1": "garantia", "par:b2": "rastreio"},
        },
    )
    assert ia.trava_do_agente(com, _canal()) is None


def test_as_travas_do_assunto_vem_antes_e_nao_mudam_de_nome():
    """O assunto não liberado continua com o código dele (a regra do e-mail os lê)."""
    r = _rascunho("garantia", {"banco_exigido": True, "portao_banco": True, "banco_tipos": {}})
    assert ia.trava_do_agente(r, _canal()) == "orientacao_nao_liberada"
    r = _rascunho(
        "estoque_variacao", {"banco_exigido": True, "portao_banco": True, "banco_tipos": {}}
    )
    assert ia.trava_do_agente(r, _canal()) in duvida.ASSUNTO_NAO_LIBERADO
    pessoa = _rascunho("rastreio", {"banco_exigido": True, "portao_banco": True, "banco_tipos": {}})
    pessoa.fatos["agente"]["acoes_pessoa"] = ["reclamacao_aberta"]
    assert ia.trava_do_agente(pessoa, _canal()) == "acao_pessoa"


def test_cortesia_nao_pede_banco():
    canal = _canal(auto_categorias=["agradecimento"], auto_agente=["agradecimento"])
    r = _rascunho("agradecimento", {"banco_exigido": True, "portao_banco": True, "banco_tipos": {}})
    assert not ia.sem_banco_do_tipo(r.fatos["agente"], "agradecimento")
    assert ia.trava_do_agente(r, canal) is None


def test_a_linha_da_duvida_diz_o_portao_do_banco():
    fatos = {duvida.FATO_AUTO_ENVIO: {"motivo": ia.MOTIVO_SEM_BANCO}}
    assert duvida.linha(fatos, "rastreio") == duvida.FRASES["sem_banco"]


@pytest.mark.parametrize(
    ("le_banco", "regua", "plataforma", "canal", "esperado"),
    [
        (True, True, "shopee", "chat", True),
        (True, True, "ml", "pos_venda", True),
        (False, True, "shopee", "chat", False),
        (True, False, "shopee", "chat", False),
        (True, True, "shopee", constantes.CANAL_EMAIL, False),
        (True, True, "whatsapp", "chat", False),
        (True, True, None, "chat", False),
    ],
)
def test_onde_o_portao_vale(monkeypatch, le_banco, regua, plataforma, canal, esperado):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_agente_le_banco", le_banco)
    monkeypatch.setattr(s, "atendimento_regua_ativa", regua)
    assert agente_ctx.banco_exigido_para(plataforma, canal) is esperado


def test_o_contexto_grava_a_marca_e_os_tipos_so_com_o_portao():
    ctx = agente_ctx.ContextoAgente(
        conversa_id="c",
        gatilho_id=None,
        canal_id=None,
        integration_id=None,
        plataforma="shopee",
        canal="chat",
        modo="auto",
    )
    ctx.registrar_pares(
        [
            {"id": "par:b10", "tipo": "rastreio", "pergunta": "p", "resposta": "r"},
            {"id": "par:b11", "tipo": None, "pergunta": "p", "resposta": "r"},
            {"id": "par:77", "tipo": "rastreio", "pergunta": "p", "resposta": "r"},
        ]
    )
    assert "banco_exigido" not in ctx.para_fatos()
    ctx.banco_exigido = True
    fatos = ctx.para_fatos()
    assert fatos["banco_exigido"] is True
    # Só as do banco (`par:b…`) e com tipo.
    assert fatos["banco_tipos"] == {"par:b10": "rastreio"}


# ── A régua solta o assunto "só de pessoa" (13/11/2026) ──────────────────────
# Dos 8 assuntos "só de pessoa", 7 são de orientação: a lista `auto_orientacao` (que a
# régua escreve) já os solta. O único fora dela é a reclamação forte — é ela que o
# `liberado_pela_regua` solta.


def test_so_pessoa_continua_preso_sem_a_regua():
    canal = _canal(auto_categorias=["reclamacao_forte"], auto_agente=["reclamacao_forte"])
    r = _rascunho("reclamacao_forte", {})
    assert ia.trava_do_agente(r, canal) == "assunto_so_humano"


def test_a_regua_solta_o_assunto_que_ela_pos_na_lista():
    canal = _canal(
        auto_categorias=["reclamacao_forte"],
        auto_agente=["reclamacao_forte"],
        auto_orientacao=["reembolso"],
    )
    r = _rascunho("reclamacao_forte", {"regua": True})
    assert ia.liberado_pela_regua(r.fatos["agente"], "reclamacao_forte", canal)
    assert ia.trava_do_agente(r, canal) is None
    # O de orientação sai pela lista dele, como sempre.
    assert ia.trava_do_agente(_rascunho("reembolso", {"regua": True}), canal) is None
    # Fora da lista do canal (a régua não soltou): o assunto continua não liberado.
    fora = _rascunho("cancelamento", {"regua": True})
    assert ia.trava_do_agente(fora, canal) in duvida.ASSUNTO_NAO_LIBERADO
    # E o portão do banco continua valendo depois.
    sem_banco = _rascunho(
        "reclamacao_forte",
        {"regua": True, "banco_exigido": True, "portao_banco": True, "banco_tipos": {}},
    )
    assert ia.trava_do_agente(sem_banco, canal) == "sem_banco"
    # A ação de pessoa nunca é solta pela régua.
    acao = _rascunho("reclamacao_forte", {"regua": True})
    acao.fatos["agente"]["acoes_pessoa"] = ["reclamacao_aberta"]
    assert ia.trava_do_agente(acao, canal) == "acao_pessoa"


def test_o_contexto_grava_a_regua_so_quando_ligada(monkeypatch):
    ctx = agente_ctx.ContextoAgente(
        conversa_id="c",
        gatilho_id=None,
        canal_id=None,
        integration_id=None,
        plataforma="shopee",
        canal="chat",
        modo="auto",
    )
    assert "regua" not in ctx.para_fatos()
    ctx.regua = True
    assert ctx.para_fatos()["regua"] is True
    monkeypatch.setattr(get_settings(), "atendimento_regua_ativa", True)
    assert agente_ctx.regua_ativa() is True
    monkeypatch.setattr(get_settings(), "atendimento_regua_ativa", False)
    assert agente_ctx.regua_ativa() is False


# ── "Bateu com a do banco, faz sentido, já manda" (13/11/2026) ───────────────


def _banco(resposta: str, tipo: str = "rastreio") -> dict:
    return {
        "regua": True,
        "banco_exigido": True,
        "banco_tipos": {"par:b7": tipo},
        "banco_respostas": {"par:b7": resposta},
    }


def test_rascunho_igual_ao_do_banco_sai_mesmo_sem_o_assunto_na_lista():
    canal = _canal(auto_categorias=[], auto_agente=[])
    banco = "Olá [NOME]! Seu pedido foi postado em 10/11 pela J&T, código JT123. Obrigado!"
    r = _rascunho("rastreio", _banco(banco))
    r.texto = "Oi, Maria! Seu pedido foi postado em 12/11 pela J&T, código JT987654. Obrigada!"
    assert ia.bateu_com_o_banco(r.fatos["agente"], "rastreio", r.texto)
    assert ia.trava_do_agente(r, canal) is None
    # Texto diferente: continua esperando a régua.
    r.texto = "Seu pedido ainda está em separação, deve sair amanhã."
    assert ia.trava_do_agente(r, canal) == "assunto_nao_liberado"


def test_o_banco_nao_solta_sensivel_nem_acao_nem_outro_tipo():
    canal = _canal(auto_categorias=[], auto_agente=[], auto_orientacao=[])
    texto = "O reembolso é feito pela própria Shopee, pela mesma forma de pagamento."
    sens = _rascunho("reembolso", _banco(texto, tipo="reembolso"))
    sens.texto = texto
    assert not ia.bateu_com_o_banco(sens.fatos["agente"], "reembolso", texto)
    assert ia.trava_do_agente(sens, canal) == "orientacao_nao_liberada"
    acao = _rascunho("rastreio", _banco("Seu pedido foi postado."))
    acao.texto = "Seu pedido foi postado."
    acao.fatos["agente"]["acoes_pessoa"] = ["reclamacao_aberta"]
    assert ia.trava_do_agente(acao, canal) == "acao_pessoa"
    outro = _rascunho("prazo_envio", _banco("Seu pedido foi postado.", tipo="rastreio"))
    outro.texto = "Seu pedido foi postado."
    assert not ia.bateu_com_o_banco(outro.fatos["agente"], "prazo_envio", outro.texto)
    sem_regua = _banco("Seu pedido foi postado.")
    sem_regua["regua"] = False
    assert not ia.bateu_com_o_banco(sem_regua, "rastreio", "Seu pedido foi postado.")


def test_o_contexto_grava_as_respostas_do_banco_com_a_marca():
    ctx = agente_ctx.ContextoAgente(
        conversa_id="c",
        gatilho_id=None,
        canal_id=None,
        integration_id=None,
        plataforma="shopee",
        canal="chat",
        modo="auto",
    )
    ctx.registrar_pares([{"id": "par:b10", "tipo": "rastreio", "pergunta": "p", "resposta": "r1"}])
    assert "banco_respostas" not in ctx.para_fatos()
    ctx.banco_exigido = True
    assert ctx.para_fatos()["banco_respostas"] == {"par:b10": "r1"}


def test_o_portao_do_banco_fica_desligado_sem_a_chave():
    """13/11/2026: no teste da virada o portão segurou 11 de 40 respostas de assuntos que já
    saíam sozinhos — fica desligado (a chave `atendimento_portao_banco`)."""
    vazio = _rascunho("rastreio", {"banco_exigido": True, "banco_tipos": {}})
    assert ia.trava_do_agente(vazio, _canal()) is None
    assert get_settings().atendimento_portao_banco is False
