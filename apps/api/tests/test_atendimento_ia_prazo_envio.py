# ruff: noqa: F811 — fixtures compartilhadas com a suíte da IA
"""Consulta de postagem pode responder o status sem prometer envio no dia.

O modelo é falso: os testes medem a aplicação do Manual completo e as
travas de envio, não a interpretação de linguagem natural do provedor.
"""

import pytest
from sqlalchemy import select, update

from app.models import AtendimentoRegra, BlingOrder, Logistica
from app.services.atendimento import ia
from app.services.atendimento.constantes import ORIGEM_IA, RASCUNHO_PENDENTE
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    PEDIDO_BLING,
    PEDIDO_MKT,
    ModeloSequencia,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
    _saida,
    envios,
    ia_ligada,
)

RESPOSTA = (
    "Olá! Seu pedido {numero_pedido} está em preparação. Quando for postado, "
    "o código de rastreio e a previsão de entrega aparecerão no próprio pedido."
)


async def _pedido_em_preparacao(db):
    await _pedido_completo(db)
    await db.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == PEDIDO_BLING)
        .values(situacao="21", status="Em digitação", em_andamento_data=None)
    )
    await db.execute(
        update(Logistica)
        .where(Logistica.pedido_bling == PEDIDO_BLING)
        .values(
            rastreio=None,
            postagem_data=None,
            previsao_correios=None,
            localizacao="Pedido em preparação",
        )
    )
    await db.commit()


async def _cenario(db, make_user, ia_ligada, monkeypatch, pergunta, *, precisa_humano=False):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    conversa = await _conversa(
        db, make_user, modo="auto", auto_categorias=["prazo_envio"]
    )
    await _msg(db, conversa, pergunta)
    modelo = ModeloSequencia(
        [
            {"categoria": "prazo_envio", "confianca": 0.97},
            _saida(
                RESPOSTA,
                categoria="prazo_envio",
                precisa_humano=precisa_humano,
                motivo="pedido urgente" if precisa_humano else "status conhecido, sem promessa",
            ),
        ]
    )
    monkeypatch.setattr(ia, "_chamar_modelo", modelo)
    return conversa, modelo


@pytest.mark.parametrize(
    "pergunta",
    [
        "Tranquilo, vocês conseguem enviar hoje?",
        "Vocês enviam amanhã?",
        "Quando será postado o meu pedido?",
    ],
)
async def test_consulta_de_postagem_envia_status_sem_prometer_dia(
    db, make_user, ia_ligada, envios, monkeypatch, pergunta
):
    await _pedido_em_preparacao(db)
    conversa, modelo = await _cenario(db, make_user, ia_ligada, monkeypatch, pergunta)

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert rascunho.status == RASCUNHO_PENDENTE
    assert rascunho.validador_ok and not rascunho.precisa_humano
    # Sem o catálogo de nomes do Bling semeado, o contexto mantém o código.
    assert rascunho.fatos["pedido"]["situacao_no_sistema"] == "21"
    assert rascunho.fatos["lacunas"]["data_envio"] is None
    assert rascunho.fatos["lacunas"]["previsao_entrega"] is None
    assert rascunho.texto == RESPOSTA.replace("{numero_pedido}", PEDIDO_MKT)
    assert len(envios) == 1 and envios[0]["origem"] == ORIGEM_IA
    assert envios[0]["texto"] == rascunho.texto
    regra = await db.scalar(
        select(AtendimentoRegra).where(
            AtendimentoRegra.categoria == "prazo_envio",
            AtendimentoRegra.plataforma.is_(None),
            AtendimentoRegra.canal.is_(None),
        )
    )
    assert regra.faca in modelo.sistema
    assert pergunta in modelo.usuario
    assert len(modelo.chamadas) == 2


@pytest.mark.parametrize(
    "pergunta",
    [
        "É urgente, preciso receber até amanhã.",
        "Pode priorizar o meu pedido e enviar antes dos outros?",
        "Vocês conseguem enviar hoje? Preciso receber amanhã para viajar.",
        "O prazo de postagem do meu pedido já passou.",
    ],
)
async def test_urgencia_prioridade_ou_prazo_vencido_continuam_para_pessoa(
    db, make_user, ia_ligada, envios, monkeypatch, pergunta
):
    await _pedido_em_preparacao(db)
    conversa, _modelo = await _cenario(
        db, make_user, ia_ligada, monkeypatch, pergunta, precisa_humano=True
    )

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert rascunho.status == RASCUNHO_PENDENTE and rascunho.validador_ok
    assert rascunho.precisa_humano and envios == []
    assert rascunho.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "precisa_humano"


async def test_sem_pedido_conhecido_nao_envia_mesmo_com_modelo_liberando(
    db, make_user, ia_ligada, envios, monkeypatch
):
    conversa, _modelo = await _cenario(
        db, make_user, ia_ligada, monkeypatch, "Vocês conseguem enviar hoje?"
    )

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert rascunho.precisa_humano and envios == []
    assert "pedido não encontrado no sistema" in rascunho.motivo


async def test_consulta_com_auto_desligado_fica_somente_como_sugestao(
    db, make_user, ia_ligada, envios, monkeypatch
):
    await _pedido_em_preparacao(db)
    conversa, _modelo = await _cenario(
        db, make_user, ia_ligada, monkeypatch, "Vocês conseguem enviar hoje?"
    )
    ia_ligada.atendimento_auto_ativo = False

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert not rascunho.precisa_humano and rascunho.validador_ok
    assert envios == []
    assert rascunho.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "auto_desligado"
