"""Padrões de e-mail das marcas — renderização com logo e assinatura.

Eduardo, 15/09/2026: "o envio automático de e-mail dos SAC tem que ser
padronizado com logo da marca, assinatura da empresa, site, logo do zap e o
zap". Aqui mora a renderização: o operador escreve `assunto`/`corpo` como
templates Jinja SÓ com expressões (`{{ marca }}`, `{{ cliente }}`…), em
SANDBOX e com autoescape — o texto do corpo nunca vira HTML, só os
placeholders são substituídos. O HTML final é uma tabela de 600px com o logo
no topo (`cid:logo`) e a assinatura da empresa embaixo (razão social + CNPJ,
site, WhatsApp com ícone `cid:whatsapp`, e-mail SAC). Também sai a versão em
texto puro. As imagens vão INLINE (Mailjet InlinedAttachments) — e-mail não
carrega imagem de URL autenticada.

Usado pela prévia e pelo envio de teste (routers/email_padroes.py) e por
robôs/automações via POST /api/email-padroes/{id}/render.
"""

from __future__ import annotations

import html as html_mod
from base64 import b64encode
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import DebugUndefined, TemplateError, Undefined, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import escape

from app.config import get_settings
from app.models import Company, Marca, MarcaEmailPadrao

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "email_templates"
WHATSAPP_ICON = TEMPLATES_DIR / "whatsapp.png"

# Placeholders que o operador pode usar (documentados na tela).
PLACEHOLDERS_MARCA = ("marca", "empresa", "cnpj", "site", "whatsapp", "whatsapp_link", "email_sac")
PLACEHOLDERS_CONTEXTO = ("cliente", "pedido", "produto", "plataforma")
PLACEHOLDERS = PLACEHOLDERS_MARCA + PLACEHOLDERS_CONTEXTO

# Teto do HTML renderizado (um template cheio de placeholders repetidos não
# vira um e-mail de megabytes).
MAX_RENDER_CHARS = 20_000
# Filtros que o operador pode usar nos placeholders ({{ cliente|title }}).
FILTROS_PERMITIDOS = frozenset({"upper", "lower", "title", "trim", "capitalize"})

# Os templates são TEXTO (autoescape do Jinja só escaparia os valores das
# variáveis, nunca o texto literal do operador): renderiza como texto e o
# HTML nasce de markupsafe.escape() do resultado + quebras → <br>. Assim nem
# o corpo nem um placeholder viram HTML. Na prévia, DebugUndefined mostra
# `{{ marcaa }}` literal quando o nome está errado; no envio, Undefined → "".
_env = SandboxedEnvironment(autoescape=False, undefined=Undefined, enable_async=False)
_env_previa = SandboxedEnvironment(autoescape=False, undefined=DebugUndefined, enable_async=False)
# Sem globals (range, lipsum, cycler…): o template é texto + placeholders.
_env.globals.clear()
_env_previa.globals.clear()
# Whitelist da AST (revisão 15/09): texto literal, `{{ placeholder }}` e
# `{{ placeholder|filtro }}`. Nada de bloco, chamada, atributo, índice,
# aritmética ou constante — o sandbox NÃO barra `{{ 'x' * 10**9 }}` (aloca
# antes de qualquer teto) nem `{{ 2 ** (2 ** 31) }}` (trava o event loop).
_NODES_OK = (nodes.Template, nodes.Output, nodes.TemplateData)


class TemplateInvalidoError(ValueError):
    """Template que o Jinja não compila ou que usa bloco `{% %}`."""


@dataclass
class EmailRenderizado:
    assunto: str
    html: str
    text: str
    # From explícito (remetente_email validado no Mailjet) ou None = EMAIL_FROM.
    from_email: str | None
    from_name: str | None
    # (e-mail, nome) do Reply-To — o SAC da marca, quando existe.
    reply_to: tuple[str, str] | None = None
    # {content_id: (mime, bytes)} — só o que o HTML referencia.
    inline_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    # O que falta na marca pra assinatura sair completa (a prévia mostra).
    avisos: list[str] = field(default_factory=list)
    # Placeholders usados no template que o sistema não conhece (prévia).
    variaveis_desconhecidas: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _icone_whatsapp() -> bytes | None:
    try:
        return WHATSAPP_ICON.read_bytes()
    except OSError:
        return None


