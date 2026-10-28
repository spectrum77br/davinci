"""Chamado de devolução de KIT (28/10/2026, pedido 299959): o chamado é um por
pedido e vai com o nome do kit vendido, não com a primeira peça lançada."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import (
    BlingKitComponent,
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    Devolution,
    Product,
    UserRole,
)
from app.services import chamados as svc

pytestmark = pytest.mark.asyncio

KIT = "Kit 6 Malas Lisa M2 - Cinza Escuro"


async def _pedido(db, numero="299959", itens=(("b021", KIT),)):
    for i, (codigo, descricao) in enumerate(itens):
        db.add(
            BlingOrder(
                numero=numero,
                numeroloja="2000015257260637",
                loja="L1",
                data=datetime(2026, 9, 29, tzinfo=UTC),
                total=788.5,
                situacao="15",
                item_index=i,
                item_codigo=codigo,
                item_descricao=descricao,
                item_quantidade=1,
            )
        )
    await db.commit()


async def _kit_no_bling(db, dono, base=900):
    """b021 (kit) = b021.12 ×2 + b021.18, como no cache da composição do Bling."""
    for sku, nome, bid in (
        ("b021", KIT, base),
        ("b021.12", "Mala Lisa M2 tamanho 12 - Cinza Escuro", base + 1),
        ("b021.18", "Mala Lisa M2 tamanho 18 - Cinza Escuro", base + 2),
    ):
        db.add(Product(user_id=dono.id, sku=sku, name=nome, bling_product_id=bid))
    db.add_all(
        [
            BlingKitComponent(
                kit_bling_product_id=base, component_bling_product_id=base + 1, quantidade=2
            ),
            BlingKitComponent(
                kit_bling_product_id=base, component_bling_product_id=base + 2, quantidade=1
            ),
        ]
    )
    await db.commit()


async def _linha(
    db, sku="b021.12", produtos="Mala Lisa M2 tamanho 12 - Cinza Escuro", numero="299959"
):
    dev = Devolution(
        pedido_bling=numero,
        pedido_marketplace="2000015257260637",
        conta="ML Marquezini",
        sku=sku,
        produtos=produtos,
        condicao_produto="Extraviado",
        motivo_devolucao="Golpe",
    )
    db.add(dev)
    await db.commit()
    return dev


async def test_chamado_do_kit_vai_com_o_nome_do_kit(db, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    await _kit_no_bling(db, dono)
    await _pedido(db)
    dev = await _linha(db)
    ch = await svc.abrir_chamado_devolucao(db, dev)
    await db.commit()
    assert (ch.produto, ch.sku) == (KIT, "b021")
    # A segunda peça do mesmo kit não abre outro chamado (um por pedido).
    outra = await _linha(db, sku="b021.18", produtos="Mala Lisa M2 tamanho 18 - Cinza Escuro")
    assert await svc.abrir_chamado_devolucao(db, outra) is None


async def test_kit_fora_do_cache_usa_a_unica_linha_do_pedido(db):
    await _pedido(db)  # sem bling_kit_components
    dev = await _linha(db)
    assert await svc.item_do_pedido(db, dev) == (KIT, "b021")


async def test_sku_com_mais_e_mala_com_varios_tamanhos(db):
    await _pedido(
        db,
        numero="400001",
        itens=(("dg053.ci+a001.ci", "Celular U1 + Capa"), ("b004.12.18", "Kit 2 Malas b004")),
    )
    capa = await _linha(db, sku="a001.ci", produtos="Capa", numero="400001")
    mala = await _linha(db, sku="b004.18", produtos="Mala 18", numero="400001")
    assert await svc.item_do_pedido(db, capa) == ("Celular U1 + Capa", "dg053.ci+a001.ci")
    assert await svc.item_do_pedido(db, mala) == ("Kit 2 Malas b004", "b004.12.18")


async def test_produto_que_e_item_do_pedido_nao_muda(db):
    await _pedido(db, numero="400002", itens=(("b021.12", "Mala 12"), ("a001", "Cadeado")))
    dev = await _linha(db, sku="b021.12", produtos="Mala 12", numero="400002")
    assert await svc.item_do_pedido(db, dev) is None
    ch = await svc.abrir_chamado_devolucao(db, dev)
    await db.commit()
    assert (ch.produto, ch.sku) == ("Mala 12", "b021.12")


async def test_correcao_dos_chamados_ja_abertos(db, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    await _kit_no_bling(db, dono, base=950)
    await _pedido(db)
    dev = await _linha(db)
    # O chamado antigo (antes desta mudança): com a peça.
    antigo = Chamado(
        pedido_bling="299959",
        produto=dev.produtos,
        sku=dev.sku,
        origem="devolucao",
        origem_ref=str(dev.id),
        canal="manual",
    )
    # E um que uma pessoa editou: fica como está.
    await _pedido(db, numero="299960")
    dev2 = await _linha(db, numero="299960")
    editado = Chamado(
        pedido_bling="299960",
        produto="Escrito por alguém",
        sku=dev2.sku,
        origem="devolucao",
        origem_ref=str(dev2.id),
        canal="manual",
    )
    db.add_all([antigo, editado])
    await db.commit()
    assert await svc.corrigir_kits_dos_chamados(db) == 1
    await db.commit()
    assert (antigo.produto, antigo.sku) == (KIT, "b021")
    assert editado.produto == "Escrito por alguém"
    msgs = list(
        (
            await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == antigo.id))
        ).scalars()
    )
    assert any("o kit Kit 6 Malas" in (m.texto or "") for m in msgs)
    # Rodar de novo não muda nada.
    assert await svc.corrigir_kits_dos_chamados(db) == 0
