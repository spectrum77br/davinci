"""Cliente da Open API da Temu para o VENDEDOR LOCAL do Brasil.

Decisão do dono (24/11/2026): a loja Temu "Barbosa" (vendedor local BR, app
próprio "Stock Sync Hub") passa a receber o ESTOQUE pelo DaVinci. Preço,
pedidos e rastreio ficam para depois.

O que este cliente faz:
- Testar conexão: `bg.open.accesstoken.info.get` (loja, tipo, região,
  validade e APIs liberadas), sem nunca devolver o token.
- Ler anúncios: `temu.local.sku.list.retrieve`, um registro por SKU
  (external_id = goodsId, variation_id = skuId, sku = outSkuSn).
- Enviar estoque: `bg.local.goods.stock.edit` com `skuStockTargetList`
  (valor ABSOLUTO), conferindo o resultado de cada SKU.
- Preço: NÃO envia nada (a API de preço base exige whitelist da Temu).

Trava de escrita: só envia estoque se a credencial tiver a chave de servidor
`_temu_stock_write_enabled` = True (padrão desligado; liga o administrador em
Integrações). A trava é conferida aqui no cliente E no transporte: qualquer API
que não seja de leitura é recusada antes de sair, salvo a de estoque com a
trava ligada.

Assinatura (doc "Signature Method for API request" + exemplo oficial em
Python): parâmetros de topo ordenados por chave, cada valor passado por
`json.dumps(v, ensure_ascii=False, separators=(',', ':')).strip('"')`,
concatenados chave+valor, com o app_secret no começo e no fim; MD5 maiúsculo.
O app_secret nunca vai na URL, no corpo, em log nem em mensagem de erro.

Credenciais em `integrations.credentials`:
    {
      "app_key":      str,
      "app_secret":   str,
      "access_token": str,
      "region":       "global" | "us" | "eu",   (padrão: global = Brasil)
      "_temu_stock_write_enabled": bool,         (só o servidor grava)
      "_temu_mall_id": str,                      (loja fixada ao ligar a trava)
      "_temu_ultimo_teste": {...}                (o que o último Testar leu)
    }

Este módulo só importa `base` do pacote de marketplaces (o teste
erp_local_tests/test_temu_protocol.py o carrega isolado).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

import httpx
import structlog

from app.services.marketplaces.base import SyncResult, SyncStatus, TestResult

if TYPE_CHECKING:
    from app.models import ProductLink

logger = structlog.get_logger()

TEMU_REGION_URLS: dict[str, str] = {
    "us": "https://openapi-b-us.temu.com/openapi/router",
    "eu": "https://openapi-b-eu.temu.com/openapi/router",
    # Brasil, México, Japão etc. (doc "Endpoints and Request Method").
    "global": "https://openapi-b-global.temu.com/openapi/router",
}
REGIOES_VALIDAS = frozenset(TEMU_REGION_URLS)

# Chave de servidor (como a `_carrefour_stock_write_enabled`): o formulário de
# credenciais não pode gravá-la; só o endpoint de administrador.
STOCK_WRITE_FLAG = "_temu_stock_write_enabled"
# Chaves de servidor também (o formulário não grava nenhuma delas):
# - a loja (mallId) desta integração, fixada na 1ª vez que o administrador liga
#   a trava. Token de outra loja reprova no Testar e não religa a trava.
MALL_ID_FIXADO = "_temu_mall_id"
# - o que o último Testar OK leu do token (loja, tipo, região, permissões que
#   faltam). Ligar a trava exige este registro completo; trocar a credencial
#   apaga o registro e desliga a trava.
ULTIMO_TESTE = "_temu_ultimo_teste"
# Campos que a pessoa digita no formulário.
CAMPOS_CREDENCIAL = frozenset({"app_key", "app_secret", "access_token", "region"})

MALL_TYPE_LOCAL = 100  # bg.open.accesstoken.info.get: 1 = SEMI, 100 = LOCAL
REGIAO_BRASIL = 29  # regionId do Brasil (pedidos PO-029-...)
ESTOQUE_MAX = 1_000_000  # bg.local.goods.stock.edit: 0..1.000.000 (erro 150013002)
STOCK_TYPE_COMUM = 0  # 0 = estoque comum (padrão); 1 = pré-venda

API_TOKEN_INFO = "bg.open.accesstoken.info.get"  # noqa: S105 — nome de API, não segredo
API_TOKEN_CREATE = "bg.open.accesstoken.create"  # noqa: S105 — nome de API, não segredo
API_SKU_LIST = "temu.local.sku.list.retrieve"
API_STOCK_EDIT = "bg.local.goods.stock.edit"

# Só estas saem sem a trava (todas só leem). A `bg.open.accesstoken.create`
# NÃO é leitura (emite token novo e pode invalidar o que está em uso): sai só
# pelo caminho explícito do `exchange_authorization_code`.
_APIS_LEITURA = frozenset(
    {
        API_TOKEN_INFO,
        API_SKU_LIST,
        "bg.local.goods.cats.get",
        "bg.local.goods.list.query",
        "bg.local.goods.sku.list.query",
        "temu.local.goods.sku.stock.query",
    }
)
# A única escrita permitida, e só com a trava ligada.
_APIS_ESCRITA_ESTOQUE = frozenset({API_STOCK_EDIT})

# APIs que o DaVinci usa para estoque, e o pacote de permissão de cada uma.
ESCOPOS_ESTOQUE: dict[str, str] = {
    API_SKU_LIST: "Product Management (ler os anúncios)",
    API_STOCK_EDIT: "Product Management (enviar estoque)",
}

# Parâmetros comuns: a chamada não pode trocar nenhum deles.
_PARAMS_COMUNS = frozenset(
    {"type", "app_key", "access_token", "timestamp", "data_type", "version", "sign", "app_secret"}
)

COD_SUCESSO = 1_000_000

# Tentar de novo: limite de chamadas, falha interna da Temu, "try again later".
CODIGOS_RETRYABLE = frozenset(
    {
        2_000_000,  # BUSINESS_EXCEPTION (falha interna da Temu)
        4_000_000,  # SYSTEM_EXCEPTION
        4_000_004,  # RATE_LIMIT_EXCEED_EXCEPTION
        7_000_000,  # BUSINESS_SERVICE_ERROR (falha interna, como o 2000000)
        150_010_002,  # System error, please try again later
        150_010_005,  # Try again later
    }
)
# Erro DA CONTA (credencial, permissão, IP) ou do relógio do servidor: vale
# para todos os anúncios da loja; não é culpa do vínculo.
CODIGOS_CONTA = frozenset(
    {
        3_000_001,  # assinatura inválida (app_secret errado)
        3_000_011,  # timestamp inválido (relógio do servidor adiantado)
        3_000_012,  # timestamp vencido (relógio do servidor atrasado)
        3_000_019, 3_000_020, 3_000_021, 3_000_022,  # client_id
        3_000_025, 3_000_026, 3_000_027, 3_000_028,  # app_key
        3_000_030, 3_000_031, 3_000_032, 3_000_033, 3_000_034,  # access_token
        5_000_000,  # AUTHORIZE_NOT_ALLOW
        5_000_003,  # NOT_IN_IP_WHITE_LIST
    }
)
# Erro do GATEWAY sobre a chamada em si (API aposentada/inexistente, parâmetro
# comum faltando): vale para o app inteiro, não para um anúncio. Mesmo
# tratamento do erro da conta (não mata vínculo; 1 aviso por conta e por dia).
CODIGOS_APP = frozenset(
    {
        3_000_000,  # BAD_PARAMS
        3_000_002,  # falta o type
        3_000_003,  # type não existe
        3_000_004,  # type aposentado ("sunset")
        3_000_010,  # falta o timestamp
        3_000_013,  # falta o data_type
        3_000_014,  # data_type inválido
        3_000_040,  # falta o sign
        5_000_001,  # ROUTER_NOT_ALLOW
        5_000_002,  # NOT_SUPPORT_STAGING_ENV
        6_000_001,  # RPC_INTERFACE_NOT_FOUND (API interna sumiu)
    }
)
# Códigos de negócio da própria API de produto/estoque (1500xxxxx): esses sim
# falam do produto/SKU pedido.
_FAIXA_NEGOCIO = range(150_000_000, 160_000_000)

_DICA_API_APOSENTADA = "a Temu não reconhece ou aposentou esta API: atualizar o DaVinci"
_DICA_PEDIDO_MAL_FORMADO = (
    "a Temu recusou o formato do pedido do DaVinci: avisar o desenvolvedor"
)
_DICA_RELOGIO = (
    "o relógio do servidor do DaVinci está fora da hora (a Temu aceita ±5 min): "
    "acertar o relógio"
)
DICAS_CONTA: dict[int, str] = {
    3_000_000: _DICA_PEDIDO_MAL_FORMADO,
    3_000_002: _DICA_PEDIDO_MAL_FORMADO,
    3_000_003: _DICA_API_APOSENTADA,
    3_000_004: _DICA_API_APOSENTADA,
    3_000_010: _DICA_PEDIDO_MAL_FORMADO,
    3_000_011: _DICA_RELOGIO,
    3_000_012: _DICA_RELOGIO,
    3_000_013: _DICA_PEDIDO_MAL_FORMADO,
    3_000_014: _DICA_PEDIDO_MAL_FORMADO,
    3_000_040: _DICA_PEDIDO_MAL_FORMADO,
    5_000_001: _DICA_PEDIDO_MAL_FORMADO,
    5_000_002: "o pedido não chegou ao ambiente de produção da Temu: avisar o desenvolvedor",
    6_000_001: _DICA_API_APOSENTADA,
    3_000_001: "assinatura recusada: confira o App Secret",
    3_000_020: "App Key não existe na Temu",
    3_000_026: "App Key não existe na Temu",
    3_000_021: "o app não tem permissão para esta API",
    3_000_027: "o app não tem permissão para esta API",
    3_000_022: "o app está suspenso na Temu",
    3_000_028: "o app está suspenso na Temu",
    3_000_031: "access token não existe: gere um novo no Seller Center",
    3_000_032: (
        "o token não tem permissão para esta API: autorize o pacote no Seller Center "
        "e troque o token"
    ),
    3_000_033: "o access token não é deste App Key",
    3_000_034: "access token vencido ou trocado: gere um novo no Seller Center",
    5_000_000: "a loja não autorizou o app",
    5_000_003: "o IP do servidor não está na lista de IPs do app na Temu",
}

CLASSE_RETRYABLE = "retryable"
CLASSE_CONTA = "conta"
CLASSE_FATAL = "fatal"

_BRT = timezone(timedelta(hours=-3))

_MSG_NAO_JSON = "resposta não-JSON"
_MSG_INVALIDA = "resposta inválida"
_SEM_CONFIRMACAO = object()  # marcador do _falha_por_sku


class TemuErro(RuntimeError):  # noqa: N818 — nome em português, como o resto
    """Erro da Temu já classificado. A mensagem nunca leva segredo nem token."""

    def __init__(
        self, mensagem: str, *, codigo: int | None, classe: str, http: int | None = None
    ):
        super().__init__(mensagem)
        self.codigo = codigo
        self.classe = classe
        self.http = http


class TemuEscritaBloqueada(RuntimeError):  # noqa: N818
    """Tentativa de chamar uma API que escreve sem a trava ligada."""


# ---------------------------------------------------------------- assinatura


def _valor_assinado(valor: Any) -> str:
    """Igual ao Python oficial: JSON compacto e sem as aspas das pontas."""
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":")).strip('"')


def assinar(app_secret: str, params: dict[str, Any]) -> str:
    corpo = "".join(f"{k}{_valor_assinado(v)}" for k, v in sorted(params.items()))
    # MD5 é o que a Temu exige na assinatura (não é guarda de senha).
    return hashlib.md5(f"{app_secret}{corpo}{app_secret}".encode()).hexdigest().upper()  # noqa: S324


def classificar_codigo(codigo: int | None) -> str:
    """Classe do erro do ENVELOPE (a chamada inteira falhou).

    Só o código de negócio da própria API (1500xxxxx) é do anúncio pedido e
    vira FATAL do vínculo. Erro do gateway, da conta, do app ou desconhecido
    vale para a conta toda: não mata vínculo (sairia do webhook) e gera um
    aviso por conta e por dia."""
    if codigo in CODIGOS_RETRYABLE:
        return CLASSE_RETRYABLE
    if codigo is not None and codigo in _FAIXA_NEGOCIO:
        return CLASSE_FATAL
    return CLASSE_CONTA


def classificar_codigo_do_sku(codigo: int | None) -> str:
    """Classe da recusa de UM SKU dentro de um envelope de sucesso."""
    return CLASSE_RETRYABLE if codigo in CODIGOS_RETRYABLE else CLASSE_FATAL


def _int_ou_none(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _id_numerico(v: Any) -> int | None:
    texto = str(v or "").strip()
    if not texto.isascii() or not texto.isdecimal():
        return None
    n = int(texto)
    return n if n > 0 else None


def _status_http(result: dict) -> int | None:
    """O `_request` devolve {"error_msg": "HTTP nnn"} quando o HTTP não é 200."""
    partes = str(result.get("error_msg") or "").split()
    if len(partes) == 2 and partes[0] == "HTTP":
        return _int_ou_none(partes[1])
    return None


class TemuClient:
    def __init__(self, creds: dict, on_token_refresh=None):
        self.creds = dict(creds)
        # A Temu não tem refresh token: autoriza de novo no Seller Center.
        self._on_refresh = on_token_refresh
        region = str(creds.get("region") or "global").strip().lower()
        self._base_url = TEMU_REGION_URLS.get(region, TEMU_REGION_URLS["global"])

    @property
    def app_key(self) -> str:
        return str(self.creds.get("app_key") or "").strip()

    @property
    def app_secret(self) -> str:
        return str(self.creds.get("app_secret") or "").strip()

    @property
    def access_token(self) -> str:
        return str(self.creds.get("access_token") or "").strip()

    @property
    def stock_write_enabled(self) -> bool:
        return self.creds.get(STOCK_WRITE_FLAG) is True

    # ---------------------------------------------------------------- assinatura

    def _generate_sign(self, params: dict[str, Any]) -> str:
        return assinar(self.app_secret, params)

    def _timestamp(self) -> int:
        return int(time.time())

    # ---------------------------------------------------------------- transporte

    def _redigir(self, texto: str) -> str:
        for segredo in (self.app_secret, self.access_token):
            if segredo:
                texto = texto.replace(segredo, "[redacted]")
        return texto

    async def _request(
        self,
        api_type: str,
        request_params: dict[str, Any] | None = None,
        *,
        _criar_token: bool = False,
    ) -> dict:
        """POST no gateway. Devolve o corpo da Temu, ou
        {"success": False, "error_msg": "HTTP nnn"} quando o HTTP não é 200.
        Erro de rede sobe como httpx.HTTPError.

        `_criar_token` libera só a `bg.open.accesstoken.create`, e só quem a
        pede de propósito (`exchange_authorization_code`)."""
        params = dict(request_params or {})
        sobrescritos = sorted(_PARAMS_COMUNS & params.keys())
        if sobrescritos:
            raise ValueError(f"parâmetro comum não pode vir da chamada: {','.join(sobrescritos)}")
        liberada = (
            api_type in _APIS_LEITURA
            or (api_type in _APIS_ESCRITA_ESTOQUE and self.stock_write_enabled)
            or (api_type == API_TOKEN_CREATE and _criar_token)
        )
        if not liberada:
            raise TemuEscritaBloqueada(
                f"chamada {api_type} recusada: escrita na Temu não liberada"
            )
        comuns: dict[str, Any] = {
            "type": api_type,
            "app_key": self.app_key,
            "access_token": self.access_token,
            "timestamp": self._timestamp(),
            "data_type": "JSON",
            "version": "V1",
        }
        todos = {**comuns, **params}
        payload = {**todos, "sign": self._generate_sign(todos)}

        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                self._base_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        if r.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {r.status_code}"}
        try:
            corpo = r.json()
        except ValueError:
            return {"success": False, "error_msg": _MSG_NAO_JSON}
        if not isinstance(corpo, dict):
            return {"success": False, "error_msg": _MSG_INVALIDA}
        return corpo

    def _is_success(self, result: dict) -> bool:
        sucesso = result.get("success")
        if sucesso is True:
            return True
        if sucesso is False:
            return False
        return _int_ou_none(result.get("errorCode")) == COD_SUCESSO

    def _error_message(self, result: dict) -> str:
        msg = str(
            result.get("errorMsg")
            or result.get("error_msg")
            or result.get("errorMessage")
            or result.get("message")
            or "Erro desconhecido"
        )
        return self._redigir(msg)[:300]

    def _erro(self, result: dict) -> TemuErro:
        codigo = _int_ou_none(result.get("errorCode"))
        if codigo is None:
            codigo = _int_ou_none(result.get("error_code"))
        msg = self._error_message(result)
        http = _status_http(result)
        if http is not None:
            # 429/5xx: tentar de novo. Qualquer outro HTTP (401/403, 404 de
            # gateway errado, 400…) vale para a conta toda, não para o anúncio.
            if http == 429 or http >= 500:
                return TemuErro(f"Temu {msg}", codigo=None, classe=CLASSE_RETRYABLE, http=http)
            return TemuErro(
                f"Temu {msg} (o gateway da Temu recusou a chamada)",
                codigo=None,
                classe=CLASSE_CONTA,
                http=http,
            )
        if codigo is None and msg in (_MSG_NAO_JSON, _MSG_INVALIDA):
            # Página de WAF/gateway com HTTP 200: soluço de rede, não o anúncio.
            return TemuErro(f"Temu: {msg}", codigo=None, classe=CLASSE_RETRYABLE)
        classe = classificar_codigo(codigo)
        dica = DICAS_CONTA.get(codigo or 0)
        texto = f"Temu {codigo}: {msg}" if codigo is not None else f"Temu: {msg}"
        if dica:
            texto = f"{texto} ({dica})"
        return TemuErro(texto, codigo=codigo, classe=classe)

    async def _chamar(
        self,
        api_type: str,
        params: dict[str, Any] | None = None,
        *,
        _criar_token: bool = False,
    ) -> dict:
        """Chama e devolve o corpo de sucesso; erro sobe como TemuErro."""
        try:
            result = await self._request(api_type, params, _criar_token=_criar_token)
        except httpx.HTTPError as e:
            raise TemuErro(
                f"Temu fora do ar ou lenta ({type(e).__name__})",
                codigo=None,
                classe=CLASSE_RETRYABLE,
            ) from None
        if not self._is_success(result):
            raise self._erro(result)
        return result

    # ---------------------------------------------------------------- autorização

    @staticmethod
    def authorization_url(
        app_key: str, redirect_uri: str, state: str, *, seller_host: str = "seller.temu.com"
    ) -> str:
        """Link de autorização dentro do app (doc "Seller Authorization Guide").
        O host do Seller Center do Brasil seria br.seller.temu.com (inferido;
        a doc só mostra o dos EUA)."""
        if not app_key or not state:
            raise ValueError("app_key e state são obrigatórios")
        partes = urlsplit(redirect_uri)
        if (
            partes.scheme != "https"
            or not partes.hostname
            or partes.username is not None
            or partes.password is not None
            or partes.fragment
            or "#" in redirect_uri
        ):
            raise ValueError("redirect_uri precisa ser https, sem usuário e sem fragmento")
        consulta = urlencode({"appKey": app_key, "redirect_uri": redirect_uri, "state": state})
        return f"https://{seller_host}/open-platform/client-manage/authorization?{consulta}"

    @classmethod
    async def exchange_authorization_code(
        cls, app_key: str, app_secret: str, code: str, *, region: str = "global"
    ) -> dict[str, Any]:
        """Troca o code do callback por access_token (bg.open.accesstoken.create;
        na primeira chamada o access_token é o próprio code)."""
        cliente = cls(
            {"app_key": app_key, "app_secret": app_secret, "access_token": code, "region": region}
        )
        corpo = await cliente._chamar(API_TOKEN_CREATE, {"code": code}, _criar_token=True)
        r = corpo.get("result") if isinstance(corpo.get("result"), dict) else {}
        token = str(r.get("accessToken") or "").strip()
        mall_id = r.get("mallId")
        if not token or mall_id in (None, ""):
            raise ValueError("a Temu não devolveu o token e a loja da autorização")
        return {
            "access_token": token,
            "mall_id": str(mall_id),
            "mall_type": _int_ou_none(r.get("mallType")),
            "region_id": _int_ou_none(r.get("regionId")),
            "expired_time": _int_ou_none(r.get("expiredTime")),
            "api_scope_list": [str(s) for s in r.get("apiScopeList") or []],
        }

    # ---------------------------------------------------------------- MarketplaceClient

    async def test_connection(self) -> TestResult:
        required = ("app_key", "app_secret", "access_token")
        missing = [k for k in required if not str(self.creds.get(k) or "").strip()]
        if missing:
            return TestResult(ok=False, detail=f"missing_credentials: {','.join(missing)}")
        try:
            corpo = await self._chamar(API_TOKEN_INFO)
        except TemuErro as e:
            return TestResult(ok=False, detail=str(e))
        except Exception as e:  # noqa: BLE001
            texto = self._redigir(f"error: {type(e).__name__}: {e}")
            return TestResult(ok=False, detail=texto[:300])

        r = corpo.get("result") if isinstance(corpo.get("result"), dict) else {}
        info = _info_do_token(r)
        problemas: list[str] = []
        avisos: list[str] = []
        mall_type = info["mall_type"]
        region_id = info["region_id"]
        if mall_type is None:
            avisos.append("a Temu não informou o tipo da loja")
        elif mall_type != MALL_TYPE_LOCAL:
            problemas.append(
                f"a loja não é de vendedor LOCAL (mallType={mall_type}); "
                "o DaVinci só envia estoque para loja local"
            )
        if region_id is None:
            avisos.append("a Temu não informou a região da loja")
        elif region_id != REGIAO_BRASIL:
            problemas.append(
                f"a loja não é do Brasil (regionId={region_id}, esperado {REGIAO_BRASIL})"
            )
        epoch = info.get("expira_em_epoch")
        if epoch is not None and epoch <= time.time():
            problemas.append(f"o token venceu em {info['expira_em_texto']}")
        if info.get("validade_ilegivel"):
            avisos.append("a validade do token veio ilegível da Temu")
        fixada = str(self.creds.get(MALL_ID_FIXADO) or "").strip()
        if fixada and info.get("mall_id") != fixada:
            problemas.append(
                f"o token é da loja {info.get('mall_id') or '(não informada)'}, "
                f"mas esta integração é da loja {fixada}; para outra loja, crie outra integração"
            )
        faltando = info["escopos_faltando"]
        if faltando:
            avisos.append(
                "faltam permissões no token: "
                + "; ".join(f"{api} — {ESCOPOS_ESTOQUE[api]}" for api in faltando)
                + " (autorize de novo no Seller Center e troque o token)"
            )
        info["avisos"] = avisos
        # Pronto para ligar o envio de estoque: loja informada, LOCAL, do
        # Brasil e com as duas permissões. O PUT /temu-writes exige isto.
        info["pronto_para_estoque"] = bool(
            not problemas
            and info.get("mall_id")
            and mall_type == MALL_TYPE_LOCAL
            and region_id == REGIAO_BRASIL
            and not faltando
        )
        loja = f"loja {info['mall_id']}" if info.get("mall_id") else "loja"
        if problemas:
            return TestResult(ok=False, detail="Temu: " + "; ".join(problemas), info=info)
        detalhe = f"Conectado à Temu ({loja})"
        if info.get("expira_em_texto"):
            detalhe += f"; token vale até {info['expira_em_texto']}"
        if avisos:
            detalhe += ". Atenção: " + "; ".join(avisos)
        return TestResult(ok=True, detail=detalhe, info=info)

    async def update_stock(
        self,
        link: ProductLink,
        qty: int,
        *,
        bling_store_id: int | None = None,
        force: bool = False,
    ) -> SyncResult:
        """Grava o estoque ABSOLUTO de um SKU (bg.local.goods.stock.edit).

        link.external_id = goodsId; link.variation_id = skuId.
        `force` existe pela interface; a Temu não tem trava de zero."""
        del bling_store_id, force
        qty_before = link.stock
        if not self.stock_write_enabled:
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="temu_escrita_desligada",
                error_detail=(
                    "envio de estoque para a Temu desligado nesta conta "
                    "(o administrador liga em Integrações)"
                ),
            )
        goods_id = _id_numerico(link.external_id)
        sku_id = _id_numerico(link.variation_id)
        if goods_id is None or sku_id is None:
            return SyncResult(
                status=SyncStatus.FATAL,
                qty_before=qty_before,
                error_code="temu_ids_invalidos",
                error_detail=(
                    f"vínculo sem goodsId/skuId da Temu (external_id={link.external_id!r}, "
                    f"variation_id={link.variation_id!r}); vincule de novo"
                ),
            )
        alvo = max(0, min(int(qty), ESTOQUE_MAX))
        corpo_pedido = {
            "goodsId": goods_id,
            "stockType": STOCK_TYPE_COMUM,
            "skuStockTargetList": [{"skuId": sku_id, "stockTarget": alvo}],
        }
        try:
            corpo = await self._chamar(API_STOCK_EDIT, corpo_pedido)
        except TemuErro as e:
            return _resultado_do_erro(e, qty_before)
        except TemuEscritaBloqueada as e:  # não acontece com a trava ligada
            return SyncResult(
                status=SyncStatus.SKIPPED,
                qty_before=qty_before,
                error_code="temu_escrita_desligada",
                error_detail=str(e),
            )

        falha = _falha_por_sku(corpo, sku_id)
        if falha is not None and falha[0] is _SEM_CONFIRMACAO:
            # Envelope de sucesso sem o resultado do SKU (a ficha da API marca
            # todos os campos do result como opcionais): não é recusa.
            logger.warning("temu_estoque_sem_confirmacao", goods_id=goods_id, sku_id=sku_id)
            return SyncResult(
                status=SyncStatus.RETRYABLE,
                qty_before=qty_before,
                error_code="temu_sem_confirmacao",
                error_detail=(
                    f"a Temu não confirmou o estoque do SKU {sku_id} "
                    f"(resposta sem o resultado do SKU): {self._redigir(falha[1])}"
                )[:500],
            )
        if falha is not None:
            codigo, msg = falha
            msg = self._redigir(msg)
            classe = classificar_codigo_do_sku(codigo)
            status = SyncStatus.RETRYABLE if classe == CLASSE_RETRYABLE else SyncStatus.FATAL
            logger.warning(
                "temu_estoque_sku_recusado", goods_id=goods_id, sku_id=sku_id, codigo=codigo
            )
            return SyncResult(
                status=status,
                qty_before=qty_before,
                error_code=f"temu_{codigo}" if codigo is not None else "temu_sku_recusado",
                error_detail=f"a Temu recusou o estoque do SKU {sku_id}: {msg}"[:500],
            )
        logger.info("temu_estoque_enviado", goods_id=goods_id, sku_id=sku_id, quantidade=alvo)
        return SyncResult(
            status=SyncStatus.OK,
            qty_before=qty_before,
            qty_after=alvo,
            payload={"goods_id": str(goods_id), "sku_id": str(sku_id), "stock_target": alvo},
        )

    # ---------------------------------------------------------------- preço

    async def update_price(
        self, link: ProductLink | None = None, price: Any = None, **_: Any
    ) -> SyncResult:
        """Preço da Temu ainda NÃO é enviado: a API de preço base
        (bg.local.goods.priceorder.change.sku.price) exige whitelist da Temu e
        token novo. Nada sai daqui."""
        del link, price
        return SyncResult(
            status=SyncStatus.SKIPPED,
            error_code="temu_preco_nao_liberado",
            error_detail=(
                "preço da Temu ainda não liberado (falta a whitelist da Temu); "
                "nada foi enviado"
            ),
        )

    # ---------------------------------------------------------------- anúncios

    async def search_products(
        self,
        *,
        sku_search_type: str = "ACTIVE",
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[dict], str | None]:
        """Uma página de SKUs (temu.local.sku.list.retrieve). Devolve
        (skus normalizados, próximo pageToken ou None)."""
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(page_size), 100)),
            "skuSearchType": sku_search_type,
        }
        if page_token:
            params["pageToken"] = page_token
        corpo = await self._chamar(API_SKU_LIST, params)
        r = corpo.get("result") if isinstance(corpo.get("result"), dict) else {}
        itens = [
            _sku_normalizado(s, sku_search_type)
            for s in r.get("skuList") or []
            if isinstance(s, dict)
        ]
        pag = r.get("pagination") if isinstance(r.get("pagination"), dict) else {}
        proximo = str(pag.get("nextToken") or "").strip() or None
        return [i for i in itens if i is not None], proximo

    async def list_listings(
        self,
        *,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict]:
        """Um registro por SKU: ativos, inativos, incompletos e excluídos (o
        excluído serve para marcar vínculo morto; não vira vínculo novo).
        Rascunho fica de fora."""
        for tipo in ("ACTIVE", "INACTIVE", "INCOMPLETE", "DELETED"):
            token: str | None = None
            vistos: set[str] = set()
            paginas = 0
            while True:
                if max_pages is not None and paginas >= max_pages:
                    break
                itens, proximo = await self.search_products(
                    sku_search_type=tipo, page_token=token, page_size=page_size
                )
                paginas += 1
                for item in itens:
                    yield item
                if not itens or not proximo or proximo in vistos:
                    break
                vistos.add(proximo)
                token = proximo


# ---------------------------------------------------------------- helpers


def _info_do_token(r: dict) -> dict[str, Any]:
    """Só os campos liberados: o token (accessToken) nunca entra."""
    mall_id = r.get("mallId")
    escopos = sorted({str(s) for s in r.get("apiScopeList") or [] if s})
    epoch = _int_ou_none(r.get("expiredTime"))
    expira_iso = expira_texto = None
    ilegivel = False
    if epoch is not None and epoch > 0:
        if epoch > 100_000_000_000:  # a doc diz segundos; isto é milissegundo
            epoch //= 1000
        try:
            quando = datetime.fromtimestamp(epoch, tz=UTC)
            expira_iso = quando.isoformat()
            expira_texto = (
                quando.astimezone(_BRT).strftime("%d/%m/%Y %H:%M") + " (horário de Brasília)"
            )
        except (ValueError, OverflowError, OSError):
            ilegivel = True
    return {
        "validade_ilegivel": ilegivel,
        "mall_id": str(mall_id) if mall_id not in (None, "") else None,
        "mall_type": _int_ou_none(r.get("mallType")),
        "loja_local": _int_ou_none(r.get("mallType")) == MALL_TYPE_LOCAL,
        "region_id": _int_ou_none(r.get("regionId")),
        "regiao_brasil": _int_ou_none(r.get("regionId")) == REGIAO_BRASIL,
        "expira_em": expira_iso,
        "expira_em_texto": expira_texto,
        "expira_em_epoch": epoch if expira_iso else None,
        "escopos": escopos,
        "escopos_faltando": [api for api in ESCOPOS_ESTOQUE if api not in escopos],
    }


_STATUS_SKU = {
    "active": "active",
    "inactive": "paused",
    "incomplete": "under_review",
    "deleted": "closed",
    "draft": "inactive",
}


def _temu_status_to_listing(status: Any) -> str:
    """skuStatus (Active/InActive/Incomplete/Deleted/Draft) ou o skuSearchType
    usado na busca → status da lista do DaVinci."""
    return _STATUS_SKU.get(str(status or "").strip().lower(), "inactive")


def _sku_normalizado(s: dict, tipo_busca: str) -> dict | None:
    goods_id = str(s.get("goodsId") or "").strip()
    sku_id = str(s.get("skuId") or "").strip()
    if not goods_id or not sku_id:
        return None
    titulo = str(s.get("goodsName") or "").strip()
    spec = str(s.get("specName") or "").strip()
    if spec:
        titulo = f"{titulo} — {spec}" if titulo else spec
    estoque = _int_ou_none(s.get("stock"))
    return {
        "external_id": goods_id,
        "variation_id": sku_id,
        "sku": str(s.get("outSkuSn") or "").strip() or None,
        "title": titulo,
        "stock": estoque,
        "status": _temu_status_to_listing(s.get("skuStatus") or tipo_busca),
        "thumbnail_url": s.get("thumbUrl") or None,
        "category": str(s.get("catId") or "") or None,
        "raw": s,
    }


def _falha_por_sku(corpo: dict, sku_id: int) -> tuple[Any, str] | None:
    """None se o SKU foi gravado; (código, mensagem) se foi recusado;
    (_SEM_CONFIRMACAO, mensagem) se a resposta não diz nada do SKU. Envelope
    de sucesso com o SKU recusado é FALHA."""
    r = corpo.get("result") if isinstance(corpo.get("result"), dict) else {}
    operate = r.get("operateResult")
    entrada = None
    for item in r.get("skuStockEditStatusInfoList") or []:
        if isinstance(item, dict) and str(item.get("skuId") or "") == str(sku_id):
            entrada = item
            break
    if entrada is not None:
        if entrada.get("stockEditStatus") is True and operate is not False:
            return None
        codigo = _int_ou_none(entrada.get("errorCode"))
        msg = str(entrada.get("errorMsg") or r.get("msg") or "sem confirmação do SKU")
        return codigo, msg
    if operate is True:
        return None
    if operate is False:
        return None, str(r.get("msg") or "a Temu recusou a gravação do produto")
    return _SEM_CONFIRMACAO, str(r.get("msg") or "sem detalhe")


def _resultado_do_erro(e: TemuErro, qty_before: int | None) -> SyncResult:
    codigo = f"temu_{e.codigo}" if e.codigo is not None else "temu_erro"
    sufixo = (
        str(e.codigo) if e.codigo is not None else (f"http_{e.http}" if e.http else None)
    )
    if e.classe == CLASSE_RETRYABLE:
        return SyncResult(
            status=SyncStatus.RETRYABLE,
            qty_before=qty_before,
            error_code=codigo if e.codigo is not None else "temu_tentar_de_novo",
            error_detail=str(e)[:500],
        )
    if e.classe == CLASSE_CONTA:
        # Erro da CONTA/APP (token, permissão, IP, relógio, API aposentada,
        # gateway): não marca o vínculo como FATAL (ele sairia do webhook); o
        # orquestrador gera 1 alerta por conta e por dia.
        return SyncResult(
            status=SyncStatus.SKIPPED,
            qty_before=qty_before,
            error_code=f"temu_conta_{sufixo}" if sufixo else "temu_conta",
            error_detail=str(e)[:500],
            payload={"erro_de_conta": True},
        )
    return SyncResult(
        status=SyncStatus.FATAL,
        qty_before=qty_before,
        error_code=codigo,
        error_detail=str(e)[:500],
    )



# ---------------------------------------------------------------- lista por produto

# Melhor status primeiro: um produto com um SKU à venda está à venda.
_ORDEM_STATUS = {"active": 0, "paused": 1, "under_review": 2, "inactive": 3, "closed": 4}


def agrupar_por_produto(skus: list[dict]) -> list[dict]:
    """A lista de anúncios do DaVinci (`listings`) guarda UMA linha por
    produto (goodsId), mas o leitor da Temu traz um registro por SKU. Gravar
    SKU a SKU deixava o último lido (o excluído) mandar na linha: produto à
    venda aparecia encerrado, com estoque 0 e o SKU da variação excluída.

    Aqui junta por goodsId: status = o melhor entre os SKUs; estoque = soma
    dos SKUs vivos (não excluídos); SKU = o único SKU vivo, ou o código do
    produto (outGoodsSn) quando há várias variações. O vínculo por SKU
    continua sendo do Vincular, que lê SKU a SKU."""
    grupos: dict[str, list[dict]] = {}
    for item in skus:
        grupos.setdefault(item["external_id"], []).append(item)
    saida: list[dict] = []
    for goods_id, itens in grupos.items():
        melhor = min(itens, key=lambda s: _ORDEM_STATUS.get(s.get("status") or "", 3))
        vivos = [s for s in itens if s.get("status") != "closed"] or itens
        estoques = [s["stock"] for s in vivos if s.get("stock") is not None]
        skus_vivos = list(dict.fromkeys(s["sku"] for s in vivos if s.get("sku")))
        raw0 = melhor.get("raw") if isinstance(melhor.get("raw"), dict) else {}
        if len(skus_vivos) == 1:
            sku = skus_vivos[0]
        else:
            sku = str(raw0.get("outGoodsSn") or "").strip() or None
        titulo = (
            melhor.get("title")
            if len(itens) == 1
            else (str(raw0.get("goodsName") or "").strip() or melhor.get("title"))
        )
        saida.append(
            {
                "external_id": goods_id,
                "sku": sku,
                "title": titulo,
                "stock": sum(estoques) if estoques else None,
                "status": melhor.get("status"),
                "thumbnail_url": melhor.get("thumbnail_url"),
                "category": melhor.get("category"),
                "raw": {
                    "goodsId": goods_id,
                    "goodsName": raw0.get("goodsName"),
                    "outGoodsSn": raw0.get("outGoodsSn"),
                    "skus": [s.get("raw") for s in itens],
                },
            }
        )
    return saida


class TemuListaPorProduto:
    """Leitor para a lista de anúncios (importar e o botão Atualizar): uma
    linha por produto. Só lê (temu.local.sku.list.retrieve)."""

    def __init__(self, cliente: TemuClient):
        self.cliente = cliente

    async def list_listings(
        self, *, page_size: int = 100, max_pages: int | None = None
    ) -> AsyncIterator[dict]:
        skus = [
            s async for s in self.cliente.list_listings(page_size=page_size, max_pages=max_pages)
        ]
        for item in agrupar_por_produto(skus):
            yield item