def formatar_cnpj(cnpj: str | None) -> str:
    d = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(d) != 14:
        return cnpj or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def formatar_fone(fone: str | None) -> str:
    """Só dígitos → (11) 98888-7777 / (11) 3333-4444; outros tamanhos ficam
    como estão (com +55 quando vem com DDI)."""
    d = "".join(ch for ch in (fone or "") if ch.isdigit())
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d


def whatsapp_link(fone: str | None) -> str:
    d = "".join(ch for ch in (fone or "") if ch.isdigit())
    if not d:
        return ""
    if not d.startswith("55"):
        d = "55" + d
    return f"https://wa.me/{d}"


def variaveis_da_marca(marca: Marca, company: Company | None) -> dict[str, str]:
    return {
        "marca": marca.nome,
        "empresa": (company.razao_social if company else "") or "",
        "cnpj": formatar_cnpj(company.cnpj) if company else "",
        "site": marca.site or "",
        "whatsapp": formatar_fone(marca.sac_fone),
        "whatsapp_link": whatsapp_link(marca.sac_fone),
        "email_sac": marca.sac_email or "",
    }


def _variaveis(marca: Marca, company: Company | None, extra: dict[str, Any] | None) -> dict:
    v: dict[str, Any] = dict.fromkeys(PLACEHOLDERS_CONTEXTO, "")
    v.update(variaveis_da_marca(marca, company))
    for k, val in (extra or {}).items():
        # Só os placeholders de CONTEXTO entram de fora (cliente, pedido…), e
        # como texto; os da marca (site, whatsapp_link, email_sac — viram href
        # na assinatura) ninguém sobrescreve.
        if k in PLACEHOLDERS_CONTEXTO:
            v[k] = "" if val is None else str(val)[:500]
    return v


def _checa_no(no: nodes.Node) -> None:
    if isinstance(no, _NODES_OK):
        for filho in no.iter_child_nodes():
            _checa_no(filho)
        return
    if isinstance(no, nodes.Name):
        if no.name not in PLACEHOLDERS:
            raise TemplateInvalidoError(f"placeholder_desconhecido: {no.name}")
        return
    if isinstance(no, nodes.Filter):
        proibido = no.args or no.kwargs or no.dyn_args or no.dyn_kwargs
        if no.name not in FILTROS_PERMITIDOS or proibido:
            raise TemplateInvalidoError(f"filtro_nao_permitido: {no.name}")
        _checa_no(no.node)
        return
    raise TemplateInvalidoError(f"template_bloco_nao_permitido: {type(no).__name__}")


def validar_template(texto: str) -> None:
    """Levanta TemplateInvalidoError se o template não compila ou sai da
    whitelist (só texto, `{{ placeholder }}` e `{{ placeholder|filtro }}`).
    Chamada pelo schema (POST/PATCH) e de novo antes de renderizar."""
    if "{%" in (texto or ""):
        raise TemplateInvalidoError("template_bloco_nao_permitido")
    try:
        arvore = _env.parse(texto or "")
    except TemplateError as e:
        raise TemplateInvalidoError(f"template_invalido: {e}") from e
    _checa_no(arvore)


def _render(env: SandboxedEnvironment, texto: str, variaveis: dict) -> str:
    validar_template(texto)
    try:
        out = env.from_string(texto or "").render(**variaveis)
    except Exception as e:  # OverflowError/ValueError também viram 422, nunca 500
        raise TemplateInvalidoError(f"template_invalido: {type(e).__name__}") from e
    if len(out) > MAX_RENDER_CHARS:
        raise TemplateInvalidoError("template_saida_muito_grande")
    return out


