"""Flex por anúncio: o resultado de UMA chamada às APIs do Flex e como ler a
resposta de cada plataforma (projeto Flex, etapa 3).

Por que um classificador PRÓPRIO, e não o `_map_status_error` do estoque:
lá o 400 é fatal e o resultado vai para `product_links.last_sync_status` —
um vínculo FATAL para de receber estoque pelo aviso do Bling
(webhooks.py). O Flex tem outra semântica (FATO, página oficial
developers.mercadolivre.com.br/pt_br/envios-flex, 22/09/2026):

  POST   /flex/sites/MLB/items/{id}/v2  liga    → 204
  DELETE /flex/sites/MLB/items/{id}/v2  desliga → 204
  GET    /flex/sites/MLB/items/{id}/v2  lê      → 200 {"has_flex": bool}

  400 "item is already in flex"  → o que se queria já está feito: SUCESSO
  403 "item down"                → o anúncio não oferece Flex: INELEGÍVEL,
                                   não adianta repetir
  404 "item not found"           → país/anúncio sem Flex: INDISPONÍVEL
  409 "can't activate item"      → pedidos simultâneos no mesmo anúncio:
                                   tentar DEPOIS (o motor serializa por
                                   anúncio, então é raro)
  429 / 5xx / rede               → tentar depois
  401                            → token/escopo ("revisar scope"): pessoa

O resultado do Flex NUNCA é gravado no vínculo — vai para
`flex_anuncio_estado` / `flex_log` (services/flex_motor).

Shopee (Entrega Direta, canal 90022): o canal é por ANÚNCIO, no
`logistic_info` do item. Ler: `get_item_base_info` (até 50 por chamada).
Escrever: `update_item` com `logistic_info` — e SEMPRE a lista COMPLETA de
canais lida antes, mudando só o `enabled` do canal Flex. A doc atual tirou
`logistic_info` da lista de parâmetros do update_item (só sobra no exemplo):
uma lista parcial pode desligar a Shopee Express do anúncio, e a Entrega
Direta não pode ser o único canal. Por isso a escrita Shopee só acontece com
`flex_shopee_escrita=True` (configuração) e é conferida com nova leitura.

A CONTA (revisão de 02/10/2026, com os fatos lidos nas contas): antes de ler
ou escrever qualquer anúncio, o motor confere se a conta PODE ter Flex —
`AssinaturaFlex`:
  ML     GET /flex/sites/MLB/users/{user_id}/subscriptions/v1 → lista de
         assinaturas com `status` "in" / "pending" / "out". Só "in" tem Flex;
         algumas contas respondem 404 ou 403 (sem Flex).
  Shopee GET /api/v2/logistics/get_channel_list → o canal 90022 tem de estar
         `enabled` NA LOJA (e com `mask_channel_id` 0). Em 02/10/2026 ele
         existe nas 14 lojas e está desligado em todas.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

# Tipos de resultado (o que o motor faz com cada um está em flex_motor).
OK = "ok"
INELEGIVEL = "inelegivel"  # a plataforma diz que o anúncio não pode ter Flex
INDISPONIVEL = "indisponivel"  # anúncio/país sem Flex (404)
REPETIR = "repetir"  # passageiro: 409, 429, 5xx, rede — tentar depois
SEM_PERMISSAO = "sem_permissao"  # 401/escopo — precisa de pessoa
ERRO = "erro"  # qualquer outra recusa (400 sem "already", resposta estranha)

TIPOS = (OK, INELEGIVEL, INDISPONIVEL, REPETIR, SEM_PERMISSAO, ERRO)
# A plataforma recusou o anúncio em si: repetir não muda a resposta.
TIPOS_RECUSA = frozenset({INELEGIVEL, INDISPONIVEL})

ML_SITE = "MLB"


@dataclass(frozen=True)
class ResultadoFlex:
    """Resposta de uma chamada do Flex, já classificada.

    `has_flex`: o estado do Flex do anúncio DEPOIS da chamada (ou lido),
    quando se sabe; None = não se sabe. `canais` (só Shopee): a lista
    `logistic_info` inteira como a Shopee devolveu — é dela que sai o payload
    da escrita (nunca lista parcial)."""

    tipo: str
    has_flex: bool | None = None
    status_http: int | None = None
    detalhe: str = ""
    canais: tuple[dict, ...] | None = field(default=None, compare=False)
    # Status do anúncio que veio na MESMA leitura (Shopee: `item_status` do
    # get_item_base_info), já nos nomes da importação — ver `status_shopee`.
    # None = a leitura não diz (o GET do Flex do ML só traz `has_flex`).
    status_anuncio: str | None = field(default=None, compare=False)

    @property
    def ok(self) -> bool:
        return self.tipo == OK

    def texto(self) -> str:
        """Para `ultimo_erro` / `flex_log.erro`: "403 item down"."""
        partes = [str(self.status_http) if self.status_http else "", self.detalhe]
        return " ".join(p for p in partes if p).strip()[:500] or self.tipo


@dataclass(frozen=True)
class AssinaturaFlex:
    """A conta pode ter Flex? `ativo`: True = sim; False = não (a plataforma
    respondeu que não: out/pending/404/403, canal desligado na loja); None =
    não deu para saber agora (rede, 429, 5xx) — vale a resposta anterior.
    `status`: o que veio, cru ("in", "pending", "out", "http_404",
    "sem_assinatura", "sem_canal").

    `origem_cep` / `origem_cidade` (ML, só com assinatura "in"): de onde o
    motoboy do Flex sai — `origin.zip_code` / `origin.city.name` de cada
    assinatura "in" (só dígitos; mais de uma origem vem separada por
    vírgula). None = a resposta não trouxe (a trava de origem do motor trata
    como "não se sabe de onde sai")."""

    ativo: bool | None
    status: str | None = None
    detalhe: str = ""
    origem_cep: str | None = None
    origem_cidade: str | None = None


@dataclass(frozen=True)
class ListagemConta:
    """Ids dos anúncios de UMA conta com um status (descoberta). `completo`:
    leu até o fim sem buraco; `erro`: o porquê de ter parado antes."""

    ids: tuple[str, ...] = ()
    completo: bool = False
    erro: str | None = None


# Status do anúncio nos nomes da importação (models.enums.ListingStatus).
STATUS_ATIVO = "active"
STATUS_ANUNCIO = ("active", "paused", "under_review", "inactive", "closed")
# Shopee `item_status` → status da importação.
_STATUS_SHOPEE = {
    "NORMAL": "active",
    "UNLIST": "paused",
    "REVIEWING": "under_review",
    "BANNED": "inactive",
    "DELETED": "closed",
    "SELLER_DELETE": "closed",
    "SHOPEE_DELETE": "closed",
}


def status_shopee(item_status: Any) -> str | None:
    """`item_status` da Shopee nos nomes da importação; desconhecido = None."""
    return _STATUS_SHOPEE.get(str(item_status or "").strip().upper())


def status_ml(status: Any) -> str | None:
    """`status` do item do ML (active, paused, closed, under_review,
    inactive); desconhecido = None."""
    s = str(status or "").strip().lower()
    return s if s in STATUS_ANUNCIO else None


# ---- Mercado Livre -------------------------------------------------------------


def _texto_ml(r: httpx.Response) -> str:
    """`message` + `error` do corpo de erro do ML, em minúsculas (vazio se não
    for JSON)."""
    try:
        corpo = r.json()
    except ValueError:
        return (r.text or "")[:300].lower()
    if not isinstance(corpo, Mapping):
        return ""
    partes = [str(corpo.get(k) or "") for k in ("message", "error", "cause")]
    return " ".join(p for p in partes if p)[:300].lower()


def classificar_ml(acao: str, r: httpx.Response) -> ResultadoFlex:
    """Classifica a resposta do ML. `acao`: "ler" | "ligar" | "desligar"."""
    st = r.status_code
    msg = _texto_ml(r)
    if 200 <= st < 300:
        if acao == "ler":
            try:
                corpo = r.json()
            except ValueError:
                corpo = None
            valor = corpo.get("has_flex") if isinstance(corpo, Mapping) else None
            if isinstance(valor, bool):
                return ResultadoFlex(OK, has_flex=valor, status_http=st)
            return ResultadoFlex(ERRO, status_http=st, detalhe="resposta sem has_flex")
        return ResultadoFlex(OK, has_flex=(acao == "ligar"), status_http=st)
    if st == 400 and acao == "ligar" and "already" in msg:
        # "item is already in flex": idempotente — o que se queria já está.
        return ResultadoFlex(OK, has_flex=True, status_http=st, detalhe=msg)
    if st == 401:
        return ResultadoFlex(SEM_PERMISSAO, status_http=st, detalhe=msg or "token/escopo recusado")
    if st == 403:
        if "down" in msg:
            return ResultadoFlex(INELEGIVEL, status_http=st, detalhe=msg)
        # O GET documenta 403 como problema de token; sem "item down" é isso.
        return ResultadoFlex(SEM_PERMISSAO, status_http=st, detalhe=msg or "acesso negado")
    if st == 404:
        return ResultadoFlex(INDISPONIVEL, status_http=st, detalhe=msg or "item not found")
    if st in (409, 429) or st >= 500:
        return ResultadoFlex(REPETIR, status_http=st, detalhe=msg)
    return ResultadoFlex(ERRO, status_http=st, detalhe=msg)


def _lista_de_assinaturas(corpo: Any) -> list[Mapping[str, Any]]:
    """A resposta do subscriptions/v1 é uma LISTA; aceita também o envelope
    `{"results": [...]}` (o formato não está na página oficial)."""
    if isinstance(corpo, Mapping):
        for chave in ("results", "subscriptions", "data"):
            if isinstance(corpo.get(chave), list):
                corpo = corpo[chave]
                break
        else:
            corpo = [corpo] if corpo.get("status") is not None else []
    if not isinstance(corpo, list):
        return []
    return [x for x in corpo if isinstance(x, Mapping)]


def _origens_ml(assinaturas: Iterable[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """CEP (só dígitos) e cidade da origem de cada assinatura "in", sem
    repetir, separados por vírgula. Origem sem CEP não entra (a trava trata
    a conta inteira como "não se sabe de onde sai")."""
    ceps: list[str] = []
    cidades: list[str] = []
    for a in assinaturas:
        origem = a.get("origin")
        if not isinstance(origem, Mapping):
            continue
        cep = "".join(ch for ch in str(origem.get("zip_code") or "") if ch.isdigit())
        if not cep:
            continue
        if cep not in ceps:
            ceps.append(cep)
        cidade = origem.get("city")
        nome = str(cidade.get("name") or "").strip() if isinstance(cidade, Mapping) else ""
        if nome and nome not in cidades:
            cidades.append(nome)
    return (",".join(ceps) or None), (", ".join(cidades)[:200] or None)


def classificar_assinatura_ml(r: httpx.Response) -> AssinaturaFlex:
    """A conta do ML tem o Flex? Só a assinatura "in" vale ("pending" ainda
    não; "out" saiu). 401/403/404: a conta não tem Flex (fato: eron, mega e
    dream2 respondem 404; nexus, 403). 429/5xx: não deu para saber."""
    st = r.status_code
    if 200 <= st < 300:
        try:
            corpo = r.json()
        except ValueError:
            return AssinaturaFlex(None, None, "resposta não-JSON")
        assinaturas = _lista_de_assinaturas(corpo)
        statuses = [str(x.get("status") or "").strip().lower() for x in assinaturas]
        statuses = [x for x in statuses if x]
        if "in" in statuses:
            cep, cidade = _origens_ml(
                x for x in assinaturas if str(x.get("status") or "").strip().lower() == "in"
            )
            return AssinaturaFlex(
                True, "in", "assinatura do Flex ativa", origem_cep=cep, origem_cidade=cidade
            )
        if not statuses:
            return AssinaturaFlex(False, "sem_assinatura", "a conta não tem assinatura do Flex")
        # "pending" diz mais que "out" (a assinatura está a caminho).
        status = "pending" if "pending" in statuses else statuses[0]
        return AssinaturaFlex(False, status, f"assinatura do Flex: {status}")
    if st == 429 or st >= 500:
        return AssinaturaFlex(None, None, f"{st} {_texto_ml(r)}".strip())
    return AssinaturaFlex(False, f"http_{st}", f"{st} {_texto_ml(r)}".strip())


def erro_de_rede(exc: BaseException) -> ResultadoFlex:
    """Timeout / conexão caída / refresh que falhou: tentar depois. O refresh
    recusado (RuntimeError `ml_refresh_failed`) é de pessoa."""
    texto = str(exc)[:300]
    if isinstance(exc, httpx.HTTPError):
        return ResultadoFlex(REPETIR, detalhe=f"rede: {texto}")
    if "refresh" in texto.lower():
        return ResultadoFlex(SEM_PERMISSAO, detalhe=texto)
    return ResultadoFlex(ERRO, detalhe=texto)


def assinatura_erro(exc: BaseException) -> AssinaturaFlex:
    """Conferência da conta que não chegou a ter resposta (rede, refresh do
    token): não se sabe — vale a anterior."""
    return AssinaturaFlex(None, None, str(exc)[:300])


# ---- Shopee --------------------------------------------------------------------


def classificar_canal_loja_shopee(
    resp: Mapping[str, Any] | None, canais_flex: Collection[str]
) -> AssinaturaFlex:
    """O canal Flex (Entrega Direta, 90022) está ligado NA LOJA? A guia
    "Product creation preparation": no produto só vale canal com
    `enabled=true` E `mask_channel_id=0` no `get_channel_list`."""
    alvo = {str(c).strip() for c in canais_flex}
    lista = (resp or {}).get("logistics_channel_list")
    if not isinstance(lista, list):
        return AssinaturaFlex(None, None, "a Shopee não mandou logistics_channel_list")
    achados = [
        c
        for c in lista
        if isinstance(c, Mapping) and str(c.get("logistics_channel_id") or "").strip() in alvo
    ]
    if not achados:
        return AssinaturaFlex(
            False, "sem_canal", "a loja não tem o canal Entrega Direta na Shopee"
        )
    for c in achados:
        try:
            mascara = int(c.get("mask_channel_id") or 0)
        except (TypeError, ValueError):
            mascara = -1
        if c.get("enabled") is True and mascara == 0:
            return AssinaturaFlex(True, "in", "Entrega Direta ligada na loja")
    return AssinaturaFlex(False, "out", "Entrega Direta desligada na loja")



def _id_canal(entrada: Mapping[str, Any]) -> str:
    return str(entrada.get("logistic_id") if entrada.get("logistic_id") is not None else "")


def flex_nos_canais(
    logistic_info: Iterable[Mapping[str, Any]] | None, canais_flex: Collection[str]
) -> bool | None:
    """O canal Flex está ligado no anúncio? None quando a Shopee não mandou
    `logistic_info` (a doc marcou campos de logística como "deprecated" na
    resposta em 24/06/2026: sem o campo, "não sei" — nunca "desligado")."""
    if logistic_info is None:
        return None
    alvo = {str(c).strip() for c in canais_flex}
    return any(
        _id_canal(e) in alvo and bool(e.get("enabled"))
        for e in logistic_info
        if isinstance(e, Mapping)
    )


# Campos que o `update_item` aceita em cada canal (exemplo da doc). Os outros
# (`logistic_name`, `estimated_shipping_fee`) são só de leitura.
_CAMPOS_CANAL = ("logistic_id", "enabled", "shipping_fee", "size_id", "is_free")


def payload_canais(
    logistic_info: Iterable[Mapping[str, Any]],
    canais_flex: Collection[str],
    ligar: bool,
) -> list[dict]:
    """A lista COMPLETA de canais do anúncio para o `update_item`, igual à
    lida, mudando só o `enabled` do(s) canal(is) Flex. Levanta ValueError
    quando o canal Flex não aparece no anúncio (não dá para "ligar" um canal
    que a Shopee não ofereceu ao item) ou a lista veio vazia."""
    alvo = {str(c).strip() for c in canais_flex}
    out: list[dict] = []
    achou = False
    for e in logistic_info:
        if not isinstance(e, Mapping) or not _id_canal(e):
            continue
        item = {k: e[k] for k in _CAMPOS_CANAL if k in e and e[k] is not None}
        try:
            item["logistic_id"] = int(item["logistic_id"])
        except (TypeError, ValueError):
            pass
        if _id_canal(e) in alvo:
            achou = True
            item["enabled"] = bool(ligar)
        else:
            item["enabled"] = bool(e.get("enabled"))
        out.append(item)
    if not out:
        raise ValueError("o anúncio veio sem nenhum canal de envio")
    if not achou:
        raise ValueError("o canal Flex não aparece nos canais do anúncio")
    return out


def classificar_shopee(r: httpx.Response, corpo: Mapping[str, Any] | None) -> ResultadoFlex | None:
    """Erro da Shopee na escrita, ou None quando deu certo. O canal/preço que
    não serve para o anúncio (`error_invalid_price_for_logistic`, mensagens
    de logística) é recusa: repetir não muda nada."""
    st = r.status_code
    if st == 429 or st >= 500:
        return ResultadoFlex(REPETIR, status_http=st, detalhe=(r.text or "")[:200])
    if corpo is None:
        return ResultadoFlex(ERRO, status_http=st, detalhe="resposta não-JSON")
    erro = str(corpo.get("error") or "")
    if not erro and st < 400:
        return None
    msg = f"{erro}: {corpo.get('message') or ''}".strip(": ")[:300]
    baixo = msg.lower()
    if "auth" in baixo or "token" in baixo or "permission" in baixo:
        return ResultadoFlex(SEM_PERMISSAO, status_http=st, detalhe=msg)
    if "logistic" in baixo or "channel" in baixo:
        return ResultadoFlex(INELEGIVEL, status_http=st, detalhe=msg)
    if any(t in baixo for t in ("busy", "too_many", "too many", "rate_limit", "rate limit")):
        return ResultadoFlex(REPETIR, status_http=st, detalhe=msg)
    return ResultadoFlex(ERRO, status_http=st, detalhe=msg)
