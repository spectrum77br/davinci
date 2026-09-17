"""Envio das respostas de DM (Eduardo, 16/09/2026).

O que estes testes protegem, em ordem de quanto dói se quebrar:

  1. MODO SECO não chama a Meta. É o que permite rodar uma semana em
     produção, contra DM real, lendo o que o robô TERIA dito.
  2. AMBÍGUO nunca retenta. Aqui não existe `container_id` pra perguntar
     "será que saiu?" — post se apaga, DM não se desvê.
  3. As guardas rodam DE NOVO na hora do envio: entre enfileirar e enviar a
     janela pode ter vencido ou um humano pode ter assumido.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DmConta, DmConversa, DmMensagem, Marca, RedeSocial
from app.security.cipher import encrypt_json
from app.services import instagram_dm
from app.services.marketing.meta_client import ResultadoMensagem

IG_CONTA = "17841400000000000"
IGSID = "9876543210"


async def _cenario(
    db: AsyncSession,
    *,
    recebida_ha_horas: float = 1.0,
    dm_auto: bool = True,
    texto: str = "Oi! Me diz qual modelo você viu que o time confirma.",
) -> tuple[DmConversa, DmMensagem]:
    m = Marca(nome="Charlots", slug=f"charlots-env-{recebida_ha_horas}-{dm_auto}")
    db.add(m)
    await db.flush()
    r = RedeSocial(
        marca_id=m.id, plataforma="instagram", conta="charlots_br", ativo=True, dm_auto=dm_auto
    )
    db.add(r)
    await db.flush()
    db.add(
        DmConta(
            rede_social_id=r.id,
            ig_user_id=IG_CONTA,
            status="ok",
            token_enc=encrypt_json({"access_token": "tok", "expires_at": None, "scopes": []}),
        )
    )
    c = DmConversa(
        rede_social_id=r.id,
        plataforma="instagram",
        conta="charlots_br",
        participante_id=IGSID,
        ultima_recebida_em=datetime.now(UTC) - timedelta(hours=recebida_ha_horas),
    )
    db.add(c)
    await db.flush()
    msg = DmMensagem(
        conversa_id=c.id, direcao="enviada", tipo="texto", texto=texto, status="pendente"
    )
    db.add(msg)
    await db.commit()
    await db.refresh(c)
    await db.refresh(msg)
    return c, msg


# ─────────────────────────── modo seco ───────────────────────────


@pytest.mark.asyncio
async def test_modo_seco_nao_chama_a_meta(db: AsyncSession):
    c, msg = await _cenario(db)
    assert get_settings().dm_resposta_commit is False  # o padrão

    with patch.object(instagram_dm.meta_client, "enviar_dm", new=AsyncMock()) as envio:
        await instagram_dm.enviar(db, msg)

    envio.assert_not_awaited()  # NADA saiu
    await db.refresh(msg)
    assert msg.status == "seco"
    assert msg.texto  # o texto fica gravado: é o material de avaliação


@pytest.mark.asyncio
async def test_allowlist_envia_de_verdade_mesmo_em_seco(db: AsyncSession, monkeypatch):
    """A Meta não tem sandbox de DM — a allowlist é o sandbox."""
    c, msg = await _cenario(db)
    s = get_settings()
    monkeypatch.setattr(s, "dm_resposta_allowlist", IGSID, raising=False)

    with patch.object(
        instagram_dm.meta_client,
        "enviar_dm",
        new=AsyncMock(return_value=ResultadoMensagem(ok=True, mid="mid.saiu")),
    ) as envio:
        await instagram_dm.enviar(db, msg)

    envio.assert_awaited_once()
    await db.refresh(msg)
    assert msg.status == "enviada"
    assert msg.mid == "mid.saiu"
    await db.refresh(c)
    assert c.status == "respondida"


# ─────────────────────── a regra que endurece ───────────────────────


@pytest.mark.asyncio
async def test_ambiguo_vai_pra_revisar_e_nao_retenta(db: AsyncSession, monkeypatch):
    """Timeout: a mensagem PODE ter saído, e não há como perguntar.

    Na postagem existe `container_id` e dá pra reconciliar. Aqui não — então
    ambíguo sai da fila e vai pra humano, sem segunda tentativa.
    """
    c, msg = await _cenario(db)
    monkeypatch.setattr(get_settings(), "dm_resposta_allowlist", IGSID, raising=False)

    with patch.object(
        instagram_dm.meta_client,
        "enviar_dm",
        new=AsyncMock(return_value=ResultadoMensagem(ok=False, erro="timeout", ambiguo=True)),
    ):
        await instagram_dm.enviar(db, msg)

    await db.refresh(msg)
    assert msg.status == "revisar"
    assert "ambíguo" in (msg.motivo or "")
    await db.refresh(c)
    assert c.auto is False  # robô sai da conversa
    assert c.status == "humano"

    # e não volta pra fila: o lease não a enxerga mais
    restantes = await instagram_dm.proximas_para_enviar(db)
    assert restantes == []


@pytest.mark.asyncio
async def test_fora_da_janela_nao_envia(db: AsyncSession, monkeypatch):
    c, msg = await _cenario(db, recebida_ha_horas=30)
    monkeypatch.setattr(get_settings(), "dm_resposta_allowlist", IGSID, raising=False)

    with patch.object(instagram_dm.meta_client, "enviar_dm", new=AsyncMock()) as envio:
        await instagram_dm.enviar(db, msg)

    envio.assert_not_awaited()
    await db.refresh(msg)
    assert msg.status == "falhou"
    assert "janela" in (msg.motivo or "")
    await db.refresh(c)
    assert c.status == "humano"


@pytest.mark.asyncio
async def test_conta_com_dm_auto_desligado_nao_envia(db: AsyncSession, monkeypatch):
    c, msg = await _cenario(db, dm_auto=False)
    monkeypatch.setattr(get_settings(), "dm_resposta_allowlist", IGSID, raising=False)

    with patch.object(instagram_dm.meta_client, "enviar_dm", new=AsyncMock()) as envio:
        await instagram_dm.enviar(db, msg)

    envio.assert_not_awaited()
    await db.refresh(msg)
    assert msg.status == "falhou"
    assert "desligada" in (msg.motivo or "")


@pytest.mark.asyncio
async def test_humano_assumiu_entre_enfileirar_e_enviar(db: AsyncSession, monkeypatch):
    """A guarda roda de novo NA HORA — não só quando enfileirou."""
    c, msg = await _cenario(db)
    monkeypatch.setattr(get_settings(), "dm_resposta_allowlist", IGSID, raising=False)
    c.auto = False  # alguém respondeu pela caixa de entrada nesse meio-tempo
    await db.commit()

    with patch.object(instagram_dm.meta_client, "enviar_dm", new=AsyncMock()) as envio:
        await instagram_dm.enviar(db, msg)

    envio.assert_not_awaited()
    await db.refresh(msg)
    assert msg.status == "falhou"
    assert "humano" in (msg.motivo or "")


# ─────────────────────────── a fila ───────────────────────────


@pytest.mark.asyncio
async def test_lease_marca_enviando_e_nao_repete(db: AsyncSession):
    await _cenario(db)

    primeira = await instagram_dm.proximas_para_enviar(db)
    assert len(primeira) == 1
    assert primeira[0].status == "enviando"  # claim durável

    # um segundo tick não pega a mesma linha
    segunda = await instagram_dm.proximas_para_enviar(db)
    assert segunda == []


@pytest.mark.asyncio
async def test_banco_impede_duas_respostas_em_voo(db: AsyncSession):
    """O índice parcial `uq_dm_resposta_em_voo` é a trava de verdade.

    Não é a aplicação que impede responder duas vezes à mesma pessoa — é o
    banco. Dois workers podem tentar; só um grava.
    """
    c, _ = await _cenario(db)
    db.add(
        DmMensagem(
            conversa_id=c.id, direcao="enviada", tipo="texto", texto="segunda", status="pendente"
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_pode_enviar_respeita_commit_e_allowlist(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dm_resposta_commit", False, raising=False)
    monkeypatch.setattr(s, "dm_resposta_allowlist", "", raising=False)
    assert instagram_dm.pode_enviar(IGSID) is False

    monkeypatch.setattr(s, "dm_resposta_allowlist", f"111, {IGSID} ,222", raising=False)
    assert instagram_dm.pode_enviar(IGSID) is True
    assert instagram_dm.pode_enviar("outro") is False

    monkeypatch.setattr(s, "dm_resposta_commit", True, raising=False)
    monkeypatch.setattr(s, "dm_resposta_allowlist", "", raising=False)
    assert instagram_dm.pode_enviar("qualquer") is True


@pytest.mark.asyncio
async def test_falha_com_codigo_nao_escala_na_primeira(db: AsyncSession, monkeypatch):
    """Erro COM código é recusa da Meta: a mensagem não saiu, dá pra retentar."""
    c, msg = await _cenario(db)
    monkeypatch.setattr(get_settings(), "dm_resposta_allowlist", IGSID, raising=False)

    with patch.object(
        instagram_dm.meta_client,
        "enviar_dm",
        new=AsyncMock(return_value=ResultadoMensagem(ok=False, erro="rate limit", code=613)),
    ):
        await instagram_dm.enviar(db, msg)

    await db.refresh(msg)
    assert msg.status == "falhou"
    assert msg.attempts == 1
    await db.refresh(c)
    assert c.auto is True  # ainda não desistiu da conversa
