"""Usar este IP no celular (16/11/2026).

Eduardo: "se eu jogar um link para o meu celular, o meu celular usa o IP da
loja (ex.: Mercado Livre Atlas) por um tempo". O desenho, as proteções e o que
fazer no servidor estão em docs/ip-celular.md.

Duas partes:
- `router` (/api/ip-celular): só ADMIN e só com a tela Empresas desbloqueada
  (a mesma senha extra das rotas de proxy). Cria o link (15 min, 1 h ou 4 h),
  mostra o QR de novo, lista os links e corta ("Cortar agora").
- `router_publico` (/api/ipcel/<código>): a página que abre no iPhone, sem
  login. O GET só mostra o botão — a prévia de link do WhatsApp/iMessage faz
  GET e não pode gastar o link. O POST gasta o link (uso único), põe num
  cookie SÓ deste endereço um token da página e redireciona (303) para o GET,
  que com o cookie mostra os botões do Shadowrocket com a credencial
  TEMPORÁRIA. Recarregar, voltar ou o iOS descartar a aba não perde a página
  (o código já gasto, sem o cookie, não mostra nada). A senha real do proxy
  nunca aparece. Também o módulo do Shadowrocket (sem segredo) que segura o
  UDP/QUIC.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin
from app.ip_celular import servico
from app.models import User
from app.routers.auth import _client_ip
from app.security.senha_extra import require_empresas_unlock

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/ip-celular",
    tags=["ip-celular"],
    dependencies=[Depends(require_empresas_unlock)],
)
router_publico = APIRouter(prefix="/api/ipcel", tags=["ip-celular-publico"])


class CriarIn(BaseModel):
    company_id: UUID | None = None
    store_info_id: UUID | None = None
    minutos: Literal[15, 60, 240]


class LinkOut(BaseModel):
    link_id: str
    empresa_id: str | None = None
    empresa: str | None = None
    nome: str | None = None
    status: str
    minutos: int | None = None
    criado_em: str | None = None
    criado_por: str | None = None
    # Quem pede gerou este link (só ele vê o QR de novo).
    meu: bool = False
    expira_em: str | None = None
    ate: str | None = None
    aberto_em: str | None = None
    aberto_as: str | None = None
    aberto_ip: str | None = None
    # A credencial só vale do primeiro IP que entrou; outro IP é recusado.
    preso_ip: str | None = None
    outro_ip: str | None = None
    outro_ip_as: str | None = None
    outro_ip_n: int = 0
    cortado_em: str | None = None
    link_vence_em: str | None = None
    conexoes: int = 0
    abertas: int = 0
    recusadas: int = 0
    bytes_sobe: int = 0
    bytes_desce: int = 0
    # Só na criação e no "mostrar o QR de novo" (enquanto ninguém abriu).
    url: str | None = None
    qr: str | None = None


class AlvoOut(BaseModel):
    empresa_id: str
    empresa: str
    nome: str
    pode: bool
    motivo: str | None = None
    ligado: bool


@router.get("/alvo", response_model=AlvoOut)
async def ver_alvo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
    company_id: UUID | None = None,
    store_info_id: UUID | None = None,
) -> AlvoOut:
    """Antes de gerar: de qual empresa sai o IP e se dá para gerar."""
    alvo = await servico.resolver_alvo(session, company_id=company_id, store_info_id=store_info_id)
    motivo = servico.motivo_empresa_sem_ip_celular(alvo.empresa)
    try:
        servico.cofre()
        ligado = True
    except Exception:  # noqa: BLE001 - 503 ipcel_desligado
        ligado = False
    return AlvoOut(
        empresa_id=str(alvo.empresa.id),
        empresa=alvo.empresa.apelido,
        nome=alvo.nome,
        pode=motivo is None and ligado,
        motivo=motivo if motivo is not None else (None if ligado else "ipcel_desligado"),
        ligado=ligado,
    )


@router.post("/links", response_model=LinkOut, status_code=201)
async def criar_link(
    body: CriarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> LinkOut:
    alvo = await servico.resolver_alvo(
        session, company_id=body.company_id, store_info_id=body.store_info_id
    )
    return LinkOut(**await servico.criar(session, alvo, body.minutos, admin))


@router.get("/links", response_model=list[LinkOut])
async def listar_links(
    admin: Annotated[User, Depends(require_admin)],
    company_id: Annotated[UUID | None, Query()] = None,
) -> list[LinkOut]:
    itens = await servico.listar(company_id=str(company_id) if company_id else None, por=admin)
    return [LinkOut(**x) for x in itens]


@router.get("/links/{link_id}/qr", response_model=LinkOut)
async def mostrar_qr(
    link_id: str,
    admin: Annotated[User, Depends(require_admin)],
) -> LinkOut:
    """Só quem gerou; fica no Histórico (nomes.REVELACOES)."""
    return LinkOut(**await servico.ver_qr(link_id, admin))


@router.post("/links/{link_id}/cortar", response_model=LinkOut)
async def cortar_link(
    link_id: str,
    admin: Annotated[User, Depends(require_admin)],
) -> LinkOut:
    return LinkOut(**await servico.cortar(link_id, admin))


@router.post("/links/{link_id}/soltar", response_model=LinkOut)
async def soltar_ip_do_link(
    link_id: str,
    admin: Annotated[User, Depends(require_admin)],
) -> LinkOut:
    """O celular trocou de rede: o próximo IP que entrar fica com o link."""
    return LinkOut(**await servico.soltar_ip(link_id, admin))


# ---------------------------------------------------------------- página do iPhone

_RX_CODIGO_NO_PATH = re.compile(r"(/api/ipcel/)[A-Za-z0-9_-]+")
APP_STORE = "https://apps.apple.com/br/app/shadowrocket/id932747118"
LIMITE_PAGINA_MIN = 30


def mascarar_codigo_no_access_log() -> None:
    """O código de uso único vai no path: some do log de acesso do uvicorn
    (mesmo jeito do conector do Claude)."""

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], _RX_CODIGO_NO_PATH.sub(r"\1***", args[2]), *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())


def _e(v: object) -> str:
    return html_mod.escape(str(v), quote=True)


def _pagina(titulo: str, corpo: str, *, status_code: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_e(titulo)} — DaVinci</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
         padding: 0 16px; background: #f4f4f5; color: #18181b; }}
  .card {{ background: #fff; border-radius: 14px; padding: 22px 18px;
           margin: 16px auto; max-width: 440px; box-sizing: border-box;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  a {{ color: #047857; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 6px; }}
  h2 {{ font-size: 1rem; margin: 22px 0 6px; }}
  p, li {{ color: #3f3f46; line-height: 1.5; margin: 6px 0; font-size: .95rem; }}
  ol {{ padding-left: 20px; }}
  .nome {{ font-weight: 600; color: #18181b; }}
  .ok {{ color: #047857; }} .erro {{ color: #b91c1c; }} .aviso {{ color: #92400e; }}
  .caixa {{ background: #fef3c7; border-radius: 10px; padding: 10px 12px; margin: 12px 0; }}
  a.botao, button {{ display: block; box-sizing: border-box; width: 100%;
            text-align: center; text-decoration: none; background: #047857;
            color: #fff; border: 0; border-radius: 10px; padding: 14px;
            font-size: 1rem; font-weight: 600; margin-top: 10px;
            cursor: pointer; font-family: inherit; }}
  a.botao.secundario, button.secundario {{ background: #e4e4e7; color: #18181b; }}
  a.botao.perigo {{ background: #b91c1c; }}
  textarea {{ width: 100%; box-sizing: border-box; font-family: ui-monospace, monospace;
              font-size: .75rem; border-radius: 8px; border: 1px solid #d4d4d8;
              padding: 8px; margin-top: 8px; }}
  small {{ color: #71717a; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #09090b; color: #fafafa; }}
    .card {{ background: #18181b; }}
    p, li {{ color: #d4d4d8; }} .nome {{ color: #fafafa; }}
    .caixa {{ background: #422006; }} .aviso {{ color: #fcd34d; }}
    a.botao.secundario, button.secundario {{ background: #3f3f46; color: #fafafa; }}
    textarea {{ background: #09090b; color: #fafafa; border-color: #3f3f46; }}
    a {{ color: #6ee7b7; }}
  }}
</style></head>
<body><div class="card">{corpo}</div></body></html>"""
    resp = HTMLResponse(doc, status_code=status_code)
    # Nada desta página fica guardado nem vaza para outro site.
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


