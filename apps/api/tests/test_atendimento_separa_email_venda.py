"""O "E-mails da venda" no CHAT: só números e o link para a aba E-mail (13/10/2026).

O dono: "o e-mail aparece só na aba de e-mail; a do chat de atendimento
aparece só no chat". No chat não pode aparecer NADA do e-mail (assunto,
remetente, texto, anexos, caixa, pasta) — mas quem atende no chat precisa
saber que a venda tem e-mail. GET /api/atendimento/email/conversas/{id}/
emails-da-venda (`routers/atendimento_email.py`):

  • conversa de CHAT → `total` (os e-mails das conversas de e-mail da MESMA
    venda), `aguardando` (quantas delas esperam resposta) e `conversas`
    ([{id, emails, aguardando}], a que espera resposta primeiro); `caixas`
    sempre vazia. Sem conversa de e-mail na venda: o zero de sempre;
  • a mesma venda = a mesma loja e o pedido da família (nunca outra loja, nunca
    outro pedido); o e-mail que a ponte ligou ao PRÓPRIO chat não conta;
  • o escopo é o da lista `?caixa=email`: quem não vê não recebe id nem número;
  • conversa de E-MAIL → a lista completa (caixa → loja, pastas), como antes;
  • a Amazon pelo Gmail (canal email SEM a marca do Tuta) é chat: as mensagens
    dela continuam todas no detalhe, e ela ganha só os números.

Postgres local de verdade, o mundo da ponte (`tests/test_mail_ponte.cena`).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import get_settings
from app.models import AtendimentoConversa, StoreInfo, UserRole
from app.services.atendimento import separa_email
from tests.test_mail_ponte import (
    AGORA,
    _amazon,
    _conversa_do_gmail,
    categorizar,
    conversa,
    entrar,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

URL = "/api/atendimento"
EMAIL = f"{URL}/email"
PEDIDO = "2000012345678901"
OUTRO = "quem-mexe@davinci-test.com"
# O que é do e-mail em si (texto, assunto, remetente, caixa, loja da caixa,
# pasta): nada disso pode sair na resposta do chat.
CONTEUDO = (
    "quebrado",
    "novidade",
    "Nova mensagem",
    "O comprador mandou",
    "maria@gmail.com",
    "Maria",
    "nao-responder",
    "Mercado Livre",
    "21max",
    "Barbosa",
    "problema ml",
    "mensagens ml",
    f"Pedido {PEDIDO}",
)


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


def _venda(conversa_id) -> str:
    return f"{EMAIL}/conversas/{conversa_id}/emails-da-venda"


async def _ids_do_email(client) -> set[str]:
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "filtro": "todas"})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["itens"]}


async def _venda_com_email(db, cena) -> dict:
    """O pack do ML (o chat) e a conversa de e-mail do MESMO pedido: dois e-mails
    da compradora e o aviso do ML (os três na conversa de e-mail, nunca no chat)."""
    pack = AtendimentoConversa(
        integration_id=cena.ml.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-venda",
        comprador_id="comprador-ml-1",
        pedido_marketplace=PEDIDO,
        situacao="respondida",
    )
    db.add(pack)
    await db.commit()
    await pedido_existe(db, cena.ml, PEDIDO)
    for corpo in ("Chegou quebrado.", "Alguma novidade?"):
        await entrar(db, cena.geral, pasta="INBOX", assunto=f"Pedido {PEDIDO}", corpo=corpo)
    # 21/10/2026: o aviso vem da pasta de VENDA (categoria venda): entra na
    # conversa de e-mail da venda — a mesma do cliente, achada pelo pedido.
    await categorizar(db, cena.geral, "vendas ml", "venda")
    aviso = await entrar(
        db,
        cena.geral,
        pasta="vendas ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto=f"Nova mensagem no pedido {PEDIDO}",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    email = await conversa(db, (await meta_de(db, aviso)).conversa_id)
    assert separa_email.e_conversa_de_email(email) and email.pedido_marketplace == PEDIDO
    assert email.id != pack.id and email.aguardando_resposta is True
    return {"pack": pack, "email": email}


def _conversa_de_email(integ, externo: str, *, pedido: str | None, aguardando: bool, **dados):
    """Uma conversa de e-mail da ponte gravada direto (sem e-mail na Central)."""
    return AtendimentoConversa(
        integration_id=integ.id,
        plataforma="ml",
        canal="email",
        externo_id=externo,
        comprador_id="alguem@gmail.com",
        pedido_marketplace=pedido,
        aguardando_resposta=aguardando,
        ultima_mensagem_em=AGORA,
        dados={"fonte": "tuta", "mail": {"alias": "21max@tuta.com"}, **dados},
    )


# ─────────────── o chat: só números e ids ───────────────


async def test_chat_da_venda_so_numeros_e_ids(db, cena, client, admin):
    v = await _venda_com_email(db, cena)
    pack, email = v["pack"], v["email"]
    # Conversa sem e-mails não cria vínculo visível; comprador sem pedido
    # também não prova qual das vendas dele originou o e-mail (19/10/2026).
    respondida = _conversa_de_email(cena.ml, "mail:resp", pedido=PEDIDO, aguardando=False)
    pela_pessoa = _conversa_de_email(
        cena.ml,
        "mail:pessoa",
        pedido=None,
        aguardando=False,
        comprador_plataforma="comprador-ml-1",
    )
    # Nunca: o mesmo pedido em OUTRA loja, outro pedido na mesma loja, outro
    # comprador sem pedido.
    outra_loja = _conversa_de_email(cena.oliveira_ml, "mail:loja", pedido=PEDIDO, aguardando=True)
    # (o outro pedido é do MESMO comprador: é outra compra, não esta venda)
    outro_pedido = _conversa_de_email(
        cena.ml,
        "mail:pedido",
        pedido="999",
        aguardando=True,
        comprador_plataforma="comprador-ml-1",
    )
    outra_pessoa = _conversa_de_email(
        cena.ml,
        "mail:outra",
        pedido=None,
        aguardando=True,
        comprador_plataforma="comprador-ml-2",
    )
    db.add_all([respondida, pela_pessoa, outra_loja, outro_pedido, outra_pessoa])
    await db.commit()

    r = await client.get(_venda(pack.id))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    corpo = r.json()
    assert set(corpo) == {"total", "caixas", "aguardando", "conversas"}
    assert corpo["caixas"] == [], "no chat, nunca a caixa/loja/pasta"
    assert corpo["total"] == 3 and corpo["aguardando"] == 1
    # A que espera resposta primeiro (é a que o link abre).
    assert corpo["conversas"][0] == {"id": str(email.id), "emails": 3, "aguardando": True}
    assert {c["id"] for c in corpo["conversas"]} == {str(email.id)}
    for c in corpo["conversas"]:
        assert set(c) == {"id", "emails", "aguardando"}
    assert corpo["conversas"][1:] == []
    # Nada do e-mail em si.
    for proibido in CONTEUDO:
        assert proibido not in r.text, proibido
    # Os ids são de conversas que a mesma pessoa abre na aba E-mail.
    assert {c["id"] for c in corpo["conversas"]} <= await _ids_do_email(client)
    # E o chat continua sem os cartões do e-mail.
    assert (await client.get(f"{EMAIL}/conversas/{pack.id}/emails")).json()["emails"] == []


async def test_chat_sem_conversa_de_email_e_o_zero_de_sempre(db, cena, client, admin):
    chat = AtendimentoConversa(
        integration_id=cena.shopee.id,
        plataforma="shopee",
        canal="chat",
        externo_id="chat-sem-email",
        comprador_id="comprador-shopee",
        pedido_marketplace="SHP-1",
    )
    db.add(chat)
    await db.commit()
    assert (await client.get(_venda(chat.id))).json() == {"total": 0, "caixas": []}
    # O e-mail que a ponte ligou ao PRÓPRIO chat (o aviso de antes de 13/10,
    # simulado na meta): não há conversa de e-mail para abrir — não conta.
    antigo = await entrar(db, cena.geral, assunto="Aviso antigo")
    await rodar(db)
    meta = await meta_de(db, antigo)
    meta.conversa_id, meta.vinculado_por = chat.id, "aviso_api"
    await db.commit()
    assert (await client.get(_venda(chat.id))).json() == {"total": 0, "caixas": []}


# ─────────────── quem não vê ───────────────


async def test_quem_nao_ve_nao_recebe_id_nem_numero(
    db, cena, client, auth_as, make_user, monkeypatch
):
    v = await _venda_com_email(db, cena)
    pack, email = v["pack"], v["email"]
    # Equipe só com a Shopee: nem o chat do ML abre (nada de id nem de número).
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="shopee",
            account_name="equipe-7",
            sales_team=7,
            integration_id=cena.shopee.id,
        )
    )
    membro = await make_user(role=UserRole.USER)
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    r = await client.get(_venda(pack.id))
    assert r.status_code == 404
    assert str(email.id) not in r.text and '"total"' not in r.text
    assert str(email.id) not in await _ids_do_email(client), "a lista da aba E-mail também não"
    # Equipe com o ML: vê o chat, os números e o id — o mesmo que a lista mostra.
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="mercadolivre",
            account_name="equipe-8",
            sales_team=8,
            integration_id=cena.ml.id,
        )
    )
    do_ml = await make_user(role=UserRole.USER)
    do_ml.sales_teams = [8]
    await db.commit()
    auth_as(do_ml)
    corpo = (await client.get(_venda(pack.id))).json()
    assert corpo["total"] == 3 and [c["id"] for c in corpo["conversas"]] == [str(email.id)]
    assert str(email.id) in await _ids_do_email(client)
    # Quem só lê (sem equipe) vê como na lista da aba E-mail.
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    auth_as(await make_user(role=UserRole.USER))
    corpo = (await client.get(_venda(pack.id))).json()
    assert corpo["total"] == 3 and corpo["aguardando"] == 1
    # Quem não vê o /atendimento: 403, nada.
    monkeypatch.setattr("app.services.atendimento.acesso.pode_ver", lambda user: False)
    r = await client.get(_venda(pack.id))
    assert r.status_code == 403
    assert str(email.id) not in r.text and '"total"' not in r.text


# ─────────────── a conversa de e-mail: como antes ───────────────


async def test_conversa_de_email_lista_completa_como_antes(db, cena, client, admin):
    v = await _venda_com_email(db, cena)
    r = await client.get(_venda(v["email"].id))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert set(corpo) == {"total", "caixas"}, "sem os números do chat"
    assert corpo["total"] == 3
    [caixa] = corpo["caixas"]
    assert (caixa["caixa"], caixa["loja"]) == ("21max@tuta.com", "Barbosa")
    assert caixa["plataforma"] == "ml"
    assert caixa["quantidade"] == 3
    assert caixa["pastas"] == [
        {"pasta": "INBOX", "finalidade": "entrada", "destaque": False, "quantidade": 2},
        {"pasta": "vendas ml", "finalidade": "vendas", "destaque": False, "quantidade": 1},
    ]
    # Os cartões completos continuam na conversa de e-mail.
    cartoes = (await client.get(f"{EMAIL}/conversas/{v['email'].id}/emails")).json()["emails"]
    assert len(cartoes) == 3


# ─────────────── a Amazon pelo Gmail (chat): nada muda ───────────────


async def test_amazon_pelo_gmail_continua_inteira_e_so_ganha_os_numeros(db, cena, client, admin):
    gmail = await _conversa_do_gmail(db, cena)
    m1 = await _amazon(db, cena, 1)
    await rodar(db)
    assert (await meta_de(db, m1)).vinculado_por == "amazon_gmail"
    # É chat: fica na Caixa, com a mensagem dela inteira (não tem `payload.mail`).
    assert not separa_email.e_conversa_de_email(await conversa(db, gmail))
    detalhe = (await client.get(f"{URL}/conversas/{gmail}")).json()
    assert detalhe["conversa"]["email"] is None
    assert [m["texto"] for m in detalhe["mensagens"]] == ["Oi, quando chega?"]
    # O e-mail do Tuta que é a MESMA mensagem do Gmail (ligado ao chat): não conta.
    assert (await client.get(_venda(gmail))).json() == {"total": 0, "caixas": []}
    # O e-mail do mesmo pedido que foi para uma conversa de e-mail à parte: só o número e o id.
    m2 = await _amazon(db, cena, 2, criado_em=AGORA - timedelta(hours=7))
    await rodar(db)
    a_parte = (await meta_de(db, m2)).conversa_id
    assert a_parte is not None and a_parte != gmail
    assert separa_email.e_conversa_de_email(await conversa(db, a_parte))
    r = await client.get(_venda(gmail))
    corpo = r.json()
    assert corpo["total"] == 1 and corpo["caixas"] == []
    assert [c["id"] for c in corpo["conversas"]] == [str(a_parte)]
    for proibido in ("Ainda não chegou", "abc123@", "mike14", "Mike", "mensagens amazon"):
        assert proibido not in r.text, proibido
    # E o detalhe do Gmail não mudou.
    detalhe = (await client.get(f"{URL}/conversas/{gmail}")).json()
    assert [m["texto"] for m in detalhe["mensagens"]] == ["Oi, quando chega?"]
