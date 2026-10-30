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
    equipes não tem as caixas dos sites;
  • Makisa e 7buyers (sites só de e-mail, `so_email`): as caixas pela pasta
    (sem a categoria; a pasta sem categoria guarda sem o alias — a caixa sai
    do endereço que recebeu), o código guardado sem a régua fica fora, a pasta
    que não chegou diz por quê; admin@makisa/admin@7buyers nunca viram
    chamado do site (admin@ só é caixa de site na Uranyx e na Charlots).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import (
    AtendimentoConversa,
    MailAttachment,
    MailMailbox,
    Marca,
    StoreInfo,
    UserRole,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.mail_atendimento import barra_lojas, pedido, responder, rotear
from app.services.mail_atendimento import constantes as mail_constantes
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_separa_email import URL
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_mail_ponte import (
    ALIASES_GERAL,
    ALIASES_GOSLIN,
    GERAL,
    GOSLIN,
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


async def test_admin_cadastrado_nao_entra_na_rota_pelo_cadastro(db, cena):
    """28/10/2026: o admin@ em `marca_emails` (Cadastros › E-mails) não vira caixa
    pelo cadastro — o da Locagil continua interno; o da Uranyx segue caixa de site
    pelo NOME (o tipo do protocolo/pasta, as travas de sempre)."""
    from app.models import MarcaEmail

    locagil = Marca(nome="locagil", slug="locagil", dominio_br="locagil.com.br")
    db.add(locagil)
    await db.flush()
    uranyx = (await db.execute(select(Marca).where(Marca.slug == "uranyx"))).scalar_one()
    db.add(MarcaEmail(marca_id=locagil.id, tipo="admin", email="admin@locagil.com.br"))
    await db.commit()
    rotear.esquecer(db)
    cad = await rotear.cadastro(db)
    assert "admin@locagil.com.br" not in cad.caixas
    assert "admin@uranyx.com.br" not in cad.caixas  # o da cena (sac + admin)
    assert "sac@uranyx.com.br" in cad.caixas
    assert cad.caixas["sac@uranyx.com.br"].marca_id == uranyx.id
    assert not rotear.e_caixa_de_site("admin@locagil.com.br", cad)
    assert rotear.e_caixa_de_site("admin@uranyx.com.br", cad)
    rota = rotear._rota_do_site(
        "admin@uranyx.com.br", cad, marca_pasta=None, caixa_pasta="atacado"
    )
    assert rota is not None and rota.tipo_caixa == "atacado"


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
    # Makisa e 7buyers (27/10/2026): sites só de e-mail (fora dos SITES do atendimento).
    assert barra_lojas.site_da_pasta("*Makisa") == "makisa"
    assert barra_lojas.site_da_pasta("*7buyers") == "7buyers"
    assert [(s.externo_ref, s.so_email) for s in barra_lojas.sites()] == [
        ("site:uranyx", False),
        ("site:charlots", False),
        ("site:makisa", True),
        ("site:7buyers", True),
    ]


def test_o_codigo_guardado_sem_a_regua_fica_fora_do_site():
    codigo = ("Seu código de verificação", "Use o código 482913 para entrar na sua conta.")
    # O guardado sem categoria e o duplicado: a régua estrita da ponte, na hora.
    assert barra_lojas.escondido_no_site("ignorado", *codigo)
    assert barra_lojas.escondido_no_site("duplicado", *codigo)
    assert not barra_lojas.escondido_no_site("ignorado", "Dúvida", "Quando chega meu pedido?")
    # O de segurança sempre fora; o que passou pela régua (a conversa) fica.
    assert barra_lojas.escondido_no_site("seguranca", "Oi", "Oi")
    assert not barra_lojas.escondido_no_site("gravado", *codigo)


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
    dois = ["ana@gmail.com", "bia@hotmail.com"]
    assert rotear.cliente_pelo_reply_to(**{**base, "reply_to": dois}) is None
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
    # A assinatura da caixa Admin (28/10/2026): a de Cadastros › E-mails.
    assert previa["assinatura"].startswith(
        "Atenciosamente,\nEquipe Uranyx\nAdministrativo\nE-mail: admin@uranyx.com.br\n"
    )
    assert "Administrativo" in previa["assinatura_html"]
    assert 'href="mailto:admin@uranyx.com.br"' in previa["assinatura_html"]
    r = await _responder(client, cid, texto="Segue a tabela.\n<sem tag>")
    assert r.status_code == 200, r.text
    [job] = await _jobs(db)
    carga = decrypt_json(job.content_enc)
    assert (carga["from_address"], carga["to"]) == (
        "admin@uranyx.com.br",
        "compras@revenda.com.br",
    )
    assert carga["subject"].startswith("Re: [UA-26-0045]")
    # O corpo em HTML: o texto da pessoa ESCAPADO, a assinatura e a citação.
    assert "Segue a tabela.<br>&lt;sem tag&gt;" in carga["html"]
    assert previa["assinatura_html"] in carga["html"]
    assert "<blockquote" in carga["html"]


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
    assert "Serviço de Atendimento ao Consumidor" in previa["assinatura_html"]
    assert 'href="mailto:sac@uranyx.com.br"' in previa["assinatura_html"]


async def test_charlots_assina_como_a_charlots_de_cadastros_emails(db, cena, client, admin):
    """O chamado da Charlots é da `charlots-park` (a letra C); a assinatura é a
    da `poofy` (Redes Sociais / Cadastros › E-mails): logo, site e WhatsApp."""
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    db.add(
        Marca(
            nome="charlots",
            slug="poofy",
            dominio_br="charlots.com.br",
            site="https://charlots.com.br",
            sac_fone="11977770710",
        )
    )
    await db.commit()
    rotear.esquecer(db)
    _mid, cid = await _chamado(
        db,
        cena,
        pasta="*charlots",
        de=BREVO_C,
        para=["sac@charlots.com.br"],
        reply_to="maria@gmail.com",
        assunto="[CS-26-0123] SAC — Vestido — Maria",
        corpo="Nome: Maria\nMensagem: o zíper quebrou",
    )
    conversa = await db.get(AtendimentoConversa, cid)
    assert conversa.dados["mail"]["marca"] == "charlots-park"
    # O sac@ salvo na linha da Charlots de Cadastros › E-mails (a `poofy`, o
    # botão "padrão") não puxa o chamado para a `poofy`: a rota segue pela
    # letra C e pela pasta.
    from app.models import MarcaEmail

    poofy = (await db.execute(select(Marca).where(Marca.slug == "poofy"))).scalar_one()
    db.add(MarcaEmail(marca_id=poofy.id, tipo="sac", email="sac@charlots.com.br"))
    await db.commit()
    rotear.esquecer(db)
    assert "sac@charlots.com.br" not in (await rotear.cadastro(db)).caixas
    _mid2, cid2 = await _chamado(
        db,
        cena,
        pasta="*charlots",
        de=BREVO_C,
        para=["sac@charlots.com.br"],
        reply_to="joana@gmail.com",
        assunto="[CS-26-0124] SAC — Saia — Joana",
        corpo="Nome: Joana\nMensagem: tamanho errado",
    )
    conversa2 = await db.get(AtendimentoConversa, cid2)
    assert conversa2.dados["mail"]["marca"] == "charlots-park"
    assert not conversa2.dados["mail"].get("alertas")
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{cid}/previa")).json()
    assert previa["de"] == "sac@charlots.com.br"
    html = previa["assinatura_html"]
    assert "logo-charlots-dark.png" in html and "Equipe Charlots<" in html
    assert ">www.charlots.com.br</a>" in html and "https://wa.me/5511977770710" in html
    assert "Charlots Park" not in html and "charlotspark" not in html


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
    um = {"emails": 1, "abertas": 1, "aguardando": 1, "total": 1, "nao_lidas": 1}
    assert u["grupos"]["sac"] == um
    assert u["grupos"]["admin"] == um
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
    assert (await _lista(client, "poofy", "sac")).status_code == 422
    assert (await _lista(client, "uranyx", "problemas")).status_code == 422


async def test_pasta_do_site_sem_categoria_diz_por_que(db, cena, client, admin):
    await _com_os_enderecos_do_site(db, cena)
    await categorizar(db, cena.geral, "*uranyx", None)
    sites = await _sites(client)
    assert sites["uranyx"]["status"] == "sem_categoria"
    assert sites["charlots"]["status"] == "sem_pasta"


async def test_quem_nao_e_admin_nao_ve_as_caixas_do_site(db, cena, client, quem_le, auth_as):
    """30/10/2026 (as lojas por pessoa): o site não é loja da planilha de ninguém —
    as caixas SAC/Admin dele ficam só para o admin, mesmo com todas as lojas."""
    ids = await _cena_da_barra(db, cena)
    auth_as(quem_le)
    assert await _sites(client) == {}
    r = await _lista(client, "uranyx", "admin")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "email_do_site_fora_do_escopo"
    assert ids["admin"]


async def test_quem_ve_so_a_equipe_nao_tem_as_caixas_dos_sites(
    db, cena, client, make_user, auth_as
):
    await _cena_da_barra(db, cena)
    membro = await make_user()  # só a Barbosa do ML (30/10/2026, as lojas por pessoa)
    await dar_lojas(db, membro, cena.ml)
    auth_as(membro)
    assert await _sites(client) == {}
    for site in ("uranyx", "makisa", "7buyers"):
        r = await _lista(client, site, "sac")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "email_do_site_fora_do_escopo"


# ─────────────── Makisa e 7buyers: sites SÓ DE E-MAIL (27/10/2026) ───────────────
#
# O Eduardo, 27/10: as pastas "*makisa" e "*7buyers" do Tuta também são sites —
# "por enquanto só chega os e-mails e eles mostram no atendimento". A barra
# ganha uma linha só de e-mail para cada um (sem carrinho: fora dos SITES do
# atendimento), com as mesmas caixas SAC e Admin; a ponte não muda nada.

MAKISA = ["sac@makisa.com.br", "cheng28@tuta.com"]


async def _cena_da_makisa(db, cena) -> dict[str, str]:
    """A pasta "*makisa" como em produção (criada, SEM categoria) e três e-mails."""
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.config_enc = encrypt_json(
        {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, *MAKISA]}
    )
    await db.commit()
    rotear.esquecer(db)
    await categorizar(db, cena.geral, "*makisa", None)
    ids = {}
    ids["sac"] = await entrar(
        db,
        cena.geral,
        pasta="*makisa",
        de="cliente@gmail.com",
        de_nome="Cliente",
        para=["SAC@makisa.com.br"],
        assunto="Troca do produto",
        corpo="Quero trocar o tamanho.",
    )
    # O login de loja que cai na pasta (cheng28@, 21max@…): Admin.
    ids["admin"] = await entrar(
        db,
        cena.geral,
        pasta="*makisa",
        de="avisos@fornecedor.com.br",
        para=["cheng28@tuta.com"],
        assunto="Sua fatura de outubro",
        corpo="A fatura está disponível.",
    )
    # O código de acesso da conta da loja na mesma pasta: guardado SEM a régua.
    ids["codigo"] = await entrar(
        db,
        cena.geral,
        pasta="*makisa",
        de="no-reply@accounts.google.com",
        para=["21max@tuta.com"],
        assunto="Seu código de verificação",
        corpo="Use o código 482913 para entrar na sua conta.",
        anexos=[("codigo.txt", "text/plain", b"482913")],
    )
    await rodar(db)
    for chave, mid in ids.items():
        ids[chave] = str(mid)
    return ids


async def test_makisa_mostra_os_emails_da_pasta_em_sac_e_admin(db, cena, client, admin):
    conversas_antes = len((await db.execute(select(AtendimentoConversa.id))).all())
    ids = await _cena_da_makisa(db, cena)
    # A ponte como hoje: a pasta sem categoria guarda, sem conversa nem chamado.
    for chave in ("sac", "admin", "codigo"):
        m = await meta_de(db, ids[chave])
        assert (m.estado, m.motivo, m.conversa_id) == (
            "ignorado",
            "pasta_sem_categoria",
            None,
        ), chave
    assert len((await db.execute(select(AtendimentoConversa.id))).all()) == conversas_antes

    sites = await _sites(client)
    mk = sites["makisa"]
    assert (mk["externo_ref"], mk["nome"], mk["pastas"], mk["so_email"], mk["status"]) == (
        "site:makisa",
        "Makisa",
        ["*makisa"],
        True,
        "sem_categoria",
    )
    assert [x["chave"] for x in mk["caixas"]] == ["sac", "admin"]
    so_um = {"emails": 1, "abertas": 0, "aguardando": 0, "total": 0, "nao_lidas": 0}
    assert mk["grupos"]["sac"] == so_um
    # Sem o alias gravado (a pasta sem categoria): a caixa pelo endereço que
    # recebeu; o código de acesso fica fora do número e da lista.
    assert mk["grupos"]["admin"] == so_um
    assert mk["emails_30d"] == 2
    assert sites["uranyx"]["so_email"] is False and sites["charlots"]["so_email"] is False

    r = await _lista(client, "makisa", "sac")
    assert r.status_code == 200, r.text
    [item] = r.json()["itens"]
    assert (item["id"], item["conversa_id"], item["pasta"]) == (ids["sac"], None, "*makisa")
    r = await _lista(client, "makisa", "admin")
    assert [i["id"] for i in r.json()["itens"]] == [ids["admin"]]
    # O e-mail inteiro abre (o nome do site); o código de acesso não (404).
    r = await client.get(f"{EMAIL}/lojas/emails/{ids['sac']}")
    assert r.status_code == 200 and r.json()["loja"] == "Makisa"
    assert r.json()["texto"] == "Quero trocar o tamanho."
    assert (await client.get(f"{EMAIL}/lojas/emails/{ids['codigo']}")).status_code == 404
    # Nem o anexo dele (a mesma régua).
    anexo = await db.scalar(
        select(MailAttachment.id).where(MailAttachment.message_id == UUID(ids["codigo"]))
    )
    assert (await client.get(f"{EMAIL}/lojas/anexos/{anexo}")).status_code == 404
    # O código não aparece em nenhuma caixa de nenhum site.
    for site in ("makisa", "uranyx", "charlots", "7buyers"):
        for caixa in ("sac", "admin"):
            itens = (await _lista(client, site, caixa)).json()["itens"]
            assert ids["codigo"] not in {i["id"] for i in itens}, (site, caixa)
    # Nenhum outro site vê a pasta da Makisa.
    assert (await _lista(client, "uranyx", "admin")).json()["itens"] == []


async def test_7buyers_sem_a_pasta_mostra_as_caixas_vazias_com_o_porque(
    db, cena, client, admin
):
    await _cena_da_makisa(db, cena)
    b7 = (await _sites(client))["7buyers"]
    assert (b7["externo_ref"], b7["nome"], b7["pastas"], b7["so_email"], b7["status"]) == (
        "site:7buyers",
        "7buyers",
        ["*7buyers"],
        True,
        "sem_pasta",
    )
    assert b7["emails_30d"] == 0
    vazia = {"emails": 0, "abertas": 0, "aguardando": 0, "total": 0, "nao_lidas": 0}
    assert b7["grupos"] == {"sac": vazia, "admin": vazia}
    for grupo in ("sac", "admin"):
        r = await _lista(client, "7buyers", grupo)
        assert r.status_code == 200 and r.json() == {"itens": [], "proximo": None}


async def test_quem_nao_e_admin_nao_ve_a_makisa(db, cena, client, quem_le, auth_as):
    """30/10/2026: os sites só de e-mail (Makisa, 7buyers) também só para o admin."""
    ids = await _cena_da_makisa(db, cena)
    auth_as(quem_le)
    assert await _sites(client) == {}
    r = await _lista(client, "makisa", "sac")
    assert r.status_code == 403 and ids["sac"]


# ─────────────── admin@makisa e admin@7buyers: só mostrar (27/10/2026) ───────────────
#
# O Eduardo criou admin@makisa.com.br e admin@7buyers.com.br no Tuta (regras
# para "*makisa" e "*7buyers"). A 7buyers é MARCA (domínio 7buyers.com.br), mas
# não é site do atendimento: o admin@ dela não pode virar chamado do site (o
# admin@ só é caixa de site na Uranyx e na Charlots) — continua interno, e a
# caixa Admin da barra mostra o e-mail pela pasta. Nada quebra nem levanta.

DOS_SITES_SO_EMAIL = [
    "sac@makisa.com.br",
    "admin@makisa.com.br",
    "sac@7buyers.com.br",
    "admin@7buyers.com.br",
]


def test_admin_so_e_caixa_de_site_nas_marcas_do_atendimento():
    cad = rotear.Cadastro()
    cad.marcas["uranyx"] = (None, "Uranyx", ("uranyx.com.br",))
    cad.marcas["7buyers"] = (None, "7buyers", ("7buyers.com.br",))
    assert rotear.e_caixa_de_site("admin@uranyx.com.br", cad)
    assert not rotear.e_caixa_de_site("admin@7buyers.com.br", cad)
    assert not rotear.e_caixa_de_site("admin@7buyers.com.br", cad, marca_pasta="7buyers")
    assert not rotear.e_caixa_de_site("admin@makisa.com.br", cad, marca_pasta="makisa")
    # Makisa e 7buyers só mostram: nem o sac@ deles vira chamado do site.
    assert not rotear.e_caixa_de_site("sac@7buyers.com.br", cad)
    assert rotear.e_caixa_de_site("sac@uranyx.com.br", cad)
    # Mas passam no filtro da caixa privada (o e-mail chega às caixas da barra).
    for endereco in ("sac@makisa.com.br", "admin@makisa.com.br", "sac@7buyers.com.br",
                     "admin@7buyers.com.br", "admin@uranyx.com.br", "sac@charlots.com.br"):
        assert rotear.passa_no_filtro_privado(endereco, cad), endereco
    assert not rotear.passa_no_filtro_privado("admin@poofy.com.br", cad)
    assert not rotear.passa_no_filtro_privado("gabrieli@7buyers.com.br", cad)
    # Só os 8 endereços dos sites (e os logins de loja): a caixa de site das outras
    # marcas e os antigos duvidas@/atacado@ continuam privados.
    cad.marcas["lukako"] = (None, "Lukako", ("lukako.com.br",))
    for endereco in ("suporte@lukako.com.br", "duvidas@uranyx.com.br", "atacado@uranyx.com.br",
                     "sac@mail.uranyx.com.br", "admin@uranyx.com"):
        assert not rotear.passa_no_filtro_privado(endereco, cad), endereco
    # O admin@ da Uranyx que caiu na pasta "*7buyers": a marca do domínio, nunca a da pasta.
    rota = rotear._rota_do_site(
        "admin@uranyx.com.br", cad, marca_pasta="7buyers", caixa_pasta=None
    )
    assert rota is not None and rota.marca_slug == "uranyx"
    sem = rotear._rota_do_site("admin@7buyers.com.br", cad, marca_pasta="7buyers", caixa_pasta=None)
    assert sem is None


async def _com_os_sites_so_email(db, cena) -> None:
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.config_enc = encrypt_json(
        {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, *DOS_SITES_SO_EMAIL]}
    )
    # Como em produção: a 7buyers é marca ativa (sem marca_emails); a Makisa não.
    db.add(Marca(nome="7buyers", slug="7buyers", dominio_br="7buyers.com.br"))
    await db.commit()
    rotear.esquecer(db)


