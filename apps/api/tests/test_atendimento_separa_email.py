"""O E-MAIL só na aba E-mail; o CHAT só na Caixa (13/10/2026).

O dono (print da Caixa da Amazon cheia de "Comprador | Kia — não precisa de
resposta"): "vamos deixar o e-mail aparecer só na aba de e-mail; a do chat de
atendimento aparece só no chat mesmo. Percebi que na Amazon, na caixa de
entrada, está vindo os e-mails — pois lá já estamos linkando com a loja".

Postgres local de verdade, o mundo da ponte (`tests/test_mail_ponte.cena`). O
que não pode falhar (`services/atendimento/separa_email.py`):
  • a conversa de e-mail da ponte (canal email + `dados.fonte = 'tuta'`) não
    aparece na Caixa nem em nenhum número dela — lista, /resumo (plataformas,
    barra de lojas, etiquetas), Caixa Humano, métricas;
  • a Amazon pelo GMAIL (canal email SEM a marca) é o chat da Amazon e fica;
  • a aba E-mail lista as conversas de e-mail (`?caixa=email`) e conta as
    dela (`/email/contagens`); o GET de UMA conversa pelo id continua;
  • o e-mail que a ponte gravou DENTRO do chat (o aviso de antes) não aparece
    mais nele — detalhe, abas, prévia, fila —, sem apagar nada;
  • a ponte nova não grava e-mail no chat (`test_mail_ponte`);
  • vincular ao pedido pela conversa de e-mail aberta na aba E-mail;
  • a prévia/ordem antiga do chat: a migration 0391 refaz (e o downgrade volta);
  • a conversa PRINCIPAL do pedido (Ag. cancelamento, troca, oferta,
    devolução) continua o chat depois do aviso por e-mail — a de e-mail vai
    por último; o alerta do Telegram, o cartão Cliente e a thread da Amazon
    pelo Gmail não pegam a conversa de e-mail.
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, UserRole
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from app.services.atendimento import abas as abas_svc
from app.services.atendimento import amazon_email, etiqueta_fatos, gravar, humano, separa_email
from app.services.atendimento import cliente as cliente_svc
from app.services.atendimento import sync as sync_svc
from app.services.mail_atendimento import barra_lojas
from tests.test_mail_ponte import (
    AGORA,
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

_ALEMBIC = Path(__file__).resolve().parents[1] / "alembic"
_spec = importlib.util.spec_from_file_location(
    "m0391", _ALEMBIC / "versions" / "0391_email_fora_do_chat.py"
)
m0391 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0391)

URL = "/api/atendimento"
EMAIL = f"{URL}/email"
OUTRO = "quem-mexe@davinci-test.com"
PEDIDO = "2000012345678901"


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


@pytest.fixture
async def quem_le(make_user, auth_as, monkeypatch):
    """Pessoa ativa do DaVinci, sem equipe: LÊ o /atendimento (não mexe)."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    user = await make_user(role=UserRole.USER)
    auth_as(user)
    return user


async def _chat(db, integ, plataforma: str, externo: str, *, canal: str = "chat", pedido=None):
    c, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=externo,
        comprador_id=f"comprador-{externo}",
        comprador_nome="Comprador",
        pedido_marketplace=pedido,
    )
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id=f"{externo}-c1",
        autor="cliente",
        texto="quando chega?",
        enviada_em=AGORA - timedelta(hours=3),
    )
    await db.commit()
    return c


async def _email_antigo_no_chat(db, c: AtendimentoConversa, quando) -> AtendimentoMensagem:
    """O aviso que a ponte gravava DENTRO do chat até 13/10/2026 (e a prévia de então)."""
    m = AtendimentoMensagem(
        conversa_id=c.id,
        externo_id=f"mail:{uuid4()}",
        autor="sistema",
        origem="sistema",
        tipo="texto",
        texto="E-mail da plataforma (mensagens ml): Nova mensagem no pedido",
        enviada_em=quando,
        payload={"mail": {"message_id": str(uuid4()), "aviso_api": True}},
    )
    db.add(m)
    await db.flush()
    gravar.recalcular(c, [m])  # a régua de antes: o e-mail virava a prévia
    await db.commit()
    return m


