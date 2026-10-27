# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""As caixas SAC e Admin dos SITES na barra de lojas da Caixa (27/10/2026).

O Eduardo, 27/10: cada site (Uranyx e Charlots) tem dois endereços de contato
— sac@<domínio> (o formulário SAC) e admin@<domínio> ("Fale com a gente" e
"Atacado"; os antigos duvidas@ e atacado@ ainda podem receber) —, e o Tuta
manda todos de um site para UMA pasta ("*uranyx", "*charlots"). Na barra, a
linha do site ganha o ampliável com DUAS caixas, SAC e Admin, pelo endereço
que RECEBEU; a resposta sai pelo mesmo endereço (SAC como sac@, Admin como
admin@). O e-mail da equipe vem do remetente técnico do site
(nao-responda@envio.<domínio>, o Brevo) com o Reply-To = o cliente.

O que não pode falhar:
  • admin@ de um domínio de MARCA é caixa de site (antes: endereço interno,
    ignorado); admin@ de fora continua interno;
  • o formulário pelo remetente técnico: o cliente é o Reply-To, o protocolo
    (US/UA/UDS, CS/CA/CDS) decide o tipo e a conversa é o chamado do site;
  • a resposta: SAC pelo sac@, Admin pelo admin@ (o antigo duvidas@ também),
    para o cliente do Reply-To;
  • GET …/email/contagens?por_loja=true → `sites_na_barra` com SAC e Admin; a
    lista (GET …/lojas/emails?site=…&grupo=sac|admin) e o e-mail inteiro; o
    e-mail de segurança da pasta do site fica fora; quem não vê todas as
    equipes não tem as caixas dos sites.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, Marca, MailMailbox, StoreInfo, UserRole
from app.security.cipher import decrypt_json, encrypt_json
from app.services.mail_atendimento import barra_lojas, pedido, responder, rotear
from app.services.mail_atendimento import constantes as mail_constantes
from tests.test_atendimento_separa_email import URL
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_mail_ponte import (
    ALIASES_GERAL,
    GERAL,
    _config,
    categorizar,
    cena,
    conversa,
    entrar,
    meta_de,
    rodar,
)
from tests.test_mail_responder import _jobs, _ligar_envio, _responder

EMAIL = f"{URL}/email"
DO_SITE = [
    "admin@uranyx.com.br",
    "sac@charlots.com.br",
    "admin@charlots.com.br",
]
BREVO_U = "nao-responda@envio.uranyx.com.br"
BREVO_C = "nao-responda@envio.charlots.com.br"


async def _com_os_enderecos_do_site(db, cena) -> None:
    """Os aliases novos na caixa geral e a marca da Charlots (o domínio dela)."""
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.config_enc = encrypt_json(
        {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, *DO_SITE]}
    )
    db.add(Marca(nome="Charlots Park", slug="charlots-park", dominio_br="charlots.com.br"))
    await db.commit()
    rotear.esquecer(db)


async def _pastas_dos_sites(db, cena) -> None:
    for nome in ("*uranyx", "*charlots"):
        await categorizar(db, cena.geral, nome, "site")


# ─────────────── puras ───────────────


def test_admin_e_caixa_de_site_so_no_dominio_da_marca():
    assert "admin" in mail_constantes.LOCAIS_DE_SITE
    assert "admin" in mail_constantes.LOCAIS_INTERNOS
    cad = rotear.Cadastro()
    cad.marcas["uranyx"] = (None, "Uranyx", ("uranyx.com.br", "uranyx.com"))
    assert rotear.e_caixa_de_site("admin@uranyx.com.br", cad)
    assert not rotear.e_caixa_de_site("admin@escritorio.com.br", cad)
    rota = rotear._rota_do_site("admin@uranyx.com.br", cad, marca_pasta=None, caixa_pasta=None)
    assert rota is not None and rota.marca_slug == "uranyx" and rota.tipo_caixa is None