def _texto_para_html(texto: str) -> str:
    """Escapa DEPOIS de renderizar (sem duplo-escape de '&' nos placeholders)
    e troca quebra de linha por <br>."""
    return str(escape(texto)).replace("\r\n", "\n").replace("\n", "<br>\n")


def variaveis_desconhecidas(*templates: str) -> list[str]:
    """Nomes usados nos templates que não são placeholders suportados."""
    usados: set[str] = set()
    for t in templates:
        try:
            usados |= meta.find_undeclared_variables(_env.parse(t or ""))
        except TemplateError:
            continue
    return sorted(usados - set(PLACEHOLDERS))


def _assinatura_html(
    v: dict[str, str], *, com_icone_zap: bool, com_separador: bool = True
) -> str:
    linhas: list[str] = [
        '<div style="padding-bottom:10px;font-weight:600;color:#111827;">'
        f'{html_mod.escape(v["marca"])}</div>'
    ]
    if v["empresa"]:
        emp = html_mod.escape(v["empresa"])
        if v["cnpj"]:
            emp += f" &middot; CNPJ {html_mod.escape(v['cnpj'])}"
        linhas.append(f'<div style="padding-bottom:10px;color:#4b5563;">{emp}</div>')
    if v["site"]:
        s = html_mod.escape(v["site"])
        linhas.append(
            f'<div style="padding-bottom:10px"><a href="{s}" '
            f'style="color:#2563eb;text-decoration:none;">{s}</a></div>'
        )
    if v["whatsapp"]:
        icone = (
            '<img src="cid:whatsapp" width="28" height="28" alt="WhatsApp" '
            'style="display:inline-block;width:28px;height:28px;vertical-align:middle;'
            'margin-right:12px;border:0;">'
            if com_icone_zap
            else ""
        )
        link = html_mod.escape(v["whatsapp_link"])
        linhas.append(
            '<div style="padding-bottom:10px">'
            f'<a href="{link}" style="color:#111827;text-decoration:none;">'
            f"{icone}WhatsApp {html_mod.escape(v['whatsapp'])}</a></div>"
        )
    if v["email_sac"]:
        e = html_mod.escape(v["email_sac"])
        linhas.append(
            f'<div><a href="mailto:{e}" style="color:#2563eb;text-decoration:none;">{e}</a></div>'
        )
    separador = (
        "margin-top:24px;padding-top:20px;border-top:1px solid #e5e7eb;"
        if com_separador
        else ""
    )
    return f'<div style="{separador}font-size:13px;line-height:1.6;">' + "".join(linhas) + "</div>"


def _assinatura_texto(v: dict[str, str]) -> str:
    linhas = ["--", v["marca"]]
    if v["empresa"]:
        linhas.append(v["empresa"] + (f" · CNPJ {v['cnpj']}" if v["cnpj"] else ""))
    if v["site"]:
        linhas.append(v["site"])
    if v["whatsapp"]:
        linhas.append(f"WhatsApp {v['whatsapp']} — {v['whatsapp_link']}")
    if v["email_sac"]:
        linhas.append(v["email_sac"])
    return "\n".join(linhas)


