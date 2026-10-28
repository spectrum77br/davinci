"""A assinatura de e-mail das marcas — a MESMA na tela e na resposta.

Eduardo, 07/10/2026: Cadastros › E-mails › Assinatura, a prévia por marca e
caixa com "Copiar assinatura" para colar no Tuta (referência:
assinaturas-email-marcas.html). Eduardo, 28/10/2026: "se for respondido o
email pelo proprio davinci deve ser colocado o rodape de assinatura" e as
caixas agora são só SAC e Admin. Por isso a assinatura mora AQUI (antes era
`apps/web/lib/assinaturaEmail.ts`): a tela pede a prévia pela API
(`GET /api/marca-emails/{id}/assinatura`) e a resposta do /atendimento
(`mail_atendimento.responder`) usa as mesmas funções — o que a pessoa vê é o
que sai.

HTML só com tabela e estilo inline: é o que sobrevive à colagem no editor do
Tuta e aos clientes de e-mail de quem recebe. O logo vai por URL PÚBLICA do
site da marca (o logo guardado no DaVinci, /api/marcas/{id}/logo, exige login
e não abriria para quem recebe). Todo valor do cadastro passa por
`html.escape`. Funções PURAS (sem banco).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.models.enums import MarcaEmailTipo


class MarcaDaAssinatura(Protocol):
    nome: str
    slug: str
    site: str | None
    sac_fone: str | None


@dataclass(frozen=True)
class Logo:
    src: str
    w: int
    h: int
    # Faixa colorida atrás (o logo branco da 7 Buyers).
    bg: str | None = None


@dataclass(frozen=True)
class Visual:
    logo: Logo | None
    # Cor da marca: a barra lateral e os links.
    cor: str
    # O site da linha "Site:" no lugar do cadastrado na marca.
    site: str | None = None
    # Sem linha de site (locagil.com.br não abre: erro de certificado).
    sem_site: bool = False


# Por slug da marca (a Charlots tem slug "poofy").
VISUAL: dict[str, Visual] = {
    "uranyx": Visual(Logo("https://uranyx.com.br/assets/img/logo.png", 170, 17), "#F47B42"),
    "poofy": Visual(
        Logo("https://charlots.com.br/assets/img/logo-charlots-dark.png", 130, 34), "#242424"
    ),
    "7buyers": Visual(
        Logo(
            "https://www.7buyers.com.br/cdn/shop/files/"
            "7buyers_5b5b7f60-80eb-4fed-9045-4ae52c7b6d75_460x.png",
            120,
            25,
            bg="#EE1C25",
        ),
        "#EE1C25",
        site="https://www.7buyers.com.br",
    ),
    "locagil": Visual(None, "#1f5f8b", sem_site=True),
}
VISUAL_PADRAO = Visual(None, "#1f5f8b")

CARGO: dict[str, str] = {
    MarcaEmailTipo.SAC: "Serviço de Atendimento ao Consumidor",
    MarcaEmailTipo.ADMIN: "Administrativo",
}

# A marca que ASSINA pelo chamado do site: a Charlots do atendimento é a
# `charlots-park` (a letra C do protocolo, o domínio charlots.com.br), mas a
# das Redes Sociais e de Cadastros › E-mails — logo, site e WhatsApp — é a
# `poofy` (nome "charlots"). A resposta assina como a tela mostra.
MARCA_DA_ASSINATURA: dict[str, str] = {"charlots-park": "poofy"}

# Nome de exibição ("Equipe Charlots", "Equipe 7 Buyers").
NOME_EXIBICAO: dict[str, str] = {
    "uranyx": "Uranyx",
    "poofy": "Charlots",
    "7buyers": "7 Buyers",
    "locagil": "Locagil",
}

_F = "font-family:Arial,Helvetica,sans-serif;"
_P = f"{_F}font-size:13px;line-height:20px;color:#333333;margin:0;"


def visual_de(slug: str) -> Visual:
    return VISUAL.get(slug, VISUAL_PADRAO)


def nome_exibicao(marca: MarcaDaAssinatura) -> str:
    nome = NOME_EXIBICAO.get(marca.slug)
    if nome:
        return nome
    return marca.nome[:1].upper() + marca.nome[1:]


def tipo_do_endereco(endereco: str | None) -> str:
    """A caixa da assinatura pelo endereço que envia: sac@ → SAC; o resto → Admin."""
    local = (endereco or "").strip().lower().partition("@")[0]
    return MarcaEmailTipo.SAC if local == MarcaEmailTipo.SAC else MarcaEmailTipo.ADMIN


def formatar_fone(fone: str | None) -> str:
    """ "11983517003" → "+55 11 98351-7003" (o `sac_fone` fica só com dígitos)."""
    d = "".join(ch for ch in (fone or "") if ch.isdigit())
    if not d:
        return ""
    if len(d) >= 12 and d.startswith("55"):
        d = d[2:]
    if len(d) == 11:
        return f"+55 {d[:2]} {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"+55 {d[:2]} {d[2:6]}-{d[6:]}"
    return (fone or "").strip()


def site_da(marca: MarcaDaAssinatura) -> str | None:
    """O site da linha "Site:" (e do link do logo); só http(s)."""
    v = visual_de(marca.slug)
    if v.sem_site:
        return None
    site = (v.site or marca.site or "").strip()
    return site if site.lower().startswith(("https://", "http://")) else None


def _host(site: str) -> str:
    try:
        host = urlsplit(site).hostname or ""
    except ValueError:
        host = ""
    host = host.removeprefix("www.")
    return f"www.{host}" if host else site


def _whatsapp(fone: str) -> str:
    d = "".join(ch for ch in fone if ch.isdigit())
    if len(d) < 10:
        return ""
    return d if d.startswith("55") else "55" + d


def _cargo(tipo: str) -> str:
    return CARGO.get(tipo, CARGO[MarcaEmailTipo.ADMIN])


def montar_html(marca: MarcaDaAssinatura, tipo: str, email: str, fone: str) -> str:
    """A assinatura em HTML (tabela, estilo inline). PURA."""
    v = visual_de(marca.slug)
    nome = nome_exibicao(marca)
    site = site_da(marca)
    esc = html.escape
    if v.logo:
        lg = v.logo
        img = (
            f'<img src="{esc(lg.src)}" width="{lg.w}" height="{lg.h}" alt="{esc(nome)}" '
            f'style="display:block;border:0;width:{lg.w}px;height:{lg.h}px;">'
        )
        link = f'<a href="{esc(site)}" style="text-decoration:none;">{img}</a>' if site else img
        logo = (
            '<table cellpadding="0" cellspacing="0" border="0" role="presentation"><tr>'
            f'<td style="background:{lg.bg};padding:9px 12px;">{link}</td></tr></table>'
            if lg.bg
            else link
        )
    else:
        logo = (
            f'<span style="{_F}font-size:20px;font-weight:bold;letter-spacing:3px;'
            f'color:{v.cor};">{esc(nome.upper())}</span>'
        )
    lk = f"color:{v.cor};text-decoration:none;"
    linhas: list[str] = []
    email = email.strip()
    fone = fone.strip()
    if email:
        linhas.append(f'E-mail: <a href="mailto:{esc(email)}" style="{lk}">{esc(email)}</a>')
    if site:
        linhas.append(f'Site: <a href="{esc(site)}" style="{lk}">{esc(_host(site))}</a>')
    if fone:
        wa = _whatsapp(fone)
        numero = f'<a href="https://wa.me/{wa}" style="{lk}">{esc(fone)}</a>' if wa else esc(fone)
        linhas.append(f"WhatsApp: {numero}")
    corpo = "\n".join(f'<p style="{_P}">{linha}</p>' for linha in linhas)
    return (
        '<table cellpadding="0" cellspacing="0" border="0" role="presentation" '
        f'style="border-collapse:collapse;{_F}">\n'
        f'<tr><td style="padding:0 0 14px 0;"><p style="{_P}">Atenciosamente,</p></td></tr>\n'
        f'<tr><td style="padding:0 0 10px 0;">{logo}</td></tr>\n'
        f'<tr><td style="border-left:3px solid {v.cor};padding:2px 0 2px 12px;">\n'
        f'<p style="{_P}font-weight:bold;color:#1a1a1a;">Equipe {esc(nome)}</p>\n'
        f'<p style="{_P}">{esc(_cargo(tipo))}</p>\n'
        f"{corpo}\n"
        "</td></tr>\n"
        "</table>"
    )


def montar_texto(marca: MarcaDaAssinatura, tipo: str, email: str, fone: str) -> str:
    """A mesma assinatura em texto puro (a parte de texto e a prévia). PURA."""
    site = site_da(marca)
    linhas = ["Atenciosamente,", f"Equipe {nome_exibicao(marca)}", _cargo(tipo)]
    if email.strip():
        linhas.append(f"E-mail: {email.strip()}")
    if site:
        linhas.append(f"Site: {_host(site)}")
    if fone.strip():
        linhas.append(f"WhatsApp: {fone.strip()}")
    return "\n".join(linhas)
