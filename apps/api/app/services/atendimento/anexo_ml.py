"""O anexo que o comprador manda no Mercado Livre, MOSTRADO na conversa (28/10/2026).

O pedido do Eduardo: a foto que o comprador manda no pós-venda do ML tem de
APARECER na conversa, como no Duoke — até aqui só o nome do arquivo
("Screenshot_20261028_110935_Gallery.jpeg"). A leitura (`ml._anexos`,
`reclamacoes._anexos`) guarda o `arquivo` (o id do anexo no ML), o nome, o
tipo e o tamanho — nunca uma URL, porque a do marketplace expira. Esta peça
busca o arquivo NA HORA, pela API oficial, com o token da PRÓPRIA loja da
conversa (o mesmo que lê e responde; nada de AdsPower nem navegador), e a
rota `GET /api/atendimento/conversas/{id}/anexos/{arquivo}` entrega os bytes
para o `<img>` (imagem) ou para o link de baixar (PDF e o resto).

Os endereços (doc oficial, consultada em 28/10/2026):

  pós-venda    GET /messages/attachments/{id}?tag=post_sale&site_id=MLB
               ("Obtenha os anexos enviados" — "a chamada retornará o
               arquivo solicitado"; 422 = o id não existe ou não é da conta).
               https://developers.mercadolivre.com.br/pt_br/mensagens-post-venda
  reclamação   GET /post-purchase/v1/claims/{claim_id}/attachments/{id}/download
               ("Baixar o arquivo"; o `claim_id` é o `externo_id` da conversa
               `reclamacao`).
               https://developers.mercadolivre.com.br/pt_br/gerenciar-mensagem-de-uma-eclamacao

Os cuidados:

- SÓ O ANEXO DA CONVERSA: o `arquivo` tem de estar no `anexos` de uma
  mensagem DAQUELA conversa (`anexo_da_conversa`, JSONB `@>`) — nunca um id
  qualquer que alguém digite na URL. O formato é conferido antes (só
  `[A-Za-z0-9_.-]`, sem `..`), e ainda vai codificado no caminho.
- O TOKEN DA LOJA DA CONVERSA (`clientes.cliente_da_integracao`, com a
  renovação blindada de sempre: vencido antes, 401 uma vez). O Bearer só vai
  para `api.mercadolibre.com` em https; se o ML redirecionar (até
  `MAX_SALTOS`), o salto para outro host vai SEM o token.
- TETO: 25 MB (o do ML) pelo tamanho da leitura, pelo `Content-Length` e
  DURANTE o download (para no byte que passa). Tempo: `TEMPO_HTTP` por ida e
  `TEMPO_TOTAL_S` no todo.
- O TIPO: o `Content-Type` do ML quando é um da lista (`TIPOS_ACEITOS`:
  JPEG, PNG, GIF, WebP, HEIC/HEIF e PDF — os que o ML aceita); genérico ou
  fora da lista → o `tipo` que a leitura guardou, a assinatura dos bytes, e
  por último `application/octet-stream`. Nunca SVG nem HTML. `inline` só a
  imagem que o navegador mostra (`TIPOS_INLINE`); o resto sai `attachment`
  (baixa, nunca abre aqui). A rota põe `nosniff` e `sandbox`.
- CACHE: só o do navegador (`Cache-Control: private`, `CACHE_S`) — o id do
  anexo não muda de conteúdo. Nada do comprador fica no Redis nem no disco.
- LOG: só o id da conversa, o código e o HTTP. Nunca o token, o nome, o id do
  anexo (começa pelo id da conta do comprador) nem a URL.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento.constantes import CANAL_POS_VENDA, CANAL_RECLAMACAO, ORIGEM_NOTA

logger = structlog.get_logger()

PLATAFORMA = "ml"
CANAIS = frozenset({CANAL_POS_VENDA, CANAL_RECLAMACAO})
SITE = "MLB"
ML_HOST = "api.mercadolibre.com"
ML_API_BASE = f"https://{ML_HOST}"
# O teto do ML para anexo de mensagem (doc: "tamanho máximo de 25 MB").
MAX_BYTES = 25 * 1024 * 1024
MAX_SALTOS = 3
TEMPO_HTTP = httpx.Timeout(20.0, connect=5.0)
TEMPO_TOTAL_S = 45.0
_PEDACO = 64 * 1024
# A rota: o navegador guarda por 15 min, só para quem pediu (`private`).
CACHE_S = 900

# O `arquivo` do ML: "118859700_3dba482b-....jpeg" (pós-venda) ou
# "3cf94d52-....jpeg" (reclamação). Nada de barra, espaço, `%` ou `..`.
_RE_ARQUIVO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,199}$")

TIPOS_INLINE = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
TIPOS_ACEITOS = TIPOS_INLINE | frozenset({"image/heic", "image/heif", "application/pdf"})
_SINONIMOS = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
GENERICO = "application/octet-stream"

# Códigos estáveis (a rota devolve; a tela só cai no nome).
NAO_ENCONTRADO = "anexo_nao_encontrado"
INDISPONIVEL = "anexo_indisponivel"
GRANDE_DEMAIS = "anexo_grande_demais"
SEM_RESPOSTA = "anexo_sem_resposta"


class AnexoIndisponivel(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """O ML não entregou o anexo. `code` estável; `http` = o que a rota responde."""

    def __init__(self, code: str, http: int, ml_http: int | None = None) -> None:
        self.code = code
        self.http = http
        self.ml_http = ml_http
        super().__init__(code)


@dataclass(frozen=True)
class Baixado:
    conteudo: bytes
    mime: str

    @property
    def inline(self) -> bool:
        return self.mime in TIPOS_INLINE


def arquivo_valido(arquivo: str | None) -> bool:
    """O `arquivo` tem a cara de um id de anexo do ML? PURA."""
    a = arquivo or ""
    return bool(_RE_ARQUIVO.match(a)) and ".." not in a


def conversa_aceita(conversa: AtendimentoConversa) -> bool:
    """Pós-venda ou reclamação do ML (as caixas que têm anexo pela API)."""
    return conversa.plataforma == PLATAFORMA and conversa.canal in CANAIS


def caminho_no_ml(conversa: AtendimentoConversa, arquivo: str) -> tuple[str, dict | None]:
    """(caminho, params) do GET do arquivo na API do ML. Levanta `AnexoIndisponivel`."""
    if not conversa_aceita(conversa) or not arquivo_valido(arquivo):
        raise AnexoIndisponivel(NAO_ENCONTRADO, 404)
    seguro = quote(arquivo, safe="")
    if conversa.canal == CANAL_RECLAMACAO:
        claim_id = str(conversa.externo_id or "").strip()
        if not claim_id.isdigit():
            raise AnexoIndisponivel(NAO_ENCONTRADO, 404)
        return f"/post-purchase/v1/claims/{claim_id}/attachments/{seguro}/download", None
    return f"/messages/attachments/{seguro}", {"tag": "post_sale", "site_id": SITE}


async def anexo_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, arquivo: str
) -> dict | None:
    """O anexo `arquivo` numa mensagem DESTA conversa (sem nota interna); None se não há."""
    if not arquivo_valido(arquivo):
        return None
    linhas = (
        await session.execute(
            select(AtendimentoMensagem.anexos)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.origem != ORIGEM_NOTA,
                AtendimentoMensagem.anexos.contains([{"arquivo": arquivo}]),
            )
            .limit(1)
        )
    ).scalars()
    for anexos in linhas:
        for a in anexos or []:
            if isinstance(a, dict) and a.get("arquivo") == arquivo:
                return a
    return None


def _normalizar(mime: Any) -> str:
    bruto = str(mime or "").split(";", 1)[0].strip().lower()
    return _SINONIMOS.get(bruto, bruto)


def tipo_pela_assinatura(dados: bytes) -> str | None:
    """O tipo pelos primeiros bytes (só os da lista). PURA."""
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if dados.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    if dados.startswith(b"%PDF-"):
        return "application/pdf"
    if dados[4:8] == b"ftyp" and dados[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "image/heic"
    return None


def tipo_da_resposta(declarado: Any, guardado: Any, dados: bytes) -> str:
    """O `Content-Type` da rota: o do ML (da lista), o guardado, a assinatura. PURA."""
    for candidato in (_normalizar(declarado), _normalizar(guardado)):
        if candidato in TIPOS_ACEITOS:
            return candidato
    return tipo_pela_assinatura(dados) or GENERICO


def _http() -> httpx.AsyncClient:
    """O cliente HTTP do download (o teste troca por um transporte falso)."""
    return httpx.AsyncClient(timeout=TEMPO_HTTP, follow_redirects=False)


def _do_ml(url: httpx.URL) -> bool:
    """O Bearer pode ir para este endereço? Só https em api.mercadolibre.com. PURA."""
    return (
        url.scheme == "https"
        and not url.userinfo
        and (url.host or "").lower().rstrip(".") == ML_HOST
        and url.port in (None, 443)
    )


async def _uma_ida(
    http: httpx.AsyncClient, caminho: str, params: dict | None, token: str | None
) -> tuple[int, str, bytes]:
    """GET com os saltos conferidos → (status, content-type, corpo até o teto)."""
    atual = httpx.URL(f"{ML_API_BASE}{caminho}", params=params or {})
    for _ in range(MAX_SALTOS + 1):
        if atual.scheme != "https":
            raise AnexoIndisponivel(INDISPONIVEL, 502)
        headers = {"Accept": "*/*"}
        if token and _do_ml(atual):
            headers["Authorization"] = f"Bearer {token}"
        async with http.stream("GET", atual, headers=headers) as resp:
            if resp.is_redirect and "location" in resp.headers:
                atual = resp.url.join(resp.headers["location"])
                continue
            if resp.status_code >= 400:
                return resp.status_code, "", b""
            declarado = resp.headers.get("content-length", "")
            if declarado.isdigit() and int(declarado) > MAX_BYTES:
                raise AnexoIndisponivel(GRANDE_DEMAIS, 502)
            pedacos: list[bytes] = []
            n = 0
            async for pedaco in resp.aiter_bytes(_PEDACO):
                n += len(pedaco)
                if n > MAX_BYTES:
                    raise AnexoIndisponivel(GRANDE_DEMAIS, 502)
                pedacos.append(pedaco)
            return resp.status_code, resp.headers.get("content-type", ""), b"".join(pedacos)
    raise AnexoIndisponivel(INDISPONIVEL, 502)


async def _baixar(cliente: Any, caminho: str, params: dict | None) -> tuple[str, bytes]:
    if cliente._expired():  # noqa: SLF001 — o mesmo critério do cliente
        await cliente.refresh()
    async with _http() as http:
        for tentativa in range(2):
            status, tipo, corpo = await _uma_ida(http, caminho, params, cliente.access_token)
            if status == 401 and tentativa == 0:
                await cliente.refresh()
                continue
            if status in (404, 410, 422):
                raise AnexoIndisponivel(NAO_ENCONTRADO, 404, status)
            if status >= 400:
                raise AnexoIndisponivel(INDISPONIVEL, 502, status)
            if _normalizar(tipo) == "application/json":
                # O ML respondeu um JSON (erro ou metadado), não o arquivo:
                # nenhum anexo do ML é JSON (a doc só aceita foto e PDF).
                raise AnexoIndisponivel(INDISPONIVEL, 502, status)
            return tipo, corpo
    raise AnexoIndisponivel(INDISPONIVEL, 502, 401)


async def baixar(
    cliente: Any, conversa: AtendimentoConversa, arquivo: str, anexo: dict | None = None
) -> Baixado:
    """Os bytes do anexo `arquivo` da conversa, pela API do ML com o token da loja.

    Quem chama já conferiu que o `arquivo` é desta conversa
    (`anexo_da_conversa`). Levanta `AnexoIndisponivel`; o log só leva o id da
    conversa e os códigos.
    """
    caminho, params = caminho_no_ml(conversa, arquivo)
    guardado = anexo or {}
    tamanho = guardado.get("tamanho")
    if isinstance(tamanho, int) and tamanho > MAX_BYTES:
        raise AnexoIndisponivel(GRANDE_DEMAIS, 502)
    conversa_id = str(conversa.id)
    try:
        tipo, corpo = await asyncio.wait_for(
            _baixar(cliente, caminho, params), timeout=TEMPO_TOTAL_S
        )
    except AnexoIndisponivel as e:
        logger.info(
            "atendimento_anexo_ml_indisponivel",
            conversa_id=conversa_id,
            code=e.code,
            ml_http=e.ml_http,
        )
        raise
    except (TimeoutError, httpx.TimeoutException):
        logger.info("atendimento_anexo_ml_indisponivel", conversa_id=conversa_id, code=SEM_RESPOSTA)
        raise AnexoIndisponivel(SEM_RESPOSTA, 504) from None
    except (httpx.HTTPError, httpx.InvalidURL, RuntimeError) as e:
        # Só o tipo: a mensagem do httpx traz a URL; a do refresh, o corpo do ML.
        logger.info(
            "atendimento_anexo_ml_indisponivel",
            conversa_id=conversa_id,
            code=INDISPONIVEL,
            err=type(e).__name__,
        )
        raise AnexoIndisponivel(INDISPONIVEL, 502) from None
    if not corpo:
        raise AnexoIndisponivel(INDISPONIVEL, 502)
    return Baixado(conteudo=corpo, mime=tipo_da_resposta(tipo, guardado.get("tipo"), corpo))


def nome_para_baixar(anexo: dict | None, arquivo: str) -> str:
    """O nome do `Content-Disposition`: o original do comprador (sem caminho), ou o id."""
    bruto = str((anexo or {}).get("nome") or "").replace("\\", "/").rsplit("/", 1)[-1]
    limpo = "".join(c for c in bruto if c.isprintable()).strip(" .")
    return (limpo or arquivo)[:150]


def cabecalhos(baixado: Baixado, anexo: dict | None, arquivo: str) -> dict[str, str]:
    """Os cabeçalhos da rota: os mesmos cuidados da mídia do Zap (`zap_midia`).

    `inline` só a imagem da lista; o resto `attachment` com o nome SEGURO
    (sem CR/LF/aspas cru). `nosniff` e `sandbox`: mesmo aberto direto numa
    aba, nada roda. `private`: nunca num cache compartilhado.
    """
    from app.services.atendimento.zap_midia import content_disposition

    disposicao = "inline" if baixado.inline else "attachment"
    return {
        "Content-Disposition": content_disposition(disposicao, nome_para_baixar(anexo, arquivo)),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox" if baixado.inline else "sandbox; default-src 'none'",
        "Cache-Control": f"private, max-age={CACHE_S}",
    }
