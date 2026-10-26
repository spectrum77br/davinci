"""Zap (WhatsApp Cloud API) — o HTTP com a Meta (Onda 1: os GETs do cadastro; Onda 2: o envio).

Desenho: `docs/zap/desenho.md` §3.1, §6.4 e §10.1.

  • `inspecionar_token` — ESTRITA (a `meta_client.validade_do_token` é "melhor
    esforço" e confunde "nunca vence" com "não consegui ver"): exige
    `is_valid`, o `app_id` do NOSSO app, os dois escopos do WhatsApp e a WABA
    dentro de `granular_scopes[].target_ids`. `expires_at = 0` vira
    `nunca_vence=True` de forma explícita. Falha na inspeção é ERRO
    (`ZapApiErro`), nunca "sem validade".
  • `ler_waba` (`GET /{waba}`) e `listar_numeros` (`GET /{waba}/phone_numbers`).
  • Onda 2 (26/10/2026): `enviar_mensagem` (`POST /{pnid}/messages`) e
    `marcar_lida` (o mesmo POST com `status: "read"`). Quem decide se pode
    mandar é o `enviar._destino_zap` (as travas) e o `zap.enviar_texto` (o
    cinto); aqui só o HTTP. Timeout, conexão cortada e 5xx/3xx SEM código
    viram `ZapApiErro` sem `code` — o adaptador trata como AMBÍGUO (pode ter
    saído: `revisar`, nunca retentado).

Do `marketing/meta_client.py` vem só o que não muda frase nem versão:
`GRAPH_HOST`, `_eh_ambiguo`, `_redigir` e `_headers` (token no HEADER, nunca
na URL). A versão da Graph é a PRÓPRIA do Zap (`meta_whatsapp_graph_version`):
subir a do Instagram não muda os payloads do WhatsApp. Os dicionários de
erro do `meta_client` NÃO são usados (mexer neles muda as frases do
Instagram): a tradução é a da tabela única `zap_erros` (Onda 2), lida pelo
`_erro_zap` deste módulo.

O `debug_token` exige o token inspecionado em `?input_token=` na URL: por
isso esta URL nunca vai para log e `_redigir_zap` mascara `input_token=`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.services.atendimento import zap_erros
from app.services.marketing.meta_client import GRAPH_HOST, _eh_ambiguo, _headers, _redigir

logger = structlog.get_logger()

__all__ = [
    "ESCOPOS_OBRIGATORIOS",
    "InspecaoToken",
    "ZapApiErro",
    "enviar_mensagem",
    "graph_version",
    "inspecionar_token",
    "ler_waba",
    "listar_numeros",
    "marcar_lida",
    "novo_cliente",
    "eh_ambiguo",
]

ESCOPO_MENSAGENS = "whatsapp_business_messaging"
ESCOPO_GESTAO = "whatsapp_business_management"
ESCOPOS_OBRIGATORIOS = (ESCOPO_MENSAGENS, ESCOPO_GESTAO)

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# Páginas de `phone_numbers` (25 por página; uma WABA real tem poucos números).
_MAX_PAGINAS = 20

_RE_INPUT_TOKEN = re.compile(r"(input_token=)[^&\s\"']+", re.IGNORECASE)

class ZapApiErro(Exception):  # noqa: N818 — português, como `ErroDeMidia`
    """Erro da Graph API (ou da conferência estrita), já em português e SEM token.

    `http_status` (Onda 2): o status da resposta (None = nem houve resposta:
    timeout, conexão cortada). Com `code` da Meta, a recusa é definitiva; sem
    código, só um 4xx diz que a Meta não aceitou — o resto é ambíguo.
    """

    def __init__(
        self,
        mensagem: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        http_status: int | None = None,
    ):
        self.code = code
        self.subcode = subcode
        self.http_status = http_status
        super().__init__(mensagem)


def eh_ambiguo(exc: BaseException) -> bool:
    """Timeout, conexão cortada ou erro SEM código: não dá para dizer que a Meta recusou.

    Onda 2: o 4xx sem código (corpo que não é o JSON de erro da Meta) é
    recusa — o pedido não foi aceito; o 5xx/3xx sem código e os códigos de
    "erro desconhecido" da tabela (`zap_erros`, classe `ambiguo`) não.
    """
    if isinstance(exc, ZapApiErro):
        if exc.code is not None:
            return zap_erros.traduzir(exc.code).ambiguo
        if exc.subcode is not None:
            return False
        return not (exc.http_status is not None and 400 <= exc.http_status < 500)
    return _eh_ambiguo(exc)


def graph_version() -> str:
    """A versão da Graph do ZAP (`meta_whatsapp_graph_version`), lida na hora da chamada."""
    return get_settings().meta_whatsapp_graph_version


def _url(caminho: str) -> str:
    return f"{GRAPH_HOST}/{graph_version()}/{caminho.lstrip('/')}"


def novo_cliente() -> httpx.AsyncClient:
    """O cliente HTTP (os testes trocam por um de mentira: nenhum teste fala com a Meta).

    `follow_redirects=False`: o Bearer nunca segue sozinho para outro host.
    """
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


def _redigir_zap(texto: str | None, token: str | None = None) -> str:
    """O `_redigir` do meta_client + o `input_token=` do `debug_token`."""
    return _RE_INPUT_TOKEN.sub(r"\1***", _redigir(texto, token))


def _inteiro(valor: object) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def _erro_zap(resp: httpx.Response, token: str) -> ZapApiErro:
    """O corpo de erro da Meta → `ZapApiErro` em português (lê `code`/`error_subcode`)."""
    try:
        corpo: Any = resp.json()
    except ValueError:
        corpo = None
    erro = corpo.get("error") if isinstance(corpo, dict) else None
    erro = erro if isinstance(erro, dict) else {}
    code = _inteiro(erro.get("code"))
    subcode = _inteiro(erro.get("error_subcode"))
    # A tabela única (`zap_erros`, §6.5). Código fora dela: a frase da Meta,
    # curta (sem dado do cliente: a resposta do POST não ecoa o texto).
    traduzido = zap_erros.traduzir(code) if code is not None else None
    if traduzido is not None and not traduzido.frase.startswith(zap_erros.FRASE_DESCONHECIDO):
        frase = traduzido.frase.rstrip(".")
        frase = frase[:1].lower() + frase[1:]
    else:
        bruta = str(erro.get("error_user_msg") or erro.get("message") or "")[:200]
        frase = f"a Meta recusou: {bruta}" if bruta else "a Meta recusou o pedido"
    marcas = [f"code {code}"] if code is not None else []
    if subcode is not None:
        marcas.append(f"subcode {subcode}")
    if not marcas:
        marcas.append(f"HTTP {resp.status_code}")
    return ZapApiErro(
        _redigir_zap(f"{frase} ({', '.join(marcas)})", token),
        code=code,
        subcode=subcode,
        http_status=resp.status_code,
    )


def _json(resp: httpx.Response, token: str) -> dict[str, Any]:
    """O corpo como dict — ou `ZapApiErro` (a Meta às vezes manda 200 com `error`)."""
    if resp.status_code >= 400:
        raise _erro_zap(resp, token)
    try:
        dados = resp.json()
    except ValueError:
        raise ZapApiErro(
            f"a Meta respondeu algo que não é JSON (HTTP {resp.status_code})",
            http_status=resp.status_code,
        ) from None
    if not isinstance(dados, dict):
        raise ZapApiErro("a Meta respondeu num formato inesperado", http_status=resp.status_code)
    if dados.get("error"):
        raise _erro_zap(resp, token)
    return dados


async def _get(
    caminho: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET na Graph com o token no header. Nunca loga a URL nem o token."""
    proprio = cliente is None
    http = cliente or novo_cliente()
    try:
        resp = await http.get(_url(caminho), params=params or {}, headers=_headers(token))
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        # Só o tipo: a mensagem do httpx traz a URL (e a do debug_token traz
        # o token no `input_token`).
        raise ZapApiErro(f"a Meta não respondeu ({type(exc).__name__})") from None
    finally:
        if proprio:
            await http.aclose()
    if 300 <= resp.status_code < 400:
        # Redirecionamento na Graph não é esperado: o Bearer não segue.
        raise ZapApiErro(
            f"a Meta redirecionou o pedido (HTTP {resp.status_code})",
            http_status=resp.status_code,
        )
    return _json(resp, token)