async def _mundo(db, cena) -> dict:
    """O chat (Shopee), a Amazon pelo Gmail, o e-mail da ponte (ML) e um chamado do site."""
    chat = await _chat(db, cena.shopee, "shopee", "chat-1")
    gmail = await _chat(db, cena.amazon, "amazon", "gmail-1", canal="email")
    mid_ml = await entrar(db, cena.geral, assunto="Pergunta", corpo="Quando chega?")
    mid_site = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0001] Dúvida",
        corpo="Nome: Maria\nE-mail: maria@gmail.com\nMensagem: oi",
    )
    await rodar(db)
    ml = await conversa(db, (await meta_de(db, mid_ml)).conversa_id)
    site = await conversa(db, (await meta_de(db, mid_site)).conversa_id)
    assert separa_email.e_conversa_de_email(ml) and separa_email.e_conversa_de_email(site)
    assert not separa_email.e_conversa_de_email(gmail), "a Amazon pelo Gmail é chat"
    assert ml.aguardando_resposta and site.aguardando_resposta and gmail.aguardando_resposta
    return {"chat": chat, "gmail": gmail, "ml": ml, "site": site, "mid_ml": mid_ml}


async def _migration(db, *, esconder_email: bool = True) -> dict:
    """A 0391 DE VERDADE (a função dela) no schema do teste; upgrade = esconder o e-mail."""
    conexao = await db.connection()
    schema = get_settings().database_schema
    saida = await conexao.run_sync(
        lambda c: m0391.refazer(c, schema, esconder_email=esconder_email)
    )
    await db.commit()
    return saida


async def _ids(client, **params) -> set[str]:
    r = await client.get(f"{URL}/conversas", params=params)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["itens"]}


async def _ids_pela_barra(client, **params) -> set[str]:
    """A lista de UMA loja como a barra de lojas da Caixa pede — todos os grupos
    (as pastas Problemas · Envios · Devolução) que ela mostra. Desde 27/10/2026 a
    aba E-mail é só de admin: quem não é admin vê o e-mail SÓ por aqui (a lista
    inteira é 403 `aba_email_so_admin`)."""
    ids: set[str] = set()
    for g in barra_lojas.chaves():
        ids |= await _ids(client, caixa="email", grupo_email=g, **params)
    return ids


async def _na_barra(db, conversa_id, pasta: str = "problemas") -> None:
    """Os e-mails da conversa numa pasta de um grupo da barra (27/10/2026: a
    barra de lojas mostra só as pastas Problemas · Envios · Devolução · Informativo)."""
    metas = (
        (
            await db.execute(
                select(MailMessageMeta).where(MailMessageMeta.conversa_id == conversa_id)
            )
        )
        .scalars()
        .all()
    )
    for meta in metas:
        folder = await db.scalar(
            select(MailFolder).where(
                MailFolder.mailbox_id == meta.mailbox_id, MailFolder.chave == pasta
            )
        )
        if folder is None:
            folder = MailFolder(
                mailbox_id=meta.mailbox_id, chave=pasta, nome=pasta, caminho=pasta, tipo_tuta="0"
            )
            db.add(folder)
            await db.flush()
        meta.folder_id = folder.id
    await db.commit()


async def _lista_inteira_recusada(client) -> None:
    """A lista de todas as lojas (a aba E-mail) para quem não é admin: 403."""
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "filtro": "todas"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "aba_email_so_admin"


async def _tipo_email(db, conversa_id, tipo: str) -> None:
    c = await db.get(AtendimentoConversa, conversa_id)
    c.dados = {**c.dados, "mail": {**c.dados["mail"], "tipo_email": tipo}}
    await db.commit()


# ─────────────── a régua (pura e SQL, gêmeas) ───────────────


async def test_a_regua_e_uma_so(db, cena):
    m = await _mundo(db, cena)
    puras = {
        str(c.id)
        for c in (await db.execute(select(AtendimentoConversa))).scalars()
        if separa_email.e_conversa_de_email(c)
    }
    sql = {
        str(i)
        for i in (
            await db.execute(
                select(AtendimentoConversa.id).where(separa_email.conversa_de_email_sql())
            )
        ).scalars()
    }
    assert puras == sql == {str(m["ml"].id), str(m["site"].id)}
    caixa = {
        str(i)
        for i in (
            await db.execute(select(AtendimentoConversa.id).where(separa_email.na_caixa_sql()))
        ).scalars()
    }
    assert caixa == {str(m["chat"].id), str(m["gmail"].id)}
    # A marca com espaço (como a régua do envio) também é e-mail; outra fonte, não.
    assert separa_email.e_email("email", {"fonte": " tuta "})
    assert not separa_email.e_email("email", {"fonte": "gmail"})
    assert not separa_email.e_email("chat", {"fonte": "tuta"})
    # As abas usam a MESMA régua.
    assert abas_svc.e_email_tuta(m["ml"]) and not abas_svc.e_email_tuta(m["gmail"])


