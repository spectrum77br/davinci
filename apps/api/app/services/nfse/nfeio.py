"""Conversa com a API da NFE.io — o motor da NFS-e desde 29/09/2026.

A NFE.io monta a DPS/RPS, assina com o certificado da empresa, numera, fala
com a prefeitura (ou o Emissor Nacional) e guarda o PDF/XML. Aqui:

- `Authorization: <chave>` (sem "Bearer"). A chave emite por TODAS as empresas
  da conta: sai só de `settings.nfeio_api_key` e nunca vai pra URL, log, banco
  ou resposta. Nos redirects de PDF/XML (URL assinada) a chave NÃO vai junto.
- GET repete sozinho em 429/5xx/sem resposta. POST e DELETE NUNCA repetem:
  emissão não é idempotente e, depois de um timeout, repetir é tentar emitir a
  mesma nota duas vezes. Na dúvida quem decide é o GET pelo `externalId`.
- Notas pela v3 (`federalTaxNumber` como texto, aceita CNPJ alfanumérico);
  empresas pela v1 (`/v1/companies`) e Inscrições Municipais pela
  `api.nfse.io/v2`. Na v1 o CNPJ vem como NÚMERO (sem o zero da frente).
- `TRANSPORTE`: gancho dos testes (httpx falso no lugar da NFE.io).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.nfse.erros import NfseError

TRANSPORTE: Any = None

# A NFE.io corta a requisição em 60 s (408): esperar um pouco mais que isso.
TIMEOUT = httpx.Timeout(connect=15.0, read=75.0, write=30.0, pool=10.0)
REPETICOES_GET = 2
# Medido na conta (30/09): pageCount acima de 50 dá 400 "pageCount must be
# between 1 and 50".
POR_PAGINA_MAX = 50
ESPERA_GET = 1.0  # segundos, dobra a cada tentativa

APP = "https://app.nfe.io/companies/{id}"
_ID = re.compile(r"^(?:[0-9a-f]{24}|[0-9a-f]{32})$")
_ID_NO_LINK = re.compile(r"companies/([0-9a-fA-F]{24}(?:[0-9a-fA-F]{8})?)(?![0-9a-fA-F])")

CHAVE_AUSENTE = "A chave da NFE.io não está configurada no servidor (NFEIO_API_KEY)."
CHAVE_RECUSADA = "A NFE.io recusou a chave de acesso do servidor (NFEIO_API_KEY inválida)."


def chave_configurada() -> bool:
    return bool((get_settings().nfeio_api_key or "").strip())


def normalizar_id(ref: str | None) -> str | None:
    """Id da empresa na NFE.io: 24 hex (a maioria) ou 32 hex (criadas pela API
    v2, ex.: SL e Rodrigues), puro ou dentro do link colado do painel
    (`https://app.nfe.io/companies/<id>`). None se não parecer um id."""
    txt = (ref or "").strip()
    if not txt:
        return None
    m = _ID_NO_LINK.search(txt)
    if m:
        return m.group(1).lower()
    txt = txt.lower()
    return txt if _ID.fullmatch(txt) else None


def link(company_id: str) -> str:
    return APP.format(id=company_id)


def _campo(d: Mapping[str, Any], *nomes: str) -> str:
    for nome in nomes:
        for v in (nome, nome[:1].upper() + nome[1:]):
            if v in d and d[v] not in (None, ""):
                return str(d[v])
    return ""


def mensagens(corpo: Any) -> list[dict]:
    """Achata os formatos de erro da NFE.io:
    `{"message": ...}` / `{"code": 40001, "message": ...}`;
    `{"errors": [{"code", "message"}]}`;
    `{"title": ..., "errors": {"$.campo": ["..."]}}` (ProblemDetails);
    texto puro em inglês (`company is not active`) — guardado como {"texto"}."""
    out: list[dict] = []
    if isinstance(corpo, str):
        corpo = {"texto": corpo}
    if isinstance(corpo, list):
        corpo = {"errors": corpo}
    if not isinstance(corpo, Mapping):
        return out
    erros = corpo.get("errors") if "errors" in corpo else corpo.get("Errors")
    if isinstance(erros, list):
        for it in erros:
            if isinstance(it, str):
                out.append({"codigo": "", "descricao": it, "complemento": ""})
            elif isinstance(it, Mapping):
                m = {
                    "codigo": _campo(it, "code", "codigo"),
                    "descricao": _campo(it, "message", "mensagem", "descricao"),
                    "complemento": _campo(it, "field", "detail"),
                }
                if any(m.values()):
                    out.append(m)
    elif isinstance(erros, Mapping):
        for campo, v in erros.items():
            textos = v if isinstance(v, list) else [v]
            for t in textos:
                if t:
                    out.append({"codigo": "", "descricao": str(t), "complemento": str(campo)})
    if not out:
        texto = _campo(corpo, "message", "mensagem", "title", "texto", "detail")
        if texto:
            out.append(
                {"codigo": _campo(corpo, "code", "codigo"), "descricao": texto, "complemento": ""}
            )
    return out


def texto_do_erro(r: Resposta) -> str:
    return " ".join(m["descricao"] for m in r.msgs if m.get("descricao")).strip()


@dataclass
class Resposta:
    operacao: str
    status: int | None  # None = não houve resposta (rede/timeout)
    corpo: Any = None
    erro_rede: str | None = None
    duracao_ms: int = 0
    msgs: list[dict] = field(default_factory=list)
    location: str | None = None
    # Corpo cru e tipo (PDF/XML). Nunca vai pra log nem pro banco como está.
    bruto: bytes = field(default=b"", repr=False)
    tipo: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def codigos(self) -> list[str]:
        return [m["codigo"] for m in self.msgs if m.get("codigo")]

    @property
    def incerta(self) -> bool:
        """Sem resposta, 408 ou 5xx: pode ter chegado lá (POST) ou não."""
        return self.status is None or self.status == 408 or self.status >= 500


@dataclass
class Arquivo:
    """PDF/XML baixado. `conteudo` None = ainda não existe (404) ou falhou."""

    resposta: Resposta
    conteudo: bytes | None = None
    tipo: str | None = None


def _pagina(corpo: Any, chave: str) -> list[dict]:
    if isinstance(corpo, Mapping):
        itens = corpo.get(chave)
        if isinstance(itens, list):
            return [x for x in itens if isinstance(x, Mapping)]
    return []


class ClienteNfeio:
    """Use com `async with`. Sem chave configurada: NfseError 503 `chave_nfeio`."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        s = get_settings()
        chave = (s.nfeio_api_key or "").strip()
        if not chave:
            raise NfseError(503, "chave_nfeio", CHAVE_AUSENTE)
        self.base = (s.nfeio_base_url or "https://api.nfe.io").rstrip("/")
        self.base_nfse = (s.nfeio_nfse_base_url or "https://api.nfse.io").rstrip("/")
        kwargs: dict[str, Any] = {
            "timeout": TIMEOUT,
            "headers": {"Authorization": chave, "Accept": "application/json"},
            # Redirect de PDF/XML é seguido à mão, sem a chave (`_baixar`).
            "follow_redirects": False,
        }
        t = transport if transport is not None else TRANSPORTE
        if t is not None:
            kwargs["transport"] = t
        self._http = httpx.AsyncClient(**kwargs)
        self._t = t

    async def __aenter__(self) -> ClienteNfeio:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    # --- núcleo -------------------------------------------------------------

    async def _uma(self, operacao: str, metodo: str, url: str, **kw: Any) -> Resposta:
        inicio = time.monotonic()
        try:
            r = await self._http.request(metodo, url, **kw)
        except httpx.TimeoutException as e:
            return Resposta(
                operacao,
                None,
                erro_rede=f"tempo esgotado ({type(e).__name__})",
                duracao_ms=int((time.monotonic() - inicio) * 1000),
            )
        except httpx.HTTPError as e:
            # Só o tipo: a mensagem do httpx pode trazer a URL, nunca a chave,
            # mas não custa nada não arriscar.
            return Resposta(
                operacao,
                None,
                erro_rede=f"sem conexão com a NFE.io ({type(e).__name__})",
                duracao_ms=int((time.monotonic() - inicio) * 1000),
            )
        ms = int((time.monotonic() - inicio) * 1000)
        tipo = r.headers.get("content-type") or ""
        corpo: Any = None
        if r.content and ("json" in tipo or "text" in tipo or not tipo):
            try:
                corpo = r.json()
            except ValueError:
                corpo = {"texto": r.text[:2000]}
        return Resposta(
            operacao,
            r.status_code,
            corpo=corpo,
            duracao_ms=ms,
            msgs=mensagens(corpo) if r.status_code >= 400 else [],
            location=r.headers.get("location"),
            bruto=r.content,
            tipo=tipo or None,
        )

    async def _get(self, operacao: str, url: str, **kw: Any) -> Resposta:
        espera = ESPERA_GET
        r = await self._uma(operacao, "GET", url, **kw)
        for _ in range(REPETICOES_GET):
            if not (r.status is None or r.status == 429 or r.status >= 500):
                break
            await asyncio.sleep(espera)
            espera *= 2
            r = await self._uma(operacao, "GET", url, **kw)
        return r

    def _si(self, cid: str, resto: str = "") -> str:
        return f"{self.base}/v3/companies/{quote(cid, safe='')}/serviceinvoices{resto}"

    # --- empresas (só leitura) ---------------------------------------------

    async def listar_empresas(self) -> tuple[Resposta, list[dict]]:
        """Todas as empresas da conta (v1). Pagina até a página vir incompleta."""
        todas: list[dict] = []
        r = Resposta("listar_empresas", None)
        for pagina in range(1, 21):
            r = await self._get(
                "listar_empresas",
                f"{self.base}/v1/companies",
                params={"pageCount": POR_PAGINA_MAX, "pageIndex": pagina},
            )
            if not r.ok:
                return r, todas
            itens = _pagina(r.corpo, "companies")
            todas.extend(itens)
            if len(itens) < POR_PAGINA_MAX:
                break
        return r, todas

    async def empresa(self, id_ou_cnpj: str) -> Resposta:
        """GET /v1/companies/{id ou CNPJ}. `corpo["companies"]` é a empresa."""
        return await self._get("empresa", f"{self.base}/v1/companies/{quote(id_ou_cnpj, safe='')}")

    async def inscricoes_municipais(self, cid: str) -> tuple[Resposta, list[dict]]:
        r = await self._get(
            "inscricoes_municipais",
            f"{self.base_nfse}/v2/companies/{quote(cid, safe='')}/municipaltaxes",
        )
        return r, _pagina(r.corpo, "municipalTaxes") if r.ok else []

    # --- notas --------------------------------------------------------------

    async def emitir(self, cid: str, payload: dict) -> Resposta:
        """POST: NUNCA repete. 202 (nota parcial + Location) ou 201."""
        return await self._uma("emitir", "POST", self._si(cid), json=payload)

    async def nota(self, cid: str, nota_id: str) -> Resposta:
        return await self._get("nota", self._si(cid, f"/{quote(nota_id, safe='')}"))

    async def nota_por_external(self, cid: str, external_id: str) -> tuple[Resposta, dict | None]:
        """Não achou = 404 OU 200 com lista vazia (os dois já foram vistos)."""
        r = await self._get(
            "nota_por_external", self._si(cid, f"/external/{quote(external_id, safe='')}")
        )
        if not r.ok:
            return r, None
        if isinstance(r.corpo, Mapping) and "serviceInvoices" in r.corpo:
            itens = _pagina(r.corpo, "serviceInvoices")
            return r, (itens[0] if itens else None)
        if isinstance(r.corpo, Mapping) and r.corpo.get("id"):
            return r, dict(r.corpo)
        return r, None

    async def listar_notas(
        self,
        cid: str,
        *,
        de: str | None = None,
        ate: str | None = None,
        por_pagina: int = 50,
        max_paginas: int = 10,
    ) -> tuple[Resposta, list[dict]]:
        """Notas da empresa, da mais nova pra mais velha. `de`/`ate` (AAAA-MM-DD)
        filtram pela competência (issuedBegin/issuedEnd)."""
        por_pagina = max(1, min(por_pagina, POR_PAGINA_MAX))
        params: dict[str, Any] = {"pageCount": por_pagina}
        if de:
            params["issuedBegin"] = de
        if ate:
            params["issuedEnd"] = ate
        todas: list[dict] = []
        r = Resposta("listar_notas", None)
        for pagina in range(1, max_paginas + 1):
            r = await self._get(
                "listar_notas", self._si(cid), params={**params, "pageIndex": pagina}
            )
            if not r.ok:
                return r, todas
            itens = _pagina(r.corpo, "serviceInvoices")
            todas.extend(itens)
            if len(itens) < por_pagina:
                break
        return r, todas

    async def cancelar(self, cid: str, nota_id: str) -> Resposta:
        """DELETE: NUNCA repete. 200 (nota) ou 202 (WaitingSendCancel)."""
        return await self._uma("cancelar", "DELETE", self._si(cid, f"/{quote(nota_id, safe='')}"))

    # Sem `enviar_email` (PUT /sendemail) desde 30/09/2026: ele não aceita
    # destinatário e manda para o e-mail gravado na nota, que não vai mais à
    # NFE.io. O envio é do DaVinci (`emissao.enviar_por_email`).

    async def _baixar(self, operacao: str, url: str, accept: str) -> Arquivo:
        r = await self._get(operacao, url, headers={"Accept": accept})
        if r.status in (301, 302, 303, 307, 308) and r.location:
            # URL assinada (storage): cliente NOVO, sem o header da chave.
            inicio = time.monotonic()
            kwargs: dict[str, Any] = {"timeout": TIMEOUT, "follow_redirects": True}
            if self._t is not None:
                kwargs["transport"] = self._t
            try:
                async with httpx.AsyncClient(**kwargs) as livre:
                    rr = await livre.get(r.location, headers={"Accept": accept})
            except httpx.HTTPError as e:
                return Arquivo(
                    Resposta(operacao, None, erro_rede=f"sem conexão ({type(e).__name__})")
                )
            resp = Resposta(
                operacao, rr.status_code, duracao_ms=int((time.monotonic() - inicio) * 1000)
            )
            if rr.status_code == 200 and rr.content:
                return Arquivo(resp, rr.content, rr.headers.get("content-type"))
            return Arquivo(resp)
        if r.status == 200 and r.bruto:
            return Arquivo(r, r.bruto, r.tipo)
        return Arquivo(r)

    async def pdf(self, cid: str, nota_id: str) -> Arquivo:
        return await self._baixar(
            "pdf", self._si(cid, f"/{quote(nota_id, safe='')}/pdf"), "application/pdf"
        )

    async def xml(self, cid: str, nota_id: str) -> Arquivo:
        return await self._baixar(
            "xml", self._si(cid, f"/{quote(nota_id, safe='')}/xml"), "application/xml"
        )
