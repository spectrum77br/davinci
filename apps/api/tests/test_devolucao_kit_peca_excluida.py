"""Kit de mala com peça excluída no Bling (pedido 308307, 25/11/2026).

A b027.24 foi excluída no Bling em 18/11 e o Bling a tirou da estrutura do kit
b027.12.24: o cache `bling_kit_components` ficou só com a b027.12 e a busca da
devolução mostrava só a mala 12. O SKU do kit diz os tamanhos (12 + 24): a
peça que a composição de hoje perdeu volta como unidade própria.

Revisão de 25/11: peça RENOMEADA não é peça a mais. Nos kits b028.*.24 a
b028.24 virou `z002800.mala` (mesmo id do Bling) e continua na composição."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingKitComponent, BlingOrder, Devolution, Product
from app.routers.devolutions import _tamanhos_sem_peca, pecas_do_pedido
from app.services import chamados as svc_chamados

KIT_BID, MALA12_BID, MALA24_BID = 730830701, 730830702, 730830703
B028_KIT, B028_12, Z002800 = 730830711, 730830712, 730830713
B099_KIT, B099_12 = 730830721, 730830722
KITS_DO_TESTE = (KIT_BID, B028_KIT, B099_KIT)


def _c(sku: str, qty: int = 1) -> dict:
    return {"sku": sku, "name": None, "cost": None, "qty": qty}


@pytest.mark.parametrize(
    ("kit", "comps", "faltam"),
    [
        # 308307: a 24 sumiu da composição
        ("b027.12.24", [_c("b027.12")], ["b027.24"]),
        # composição completa: nada a acrescentar
        ("b027.12.24", [_c("b027.12"), _c("b027.24")], []),
        # peça renomeada (b028.24 → z002800.mala) continua na composição: as
        # peças fecham com os tamanhos, nada a acrescentar
        ("b028.12.24", [_c("b028.12"), _c("z002800.mala")], []),
        ("b028.10.12.20.24", [_c("b028.10"), _c("b028.12"), _c("b028.20"), _c("z002800.mala")], []),
        # 1 peça a menos, mas o tamanho sem par não fecha com o buraco: ambíguo
        ("b030.12.24.28", [_c("b030.12"), _c("x001.alca")], []),
        # o tamanho vale pelo número: b027.24us cobre o 24
        ("b027.12.24", [_c("b027.12"), _c("b027.24us")], []),
        # quantidade conta: duas 24 numa linha da composição
        ("b056.24.24", [_c("b056.24", 2)], []),
        ("b056.24.24", [_c("b056.24", 1)], ["b056.24"]),
        # 3 tamanhos, 1 peça: faltam 2
        ("b026.12.18.28", [_c("b026.18")], ["b026.12", "b026.28"]),
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


def _p(user_id, sku, nome, bid, *, formato="S", situacao="A", custo=None) -> Product:
    return Product(
        user_id=user_id,
        sku=sku,
        name=nome,
        bling_product_id=bid,
        formato=formato,
        situacao=situacao,
        cost_price=custo,
    )


async def _kit_b027(db: AsyncSession, user_id) -> None:
    db.add_all(
        [
            _p(
                user_id,
                "b027.12.24",
                "Kit Malas Chanfrada M3 12.24 - Branca",
                KIT_BID,
                formato="E",
                situacao="E",
                custo=167,
            ),
            _p(user_id, "b027.12", "Mala Chanfrada M3 tamanho 12 - Branca", MALA12_BID, custo=41),
            _p(
                user_id,
                "b027.24",
                "Mala Chanfrada M3 tamanho 24 - Branca",
                MALA24_BID,
                situacao="E",
                custo=126,
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
        delete(BlingKitComponent).where(BlingKitComponent.kit_bling_product_id.in_(KITS_DO_TESTE))
    )
    await db.commit()


async def _view_com_o_pedido(db: AsyncSession, numero: str, kit_sku: str, custo: float) -> None:
    """`vw_devolucoes` de teste com 1 pedido de 1 kit (o mesmo molde do
    test_devolutions_router)."""
    schema = get_settings().database_schema
    item_id = uuid4()
    await db.execute(
        text(
            "INSERT INTO bling_orders (id, numero, numeroloja, preco_custo) "
            "VALUES (:id, :numero, :loja, :custo)"
        ),
        {"id": item_id, "numero": numero, "loja": f"MKT-{numero}", "custo": custo},
    )
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES (
                '2026-11-10T13:18:00+00:00'::timestamptz, '{numero}'::text,
                'MKT-{numero}'::text, 'Amazon Poofy'::text, NULL::bigint, '{kit_sku}'::text,
                'Kit de malas'::text, 1::integer,
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


async def _sem_view(db: AsyncSession) -> None:
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()
    await _limpar_composicao(db)


async def _usuario_que_ve(make_user, auth_as):
    user = await make_user(
        permissions={"devolucoes": {"view": True, "edit": False, "delete": False}}
    )
    auth_as(user)
    return user


@pytest.mark.asyncio
async def test_busca_da_devolucao_mostra_a_mala_excluida(
    client, db: AsyncSession, make_user, auth_as
):
    user = await _usuario_que_ve(make_user, auth_as)
    await _kit_b027(db, user.id)
    await _view_com_o_pedido(db, "308307", "b027.12.24", 167)
    try:
        resp = await client.get("/api/devolutions/order-lookup?pedido=308307")
        # o painel novo ("resolve sozinho") usa a mesma conta com exato=True
        exato = await pecas_do_pedido(db, "308307", exato=True)
    finally:
        await _sem_view(db)

    assert resp.status_code == 200, resp.text
    linhas = [(r["sku"], r["produtos"], r["custo_produto"]) for r in resp.json()]
    assert linhas == [
        ("b027.12", "Mala Chanfrada M3 tamanho 12 - Branca", 41),
        ("b027.24", "Mala Chanfrada M3 tamanho 24 - Branca", 126),
    ]
    assert not any(r["ja_devolvido"] for r in resp.json())
    assert [r["sku"] for r in exato] == ["b027.12", "b027.24"]


@pytest.mark.asyncio
async def test_peca_renomeada_nao_vira_mala_a_mais(client, db: AsyncSession, make_user, auth_as):
    """b028.12.24: a mala 24 está na composição como z002800.mala (renomeada no
    Bling, mesmo id). São 2 malas, não 3."""
    user = await _usuario_que_ve(make_user, auth_as)
    db.add_all(
        [
            _p(
                user.id,
                "b028.12.24",
                "Kit Malas Chanfrada 12.24 - Champanhe",
                B028_KIT,
                formato="E",
                situacao="E",
                custo=167,
            ),
            _p(user.id, "b028.12", "Mala Chanfrada M3 tamanho 12 - Champanhe", B028_12, custo=41),
            _p(
                user.id,
                "z002800.mala",
                "Mala Chanfrada M3 tamanho 24 - Champanhe AVULSO",
                Z002800,
                custo=126,
            ),
            BlingKitComponent(kit_bling_product_id=B028_KIT, component_bling_product_id=B028_12),
            BlingKitComponent(kit_bling_product_id=B028_KIT, component_bling_product_id=Z002800),
        ]
    )
    await db.commit()
    await _view_com_o_pedido(db, "241130", "b028.12.24", 167)
    try:
        resp = await client.get("/api/devolutions/order-lookup?pedido=241130")
    finally:
        await _sem_view(db)
    assert resp.status_code == 200, resp.text
    assert sorted(r["sku"] for r in resp.json()) == ["b028.12", "z002800.mala"]


@pytest.mark.asyncio
async def test_peca_fora_do_catalogo_nao_entra(client, db: AsyncSession, make_user, auth_as):
    """O tamanho falta na composição, mas b099.28 nunca foi cadastrada: sem
    produto não há nome, custo nem onde dar entrada — fica como estava."""
    user = await _usuario_que_ve(make_user, auth_as)
    db.add_all(
        [
            _p(
                user.id,
                "b099.12.28",
                "Kit Malas b099 12.28",
                B099_KIT,
                formato="E",
                situacao="E",
                custo=200,
            ),
            _p(user.id, "b099.12", "Mala b099 tamanho 12", B099_12, custo=50),
            BlingKitComponent(kit_bling_product_id=B099_KIT, component_bling_product_id=B099_12),
        ]
    )
    await db.commit()
    await _view_com_o_pedido(db, "309999", "b099.12.28", 200)
    try:
        resp = await client.get("/api/devolutions/order-lookup?pedido=309999")
    finally:
        await _sem_view(db)
    assert resp.status_code == 200, resp.text
    assert [r["sku"] for r in resp.json()] == ["b099.12"]


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