def test_a_caixa_do_site_pelo_endereco_que_recebeu():
    assert barra_lojas.caixa_do_alias("sac@uranyx.com.br") == "sac"
    assert barra_lojas.caixa_do_alias("SAC@Charlots.com.br") == "sac"
    assert barra_lojas.caixa_do_alias("sac@uranyx.com") == "sac"
    for outro in ("admin@uranyx.com.br", "duvidas@uranyx.com.br", "atacado@uranyx.com.br",
                  "app@uranyx.com.br", "support@uranyx.com", None, ""):
        assert barra_lojas.caixa_do_alias(outro) == "admin", outro
    assert [k for k, _ in barra_lojas.caixas_do_site()] == ["sac", "admin"]
    assert barra_lojas.site_da_pasta("*uranyx") == "uranyx"
    assert barra_lojas.site_da_pasta("*Charlots") == "charlots"
    # As pastas antigas ficam como estão (não são as caixas da barra).
    assert barra_lojas.site_da_pasta("*uranyx sac") is None
    assert barra_lojas.site_da_pasta("*poofy") is None
    assert [s.externo_ref for s in barra_lojas.sites()] == ["site:uranyx", "site:charlots"]


def test_quem_responde_o_email_do_site():
    assert responder.de_do_site("sac@uranyx.com.br") == ["sac@uranyx.com.br"]
    assert responder.de_do_site("ADMIN@uranyx.com.br") == ["admin@uranyx.com.br"]
    assert responder.de_do_site("duvidas@uranyx.com.br") == [
        "admin@uranyx.com.br",
        "duvidas@uranyx.com.br",
    ]
    assert responder.de_do_site(None) == []


@pytest.mark.parametrize(
    ("assunto", "numero", "letra", "tipo"),
    [
        ("[US-26-0123] SAC — Hotwav A17 — Maria", "US-26-0123", "U", "sac"),
        ("[US-26-0124] SAC (app) — Hotwav A17 — Maria", "US-26-0124", "U", "sac"),
        ("[UA-26-0045] Atacado — Loja X — Preço", "UA-26-0045", "U", "atacado"),
        ("[UDS-26-0067] Fale com a gente — Ana", "UDS-26-0067", "U", "duvidas"),
        ("[CS-26-0123] SAC — Vestido — Joana", "CS-26-0123", "C", "sac"),
        ("[CA-26-0001] Atacado — Boutique — Grade", "CA-26-0001", "C", "atacado"),
        ("[CDS-26-0002] Fale com a gente — Lia", "CDS-26-0002", "C", "duvidas"),
    ],
)
def test_os_protocolos_dos_assuntos_novos(assunto, numero, letra, tipo):
    p = pedido.protocolo(assunto)
    assert p is not None and (p.numero, p.letra, p.tipo, p.valido) == (numero, letra, tipo, True)


def test_cliente_pelo_reply_to_so_no_formulario_do_site():
    cad = rotear.Cadastro()
    cad.marcas["uranyx"] = (None, "Uranyx", ("uranyx.com.br",))
    base = {
        "de": BREVO_U,
        "reply_to": ["Maria@Gmail.com"],
        "nossos": ["admin@uranyx.com.br"],
        "conhecidos": {"admin@uranyx.com.br", "sac@uranyx.com.br"},
        "cad": cad,
    }
    assert rotear.cliente_pelo_reply_to(**base) == "maria@gmail.com"
    # Remetente de pessoa, de fora da marca, sem Reply-To, dois clientes, ou não é caixa de site.
    assert rotear.cliente_pelo_reply_to(**{**base, "de": "maria@uranyx.com.br"}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "de": "noreply@brevo.com"}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "reply_to": []}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "reply_to": ["ana@gmail.com", "bia@hotmail.com"]}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "reply_to": ["sac@uranyx.com.br"]}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "reply_to": ["noreply@loja.com.br"]}) is None
    assert rotear.cliente_pelo_reply_to(**{**base, "nossos": ["21max@tuta.com"]}) is None


# ─────────────── a ponte ───────────────