@pytest.mark.parametrize("site", ["makisa", "7buyers"])
async def test_admin_do_site_so_de_email_nunca_vira_conversa(db, cena, client, admin, site):
    await _com_os_sites_so_email(db, cena)
    conversas_antes = len((await db.execute(select(AtendimentoConversa.id))).all())
    # Na Entrada (a ponte roteia): endereço interno, como antes de 27/10.
    na_entrada = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="parceiro@empresa.com.br",
        para=[f"admin@{site}.com.br"],
        assunto="Proposta de parceria",
        corpo="Podemos conversar?",
    )
    await rodar(db)
    m = await meta_de(db, na_entrada)
    assert (m.estado, m.motivo, m.conversa_id) == ("ignorado", "alias_interno", None)
    # Na pasta do site sem categoria (como a regra nova do Tuta manda): guardado e
    # mostrado na caixa Admin; o sac@ na caixa SAC.
    await categorizar(db, cena.geral, f"*{site}", None)
    ids = {}
    for caixa in ("admin", "sac"):
        ids[caixa] = str(
            await entrar(
                db,
                cena.geral,
                pasta=f"*{site}",
                de="cliente@gmail.com",
                para=[f"{caixa}@{site}.com.br"],
                assunto=f"Contato pelo {caixa}",
                corpo="Olá, preciso de ajuda.",
            )
        )
    await rodar(db)
    for mid in ids.values():
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo, m.conversa_id) == ("ignorado", "pasta_sem_categoria", None)
    # A pasta do site com a categoria "Site (como hoje)": o admin@ continua interno.
    await categorizar(db, cena.geral, f"*{site}", "site")
    categorizado = await entrar(
        db,
        cena.geral,
        pasta=f"*{site}",
        de="cliente@gmail.com",
        para=[f"admin@{site}.com.br"],
        assunto="Outra pergunta",
        corpo="Olá de novo.",
    )
    await rodar(db)
    m = await meta_de(db, categorizado)
    assert (m.estado, m.motivo, m.conversa_id) == ("ignorado", "alias_interno", None)
    assert len((await db.execute(select(AtendimentoConversa.id))).all()) == conversas_antes

    sites = await _sites(client)
    assert sites[site]["grupos"]["sac"]["emails"] == 1
    assert sites[site]["grupos"]["admin"]["emails"] == 1
    for caixa, mid in ids.items():
        r = await _lista(client, site, caixa)
        assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [mid], caixa