_ESPERE = "<p class='erro'>Muitas tentativas. Espere 1 minuto.</p>"
_DESLIGAR = "shadowrocket://disconnect"
COOKIE = "ipcel_ver"


def _botao_desligar() -> str:
    return (
        "<p>Se você já tinha ligado o Shadowrocket com este IP, desligue:</p>"
        f"<a class='botao perigo' href='{_e(_DESLIGAR)}'>Desligar o Shadowrocket</a>"
    )


_INVALIDO = (
    "<h1>IP da loja no celular</h1>"
    "<p class='erro'>Este link não vale mais: venceu, já foi aberto ou foi cortado.</p>"
    "<p>Se você já tocou em <b>mostrar o IP</b> NESTE iPhone, o proxy já está no "
    "Shadowrocket: ligue e desligue por lá.</p>"
    "<p>Para usar de novo, gere outro no DaVinci (Empresas › Usar este IP no celular).</p>"
    + _botao_desligar()
)
_ACABOU = {
    "vencido": "Acabou o tempo deste IP.",
    "cortado": "Este IP foi cortado no DaVinci.",
    "limite_de_dados": "Este IP passou do limite de dados e foi cortado.",
    "nao_aberto": "Este link venceu.",
}


async def _limite_pagina(request: Request) -> bool:
    """30 aberturas por minuto por IP (o código é longo; isto só segura robô)."""
    from app.services.rate_limit import RateLimitError, sliding_window_check

    ip = _client_ip(request) or "?"
    try:
        await sliding_window_check(
            key=servico.chaves().pagina(ip), limit=LIMITE_PAGINA_MIN, window_seconds=60
        )
    except RateLimitError:
        return False
    except Exception:  # noqa: BLE001 - Redis fora: a página falha mais adiante
        return True
    return True


