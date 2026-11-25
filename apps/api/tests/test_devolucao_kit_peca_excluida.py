"""Kit de mala com peça excluída no Bling (pedido 308307, 25/11/2026).

A b027.24 foi excluída no Bling em 18/11 e o Bling a tirou da estrutura do kit
b027.12.24: o cache `bling_kit_components` ficou só com a b027.12 e a busca da
devolução mostrava só a mala 12. O SKU do kit diz os tamanhos (12 + 24): a
peça que a composição de hoje perdeu volta como unidade própria."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingKitComponent, BlingOrder, Devolution, Product
from app.routers.devolutions import _tamanhos_sem_peca
from app.services import chamados as svc_chamados

KIT_BID, MALA12_BID, MALA24_BID = 730830701, 730830702, 730830703


def _c(sku: str, qty: int = 1) -> dict:
    return {"sku": sku, "name": None, "cost": None, "qty": qty}


@pytest.mark.parametrize(
    ("kit", "comps", "faltam"),
    [
        # 308307: a 24 sumiu da composição
        ("b027.12.24", [_c("b027.12")], ["b027.24"]),
        # composição completa: nada a acrescentar
        ("b027.12.24", [_c("b027.12"), _c("b027.24")], []),
        # peça de outra base (alça/capa) não conta como tamanho
        ("b028.12.24", [_c("b028.12"), _c("z002800.mala")], ["b028.24"]),
        (
            "b028.10.12.20.24",
            [_c("b028.10"), _c("b028.12"), _c("b028.20"), _c("z002800.mala")],
            ["b028.24"],
        ),
        # o tamanho vale pelo número: b027.24us cobre o 24
        ("b027.12.24", [_c("b027.12"), _c("b027.24us")], []),
        # quantidade conta: duas 24 numa linha da composição
        ("b056.24.24", [_c("b056.24", 2)], []),
        ("b056.24.24", [_c("b056.24", 1)], ["b056.24"]),
        # maiúscula/espaço no SKU não atrapalham
        (" B027.12.24 ", [_c("B027.12")], ["b027.24"]),
        # não é mala com tamanhos no SKU: não mexe
        ("b011", [_c("b011.8")], []),
        ("b027.12.sp", [_c("b027.12")], []),
        ("dg015.pi+a001.pi", [_c("a001.pi")], []),
        ("", [], []),
    ],
)
def test_tamanhos_sem_peca(kit, comps, faltam):
    assert _tamanhos_sem_peca(kit, comps) == faltam


async def _kit_b027(db: AsyncSession, user_id) -> None:
    db.add_all(
        [
            Product(
                user_id=user_id,
                sku="b027.12.24",
                name="Kit Malas Chanfrada M3 12.24 - Branca",
                bling_product_id=KIT_BID,
                formato="E",
                situacao="E",
                cost_price=167,
            ),
            Product(
                user_id=user_id,
                sku="b027.12",
                name="Mala Chanfrada M3 tamanho 12 - Branca",
                bling_product_id=MALA12_BID,
                formato="S",
                situacao="A",
                cost_price=41,
            ),
            Product(
                user_id=user_id,
                sku="b027.24",
                name="Mala Chanfrada M3 tamanho 24 - Branca",
                bling_product_id=MALA24_BID,
                formato="S",
                situacao="E",
                cost_price=126,
            ),
            # a composição de HOJE no Bling: só a 12 (a 24 foi excluída)
            BlingKitComponent(
                kit_bling_product_id=KIT_BID, component_bling_product_id=MALA12_BID, quantidade=1
            ),
        ]
    )
    await db.commit()


async def _limpar_composicao(db: AsyncSession) -> None:
    # `bling_kit_components` não está na limpeza do conftest
    await db.execute(
        delete(BlingKitComponent).where(BlingKitComponent.kit_bling_product_id == KIT_BID)
    )
    await db.commit()


@pytest.mark.asyncio
async def test_busca_da_devolucao_mostra_a_mala_excluida(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(
        permissions={"devolucoes": {"view": True, "edit": False, "delete": False}}
    )
    auth_as(user)
    schema = get_settings().database_schema
    item_id = UUID("30830700-0000-0000-0000-000000000001")
    await _kit_b027(db, user.id)
    await db.execute(
        text(
            "INSERT INTO bling_orders (id, numero, numeroloja, preco_custo) "
            "VALUES (:id, '308307', '701-7579405-1188228', 167)"
        ),
        {"id": item_id},
    )
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES (
                '2026-11-10T13:18:00+00:00'::timestamptz, '308307'::text,
                '701-7579405-1188228'::text,
                'Amazon Poofy'::text, NULL::bigint, 'b027.12.24'::text,
                'Kit Malas Chanfrada M3 tamanho 12.24 Polegadas - Branca'::text, 1::integer,
                '{item_id}'::uuid, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                NULL::text, NULL::text, NULL::text
            )) AS t(
                data, pedido_bling, pedido_marketplace, loja_nome, bling_loja_id, sku, produto,
                quantidade, bling_order_item_id, nome_destinatario, cep_destino, endereco_destino,
                numero_destino, complemento_destino, bairro_destino, cidade_destino, uf_destino
            )
            """  # noqa: S608
        )
    )
    await db.commit()
    try:
        resp = await client.get("/api/devolutions/order-lookup?pedido=308307")
    finally:
        await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
        await db.commit()
        await _limpar_composicao(db)

    assert resp.status_code == 200, resp.text
    linhas = [(r["sku"], r["produtos"], r["custo_produto"]) for r in resp.json()]
    assert linhas == [
        ("b027.12", "Mala Chanfrada M3 tamanho 12 - Branca", 41),
        ("b027.24", "Mala Chanfrada M3 tamanho 24 - Branca", 126),
    ]
    assert not any(r["ja_devolvido"] for r in resp.json())


@pytest.mark.asyncio
async def test_chamado_da_mala_excluida_vai_com_o_kit(db: AsyncSession, make_user):
    """O chamado acha de que linha do pedido a peça veio pelo mesmo splitter: a
    b027.24 (fora da composição de hoje) também é do kit b027.12.24."""
    dono = await make_user()
    await _kit_b027(db, dono.id)
    for i, (codigo, descricao) in enumerate(
        (("b027.12.24", "Kit Malas Chanfrada M3 12.24 - Branca"), ("a001", "Cadeado"))
    ):
        db.add(
            BlingOrder(
                numero="308307",
                numeroloja="701-7579405-1188228",
                loja="L1",
                data=datetime(2026, 11, 10, tzinfo=UTC),
                total=228.2,
                situacao="83957",
                item_index=i,
                item_codigo=codigo,
                item_descricao=descricao,
                item_quantidade=1,
            )
        )
    dev = Devolution(
        pedido_bling="308307",
        pedido_marketplace="701-7579405-1188228",
        conta="Amazon Poofy",
        sku="b027.24",
        produtos="Mala Chanfrada M3 tamanho 24 - Branca",
        condicao_produto="Novo",
        motivo_devolucao="Devolvido ao vendedor",
    )
    db.add(dev)
    await db.commit()
    try:
        assert await svc_chamados.item_do_pedido(db, dev) == (
            "Kit Malas Chanfrada M3 12.24 - Branca",
            "b027.12.24",
        )
    finally:
        await _limpar_composicao(db)