async def test_email_para_o_admin_da_marca_vira_conversa_do_site(db, cena):
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de="ana@gmail.com",
        de_nome="Ana",
        para=["Admin@Uranyx.com.br"],
        assunto="Re: [UDS-26-0067] Fale com a gente — Ana",
        corpo="Obrigada pela resposta!",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado", (m.estado, m.motivo)
    assert (m.alias_recebido, m.marca_id, m.protocolo, m.tipo_caixa) == (
        "admin@uranyx.com.br",
        cena.marca.id,
        "UDS-26-0067",
        "duvidas",
    )
    c = await conversa(db, m.conversa_id)
    assert (c.plataforma, c.integration_id, c.externo_id) == (
        "site",
        None,
        "mail-protocolo:UDS-26-0067",
    )
    assert c.comprador_id == "ana@gmail.com" and c.aguardando_resposta is True


async def test_admin_fora_do_dominio_da_marca_continua_interno(db, cena):
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.config_enc = encrypt_json(
        {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, "admin@escritorio.com.br"]}
    )
    await db.commit()
    mid = await entrar(db, cena.geral, para=["admin@escritorio.com.br"], de="ti@fornecedor.com")
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.conversa_id) == ("ignorado", "alias_interno", None)


@pytest.mark.parametrize(
    ("de", "para", "pasta", "assunto", "protocolo", "tipo", "marca"),
    [
        (BREVO_U, "sac@uranyx.com.br", "*uranyx", "[US-26-0123] SAC — A17 — Maria",
         "US-26-0123", "sac", "uranyx"),
        (BREVO_U, "admin@uranyx.com.br", "*uranyx", "[UA-26-0045] Atacado — Loja X — Preço",
         "UA-26-0045", "atacado", "uranyx"),
        (BREVO_U, "admin@uranyx.com.br", "INBOX", "[UDS-26-0067] Fale com a gente — Maria",
         "UDS-26-0067", "duvidas", "uranyx"),
        (BREVO_C, "sac@charlots.com.br", "*charlots", "[CS-26-0123] SAC — Vestido — Maria",
         "CS-26-0123", "sac", "charlots-park"),
        (BREVO_C, "admin@charlots.com.br", "*charlots", "[CA-26-0001] Atacado — Boutique — Grade",
         "CA-26-0001", "atacado", "charlots-park"),
    ],
)
async def test_formulario_pelo_remetente_tecnico_o_cliente_e_o_reply_to(
    db, cena, de, para, pasta, assunto, protocolo, tipo, marca
):
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    mid = await entrar(
        db,
        cena.geral,
        pasta=pasta,
        de=de,
        de_nome="Site",
        para=[para],
        reply_to="Maria.Cliente@Gmail.com",
        assunto=assunto,
        corpo="Nome: Maria Cliente\nTelefone: (11) 99999-0000\nMensagem: preciso de ajuda "
        "com a senha do app",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado", (m.estado, m.motivo)
    assert (m.protocolo, m.tipo_caixa, m.alias_recebido, m.remetente_tipo) == (
        protocolo,
        tipo,
        para,
        "comprador",
    )
    c = await conversa(db, m.conversa_id)
    assert c.plataforma == "site" and c.dados["mail"]["marca"] == marca
    assert c.externo_id == f"mail-protocolo:{protocolo}"
    assert c.comprador_id == "maria.cliente@gmail.com"
    assert c.comprador_nome == "Maria Cliente"
    assert c.aguardando_resposta is True
    assert not any(a["codigo"] == "tipo_x_caixa" for a in m.alertas or [])


async def test_resposta_do_cliente_ao_admin_volta_ao_mesmo_chamado(db, cena):
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    primeiro = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de=BREVO_U,
        para=["admin@uranyx.com.br"],
        reply_to="compras@revenda.com.br",
        assunto="[UA-26-0045] Atacado — Revenda — Preço",
        corpo="Nome: João\nMensagem: tabela de preços",
    )
    await rodar(db)
    m1 = await meta_de(db, primeiro)
    segundo = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de="compras@revenda.com.br",
        para=["admin@uranyx.com.br"],
        assunto="Re: [UA-26-0045] Atacado — Revenda — Preço",
        corpo="Pode mandar.",
    )
    await rodar(db)
    m2 = await meta_de(db, segundo)
    assert m2.estado == "gravado" and m2.conversa_id == m1.conversa_id


# ─────────────── a resposta: pelo endereço que recebeu ───────────────


