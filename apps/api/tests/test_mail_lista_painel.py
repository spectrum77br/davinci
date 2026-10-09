"""O e-mail das lojas na LISTA e no PAINEL ④ do /atendimento (09/10/2026).

RF1/RF6: os chips SAC / Atacado / Dúvidas e sugestões do grupo Site
(`?tipo_chamado=` sobre `dados.mail.caixa`, com os números no /resumo, sem
mexer no motor de etiqueta). RF1/RF5: o filtro "E-mail sem vínculo" na lista
(a conversa de e-mail da ponte, aberta, sem pedido, fora de site), com os
números por plataforma e por loja. RF5: o nome da caixa nas Pastas (a tela
Pastas e regras agrupa por caixa). RF1 ④: o resumo "E-mails da venda" (caixa
→ loja, pasta, quantos) da conversa e da família dela.

Postgres local de verdade (o mundo `cena` da ponte); nunca conta do Tuta.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, UserRole
from app.models.mail_atendimento import MailMessageMeta
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte
from tests.test_mail_ponte import conversa, entrar, meta_de, pedido_existe, rodar

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
        pasta=f"*uranyx {caixa}",
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
    await entrar(db, cena.geral, pasta="mensagens ml")
    await rodar(db)

    r = await client.get(f"{API}/conversas", params={"plataforma": "site", "tipo_chamado": "sac"})
    assert _ids(r) == [str(c_sac)]
    r = await client.get(f"{API}/conversas", params={"tipo_chamado": "duvidas"})
    assert _ids(r) == [str(c_duv)], "sem plataforma, o tipo já é só do site"
    r = await client.get(
        f"{API}/conversas", params={"plataforma": "site", "tipo_chamado": "atacado"}
    )
    assert _ids(r) == []
    # Junto de qualquer filtro (o chamado novo espera resposta).
    r = await client.get(
        f"{API}/conversas",
        params={"plataforma": "site", "tipo_chamado": "SAC", "filtro": "aguardando"},
    )
    assert _ids(r) == [str(c_sac)]
    r = await client.get(f"{API}/conversas", params={"tipo_chamado": "outro"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "tipo_chamado_invalido"

    resumo = (await client.get(f"{API}/resumo")).json()
    assert _plataforma(resumo, "site")["chamados"] == {"sac": 1, "atacado": 0, "duvidas": 1}
    # Fechado sai da conta (como as etiquetas).
    c = await conversa(db, c_duv)
    c.situacao = "fechada"
    await db.commit()
    resumo = (await client.get(f"{API}/resumo")).json()
    assert _plataforma(resumo, "site")["chamados"] == {"sac": 1, "atacado": 0, "duvidas": 0}
    assert _plataforma(resumo, "ml").get("chamados") == {}


# ─────────────── W2: o filtro "E-mail sem vínculo" ───────────────


async def test_w2_email_sem_vinculo_filtra_e_conta(db, cena, client, admin):
    await pedido_existe(db, cena.ml, "2000012345678901")
    sem = await entrar(db, cena.geral, pasta="mensagens ml", assunto="Oi", corpo="Quando chega?")
    com = await entrar(
        db,
        cena.geral,
        pasta="problema ml",
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

    r = await client.get(f"{API}/conversas", params={"filtro": "email_sem_vinculo"})
    assert _ids(r) == [str(m_sem.conversa_id)]
    r = await client.get(
        f"{API}/conversas", params={"filtro": "email_sem_vinculo", "plataforma": "ml"}
    )
    assert _ids(r) == [str(m_sem.conversa_id)]
    r = await client.get(
        f"{API}/conversas", params={"filtro": "email_sem_vinculo", "plataforma": "site"}
    )
    assert _ids(r) == [], "o chamado do site nunca é 'sem vínculo'"
    assert (await meta_de(db, chamado)).conversa_id is not None

    resumo = (await client.get(f"{API}/resumo")).json()
    assert resumo["email_sem_vinculo"] == 1
    assert _plataforma(resumo, "ml")["email_sem_vinculo"] == 1
    assert _plataforma(resumo, "site")["email_sem_vinculo"] == 0
    loja = next(lj for lj in resumo["lojas"] if lj["integration_id"] == str(cena.ml.id))
    assert loja["email_sem_vinculo"] == 1

    # Vinculou ao pedido: sai do filtro e da conta.
    r = await client.post(f"{EMAIL}/emails/{sem}/vincular", json={"pedido": "2000012345678901"})
    assert r.status_code == 200, r.text
    r = await client.get(f"{API}/conversas", params={"filtro": "email_sem_vinculo"})
    assert _ids(r) == []
    assert (await client.get(f"{API}/resumo")).json()["email_sem_vinculo"] == 0


async def test_w2_quem_so_le_ve_o_filtro_no_escopo(db, cena, client, make_user, auth_as):
    await entrar(db, cena.geral, pasta="mensagens ml")
    await rodar(db)
    # Quem só lê (a fase de observação) usa o filtro como qualquer filtro da lista.
    auth_as(await make_user(role=UserRole.USER))
    r = await client.get(f"{API}/conversas", params={"filtro": "email_sem_vinculo"})
    assert r.status_code == 200, r.text


# ─────────────── W3: o nome da caixa nas pastas ───────────────


async def test_w3_pastas_trazem_o_nome_da_caixa(db, cena, client, admin):
    await entrar(db, cena.geral, pasta="mensagens ml")
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
            pasta="problema ml",
            assunto="Pedido 2000012345678901",
            corpo=corpo,
        )
    # O aviso do ML com o pedido: na própria conversa da API.
    aviso = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Nova mensagem no pedido 2000012345678901",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    assert (await meta_de(db, aviso)).conversa_id == pack.id

    r = await client.get(f"{EMAIL}/conversas/{pack.id}/emails-da-venda")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    corpo = r.json()
    assert corpo["total"] == 3
    [caixa] = corpo["caixas"]
    assert caixa["caixa"] == "21max@tuta.com" and caixa["loja"] == "Barbosa"
    assert (caixa["plataforma"], caixa["quantidade"]) == ("ml", 3) and caixa["ultimo_em"]
    assert caixa["pastas"] == [
        {"pasta": "problema ml", "finalidade": "problema", "destaque": True, "quantidade": 2},
        {"pasta": "mensagens ml", "finalidade": "mensagens", "destaque": False, "quantidade": 1},
    ]
    # Nada do e-mail em si: nem texto, nem assunto, nem remetente.
    plano = r.text
    for proibido in ("quebrado", "novidade", "maria@gmail.com", "Pedido 2000012345678901"):
        assert proibido not in plano, proibido

    # A conversa de e-mail vê a mesma venda.
    email = (await meta_de(db, (await _um_email_da_conversa(db, pack.id)))).conversa_id
    r = (await client.get(f"{EMAIL}/conversas/{email}/emails-da-venda")).json()
    assert r["total"] == 3


async def _um_email_da_conversa(db, pack_id: UUID) -> UUID:
    """Um e-mail do cliente (o que NÃO está na conversa da API)."""
    return await db.scalar(
        select(MailMessageMeta.message_id).where(
            MailMessageMeta.conversa_id != pack_id, MailMessageMeta.conversa_id.is_not(None)
        )
    )


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
