"""Status Plataforma da Temu na Logística, pela Open API (26/11/2026).

Piloto da Barbosa — a única loja Temu com a API ligada (integração do
Marketing, app "Stock Sync Hub", vendedor local BR). Pedido do Vinicius depois
do 306833 (Anjun levou o pacote pra Rio Negro/SC, o rastreio parou em 07/11 e
a Temu reembolsou a cliente em 18/11, um dia depois do prazo de entrega): a
Temu entra na Logística como as outras, com status, rastreio e o prazo de
entrega com aviso (ver `logistica_amazon_avisos`).

Duas consultas por pedido (só leitura — o cliente da Temu recusa o resto):

- `bg.order.detail.v2.get` {parentOrderSn}: status do pedido
  (`parentOrderStatus` → TEMU_ORDER_STATUS), prazo de entrega
  (`latestDeliveryTime`) e o número do pacote (`packageSnInfo`);
- `temu.track.trackinginfo.get` {packageSn}: código da transportadora
  (`trackingNum`) e os eventos (`logisticsStatus` + `statusText` +
  `logisticsUpdatedAt`). O último evento vira o "Status do envio" da
  assinatura e a Localização.

Gravado na linha: `meli_status` {order_status, logistics_status} (assinatura
em `logistica_rules.assinatura_temu`), `status_datas` (hora do evento do
rastreio = data oficial), `rastreio`, `localizacao` (+ `localizacao_at` = hora
do evento), `prazo_entrega_amazon` (a coluna da data máxima da Amazon serve
também à Temu: mesma regra — passou, a plataforma reembolsa) e `entregue_em`.

Conta sem integração Temu (Eron, JLAS, ATV) não é lida: a ingestão nem põe
essas lojas na Logística (`contas_com_api`). Best-effort: pedido que a Temu
não devolve fica como estava (não derruba o lote).

Revisão de 26/11 (dois revisores céticos antes de publicar):
- pedido com vários pacotes lê todos; entregue só com todos entregues;
- rastreio que falha (429, timeout) NÃO apaga o "Status do envio" anterior;
- `coleta_pela_api` serve o `temu_rastreio` (21→15 no Bling) com a lista
  POSITIVA de coleta (`logistica_rules.TEMU_LOGISTICS_COLETADO`);
- `temu_viva`: a linha viva entra sempre no motor (nunca fica esquecida por
  uma regra que a esconde), com teto por rodada e parada na 1ª falha de rede.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules
from app.services.marketplaces.temu import CLASSE_CONTA, CLASSE_RETRYABLE, TemuClient, TemuErro

logger = structlog.get_logger()

_TEMU_PLATAFORMAS = logistica_rules._TEMU_PLATAFORMAS
API_PEDIDO = "bg.order.detail.v2.get"
API_RASTREIO = "temu.track.trackinginfo.get"
# Pós-venda (26/11/2026, pra a regra da aba Status mandar pras Devoluções):
# a lista dá o tipo e a situação do caso de cada pedido; o detalhe dá o motivo —
# na Temu "reembolso sem devolução" também é AJUSTE DE PREÇO (price match /
# price adjustment: reembolso parcial quando o preço cai), que não é devolução.
API_POS_VENDA_LISTA = "bg.aftersales.parentaftersales.list.get"
API_POS_VENDA_DETALHE = "temu.aftersales.parentaftersales.detail.get"
# A lista é por data de criação do caso, em janelas de 30 dias (o que a sonda
# provou que a Temu aceita), até 90 dias pra trás.
JANELAS_POS_VENDA = 3
_DIAS_JANELA = 30
_PAGINA_POS_VENDA = 50
# Motivos que são ajuste de preço (vistos em 26/11: 40 "Price adjustment refund",
# 59 "price match refund") — e qualquer motivo com "price" no texto.
_MOTIVOS_AJUSTE_PRECO = frozenset({40, 59})

# Uma linha por vez, com pausa: na 1ª rodada em produção (26/11 11:05) 3 em
# paralelo levaram "Temu 4000004: too frequent requests" depois de 14 linhas — e
# a mesma integração manda o estoque da Barbosa (Marketing). A Temu não publica
# o limite do vendedor local.
_CONCURRENCY = 1
PAUSA_ENTRE_LINHAS = 0.6  # s
# O motor de 5 min passa as pendentes do painel toda rodada. O rastreio da
# Anjun/J&T anda poucas vezes por dia: reler a mesma linha antes disto só gasta
# chamada. Linha nunca lida (status vazio) lê na hora.
RELER_APOS = timedelta(minutes=30)
# Teto de linhas por rodada do motor (as menos recém-lidas primeiro): com a
# Temu lenta (20 s por chamada) a rodada não pode passar dos 900 s do job.
TETO_POR_RODADA = 30  # 30 × (2–3 chamadas + pausa) cabe no teto de 120 s do motor
# Linha "viva" (ainda pode mudar) que o motor relê mesmo escondida por regra.
JANELA_VIVA = timedelta(days=60)
_TEMU_ORDER_FIM = frozenset({"RECEIPTED", "CANCELED"})
_BLING_FIM = frozenset({"cancelado", "resolvido", "perdimento", "entregue"})

_BRT = ZoneInfo("America/Sao_Paulo")


class TemuEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha Temu (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _momento(epoch: Any) -> datetime | None:
    """Segundos (ou milissegundos) da Temu → datetime UTC."""
    n = _int(epoch)
    if not n or n <= 0:
        return None
    if n > 100_000_000_000:
        n //= 1000
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _dia_brt(epoch: Any) -> date | None:
    m = _momento(epoch)
    return m.astimezone(_BRT).date() if m else None


def _pacotes(order_list: list[dict]) -> list[str]:
    """Todos os `packageSn` do pedido (sem repetir, na ordem)."""
    vistos: dict[str, None] = {}
    for o in order_list or []:
        for p in o.get("packageSnInfo") or []:
            sn = str((p or {}).get("packageSn") or "").strip()
            if sn:
                vistos[sn] = None
    return list(vistos)


_DESCONHECIDOS_AVISADOS: set[str] = set()


def _status_do_evento(e: dict) -> str:
    log = logistica_rules.temu_status_logistico(e.get("logisticsStatus"))
    if (
        log
        and log not in logistica_rules.TEMU_LOGISTICS_COLETADO
        and log not in _DESCONHECIDOS_AVISADOS
    ):
        # Status novo da Temu: não conta como coleta até alguém olhar.
        _DESCONHECIDOS_AVISADOS.add(log)
        logger.info("logistica_temu_status_desconhecido", status=log, texto=e.get("statusText"))
    return log


def ultimo_evento(eventos: list[dict]) -> dict | None:
    """O evento mais NOVO (a Temu manda do mais velho pro mais novo, mas não
    custa ordenar pela hora). Sem hora, vale a ordem da lista."""
    com_hora = [e for e in eventos or [] if isinstance(e, dict)]
    if not com_hora:
        return None
    return max(
        enumerate(com_hora),
        key=lambda ie: (_int(ie[1].get("logisticsUpdatedAt")) or 0, ie[0]),
    )[1]


def coleta(rastreios: list[dict]) -> datetime | None:
    """Hora da coleta: o evento MAIS ANTIGO, entre todos os pacotes, cujo status
    prova que a transportadora pegou o pacote. None se nenhum prova."""
    horas = [
        _momento(e.get("logisticsUpdatedAt"))
        for r in rastreios or []
        for e in (r or {}).get("trackingInfo") or []
        if isinstance(e, dict) and _status_do_evento(e) in logistica_rules.TEMU_LOGISTICS_COLETADO
    ]
    datadas = [h for h in horas if h is not None]
    return min(datadas) if datadas else None


def _envio(rastreios: list[dict]) -> dict[str, Any]:
    """Status do envio de um pedido com N pacotes: entregue só quando TODOS
    estão; senão o status do pacote menos adiantado (falha primeiro). A
    Localização é o evento mais NOVO de qualquer pacote."""
    ultimos = []
    for r in rastreios or []:
        ult = ultimo_evento(list((r or {}).get("trackingInfo") or []))
        if ult is not None:
            ultimos.append(ult)
    out: dict[str, Any] = {
        "log": "",
        "quando": None,
        "texto": "",
        "texto_em": None,
        "entregue_em": None,
    }
    if not ultimos:
        return out
    novo = max(ultimos, key=lambda e: _int(e.get("logisticsUpdatedAt")) or 0)
    out["texto"] = str(novo.get("statusText") or "").strip()
    out["texto_em"] = _momento(novo.get("logisticsUpdatedAt"))
    status = [(_status_do_evento(e), e) for e in ultimos]
    if len(status) == len(rastreios) and all(
        log in logistica_rules._TEMU_LOGISTICS_ENTREGUE for log, _ in status
    ):
        out["log"] = "DELIVERED"
        out["quando"] = max(
            (_momento(e.get("logisticsUpdatedAt")) for _, e in status),
            key=lambda d: d or datetime.min.replace(tzinfo=UTC),
        )
        out["entregue_em"] = out["quando"]
        return out
    abertos = [(log, e) for log, e in status if log not in logistica_rules._TEMU_LOGISTICS_ENTREGUE]
    if not abertos:
        # Os pacotes com evento estão entregues e algum ainda não tem evento:
        # o menos adiantado é esse — sem status do envio por enquanto.
        return out
    log, e = min(abertos, key=lambda le: logistica_rules.TEMU_LOGISTICS_ORDEM.get(le[0], 0))
    out["log"] = log
    out["quando"] = _momento(e.get("logisticsUpdatedAt"))
    return out


def montar(
    detalhe: dict,
    rastreio: dict | list[dict] | None,
    *,
    rastreio_falhou: bool = False,
) -> dict[str, Any]:
    """Junta o que as consultas devolveram no formato que `aplicar` grava.
    `rastreio` é a resposta de um pacote ou a lista de todos. PURA (testável
    sem rede)."""
    pm = detalhe.get("parentOrderMap") or {}
    codigo = _int(pm.get("parentOrderStatus"))
    order_status = logistica_rules.TEMU_ORDER_STATUS.get(codigo) if codigo is not None else None
    if order_status is None and codigo is not None:
        order_status = str(codigo)
    meli: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    if order_status:
        meli["order_status"] = order_status
    out: dict[str, Any] = {
        "meli_status": meli,
        "datas": datas,
        "prazo_entrega": _dia_brt(pm.get("latestDeliveryTime")),
        "rastreio": None,
        "localizacao": None,
        "localizacao_at": None,
        "entregue_em": None,
        # Algum rastreio não respondeu: `aplicar` mantém o envio que já tinha.
        "rastreio_falhou": rastreio_falhou,
    }
    if order_status == "SHIPPED":
        # Na Temu o pedido vira "Enviado" na COMPRA da etiqueta (parentShippingTime).
        logistica_datas.propor(
            datas,
            "order_status",
            _momento(pm.get("parentShippingTime")),
            logistica_datas.FONTE_PLATAFORMA,
        )
    rastreios = rastreio if isinstance(rastreio, list) else ([rastreio] if rastreio else [])
    codigos = [
        str((r or {}).get("trackingNum") or "").strip()
        for r in rastreios
        if str((r or {}).get("trackingNum") or "").strip()
    ]
    out["rastreio"] = ", ".join(dict.fromkeys(codigos)) or None
    envio = _envio(rastreios)
    if envio["log"]:
        meli["logistics_status"] = envio["log"]
        logistica_datas.propor(
            datas, "logistics_status", envio["quando"], logistica_datas.FONTE_PLATAFORMA
        )
    if envio["texto"]:
        out["localizacao"] = envio["texto"]
        out["localizacao_at"] = envio["texto_em"]
    out["entregue_em"] = envio["entregue_em"]
    return out


async def ler_pedido(client: TemuClient, parent_order_sn: str) -> dict[str, Any]:
    """Detalhe do pedido + o rastreio de cada pacote. Erro no detalhe sobe
    (TemuErro); erro num rastreio vira `rastreio_falhou` (o pedido segue)."""
    det = await client._chamar(API_PEDIDO, {"parentOrderSn": parent_order_sn})  # noqa: SLF001
    detalhe = det.get("result") or {}
    pacotes = _pacotes(detalhe.get("orderList") or [])
    rastreios: list[dict] = []
    falhou = False
    for pacote in pacotes:
        try:
            r = await client._chamar(API_RASTREIO, {"packageSn": pacote})  # noqa: SLF001
            rastreios.append(r.get("result") or {})
        except TemuErro as e:
            falhou = True
            logger.info("logistica_temu_rastreio_falhou", pedido=parent_order_sn, err=str(e)[:160])
    return {
        "detalhe": detalhe,
        "pacotes": pacotes,
        "rastreios": rastreios,
        "rastreio_falhou": falhou,
    }


def montar_caso(caso: dict, detalhe: dict | None) -> dict[str, str]:
    """Campos do caso de pós-venda pro `meli_status` (PURA). `caso` = item da
    lista; `detalhe` = resposta do detalhe (None se não deu pra ler)."""
    det = detalhe or {}
    tipo_cod = _int(det.get("afterSalesType", caso.get("afterSalesType")))
    status_cod = _int(det.get("parentAfterSalesStatus", caso.get("parentAfterSalesStatus")))
    itens = list(det.get("afterSalesList") or [])
    motivos = [_int(i.get("afterSalesReasonCode")) for i in itens]
    textos = " ".join(str(i.get("afterSalesReasonDesc") or "") for i in itens).strip()
    ajuste = any(m in _MOTIVOS_AJUSTE_PRECO for m in motivos if m is not None) or (
        "price" in textos.lower()
    )
    if ajuste:
        tipo = "PRICE_ADJUST"
    elif tipo_cod == 1:
        tipo = "REFUND"
    elif tipo_cod is not None and caso.get("returnDeliveryType") is not None:
        tipo = "RETURN_AND_REFUND"
    elif tipo_cod is not None:
        tipo = f"TEMU_TIPO_{tipo_cod}"
    else:
        tipo = ""
    out: dict[str, str] = {}
    if tipo:
        out["return_type"] = tipo
    if status_cod is not None:
        out["return_status"] = f"TEMU_{status_cod}"
    operacoes = det.get("availableOperateList", caso.get("availableOperateList")) or []
    if operacoes:
        out["return_pending"] = "1"  # a Temu espera uma ação da loja
    if textos:
        out["return_reason"] = textos[:120]
    total = 0
    for i in itens:
        v = _int((i.get("applyRefundAmount") or {}).get("amount"))
        total += v or 0
    if total:
        out["return_refunded"] = f"{total / 100:.2f}"
    return out


async def casos_pos_venda(
    client: TemuClient, *, agora: datetime | None = None
) -> dict[str, dict]:
    """{parentOrderSn: caso mais novo} dos últimos 90 dias. TemuErro sobe."""
    fim = int((agora or datetime.now(UTC)).timestamp())
    casos: dict[str, dict] = {}
    for j in range(JANELAS_POS_VENDA):
        ate = fim - j * _DIAS_JANELA * 86400
        desde = ate - _DIAS_JANELA * 86400
        pagina = 1
        while True:
            r = await client._chamar(  # noqa: SLF001
                API_POS_VENDA_LISTA,
                {
                    "pageNumber": pagina,
                    "pageSize": _PAGINA_POS_VENDA,
                    "createAtStart": desde,
                    "createAtEnd": ate,
                },
            )
            res = r.get("result") or {}
            dados = list(res.get("data") or [])
            for c in dados:
                po = str(c.get("parentOrderSn") or "").strip()
                if not po:
                    continue
                atual = casos.get(po)
                if atual is None or (_int(c.get("createAt")) or 0) > (
                    _int(atual.get("createAt")) or 0
                ):
                    casos[po] = c
            total = _int(res.get("total")) or 0
            if not dados or pagina * _PAGINA_POS_VENDA >= total or pagina >= 10:
                break
            pagina += 1
    return casos


async def caso_do_pedido(client: TemuClient, caso: dict) -> dict[str, str]:
    """Lê o detalhe do caso (motivo, valor, ação pendente) e monta os campos.
    Detalhe fora do ar: só o que a lista já disse."""
    try:
        r = await client._chamar(  # noqa: SLF001
            API_POS_VENDA_DETALHE,
            {
                "parentAfterSalesSn": caso.get("parentAfterSalesSn"),
                "parentOrderSn": caso.get("parentOrderSn"),
            },
        )
        detalhe = r.get("result") or {}
    except TemuErro as e:
        logger.info(
            "logistica_temu_pos_venda_detalhe_falhou",
            pedido=caso.get("parentOrderSn"),
            err=str(e)[:160],
        )
        detalhe = None
    return montar_caso(caso, detalhe)


async def build_enrichment(client: TemuClient, parent_order_sn: str) -> dict[str, Any]:
    leitura = await ler_pedido(client, parent_order_sn)
    return montar(
        leitura["detalhe"], leitura["rastreios"], rastreio_falhou=leitura["rastreio_falhou"]
    )


COLETA_SAIU = "saiu"
COLETA_NAO_SAIU = "nao_saiu"
COLETA_SEM_RESPOSTA = "sem_resposta"


async def coleta_pela_api(client: TemuClient, parent_order_sn: str) -> tuple[str, datetime | None]:
    """Pro `temu_rastreio` (21→15 no Bling): (COLETA_SAIU, hora da coleta),
    (COLETA_NAO_SAIU, None) ou (COLETA_SEM_RESPOSTA, None) — rastreio que não
    respondeu ou evento de coleta sem hora: quem chama cai no 17track. Erro no
    detalhe do pedido sobe (TemuErro)."""
    leitura = await ler_pedido(client, parent_order_sn)
    if leitura["rastreio_falhou"]:
        return COLETA_SEM_RESPOSTA, None
    hora = coleta(leitura["rastreios"])
    if hora is not None:
        return COLETA_SAIU, hora
    tem_coleta_sem_hora = any(
        _status_do_evento(e) in logistica_rules.TEMU_LOGISTICS_COLETADO
        for r in leitura["rastreios"]
        for e in (r or {}).get("trackingInfo") or []
        if isinstance(e, dict)
    )
    if tem_coleta_sem_hora:
        return COLETA_SEM_RESPOSTA, None
    return COLETA_NAO_SAIU, None


def temu_viva(row: Logistica, *, hoje: date | None = None) -> bool:
    """Linha Temu que ainda pode mudar (não entregue, não cancelada, Bling não
    encerrado, dos últimos 60 dias): o motor relê mesmo quando uma regra a
    esconde — senão a entrega nunca chega e o aviso de prazo sai errado."""
    if (row.plataforma or "").strip().lower() not in _TEMU_PLATAFORMAS:
        return False
    if row.entregue_em is not None:
        return False
    ordem = str((row.meli_status or {}).get("order_status") or "").strip().upper()
    if ordem in _TEMU_ORDER_FIM:
        return False
    if (row.status_bling or "").strip().lower() in _BLING_FIM:
        return False
    hoje = hoje or datetime.now(_BRT).date()
    return row.data is None or row.data >= hoje - JANELA_VIVA


async def _temu_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração Temu ativa cuja `name` casa (trim+lower) com a `conta` da linha
    (store_info.account_name "barbosa" × integração "Barbosa")."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform.TEMU,
                    Integration.archived_at.is_(None),
                    # A Conciliação tem "âncoras" Temu (status "ancora", arquivadas).
                    Integration.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


async def contas_com_api(session: AsyncSession) -> list[str]:
    """Contas (minúsculas) das lojas Temu com a Open API ligada — só elas
    entram na Logística."""
    rows = (
        (
            await session.execute(
                select(Integration.name).where(
                    Integration.platform == IntegrationPlatform.TEMU,
                    Integration.archived_at.is_(None),
                    # A Conciliação tem "âncoras" Temu (status "ancora", arquivadas).
                    Integration.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted({(n or "").strip().lower() for n in rows if (n or "").strip()})


def _build_temu_client(_session: AsyncSession, integration: Integration) -> TemuClient:
    return TemuClient(decrypt_json(integration.credentials))


def aplicar(row: Logistica, enr: dict[str, Any], *, agora: datetime | None = None) -> None:
    """Grava a leitura na linha. Pedido que a Temu devolveu sem status não
    apaga o que já estava."""
    novo = dict(enr.get("meli_status") or {})
    if not novo:
        return
    datas = dict(enr.get("datas") or {})
    falhou = bool(enr.get("rastreio_falhou"))
    if falhou:
        # Rastreio fora do ar não é "pacote sem evento": fica o envio que já
        # estava (e o carimbo dele — o merge congela valor igual).
        anterior = (row.meli_status or {}).get("logistics_status")
        if anterior:
            novo["logistics_status"] = anterior
        else:
            novo.pop("logistics_status", None)
        datas.pop("logistics_status", None)
    row.status_datas = logistica_datas.aplicar(row, novo, datas, agora=agora)
    row.meli_status = novo
    row.status_lido_em = agora or datetime.now(UTC)
    if enr.get("prazo_entrega"):
        row.prazo_entrega_amazon = enr["prazo_entrega"]
    row.divergencia = None
    if falhou:
        return
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
        row.rastreio_lido_em = row.status_lido_em
    if enr.get("localizacao"):
        row.localizacao = enr["localizacao"]
        if enr.get("localizacao_at"):
            row.localizacao_at = enr["localizacao_at"]
    if enr.get("entregue_em") and row.entregue_em is None:
        row.entregue_em = enr["entregue_em"]


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, TemuClient] | None = None,
    casos: dict[str, dict] | None = None,
) -> bool:
    """Lê o pedido Temu da linha e grava. Levanta `TemuEnrichError` quando não
    dá pra prosseguir (linha não-Temu, sem pedido, conta sem a API)."""
    if (row.plataforma or "").strip().lower() not in _TEMU_PLATAFORMAS:
        raise TemuEnrichError("logistica_nao_temu")
    po = (row.pedido_marketplace or "").strip()
    if not po:
        raise TemuEnrichError("logistica_sem_pedido")
    conta = row.conta
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _temu_integration_for_conta(session, conta)
        if integ is None:
            raise TemuEnrichError("logistica_sem_integracao")
        client = _build_temu_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client
    enr = await build_enrichment(client, po)
    if enr.get("meli_status"):
        if casos is None:
            # Lista de pós-venda não lida nesta rodada: fica o caso que já estava.
            for campo in CAMPOS_POS_VENDA:
                if (row.meli_status or {}).get(campo):
                    enr["meli_status"][campo] = row.meli_status[campo]
        elif po in casos:
            enr["meli_status"].update(await caso_do_pedido(client, casos[po]))
    aplicar(row, enr)
    return bool(enr.get("meli_status"))


CAMPOS_POS_VENDA = (
    "return_type",
    "return_status",
    "return_pending",
    "return_reason",
    "return_refunded",
)


async def casos_por_conta(
    clientes: dict[str, TemuClient], *, agora: datetime | None = None
) -> dict[str, dict[str, dict] | None]:
    """Uma leitura da lista de pós-venda por conta. Conta cuja lista falhou fica
    com None (as linhas dela guardam o caso que já tinham)."""
    out: dict[str, dict[str, dict] | None] = {}
    for conta, cliente in clientes.items():
        try:
            out[conta] = await casos_pos_venda(cliente, agora=agora)
        except TemuErro as e:
            logger.info("logistica_temu_pos_venda_lista_falhou", conta=conta, err=str(e)[:160])
            out[conta] = None
    return out


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
    agora: datetime | None = None,
    teto: int = TETO_POR_RODADA,
) -> dict[str, int]:
    """Enriquece um lote de linhas Temu (mais recentes primeiro). `ids`
    restringe às linhas dadas (o motor passa as pendentes + as vivas do
    painel): aí linha lida há menos de RELER_APOS fica pra depois, entram no
    máximo `teto` (as lidas há mais tempo primeiro), o commit é por lote e a
    rodada para na 1ª falha de rede — a Temu lenta não pode prender o motor."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_TEMU_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    if ids is not None:
        if not ids:
            return _resumo(0, 0, 0, 0, 0)
        corte = (agora or datetime.now(UTC)) - RELER_APOS
        stmt = (
            stmt.where(Logistica.id.in_(list(ids)))
            .where(
                or_(
                    Logistica.status_lido_em.is_(None),
                    Logistica.status_lido_em < corte,
                    cast(Logistica.meli_status, Text) == "{}",
                )
            )
            .order_by(Logistica.status_lido_em.asc().nulls_first(), Logistica.data.desc())
        )
    else:
        stmt = stmt.order_by(Logistica.data.desc().nulls_last(), Logistica.created_at.desc()).limit(
            limit
        )
    rows = list((await session.execute(stmt)).scalars().all())
    recentes = (len(ids) - len(rows)) if ids is not None else 0
    adiadas = 0
    if ids is not None and len(rows) > teto:
        adiadas = len(rows) - teto
        rows = rows[:teto]

    cache: dict[str, TemuClient] = {}
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_temu_integration_for_conta,
        build=_build_temu_client,
    )
    alvo = [r for r in rows if r.conta in cache]
    updated = 0
    skipped = len(rows) - len(alvo)
    failed = 0
    interrompida = 0
    # Pós-venda: uma leitura da lista por conta, antes das linhas.
    casos = await casos_por_conta({c: cache[c] for c in dict.fromkeys(r.conta for r in alvo)})
    for lote in logistica_enrich.chunked(alvo, _CONCURRENCY):
        res = await asyncio.gather(
            *(
                enrich_row(session, r, client_cache=cache, casos=casos.get(r.conta))
                for r in lote
            ),
            return_exceptions=True,
        )
        de_rede = False
        for row, r in zip(lote, res, strict=True):
            if isinstance(r, TemuEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                de_rede = de_rede or _falha_de_rede(r)
                logger.warning(
                    "logistica_temu_row_failed",
                    id=str(row.id),
                    pedido=row.pedido_marketplace,
                    err=str(r)[:200],
                )
            elif r:
                updated += 1
        # Commit por lote: rodada cortada no meio (timeout do motor) não perde
        # o que já foi lido.
        await session.commit()
        if de_rede:
            interrompida = 1
            logger.warning("logistica_temu_rodada_interrompida", lidas=updated, falhas=failed)
            break
        await asyncio.sleep(PAUSA_ENTRE_LINHAS)
    summary = _resumo(len(rows), updated, skipped, failed, max(recentes, 0))
    summary["adiadas"] = adiadas
    summary["interrompida"] = interrompida
    logger.info("logistica_temu_enrich_batch", **summary)
    return summary


def _resumo(seen: int, updated: int, skipped: int, failed: int, recentes: int) -> dict[str, int]:
    return {
        "seen": seen,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "recentes": recentes,
    }


def _falha_de_rede(e: BaseException) -> bool:
    """Temu fora do ar, lenta, limitando ou recusando a conta (token vencido):
    não é o pedido — as próximas falhariam igual, a rodada para."""
    if isinstance(e, TemuErro):
        return e.classe in (CLASSE_RETRYABLE, CLASSE_CONTA) or e.http is not None
    return isinstance(e, asyncio.TimeoutError | OSError)