async def _chamado(db, cena, **kw):
    mid = await entrar(db, cena.geral, **kw)
    await rodar(db)
    meta = await meta_de(db, mid)
    assert meta.conversa_id is not None, (meta.estado, meta.motivo)
    return mid, meta.conversa_id


async def test_admin_responde_como_admin_para_o_cliente_do_reply_to(db, cena, client, admin):
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    _mid, cid = await _chamado(
        db,
        cena,
        pasta="*uranyx",
        de=BREVO_U,
        para=["admin@uranyx.com.br"],
        reply_to="compras@revenda.com.br",
        assunto="[UA-26-0045] Atacado — Revenda — Preço",
        corpo="Nome: João\nMensagem: tabela de preços",
    )
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{cid}/previa")).json()
    # Antes: o e-mail do TIPO (atacado@); agora o que recebeu (admin@).
    assert (previa["de"], previa["para"]) == ("admin@uranyx.com.br", "compras@revenda.com.br")
    assert previa["formulario"] is False
    r = await _responder(client, cid, texto="Segue a tabela.")
    assert r.status_code == 200, r.text
    [job] = await _jobs(db)
    carga = decrypt_json(job.content_enc)
    assert (carga["from_address"], carga["to"]) == (
        "admin@uranyx.com.br",
        "compras@revenda.com.br",
    )
    assert carga["subject"].startswith("Re: [UA-26-0045]")


async def test_sac_responde_como_sac_mesmo_com_protocolo_de_atacado(db, cena, client, admin):
    await _com_os_enderecos_do_site(db, cena)
    _mid, cid = await _chamado(
        db,
        cena,
        pasta="INBOX",
        de=BREVO_U,
        para=["sac@uranyx.com.br"],
        reply_to="loja@revenda.com.br",
        assunto="[UA-26-0002] Atacado — Revenda — Grade",
        corpo="Mensagem: quero comprar",
    )
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{cid}/previa")).json()
    assert (previa["de"], previa["para"]) == ("sac@uranyx.com.br", "loja@revenda.com.br")


async def test_o_antigo_duvidas_responde_como_admin(db, cena, client, admin):
    await _com_os_enderecos_do_site(db, cena)
    _mid, cid = await _chamado(
        db,
        cena,
        pasta="INBOX",
        de="ana@gmail.com",
        para=["duvidas@uranyx.com.br"],
        assunto="Re: [UDS-26-0004] Dúvida sobre o app",
        corpo="Ainda não funcionou.",
    )
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{cid}/previa")).json()
    assert (previa["de"], previa["para"]) == ("admin@uranyx.com.br", "ana@gmail.com")
    # Com o remetente estrito (a Central só aceita quem recebeu): o próprio duvidas@.
    await _ligar_envio(db, cena.geral, remetente_estrito=True)
    previa = (await client.get(f"{EMAIL}/conversas/{cid}/previa")).json()
    assert previa["de"] == "duvidas@uranyx.com.br"


# ─────────────── a barra: SAC e Admin embaixo de cada site ───────────────


async def _cena_da_barra(db, cena) -> dict[str, str]:
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    ids = {}
    ids["sac"] = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de=BREVO_U,
        para=["sac@uranyx.com.br"],
        reply_to="maria@gmail.com",
        assunto="[US-26-0123] SAC — A17 — Maria",
        corpo="Nome: Maria\nMensagem: a tela apagou",
    )
    ids["admin"] = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de=BREVO_U,
        para=["admin@uranyx.com.br"],
        reply_to="compras@revenda.com.br",
        assunto="[UA-26-0045] Atacado — Revenda — Preço",
        corpo="Nome: João\nMensagem: tabela",
    )
    # O código de acesso de uma conta da marca na mesma pasta: fica fora da caixa.
    ids["seguranca"] = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de="no-reply@accounts.google.com",
        para=["admin@uranyx.com.br"],
        assunto="Seu código de verificação",
        corpo="Seu código é 482913",
    )
    ids["charlots"] = await entrar(
        db,
        cena.geral,
        pasta="*charlots",
        de=BREVO_C,
        para=["sac@charlots.com.br"],
        reply_to="joana@gmail.com",
        assunto="[CS-26-0007] SAC — Vestido — Joana",
        corpo="Nome: Joana\nMensagem: troca",
    )
    await rodar(db)
    for chave, mid in ids.items():
        ids[chave] = str(mid)
    return ids


