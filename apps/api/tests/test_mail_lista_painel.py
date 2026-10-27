"""O e-mail das lojas na LISTA e no PAINEL ④ do /atendimento (09/10/2026).

RF1/RF6: os chips SAC / Atacado / Dúvidas e sugestões do grupo Site
(`?tipo_chamado=` sobre `dados.mail.caixa`, com os números no /resumo, sem
mexer no motor de etiqueta). RF1/RF5: o filtro "E-mail sem vínculo" na lista
(a conversa de e-mail da ponte, aberta, sem pedido, fora de site), com os
números por plataforma e por loja. RF5: o nome da caixa nas Pastas (a tela
Pastas e regras agrupa por caixa). RF1 ④: o resumo "E-mails da venda" (caixa
→ loja, pasta, quantos) da conversa e da família dela.

13/10/2026 (o dono: "o e-mail aparece só na aba de e-mail; a do chat aparece
só no chat"): os chips do tipo e o "E-mail sem vínculo" saíram da Caixa — a
lista é a mesma rota com `?caixa=email` (a aba E-mail), os números saíram do
/resumo para GET /api/atendimento/email/contagens, e o "E-mails da venda" é só
da conversa de e-mail (o aviso da plataforma não entra mais no chat) — no chat,
só quantos e o id da conversa de e-mail (o link para a aba E-mail).

Postgres local de verdade (o mundo `cena` da ponte); nunca conta do Tuta.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, UserRole
from app.models.mail_atendimento import MailMessageMeta
from app.services.mail_atendimento import barra_lojas
from tests.test_atendimento_separa_email import _na_barra
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import (
    categorizar,
    conversa,
    entrar,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

API = "/api/atendimento"
EMAIL = f"{API}/email"


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


async def _formulario(db, cena, protocolo: str, caixa: str, cliente: str) -> UUID:
    return await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de=f"{caixa}@uranyx.com.br",
        de_nome="Site Uranyx",
        para=[f"{caixa}@uranyx.com.br"],
        assunto=f"[{protocolo}] Dúvida",
        corpo=f"Nome: Cliente\nE-mail: {cliente}\nMensagem: oi",
    )


def _ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["itens"]]


def _plataforma(resumo: dict, p: str) -> dict:
    return next(x for x in resumo["plataformas"] if x["plataforma"] == p)


# ─────────────── W1: os chips do tipo no grupo Site ───────────────


async def test_w1_tipo_do_chamado_filtra_a_lista_e_conta_no_resumo(db, cena, client, admin):
    sac = await _formulario(db, cena, "US-26-0901", "sac", "ana@gmail.com")
    duv = await _formulario(db, cena, "UDS-26-0902", "duvidas", "bia@gmail.com")
    await rodar(db)
    c_sac = (await meta_de(db, sac)).conversa_id
    c_duv = (await meta_de(db, duv)).conversa_id
    # Um e-mail de loja (não é chamado) para conferir que o tipo não o pega.
    await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)

    # Na Caixa (o chat), nunca: o chamado é e-mail (13/10/2026).
    r = await client.get(f"{API}/conversas", params={"plataforma": "site", "tipo_chamado": "sac"})
    assert _ids(r) == []
    email = {"caixa": "email"}
    r = await client.get(
        f"{API}/conversas", params={**email, "plataforma": "site", "tipo_chamado": "sac"}
    )
    assert _ids(r) == [str(c_sac)]
    r = await client.get(f"{API}/conversas", params={**email, "tipo_chamado": "duvidas"})
    assert _ids(r) == [str(c_duv)], "sem plataforma, o tipo já é só do site"
    r = await client.get(
        f"{API}/conversas", params={**email, "plataforma": "site", "tipo_chamado": "atacado"}
    )
    assert _ids(r) == []
    # Junto de qualquer filtro (o chamado novo espera resposta).
    r = await client.get(
        f"{API}/conversas",
        params={**email, "plataforma": "site", "tipo_chamado": "SAC", "filtro": "aguardando"},
    )
    assert _ids(r) == [str(c_sac)]
    r = await client.get(f"{API}/conversas", params={**email, "tipo_chamado": "outro"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "tipo_chamado_invalido"

    # Os números: da aba E-mail (o /resumo é da Caixa e não tem mais chamados).
    contagens = (await client.get(f"{EMAIL}/contagens")).json()
    assert contagens["chamados_por_tipo"] == {"sac": 1, "atacado": 0, "duvidas": 1}
    assert contagens["chamados"] == 2
    # Fechado sai da conta (como as etiquetas).
    c = await conversa(db, c_duv)
    c.situacao = "fechada"
    await db.commit()
    contagens = (await client.get(f"{EMAIL}/contagens")).json()
    assert contagens["chamados_por_tipo"] == {"sac": 1, "atacado": 0, "duvidas": 0}
    resumo = (await client.get(f"{API}/resumo")).json()
    assert all("chamados" not in p for p in resumo["plataformas"])


# ─────────────── W2: o filtro "E-mail sem vínculo" ───────────────


async def test_w2_email_sem_vinculo_filtra_e_conta(db, cena, client, admin):
    await pedido_existe(db, cena.ml, "2000012345678901")
    sem = await entrar(db, cena.geral, pasta="INBOX", assunto="Oi", corpo="Quando chega?")
    com = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="carla@gmail.com",
        assunto="Pedido 2000012345678901",
        corpo="Chegou quebrado.",
    )
    chamado = await _formulario(db, cena, "US-26-0903", "sac", "ana@gmail.com")
    await rodar(db)
    m_sem, m_com = await meta_de(db, sem), await meta_de(db, com)
    assert (m_sem.estado, m_com.estado) == ("sem_vinculo", "gravado")
    # Uma conversa da API sem pedido (pergunta) nunca é "e-mail sem vínculo".
    db.add(
        AtendimentoConversa(
            integration_id=cena.ml.id, plataforma="ml", canal="pergunta", externo_id="p-1"
        )
    )
    await db.commit()

    # Na Caixa (o chat), nunca (13/10/2026); na lista do e-mail, o filtro vale.
    r = await client.get(f"{API}/conversas", params={"filtro": "email_sem_vinculo"})
    assert _ids(r) == []
    email = {"caixa": "email", "filtro": "email_sem_vinculo"}
    r = await client.get(f"{API}/conversas", params=email)
    assert _ids(r) == [str(m_sem.conversa_id)]
    r = await client.get(f"{API}/conversas", params={**email, "plataforma": "ml"})
    assert _ids(r) == [str(m_sem.conversa_id)]
    r = await client.get(f"{API}/conversas", params={**email, "plataforma": "site"})
    assert _ids(r) == [], "o chamado do site nunca é 'sem vínculo'"
    assert (await meta_de(db, chamado)).conversa_id is not None

    # O /resumo é da Caixa: sem o número do e-mail (a fila "Sem vínculo" conta).
    resumo = (await client.get(f"{API}/resumo")).json()
    assert "email_sem_vinculo" not in resumo
    assert all("email_sem_vinculo" not in p for p in resumo["plataformas"])
    assert all("email_sem_vinculo" not in lj for lj in resumo["lojas"])

    # Vinculou ao pedido: sai do filtro.
    r = await client.post(f"{EMAIL}/emails/{sem}/vincular", json={"pedido": "2000012345678901"})
    assert r.status_code == 200, r.text
    r = await client.get(f"{API}/conversas", params=email)
    assert _ids(r) == []


async def test_w2_quem_so_le_ve_o_filtro_no_escopo(db, cena, client, make_user, auth_as):
    mid = await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    # Quem só lê (a fase de observação) usa o filtro como qualquer filtro da
    # lista — desde 27/10/2026 na lista da LOJA (a barra de lojas da Caixa: a
    # aba E-mail inteira é só de admin), num grupo (pasta) da barra.
    await _na_barra(db, (await meta_de(db, mid)).conversa_id, "envio")
    auth_as(await make_user(role=UserRole.USER))
    r = await client.get(
        f"{API}/conversas", params={"caixa": "email", "filtro": "email_sem_vinculo"}
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "aba_email_so_admin"
    loja = {"caixa": "email", "filtro": "email_sem_vinculo", "integration_id": str(cena.ml.id)}
    r = await client.get(f"{API}/conversas", params={**loja, "tipo_email": "chamado"})
    assert r.status_code == 403, "o tipo sem o grupo da barra: a aba E-mail"
    achadas = set()
    for grupo in barra_lojas.chaves():
        r = await client.get(f"{API}/conversas", params={**loja, "grupo_email": grupo})
        assert r.status_code == 200, r.text
        achadas |= {i["id"] for i in r.json()["itens"]}
    assert len(achadas) == 1


# ─────────────── W3: o nome da caixa nas pastas ───────────────


async def test_w3_pastas_trazem_o_nome_da_caixa(db, cena, client, admin):
    await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    r = (await client.get(f"{EMAIL}/pastas")).json()
    assert {"id": str(cena.geral.id), "nome": "Geral — Tuta"} in r["caixas"]
    usadas = {p["mailbox_id"] for p in r["itens"]}
    assert {c["id"] for c in r["caixas"]} == usadas, "só as caixas que têm pasta na lista"
    assert "plataformas" in r and "finalidades" in r


# ─────────────── W4: "E-mails da venda" no painel ④ ───────────────


async def test_w4_emails_da_venda_resume_a_conversa_e_a_familia(db, cena, client, admin):
    pack = AtendimentoConversa(
        integration_id=cena.ml.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-9",
        pedido_marketplace="2000012345678901",
        situacao="respondida",
    )
    db.add(pack)
    await db.commit()
    await pedido_existe(db, cena.ml, "2000012345678901")
    # O cliente escreve com o pedido: a conversa de e-mail da loja, ligada ao pedido.
    for corpo in ("Chegou quebrado.", "Alguma novidade?"):
        await entrar(
            db,
            cena.geral,
            pasta="INBOX",
            assunto="Pedido 2000012345678901",
            corpo=corpo,
        )
    # O aviso do ML com o pedido: desde 13/10/2026, na conversa de E-MAIL do
    # pedido (nunca no chat); desde 21/10, pela pasta de VENDA (categoria venda).
    await categorizar(db, cena.geral, "vendas ml", "venda")
    aviso = await entrar(
        db,
        cena.geral,
        pasta="vendas ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Nova mensagem no pedido 2000012345678901",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    email = (await meta_de(db, aviso)).conversa_id
    assert email != pack.id

    r = await client.get(f"{EMAIL}/conversas/{email}/emails-da-venda")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    corpo = r.json()
    assert corpo["total"] == 3
    [caixa] = corpo["caixas"]
    assert caixa["caixa"] == "21max@tuta.com" and caixa["loja"] == "Barbosa"
    assert (caixa["plataforma"], caixa["quantidade"]) == ("ml", 3) and caixa["ultimo_em"]
    assert caixa["pastas"] == [
        {"pasta": "INBOX", "finalidade": "entrada", "destaque": False, "quantidade": 2},
        {"pasta": "vendas ml", "finalidade": "vendas", "destaque": False, "quantidade": 1},
    ]
    # Nada do e-mail em si: nem texto, nem assunto, nem remetente.
    plano = r.text
    for proibido in ("quebrado", "novidade", "maria@gmail.com", "Pedido 2000012345678901"):
        assert proibido not in plano, proibido

    # O chat do pedido não mostra e-mail (13/10/2026): só QUANTOS e o id da
    # conversa de e-mail (o link para a aba E-mail) — nem caixa, nem pasta
    # (tests/test_atendimento_separa_email_venda.py).
    r = await client.get(f"{EMAIL}/conversas/{pack.id}/emails-da-venda")
    assert r.json() == {
        "total": 3,
        "caixas": [],
        "aguardando": 1,
        "conversas": [{"id": str(email), "emails": 3, "aguardando": True}],
    }
    for proibido in ("quebrado", "novidade", "maria@gmail.com", "21max", "problema ml"):
        assert proibido not in r.text, proibido
    ligadas = set(
        (
            await db.execute(
                select(MailMessageMeta.conversa_id).where(MailMessageMeta.conversa_id.is_not(None))
            )
        ).scalars()
    )
    assert ligadas == {email}, "os três e-mails na conversa de e-mail do pedido"


async def test_w4_sem_email_e_fora_do_escopo(db, cena, client, admin, make_user, auth_as):
    sozinha = AtendimentoConversa(
        integration_id=cena.shopee.id, plataforma="shopee", canal="chat", externo_id="chat-1"
    )
    db.add(sozinha)
    await db.commit()
    r = (await client.get(f"{EMAIL}/conversas/{sozinha.id}/emails-da-venda")).json()
    assert r == {"total": 0, "caixas": []}
    r = await client.get(f"{EMAIL}/conversas/00000000-0000-0000-0000-000000000000/emails-da-venda")
    assert r.status_code == 404
