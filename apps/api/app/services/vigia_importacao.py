"""Vigia de importação — pedido PAGO no marketplace que não caiu no Bling.

Eduardo (2026-08-27): pedidos que não caem sozinhos em "Pedidos de Venda"
são raros, mas quando acontece a equipe precisa achar o pedido na tela
"Pedidos de lojas virtuais" (canal multi loja) do Bling e importar na mão —
e o risco é ninguém perceber a tempo. A API pública do Bling v3 NÃO expõe
essa tela (verificado no OpenAPI oficial, 162 endpoints, 2026-08-27), então
o vigia olha pelo OUTRO lado: lista os pedidos da janela direto na API de
cada marketplace, separa os que DEVEM estar no Bling (regra por plataforma
abaixo) e confere se cada um já existe.

Fase 2 (21/09/2026): virou o primeiro robô da Ouvidoria (`services/
ouvidoria.py`) e cobre ML, Shopee, TikTok e Amazon. O estado anti-spam
(tabela `vigia_importacao`, mig 0230) deixou de existir: cada pedido que
falta vira uma ocorrência `<plataforma>:<pedido>` na `ouvidoria_ocorrencias`,
re-vista a cada rodada e fechada sozinha ("sumiu") quando o pedido aparece
no Bling. O aviso no Threema é o `avisar_pendentes` da Ouvidoria (uma
mensagem por rodada com tudo que está pendente; re-aviso por
`reaviso_horas`); o modo do robô (ligado / silencioso / desligado) é
editado na tela Ouvidoria › Robôs.

## O que DEVE estar no Bling (validado em produção 21/09)
- ML: `status == paid`, ignorando as tags `test_order` e `fraud_risk_detected`;
  o Bling grava como numeroLoja o id do pedido OU o pack_id (carrinho) —
  o match aceita os dois.
- Shopee: `order_status` fora de UNPAID / CANCELLED / IN_CANCEL; match pelo
  `order_sn`.
- TikTok: `status` fora de UNPAID / ON_HOLD / CANCELLED (ON_HOLD é a retenção
  de 24 h da TikTok; o Bling importa quando sai); match pelo `id`.
- Amazon: `OrderStatus` em Unshipped / PartiallyShipped / Shipped; match pelo
  `AmazonOrderId`. A cota do getOrders é apertada, então a Amazon só entra a
  cada `amazon_a_cada_rodadas` rodadas (config do robô, padrão 3).

## Por que três peneiras antes de abrir ocorrência
1. **Tolerância** (`tolerancia_min`, padrão 90 min desde o pagamento): a
   importação automática do Bling leva alguns minutos; antes disso é normal
   não estar lá.
2. **Espelho** `bling_orders.numeroloja`: barato (um SELECT) e cobre 99% —
   sincroniza a cada ~10 min (bling_orders_safety_net_tick), por isso a
   tolerância nunca acusa por atraso do próprio espelho.
3. **Bling ao vivo** (`pedidos_por_numero_loja`, só pra quem passou das duas
   primeiras, teto de _MAX_CONFERENCIAS_BLING por rodada): pedido que o
   Bling já tem mas o espelho ainda não viu NÃO vira ocorrência. Se o Bling
   não responde, na dúvida o robô não abre nada e também não fecha o que já
   estava aberto (não conferiu ≠ sumiu).

"Dentro da tolerância" e "adiado pelo teto" contam como VISTOS: uma
ocorrência já aberta nunca fecha só porque a rodada não julgou o pedido. E
uma aberta que a listagem não trouxe só fecha como "sumiu" quando o pedido
mudou de situação dentro da janela (cancelou) — se ele só ENVELHECEU (saiu
da janela de 72 h sem ninguém importar, o pior caso do robô), a rodada o
confere no Bling e mantém aberta enquanto não achar.

Conta cuja API falhou conta no resumo ("1 conta falhou") e entra em
`excluir_contas` do fechamento: o robô não olhou aquela loja, então "não
vi" não quer dizer "resolveu" — as ocorrências dela ficam abertas até uma
rodada conseguir olhar. Quem abre a ocorrência da CONTA é o **Vigia de
credenciais** desde 22/09/2026 (uma prova de vida por conta a cada hora,
que sabe separar token vencido de instabilidade e avisa o vencimento antes
de a conta cair); duas ocorrências pelo mesmo fato só confundiriam quem
recebe o Threema. As `conta:` que ESTE robô já tinha abertas em produção
foram fechadas de uma vez pela migração 0301 — elas nunca mais seriam
re-registradas e o `excluir_contas` as protegia do fechamento justamente
enquanto a conta continuasse caindo, que é quando o outro robô abre a dele.

BEST-EFFORT por conta: falha numa conta não derruba a rodada. O sweep é
serializado por advisory lock transacional numa sessão SÓ pra isso — a
sessão de trabalho commita por conta (token renovado não pode se perder num
rollback: o refresh token do ML é de uso único) e um commit soltaria o lock
se ele estivesse na mesma sessão.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
    StoreInfo,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services import bling_orders, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.factory import client_for
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

ROBO = "vigia_importacao"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76696769  # ascii "vigi"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_TOLERANCIA_MIN = 90
_JANELA_HORAS = 72
_AMAZON_A_CADA_RODADAS = 3
# Conferências ao vivo no Bling por rodada (pedidos, não chamadas: o client
# agrupa 20 por chamada). O que passar fica pra próxima rodada e aparece no
# resumo — protege a cota do Bling num dia em que a importação inteira parou.
_MAX_CONFERENCIAS_BLING = 40
_MAX_PAGINAS_ML = 20
_TZ_BR = ZoneInfo("America/Sao_Paulo")

# Contadores de uma rodada (ouvidoria_rodadas.contadores), em linguagem de
# operação: contas = olhadas; contas_puladas = Amazon fora desta rodada;
# pedidos = os que DEVEM estar no Bling; no_espelho = já em bling_orders;
# dentro_tolerancia = pagos há pouco; conferidos_bling / no_bling_vivo =
# conferência ao vivo; novas / persistem = ocorrências; adiados = passaram
# do teto do Bling; sumiram = fechadas nesta rodada.
_CONTADORES = (
    "contas", "contas_falha", "contas_puladas", "pedidos", "no_espelho",
    "dentro_tolerancia", "conferidos_bling", "no_bling_vivo", "novas",
    "persistem", "adiados", "antigas", "sumiram", "bling_falhou", "amazon_rodou",
    "shopee_sem_detalhe",
)

ACAO_IMPORTAR = (
    "Importar manualmente: Bling › Vendas › Pedidos de lojas virtuais "
    "(importar pedidos manualmente)"
)
# Ação da ocorrência de conta sem acesso. Mora aqui por histórico (era o
# vigia que a abria); quem usa agora é o Vigia de credenciais.
ACAO_REAUTORIZAR = "Reautorizar em Sistema › Integrações"

# Rótulo da plataforma NA FRENTE do nome da conta — pedido do Eduardo
# (27/08): "tem que ter o nome se é mercado livre se é shopee".
_ROTULO = {
    IntegrationPlatform.ML: "Mercado Livre",
    IntegrationPlatform.SHOPEE: "Shopee",
    IntegrationPlatform.TIKTOK: "TikTok",
    IntegrationPlatform.AMAZON: "Amazon",
}

_ML_TAGS_IGNORAR = {"test_order", "fraud_risk_detected"}
_SHOPEE_STATUS_FORA = {"UNPAID", "CANCELLED", "IN_CANCEL"}
_TIKTOK_STATUS_FORA = {"UNPAID", "ON_HOLD", "CANCELLED"}
_AMAZON_STATUS_DENTRO = {"Unshipped", "PartiallyShipped", "Shipped"}


@dataclass
class Candidato:
    """Um pedido que, pela regra da plataforma, DEVE estar no Bling."""

    plataforma: str  # ml / shopee / tiktok / amazon
    numero: str  # o numeroLoja do Bling
    conta: str = ""
    pack: str | None = None  # carrinho do ML (o Bling pode gravar este)
    pago_em: datetime | None = None  # o que vai no título ("Pago 21/09 14:08")
    criado_em: datetime | None = None  # a data pela qual a listagem filtra
    # Desde quando o Bling PODERIA ter importado — a âncora da tolerância.
    # Na maioria é o pagamento (None = usa pago_em), mas na TikTok o pedido
    # só fica importável ao sair do ON_HOLD (~24 h depois de pago) e na
    # Amazon o Unshipped começa quando ela confirma o pagamento (LastUpdate).
    importavel_em: datetime | None = None
    valor: float | None = None
    sku: str | None = None
    status: str | None = None  # como a plataforma chama

    @property
    def chave(self) -> str:
        return f"{self.plataforma}:{self.numero}"

    @property
    def tolerancia_desde(self) -> datetime | None:
        """De onde a tolerância conta: `importavel_em`, senão o pagamento."""
        return self.importavel_em or self.pago_em

    @property
    def numeros_bling(self) -> list[str]:
        """Números que o Bling pode ter gravado como numeroLoja."""
        return [n for n in (self.numero, self.pack) if n]

    @property
    def link(self) -> str | None:
        """Página do pedido no painel do vendedor de cada plataforma."""
        if self.plataforma == "ml":
            return f"https://www.mercadolivre.com.br/vendas/{self.pack or self.numero}/detalhe"
        if self.plataforma == "shopee":
            return f"https://seller.shopee.com.br/portal/sale/order/{self.numero}"
        if self.plataforma == "tiktok":
            return f"https://seller-br.tiktok.com/order/detail?order_no={self.numero}"
        if self.plataforma == "amazon":
            return f"https://sellercentral.amazon.com.br/orders-v3/order/{self.numero}"
        return None


# ─── parse ─────────────────────────────────────────────────────────────────


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _epoch(raw: object) -> datetime | None:
    """Epoch em SEGUNDOS → datetime UTC; None quando não dá pra ler.

    O valor vem da plataforma (Shopee `expire_time`, TikTok
    `refresh_token_expires_at`) e já chegou em milissegundos: aí
    `fromtimestamp` levanta OverflowError/OSError/ValueError, e quem chama —
    hoje o Vigia de credenciais, dentro da prova de vida de uma conta — não
    tem como se defender de um erro no meio da leitura. Valor torto vira
    "a plataforma não informou validade", que é o comportamento certo."""
    try:
        v = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return datetime.fromtimestamp(v, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _numero(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pago_em_ml(order: dict) -> datetime | None:
    """payments[].date_approved mais antigo; fallback date_created."""
    aprovados = [
        d
        for p in (order.get("payments") or [])
        if isinstance(p, dict) and (d := _parse_dt(p.get("date_approved"))) is not None
    ]
    if aprovados:
        return min(aprovados)
    return _parse_dt(order.get("date_created"))


def _sku_ml(order: dict) -> str | None:
    skus = []
    for entry in order.get("order_items") or []:
        item = entry.get("item") if isinstance(entry, dict) else None
        if isinstance(item, dict):
            sku = str(item.get("seller_sku") or item.get("seller_custom_field") or "").strip()
            if sku:
                skus.append(sku)
    return ", ".join(dict.fromkeys(skus)) or None


def candidato_ml(order: dict) -> Candidato | None:
    """Regra ML: `status == paid` e sem tag de teste/fraude. Devolve None
    pra pedido que NÃO precisa estar no Bling."""
    if (order.get("status") or "").lower() != "paid":
        return None
    if _ML_TAGS_IGNORAR & {str(t) for t in (order.get("tags") or [])}:
        return None
    numero = str(order.get("id") or "").strip()
    if not numero:
        return None
    pack = order.get("pack_id")
    return Candidato(
        plataforma="ml",
        numero=numero,
        pack=str(pack).strip() if pack else None,
        pago_em=_pago_em_ml(order),
        criado_em=_parse_dt(order.get("date_created")),
        valor=_numero(order.get("total_amount")),
        sku=_sku_ml(order),
        status="paid",
    )


def candidato_shopee(order: dict) -> Candidato | None:
    """Regra Shopee: qualquer situação fora de UNPAID / CANCELLED / IN_CANCEL.
    A listagem só traz order_sn + order_status; hora do pagamento, valor e
    SKU vêm depois, só pros que faltam no espelho (`_detalhar_shopee`)."""
    status = str(order.get("order_status") or "").strip().upper()
    numero = str(order.get("order_sn") or "").strip()
    if not numero or not status or status in _SHOPEE_STATUS_FORA:
        return None
    return Candidato(plataforma="shopee", numero=numero, status=status)


def candidato_tiktok(order: dict) -> Candidato | None:
    """Regra TikTok: `status` fora de UNPAID / ON_HOLD / CANCELLED. O título
    diz quando foi pago (`paid_time`, fallback `create_time`), mas a
    tolerância conta de `update_time`: o pedido fica ~24 h em ON_HOLD depois
    de pago e o Bling só importa quando sai — contar do pagamento faria todo
    pedido nascer "vencido" no instante em que vira AWAITING_SHIPMENT (alarme
    falso que fecha sozinho na rodada seguinte). `update_time` também avança
    em outras mudanças (envio, entrega); o custo disso é só adiar a abertura
    de uma ocorrência nova em mais uma tolerância — a aberta não fecha, porque
    candidato dentro da tolerância conta como visto."""
    status = str(order.get("status") or "").strip().upper()
    numero = str(order.get("id") or "").strip()
    if not numero or not status or status in _TIKTOK_STATUS_FORA:
        return None
    pagamento = order.get("payment") if isinstance(order.get("payment"), dict) else {}
    skus = []
    for li in order.get("line_items") or []:
        if isinstance(li, dict) and (sku := str(li.get("seller_sku") or "").strip()):
            skus.append(sku)
    pago_em = _epoch(order.get("paid_time")) or _epoch(order.get("create_time"))
    atualizado_em = _epoch(order.get("update_time"))
    return Candidato(
        plataforma="tiktok",
        numero=numero,
        pago_em=pago_em,
        criado_em=_epoch(order.get("create_time")),
        importavel_em=max(filter(None, (pago_em, atualizado_em)), default=None),
        valor=_numero(pagamento.get("total_amount")),
        sku=", ".join(dict.fromkeys(skus)) or None,
        status=status,
    )


def candidato_amazon(order: dict) -> Candidato | None:
    """Regra Amazon: `OrderStatus` em Unshipped / PartiallyShipped / Shipped
    (Pending = ainda verificando o pagamento; o Bling não importa). A API não
    diz quando o pagamento caiu: o título usa o `PurchaseDate` (cartão é o
    mesmo instante; boleto é a data do pedido) e a tolerância conta do
    `LastUpdateDate` — Unshipped começa quando a Amazon confirma o pagamento
    (boleto pode levar dias); pra Shipped é a hora do envio, mais tarde que
    o pagamento, então o aviso no máximo atrasa, nunca acusa cedo. Sem SKU:
    viria só do getOrderItems."""
    status = str(order.get("OrderStatus") or "").strip()
    numero = str(order.get("AmazonOrderId") or "").strip()
    if not numero or status not in _AMAZON_STATUS_DENTRO:
        return None
    total = order.get("OrderTotal") if isinstance(order.get("OrderTotal"), dict) else {}
    comprado_em = _parse_dt(order.get("PurchaseDate"))
    return Candidato(
        plataforma="amazon",
        numero=numero,
        pago_em=comprado_em,
        criado_em=comprado_em,
        importavel_em=_parse_dt(order.get("LastUpdateDate")) or comprado_em,
        valor=_numero(total.get("Amount")),
        status=status,
    )


# ─── listagem por conta ────────────────────────────────────────────────────
# Cada função lista a janela de UMA conta e devolve os candidatos (já
# filtrados pela regra da plataforma). Erro de API SOBE: o chamador
# transforma em ocorrência de conta. `conta` é só carimbado nos candidatos.


async def _pedidos_ml_por_conta(
    client: MercadoLivreClient,
    creds: dict,
    *,
    conta: str,
    desde: datetime,
    ate: datetime,
    tolerancia: timedelta,
) -> list[Candidato]:
    seller_id = str(creds.get("user_id") or "").strip()
    if not seller_id:
        raise RuntimeError("credencial sem user_id (seller)")
    fmt = "%Y-%m-%dT%H:%M:%S.000-00:00"  # formato da doc do ML
    out: list[Candidato] = []
    offset = 0
    for _ in range(_MAX_PAGINAS_ML):
        data = await client.search_orders(
            seller_id=seller_id,
            date_from=desde.strftime(fmt),
            date_to=ate.strftime(fmt),
            limit=50,
            offset=offset,
        )
        results = data.get("results") or []
        for order in results:
            if isinstance(order, dict) and (c := candidato_ml(order)) is not None:
                c.conta = conta
                out.append(c)
        total = int((data.get("paging") or {}).get("total") or 0)
        offset += 50
        if not results or offset >= total:
            break
    return out


async def _pedidos_shopee_por_conta(
    client: ShopeeClient,
    creds: dict,
    *,
    conta: str,
    desde: datetime,
    ate: datetime,
    tolerancia: timedelta,
) -> list[Candidato]:
    """A lista da Shopee não diz QUANDO o pedido foi pago, então a tolerância
    entra na própria janela: só pedidos CRIADOS há mais de `tolerancia`
    (time_to = agora − tolerância). Pedido criado antes mas pago agora há
    pouco (Pix atrasado) é filtrado depois pelo pay_time do detalhe."""
    fim = ate - tolerancia
    if fim <= desde:
        return []
    out: list[Candidato] = []
    async for order in client.iter_orders(
        time_from=int(desde.timestamp()), time_to=int(fim.timestamp())
    ):
        if (c := candidato_shopee(order)) is not None:
            c.conta = conta
            out.append(c)
    return out


async def _pedidos_tiktok_por_conta(
    client: TikTokClient,
    creds: dict,
    *,
    conta: str,
    desde: datetime,
    ate: datetime,
    tolerancia: timedelta,
) -> list[Candidato]:
    out: list[Candidato] = []
    async for order in client.iter_orders(
        create_time_ge=int(desde.timestamp()), create_time_lt=int(ate.timestamp()) + 1
    ):
        if (c := candidato_tiktok(order)) is not None:
            c.conta = conta
            out.append(c)
    return out


async def _pedidos_amazon_por_conta(
    client: AmazonClient,
    creds: dict,
    *,
    conta: str,
    desde: datetime,
    ate: datetime,
    tolerancia: timedelta,
) -> list[Candidato]:
    out: list[Candidato] = []
    async for order in client.iter_orders(
        created_after=desde.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        order_statuses=sorted(_AMAZON_STATUS_DENTRO),
    ):
        if (c := candidato_amazon(order)) is not None:
            c.conta = conta
            out.append(c)
    return out


async def _detalhar_shopee(client: ShopeeClient, candidatos: list[Candidato]) -> bool:
    """Completa pago_em / criado_em / valor / SKU dos candidatos Shopee que
    faltam no espelho (poucos por rodada) com o get_order_detail — a listagem
    não traz nada disso. Devolve False quando a Shopee não respondeu: aí o
    chamador NÃO julga esses candidatos nesta rodada (sem pay_time não dá pra
    saber se o pedido está dentro da tolerância — um Pix pago há 10 min viraria
    alarme falso), só marca como vistos e tenta de novo na próxima."""
    por_numero = {c.numero: c for c in candidatos}
    numeros = list(por_numero)
    for i in range(0, len(numeros), 50):  # teto da Shopee por chamada
        try:
            resp = await client._call(  # noqa: SLF001 — mesmo precedente do logistica_meli
                "GET",
                "/api/v2/order/get_order_detail",
                params={
                    "order_sn_list": ",".join(numeros[i : i + 50]),
                    "response_optional_fields": "pay_time,total_amount,item_list",
                },
                what="shopee_order_detail",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vigia_importacao_shopee_detalhe_falhou", error=str(e)[:200])
            return False
        for o in resp.get("order_list") or []:
            if not isinstance(o, dict):
                continue
            c = por_numero.get(str(o.get("order_sn") or "").strip())
            if c is None:
                continue
            c.pago_em = _epoch(o.get("pay_time")) or c.pago_em
            c.criado_em = _epoch(o.get("create_time")) or c.criado_em
            c.valor = _numero(o.get("total_amount")) if c.valor is None else c.valor
            skus = [
                str(it.get("model_sku") or it.get("item_sku") or "").strip()
                for it in (o.get("item_list") or [])
                if isinstance(it, dict)
            ]
            c.sku = ", ".join(dict.fromkeys(s for s in skus if s)) or c.sku
    return True


# ─── contas e clients ──────────────────────────────────────────────────────


async def _contas(
    session: AsyncSession, platform: IntegrationPlatform
) -> list[tuple[Integration, str]]:
    """Integrações ATIVAS da plataforma + rótulo "<Plataforma> <conta>"
    (conta = store_info.account_name; fallback integration.name). Mesmo
    critério do `_contas_ml` da fase 1, generalizado. store_info pode ter
    mais de uma linha por integração — o primeiro nome não-vazio ganha."""
    rows = (
        await session.execute(
            select(Integration, StoreInfo.account_name)
            .outerjoin(StoreInfo, StoreInfo.integration_id == Integration.id)
            .where(Integration.platform == platform)
            .where(Integration.status == "active")
            .where(Integration.archived_at.is_(None))
            .order_by(Integration.created_at)
        )
    ).all()
    por_id: dict = {}
    for integration, account_name in rows:
        atual = por_id.get(integration.id)
        if atual is None or (atual[1] is None and account_name):
            por_id[integration.id] = (integration, account_name)
    rotulo = _ROTULO.get(platform, str(platform.value).title())
    return [
        (integration, f"{rotulo} {(nome or integration.name or '').strip()}".strip())
        for integration, nome in por_id.values()
    ]


def _cliente(session: AsyncSession, integration: Integration) -> tuple[object, dict]:
    """Client da plataforma + credenciais abertas. O refresh de token é
    persistido E commitado na hora (padrão do `_bling_client_for_user`): o
    refresh token do ML é de uso único, então um token novo perdido num
    rollback deixaria a conta sem acesso até alguém reautorizar."""
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at") or new_creds.get("token_expires_at")
        if exp:
            try:
                integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
            except (TypeError, ValueError):
                pass
        await session.commit()

    client = client_for(
        integration.platform,
        creds,
        on_token_refresh=_persist,
        integration_id=integration.id,
    )
    return client, creds


async def _cliente_bling(session: AsyncSession):
    """BlingClient da conta principal (persiste o refresh). None = não há
    integração Bling cadastrada — aí não dá pra conferir ao vivo."""
    return await bling_orders._bling_client_for_user(session, None)  # noqa: SLF001


async def _no_espelho(session: AsyncSession, numeros: set[str]) -> set[str]:
    """Quais desses números já existem em bling_orders.numeroloja (global,
    sem filtrar por loja: o id de pedido é único por plataforma e a conferência
    ao vivo também é global)."""
    ids = list(numeros)
    out: set[str] = set()
    for i in range(0, len(ids), 500):
        out |= {
            str(n)
            for n in (
                await session.execute(
                    select(BlingOrder.numeroloja).where(
                        BlingOrder.numeroloja.in_(ids[i : i + 500])
                    )
                )
            ).scalars()
        }
    return out


async def _amazon_nesta_rodada(session: AsyncSession, a_cada: int) -> bool:
    """A Amazon entra quando NENHUMA das últimas `a_cada − 1` rodadas OK a
    incluiu — o "contador" é o `amazon_rodou` dos contadores gravados em
    ouvidoria_rodadas, então sobrevive a restart e não suja a config. Rodada
    que quebrou não conta: ela pode ter caído antes de chegar na Amazon."""
    if a_cada <= 1:
        return True
    ultimas = (
        (
            await session.execute(
                select(OuvidoriaRodada.contadores)
                .where(OuvidoriaRodada.robo_chave == ROBO)
                .where(OuvidoriaRodada.ok.is_(True))
                .order_by(OuvidoriaRodada.iniciada_em.desc())
                .limit(a_cada - 1)
            )
        )
        .scalars()
        .all()
    )
    return not any((c or {}).get("amazon_rodou") for c in ultimas)


# Trechos que denunciam token vencido / permissão negada — o oposto de um
# soluço de rede/cota (timeout, 429, 5xx). Desde 22/09 o vigia não abre mais
# ocorrência de conta (isso é do Vigia de credenciais, que faz uma prova de
# vida por conta a cada hora); a classificação continua aqui porque o LOG da
# conta que falhou diz qual dos dois foi — é o que se olha quando a rodada
# conferiu menos pedidos do que o normal.
_MARCAS_SEM_ACESSO = (
    "401", "403", "unauthorized", "forbidden", "invalid_grant", "invalid_token",
    "access_denied", "error_auth", "error_permission", "refresh", "token", "credencial",
    "missing_creds", "sem user_id",
)


def _erro_de_acesso(e: Exception) -> bool:
    """True quando o erro é de credencial (401/403/invalid_grant/refresh),
    não de instabilidade. Olha o status HTTP quando a exceção tem um e, se
    não, as marcas no texto (os clients embutem código e mensagem na
    RuntimeError). 429/5xx/timeout NUNCA são de acesso."""
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        return int(status) in (401, 403)
    texto = str(e).lower()
    if any(m in texto for m in ("status=429", " 429", "status=5", "timeout", "timed out")):
        return False
    return any(m in texto for m in _MARCAS_SEM_ACESSO)


# Folga na hora de decidir se uma aberta "saiu da janela": a listagem filtra
# pela CRIAÇÃO, e quando o pedido não trouxe a hora de criação a referência é
# o pagamento ou a abertura da ocorrência — ambos depois da criação. Na
# dúvida o robô confere no Bling (custa uma busca) em vez de fechar.
_JANELA_MARGEM = timedelta(hours=6)


def _numeros_da_ocorrencia(o: OuvidoriaOcorrencia) -> list[str]:
    """Números que o Bling pode ter gravado pra uma ocorrência de pedido."""
    pack = (o.dados or {}).get("pack_id")
    return [n for n in (o.pedido, str(pack) if pack else None) if n]


async def _abertas_que_envelheceram(
    session: AsyncSession,
    candidatos: dict[str, Candidato],
    excluir_contas: set[str],
    *,
    desde: datetime,
) -> list[OuvidoriaOcorrencia]:
    """Ocorrências de pedido abertas que a listagem desta rodada não trouxe
    E cuja criação ficou pra trás da janela (`desde`, com folga): a rodada
    não tem como saber se o pedido caiu no Bling ou continua faltando, então
    quem chama confere. As da janela que não vieram mudaram de situação
    (cancelaram) — essas fecham como sumiu, sem conferir. Ocorrências de
    conta e das contas que falharam ficam de fora (já protegidas)."""
    rows = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.pedido.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    limite = desde + _JANELA_MARGEM
    out = []
    for o in rows:
        if o.chave in candidatos or o.chave.startswith("conta:"):
            continue
        if o.conta and o.conta in excluir_contas:
            continue
        dados = o.dados or {}
        referencia = (
            _parse_dt(dados.get("criado_em"))
            or _parse_dt(dados.get("pago_em"))
            or o.aberta_em
        )
        if referencia < limite:
            out.append(o)
    out.sort(key=lambda o: o.aberta_em)
    return out


# ─── texto da ocorrência ───────────────────────────────────────────────────


def _reais(valor: float | None) -> str | None:
    if valor is None:
        return None
    s = f"{valor:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _hora_br(dt: datetime | None) -> str | None:
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M") if dt else None


def titulo_ocorrencia(c: Candidato) -> str:
    """"Pago 21/09 14:08 (R$ 739,19) e não caiu no Bling" — sem a hora ou o
    valor quando a plataforma não deu."""
    partes = ["Pago"]
    if hora := _hora_br(c.pago_em):
        partes.append(hora)
    if reais := _reais(c.valor):
        partes.append(f"({reais})")
    return " ".join(partes) + " e não caiu no Bling"


def detalhe_ocorrencia(c: Candidato, conferido_em: datetime) -> str:
    partes = [f"Pedido {c.numero} da conta {c.conta}"]
    if c.pack:
        partes.append(f"carrinho {c.pack} (o Bling pode ter gravado este número)")
    if c.status:
        partes.append(f"situação na plataforma: {c.status}")
    if c.sku:
        partes.append(f"SKU {c.sku}")
    partes.append(
        f"não estava no Bling na conferência ao vivo de {_hora_br(conferido_em)} — "
        "a importação automática não trouxe"
    )
    return " · ".join(partes) + "."


# ─── rodada ────────────────────────────────────────────────────────────────


async def vigia_importacao_run(session: AsyncSession) -> dict:
    """Uma varredura completa dentro de uma `Rodada` da Ouvidoria. A sessão
    commita por conta (ver `_cliente`) e a Rodada commita ao sair; quem chama
    só precisa garantir que não há outro sweep junto (advisory lock no
    `vigia_importacao_sweep`)."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        # Todos os contadores nascem em 0: a rodada gravada tem sempre as
        # mesmas chaves (a tela lê direto) e o dict devolvido também.
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        tolerancia = timedelta(minutes=int(cfg.get("tolerancia_min") or _TOLERANCIA_MIN))
        janela = timedelta(hours=int(cfg.get("janela_horas") or _JANELA_HORAS))
        a_cada = int(cfg.get("amazon_a_cada_rodadas") or _AMAZON_A_CADA_RODADAS)
        agora = datetime.now(UTC)
        desde = agora - janela

        amazon_agora = await _amazon_nesta_rodada(session, a_cada)
        planos = [
            (IntegrationPlatform.ML, _pedidos_ml_por_conta),
            (IntegrationPlatform.SHOPEE, _pedidos_shopee_por_conta),
            (IntegrationPlatform.TIKTOK, _pedidos_tiktok_por_conta),
            (IntegrationPlatform.AMAZON, _pedidos_amazon_por_conta),
        ]

        # 1) Lista cada conta. Conta que falhou vira ocorrência e fica fora do
        #    fechamento; conta pulada nesta rodada (Amazon) idem — não olhou.
        candidatos: dict[str, Candidato] = {}
        clientes: dict[str, object] = {}
        excluir_contas: set[str] = set()
        for platform, listar in planos:
            contas = await _contas(session, platform)
            if platform == IntegrationPlatform.AMAZON and not amazon_agora:
                excluir_contas |= {conta for _i, conta in contas}
                r.contadores["contas_puladas"] += len(contas)
                continue
            for integration, conta in contas:
                r.contadores["contas"] += 1
                try:
                    client, creds = _cliente(session, integration)
                    clientes[conta] = client
                    pedidos = await listar(
                        client, creds, conta=conta, desde=desde, ate=agora,
                        tolerancia=tolerancia,
                    )
                except Exception as e:  # noqa: BLE001 — best-effort por conta
                    # Desde 22/09 a conta que falhou NÃO vira ocorrência aqui:
                    # quem cuida de credencial é o Vigia de credenciais (uma
                    # prova de vida por conta a cada hora, e ele sabe
                    # distinguir vencimento de instabilidade). O que o vigia
                    # de importação faz é o que só ele sabe: contar a falha no
                    # resumo e NÃO julgar os pedidos dessa conta nesta rodada
                    # (não olhou ≠ sumiu) — as ocorrências dela ficam de pé.
                    erro = str(e)[:300]
                    logger.warning(
                        "vigia_importacao_conta_falhou",
                        integration=str(integration.id), conta=conta, error=erro,
                        acesso=_erro_de_acesso(e),
                    )
                    r.contadores["contas_falha"] += 1
                    excluir_contas.add(conta)
                    continue
                for c in pedidos:
                    candidatos[c.chave] = c
                await session.commit()
            if platform == IntegrationPlatform.AMAZON and amazon_agora:
                # Só conta como "Amazon conferida" depois que as contas dela
                # foram listadas — rodada que quebra antes disso não pode
                # deixar a Amazon 2 h sem conferência.
                r.contadores["amazon_rodou"] = 1
        r.contadores["pedidos"] = len(candidatos)

        # 2) Espelho: quem já está em bling_orders está resolvido.
        numeros: set[str] = set()
        for c in candidatos.values():
            numeros.update(c.numeros_bling)
        existentes = await _no_espelho(session, numeros)
        faltantes = [
            c for c in candidatos.values()
            if not any(n in existentes for n in c.numeros_bling)
        ]
        r.contadores["no_espelho"] = len(candidatos) - len(faltantes)

        # Shopee: só agora vale a pena buscar pay_time/valor/SKU (são poucos).
        # Conta cujo detalhe falhou não é julgada nesta rodada: sem pay_time
        # não dá pra saber se está dentro da tolerância (na dúvida, nem abre
        # nem fecha — mesma regra do Bling fora do ar).
        por_conta_shopee: dict[str, list[Candidato]] = {}
        for c in faltantes:
            if c.plataforma == "shopee":
                por_conta_shopee.setdefault(c.conta, []).append(c)
        sem_detalhe: set[str] = set()
        for conta, lista in por_conta_shopee.items():
            client = clientes.get(conta)
            if client is None or not await _detalhar_shopee(client, lista):  # type: ignore[arg-type]
                sem_detalhe.update(c.chave for c in lista)
        if sem_detalhe:
            r.contadores["shopee_sem_detalhe"] = len(sem_detalhe)
            r.vistas.update(sem_detalhe)
            faltantes = [c for c in faltantes if c.chave not in sem_detalhe]

        # 3) Tolerância: importável há menos que isso é o normal da importação.
        #    "Dentro da tolerância" quer dizer "ainda não abro", não "sumiu":
        #    quem já tinha ocorrência aberta conta como visto e continua
        #    aberta (a âncora pode avançar — Amazon LastUpdateDate, TikTok
        #    update_time, alguém aumentou a tolerância na tela).
        corte = agora - tolerancia
        vencidos = [
            c for c in faltantes
            if (d := c.tolerancia_desde) is None or d <= corte
        ]
        r.contadores["dentro_tolerancia"] = len(faltantes) - len(vencidos)
        chaves_vencidos = {c.chave for c in vencidos}
        r.vistas.update(c.chave for c in faltantes if c.chave not in chaves_vencidos)

        # 4) Bling ao vivo, do mais antigo pro mais novo, com teto por rodada.
        #    Quem ficou de fora é marcado como visto (a ocorrência aberta não
        #    pode fechar como "sumiu" só porque a rodada não chegou nele).
        vencidos.sort(key=lambda c: c.tolerancia_desde or desde)
        conferir = vencidos[:_MAX_CONFERENCIAS_BLING]
        adiados = vencidos[_MAX_CONFERENCIAS_BLING:]
        r.vistas.update(c.chave for c in adiados)

        # 4b) Abertas que a listagem NÃO trouxe: ou o pedido mudou de situação
        #     dentro da janela (cancelou → não precisa mais estar no Bling →
        #     sumiu, como sempre) ou só ENVELHECEU — saiu da janela por data
        #     de criação sem ninguém importar, que é justamente o pior caso do
        #     robô. Essas vão junto pro Bling: achou → deixa fechar como
        #     sumiu; não achou → continua aberta e re-avisa.
        antigas = await _abertas_que_envelheceram(
            session, candidatos, excluir_contas, desde=desde
        )
        r.contadores["antigas"] = len(antigas)
        if antigas:
            no_espelho_antigas = await _no_espelho(
                session, {n for o in antigas for n in _numeros_da_ocorrencia(o)}
            )
            antigas = [
                o for o in antigas
                if not any(n in no_espelho_antigas for n in _numeros_da_ocorrencia(o))
            ]
        sobra = max(0, _MAX_CONFERENCIAS_BLING - len(conferir))
        antigas_conferir, antigas_adiadas = antigas[:sobra], antigas[sobra:]
        for o in antigas_adiadas:
            r.rever(o, agora=agora)
        r.contadores["adiados"] = len(adiados) + len(antigas_adiadas)

        encontrados: dict[str, dict] | None = None
        if conferir or antigas_conferir:
            try:
                bling = await _cliente_bling(session)
                if bling is None:
                    raise RuntimeError("sem integração Bling cadastrada")
                encontrados = await bling.pedidos_por_numero_loja(
                    [n for c in conferir for n in c.numeros_bling]
                    + [n for o in antigas_conferir for n in _numeros_da_ocorrencia(o)]
                )
            except Exception as e:  # noqa: BLE001 — na dúvida, não abre nem fecha
                logger.warning("vigia_importacao_bling_falhou", error=str(e)[:300])
                r.contadores["bling_falhou"] = 1
                r.vistas.update(c.chave for c in conferir)
                for o in antigas_conferir:
                    r.rever(o, agora=agora)
        if encontrados is not None:
            r.contadores["conferidos_bling"] = len(conferir) + len(antigas_conferir)
            for c in conferir:
                if any(n in encontrados for n in c.numeros_bling):
                    r.contadores["no_bling_vivo"] += 1
                    continue
                row = await r.registrar(
                    chave=c.chave,
                    plataforma=c.plataforma,
                    conta=c.conta,
                    pedido=c.numero,
                    titulo=titulo_ocorrencia(c),
                    detalhe=detalhe_ocorrencia(c, agora),
                    acao=ACAO_IMPORTAR,
                    link=c.link,
                    severidade="pessoa",
                    dados={
                        "pago_em": c.pago_em.isoformat() if c.pago_em else None,
                        "criado_em": c.criado_em.isoformat() if c.criado_em else None,
                        "importavel_em": (
                            c.tolerancia_desde.isoformat() if c.tolerancia_desde else None
                        ),
                        "valor": c.valor,
                        "sku": c.sku,
                        "status_plataforma": c.status,
                        "pack_id": c.pack,
                        "conferido_bling_em": agora.isoformat(),
                    },
                    agora=agora,
                )
                if row.fechada_em is None and row.aberta_em == agora:
                    r.contadores["novas"] += 1
                else:
                    r.contadores["persistem"] += 1
            for o in antigas_conferir:
                if any(n in encontrados for n in _numeros_da_ocorrencia(o)):
                    r.contadores["no_bling_vivo"] += 1
                    continue  # não vista → fecha como sumiu no passo 5
                r.rever(o, agora=agora)
                r.contadores["persistem"] += 1

        # 5) O que a rodada não viu, sumiu — menos o das contas que não olhou.
        r.contadores["sumiram"] = await r.fechar_nao_vistas(excluir_contas=excluir_contas)

        novas = r.contadores["novas"]
        falhas = r.contadores["contas_falha"]
        partes = [
            f"{len(candidatos)} pedido{'s' if len(candidatos) != 1 else ''} conferido"
            f"{'s' if len(candidatos) != 1 else ''}",
            f"{novas} nova{'s' if novas != 1 else ''}",
            f"{falhas} conta{'s' if falhas != 1 else ''} falhou",
        ]
        if adiados or antigas_adiadas:
            n_adiados = len(adiados) + len(antigas_adiadas)
            partes.append(f"{n_adiados} ficaram pra próxima rodada (teto do Bling)")
        if r.contadores.get("bling_falhou"):
            partes.append("Bling não respondeu à conferência ao vivo")
        if sem_detalhe:
            partes.append(f"Shopee sem detalhe de {len(sem_detalhe)} (fica pra próxima)")
        if not amazon_agora:
            partes.append(f"Amazon pulada (roda a cada {a_cada})")
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_importacao_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock — a sessão de trabalho commita
    por conta e um commit soltaria o lock. O modo do robô NÃO é olhado aqui:
    o tick do worker (`vigia_importacao_tick`) é quem sai quando está
    `desligado`; o botão "Rodar agora" roda mesmo desligado (a pessoa pediu)."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await vigia_importacao_run(session)
