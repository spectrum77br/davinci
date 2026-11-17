"""Amazon envio próprio: transportadora "MelhorEnvio" → "Correios" (16/11/2026).

16/11 (Eduardo): no pedido Amazon de ENVIO PRÓPRIO (contas kia, poofy, kfa;
ShipServiceLevel "Std Dom 9"; sem Easy Ship) a etiqueta é comprada no Bling
pela logística "Envios por Melhor Envio.." e a integração Amazon do Bling
confirma o envio sozinha ~20 s depois com a transportadora "MelhorEnvio". A
Amazon não reconhece essa transportadora: o pacote fica SHIPPED, a entrega
nunca é registrada e o pedido corre risco de reclamação de A a Z. O Bling não
tem opção para mudar isso.

O que resolveu em produção (11 pedidos em 16/11, `_amz_transportadora_correios.py`
dos helpers): confirmar o envio DE NOVO, pelo mesmo `packageReferenceId`, com o
MESMO rastreio, serviço, data de envio e itens, trocando só a transportadora
para "Correios" (SP-API v0 `confirmShipment` — HTTP 204 e o pacote passa a
"Correios"). Este módulo faz isso sozinho a cada 15 min (worker
`amazon_transportadora_correios`).

Regras de segurança (todas obrigatórias):

* só pedido MFN, "Shipped", SEM Easy Ship (DBA) — AFN e Easy Ship nunca;
* só pedido com EXATAMENTE 1 pacote (2+ → não mexe e loga);
* só transportadora "MelhorEnvio" (sem diferenciar maiúsculas) e rastreio dos
  Correios (2 letras + 9 dígitos + "BR"); já "Correios" → nada (idempotente);
* só pacote com `shipTime` nas ÚLTIMAS 72 h — o Eduardo ainda NÃO autorizou
  trocar pedido antigo/entregue (ex.: 306097, com A a Z aberta, cujo
  LastUpdate mudou em 16/11): pacote mais velho que isso nunca é tocado;
* rastreio, serviço, data e itens vão exatamente como a Amazon devolveu —
  faltou qualquer um, ou os itens do pacote não batem com os do pedido → não
  mexe;
* 429 espera e segue; teto de chamadas e de trocas por rodada; erro numa conta
  ou num pedido não derruba os outros;
* no MÁXIMO 2 confirmações por pedido (por processo do worker), com 1 h ou
  mais entre elas — 16/11 (Eduardo, revisão): se a Amazon aceitar o POST e não
  refletir, se recusar (400) ou se o Bling reconfirmar "MelhorEnvio" por cima,
  o mesmo pedido NÃO leva um POST a cada 15 min por 72 h. Esgotou → loga
  `amazon_transportadora_desistiu` uma vez e o pedido nem é mais relido;
* depois do POST relê o pacote e loga o resultado;
* só no servidor (`ENV=production`): o worker de dev pode ter as credenciais da
  produção no banco local e escreveria na Amazon de verdade.

Interruptor: `AMAZON_TRANSPORTADORA_CORREIOS` no .env (Settings
`amazon_transportadora_correios`, ligado de fábrica — ver config.py, inclusive
como desligar: o worker precisa ser RECRIADO, `restart` não relê o .env).
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, Integration, IntegrationPlatform

logger = structlog.get_logger()

MARKETPLACE_BR = "A2Q3Y263D00KWC"
TRANSPORTADORA = "Correios"

# Pedidos alterados na Amazon nos últimos 2 dias (o Bling confirma ~20 s
# depois da etiqueta; a rodada de 15 min pega com folga).
JANELA_PEDIDOS = timedelta(days=2)
# Pacote despachado há mais que isto NUNCA é tocado (ver docstring do módulo).
JANELA_ENVIO = timedelta(hours=72)
# Relógio da Amazon um pouco à frente do nosso não tira o pacote da janela.
FOLGA_FUTURO = timedelta(hours=1)

# Rastreio dos Correios: 2 letras + 9 dígitos + "BR" (AD912266053BR, AE…BR).
RASTREIO_CORREIOS = re.compile(r"[A-Z]{2}\d{9}BR")

# Cota da SP-API: getOrders é 0,0167 req/s (rajada 20), getOrder/orderItems
# 0,5 req/s. Volume em 16/11: ~5–15 pedidos/dia por conta.
MAX_CHAMADAS_POR_RODADA = 150
MAX_TROCAS_POR_RODADA = 30
MAX_PAGINAS = 5
PAUSA_ENTRE_PEDIDOS_S = 1.0
PAUSA_ENTRE_PAGINAS_S = 1.0
# 429: espera 2 s, 4 s, 6 s e desiste DESSE pedido (ou da listagem da conta).
TENTATIVAS_429 = 3
ESPERA_429_S = 2.0
# A Amazon leva alguns segundos para refletir a confirmação na leitura.
ESPERA_RELEITURA_S = 4.0

# Pedido já conferido (estado final: já Correios, fora da janela, 2+ pacotes,
# rastreio de outra transportadora…) → LastUpdateDate em que foi conferido.
# Enquanto a Amazon não mexer no pedido, a rodada seguinte não relê o pacote.
# Por processo do worker: reinício só faz reler uma vez.
_CONFERIDOS: dict[str, str] = {}
_MAX_CONFERIDOS = 5000

# Confirmações (POST) já mandadas por pedido → instante de cada uma (o `agora`
# da rodada). Conta toda confirmação que pode ter chegado à Amazon — 2xx,
# recusa (400/5xx) ou erro de rede —; só o 429 persistente (a Amazon não
# processou) e o teto de chamadas antes do envio não contam. Por processo do
# worker: um reinício (deploy) dá no máximo mais 2 ao mesmo pedido.
_TENTATIVAS: dict[str, list[datetime]] = {}
MAX_TENTATIVAS_POR_PEDIDO = 2
INTERVALO_ENTRE_TENTATIVAS = timedelta(hours=1)
# Depois disto o pacote já saiu da janela de 72 h (nunca mais é tocado): o
# registro do pedido pode ser esquecido.
ESQUECER_TENTATIVAS = JANELA_ENVIO + timedelta(days=1)

Dormir = Callable[[float], Awaitable[Any]]
NumeroBling = Callable[[str], Awaitable[str | None]]


class _TetoDeChamadasError(Exception):
    """A rodada gastou `MAX_CHAMADAS_POR_RODADA` — o resto fica para a próxima."""


class _ContaError(Exception):
    """A conta não respondeu a listagem — pula a conta, segue para a próxima."""


@dataclass
class _Rodada:
    dormir: Dormir
    agora: datetime
    max_chamadas: int = MAX_CHAMADAS_POR_RODADA
    max_trocas: int = MAX_TROCAS_POR_RODADA
    chamadas: int = 0
    trocas: int = 0
    resultado: Counter = field(default_factory=Counter)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data(texto: Any) -> datetime | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _nome(valor: Any) -> str:
    """Transportadora / serviço do pacote: texto, ou objeto com o nome."""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        for k in ("name", "carrierName", "shippingServiceName", "code", "carrierCode"):
            v = valor.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def _chave_transportadora(valor: Any) -> str:
    return re.sub(r"[\s_.-]", "", _nome(valor)).lower()


def eh_melhor_envio(carrier: Any) -> bool:
    return _chave_transportadora(carrier) == "melhorenvio"


def eh_correios(carrier: Any) -> bool:
    return _chave_transportadora(carrier) == "correios"


def eh_rastreio_correios(rastreio: Any) -> bool:
    return isinstance(rastreio, str) and RASTREIO_CORREIOS.fullmatch(rastreio) is not None


async def _chamar(
    client: Any,
    rodada: _Rodada,
    metodo: str,
    caminho: str,
    *,
    params: dict | None = None,
    json: Any = None,
    conta: str = "",
) -> httpx.Response:
    """Uma chamada à SP-API contando no teto da rodada. 429 espera (2 s, 4 s,
    6 s) e tenta de novo; persistindo, devolve o 429 e quem chamou segue."""
    r: httpx.Response | None = None
    for tentativa in range(TENTATIVAS_429 + 1):
        if rodada.chamadas >= rodada.max_chamadas:
            raise _TetoDeChamadasError
        rodada.chamadas += 1
        r = await client._request(metodo, caminho, params=params, json=json)
        if r.status_code != 429:
            return r
        if tentativa < TENTATIVAS_429:
            espera = ESPERA_429_S * (tentativa + 1)
            logger.warning(
                "amazon_transportadora_429",
                conta=conta, caminho=caminho, metodo=metodo, espera_s=espera,
            )
            await rodada.dormir(espera)
    assert r is not None
    return r


def _corpo(resp: httpx.Response) -> dict:
    try:
        corpo = resp.json()
    except ValueError:
        return {}
    return corpo if isinstance(corpo, dict) else {}


async def _listar_pedidos(client: Any, conta: str, rodada: _Rodada) -> list[dict]:
    """Pedidos MFN "Shipped" alterados nos últimos 2 dias (v0 getOrders,
    `LastUpdatedAfter`). Nas páginas seguintes vai só `NextToken` +
    `MarketplaceIds` (a Amazon ignora o resto quando há token)."""
    base = {"MarketplaceIds": MARKETPLACE_BR}
    params: dict[str, Any] = {
        **base,
        "LastUpdatedAfter": _iso(rodada.agora - JANELA_PEDIDOS),
        "OrderStatuses": "Shipped",
        "FulfillmentChannels": "MFN",
        "MaxResultsPerPage": 100,
    }
    pedidos: list[dict] = []
    for pagina in range(MAX_PAGINAS):
        if pagina:
            await rodada.dormir(PAUSA_ENTRE_PAGINAS_S)
        r = await _chamar(client, rodada, "GET", "/orders/v0/orders", params=params, conta=conta)
        if r.status_code != 200:
            raise _ContaError(f"getOrders HTTP {r.status_code}: {(r.text or '')[:200]}")
        payload = _corpo(r).get("payload")
        payload = payload if isinstance(payload, dict) else {}
        pedidos.extend(o for o in payload.get("Orders") or [] if isinstance(o, dict))
        token = payload.get("NextToken")
        if not token:
            break
        params = {**base, "NextToken": token}
    return pedidos


async def _ler_pacotes(
    client: Any, conta: str, pedido: str, rodada: _Rodada
) -> list[dict] | None:
    """Pacotes do pedido (Orders v2026-01-01, `includedData=PACKAGES`). None =
    a Amazon não respondeu (429 persistente, 4xx/5xx) — o pedido fica para a
    próxima rodada."""
    r = await _chamar(
        client, rodada, "GET", f"/orders/2026-01-01/orders/{pedido}",
        params={"includedData": "PACKAGES"}, conta=conta,
    )
    if r.status_code != 200:
        logger.warning(
            "amazon_transportadora_pacotes_falhou",
            conta=conta, pedido=pedido, http=r.status_code, corpo=(r.text or "")[:200],
        )
        return None
    j = _corpo(r)
    od = j.get("order") if isinstance(j.get("order"), dict) else j.get("payload")
    od = od if isinstance(od, dict) else j
    devolvido = od.get("orderId") or od.get("amazonOrderId")
    if devolvido is not None and str(devolvido) != pedido:
        # Resposta de OUTRO pedido: nunca confirmar em cima dela.
        logger.warning(
            "amazon_transportadora_pedido_trocado", conta=conta, pedido=pedido,
            devolvido=str(devolvido),
        )
        return None
    pacotes = od.get("packages")
    return [p for p in pacotes if isinstance(p, dict)] if isinstance(pacotes, list) else []


async def _itens_do_pedido(
    client: Any, conta: str, pedido: str, rodada: _Rodada
) -> list[dict] | None:
    """`[{"orderItemId", "quantity"}]` do pedido (v0 getOrderItems), como no
    helper que funcionou em 16/11. None = não dá para ter certeza dos itens
    (sem resposta, 2ª página, item sem id/quantidade, enviado ≠ pedido)."""
    r = await _chamar(client, rodada, "GET", f"/orders/v0/orders/{pedido}/orderItems", conta=conta)
    if r.status_code != 200:
        logger.warning(
            "amazon_transportadora_itens_falhou",
            conta=conta, pedido=pedido, http=r.status_code, corpo=(r.text or "")[:200],
        )
        return None
    payload = _corpo(r).get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("NextToken"):
        return None
    itens: list[dict] = []
    for it in payload.get("OrderItems") or []:
        if not isinstance(it, dict):
            return None
        item_id = it.get("OrderItemId")
        try:
            qtd = int(it.get("QuantityOrdered"))
        except (TypeError, ValueError):
            return None
        enviada = it.get("QuantityShipped")
        if not item_id or qtd <= 0 or (enviada is not None and str(enviada) != str(qtd)):
            return None
        itens.append({"orderItemId": str(item_id), "quantity": qtd})
    return itens or None


def _itens_do_pacote(pacote: dict) -> dict[str, int] | None:
    """Itens que a Amazon guardou no pacote (`packageItems`), quando vêm num
    formato que dá para ler; None = sem essa informação (vale o getOrderItems)."""
    brutos = pacote.get("packageItems")
    if not isinstance(brutos, list) or not brutos:
        return None
    out: dict[str, int] = {}
    for it in brutos:
        if not isinstance(it, dict):
            return None
        item_id = it.get("orderItemId")
        try:
            qtd = int(it.get("quantity"))
        except (TypeError, ValueError):
            return None
        if not item_id:
            return None
        out[str(item_id)] = out.get(str(item_id), 0) + qtd
    return out


def corpo_confirmacao(pacote: dict, itens: list[dict]) -> dict:
    """O corpo EXATO que funcionou em 16/11: mesmo pacote, rastreio, serviço,
    data e itens — só a transportadora vira "Correios"."""
    return {
        "marketplaceId": MARKETPLACE_BR,
        "packageDetail": {
            "packageReferenceId": str(pacote["packageReferenceId"]),
            "carrierCode": TRANSPORTADORA,
            "carrierName": TRANSPORTADORA,
            "shippingMethod": _nome(pacote.get("shippingService")),
            "trackingNumber": pacote["trackingNumber"],
            "shipDate": pacote["shipTime"],
            "orderItems": itens,
        },
    }


def _conferido(pedido: str, ultima: str | None, cache: dict[str, str]) -> None:
    if not ultima:
        return
    if len(cache) >= _MAX_CONFERIDOS:
        cache.clear()
    cache[pedido] = ultima


def _podar_tentativas(tentativas: dict[str, list[datetime]], agora: datetime) -> None:
    velhos = [
        pedido for pedido, ts in tentativas.items()
        if not ts or agora - ts[-1] > ESQUECER_TENTATIVAS
    ]
    for pedido in velhos:
        del tentativas[pedido]


def _desfazer_tentativa(tentativas: dict[str, list[datetime]], pedido: str) -> None:
    """O POST não foi processado pela Amazon: a tentativa não conta."""
    feitas = tentativas.get(pedido)
    if feitas:
        feitas.pop()
    if not feitas:
        tentativas.pop(pedido, None)


async def _processar_pedido(
    client: Any,
    conta: str,
    ordem: dict,
    rodada: _Rodada,
    cache: dict[str, str],
    numero_bling: NumeroBling | None,
    tentativas: dict[str, list[datetime]],
) -> str:
    """Um pedido. Devolve o resultado (contado no resumo da rodada)."""
    pedido = str(ordem.get("AmazonOrderId") or "").strip()
    if not pedido:
        return "sem_id"
    if str(ordem.get("FulfillmentChannel") or "").upper() != "MFN":
        return "afn"
    if ordem.get("EasyShipShipmentStatus") not in (None, ""):
        return "easyship"
    if str(ordem.get("OrderStatus") or "") != "Shipped":
        return "nao_enviado"
    ultima = ordem.get("LastUpdateDate")
    if ultima and cache.get(pedido) == ultima:
        return "ja_conferido"
    if len(tentativas.get(pedido) or []) >= MAX_TENTATIVAS_POR_PEDIDO:
        # Já levou as confirmações que podia (o aviso saiu na última): nem
        # relê o pacote. Fica só a contagem no resumo da rodada.
        return "teto_por_pedido"

    pacotes = await _ler_pacotes(client, conta, pedido, rodada)
    await rodada.dormir(PAUSA_ENTRE_PEDIDOS_S)
    if pacotes is None:
        return "sem_resposta"
    if not pacotes:
        return "sem_pacote"
    if len(pacotes) != 1:
        logger.warning(
            "amazon_transportadora_varios_pacotes",
            conta=conta, pedido=pedido, pacotes=len(pacotes),
            transportadoras=[_nome(p.get("carrier")) for p in pacotes],
        )
        _conferido(pedido, ultima, cache)
        return "varios_pacotes"

    p = pacotes[0]
    carrier = p.get("carrier")
    rastreio = p.get("trackingNumber")
    if eh_correios(carrier):
        _conferido(pedido, ultima, cache)
        return "ja_correios"
    if not eh_melhor_envio(carrier):
        _conferido(pedido, ultima, cache)
        return "outra_transportadora"
    if not eh_rastreio_correios(rastreio):
        logger.info(
            "amazon_transportadora_rastreio_nao_correios",
            conta=conta, pedido=pedido, rastreio=rastreio,
        )
        _conferido(pedido, ultima, cache)
        return "rastreio_nao_correios"
    envio = _data(p.get("shipTime"))
    if envio is None:
        logger.warning(
            "amazon_transportadora_sem_data_envio", conta=conta, pedido=pedido, rastreio=rastreio,
        )
        return "dados_incompletos"
    if not (rodada.agora - JANELA_ENVIO <= envio <= rodada.agora + FOLGA_FUTURO):
        logger.info(
            "amazon_transportadora_fora_da_janela",
            conta=conta, pedido=pedido, rastreio=rastreio, ship_time=p.get("shipTime"),
        )
        _conferido(pedido, ultima, cache)
        return "fora_da_janela"
    ref = str(p.get("packageReferenceId") or "").strip()
    if not ref or not _nome(p.get("shippingService")).strip():
        # Sem o pacote ou o serviço de origem, a confirmação criaria outro
        # pacote ou mudaria o serviço — não mexe.
        logger.warning(
            "amazon_transportadora_dados_incompletos",
            conta=conta, pedido=pedido, rastreio=rastreio,
            pacote=p.get("packageReferenceId"), servico=p.get("shippingService"),
        )
        return "dados_incompletos"
    feitas = tentativas.get(pedido) or []
    if feitas and rodada.agora - feitas[-1] < INTERVALO_ENTRE_TENTATIVAS:
        # Confirmado há menos de 1 h e ainda "MelhorEnvio": dá tempo à Amazon
        # (e ao Bling) antes da 2ª e última confirmação.
        return "aguardando_nova_tentativa"
    if rodada.trocas >= rodada.max_trocas:
        return "teto_trocas"

    itens = await _itens_do_pedido(client, conta, pedido, rodada)
    if itens is None:
        logger.warning("amazon_transportadora_itens_incertos", conta=conta, pedido=pedido)
        return "itens_incertos"
    do_pacote = _itens_do_pacote(p)
    if do_pacote is not None and do_pacote != {i["orderItemId"]: i["quantity"] for i in itens}:
        logger.warning(
            "amazon_transportadora_itens_divergentes",
            conta=conta, pedido=pedido, pacote=do_pacote, pedido_itens=itens,
        )
        return "itens_divergentes"

    corpo = corpo_confirmacao(p, itens)
    bling: str | None = None
    if numero_bling is not None:
        try:
            bling = await numero_bling(pedido)
        except Exception as e:  # noqa: BLE001 — o número do Bling é só pro log
            logger.info("amazon_transportadora_bling_falhou", pedido=pedido, err=str(e)[:200])
    rodada.trocas += 1
    # A tentativa é registrada ANTES do POST: se a chamada cair no meio (rede,
    # timeout), a Amazon pode ter recebido — conta igual.
    registro = tentativas.setdefault(pedido, [])
    registro.append(rodada.agora)
    tentativa = len(registro)
    ultima_tentativa = tentativa >= MAX_TENTATIVAS_POR_PEDIDO
    try:
        r = await _chamar(
            client, rodada, "POST", f"/orders/v0/orders/{pedido}/shipmentConfirmation",
            json=corpo, conta=conta,
        )
    except _TetoDeChamadasError:
        _desfazer_tentativa(tentativas, pedido)  # o POST nem saiu
        raise
    log = {
        "conta": conta,
        "pedido": pedido,
        "pedido_bling": bling,
        "rastreio": rastreio,
        "servico": corpo["packageDetail"]["shippingMethod"],
        "ship_time": p.get("shipTime"),
        "nivel": ordem.get("ShipServiceLevel"),
        "transportadora_antes": _nome(carrier),
        "http": r.status_code,
        "tentativa": tentativa,
    }

    def _desistiu(motivo: str) -> None:
        # Uma vez por pedido: é a última confirmação que ele leva.
        logger.error(
            "amazon_transportadora_desistiu",
            conta=conta, pedido=pedido, pedido_bling=bling, rastreio=rastreio,
            tentativas=tentativa, motivo=motivo,
        )

    if r.status_code == 429:
        # 429 mesmo depois das esperas: a Amazon não processou — não conta.
        _desfazer_tentativa(tentativas, pedido)
        logger.warning("amazon_transportadora_correios_429", **log)
        return "confirmacao_429"
    if not 200 <= r.status_code < 300:
        logger.error(
            "amazon_transportadora_correios_falhou", corpo=(r.text or "")[:300], **log
        )
        if ultima_tentativa:
            _desistiu("recusada")
        return "erro_confirmacao"
    logger.info("amazon_transportadora_correios_trocou", **log)

    # Releitura: confirma que a Amazon gravou "Correios". Se não pegou, a 2ª
    # (e última) confirmação só sai 1 h depois — ver `MAX_TENTATIVAS_POR_PEDIDO`.
    await rodada.dormir(ESPERA_RELEITURA_S)
    try:
        depois = await _ler_pacotes(client, conta, pedido, rodada)
    except _TetoDeChamadasError:
        depois = None
    p2 = depois[0] if depois and len(depois) == 1 else {}
    nome_depois = _nome(p2.get("carrier")) if p2 else None
    if p2 and eh_correios(p2.get("carrier")) and p2.get("trackingNumber") == rastreio:
        logger.info(
            "amazon_transportadora_correios_conferido",
            conta=conta, pedido=pedido, pedido_bling=bling, rastreio=rastreio,
            transportadora_depois=nome_depois,
        )
    else:
        logger.warning(
            "amazon_transportadora_correios_nao_refletiu",
            conta=conta, pedido=pedido, pedido_bling=bling, rastreio=rastreio,
            transportadora_depois=nome_depois,
            rastreio_depois=p2.get("trackingNumber") if p2 else None,
            pacotes_depois=len(depois) if depois is not None else None,
            tentativa=tentativa,
        )
        if ultima_tentativa:
            _desistiu("nao_refletiu")
    return "trocado"


async def processar_conta(
    conta: str,
    client: Any,
    rodada: _Rodada,
    *,
    cache: dict[str, str],
    numero_bling: NumeroBling | None = None,
    tentativas: dict[str, list[datetime]] | None = None,
) -> None:
    """Uma conta. `_ContaError`/`_TetoDeChamadasError` sobem; erro de UM pedido não."""
    mkt = str(getattr(client, "marketplace_id", "") or "")
    if mkt != MARKETPLACE_BR:
        logger.info("amazon_transportadora_conta_fora_do_br", conta=conta, marketplace=mkt)
        rodada.resultado["conta_fora_do_br"] += 1
        return
    tentativas = _TENTATIVAS if tentativas is None else tentativas
    for ordem in await _listar_pedidos(client, conta, rodada):
        try:
            res = await _processar_pedido(
                client, conta, ordem, rodada, cache, numero_bling, tentativas
            )
        except _TetoDeChamadasError:
            raise
        except Exception as e:  # noqa: BLE001 — um pedido não derruba os outros
            logger.warning(
                "amazon_transportadora_pedido_falhou",
                conta=conta, pedido=ordem.get("AmazonOrderId"), err=str(e)[:300],
            )
            res = "erro"
        rodada.resultado[res] += 1


async def processar(
    contas: list[tuple[str, Any]],
    *,
    agora: datetime | None = None,
    dormir: Dormir = asyncio.sleep,
    cache: dict[str, str] | None = None,
    tentativas: dict[str, list[datetime]] | None = None,
    numero_bling: NumeroBling | None = None,
    max_chamadas: int = MAX_CHAMADAS_POR_RODADA,
    max_trocas: int = MAX_TROCAS_POR_RODADA,
) -> dict:
    """Uma rodada sobre `[(nome da conta, cliente SP-API)]`, uma conta por vez.
    Conta que falha (listagem fora do 200, 429 persistente, exceção) é contada
    e a rodada segue com as outras; o teto de chamadas encerra a rodada."""
    rodada = _Rodada(
        dormir=dormir, agora=agora or datetime.now(UTC),
        max_chamadas=max_chamadas, max_trocas=max_trocas,
    )
    cache = _CONFERIDOS if cache is None else cache
    tentativas = _TENTATIVAS if tentativas is None else tentativas
    _podar_tentativas(tentativas, rodada.agora)
    contas_ok: list[str] = []
    contas_erro: list[str] = []
    teto = False
    for conta, client in contas:
        try:
            await processar_conta(
                conta, client, rodada,
                cache=cache, numero_bling=numero_bling, tentativas=tentativas,
            )
            contas_ok.append(conta)
        except _TetoDeChamadasError:
            logger.warning(
                "amazon_transportadora_teto_de_chamadas", conta=conta, chamadas=rodada.chamadas
            )
            teto = True
            break
        except Exception as e:  # noqa: BLE001 — uma conta não derruba as outras
            logger.warning("amazon_transportadora_conta_falhou", conta=conta, err=str(e)[:300])
            contas_erro.append(conta)
    return {
        "contas_ok": contas_ok,
        "contas_erro": contas_erro,
        "teto_de_chamadas": teto,
        "chamadas": rodada.chamadas,
        "trocados": rodada.resultado.get("trocado", 0),
        "resultado": dict(rodada.resultado),
    }


async def rodada(session: AsyncSession) -> dict:
    """O job do worker: todas as contas Amazon não arquivadas. No-op com
    `AMAZON_TRANSPORTADORA_CORREIOS=false` (worker recriado — ver config.py) e
    fora do servidor."""
    s = get_settings()
    if not s.amazon_transportadora_correios:
        return {"desligado": True}
    if not s.is_prod:
        # 16/11 (revisão): o worker de dev (docker-compose.override) também
        # roda este cron, e o banco local pode ter as credenciais Amazon da
        # produção — escreveria na Amazon de verdade. Só no servidor, como a
        # conferência de preços (`pricing.conferencia.conferencia_liberada`).
        return {"so_no_servidor": True}
    # Tardio: logistica_amazon puxa a Logística inteira; o worker só paga
    # quando o interruptor está ligado.
    from app.services.logistica_amazon import _build_amazon_client

    integracoes = (
        await session.execute(
            select(Integration)
            .where(
                Integration.platform == IntegrationPlatform.AMAZON,
                Integration.archived_at.is_(None),
            )
            .order_by(Integration.name)
        )
    ).scalars().all()
    contas: list[tuple[str, Any]] = []
    falhas: list[str] = []
    for integ in integracoes:
        conta = (integ.name or "").strip().lower() or str(integ.id)
        try:
            # Token renovado é gravado nesta sessão (o job commita no fim).
            contas.append((conta, _build_amazon_client(session, integ)))
        except Exception as e:  # noqa: BLE001 — credencial quebrada não para as outras
            logger.warning("amazon_transportadora_conta_sem_cliente", conta=conta, err=str(e)[:200])
            falhas.append(conta)

    async def _numero_bling(pedido: str) -> str | None:
        return (
            await session.execute(
                select(BlingOrder.numero).where(BlingOrder.numeroloja == pedido).limit(1)
            )
        ).scalar_one_or_none()

    resumo = await processar(contas, numero_bling=_numero_bling)
    resumo["contas_erro"] = falhas + resumo["contas_erro"]
    return resumo
