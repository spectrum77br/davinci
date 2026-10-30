# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""O e-mail da loja FORMATADO no /atendimento, como na aba E-mail (30/10/2026).

O Eduardo, 30/10, com o mesmo e-mail (o encaminhado da Amirom, pela loja
7buyers) aberto nas duas telas — no atendimento texto corrido, na aba E-mail
formatado: "temos de replicar o que acontece em e-mail ali pra parte de email
que quem trabalha no atendimento consegue ver". A equipe não tem a aba E-mail.

O que não pode falhar:
  • GET …/email/lojas/emails/{id} manda o `html` com a MESMA régua do texto: o
    link de acesso sai do href (todos, se o e-mail fala de senha/código/acesso),
    a URL escrita vira "[link de acesso removido]", a senha vira "•" mesmo
    quebrada em tags; script, comentário e `on*` saem;
  • o que a limpeza não fecha (a URL de acesso partida em duas tags) não vai:
    `html` None, e a tela mostra o texto;
  • o `content_id` do anexo vai junto (a imagem embutida), e a imagem vem pela
    rota do anexo da CAIXA — para quem vê a loja, nunca pelo /api/mail;
  • a rota continua do atendimento: quem não vê o atendimento (o operador de
    estoque) leva 403; a equipe de outra loja, 404.
