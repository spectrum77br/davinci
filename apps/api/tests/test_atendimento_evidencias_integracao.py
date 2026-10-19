# ruff: noqa: F811 — fixtures compartilhadas
"""A prova comercial é conferida antes de criar sugestão pronta ou enviar."""

import pytest

from app.services.atendimento import ficha, ia
from tests.test_atendimento_ia import (  # noqa: F401
    _conversa,
    _importar_manual_base,
    _msg,
    _saida,
    envios,
    ia_ligada,
    modelo,
)


@pytest.mark.parametrize(
    "titulo,resposta,permitida",
    [
        (
            "Apple Watch Series 11",
            "Olá! O produto é original, novo, lacrado e da versão americana.",
            False,
        ),
        (
            "Apple Watch Original Novo Lacrado Versão Americana",
            "Olá! O produto é original, novo, lacrado e da versão americana.",
            True,
        ),
        ("Apple Watch Series 11", "Olá! Não é original.", False),
        ("Apple Watch Series 11", "É original? Pode confiar!", False),
    ],
)
async def test_modelo_confiante_so_envia_condicao_com_prova(
    db, make_user, ia_ligada, modelo, envios, monkeypatch, titulo, resposta, permitida
):
    async def sem_ficha(*args, **kwargs):
        return []

    monkeypatch.setattr(ficha, "fichas_da_conversa", sem_ficha)
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    conversa = await _conversa(
        db, make_user, pedido=None, modo="auto", auto_categorias=["originalidade_condicao"]
    )
    conversa.anuncio_titulo = titulo
    await db.commit()
    await _msg(db, conversa, "O produto é original e novo?")
    modelo.saida = _saida(resposta, categoria="originalidade_condicao", confianca=0.99)
    rascunho = await ia.gerar_rascunho(db, conversa)
    assert rascunho is not None
    assert rascunho.validador_ok is permitida
    if permitida:
        assert not rascunho.precisa_humano, rascunho.motivo
        assert len(envios) == 1
    else:
        assert rascunho.status == "bloqueado"
        assert rascunho.precisa_humano
        assert "sem confirmação" in rascunho.motivo
        assert envios == []


async def test_fala_do_comprador_nao_comprova_versao_americana(
    db, make_user, ia_ligada, modelo, envios, monkeypatch
):
    async def sem_ficha(*args, **kwargs):
        return []

    monkeypatch.setattr(ficha, "fichas_da_conversa", sem_ficha)
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    conversa = await _conversa(
        db, make_user, pedido=None, modo="auto", auto_categorias=["originalidade_condicao"]
    )
    conversa.anuncio_titulo = "Apple Watch Series 11"
    await db.commit()
    await _msg(db, conversa, "Vocês falaram que é versão americana. É isso mesmo?")
    modelo.saida = _saida(
        "Sim, é da versão americana.", categoria="originalidade_condicao", confianca=0.99
    )
    rascunho = await ia.gerar_rascunho(db, conversa)
    assert rascunho.status == "bloqueado" and not rascunho.validador_ok
    assert envios == []