# ─────────────── a Caixa: só o chat ───────────────


async def test_caixa_so_chat_e_a_lista_do_email(db, cena, client, admin):
    m = await _mundo(db, cena)
    chat, gmail, ml, site = (str(m[k].id) for k in ("chat", "gmail", "ml", "site"))
    for params in ({}, {"filtro": "aguardando"}, {"filtro": "pos_venda"}):
        ids = await _ids(client, **params)
        assert {chat, gmail} <= ids or params == {"filtro": "pos_venda"}, params
        assert not ids & {ml, site}, f"o e-mail não aparece na Caixa: {params}"
    assert await _ids(client, plataforma="amazon") == {gmail}, "a Amazon pelo Gmail fica"
    assert await _ids(client, plataforma="ml") == set()
    assert await _ids(client, plataforma="site") == set()
    assert await _ids(client, filtro="email_sem_vinculo") == set()
    assert await _ids(client, caixa="humano") & {ml, site} == set()
    # A lista da aba E-mail: só o e-mail (e no escopo, com os mesmos filtros).
    assert await _ids(client, caixa="email") == {ml, site}
    assert await _ids(client, caixa="email", filtro="aguardando") == {ml, site}
    assert await _ids(client, caixa="email", plataforma="site", tipo_chamado="sac") == {site}
    assert await _ids(client, caixa="email", plataforma="site", tipo_chamado="atacado") == set()
    assert await _ids(client, caixa="email", filtro="email_sem_vinculo") == {ml}
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "plataforma": "site"})
    [linha] = r.json()["itens"]
    assert linha["email"] == {
        "protocolo": "US-26-0001",
        "tipo": "sac",
        "tipo_rotulo": "SAC",
        "marca": "Uranyx",
        "alias": "sac@uranyx.com.br",
        "destaque": False,
        "tipo_email": "chamado",
        "tipo_email_inferido": False,
        "caso_id": None,
    }
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "plataforma": "ml"})
    assert r.json()["itens"][0]["email"]["alias"] == "21max@tuta.com"
    r = await client.get(f"{URL}/conversas", params={"plataforma": "shopee"})
    assert r.json()["itens"][0]["email"] is None, "o chat não leva o bloco do e-mail"
    assert (await client.get(f"{URL}/conversas", params={"caixa": "x"})).status_code == 422
    # Pelo id, a conversa de e-mail abre (a aba E-mail abre por aqui), com o e-mail.
    r = await client.get(f"{URL}/conversas/{ml}")
    assert r.status_code == 200, r.text
    detalhe = r.json()
    assert detalhe["conversa"]["email_da_ponte"] is True
    assert detalhe["conversa"]["email"]["alias"] == "21max@tuta.com"
    assert [msg["email"]["tipo"] for msg in detalhe["mensagens"]] == ["recebido"]


