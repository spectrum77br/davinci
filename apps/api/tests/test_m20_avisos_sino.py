"""M-20 (23/11/2026): o DaVinci não fala mais com o Telegram.

O administrador: "pode excluir as integrações com o telegram, não utilizamos
mais". Nenhum aviso pode se perder: o que só ia ao Telegram vira um alerta ADM
no sino (M-19) — todo administrador vê em /alertas com Equipe = ADM, quem não
é administrador não vê.

- `alerts.avisar_administradores`: o alerta ADM sem destinatário, a dedupe, a
  sessão própria ou o savepoint de quem chamou, e nunca levanta;
- o agente de atendimento (teto de custo, pausa, verificador) e os envios
  sensíveis gravam no sino de verdade;
- marketing (crédito, falhas seguidas) com a loja e a dedupe do dia; DMs;
- o script da reversão do agente (`scripts/agente_reversao.py`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    Integration,
    IntegrationPlatform,
    UserRole,
)
from app.models.alert import ESCOPO_ADM
from app.redis_client import redis
from app.security.cipher import encrypt_json
from app.services import alerts as alerts_svc
from app.services.atendimento.agente import custo

pytestmark = pytest.mark.asyncio


async def _alertas(db: AsyncSession, titulo: str) -> list[Alert]:
    return list(
        (await db.execute(select(Alert).where(Alert.title.like(f"{titulo}%")))).scalars().all()
    )


async def _titulos_na_tela(client: AsyncClient) -> set[str]:
    r = await client.get("/api/alerts", params={"limit": 200})
    assert r.status_code == 200, r.text
    return {i["title"] for i in r.json()["items"]}


# ================================================================ o ajudante


async def test_aviso_vira_alerta_adm_que_so_o_administrador_ve(
    db: AsyncSession, client: AsyncClient, auth_as, make_user
):
    admin = await make_user(role=UserRole.ADMIN)
    pessoa = await make_user(role=UserRole.USER)
    ok = await alerts_svc.avisar_administradores(
        "M20 teste: aviso do robô", "texto simples", severity=AlertSeverity.ERROR
    )
    assert ok is True
    (a,) = await _alertas(db, "M20 teste: aviso do robô")
    assert a.escopo == ESCOPO_ADM and a.user_id is None
    assert a.type == AlertType.GENERIC and a.severity == AlertSeverity.ERROR
    assert a.message == "texto simples"

    auth_as(admin)
    assert "M20 teste: aviso do robô" in await _titulos_na_tela(client)
    auth_as(pessoa)
    assert "M20 teste: aviso do robô" not in await _titulos_na_tela(client)


async def test_dedupe_grava_uma_vez_e_responde_que_esta_no_sino(db: AsyncSession):
    for _ in range(3):
        assert await alerts_svc.avisar_administradores(
            "M20 teste: dedupe", "x", dedupe_key="m20-teste-dedupe"
        )
    assert len(await _alertas(db, "M20 teste: dedupe")) == 1


async def test_com_a_sessao_de_quem_chama_grava_no_savepoint(db: AsyncSession):
    assert await alerts_svc.avisar_administradores("M20 teste: savepoint", "y", session=db)
    await db.commit()
    assert len(await _alertas(db, "M20 teste: savepoint")) == 1


async def test_erro_ao_gravar_nunca_levanta(db: AsyncSession):
    with patch.object(alerts_svc, "emit_alert", new=AsyncMock(side_effect=RuntimeError("caiu"))):
        assert await alerts_svc.avisar_administradores("M20 teste: erro", "z") is False
        # Com a sessão de quem chama, o erro não estraga a transação dela.
        assert await alerts_svc.avisar_administradores("M20 teste: erro", "z", session=db) is False
    await db.commit()
    assert await _alertas(db, "M20 teste: erro") == []


# ================================================================ atendimento / agente


async def test_teto_do_dia_do_agente_vai_ao_sino(db: AsyncSession):
    dia = "2099-01-01"
    chave = custo.CHAVE_AVISO.format(schema=get_settings().database_schema, dia=dia)
    await redis.delete(chave)
    try:
        await custo._avisar_teto_do_dia(dia, 5.5, 5.0)
        await custo._avisar_teto_do_dia(dia, 6.0, 5.0)  # uma vez por dia
    finally:
        await redis.delete(chave)
    (a,) = await _alertas(db, "Agente de atendimento: teto de custo do dia")
    assert a.escopo == ESCOPO_ADM and a.user_id is None
    assert "US$ 5.50 de US$ 5.00" in a.message and "<b>" not in a.message


async def test_aviso_uma_vez_por_dia_vai_ao_sino_com_o_titulo(db: AsyncSession):
    tipo = "m20_teste_pausa"
    dia = custo.dia_de_hoje()
    chave = custo.CHAVE_AVISO_DO_DIA.format(
        schema=get_settings().database_schema, tipo=tipo, dia=dia
    )
    await redis.delete(chave)
    try:
        for _ in range(2):
            await custo.avisar_uma_vez_por_dia(
                tipo, "O provedor recusou o pedido do agente.", titulo="M20 teste: pausa"
            )
    finally:
        await redis.delete(chave)
    (a,) = await _alertas(db, "M20 teste: pausa")
    assert a.message == "O provedor recusou o pedido do agente."


async def test_aviso_que_nao_gravou_devolve_a_marca_do_dia(db: AsyncSession):
    tipo = "m20_teste_falha"
    chave = custo.CHAVE_AVISO_DO_DIA.format(
        schema=get_settings().database_schema, tipo=tipo, dia=custo.dia_de_hoje()
    )
    await redis.delete(chave)
    with patch.object(
        alerts_svc, "avisar_administradores", new=AsyncMock(return_value=False)
    ) as aviso:
        await custo.avisar_uma_vez_por_dia(tipo, "x", titulo="M20 teste: falha")
    aviso.assert_awaited_once()
    # A marca voltou: a próxima rodada tenta de novo.
    assert not await redis.exists(chave)


async def test_envio_sensivel_grava_no_sino(db: AsyncSession):
    from app.services.atendimento import alertas_sensiveis

    texto = alertas_sensiveis.frase_do_envio(
        "reembolso", "Kia", "shopee", "https://x/atendimento?conversa=1"
    )
    assert "<b>" not in texto
    assert await custo.avisar_adm(texto, titulo=alertas_sensiveis.TITULO_ENVIO)
    (a,) = await _alertas(db, alertas_sensiveis.TITULO_ENVIO)
    assert "Reembolso" in a.message and "conversa=1" in a.message


# ================================================================ marketing / DMs


async def _loja(db: AsyncSession, make_user, nome: str) -> Integration:
    dono = await make_user(role=UserRole.ADMIN)
    i = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def test_credito_baixo_vai_ao_sino_uma_vez_por_dia_com_a_loja(db: AsyncSession, make_user):
    from app.services.marketing import alerts as mkt

    loja = await _loja(db, make_user, "m20 loja credito")
    mkt._dedup.clear()
    await mkt.notify_low_credit(loja, credit=12.5)
    mkt._dedup.clear()  # a trava em memória passou: a dedupe do dia segura
    await mkt.notify_low_credit(loja, credit=10.0)
    await mkt.notify_low_credit(loja, credit=80.0)  # acima do limite: nada
    (a,) = await _alertas(db, "Shopee Ads: crédito baixo em m20 loja credito")
    assert a.escopo == ESCOPO_ADM and a.user_id is None
    assert a.integration_id == loja.id and a.plataforma == "shopee"
    assert a.conta == "m20 loja credito" and "R$ 12.50" in a.message


async def test_tres_falhas_seguidas_vao_ao_sino_com_o_commit_do_sync(db: AsyncSession, make_user):
    from app.services.marketing import alerts as mkt

    loja = await _loja(db, make_user, "m20 loja falhas")
    mkt._dedup.clear()
    for _ in range(4):
        await mkt.record_sync_failure(db, loja, code="erro_x", message="<b>falhou</b>")
    await db.commit()
    (a,) = await _alertas(db, "Shopee Ads: 3 falhas seguidas em m20 loja falhas")
    assert a.severity == AlertSeverity.ERROR and a.escopo == ESCOPO_ADM
    assert "erro_x" in a.message


async def test_dm_que_caiu_pra_humano_vai_ao_sino(db: AsyncSession):
    from app.models import DmConversa
    from app.services import dm_alerta

    chaves = [k async for k in redis.scan_iter("dm:alerta:*")]
    if chaves:
        await redis.delete(*chaves)
    conversa = DmConversa(id=uuid4(), conta="m20_conta_dm", participante_id="1")
    await dm_alerta.avisar(
        conversa, chave="pediu_humano", motivo="pediu atendente", texto="meu cpf 123.456.789-00"
    )
    (a,) = await _alertas(db, "DM: Pediu atendente · @m20_conta_dm")
    assert a.escopo == ESCOPO_ADM and a.user_id is None
    assert "123.456.789-00" not in a.message and "[CPF]" in a.message


# ================================================================ o script da reversão


async def test_script_da_reversao_avisa_no_sino(db: AsyncSession, monkeypatch):
    """`scripts/agente_reversao.py` (a rotina do Mac): o assunto que saiu do
    automático vira alerta ADM no sino (antes `custo.avisar_telegram`)."""
    from app.services.atendimento.agente import reversao
    from scripts import agente_reversao

    feita = {
        "canal": "c1",
        "assunto": "cancelamento",
        "motivo": "erro_critico",
        "enviaria": 3,
        "notas": 10,
        "criticos": 1,
    }

    async def _reverter(session, decididas, *, gravar=True):
        return [feita]

    monkeypatch.setattr(reversao, "decidir", lambda *a, **k: [])
    monkeypatch.setattr(reversao, "reverter", _reverter)
    resumo = await agente_reversao.rodar([], gravar=True, dias=7)
    assert resumo["canais"] == [feita]
    (a,) = await _alertas(db, reversao.TITULO_ALERTA)
    assert a.escopo == ESCOPO_ADM and a.user_id is None
    assert "cancelamento: erro_critico" in a.message

    # O dry-run não avisa.
    await agente_reversao.rodar([], gravar=False, dias=7)
    assert len(await _alertas(db, reversao.TITULO_ALERTA)) == 1
