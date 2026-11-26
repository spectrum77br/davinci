"""Chamado da Temu (26/11/2026): sem API de reclamação, a IA de Chamado confere a
tela uma vez por dia (`lembrar_temu`). Caso real: 306833, resgate aberto pela
tela da Temu ("Resgates em andamento"), registrado à mão nos Chamados."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem
from app.services import chamados_devolucao_sync as sync

LINK = (
    "https://br.seller.temu.com/return-details.html?parent_order_sn=PO-029-11708709484150003"
    "&parent_after_sales_sn=PO-029-11708709484150003-D01"
)


async def _chamado(db, *, criado: datetime, plataforma: str = "temu", **kw) -> Chamado:
    ch = Chamado(
        id=uuid4(),
        origem="devolucao",
        plataforma=plataforma,
        conta="barbosa",
        canal="manual",
        pedido_bling="306833",
        chamado="PO-029-11708709484150003-D01",
        chamado_url=LINK,
        created_at=criado,
        **kw,
    )
    db.add(ch)
    await db.commit()
    return ch


def _msg(ch: Chamado, *, tipo: str, texto: str, em: datetime, direcao: str = "sistema"):
    return ChamadoMensagem(
        chamado_id=ch.id,
        direcao=direcao,
        tipo=tipo,
        status="registrada",
        canal="manual",
        texto=texto,
        autor_nome="IA de Chamado" if tipo == "analise" else "sistema",
        created_at=em,
    )


async def _instrucoes(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "instrucao")
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    )


async def test_lembrete_temu_uma_vez_por_dia(db):
    agora = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    ch = await _chamado(db, criado=agora - timedelta(hours=10))

    # Menos de 1 dia desde o registro: nada.
    assert (await sync.lembrar_temu(db, agora=agora))["lembrados"] == 0
    # 1 dia: um lembrete — e não empilha enquanto a IA não ler.
    amanha = agora + timedelta(hours=15)
    assert (await sync.lembrar_temu(db, agora=amanha))["lembrados"] == 1
    assert (await sync.lembrar_temu(db, agora=amanha + timedelta(days=3)))["lembrados"] == 0
    [instr] = await _instrucoes(db, ch)
    assert instr.direcao == "sistema" and instr.autor_nome == "sistema"
    assert instr.texto.startswith(sync.LEMBRETE_TEMU_PREFIXO)
    assert LINK in instr.texto

    # A IA leu e mandou aguardar: o relógio zera a partir da análise.
    analisou = amanha + timedelta(minutes=10)
    db.add(
        _msg(
            ch,
            tipo="analise",
            em=analisou,
            texto="Análise da IA de Chamado [x]: Resgates em andamento → aguardar a plataforma",
        )
    )
    await db.commit()
    assert (await sync.lembrar_temu(db, agora=analisou + timedelta(hours=23)))["lembrados"] == 0
    assert (await sync.lembrar_temu(db, agora=analisou + timedelta(days=1)))["lembrados"] == 1


async def test_lembrete_temu_espera_a_pessoa_quando_a_ia_pede_gente(db):
    agora = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    ch = await _chamado(db, criado=agora - timedelta(days=3))
    db.add(
        _msg(
            ch,
            tipo="analise",
            em=agora - timedelta(days=2),
            texto="Análise da IA de Chamado [x]: a Temu pediu login → humano",
        )
    )
    await db.commit()
    assert (await sync.lembrar_temu(db, agora=agora))["lembrados"] == 0


async def test_lembrete_temu_pula_resolvido_fixo_e_outras_plataformas(db):
    agora = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    velho = agora - timedelta(days=5)
    await _chamado(db, criado=velho, resolvido=True)
    await _chamado(db, criado=velho, revisao_tipo="fixo", revisao_ate=date(2026, 12, 5))
    await _chamado(db, criado=velho, revisao_tipo="encerrar")
    await _chamado(db, criado=velho, plataforma="tiktok")
    # Resolvido, fixo com data no futuro, "encerrar" e TikTok saem já na consulta.
    assert await sync.lembrar_temu(db, agora=agora) == {"abertos": 0, "lembrados": 0}


async def test_lembrete_geral_de_2_dias_deixa_a_temu_pro_diario(db):
    agora = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    ch = await _chamado(db, criado=agora - timedelta(days=5))
    db.add(
        _msg(
            ch,
            tipo="analise",
            em=agora - timedelta(days=3),
            texto="Análise da IA de Chamado [x]: em análise → aguardar a plataforma",
        )
    )
    await db.commit()
    assert (await sync.lembrar_recursos(db, agora=agora))["lembrados"] == 0
    assert (await sync.lembrar_temu(db, agora=agora))["lembrados"] == 1


# ---- a IA abre o resgate sozinha (como a SAFE-T da Amazon) ------------------


async def _temu_lancado(db, *, pedido: str = "306833", condicao: str = "Extraviado") -> Chamado:
    from app.models import Devolution, Logistica

    dev = Devolution(
        id=uuid4(),
        pedido_bling=pedido,
        pedido_marketplace="PO-029-11708709484150003",
        conta="Temu Barbosa",
        sku="b012.12.20",
        produtos="Kit Malas Lisa M2",
        condicao_produto=condicao,
        motivo_devolucao="Danificado (Outros)",
    )
    db.add(dev)
    db.add(
        Logistica(
            pedido_bling=pedido,
            plataforma="Temu",
            conta="barbosa",
            rastreio="AJ261102157415301",
            meli_status={},
        )
    )
    ch = Chamado(
        id=uuid4(),
        origem="devolucao",
        origem_ref=str(dev.id),
        plataforma="temu",
        conta="barbosa",
        canal="manual",
        pedido_bling=pedido,
        pedido_marketplace="PO-029-11708709484150003",
    )
    db.add(ch)
    await db.flush()
    db.add(
        ChamadoMensagem(
            chamado_id=ch.id,
            direcao="enviada",
            tipo="abertura",
            status="registrada",
            canal="manual",
            autor_nome="sistema",
            erro="plataforma_sem_api",
            texto="temu não tem API pra contestar devolução: abrir na mão na plataforma.",
        )
    )
    await db.commit()
    return ch


async def test_temu_lancado_ganha_instrucao_de_abrir_o_resgate_uma_vez(db):
    ch = await _temu_lancado(db)  # Extraviado ENTRA (ao contrário da SAFE-T)
    agora = datetime.now(UTC)
    assert (await sync.instruir_resgate_temu(db, agora=agora))["instruidos"] == 0

    depois = agora + sync.INSTRUCAO_RESGATE_TEMU_ESPERA + timedelta(minutes=1)
    assert (await sync.instruir_resgate_temu(db, agora=depois))["instruidos"] == 1
    assert (await sync.instruir_resgate_temu(db, agora=depois))["instruidos"] == 0
    [m] = await _instrucoes(db, ch)
    assert m.texto.startswith(sync.INSTRUCAO_RESGATE_TEMU_PREFIXO)
    assert "PO-029-11708709484150003" in m.texto and "barbosa" in m.texto
    assert "anjunexpress.com/trackPackage?tracking=AJ261102157415301" in m.texto
    assert "Extraviado" in m.texto and "Reivindicável" in m.texto
    # A SAFE-T (Amazon) não pega a Temu.
    assert (await sync.instruir_safet_amazon(db, agora=depois))["instruidos"] == 0

    # Um dia depois sem a IA ter aberto (sem nº): o lembrete pede pra ver se já dá.
    db.add(
        _msg(
            ch,
            tipo="analise",
            em=depois + timedelta(minutes=5),
            texto="Análise da IA de Chamado [x]: Temu ainda não descontou → aguardar a plataforma",
        )
    )
    await db.commit()
    assert (await sync.lembrar_temu(db, agora=depois + timedelta(days=1, minutes=6)))[
        "lembrados"
    ] == 1
    ultimo = (await _instrucoes(db, ch))[-1]
    assert "ainda não foi aberto" in ultimo.texto