async def _post(
    caminho: str,
    token: str,
    *,
    corpo: dict[str, Any],
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST na Graph com o token no header e o corpo em JSON. Nunca loga URL, token nem corpo.

    Sem retentativa aqui (nem no adaptador): mensagem para cliente não se
    desenvia, e um POST que pode ter chegado à Meta nunca vai de novo sozinho.
    """
    proprio = cliente is None
    http = cliente or novo_cliente()
    try:
        resp = await http.post(_url(caminho), json=corpo, headers=_headers(token))
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ZapApiErro(f"a Meta não respondeu ({type(exc).__name__})") from None
    finally:
        if proprio:
            await http.aclose()
    if 300 <= resp.status_code < 400:
        raise ZapApiErro(
            f"a Meta redirecionou o pedido (HTTP {resp.status_code})",
            http_status=resp.status_code,
        )
    return _json(resp, token)


# ── debug_token (estrita) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class InspecaoToken:
    """O que a inspeção estrita confirmou (sem o token)."""

    app_id: str
    nunca_vence: bool
    expira_em: datetime | None
    escopos: tuple[str, ...]
    system_user_id: str | None = None
    target_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)


async def inspecionar_token(
    token: str,
    *,
    app_id: str,
    waba_id: str,
    cliente: httpx.AsyncClient | None = None,
) -> InspecaoToken:
    """`GET /debug_token` ESTRITO → `InspecaoToken`; qualquer falta → `ZapApiErro`.

    Recusa: `is_valid` falso; `app_id` diferente do app; faltando um dos
    escopos (`whatsapp_business_messaging`, `whatsapp_business_management`);
    a WABA fora dos `granular_scopes[].target_ids` desses escopos; validade
    ilegível. A Meta que não responde também é erro: no cadastro, nada é
    gravado; no cron diário (Onda 4), vira alerta.
    """
    if not token:
        raise ZapApiErro("token vazio")
    try:
        corpo = await _get("debug_token", token, params={"input_token": token}, cliente=cliente)
    except ZapApiErro as exc:
        raise ZapApiErro(
            _redigir_zap(f"não deu para conferir o token na Meta: {exc}", token),
            code=exc.code,
            subcode=exc.subcode,
        ) from None
    dados = corpo.get("data")
    if not isinstance(dados, dict):
        raise ZapApiErro("a Meta não devolveu os dados do token")
    if dados.get("is_valid") is not True:
        raise ZapApiErro("o token não é válido (vencido, revogado ou de outro app)")
    if str(dados.get("app_id") or "") != str(app_id):
        raise ZapApiErro("o token é de OUTRO app da Meta, não do app cadastrado")
    escopos = tuple(str(e) for e in (dados.get("scopes") or []) if isinstance(e, str))
    faltam = [e for e in ESCOPOS_OBRIGATORIOS if e not in escopos]
    if faltam:
        raise ZapApiErro(f"o token não tem o(s) escopo(s): {', '.join(faltam)}")
    alvos: dict[str, tuple[str, ...]] = {}
    for g in dados.get("granular_scopes") or []:
        if isinstance(g, dict) and isinstance(g.get("scope"), str):
            alvos[g["scope"]] = tuple(str(t) for t in (g.get("target_ids") or []))
    sem_a_waba = [e for e in ESCOPOS_OBRIGATORIOS if str(waba_id) not in alvos.get(e, ())]
    if sem_a_waba:
        raise ZapApiErro(
            "o token não enxerga esta conta do WhatsApp (a WABA não está nas "
            f"permissões de {', '.join(sem_a_waba)}): atribua a WABA ao usuário do sistema"
        )
    expira = _inteiro(dados.get("expires_at"))
    if expira is None:
        raise ZapApiErro("a Meta não disse quando o token vence")
    return InspecaoToken(
        app_id=str(dados.get("app_id")),
        nunca_vence=expira == 0,
        expira_em=None if expira == 0 else datetime.fromtimestamp(expira, tz=UTC),
        escopos=escopos,
        system_user_id=str(dados.get("user_id")) if dados.get("user_id") else None,
        target_ids=alvos,
    )


# ── WABA e números ─────────────────────────────────────────────────────────


async def ler_waba(
    waba_id: str, token: str, *, cliente: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """`GET /{waba}` (id e nome) — confere que o token enxerga a conta."""
    return await _get(str(waba_id), token, params={"fields": "id,name"}, cliente=cliente)


_CAMPOS_NUMERO = "id,display_phone_number,verified_name,name_status,quality_rating"


async def listar_numeros(
    waba_id: str, token: str, *, cliente: httpx.AsyncClient | None = None
) -> list[dict[str, Any]]:
    """`GET /{waba}/phone_numbers`, todas as páginas (pelo cursor `after`)."""
    proprio = cliente is None
    http = cliente or novo_cliente()
    numeros: list[dict[str, Any]] = []
    try:
        params: dict[str, Any] = {"fields": _CAMPOS_NUMERO, "limit": 25}
        for _ in range(_MAX_PAGINAS):
            corpo = await _get(f"{waba_id}/phone_numbers", token, params=params, cliente=http)
            numeros.extend(n for n in corpo.get("data") or [] if isinstance(n, dict))
            paging = corpo.get("paging") if isinstance(corpo.get("paging"), dict) else {}
            depois = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
            if not depois:
                break
            params = {**params, "after": depois}
    finally:
        if proprio:
            await http.aclose()
    return numeros


# ── Envio (Onda 2) ─────────────────────────────────────────────────────────


async def enviar_mensagem(
    phone_number_id: str,
    token: str,
    corpo: dict[str, Any],
    *,
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """`POST /{pnid}/messages` → o corpo da Meta (`{messages: [{id, message_status?}]}`).

    O token vai no HEADER (`meta_client._headers`, o mesmo das chamadas do
    cadastro) [confirmar com o ensaio do §11.5 que a Cloud API aceita o
    esquema `OAuth` do header como aceita o `Bearer` da documentação].
    """
    return await _post(f"{phone_number_id}/messages", token, corpo=corpo, cliente=cliente)


async def marcar_lida(
    phone_number_id: str,
    token: str,
    wamid: str,
    *,
    cliente: httpx.AsyncClient | None = None,
) -> bool:
    """O tique azul: `POST /{pnid}/messages {status: "read", message_id}` (§6.7).

    Marca a mensagem DE ENTRADA `wamid` (e as anteriores) como lida no
    celular do cliente. A Meta só aceita até 30 dias depois de receber.
    """
    corpo = {"messaging_product": "whatsapp", "status": "read", "message_id": wamid}
    dados = await _post(f"{phone_number_id}/messages", token, corpo=corpo, cliente=cliente)
    return dados.get("success") is True