async def test_resumo_metricas_e_contagens(db, cena, client, admin):
    m = await _mundo(db, cena)
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    resumo = r.json()
    por = {p["plataforma"]: p for p in resumo["plataformas"]}
    assert (por["shopee"]["aguardando"], por["amazon"]["aguardando"]) == (1, 1)
    assert por["ml"]["aguardando"] == 0, "o e-mail da loja do ML não conta na Caixa"
    assert "site" not in por or por["site"]["aguardando"] == 0
    assert all("chamados" not in p and "email_sem_vinculo" not in p for p in resumo["plataformas"])
    assert "email_sem_vinculo" not in resumo
    # A barra de lojas: nenhuma linha que só existe por causa do e-mail.
    assert {lj["plataforma"] for lj in resumo["lojas"]} == {"shopee", "amazon"}
    assert sum(resumo["etiquetas"].values()) == 2, "as etiquetas contam só o chat"
    # As métricas de resposta: só o chat.
    metricas = (await client.get(f"{URL}/metricas")).json()
    assert {lj["plataforma"] for lj in metricas["lojas"]} == {"shopee", "amazon"}
    # Os números da aba E-mail.
    r = await client.get(f"{EMAIL}/contagens")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "aguardando": 2,
        "chamados": 1,
        "chamados_por_tipo": {"sac": 1, "atacado": 0, "duvidas": 0},
        "por_tipo_email": {"chamado": 2, "informativo": 0, "venda": 0},
    }
    # Fechou o chamado: sai da contagem dos chamados (e do falta responder).
    site = await db.get(AtendimentoConversa, m["site"].id)
    site.situacao = "fechada"
    site.aguardando_resposta = False
    await db.commit()
    assert (await client.get(f"{EMAIL}/contagens")).json()["chamados"] == 0


async def test_quem_so_le_ve_as_conversas_de_email_como_via_na_caixa(db, cena, client, quem_le):
    m = await _mundo(db, cena)
    # 27/10/2026: a aba E-mail é só de admin — quem só lê vê o e-mail da loja
    # pela barra de lojas (uma loja, um grupo/pasta da barra); o chamado do site
    # e a lista de todas as lojas ficam na aba (403).
    await _lista_inteira_recusada(client)
    assert await _ids_pela_barra(client, integration_id=str(cena.ml.id)) == set()
    await _na_barra(db, m["ml"].id)
    assert await _ids_pela_barra(client, integration_id=str(cena.ml.id)) == {str(m["ml"].id)}
    assert (await client.get(f"{EMAIL}/contagens")).status_code == 200
    assert (await client.get(f"{URL}/conversas/{m['ml'].id}")).status_code == 200
    # Vincular é de quem mexe.
    r = await client.post(f"{EMAIL}/conversas/{m['ml'].id}/vincular", json={"pedido": PEDIDO})
    assert r.status_code == 403


async def test_caixa_humano_sem_email(db, cena):
    m = await _mundo(db, cena)
    assert humano.no_escopo(m["ml"]) is False and humano.no_escopo(m["site"]) is False
    assert humano.no_escopo(m["chat"]) is True
    no_escopo = set(
        (await db.execute(select(AtendimentoConversa.id).where(humano.escopo_sql()))).scalars()
    )
    assert m["chat"].id in no_escopo
    assert not no_escopo & {m["ml"].id, m["site"].id}


# ─────────────── o e-mail que JÁ estava dentro do chat ───────────────


