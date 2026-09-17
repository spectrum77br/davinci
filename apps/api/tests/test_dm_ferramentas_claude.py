"""Ferramentas de DM do conector MCP (Eduardo, 16/09/2026).

"fazer a ligação com o claude ali que precisamos."

O princípio que estes testes protegem: o Claude ENFILEIRA, o servidor DECIDE.
As regras da plataforma e do CDC são validação em código — nunca instrução no
prompt, que é texto e não trava nada.

O mais importante aqui é `test_recusa_*`: são as frases que a resposta NÃO
pode conter. Preço, prazo e frete ditos em canal de atendimento vinculam a
empresa (CDC art. 30 e 35), e "foi o robô" não é defesa (art. 34).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DmConversa, DmMensagem, Marca, RedeSocial, UserRole
from app.services.claude_tarefas import (
    TarefaInvalidaError,
    listar_dms,
    responder_dm,
)


async def _conversa(db: AsyncSession, *, recebida_ha_horas: float = 1.0) -> DmConversa:
    m = Marca(nome="Charlots", slug="charlots-dmtool")
    db.add(m)
    await db.flush()
    r = RedeSocial(marca_id=m.id, plataforma="instagram", conta="charlots_br", ativo=True)
    db.add(r)
    await db.flush()
    c = DmConversa(
        rede_social_id=r.id,
        plataforma="instagram",
        conta="charlots_br",
        participante_id="9876543210",
        ultima_recebida_em=datetime.now(UTC) - timedelta(hours=recebida_ha_horas),
    )
    db.add(c)
    await db.flush()
    db.add(
        DmMensagem(
            conversa_id=c.id,
            mid="mid.pergunta",
            direcao="recebida",
            tipo="texto",
            texto="Vocês têm essa mala em preto?",
            ocorrido_em=c.ultima_recebida_em,
        )
    )
    await db.commit()
    await db.refresh(c)
    return c


# ─────────────────── a trava: o que NÃO pode ser dito ───────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Essa mala sai por R$ 299,00",
        "O valor é 299,90 à vista",
        "Temos 15% de desconto essa semana",
        "Chega em 5 dias úteis",
        "O frete é grátis pro Brasil todo",
        "A garantia é de 90 dias contra defeito",
        "Chega até sexta na sua casa",
    ],
)
@pytest.mark.asyncio
async def test_recusa_promessa_que_vincula(db: AsyncSession, make_user, texto: str):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)
    with pytest.raises(TarefaInvalidaError) as e:
        await responder_dm(db, dono=dono, args={"conversa_id": str(c.id), "texto": texto})
    assert "CDC" in str(e.value)

    # e nada foi enfileirado
    saida = await db.scalar(
        select(DmMensagem).where(
            DmMensagem.conversa_id == c.id, DmMensagem.direcao == "enviada"
        )
    )
    assert saida is None


@pytest.mark.asyncio
async def test_recusa_texto_longo_demais(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)
    with pytest.raises(TarefaInvalidaError) as e:
        await responder_dm(
            db, dono=dono, args={"conversa_id": str(c.id), "texto": "ã" * 600}
        )
    # 600 caracteres acentuados = 1200 bytes: estoura o teto da plataforma.
    assert "bytes" in str(e.value)


# ─────────────────── o caminho feliz, em modo seco ───────────────────


@pytest.mark.asyncio
async def test_resposta_valida_fica_em_seco(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)

    out = await responder_dm(
        db,
        dono=dono,
        args={
            "conversa_id": str(c.id),
            "texto": (
                "Oi! As cores mudam de um modelo para o outro. Me diz qual mala "
                "você viu que o time confirma no WhatsApp (11) 93000-0710."
            ),
        },
    )
    assert "SECO" in out  # dm_resposta_commit=False é o padrão

    saida = await db.scalar(
        select(DmMensagem).where(
            DmMensagem.conversa_id == c.id, DmMensagem.direcao == "enviada"
        )
    )
    assert saida is not None
    assert saida.status == "seco"  # existe, passou nas checagens, NÃO saiu


@pytest.mark.asyncio
async def test_uma_resposta_em_voo_por_conversa(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)
    db.add(
        DmMensagem(
            conversa_id=c.id, direcao="enviada", tipo="texto", texto="já em voo",
            status="pendente",
        )
    )
    await db.commit()

    with pytest.raises(TarefaInvalidaError) as e:
        await responder_dm(
            db, dono=dono, args={"conversa_id": str(c.id), "texto": "segunda resposta"}
        )
    assert "em voo" in str(e.value)


# ─────────────────── as regras da Meta, em código ───────────────────


@pytest.mark.asyncio
async def test_fora_da_janela_vai_pra_humano(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db, recebida_ha_horas=30)  # passou das 24h

    with pytest.raises(TarefaInvalidaError) as e:
        await responder_dm(db, dono=dono, args={"conversa_id": str(c.id), "texto": "oi"})
    assert "janela" in str(e.value)

    await db.refresh(c)
    assert c.status == "humano"  # não fica no limbo: alguém tem que pegar


@pytest.mark.asyncio
async def test_conversa_com_humano_nao_aceita_robo(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)
    c.auto = False
    await db.commit()

    with pytest.raises(TarefaInvalidaError) as e:
        await responder_dm(db, dono=dono, args={"conversa_id": str(c.id), "texto": "oi"})
    assert "humano" in str(e.value)


@pytest.mark.asyncio
async def test_escalar_desliga_o_robo(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    c = await _conversa(db)

    await responder_dm(db, dono=dono, args={"conversa_id": str(c.id), "escalar": True})
    await db.refresh(c)
    assert c.auto is False
    assert c.status == "humano"


# ─────────────────── acesso ───────────────────


@pytest.mark.asyncio
async def test_so_admin(db: AsyncSession, make_user):
    comum = await make_user(role=UserRole.USER)
    c = await _conversa(db)
    # DM de cliente é dado pessoal de terceiro: não é para qualquer usuário.
    with pytest.raises(TarefaInvalidaError):
        await listar_dms(db, dono=comum, args={})
    with pytest.raises(TarefaInvalidaError):
        await responder_dm(db, dono=comum, args={"conversa_id": str(c.id), "texto": "oi"})


@pytest.mark.asyncio
async def test_listar_mostra_a_conversa(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    await _conversa(db)
    out = await listar_dms(db, dono=dono, args={})
    assert "charlots_br" in out
    assert "Vocês têm essa mala em preto?" in out
    assert "janela" in out