@router_publico.get(f"/{servico.NOME_MODULO}")
async def modulo_shadowrocket() -> PlainTextResponse:
    """O módulo do Shadowrocket que segura UDP/QUIC (sem segredo: o mesmo para
    todos). Registrado ANTES do /{codigo}."""
    return PlainTextResponse(
        servico.MODULO,
        headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"},
    )


def _https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    return proto == "https" or get_settings().is_prod


@router_publico.get("/{codigo}")
async def pagina_do_link(codigo: str, request: Request) -> HTMLResponse:
    if not await _limite_pagina(request):
        return _pagina("Espere um pouco", _ESPERE, status_code=429)
    # Já gasto NESTE iPhone (cookie só deste endereço): a página de novo.
    aberta = await servico.pagina_aberta(request.cookies.get(COOKIE), codigo)
    if aberta is not None:
        if "acabou" in aberta:
            corpo = (
                "<h1>IP da loja no celular</h1>"
                f"<p class='nome'>{_e(aberta.get('nome') or '')}</p>"
                f"<p class='erro'>{_e(_ACABOU.get(aberta['acabou'], 'Este IP não vale mais.'))}</p>"
                "<p>Desligue o Shadowrocket: com ele ligado o iPhone fica sem internet.</p>"
                f"<a class='botao perigo' href='{_e(_DESLIGAR)}'>Desligar o Shadowrocket</a>"
            )
            return _pagina("IP da loja no celular", corpo)
        return _pagina("IP da loja no celular", _passos(aberta))
    dados = await servico.ver_codigo(codigo)
    if dados is None:
        return _pagina("Link inválido", _INVALIDO, status_code=404)
    corpo = (
        "<h1>IP da loja no celular</h1>"
        f"<p class='nome'>{_e(dados['nome'])}</p>"
        f"<p>Vale até <b>{_e(dados['ate'])}</b> (horário de Brasília).</p>"
        "<div class='caixa'><p class='aviso'><b>Este link funciona UMA vez, em UM iPhone.</b> "
        "Abra no iPhone que vai usar o IP. Ao tocar no botão verde ele se gasta.</p></div>"
        "<p>Precisa do app <b>Shadowrocket</b> (pago, R$ 19,90 — o certo é o da "
        "<i>Shadow Launch Technology Limited</i>). Instale ANTES de tocar no botão:</p>"
        f"<a class='botao secundario' href='{_e(APP_STORE)}'>Abrir o Shadowrocket na App Store</a>"
        f"<form method='post' action='/api/ipcel/{_e(codigo)}'>"
        "<button type='submit'>Já tenho o Shadowrocket — mostrar o IP</button></form>"
    )
    return _pagina("IP da loja no celular", corpo)