# ─────────────── a caixa PRIVADA (como a "Principal — Tuta" em produção) ───────────────
#
# Produção, 27/10: a caixa dos sites é privada com `ponte_so_aliases_de_loja`
# (só o e-mail das LOJAS passa). Os sac@/admin@ dos 4 sites passam no filtro:
# Uranyx e Charlots viram o chamado do site; Makisa e 7buyers só aparecem nas
# caixas da barra (nenhuma conversa). O endereço pessoal continua privado.

SITES_8 = [
    f"{local}@{dominio}"
    for dominio in ("uranyx.com.br", "charlots.com.br", "makisa.com.br", "7buyers.com.br")
    for local in ("sac", "admin")
]


async def test_caixa_privada_deixa_passar_os_enderecos_dos_4_sites(db, cena, client, admin):
    goslin = await db.get(MailMailbox, cena.goslin.id)
    goslin.config_enc = encrypt_json(
        {"address": GOSLIN, "aliases": [GOSLIN, *ALIASES_GOSLIN, *SITES_8, "pessoal@tuta.com"]}
    )
    db.add(Marca(nome="Charlots Park", slug="charlots-park", dominio_br="charlots.com.br"))
    db.add(Marca(nome="7buyers", slug="7buyers", dominio_br="7buyers.com.br"))
    await db.commit()
    rotear.esquecer(db)
    for nome in ("*uranyx", "*charlots"):
        await categorizar(db, cena.goslin, nome, "site")
    for nome in ("*makisa", "*7buyers"):
        await categorizar(db, cena.goslin, nome, None)
    conversas_antes = len((await db.execute(select(AtendimentoConversa.id))).all())
    chamados = {
        "sac@charlots.com.br": ("*charlots", "[CS-26-0009] SAC — Vestido — Joana"),
        "admin@charlots.com.br": ("*charlots", "[CA-26-0003] Atacado — Boutique — Grade"),
        "admin@uranyx.com.br": ("*uranyx", "[UDS-26-0070] Fale com a gente — Ana"),
    }
    ids = {}
    for para, (pasta, assunto) in chamados.items():
        ids[para] = await entrar(
            db, cena.goslin, pasta=pasta, de="cliente@gmail.com", para=[para],
            assunto=assunto, corpo="Nome: Cliente\nMensagem: preciso de ajuda",
        )
    so_mostrar = {}
    for site in ("makisa", "7buyers"):
        for caixa in ("sac", "admin"):
            so_mostrar[(site, caixa)] = await entrar(
                db, cena.goslin, pasta=f"*{site}", de="cliente@gmail.com",
                para=[f"{caixa}@{site}.com.br"], assunto=f"Contato {caixa}", corpo="Olá!",
            )
    # O sac@ da 7buyers fora da pasta (a Entrada): passa no filtro, mas não vira chamado.
    entrada = await entrar(
        db, cena.goslin, pasta="INBOX", de="cliente@gmail.com", para=["sac@7buyers.com.br"],
        assunto="Dúvida", corpo="Olá!",
    )
    pessoal = await entrar(db, cena.goslin, para=["pessoal@tuta.com"], de="amigo@gmail.com")
    await rodar(db)

    for para, mid in ids.items():
        m = await meta_de(db, mid)
        assert m.estado == "gravado" and m.conversa_id is not None, (para, m.estado, m.motivo)
        c = await conversa(db, m.conversa_id)
        assert c.plataforma == "site", para
    for chave, mid in so_mostrar.items():
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo, m.conversa_id) == (
            "ignorado", "pasta_sem_categoria", None,
        ), chave
    m = await meta_de(db, entrada)
    assert m.estado != "privado" and m.conversa_id is None, (m.estado, m.motivo)
    assert (await meta_de(db, pessoal)).estado == "privado"
    depois = len((await db.execute(select(AtendimentoConversa.id))).all())
    assert depois == conversas_antes + 3

    sites = await _sites(client)
    assert sites["charlots"]["grupos"]["sac"]["emails"] == 1
    assert sites["charlots"]["grupos"]["admin"]["emails"] == 1
    assert sites["uranyx"]["grupos"]["admin"]["emails"] == 1
    for (site, caixa), mid in so_mostrar.items():
        assert sites[site]["grupos"][caixa]["emails"] == 1, (site, caixa)
        r = await _lista(client, site, caixa)
        assert [i["id"] for i in r.json()["itens"]] == [str(mid)], (site, caixa)