async def test_email_antigo_no_chat_fica_escondido(db, cena, client, admin):
    chat = await _chat(db, cena.ml, "ml", "pack-1", canal="pos_venda", pedido=PEDIDO)
    await gravar.gravar_mensagem(
        db,
        chat,
        externo_id="pack-1-l1",
        autor="loja",
        texto="Já foi postado!",
        enviada_em=AGORA - timedelta(hours=2),
    )
    await db.commit()
    antes = (chat.aguardando_resposta, chat.situacao)
    aviso = await _email_antigo_no_chat(db, chat, AGORA - timedelta(hours=1))
    await db.refresh(chat)
    assert chat.ultima_mensagem_resumo.startswith("E-mail da plataforma"), "o retrato de antes"
    # O detalhe do chat: sem o e-mail (nada foi apagado).
    detalhe = (await client.get(f"{URL}/conversas/{chat.id}")).json()
    assert [msg["texto"] for msg in detalhe["mensagens"]] == ["quando chega?", "Já foi postado!"]
    assert all(msg["email"] is None for msg in detalhe["mensagens"])
    assert await db.get(AtendimentoMensagem, aviso.id) is not None
    # As abas do chat: sem aba E-mail, sem o e-mail na contagem.
    abas = (await client.get(f"{URL}/conversas/{chat.id}/abas")).json()
    assert [a["chave"] for a in abas["abas"]] == ["pos_venda"]
    assert abas["abas"][0]["total"] == 2
    # Os cartões e o "E-mails da venda" do chat: vazios.
    assert (await client.get(f"{EMAIL}/conversas/{chat.id}/emails")).json()["emails"] == []
    venda = (await client.get(f"{EMAIL}/conversas/{chat.id}/emails-da-venda")).json()
    assert venda == {"total": 0, "caixas": []}
    # O tipo da prévia na lista já ignora o e-mail; o carimbo, a migration 0391 refaz.
    r = await client.get(f"{URL}/conversas", params={"plataforma": "ml"})
    [linha] = r.json()["itens"]
    assert linha["ultima_mensagem_tipo"] == "texto"
    assert await _migration(db) == {"conversas": 1, "mudaram": 1}
    await db.refresh(chat)
    assert (chat.ultima_mensagem_resumo, chat.ultima_autor) == ("Já foi postado!", "loja")
    assert gravar._utc(chat.ultima_mensagem_em) == AGORA - timedelta(hours=2)
    assert (chat.aguardando_resposta, chat.situacao) == antes, "a fila não muda"
    assert (await _migration(db))["mudaram"] == 0, "rodar de novo: nada"
    # A busca da Caixa não acha mais o chat pelo texto do e-mail.
    assert await _ids(client, filtro="todas", q="E-mail da plataforma") == set()
    # O downgrade devolve o retrato de antes (nada foi apagado); o upgrade, de novo.
    assert (await _migration(db, esconder_email=False))["mudaram"] == 1
    await db.refresh(chat)
    assert chat.ultima_mensagem_resumo.startswith("E-mail da plataforma")
    assert (await _migration(db))["mudaram"] == 1
    await db.refresh(chat)
    assert chat.ultima_mensagem_resumo == "Já foi postado!"
    # O recálculo da fila também não olha o e-mail.
    await gravar.recalcular_conversa(db, chat)
    assert (chat.ultima_mensagem_resumo, chat.ultima_autor) == ("Já foi postado!", "loja")
    # E-mail novo no chat (nenhum caminho deveria gravar, mas se gravar): não mexe na prévia.
    _m, nova = await gravar.gravar_mensagem(
        db,
        chat,
        externo_id=f"mail:{uuid4()}",
        autor="cliente",
        texto="um e-mail",
        enviada_em=AGORA,
        payload={"mail": {"message_id": "x"}},
    )
    assert nova and chat.ultima_mensagem_resumo == "Já foi postado!"
    assert (chat.aguardando_resposta, chat.situacao) == antes


async def test_abas_o_chat_sem_email_e_o_email_sozinho(db, cena, client, admin):
    chat = await _chat(db, cena.ml, "ml", "pack-2", canal="pos_venda", pedido=PEDIDO)
    await pedido_existe(db, cena.ml, PEDIDO)
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto=f"Nova mensagem no pedido {PEDIDO}",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    meta = await meta_de(db, mid)
    email = await conversa(db, meta.conversa_id)
    assert email.pedido_marketplace == PEDIDO and email.id != chat.id
    # O chat do mesmo pedido: nada de aba E-mail.
    abas = (await client.get(f"{URL}/conversas/{chat.id}/abas")).json()
    assert "email" not in [a["chave"] for a in abas["abas"]]
    assert all(c["id"] != str(email.id) for a in abas["abas"] for c in a["conversas"])
    # A conversa de e-mail aberta (aba E-mail): sozinha, sem o chat.
    abas = (await client.get(f"{URL}/conversas/{email.id}/abas")).json()
    assert [a["chave"] for a in abas["abas"]] == ["email"]
    assert [c["id"] for c in abas["abas"][0]["conversas"]] == [str(email.id)]
    fam = await abas_svc.familia(db, email)
    assert [c.id for c in fam.conversas] == [email.id]
    # A família do chat não traz a conversa de e-mail.
    fam = await abas_svc.familia(db, chat)
    assert email.id not in {c.id for c in fam.conversas}


# ─────────────── a aba E-mail: Caixas e vincular pela conversa ───────────────


async def test_caixas_levam_a_conversa_de_email_nunca_o_chat(db, cena, client, admin):
    m = await _mundo(db, cena)
    # O aviso de antes, ligado à conversa de CHAT (simulado na meta).
    chat = await _chat(db, cena.ml, "ml", "pack-3", canal="pos_venda", pedido=PEDIDO)
    antigo = await entrar(db, cena.geral, assunto="Aviso antigo")
    await rodar(db)
    meta = await meta_de(db, antigo)
    meta.conversa_id, meta.vinculado_por = chat.id, "aviso_api"
    await db.commit()
    r = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/lista")
    assert r.status_code == 200, r.text
    por_id = {i["id"]: i for i in r.json()["itens"]}
    assert por_id[str(m["mid_ml"])]["conversa_id"] == str(m["ml"].id)
    assert por_id[str(antigo)]["conversa_id"] is None, "o chat não abre pela aba E-mail"