"""

from __future__ import annotations

from app.models import MailAttachment, StoreInfo, UserRole
from app.security.cipher import encrypt_bytes, encrypt_json
from app.services.mail_atendimento import html_caixa
from tests.test_atendimento_email_lojas import EMAIL, _pastas_ligadas
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_mail_ponte import cena, entrar, meta_de, rodar

LIMPO = html_caixa.TITULO_SEM_LINK
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c636000010000050001a5f645400000000049454e44ae426082"
)


# ── A limpeza (pura) ───────────────────────────────────────────────────────


def test_a_formatacao_fica_e_o_que_nunca_aparece_sai():
    html, n = html_caixa.limpar(
        "Fwd: Planilha Produtos - Amirom Express",
        "Bom dia. reenviando para sua apreciação.",
        "<!doctype html><html><head><style>td>p{color:#334455}</style></head>"
        '<body bgcolor="#f5f5f5"><p style="font:14px Arial">Bom dia.</p>'
        '<blockquote><table width="600"><tr><td>Assunto:</td><td>Planilha</td></tr></table>'
        '</blockquote><img src="cid:logo@amirom" alt="Amirom"><img src="https://cdn.amirom.com/a.png">'
        '<a href="https://amiromexpress.com/catalogo" onclick="roubar()">Catálogo</a>'
        "<!-- [if mso]>x<![endif] --><script>alert(1)</script><iframe>oi</iframe></body></html>",
    )
    assert n == 0
    assert "<style>td>p{color:#334455}</style>" in html, "o CSS fica (a tela ainda limpa)"
    assert '<body bgcolor="#f5f5f5">' in html
    assert '<table width="600"><tr><td>Assunto:</td><td>Planilha</td></tr></table>' in html
    assert '<img src="cid:logo@amirom" alt="Amirom">' in html, "a imagem embutida"
    assert '<img src="https://cdn.amirom.com/a.png">' in html, "a imagem de fora"
    assert '<a href="https://amiromexpress.com/catalogo">Catálogo</a>' in html, "o link comum"
    sairam = ("onclick", "roubar", "<script", "alert", "<iframe", "oi<", "<!--", "mso", "doctype")
    for fora in sairam:
        assert fora not in html.lower(), fora


def test_o_link_de_acesso_sai_do_html():
    html, n = html_caixa.limpar(
        "Seu pedido saiu",
        "Seu pedido saiu.",
        '<p>Acompanhe <a href="https://loja.com.br/pedido/123">o pedido</a>.</p>'
        '<p><a href="https://loja.com.br/account/verify?token=abc">confirme</a></p>'
        "<p>Ou copie: https://loja.com.br/reset/abc</p>"
        '<img src="https://cdn.loja.com.br/x.png" title="https://loja.com.br/login?next=1">',
    )
    assert "verify" not in html and "token" not in html and "reset" not in html
    assert "login" not in html
    assert f'<a title="{LIMPO}">confirme</a>' in html, "o texto do link fica, sem link"
    assert "Ou copie: [link de acesso removido]" in html
    assert '<a href="https://loja.com.br/pedido/123">o pedido</a>' in html, "o link comum fica"
    assert n == 3


def test_o_email_que_fala_de_acesso_perde_todos_os_links():
    html, n = html_caixa.limpar(
        "Redefinir sua senha",
        "Clique para redefinir sua senha",
        '<p>Clique <a href="https://loja.com.br/p/1">aqui</a> para redefinir sua senha.</p>'
        '<a href="mailto:sac@loja.com.br">sac@loja.com.br</a>',
    )
    assert "https://" not in html
    assert f'<a title="{LIMPO}">aqui</a>' in html and n == 1
    assert '<a href="mailto:sac@loja.com.br">' in html, "e-mail para escrever não é link de acesso"


def test_a_senha_some_mesmo_quebrada_em_tags():
    html, _ = html_caixa.limpar(
        "Acesso ao painel",
        "Senha: Xp7!kL2wQz",
        "<table><tr><td>Senha:</td><td><b>Xp7!</b><i>kL2wQz</i></td></tr></table>",
    )
    assert "Xp7" not in html and "kL2wQz" not in html
    assert "<b>••••</b><i>••••••</i>" in html


def test_o_que_a_limpeza_nao_fecha_nao_vai():
    # A URL de acesso partida em duas tags: na tela, uma URL só.
    assert html_caixa.limpar(
        "Oi", "Oi", "<p><span>https://loja.com.br/</span><span>reset?token=abcdef</span></p>"
    ) == (None, 0)
    assert html_caixa.limpar("Oi", "Oi", None) == (None, 0)
    assert html_caixa.limpar("Oi", "Oi", "   ") == (None, 0)
    assert html_caixa.limpar("Oi", "Oi", "<p>x</p>" * (html_caixa.MAXIMO // 8 + 1)) == (None, 0)


def test_tudo_e_reescrito_escapado():
    html, _ = html_caixa.limpar(
        "Oi",
        "Oi",
        '<a"x href=1>t</a"x><p title=\'a"b\' data-x=1>&lt;script&gt;</p>'
        '<a href="javascript:alert(1)">j</a><td background="https://x.com/b.png">c</td>'
        "<style>a{}</style><b>fim</b>",
    )
    assert html == (
        't<p title="a&quot;b" data-x="1">&lt;script&gt;</p><a>j</a><td>c</td>'
        "<style>a{}</style><b>fim</b>"
    )


# ── A rota (quem vê a loja) ────────────────────────────────────────────────


async def _email_formatado(db, cena) -> tuple:
    await _pastas_ligadas(db, cena)
    corpo_html = (
        '<p>Bom dia.</p><table><tr><td><img src="cid:logo@ml"></td>'
        '<td><a href="https://www.mercadolivre.com.br/vendas/123">Ver a venda</a></td></tr></table>'
        '<p><a href="https://www.mercadolivre.com.br/jms/login?token=abcdef">Ver o painel</a></p>'
    )
    mid = await entrar(
        db,
        cena.geral,
        pasta="envio",
        para=["21max@tuta.com"],
        de="avisos@mercadolivre.com.br",
        assunto="Pacote postado",
        corpo="Bom dia. Ver a venda https://www.mercadolivre.com.br/vendas/123",
        html=corpo_html,
    )
    imagem = MailAttachment(
        message_id=mid,
        metadata_enc=encrypt_json(
            {
                "filename": "logo.png",
                "content_type": "image/png",
                "content_id": "<logo@ml>",
                "disposition": "inline",
            }
        ),
        content_enc=encrypt_bytes(PNG),
        size=len(PNG),
    )
    db.add(imagem)
    await db.commit()
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "informativo"
    return mid, imagem.id


async def test_a_rota_manda_o_html_limpo_e_a_imagem_pela_caixa(
    db, cena, client, admin, auth_as, quem_le
):
    mid, imagem = await _email_formatado(db, cena)
    auth_as(quem_le)  # sem a aba E-mail: só pela barra
    r = await client.get(f"{EMAIL}/lojas/emails/{mid}")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    d = r.json()
    assert '<img src="cid:logo@ml">' in d["html"]
    assert '<a href="https://www.mercadolivre.com.br/vendas/123">Ver a venda</a>' in d["html"]
    assert "login" not in d["html"] and "token" not in d["html"]
    assert f'<a title="{LIMPO}">Ver o painel</a>' in d["html"]
    assert d["html_links_removidos"] == 1
    assert d["texto"].startswith("Bom dia."), 'o texto continua (o "Ver texto")'
    assert [(a["id"], a.get("content_id")) for a in d["anexos"]] == [(str(imagem), "<logo@ml>")]
    # A imagem embutida: pela rota da caixa, para quem vê a loja.
    a = await client.get(f"{EMAIL}/lojas/anexos/{imagem}")
    assert a.status_code == 200 and a.content == PNG
    assert a.headers["content-type"] == "application/octet-stream"


async def test_sem_html_ou_sem_fechar_a_tela_fica_no_texto(db, cena, client, admin):
    await _pastas_ligadas(db, cena)
    ml = {"para": ["21max@tuta.com"], "de": "avisos@mercadolivre.com.br", "pasta": "envio"}
    so_texto = await entrar(db, cena.geral, **ml, assunto="Pacote postado", corpo="Saiu.")
    partido = await entrar(
        db,
        cena.geral,
        **ml,
        assunto="Pacote a caminho",
        corpo="Acompanhe.",
        html="<p><span>https://www.mercadolivre.com.br/</span><span>reset?token=abcdef</span></p>",
    )
    await rodar(db)
    for mid in (so_texto, partido):
        d = (await client.get(f"{EMAIL}/lojas/emails/{mid}")).json()
        assert d["html"] is None and d["html_links_removidos"] == 0 and d["texto"]


async def test_a_rota_continua_so_do_atendimento(db, cena, client, admin, auth_as, make_user):
    mid, imagem = await _email_formatado(db, cena)
    # O operador de estoque: para ele o atendimento nem existe.
    op = await make_user(role=UserRole.USER, permissions={"controle_estoque": {"view": True}})
    op.stock_tags = ["ci"]
    await db.commit()
    auth_as(op)
    for rota in (f"lojas/emails/{mid}", f"lojas/anexos/{imagem}"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 403, (rota, r.text)
        assert r.json()["detail"]["code"] == "atendimento_restrito"
    # A equipe de OUTRA loja: nem o e-mail nem a imagem (404, nunca diz que existe).
    auth_as(cena.dono)
    loja = await db.get(StoreInfo, cena.loja_oliveira_ml.id)
    loja.sales_team = 9
    membro = await make_user()
    membro.sales_teams = [9]
    await db.commit()
    auth_as(membro)
    for rota in (f"lojas/emails/{mid}", f"lojas/anexos/{imagem}"):
        assert (await client.get(f"{EMAIL}/{rota}")).status_code == 404, rota
