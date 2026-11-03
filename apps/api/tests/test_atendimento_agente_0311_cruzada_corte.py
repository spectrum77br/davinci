# ruff: noqa: F811, E501 — fixtures importadas
"""Revisão cruzada do an0311 sobre wip/0311-integra (03/11/2026), com a API falsa do agente e do
verificador (nenhuma chamada a LLM, à loja ou à produção):

2. O corte da frase sem lastro (v17) conferia de novo a Proposta JÁ trocada para "responder":
   o Abrir chamado (entrega) que o MODELO escolheu sumia, o resto saía sem a etiqueta e sem a
   marca, e a conversa deixava a Caixa Humano. Agora o corte parte da Proposta do modelo e não
   sai se perderia uma ação de depois do envio.
1. (a cadeia ao vivo) a etiqueta do modelo atravessa o "Ok, obrigado" do turno seguinte: a
   resposta dele sai com a etiqueta e a marca, e a conversa continua na Caixa Humano.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.db as _db
from app.models import AtendimentoConversa
from app.services.atendimento import constantes, gravar, humano, ia
from app.services.atendimento.agente import checagens
from tests.test_atendimento_agente_ligacao import _decisao
from tests.test_atendimento_agente_verificador import (  # noqa: F401 — fixtures
    _mensagem_do_agente,
    _no_auto,
    _ok,
    _sem_a_regua_dos_dias,
    _sem_limites,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    modelo,
    teto_limpo,
)

COM_SOBRA = (
    "Olá! Seu pedido já foi enviado; o rastreio é {rastreio}. "
    "Ele foi embrulhado com papel de presente."
)
LINHA_DA_ACAO = "abrir o chamado da entrega com a plataforma"


def _chamado_do_modelo(texto: str = COM_SOBRA) -> dict:
    return _mensagem_do_agente(
        acao="pessoa", acao_pessoa="abrir_chamado",
        motivo_pessoa="a entrega passou da previsão", texto=texto,
    )  # fmt: skip


def _sem_lastro() -> dict:
    return _ok(
        afirma_sem_lastro=["Ele foi embrulhado com papel de presente"],
        ok_para_envio_automatico=False,
    )


async def _chega_como_do_envio(db, conversa, texto: str, *, quando: datetime | None = None):
    await gravar.gravar_mensagem(
        db, conversa, externo_id=f"ia-{datetime.now(UTC).timestamp()}", autor="loja", texto=texto,
        enviada_em=quando or datetime.now(UTC), origem=constantes.ORIGEM_IA,
    )  # fmt: skip
    await db.commit()
    await db.refresh(conversa)


async def _na_caixa(conversa) -> bool:
    await humano.recalcular(orcamento_s=None)
    async with _db.SessionLocal() as s:
        lojas_ = await humano.lojas_da_caixa(s, humano.modos_da_caixa())
        return bool(
            await s.scalar(
                select(humano.caixa_humano_sql(lojas_)).where(AtendimentoConversa.id == conversa.id)
            )
        )


async def test_2_o_corte_mantem_a_etiqueta_que_o_modelo_escolheu(
    db, make_user, agente_ligado, envios, api, teto_limpo, _sem_a_regua_dos_dias
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_chamado_do_modelo()]
    api.veredito = [_sem_lastro(), _ok()]

    r = await ia.gerar_rascunho(db, conversa)

    ag = r.fatos["agente"]
    assert _decisao(r) == "enviado" and (ag.get("corte_sem_lastro") or {}).get("saiu") is True
    assert [e["texto"] for e in envios] == [r.texto] and "papel de presente" not in r.texto
    assert ag["acoes_modelo"] == [constantes.ACAO_ABRIR_CHAMADO] and ag["acoes_pessoa"] == []
    assert ag["etiquetas_pos_envio"] == [constantes.ACAO_ABRIR_CHAMADO]
    assert ag["acoes_pos_envio"] == [constantes.ACAO_ABRIR_CHAMADO]
    # O 2º verificador também leu que a ação continua com a pessoa.
    assert [LINHA_DA_ACAO in q["messages"][0]["content"] for q in api.pedidos] == [True, True]
    # A marca foi gravada: depois do envio, a conversa continua na Caixa Humano.
    await db.refresh(conversa)
    assert (conversa.dados or {}).get(humano.CHAVE_POS_ENVIO, {}).get("acoes") == [
        constantes.ACAO_ABRIR_CHAMADO
    ]
    await _chega_como_do_envio(db, conversa, envios[-1]["texto"])
    assert not conversa.aguardando_resposta and await _na_caixa(conversa)


async def test_2_o_resto_que_perderia_a_etiqueta_nao_sai(
    db, make_user, agente_ligado, envios, api, teto_limpo, _sem_a_regua_dos_dias, monkeypatch
):
    """Se a 2ª conferência perder a ação de depois do envio (aqui, forçado), o corte não sai: o
    rascunho de antes fica, para pessoa — nunca o resto sem a etiqueta."""
    original = checagens.responde_e_marca_da_proposta
    vezes = []

    def _perde_na_segunda(p, ctx):
        vezes.append(1)
        nova, depois = original(p, ctx)
        return (nova, []) if len(vezes) > 1 else (nova, depois)

    monkeypatch.setattr(checagens, "responde_e_marca_da_proposta", _perde_na_segunda)
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_chamado_do_modelo()]
    api.veredito = [_sem_lastro(), _ok()]

    r = await ia.gerar_rascunho(db, conversa)

    ag = r.fatos["agente"]
    assert envios == [] and _decisao(r) != "enviado"
    assert ag["corte_sem_lastro"]["saiu"] is False
    assert ag["corte_sem_lastro"]["porque"] == "a 1ª camada segurou o resto"
    assert len(api.pedidos) == 1  # o 2º verificador nem foi chamado


async def test_1_a_etiqueta_do_modelo_atravessa_o_obrigado_do_cliente(
    db, make_user, agente_ligado, envios, api, teto_limpo, _sem_a_regua_dos_dias
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_chamado_do_modelo("Olá! Seu pedido já foi enviado; o rastreio é {rastreio}.")]
    api.veredito = [_ok()]
    r1 = await ia.gerar_rascunho(db, conversa)
    assert _decisao(r1) == "enviado"
    agora = datetime.now(UTC)
    await _chega_como_do_envio(
        db, conversa, envios[-1]["texto"], quando=agora - timedelta(seconds=60)
    )
    assert await _na_caixa(conversa)
    marca1 = dict((conversa.dados or {})[humano.CHAVE_POS_ENVIO])

    # O cliente agradece (o turno novo): a etiqueta continua, a conversa fica na Caixa Humano.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="obrigado", autor="cliente", texto="Ok, obrigado",
        enviada_em=agora - timedelta(seconds=30),
    )  # fmt: skip
    await db.commit()
    await db.refresh(conversa)
    assert conversa.aguardando_resposta and await _na_caixa(conversa)
    dados = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == conversa.id)
    )
    assert constantes.ACAO_ABRIR_CHAMADO in dados["humano"]["assuntos"]

    # A resposta do agente ao "obrigado" sai COM a etiqueta e grava a marca do turno novo.
    api.agente = [_mensagem_do_agente(texto="Nós que agradecemos! Qualquer dúvida, é só chamar.")]
    api.veredito = [_ok()]
    r2 = await ia.gerar_rascunho(db, conversa)
    ag2 = r2.fatos["agente"]
    assert constantes.ACAO_ABRIR_CHAMADO in ag2["acoes_codigo"]
    assert ag2.get("etiquetas_pos_envio") == [constantes.ACAO_ABRIR_CHAMADO], ag2
    assert _decisao(r2) == "enviado" and len(envios) == 2
    await db.refresh(conversa)
    marca2 = (conversa.dados or {})[humano.CHAVE_POS_ENVIO]
    assert marca2["turno"] > marca1["turno"]
    assert marca2["acoes"] == [constantes.ACAO_ABRIR_CHAMADO]
    await _chega_como_do_envio(db, conversa, envios[-1]["texto"], quando=agora)
    assert not conversa.aguardando_resposta
    # Respondida pela IA, a conversa continua na Caixa Humano com o rótulo até a pessoa agir.
    assert await _na_caixa(conversa)
    # A pessoa responde: sai.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="pessoa", autor="loja", texto="Abrimos o chamado com a Shopee.",
        enviada_em=datetime.now(UTC) + timedelta(minutes=5),
    )  # fmt: skip
    await db.commit()
    await db.refresh(conversa)
    assert not await _na_caixa(conversa)