# ─────────────── a caixa privada: a PASTA do site passa (27/10/2026, 15:15) ───────────────
#
# Produção: as pastas "*7buyers", "*charlots" e "*uranyx" da "Principal — Tuta"
# (privada, só aliases de loja) ficavam "privado" — o e-mail que a regra do Tuta
# põe na pasta do site (o De de um fornecedor, gabrieli@, o encaminhado da
# empresa) não é para sac@/admin@. O Eduardo: "o que não for do SAC cai em
# Admin". A pasta do site passa no filtro, mas SÓ PARA MOSTRAR: nunca conversa
# (nem com a pasta em "Site (como hoje)"); fora das pastas dos sites, privado.


async def _caixa_privada_dos_sites(db, cena):
    goslin = await db.get(MailMailbox, cena.goslin.id)
    goslin.config_enc = encrypt_json(
        {"address": GOSLIN, "aliases": [GOSLIN, *ALIASES_GOSLIN, *SITES_8]}
    )
    db.add(Marca(nome="Charlots Park", slug="charlots-park", dominio_br="charlots.com.br"))
    db.add(Marca(nome="7buyers", slug="7buyers", dominio_br="7buyers.com.br"))
    await db.commit()
    rotear.esquecer(db)
    # A Charlots depois do comando do dono ("Site (como hoje)"); a *7buyers nem existe.
    await categorizar(db, cena.goslin, "*charlots", "site")


