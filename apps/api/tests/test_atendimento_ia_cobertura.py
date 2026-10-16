# ruff: noqa: F811 — fixtures importadas dos testes da IA
"""Cobertura regional: a restrição do próprio anúncio pode sair no automático."""

import pytest
from sqlalchemy import select

from app.models import AtendimentoRegra
from app.services.atendimento import ficha, ia
from app.services.atendimento.constantes import ORIGEM_IA
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    ModeloSequencia,
    _conversa,
    _importar_manual_base,
    _msg,
    _saida,
    envios,
    ia_ligada,
)


@pytest.mark.parametrize("restricao", [True, False])
async def test_cobertura_anuncio_restrito_envia_e_demais_casos_aguardam_pessoa(
    db, make_user, ia_ligada, envios, monkeypatch, restricao
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    conversa = await _conversa(
        db, make_user, pedido=None, modo="auto", auto_categorias=["cobertura_regiao"]
    )
    await _msg(db, conversa, "Vocês entregam para o estado do Rio de Janeiro?")
    descricao = (
        "Não fazemos envios para o estado do Rio de Janeiro!"
        if restricao
        else "Mala de bordo com rodinhas."
    )

    async def fichas(session, conversa, recentes, textos_rajada):
        return [{"titulo": conversa.anuncio_titulo, "descricao": descricao}]

    monkeypatch.setattr(ficha, "fichas_da_conversa", fichas)
    resposta = (
        "Conforme informado no anúncio, não realizamos envios para o Rio de Janeiro. "
        "Você pode inserir o CEP no anúncio para confirmar a disponibilidade de entrega."
        if restricao
        else "Você pode conferir a disponibilidade de entrega informando o CEP no anúncio."
    )
    modelo = ModeloSequencia(
        [
            {"categoria": "cobertura_regiao", "confianca": 0.95},
            _saida(resposta, categoria="cobertura_regiao", precisa_humano=not restricao),
        ]
    )
    monkeypatch.setattr(ia, "_chamar_modelo", modelo)

    rascunho = await ia.gerar_rascunho(db, conversa)

    assert rascunho is not None and rascunho.validador_ok
    assert rascunho.precisa_humano is not restricao
    assert descricao in modelo.usuario
    regra = await db.scalar(
        select(AtendimentoRegra).where(AtendimentoRegra.categoria == "cobertura_regiao")
    )
    assert regra.quando == "Se o anúncio entrega na região do cliente."
    assert regra.faca in modelo.sistema
    assert len(envios) == int(restricao)
    if restricao:
        assert envios[0]["texto"] == resposta and envios[0]["origem"] == ORIGEM_IA