@router_publico.post("/{codigo}")
async def abrir_link(codigo: str, request: Request):
    """Gasta o link e manda para o GET (303): recarregar não reenvia nada e a
    página volta enquanto o IP valer, só neste iPhone (cookie só deste
    endereço, HttpOnly)."""
    if not await _limite_pagina(request):
        return _pagina("Espere um pouco", _ESPERE, status_code=429)
    destino = f"/api/ipcel/{codigo}"
    # Reenvio do formulário (Safari "enviar de novo"): com o cookie, só volta.
    if await servico.pagina_aberta(request.cookies.get(COOKIE), codigo) is not None:
        return RedirectResponse(destino, status_code=303)
    aberto = await servico.abrir_codigo(codigo, ip=_client_ip(request))
    if aberto is None:
        return _pagina("Link inválido", _INVALIDO, status_code=404)
    token, segundos = aberto
    resp = RedirectResponse(destino, status_code=303)
    resp.set_cookie(
        COOKIE,
        token,
        max_age=segundos,
        path=destino,
        httponly=True,
        secure=_https(request),
        samesite="lax",
    )
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _passos(dados: dict) -> str:
    lk = servico.links_shadowrocket(dados)
    ip_loja = _e(dados.get("ip") or "da loja")
    return (
        "<h1>IP da loja no celular</h1>"
        f"<p class='nome'>{_e(dados['nome'])}</p>"
        f"<p>Funciona até <b>{_e(dados['ate'])}</b>. Depois disso o IP para e o celular fica "
        "sem internet até você <b>desligar o Shadowrocket</b>.</p>"
        "<p><small>Se esta página sumir, volte por este mesmo link neste iPhone: ela "
        "aparece de novo enquanto o IP valer. O proxy também já fica guardado no "
        "Shadowrocket — dá para ligar e desligar por lá.</small></p>"
        "<h2>1. Proteção contra vazamento (uma vez só)</h2>"
        "<p>Faz o UDP e o QUIC (que o proxy não leva) serem recusados, em vez de saírem "
        "pelo IP do celular.</p>"
        f"<a class='botao' href='{_e(lk['instalar_modulo'])}'>Instalar a proteção no "
        "Shadowrocket</a>"
        "<p><small>Não abriu? No Shadowrocket: "
        "<b>Configuração (Config) › Módulos (Modules) › +</b> e cole este endereço:</small></p>"
        f"<textarea id='modulo' rows='2' readonly>{_e(lk['modulo'])}</textarea>"
        "<button type='button' class='secundario' data-copiar='modulo'>"
        "Copiar o endereço da proteção</button>"
        "<h2>2. Adicionar o IP no Shadowrocket</h2>"
        f"<a class='botao' href='{_e(lk['add'])}'>Adicionar no Shadowrocket</a>"
        f"<a class='botao secundario' href='{_e(lk['add_codificado'])}'>Não abriu? Tente este</a>"
        "<button type='button' class='secundario' data-copiar='socks'>"
        "Copiar o link do proxy</button>"
        "<p><small>Copiado, abra o Shadowrocket: ele pergunta se quer adicionar.</small></p>"
        f"<textarea id='socks' rows='3' readonly>{_e(lk['socks'])}</textarea>"
        "<details><summary><small>Outros formatos (se nenhum dos acima funcionar)</small>"
        "</summary>"
        f"<textarea id='socks_v2' rows='3' readonly>{_e(lk['socks_v2'])}</textarea>"
        "<button type='button' class='secundario' data-copiar='socks_v2'>"
        "Copiar o formato 2</button>"
        f"<textarea id='texto' rows='3' readonly>{_e(lk['texto'])}</textarea>"
        "<button type='button' class='secundario' data-copiar='texto'>Copiar o formato 3</button>"
        "</details>"
        "<h2>3. Antes de ligar</h2>"
        "<ol>"
        "<li>Feche o app do Mercado Livre (arraste para cima).</li>"
        "<li>No Shadowrocket, em <b>Ajustes (Settings) › UDP</b>: ligue "
        "<b>Desativar STUN (Disable STUN)</b>.</li>"
        "<li>Em <b>Roteamento global (Global Routing)</b>, escolha <b>Proxy</b> "
        "(ou toque em <i>Modo Proxy</i> abaixo).</li>"
        "<li>Deixe desligados <b>Sempre ativo</b> e <b>Conectar sob demanda</b>.</li>"
        "</ol>"
        "<h2>4. Ligar</h2>"
        f"<a class='botao secundario' href='{_e(lk['selecionar'])}'>Escolher este IP</a>"
        f"<a class='botao secundario' href='{_e(lk['modo_proxy'])}'>Modo Proxy (todo o tráfego)</a>"
        f"<a class='botao' href='{_e(lk['ligar'])}'>Ligar</a>"
        "<p><small>Ou pelo próprio app: toque no nome acima e ligue a chave do topo.</small></p>"
        "<h2>5. Conferir ANTES de abrir o Mercado Livre</h2>"
        "<ol>"
        "<li>No Safari, abra "
        "<a href='https://browserleaks.com/ip'>browserleaks.com/ip</a>: tem de aparecer "
        f"<b>só o IP {ip_loja}</b> — no IPv4 e em qualquer outro lugar da página.</li>"
        "<li>Abra também <a href='https://browserleaks.com/webrtc'>browserleaks.com/webrtc</a>: "
        "nenhum IP do seu celular, da sua casa ou da operadora (Vivo, Claro, TIM…).</li>"
        "</ol>"
        "<div class='caixa'><p class='aviso'><b>Se aparecer qualquer IPv6 ou qualquer outro IP, "
        "NÃO abra o Mercado Livre:</b> desligue o Shadowrocket e avise.</p></div>"
        "<h2>6. Usar</h2>"
        "<p>Abra o app do Mercado Livre e faça a leitura / verificação.</p>"
        "<p><small>Este IP só funciona na rede em que você ligou (Wi-Fi ou 4G). Trocou de "
        "rede no meio? No DaVinci, toque em <b>Soltar o IP</b> no link.</small></p>"
        "<h2>7. Quando terminar</h2>"
        f"<a class='botao perigo' href='{_e(lk['desligar'])}'>Desligar o Shadowrocket</a>"
        "<p><small>Não encaminhe esta página nem o link do proxy: quem tiver o link do "
        "proxy usa o IP da loja até o fim do prazo (no DaVinci dá para cortar antes).</small></p>"
        "<script>"
        "document.querySelectorAll('[data-copiar]').forEach(function(b){"
        "b.addEventListener('click',function(){"
        "var t=document.getElementById(b.getAttribute('data-copiar'));"
        "t.select();t.setSelectionRange(0,99999);"
        "var feito=function(){b.textContent='Copiado ✓';};"
        "if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(feito,"
        "function(){document.execCommand('copy');feito();});}"
        "else{document.execCommand('copy');feito();}"
        "});});"
        "</script>"
    )
