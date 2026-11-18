"""17/11/2026 (Vinicius, 298756/304961): chamado de devolução da Amazon nasce só
com o aviso "abrir a SAFE-T na mão" e ficava parado — a IA de Chamado não acordava.
`instruir_safet_amazon` (cron :25) deixa UMA instrução do sistema pra ela abrir a
SAFE-T na tela; Extraviado fica de fora (cobrança da transportadora)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados_devolucao_sync as sync


async def _amazon(
    db,
    *,
    pedido: str,
    condicao: str | None = "Danificado (Outros)",
    motivo: str = "Danificado (Outros)",
    plataforma: str = "amazon",
    **campos,
) -> Chamado:
    dev = Devolution(
        id=uuid4(), pedido_bling=pedido, pedido_marketplace=f"701-{pedido}",
        conta="Amazon Kia", sku="dg018.pi", produtos="Uranyx F109",
        condicao_produto=condicao, motivo_devolucao=motivo,
    )
    db.add(dev)
    ch = Chamado(
        id=uuid4(), origem="devolucao", origem_ref=str(dev.id), plataforma=plataforma,
        conta="Amazon Kia", canal="manual", pedido_bling=pedido,
        pedido_marketplace=f"701-{pedido}", **campos,
    )
    db.add(ch)
    await db.flush()
    db.add(
        ChamadoMensagem(
            chamado_id=ch.id, direcao="enviada", tipo="abertura", status="registrada",
            canal="manual", autor_nome="sistema", erro="plataforma_sem_api",
            texto="A Amazon não tem API pra contestar devolução: abrir a reivindicação SAFE-T…",
        )
    )
    await db.commit()
    return ch


async def _instrucoes(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "instrucao"
                )
            )
        ).scalars().all()
    )


async def test_amazon_parado_ganha_instrucao_de_abrir_safet_uma_vez(db):
    ch = await _amazon(db, pedido="304961")
    agora = datetime.now(UTC)

    # recém-aberto: espera a equipe terminar o lançamento (fotos)
    assert (await sync.instruir_safet_amazon(db, agora=agora))["instruidos"] == 0

    depois = agora + sync.INSTRUCAO_SAFET_ESPERA + timedelta(minutes=1)
    assert (await sync.instruir_safet_amazon(db, agora=depois))["instruidos"] == 1
    assert (await sync.instruir_safet_amazon(db, agora=depois))["instruidos"] == 0

    instr = await _instrucoes(db, ch)
    assert len(instr) == 1
    m = instr[0]
    assert m.autor_nome == "sistema" and m.direcao == "sistema" and m.status == "registrada"
    assert m.texto.startswith(sync.INSTRUCAO_SAFET_PREFIXO)
    assert "SAFE-T" in m.texto and "701-304961" in m.texto and "Amazon Kia" in m.texto
    assert "Danificado (Outros)" in m.texto and "dg018.pi Uranyx F109" in m.texto
    assert "já cobrou o reembolso" in m.texto


async def test_extraviado_nao_ganha_instrucao(db):
    """298756: entrega suspensa e o pacote sumiu nos Correios na volta — a cobrança
    é do Melhor Envio, não SAFE-T."""
    ch = await _amazon(db, pedido="298756", condicao="Extraviado", motivo="Não recebido")
    depois = datetime.now(UTC) + timedelta(hours=2)
    r = await sync.instruir_safet_amazon(db, agora=depois)
    assert r["instruidos"] == 0 and r["extraviados"] == 1
    assert await _instrucoes(db, ch) == []


async def test_kit_com_um_item_extraviado_instrui_so_o_que_voltou(db):
    ch = await _amazon(db, pedido="305001", condicao="Danificado (Outros)")
    db.add(
        Devolution(
            id=uuid4(), pedido_bling="305001", pedido_marketplace="701-305001",
            conta="Amazon Kia", sku="mala.p", produtos="Mala P",
            condicao_produto="Extraviado", motivo_devolucao="Não recebido",
        )
    )
    await db.commit()
    depois = datetime.now(UTC) + timedelta(hours=2)
    assert (await sync.instruir_safet_amazon(db, agora=depois))["instruidos"] == 1
    (m,) = await _instrucoes(db, ch)
    assert "dg018.pi" in m.texto and "mala.p" not in m.texto


async def test_fica_de_fora_quem_ja_tem_dono(db):
    depois = datetime.now(UTC) + timedelta(hours=2)
    com_numero = await _amazon(db, pedido="305101", chamado="17991-75373-0306254")
    concluido = await _amazon(db, pedido="305102", resolvido=True)
    encerrar = await _amazon(db, pedido="305103", revisao_tipo="encerrar")
    fixo = await _amazon(
        db, pedido="305104", revisao_tipo="fixo", revisao_ate=(depois + timedelta(days=3)).date()
    )
    ml = await _amazon(db, pedido="305105", plataforma="mercado livre")
    com_instrucao = await _amazon(db, pedido="305106")
    com_analise = await _amazon(db, pedido="305107")
    for ch, tipo in ((com_instrucao, "instrucao"), (com_analise, "analise")):
        db.add(
            ChamadoMensagem(
                chamado_id=ch.id, direcao="sistema", tipo=tipo, status="registrada",
                canal="manual", autor_nome="cairo sa", texto="Pedido da equipe: …",
            )
        )
    await db.commit()

    assert (await sync.instruir_safet_amazon(db, agora=depois))["instruidos"] == 0
    for ch in (com_numero, concluido, encerrar, fixo, ml):
        assert await _instrucoes(db, ch) == []
    assert len(await _instrucoes(db, com_instrucao)) == 1  # só a da equipe
