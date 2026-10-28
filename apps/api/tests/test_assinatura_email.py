"""A assinatura das marcas (app.services.assinatura_email) — a mesma da tela
Cadastros › E-mails e da resposta do /atendimento (28/10/2026). PURAS."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import assinatura_email as ae


def _marca(slug: str, nome: str | None = None, site: str | None = None, fone: str | None = None):
    return SimpleNamespace(slug=slug, nome=nome or slug, site=site, sac_fone=fone)


URANYX = _marca("uranyx", site="https://www.uranyx.com.br/", fone="11983517003")


def test_uranyx_sac_igual_a_tela():
    html = ae.montar_html(URANYX, "sac", "sac@uranyx.com.br", "+55 11 98351-7003")
    assert (
        '<img src="https://uranyx.com.br/assets/img/logo.png" width="170" height="17" '
        'alt="Uranyx"' in html
    )
    assert '<a href="https://www.uranyx.com.br/" style="text-decoration:none;"><img' in html
    assert "Equipe Uranyx" in html
    assert "Serviço de Atendimento ao Consumidor" in html
    assert 'href="mailto:sac@uranyx.com.br"' in html
    assert ">www.uranyx.com.br</a>" in html
    assert 'href="https://wa.me/5511983517003"' in html
    assert "border-left:3px solid #F47B42" in html
    assert html.startswith("<table") and html.endswith("</table>")


def test_admin_e_o_cargo_administrativo():
    html = ae.montar_html(URANYX, "admin", "admin@uranyx.com.br", "")
    assert "Administrativo" in html and "Serviço de Atendimento" not in html
    assert "WhatsApp" not in html
    # Tipo desconhecido (o antigo duvidas/atacado) cai no Admin.
    assert "Administrativo" in ae.montar_html(URANYX, "duvidas", "x@uranyx.com.br", "")


def test_charlots_7buyers_locagil():
    charlots = _marca("poofy", nome="charlots", site="https://charlots.com.br")
    html = ae.montar_html(charlots, "sac", "sac@charlots.com.br", "")
    assert "Equipe Charlots" in html and "logo-charlots-dark.png" in html
    # 7 Buyers: logo branco na faixa vermelha e o site com www.
    sete = _marca("7buyers", site="https://7buyers.com.br")
    html = ae.montar_html(sete, "admin", "admin@7buyers.com.br", "")
    assert '<td style="background:#EE1C25;padding:9px 12px;">' in html
    assert 'href="https://www.7buyers.com.br"' in html and "Equipe 7 Buyers" in html
    # Locagil: sem logo (o nome em texto) e sem linha de site.
    locagil = _marca("locagil", site="https://locagil.com.br")
    html = ae.montar_html(locagil, "sac", "sac@locagil.com.br", "")
    assert ">LOCAGIL</span>" in html
    assert "<img" not in html and "Site:" not in html
    # Marca sem visual: o nome com maiúscula e a cor padrão.
    html = ae.montar_html(_marca("doscatos"), "sac", "", "")
    assert "Equipe Doscatos" in html and "#1f5f8b" in html and "E-mail:" not in html


def test_escapa_o_que_vem_do_cadastro_e_da_tela():
    marca = _marca("x", nome='Ma<b>"rca', site='https://x.com.br/?a=1&b="2"')
    html = ae.montar_html(marca, "sac", 'a"<b>@x.com', "<script>")
    assert "<b>" not in html and "<script>" not in html
    assert "Equipe Ma&lt;b&gt;&quot;rca" in html
    assert 'href="https://x.com.br/?a=1&amp;b=&quot;2&quot;"' in html
    assert "mailto:a&quot;&lt;b&gt;@x.com" in html
    # Site que não é http(s) não vira link.
    js = _marca("x", site="javascript:alert(1)")
    assert "javascript" not in ae.montar_html(js, "sac", "", "")


def test_texto_puro_igual_ao_html():
    texto = ae.montar_texto(URANYX, "sac", "sac@uranyx.com.br", "+55 11 98351-7003")
    assert texto == (
        "Atenciosamente,\nEquipe Uranyx\nServiço de Atendimento ao Consumidor\n"
        "E-mail: sac@uranyx.com.br\nSite: www.uranyx.com.br\nWhatsApp: +55 11 98351-7003"
    )
    locagil = _marca("locagil", site="https://locagil.com.br")
    assert ae.montar_texto(locagil, "admin", "", "") == (
        "Atenciosamente,\nEquipe Locagil\nAdministrativo"
    )


def test_fone_tipo_e_nome():
    assert ae.formatar_fone("11983517003") == "+55 11 98351-7003"
    assert ae.formatar_fone("5547997720970") == "+55 47 99772-0970"
    assert ae.formatar_fone("1933334444") == "+55 19 3333-4444"
    assert ae.formatar_fone(None) == "" and ae.formatar_fone("123") == "123"
    assert ae.tipo_do_endereco("SAC@Uranyx.com.br") == "sac"
    for outro in ("admin@uranyx.com.br", "duvidas@uranyx.com.br", "atacado@x.com", None, ""):
        assert ae.tipo_do_endereco(outro) == "admin"
    assert ae.nome_exibicao(_marca("poofy", nome="charlots")) == "Charlots"