async def test_a_pasta_do_site_passa_na_caixa_privada_so_para_mostrar(
    db, cena, client, admin
):
    from app.models.mail_atendimento import MailFolder

    await _caixa_privada_dos_sites(db, cena)
    conversas_antes = len((await db.execute(select(AtendimentoConversa.id))).all())
    ids = {}
    ids["gabrieli"] = await entrar(
        db, cena.goslin, pasta="*7buyers", de="fornecedor@empresa.com.br",
        para=["gabrieli@7buyers.com.br"], assunto="Danfe 123 — 7Buyers Club",
        corpo="Segue a nota fiscal.",
    )
    ids["bertec"] = await entrar(
        db, cena.goslin, pasta="*charlots", de="financeiro1@bertecempreendimentos.com.br",
        para=[GOSLIN], assunto="Orçamento e proposta", corpo="Segue o orçamento.",
    )
    ids["codigo"] = await entrar(
        db, cena.goslin, pasta="*7buyers", de="no-reply@accounts.google.com",
        para=["gabrieli@7buyers.com.br"], assunto="Seu código de verificação",
        corpo="Use o código 482913 para entrar na sua conta.",
    )
    # O mesmo tipo de e-mail fora das pastas dos sites: privado como sempre.
    ids["entrada"] = await entrar(
        db, cena.goslin, pasta="INBOX", de="financeiro1@bertecempreendimentos.com.br",
        para=[GOSLIN], assunto="Orçamento", corpo="Segue.",
    )
    ids["contabilidade"] = await entrar(
        db, cena.goslin, pasta="contabilidade", de="financeiro1@bertecempreendimentos.com.br",
        para=[GOSLIN], assunto="Orçamento", corpo="Segue.",
    )
    await rodar(db)

    for chave in ("gabrieli", "bertec", "codigo"):
        m = await meta_de(db, ids[chave])
        assert (m.estado, m.motivo, m.conversa_id, m.alias_recebido) == (
            "ignorado", "so_pela_pasta_do_site", None, None,
        ), chave
        assert m.folder_id is not None, chave
    pasta_7b = await db.get(MailFolder, (await meta_de(db, ids["gabrieli"])).folder_id)
    assert pasta_7b.nome == "*7buyers"
    for chave in ("entrada", "contabilidade"):
        m = await meta_de(db, ids[chave])
        assert (m.estado, m.motivo, m.folder_id) == ("privado", "nao_e_alias_de_loja", None), chave
    # Nenhuma conversa (nem na *charlots com a categoria "site").
    assert len((await db.execute(select(AtendimentoConversa.id))).all()) == conversas_antes

    sites = await _sites(client)
    assert sites["7buyers"]["grupos"]["admin"]["emails"] == 1, "o código fica fora do número"
    assert sites["7buyers"]["grupos"]["sac"]["emails"] == 0
    assert sites["charlots"]["grupos"]["admin"]["emails"] == 1
    r = await _lista(client, "7buyers", "admin")
    assert [i["id"] for i in r.json()["itens"]] == [str(ids["gabrieli"])]
    r = await _lista(client, "charlots", "admin")
    assert [i["id"] for i in r.json()["itens"]] == [str(ids["bertec"])]
    # O código: fora da lista, do e-mail inteiro e de toda caixa.
    assert (await client.get(f"{EMAIL}/lojas/emails/{ids['codigo']}")).status_code == 404
    r = await client.get(f"{EMAIL}/lojas/emails/{ids['gabrieli']}")
    assert r.status_code == 200 and r.json()["loja"] == "7buyers"


