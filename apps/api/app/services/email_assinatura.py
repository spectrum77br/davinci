"""Renderiza apenas o rodapé. Não gera assunto/corpo e não envia e-mail."""

from base64 import b64encode
from dataclasses import dataclass, field
from html import escape
from typing import Literal

from app.models import Company, Marca
from app.services.email_marca import (
    _assinatura_html,
    _assinatura_texto,
    _icone_whatsapp,
    _texto_para_html,
    variaveis_da_marca,
)


@dataclass
class AssinaturaRenderizada:
    html: str
    text: str
    avisos: list[str]
    inline_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def render_assinatura(
    marca: Marca,
    company: Company | None,
    *,
    texto: str,
    incluir_logo: bool,
    incluir_dados_marca: bool,
    imagens: Literal["cid", "data"] = "cid",
) -> AssinaturaRenderizada:
    logo_html = ""
    texto_html = ""
    dados_html = ""
    texto_partes: list[str] = []
    avisos: list[str] = []
    inline_images: dict[str, tuple[str, bytes]] = {}
    if incluir_logo:
        if marca.logo_mime and marca.logo:
            inline_images["assinatura-logo"] = (marca.logo_mime, marca.logo)
            logo_html = (
                '<img src="cid:assinatura-logo" width="120" '
                f'alt="{escape(marca.nome)}" '
                'style="display:block;width:auto;max-width:120px;max-height:80px;border:0">'
            )
        else:
            avisos.append("Logo não cadastrada em Marcas.")
    if texto.strip():
        texto_html = (
            f'<div style="font-size:14px;line-height:1.6">{_texto_para_html(texto)}</div>'
        )
        texto_partes.append(texto.rstrip())
    if incluir_dados_marca:
        # Nome como cadastrado (planilha/Marcas): nada de capitalizar.
        v = variaveis_da_marca(marca, company)
        icone = _icone_whatsapp() if v["whatsapp"] else None
        dados_html = _assinatura_html(
            v, com_icone_zap=bool(icone), com_separador=bool(logo_html or texto_html)
        )
        if icone:
            inline_images["assinatura-whatsapp"] = ("image/png", icone)
            dados_html = dados_html.replace("cid:whatsapp", "cid:assinatura-whatsapp")
        texto_partes.append(_assinatura_texto(v))
        if not company:
            avisos.append("Empresa não vinculada: razão social e CNPJ não aparecem.")
        if not marca.sac_fone:
            avisos.append("WhatsApp não preenchido em Redes Sociais.")
        if not marca.sac_email:
            avisos.append("E-mail SAC não preenchido em Redes Sociais.")
        if not marca.site:
            avisos.append("Site não preenchido em Marcas.")
    if logo_html and texto_html:
        # Só logo e despedida ficam lado a lado; os contatos ocupam toda a largura.
        cabecalho_html = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="100%" style="border-collapse:collapse;table-layout:fixed"><tr>'
            '<td width="120" valign="top" style="width:120px;padding:4px 24px 0 0">'
            + logo_html
            + '</td><td valign="top" '
            'style="padding:0;overflow-wrap:anywhere;word-break:break-word">'
            + texto_html
            + "</td></tr></table>"
        )
    else:
        cabecalho_html = logo_html or texto_html
    html = (
        '<div style="font-family:Arial,sans-serif;color:#111827;padding:24px;background:white">'
        + cabecalho_html
        + dados_html
        + "</div>"
    )
    if imagens == "data":
        for cid, (mime, data) in inline_images.items():
            html = html.replace(f"cid:{cid}", f"data:{mime};base64,{b64encode(data).decode()}")
        # O Tuta guarda a assinatura como está (imagem em base64 inclusa) e
        # recomenda ≤ 15 KB; acima disso avisa e pode recusar. Gmail corta o
        # e-mail em ~102 KB. O logo já é reduzido no upload (routers/marcas).
        kb = len(html.encode()) / 1024
        if kb > 15:
            avisos.append(
                f"Assinatura com {kb:.0f} KB (Tuta recomenda até 15 KB): use um logo menor."
            )
    return AssinaturaRenderizada(
        html=html, text="\n\n".join(texto_partes), avisos=avisos, inline_images=inline_images
    )
