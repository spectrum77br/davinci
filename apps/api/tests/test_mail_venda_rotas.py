# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""A venda X só libera e-mails X, mesmo quando um caso também trata de Y."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, StoreInfo, UserRole
from app.models.mail_atendimento import MailMessageMeta
from tests.test_atendimento_separa_email_venda import _venda_com_email
from tests.test_mail_ponte import _config, cena, conversa, entrar, meta_de, pedido_existe, rodar

BASE = "/api/atendimento/email/venda"
X = "2000012345678901"
Y = "2000012345678902"


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)


async def _chat(db, cena, numero=X, **kwargs):
    c = AtendimentoConversa(
        integration_id=kwargs.pop("integration_id", cena.ml.id),
        plataforma=kwargs.pop("plataforma", "ml"),
        canal=kwargs.pop("canal", "pos_venda"),
        externo_id=str(uuid4()),
        pedido_marketplace=numero,
        **kwargs,
    )
    db.add(c)
    await db.commit()
    return c


async def _caso(db, cena, caso="12345678", numero=X, **kwargs):
    mid = await entrar(
        db,
        kwargs.pop("caixa", cena.geral),
        pasta="problema ml",
        de="nao-responder@mercadolivre.com.br",
        assunto=f"Case: {caso}" + (f" Pedido {numero}" if numero else ""),
        corpo=kwargs.pop("corpo", "Mensagem deste caso."),
        **kwargs,
    )
    await rodar(db)
    return await meta_de(db, mid)


async def test_lista_so_metadados_e_detalhe_protegido_sem_liberar_outra_venda(
    db, cena, client, admin
):
    chat_x, chat_y = await _chat(db, cena, X), await _chat(db, cena, Y)
    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.ml, Y)
    x = await _caso(db, cena, numero=X, corpo="Conteúdo da venda X.")
    y = await _caso(db, cena, numero=Y, corpo="Conteúdo da venda Y.")
    sem_numero = await _caso(db, cena, numero=None, corpo="Andamento geral do caso.")
    assert x.conversa_id == y.conversa_id == sem_numero.conversa_id
    assert sem_numero.pedido_marketplace is None

    for chat, permitido, negado in ((chat_x, x, y), (chat_y, y, x)):
        r = await client.get(f"{BASE}/{chat.id}")
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        d = r.json()
        assert d["estado"] == "ok" and d["pedido"] == chat.pedido_marketplace
        assert d["total"] == 1 and [i["id"] for i in d["itens"]] == [str(permitido.message_id)]
        assert not ({"texto", "assunto", "de", "anexos"} & d["itens"][0].keys())
        assert (
            await client.get(f"{BASE}/{chat.id}/emails/{permitido.message_id}")
        ).status_code == 200
        assert (await client.get(f"{BASE}/{chat.id}/emails/{negado.message_id}")).status_code == 404
        assert (
            await client.get(f"{BASE}/{chat.id}/emails/{sem_numero.message_id}")
        ).status_code == 404
        resumo = (
            await client.get(f"/api/atendimento/email/conversas/{chat.id}/emails-da-venda")
        ).json()
        assert resumo["total"] == 1


async def test_multiplos_casos_do_mesmo_pedido_e_retorno_univoco_entram(db, cena, client, admin):
    chat = await _chat(db, cena)
    await pedido_existe(db, cena.ml, X)
    a = await _caso(db, cena, "12345678", X)
    b = await _caso(db, cena, "87654321", X)
    retorno = await _caso(db, cena, "12345678", None)
    assert a.conversa_id != b.conversa_id and retorno.conversa_id == a.conversa_id
    d = (await client.get(f"{BASE}/{chat.id}")).json()
    assert d["total"] == 3
    assert {i["id"] for i in d["itens"]} == {str(m.message_id) for m in (a, b, retorno)}


@pytest.mark.parametrize("ignorado", [False, True])
@pytest.mark.parametrize("encontrado", [False, True])
async def test_pedido_citado_diferente_impede_herdar_venda_do_caso(
    db, cena, client, admin, ignorado, encontrado
):
    chat = await _chat(db, cena)
    await pedido_existe(db, cena.ml, X)
    if encontrado:
        await pedido_existe(db, cena.ml, Y)
    x = await _caso(db, cena, numero=X)
    y = await _caso(db, cena, numero=Y)
    retorno = await _caso(db, cena, numero=None)
    assert y.pedido_marketplace == (Y if encontrado else None)
    assert y.pedidos_citados == [{"pedido": Y, "existe": encontrado, "plataforma": "ml"}]
    assert x.conversa_id == y.conversa_id == retorno.conversa_id
    if ignorado:
        y.estado = "ignorado"
        await db.commit()
    d = (await client.get(f"{BASE}/{chat.id}")).json()
    assert d["total"] == 1 and [i["id"] for i in d["itens"]] == [str(x.message_id)]
    for m in (y, retorno):
        assert (await client.get(f"{BASE}/{chat.id}/emails/{m.message_id}")).status_code == 404