async def test_sac_do_site_na_pasta_cai_no_sac_e_o_resto_no_admin(db, cena, client, admin):
    await _caixa_privada_dos_sites(db, cena)
    sac = await entrar(
        db, cena.goslin, pasta="*7buyers", de="cliente@gmail.com",
        para=["sac@7buyers.com.br"], assunto="Troca", corpo="Quero trocar.",
    )
    outro = await entrar(
        db, cena.goslin, pasta="*7buyers", de="cliente@gmail.com",
        para=[GOSLIN], assunto="Pergunta", corpo="Olá.",
    )
    await rodar(db)
    r = await _lista(client, "7buyers", "sac")
    assert [i["id"] for i in r.json()["itens"]] == [str(sac)]
    r = await _lista(client, "7buyers", "admin")
    assert [i["id"] for i in r.json()["itens"]] == [str(outro)]



# ─────────────── a bolinha do site: NÃO LIDA (27/10/2026, 15:25) ───────────────
#
# O Eduardo: "e-mail novo que chega vira bolinha vermelha, e ao clicar some".
# A bolinha da caixa do site conta a conversa não lida (falta responder e
# e-mail do cliente depois do "visto no DaVinci"); abrir a conversa (o POST
# /visto da tela, o mesmo da Caixa) apaga; a resposta nova do cliente acende.