async def _sites(client) -> dict[str, dict]:
    r = await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})
    assert r.status_code == 200, r.text
    return {s["site"]: s for s in r.json()["sites_na_barra"]}


async def _lista(client, site, grupo):
    return await client.get(f"{EMAIL}/lojas/emails", params={"site": site, "grupo": grupo})


async def test_os_numeros_e_as_listas_das_caixas_dos_sites(db, cena, client, admin):
    ids = await _cena_da_barra(db, cena)
    sites = await _sites(client)
    u, c = sites["uranyx"], sites["charlots"]
    assert (u["externo_ref"], u["nome"], u["pastas"], u["status"]) == (
        "site:uranyx",
        "Uranyx",
        ["*uranyx"],
        "liga",
    )
    assert [x["chave"] for x in u["caixas"]] == ["sac", "admin"]
    assert u["grupos"]["sac"] == {"emails": 1, "abertas": 1, "aguardando": 1, "total": 1}
    assert u["grupos"]["admin"] == {"emails": 1, "abertas": 1, "aguardando": 1, "total": 1}
    assert u["emails_30d"] == 2
    assert c["grupos"]["sac"]["emails"] == 1 and c["grupos"]["admin"]["emails"] == 0

    r = await _lista(client, "uranyx", "sac")
    assert r.status_code == 200, r.text
    [item] = r.json()["itens"]
    assert item["id"] == ids["sac"] and item["conversa_id"] and item["aguardando_resposta"]
    r = await _lista(client, "uranyx", "admin")
    assert [i["id"] for i in r.json()["itens"]] == [ids["admin"]]
    assert ids["seguranca"] not in {i["id"] for i in r.json()["itens"]}
    r = await _lista(client, "charlots", "sac")
    assert [i["id"] for i in r.json()["itens"]] == [ids["charlots"]]
    # O e-mail inteiro (o da caixa do site) e o de segurança (404).
    r = await client.get(f"{EMAIL}/lojas/emails/{ids['admin']}")
    assert r.status_code == 200 and r.json()["loja"] == "Uranyx"
    r = await client.get(f"{EMAIL}/lojas/emails/{ids['seguranca']}")
    assert r.status_code == 404
    # Parâmetros errados.
    assert (await _lista(client, "makisa", "sac")).status_code == 422
    assert (await _lista(client, "uranyx", "problemas")).status_code == 422


async def test_pasta_do_site_sem_categoria_diz_por_que(db, cena, client, admin):
    await _com_os_enderecos_do_site(db, cena)
    await categorizar(db, cena.geral, "*uranyx", None)
    sites = await _sites(client)
    assert sites["uranyx"]["status"] == "sem_categoria"
    assert sites["charlots"]["status"] == "sem_pasta"


async def test_quem_nao_e_admin_ve_as_caixas_do_site_pela_barra(
    db, cena, client, quem_le, auth_as
):
    ids = await _cena_da_barra(db, cena)
    auth_as(quem_le)
    sites = await _sites(client)
    assert sites["uranyx"]["grupos"]["sac"]["emails"] == 1
    r = await _lista(client, "uranyx", "admin")
    assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [ids["admin"]]
    cid = r.json()["itens"][0]["conversa_id"]
    # A conversa do site abre (os cartões dela) como a das lojas.
    assert (await client.get(f"{EMAIL}/conversas/{cid}/emails")).status_code == 200


async def test_quem_ve_so_a_equipe_nao_tem_as_caixas_dos_sites(
    db, cena, client, make_user, auth_as
):
    await _cena_da_barra(db, cena)
    loja = await db.get(StoreInfo, cena.loja_ml.id)
    loja.sales_team = 7
    membro = await make_user()
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    assert await _sites(client) == {}
    r = await _lista(client, "uranyx", "sac")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "email_do_site_fora_do_escopo"