def render_email(
    *,
    marca: Marca,
    company: Company | None,
    assunto: str,
    corpo: str,
    incluir_logo: bool = True,
    incluir_assinatura: bool = True,
    remetente_nome: str | None = None,
    remetente_email: str | None = None,
    variaveis: dict[str, Any] | None = None,
    imagens: Literal["cid", "data"] = "cid",
    previa: bool = False,
) -> EmailRenderizado:
    """Renderiza assunto/corpo (Jinja em sandbox) e monta o HTML com logo e
    assinatura. Levanta TemplateInvalidoError quando o template não presta.

    `imagens="cid"` referencia as imagens inline (`cid:logo`) — é o que vai
    pro Mailjet; `"data"` embute como data: URI, pra prévia num iframe
    (onde `cid:` não existe e uma URL da API sairia sem cookie)."""
    v = _variaveis(marca, company, variaveis)
    env = _env_previa if previa else _env
    # Assunto numa linha só (CR/LF num header de e-mail = injeção de header).
    assunto_r = " ".join(_render(env, assunto, v).split())
    corpo_txt = _render(env, corpo, v)
    corpo_html = _texto_para_html(corpo_txt)

    imagens_inline: dict[str, tuple[str, bytes]] = {}
    partes: list[str] = []
    if incluir_logo and marca.logo_mime and marca.logo:
        imagens_inline["logo"] = (marca.logo_mime, marca.logo)
        partes.append(
            '<div style="margin-bottom:20px;"><img src="cid:logo" alt="'
            + html_mod.escape(marca.nome)
            + '" style="max-height:60px;max-width:240px;"></div>'
        )
    partes.append(f'<div style="font-size:15px;line-height:1.6;color:#111827;">{corpo_html}</div>')
    texto = corpo_txt
    if incluir_assinatura:
        icone = _icone_whatsapp()
        com_icone = bool(v["whatsapp"] and icone)
        if com_icone:
            imagens_inline["whatsapp"] = ("image/png", icone)  # type: ignore[arg-type]
        partes.append(_assinatura_html(v, com_icone_zap=com_icone))
        texto = corpo_txt.rstrip() + "\n\n" + _assinatura_texto(v)

    html = (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">'
        f"<title>{html_mod.escape(assunto_r)}</title></head>"
        '<body style="margin:0;padding:24px;background:#f3f4f6;'
        'font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="max-width:600px;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;">'
        '<tr><td style="padding:28px 32px;">' + "".join(partes) + "</td></tr>"
        "</table></td></tr></table></body></html>"
    )
    if imagens == "data":
        for cid, (mime, data) in imagens_inline.items():
            html = html.replace(f"cid:{cid}", f"data:{mime};base64,{b64encode(data).decode()}")
    settings = get_settings()
    # From explícito só quando o operador preencheu (precisa estar validado no
    # Mailjet); o padrão é EMAIL_FROM com o nome da marca e Reply-To no SAC.
    from_email = (remetente_email or "").strip() or None
    from_name = (remetente_nome or "").strip() or marca.nome or settings.email_from_name
    reply_to = (marca.sac_email, marca.nome) if marca.sac_email else None
    avisos: list[str] = []
    if incluir_assinatura:
        if not company:
            avisos.append("marca sem empresa vinculada: assinatura sai sem razão social/CNPJ")
        if not marca.sac_fone:
            avisos.append("marca sem Fone/WhatsApp: assinatura sai sem WhatsApp")
        if not marca.sac_email:
            avisos.append("marca sem e-mail SAC: sem Reply-To e sem e-mail na assinatura")
        if not marca.site:
            avisos.append("marca sem site")
    if incluir_logo and not marca.logo_mime:
        avisos.append("marca sem logo")
    return EmailRenderizado(
        assunto=assunto_r,
        html=html,
        text=texto,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to,
        inline_images=imagens_inline,
        avisos=avisos,
        variaveis_desconhecidas=variaveis_desconhecidas(assunto, corpo) if previa else [],
    )


def render_padrao(
    padrao: MarcaEmailPadrao,
    marca: Marca,
    company: Company | None,
    variaveis: dict[str, Any] | None = None,
    imagens: Literal["cid", "data"] = "cid",
) -> EmailRenderizado:
    return render_email(
        marca=marca,
        company=company,
        assunto=padrao.assunto,
        corpo=padrao.corpo,
        incluir_logo=padrao.incluir_logo,
        incluir_assinatura=padrao.incluir_assinatura,
        remetente_nome=padrao.remetente_nome,
        remetente_email=padrao.remetente_email,
        variaveis=variaveis,
        imagens=imagens,
    )