async def test_caixas_sem_conversa_para_quem_nao_ve_o_atendimento(
    db, cena, client, auth_as, monkeypatch
):
    m = await _mundo(db, cena)
    monkeypatch.setattr("app.services.atendimento.acesso.pode_ver", lambda user: False)
    auth_as(cena.dono)
    r = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/lista")
    assert r.status_code == 200, r.text
    por_id = {i["id"]: i for i in r.json()["itens"]}
    assert por_id[str(m["mid_ml"])]["conversa_id"] is None


async def test_vincular_ao_pedido_pela_conversa_de_email(db, cena, client, admin):
    m = await _mundo(db, cena)
    ml = m["ml"]
    r = await client.post(f"{EMAIL}/conversas/{ml.id}/vincular", json={"pedido": PEDIDO})
    assert r.status_code == 200, r.text
    assert r.json() == {"conversa_id": str(ml.id), "pedido": PEDIDO}
    ml = await conversa(db, ml.id)
    assert ml.pedido_marketplace == PEDIDO
    meta = await meta_de(db, m["mid_ml"])
    assert (meta.estado, meta.vinculado_por, meta.pedido_marketplace) == (
        "gravado",
        "manual",
        PEDIDO,
    )
    # A nota de quem vinculou fica na conversa de e-mail.
    n = await db.scalar(
        select(func.count())
        .select_from(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == ml.id, AtendimentoMensagem.autor == "sistema")
    )
    assert n == 1
    # Outro pedido: recusa (a conversa já tem).
    r = await client.post(f"{EMAIL}/conversas/{ml.id}/vincular", json={"pedido": "999"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conversa_ja_tem_pedido"
    # O chat não é conversa de e-mail.
    r = await client.post(f"{EMAIL}/conversas/{m['chat'].id}/vincular", json={"pedido": PEDIDO})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conversa_sem_email"
    # Sem pedido: 422.
    r = await client.post(f"{EMAIL}/conversas/{m['site'].id}/vincular", json={"pedido": " "})
    assert r.status_code == 422


async def test_metas_das_conversas_de_email_continuam(db, cena):
    """Nada é apagado: as metas da ponte continuam ligadas às conversas de e-mail."""
    m = await _mundo(db, cena)
    ligadas = set(
        (
            await db.execute(
                select(MailMessageMeta.conversa_id).where(MailMessageMeta.conversa_id.is_not(None))
            )
        ).scalars()
    )
    assert {m["ml"].id, m["site"].id} <= ligadas


# ─────────────── a migration 0391 (a prévia/ordem antiga do chat) ───────────────


def test_migration_0391_uma_ponta_depois_da_0390():
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    nossa = scripts.get_revision("0391_email_fora_do_chat")
    assert nossa.down_revision == "0390_denuncia_juridico_pasta"


async def test_migration_0391_faz_a_mesma_conta_do_recalculo(db, cena):
    """A migration e o `gravar.recalcular_conversa` dão a MESMA prévia (texto
    longo com quebras, imagem sem texto, nota interna depois); a conversa de
    e-mail, a Amazon pelo Gmail e o chat sem e-mail não mudam."""
    longo = await _chat(db, cena.shopee, "shopee", "longo")
    await gravar.gravar_mensagem(
        db,
        longo,
        externo_id="longo-l1",
        autor="loja",
        texto="  Olá!\n\n" + "palavra " * 40,
        enviada_em=AGORA - timedelta(hours=2),
    )
    await db.commit()
    await _email_antigo_no_chat(db, longo, AGORA - timedelta(hours=1))
    imagem = await _chat(db, cena.shopee, "shopee", "imagem")
    await gravar.gravar_mensagem(
        db,
        imagem,
        externo_id="imagem-c2",
        autor="cliente",
        texto=None,
        tipo="imagem",
        enviada_em=AGORA - timedelta(hours=2),
    )
    await db.commit()
    await _email_antigo_no_chat(db, imagem, AGORA - timedelta(hours=1))
    db.add(
        AtendimentoMensagem(
            conversa_id=imagem.id,
            externo_id=f"nota:{uuid4()}",
            autor="loja",
            origem="davinci_nota",
            tipo="nota",
            texto="nota interna",
            enviada_em=AGORA,
        )
    )
    await db.commit()
    m = await _mundo(db, cena)
    retrato = {
        c.id: (c.ultima_mensagem_em, c.ultima_mensagem_resumo, c.ultima_autor)
        for c in (m["ml"], m["site"], m["gmail"], m["chat"])
    }
    saida = await _migration(db)
    assert saida == {"conversas": 2, "mudaram": 2}
    ids = (longo.id, imagem.id)
    db.expire_all()
    for cid in ids:
        c = await db.get(AtendimentoConversa, cid)
        da_migration = (gravar._utc(c.ultima_mensagem_em), c.ultima_mensagem_resumo, c.ultima_autor)
        await gravar.recalcular_conversa(db, c)
        assert da_migration == (
            gravar._utc(c.ultima_mensagem_em),
            c.ultima_mensagem_resumo,
            c.ultima_autor,
        )
        await db.rollback()
    longo = await db.get(AtendimentoConversa, ids[0])
    assert (
        longo.ultima_mensagem_resumo.startswith("Olá! palavra")
        and len(longo.ultima_mensagem_resumo) == 160
    )
    assert longo.ultima_mensagem_resumo.endswith("…")
    imagem = await db.get(AtendimentoConversa, ids[1])
    assert (imagem.ultima_mensagem_resumo, imagem.ultima_autor) == ("[imagem]", "cliente")
    for cid, antes in retrato.items():
        c = await db.get(AtendimentoConversa, cid)
        assert (c.ultima_mensagem_em, c.ultima_mensagem_resumo, c.ultima_autor) == antes


async def test_migration_0391_nao_sobrescreve_quem_mudou_no_meio(db, cena):
    """Os carimbos só são gravados se ainda forem os lidos (a mensagem nova do
    sync entre a leitura e a gravação já deixou a prévia certa)."""
    chat = await _chat(db, cena.shopee, "shopee", "meio")
    await _email_antigo_no_chat(db, chat, AGORA - timedelta(hours=1))
    chat_id = chat.id
    conexao = await db.connection()
    schema = get_settings().database_schema

    def _no_meio(c):
        linhas = c.execute(m0391._consulta(schema, esconder_email=True)).all()
        # O sync grava a mensagem nova (a prévia muda) antes da migration gravar.
        c.execute(
            text(
                "UPDATE atendimento_conversas SET ultima_mensagem_resumo = 'mensagem nova' "
                "WHERE id = :id"
            ),
            {"id": chat_id},
        )
        [(cid, antes_em, antes_resumo, antes_autor, quando, _t, _tipo, autor)] = linhas
        r = c.execute(
            m0391._gravar(schema),
            {
                "id": cid,
                "em": quando,
                "resumo": "quando chega?",
                "autor": autor,
                "antes_em": antes_em,
                "antes_resumo": antes_resumo,
                "antes_autor": antes_autor,
            },
        )
        return r.rowcount

    assert await conexao.run_sync(_no_meio) == 0
    await db.commit()
    db.expire_all()
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.ultima_mensagem_resumo == "mensagem nova"


# ─────────────── o pedido: a principal continua o chat ───────────────


async def test_principal_do_pedido_e_o_chat_depois_do_aviso(db, cena):
    """O aviso da plataforma por e-mail entra na conversa de E-MAIL, com o
    pedido e um horário mais novo que o do chat: quem pega a primeira de
    `conversas_do_pedido` (Ag. cancelamento, troca, oferta, devolução) quer o
    chat — e a reclamação, que também está na Caixa, vem antes do e-mail."""
    chat = await _chat(db, cena.ml, "ml", "pack-p", canal="pos_venda", pedido=PEDIDO)
    await pedido_existe(db, cena.ml, PEDIDO)
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto=f"Nova mensagem no pedido {PEDIDO}",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    email = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert separa_email.e_conversa_de_email(email) and email.pedido_marketplace == PEDIDO
    assert gravar._utc(email.ultima_mensagem_em) > gravar._utc(chat.ultima_mensagem_em)
    ids = [c.id for c in await etiqueta_fatos.conversas_do_pedido(db, "ml", PEDIDO)]
    assert ids == [chat.id, email.id]
    # Com a conversa da reclamação: chat, reclamação, e-mail.
    recl = await _chat(db, cena.ml, "ml", "recl-p", canal="reclamacao", pedido=PEDIDO)
    ids = [c.id for c in await etiqueta_fatos.conversas_do_pedido(db, "ml", PEDIDO)]
    assert ids == [chat.id, recl.id, email.id]
    assert (await etiqueta_fatos.conversa_do_pedido(db, "ml", PEDIDO)).id == chat.id
    # Só a de e-mail (o pedido sem chat): ela continua achada.
    ids = [c.id for c in await etiqueta_fatos.conversas_do_pedido(db, "ml", "outro")]
    assert ids == []


# ─────────────── o que ainda olhava a conversa de e-mail como chat ───────────────


async def test_alerta_do_telegram_so_com_a_caixa(db, cena, monkeypatch):
    """O alerta de prazo do Telegram é o filtro "vencendo" da Caixa: a conversa
    de e-mail (que mora na aba E-mail, com o número dela) não entra."""
    m = await _mundo(db, cena)
    for c in (m["ml"], m["chat"]):
        c.prazo_resposta_em = AGORA + timedelta(hours=1)
        c.aguardando_resposta = True
        c.sem_resposta_necessaria = False
    await db.commit()
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", True)
    enviados: list[str] = []

    class _Telegram:
        async def safe_send(self, texto):
            enviados.append(texto)
            return True

    async def _sempre(_chave):
        return True

    monkeypatch.setattr(sync_svc, "TelegramClient", _Telegram)
    monkeypatch.setattr(sync_svc, "_reservar_aviso", _sempre)
    ml_id, chat_id = m["ml"].id, m["chat"].id
    assert await sync_svc.alertar_prazos(db) == 1
    [texto] = enviados
    assert str(chat_id) in texto and str(ml_id) not in texto


async def test_cartao_cliente_e_thread_da_amazon_sem_a_conversa_de_email(db, cena):
    """A Amazon pelo GMAIL (o chat) e a conversa de e-mail da ponte com o MESMO
    endereço de retransmissão: o cartão Cliente do chat não aponta a de
    e-mail, e o e-mail seguinte do comprador (sem pedido) entra no chat."""
    relay = "abc123@marketplace.amazon.com.br"
    gmail, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{relay}|-",
        comprador_id=relay,
        comprador_nome="Comprador",
    )
    await gravar.gravar_mensagem(
        db,
        gmail,
        externo_id="g1",
        autor="cliente",
        texto="oi",
        enviada_em=AGORA - timedelta(hours=2),
    )
    da_ponte, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id="mail-fio:x1",
        comprador_id=relay,
        comprador_nome="Comprador",
        dados={"fonte": "tuta", "mail": {"alias": "mike14@tuta.com"}},
    )
    await gravar.gravar_mensagem(
        db,
        da_ponte,
        externo_id="mail:x1",
        autor="cliente",
        texto="outro e-mail",
        enviada_em=AGORA - timedelta(hours=1),
        payload={"mail": {"message_id": "x1"}},
    )
    await db.commit()
    assert separa_email.e_conversa_de_email(da_ponte)
    cartao = await cliente_svc.cartao_cliente(db, gmail)
    refs = [e.get("ref") for e in (cartao.get("linha_do_tempo") or [])]
    assert str(da_ponte.id) not in refs
    # Aberta pela aba E-mail, a conversa de e-mail continua vendo o chat do comprador.
    cartao = await cliente_svc.cartao_cliente(db, da_ponte)
    assert str(gmail.id) in [e.get("ref") for e in (cartao.get("linha_do_tempo") or [])]
    email_ = amazon_email.EmailAmazon(
        message_id="<seguinte@x>",
        remetente=relay,
        remetente_nome="Comprador",
        tag=None,
        assunto="Re: oi",
        pedido=None,
        enviada_em=AGORA,
        texto="e aí?",
    )
    achada = await amazon_email._conversa_da_thread(db, email_, AGORA)
    assert achada is not None and achada.id == gmail.id