async def test_duas_lojas_com_mesmo_pedido_e_caso_so_abrem_seus_emails(db, cena, client, admin):
    x = await _chat(db, cena)
    outra = await _chat(db, cena, integration_id=cena.oliveira_ml.id)
    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.oliveira_ml, X)
    a = await _caso(db, cena)
    b = await _caso(db, cena, caixa=cena.goslin, para=["mia30@tuta.com"])
    assert a.integration_id == cena.ml.id and b.integration_id == cena.oliveira_ml.id
    for chat, permitido, negado in ((x, a, b), (outra, b, a)):
        d = (await client.get(f"{BASE}/{chat.id}", params={"pedido": Y})).json()
        assert d["total"] == 1 and [i["id"] for i in d["itens"]] == [str(permitido.message_id)]
        assert (await client.get(f"{BASE}/{chat.id}/emails/{negado.message_id}")).status_code == 404


async def test_outra_loja_mesmo_numero_outra_plataforma_e_buyer_sem_pedido_nao_entram(
    db, cena, client, admin
):
    v = await _venda_com_email(db, cena)
    permitido = (await client.get(f"{BASE}/{v['pack'].id}")).json()
    assert permitido["total"] == 3
    email = v["email"]
    # Mesma conta de comprador não liga uma mensagem sem pedido a esta venda.
    email.pedido_marketplace = None
    email.dados = {**email.dados, "comprador_plataforma": "comprador-ml-1"}
    metas = list(
        await db.scalars(select(MailMessageMeta).where(MailMessageMeta.conversa_id == email.id))
    )
    for m in metas:
        m.pedido_marketplace = None
        m.pedidos_citados = []
    await db.commit()
    assert (await client.get(f"{BASE}/{v['pack'].id}")).json()["total"] == 0
    # Pedido explícito em meta de outra loja, mesmo com conversa indevida,
    # tampouco passa: as duas identidades precisam coincidir.
    for m in metas:
        m.pedido_marketplace = X
        m.integration_id = cena.shopee.id
    email.pedido_marketplace = X
    await db.commit()
    assert (await client.get(f"{BASE}/{v['pack'].id}")).json()["total"] == 0
    for m in metas:
        m.integration_id = cena.ml.id
        m.plataforma = "shopee"
    await db.commit()
    assert (await client.get(f"{BASE}/{v['pack'].id}")).json()["total"] == 0


async def test_pack_order_explicitamente_na_propria_venda_sao_aliases(db, cena, client, admin):
    chat = await _chat(db, cena, X, dados={"order_id": Y})
    await pedido_existe(db, cena.ml, Y)
    meta = await _caso(db, cena, numero=Y)
    d = (await client.get(f"{BASE}/{chat.id}")).json()
    assert [i["id"] for i in d["itens"]] == [str(meta.message_id)]


@pytest.mark.parametrize("numero", [None, "", f"{X}, {Y}"])
async def test_sem_pedido_univoco_fica_vazio(db, cena, client, admin, numero):
    chat = await _chat(db, cena, numero)
    assert (await client.get(f"{BASE}/{chat.id}")).json() == {
        "estado": "sem_pedido",
        "pedido": None,
        "total": 0,
        "itens": [],
    }


async def test_sem_loja_forte_nao_usa_nome_ou_alias(db, cena, client, admin):
    chat = await _chat(db, cena, integration_id=None, conta="Barbosa")
    assert (await client.get(f"{BASE}/{chat.id}")).json() == {
        "estado": "sem_loja",
        "pedido": X,
        "total": 0,
        "itens": [],
    }


async def test_leitor_pode_ver_email_vinculado_e_escopo_negado_nao_revela_ids(
    db, cena, client, auth_as, make_user, monkeypatch
):
    v = await _venda_com_email(db, cena)
    ids = list(await db.scalars(select(MailMessageMeta.message_id)))
    leitor = await make_user(role=UserRole.USER)
    auth_as(leitor)
    monkeypatch.setattr("app.services.atendimento.acesso.pode_mexer", lambda user: False)
    d = (await client.get(f"{BASE}/{v['pack'].id}")).json()
    assert d["total"] == 3
    assert (await client.get(f"{BASE}/{v['pack'].id}/emails/{ids[0]}")).status_code == 200
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="shopee",
            account_name="só-shopee",
            sales_team=7,
            integration_id=cena.shopee.id,
        )
    )
    leitor.sales_teams = [7]
    await db.commit()
    for path in (f"{BASE}/{v['pack'].id}", f"{BASE}/{v['pack'].id}/emails/{ids[0]}"):
        r = await client.get(path)
        assert r.status_code == 404 and str(ids[0]) not in r.text