async def test_abrir_a_conversa_apaga_a_bolinha_e_o_email_novo_acende(db, cena, client, admin):
    ids = await _cena_da_barra(db, cena)
    r = await _lista(client, "uranyx", "sac")
    [item] = r.json()["itens"]
    assert item["nao_lida"] is True and item["aguardando_resposta"] is True
    cid = item["conversa_id"]
    assert (await _sites(client))["uranyx"]["grupos"]["sac"]["nao_lidas"] == 1
    # Abriu a conversa (a tela chama o POST /visto ao abrir).
    r = await client.post(f"{URL}/conversas/{cid}/visto", json={})
    assert r.status_code == 200, r.text
    g = (await _sites(client))["uranyx"]["grupos"]["sac"]
    assert (g["nao_lidas"], g["aguardando"]) == (0, 1), "lida, mas ainda falta responder"
    [item] = (await _lista(client, "uranyx", "sac")).json()["itens"]
    assert item["nao_lida"] is False and item["aguardando_resposta"] is True
    # O cliente escreve de novo no mesmo chamado: a bolinha volta.
    await entrar(
        db, cena.geral, pasta="*uranyx", de="maria@gmail.com", para=["sac@uranyx.com.br"],
        assunto="Re: [US-26-0123] SAC — A17 — Maria", corpo="Alguma novidade?",
    )
    await rodar(db)
    assert (await _sites(client))["uranyx"]["grupos"]["sac"]["nao_lidas"] == 1
    assert ids["sac"]
