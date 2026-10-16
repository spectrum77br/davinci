# ruff: noqa: F811 — fixtures compartilhadas
"""Retomada automática sem atropelar pessoa, com diagnóstico sem PII."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import AtendimentoMensagem, AtendimentoRascunho
from app.services.atendimento import enviar, humano, ia
from app.services.atendimento.constantes import (
    ETIQUETA_AG_CANCELAMENTO,
    ORIGEM_AUTO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_ia import (  # noqa: F401
    _cenario_auto,
    _msg,
    envios,
    ia_ligada,
    modelo,
)


@pytest.mark.parametrize("origem", ["externo", ORIGEM_HUMANO])
async def test_aguarda_pessoa_30min_e_retoma_sem_consumir_retry(
    db, make_user, ia_ligada, modelo, envios, origem
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    cliente = await ia._ultima_do_cliente(db, conversa)
    cliente.enviada_em = datetime.now(UTC) - timedelta(minutes=20)
    conversa.ultima_do_cliente_em = cliente.enviada_em
    await db.commit()
    await _msg(
        db,
        conversa,
        "Vou conferir para você.",
        autor="loja",
        origem=origem,
        ha=timedelta(minutes=25),
    )
    pessoa = await db.scalar(
        select(AtendimentoMensagem).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == "loja",
        )
    )
    r = await ia.gerar_rascunho(db, conversa)
    assert r is not None and r.status == RASCUNHO_PENDENTE
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "pausa_humana"
    assert "retomar_em" in r.fatos[ia.FATO_AUTO_ENVIO]
    assert envios == []
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - timedelta(minutes=3))
    )
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 0
    assert await ia.redis.get(ia._CHAVE_REENVIO.format(ia_ligada.database_schema, r.id)) is None
    # O alerta de atraso da Caixa Humano não transforma espera em veto eterno.
    assert humano.ia_sem_resposta(conversa, datetime.now(UTC))
    pessoa.enviada_em = datetime.now(UTC) - timedelta(minutes=31)
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 1
    assert [e["rascunho_id"] for e in envios] == [r.id]
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "enviado"
    assert len(modelo.chamadas) == 1
    assert await ia.reenviar_pendentes(db) == 0
    assert len(envios) == 1


@pytest.mark.parametrize("origem", ["externo", ORIGEM_HUMANO, ORIGEM_AUTO, ORIGEM_IA])
async def test_so_pessoa_real_inicia_pausa(db, make_user, ia_ligada, modelo, origem):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    await _msg(
        db,
        conversa,
        "Vou conferir para você.",
        autor="loja",
        origem=origem,
        ha=timedelta(minutes=15),
    )
    assert await ia._humano_respondeu_recente(db, conversa) is (
        origem in ("externo", ORIGEM_HUMANO)
    )


async def test_menu_e_resposta_falha_nao_iniciam_pausa(db, make_user, ia_ligada, modelo):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    await _msg(
        db,
        conversa,
        "Olá, a sua mensagem foi recebida. Aguarde um atendente.",
        autor="loja",
        ha=timedelta(minutes=15),
    )
    falha = await _msg(
        db,
        conversa,
        "Vou conferir para você.",
        autor="loja",
        origem=ORIGEM_HUMANO,
        ha=timedelta(minutes=14),
    )
    falha.status = "falhou"
    await _msg(
        db,
        conversa,
        "Nota interna",
        autor="loja",
        origem=ORIGEM_HUMANO,
        tipo="nota",
        ha=timedelta(minutes=13),
    )
    await db.commit()
    assert await ia._humano_respondeu_recente(db, conversa) is False


async def test_motivo_real_da_caixa_humano_veta_rascunho_seguro(
    db, make_user, ia_ligada, modelo, envios
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    ia_ligada.atendimento_auto_ativo = False
    r = await ia.gerar_rascunho(db, conversa)
    assert not r.precisa_humano and r.validador_ok
    ia_ligada.atendimento_auto_ativo = True
    conversa.etiqueta = ETIQUETA_AG_CANCELAMENTO
    await db.commit()
    await ia._talvez_enviar(db, conversa, r)
    assert envios == []
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "caixa_humano"
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivos_humano"] == ["ag_cancelamento"]


async def test_recusa_registra_apenas_codigo_sem_texto_do_cliente(
    db, make_user, ia_ligada, modelo, monkeypatch
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)

    async def recusar(*args, **kwargs):
        raise enviar.EnvioRecusado("envio_desligado", "SEGREDO: texto ou dado de comprador")

    monkeypatch.setattr(enviar, "enviar_resposta", recusar)
    r = await ia.gerar_rascunho(db, conversa)
    decisao = r.fatos[ia.FATO_AUTO_ENVIO]
    assert decisao["motivo"] == "recusado:envio_desligado"
    assert "SEGREDO" not in str(decisao)
    await db.refresh(r)
    assert r.fatos[ia.FATO_AUTO_ENVIO] == decisao


@pytest.mark.parametrize("guarda", ["pessoa", "caixa_humano"])
async def test_caminho_de_envio_reconfere_sob_trava(
    db, make_user, ia_ligada, modelo, guarda, monkeypatch
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    ia_ligada.atendimento_auto_ativo = False
    r = await ia.gerar_rascunho(db, conversa)
    ia_ligada.atendimento_auto_ativo = True
    monkeypatch.setattr(ia_ligada, "atendimento_envio_ativo", True)
    monkeypatch.setattr(ia_ligada, "atendimento_simulador", True)
    if guarda == "pessoa":
        await _msg(
            db,
            conversa,
            "Vou conferir para você.",
            autor="loja",
            origem=ORIGEM_HUMANO,
            ha=timedelta(minutes=15),
        )
    else:
        conversa.etiqueta = ETIQUETA_AG_CANCELAMENTO
        await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as exc:
        await enviar.enviar_resposta(
            db, conversa, r.texto, user=None, rascunho_id=r.id, origem=ORIGEM_IA
        )
    assert exc.value.code == "nao_aguarda"
    assert not await db.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.origem == ORIGEM_IA,
        )
    )


@pytest.mark.parametrize("status", ["falhou", "revisar"])
async def test_resultado_da_plataforma_nao_vira_enviado(
    db, make_user, ia_ligada, modelo, monkeypatch, status
):
    from types import SimpleNamespace

    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)

    async def resultado(*args, **kwargs):
        return SimpleNamespace(status=status)

    monkeypatch.setattr(enviar, "enviar_resposta", resultado)
    r = await ia.gerar_rascunho(db, conversa)
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "resultado:" + status


async def test_reenvio_ja_tentado_nao_rele_contexto(
    db, make_user, ia_ligada, modelo, envios, monkeypatch
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    ia_ligada.atendimento_auto_ativo = False
    r = await ia.gerar_rascunho(db, conversa)
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - timedelta(minutes=3))
    )
    await db.commit()
    ia_ligada.atendimento_auto_ativo = True
    assert await ia._marcar_reenvio(r.id)

    async def contexto_nao_deve_ser_lido(*args, **kwargs):
        raise AssertionError("rascunho já tentado não deve reler contexto")

    monkeypatch.setattr(ia, "_motivos_humano_para_auto", contexto_nao_deve_ser_lido)
    assert await ia.reenviar_pendentes(db) == 0
    assert envios == []


@pytest.mark.parametrize("segundos,pausa", [(1799, True), (1800, False), (1801, False)])
async def test_limite_da_pausa_e_exatamente_30min(
    db, make_user, ia_ligada, modelo, segundos, pausa
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    pessoa = await _msg(
        db,
        conversa,
        "Vou conferir para você.",
        autor="loja",
        origem=ORIGEM_HUMANO,
        ha=timedelta(minutes=15),
    )
    agora = pessoa.enviada_em + timedelta(seconds=segundos)
    assert (await ia._pausa_humana_ate(db, conversa, agora=agora) is not None) is pausa


async def test_pessoa_entrou_entre_filtro_e_envio_preserva_retry(
    db, make_user, ia_ligada, modelo, monkeypatch
):
    from types import SimpleNamespace

    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    ia_ligada.atendimento_auto_ativo = False
    r = await ia.gerar_rascunho(db, conversa)
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - timedelta(minutes=3))
    )
    await db.commit()
    ia_ligada.atendimento_auto_ativo = True
    pessoas = []

    async def pessoa_chegou(*args, **kwargs):
        if not pessoas:
            pessoas.append(
                await _msg(
                    db,
                    conversa,
                    "Vou conferir para você.",
                    autor="loja",
                    origem=ORIGEM_HUMANO,
                    ha=timedelta(minutes=15),
                )
            )
            raise enviar.EnvioRecusado("nao_aguarda", "Uma pessoa entrou no intervalo")
        return SimpleNamespace(status="enviada")

    monkeypatch.setattr(enviar, "enviar_resposta", pessoa_chegou)
    assert await ia.reenviar_pendentes(db) == 0
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "pausa_humana"
    assert not await ia._reenvio_ja_marcado(r.id)
    pessoas[0].enviada_em = datetime.now(UTC) - timedelta(minutes=31)
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 1
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "enviado"
