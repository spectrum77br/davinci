"""Conferência do preço VIVO na Amazon depois de cada envio da Tabela de Preços.

Eduardo (16/09/2026): enviou R$ 445, o DaVinci disse "ok", a Amazon guardou 445
no atributo do anúncio (purchasable_offer.our_price) — e a loja continuou
vendendo a R$ 599. Lendo a família inteira: 32 anúncios divergentes, TODOS eles
exatamente os que o DaVinci já tinha enviado (208 → vivo 499; 445 → vivo 599),
e 0 divergentes entre os 65 nunca enviados. O SKU casa, o valor chega, a Amazon
aceita sem "issues" e não aplica.

Não dá para forçar a Amazon daqui. O que dá é NÃO FICAR CALADO: minutos depois
do envio, ler o preço que está valendo (offers[], não o atributo) e, se for
diferente do enviado, registrar e avisar — no sino e no Threema.

Fonte dos envios: `pricing_push_idempotency` (já guarda conta, produto, preço e
os anúncios que responderam ok). Registro da conferência: `pricing_push_confirmacao`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingPushConfirmacao,
    PricingPushIdempotency,
    ProductLink,
)
from app.models.enums import AlertSeverity, AlertType
from app.models.user import User
from app.security.cipher import decrypt_json
from app.services import threema
from app.services.alerts import emit_alert
from app.services.marketplaces.amazon import AmazonClient

logger = structlog.get_logger()

# Quanto tempo depois do envio já vale conferir. A Amazon reflete o atributo em
# ~20 s; a oferta viva, quando reflete, leva minutos.
ATRASO_MIN = 6
# Envio mais velho que isto não é mais conferido (o primeiro tick depois do
# deploy pega o dia inteiro; depois a janela raramente importa).
JANELA_MIN = 720
LIMITE_POR_TICK = 30

STATUS_CONFIRMADO = "confirmado"
STATUS_DIVERGENTE = "divergente"
STATUS_SEM_LEITURA = "sem_leitura"


def parse_key(key: str) -> tuple[UUID, UUID] | None:
    """`cell:{product_id}:{account_id}:{ts}` → (account_id, product_id).

    A ordem na chave é PRODUTO primeiro, conta depois — conferido no banco em
    16/09/2026 (o primeiro UUID existe em pricing_products, o segundo em
    pricing_accounts). Ler ao contrário fazia o conferente procurar uma conta
    com o id do produto, não achar nada e pular todos os envios em silêncio
    (vistos=0 no primeiro tick)."""
    partes = (key or "").split(":")
    if len(partes) < 4 or partes[0] != "cell":
        return None
    try:
        product_id, account_id = UUID(partes[1]), UUID(partes[2])
    except ValueError:
        return None
    return account_id, product_id


def diverge(enviado: float, vivo: float | None) -> bool:
    """A Amazon BR só aceita reais inteiros; o envio já arredonda antes de
    mandar, então a comparação é em inteiros. Sem leitura não é divergência."""
    if vivo is None:
        return False
    return round(float(enviado)) != round(float(vivo))


async def _integracao_amazon_da_conta(
    session: AsyncSession, account_id: UUID
) -> Integration | None:
    acc = await session.get(PricingAccount, account_id)
    if acc is None or acc.integration_id is None:
        return None
    integ = await session.get(Integration, acc.integration_id)
    if integ is None or integ.platform != IntegrationPlatform.AMAZON:
        return None
    return integ


async def _links_por_external_id(
    session: AsyncSession, integration_id: UUID, external_ids: list[str]
) -> dict[str, ProductLink]:
    if not external_ids:
        return {}
    rows = (
        await session.execute(
            select(ProductLink)
            .where(ProductLink.integration_id == integration_id)
            .where(ProductLink.external_id.in_(external_ids))
        )
    ).scalars().all()
    return {lk.external_id: lk for lk in rows}


# Quem recebe o Threema. Eduardo (16/09/2026): "envie a mensagem somente para o
# heisenberg". É o nome do usuário no DaVinci; o ID do Threema sai da ficha
# dele — trocar de pessoa é mudar o cadastro, não o código. Sem grupo: este
# aviso é de decisão de preço, não de operação.
DESTINATARIO_THREEMA = "heisenberg"


async def _destinos_threema(session: AsyncSession) -> list[str]:
    user = (
        await session.execute(
            select(User)
            .where(func.lower(User.name) == DESTINATARIO_THREEMA)
            .where(User.threema.is_not(None))
        )
    ).scalars().first()
    return threema.parse_recipients(user.threema if user else None)


def _texto_aviso(conta: str, enviado: float, divergentes: list[dict[str, Any]]) -> str:
    linhas = [f"· {d['sku']}: vendendo a R$ {d['vivo']:.0f}" for d in divergentes[:12]]
    sobra = len(divergentes) - 12
    if sobra > 0:
        linhas.append(f"… e mais {sobra}.")
    return (
        f"Preço NÃO aplicado na Amazon ({conta}).\n"
        f"Enviado R$ {enviado:.0f} — a Amazon aceitou e guardou, mas a loja continua "
        f"com o preço antigo em {len(divergentes)} anúncio(s):\n" + "\n".join(linhas) + "\n\n"
        "Provável: regra de precificação automática ligada nesses anúncios no Seller "
        "Central. O DaVinci não consegue desligar isso por API."
    )


async def confirmar_pushes_recentes(
    session: AsyncSession,
    *,
    atraso_min: int = ATRASO_MIN,
    janela_min: int = JANELA_MIN,
    limite: int = LIMITE_POR_TICK,
    client_factory: Any = None,
) -> dict[str, int]:
    """Confere os envios recentes da Amazon ainda não conferidos.

    `client_factory(integration) -> client` existe para os testes trocarem a
    Amazon por um dublê; em produção é o AmazonClient de verdade."""
    agora = datetime.now(UTC)
    inicio = agora - timedelta(minutes=janela_min)
    fim = agora - timedelta(minutes=atraso_min)

    ja = set(
        (
            await session.execute(
                select(PricingPushConfirmacao.push_key).where(
                    PricingPushConfirmacao.created_at >= inicio
                )
            )
        ).scalars().all()
    )
    pushes = (
        await session.execute(
            select(PricingPushIdempotency)
            .where(PricingPushIdempotency.key.like("cell:%"))
            .where(PricingPushIdempotency.created_at >= inicio)
            .where(PricingPushIdempotency.created_at <= fim)
            .order_by(PricingPushIdempotency.created_at.desc())
            .limit(limite * 3)
        )
    ).scalars().all()

    resumo = {"vistos": 0, "confirmados": 0, "divergentes": 0, "sem_leitura": 0, "pulados": 0}
    feitos = 0
    for push in pushes:
        if feitos >= limite:
            break
        if push.key in ja:
            continue
        resp = push.response or {}
        ids = parse_key(push.key)
        if not ids or not resp.get("ok"):
            continue
        account_id, product_id = ids
        integ = await _integracao_amazon_da_conta(session, account_id)
        if integ is None:
            continue  # não é Amazon: nada a conferir
        resumo["vistos"] += 1
        feitos += 1
        try:
            enviado = float(resp.get("price"))
        except (TypeError, ValueError):
            resumo["pulados"] += 1
            continue
        links_ok = [
            str(item.get("externalId") or item.get("external_id") or "")
            for item in ((resp.get("payload") or {}).get("links") or [])
            if item.get("success")
        ]
        links = await _links_por_external_id(session, integ.id, [x for x in links_ok if x])

        if client_factory:
            client = client_factory(integ)
        else:
            client = AmazonClient(decrypt_json(integ.credentials))
        divergentes: list[dict[str, Any]] = []
        lidos = 0
        for ext, link in links.items():
            try:
                vivo = await client.get_listing_price(link)
            except Exception as e:  # noqa: BLE001 — leitura é best-effort
                logger.warning("pricing_confirmacao_leitura_falhou", sku=ext, err=str(e)[:120])
                vivo = None
            if vivo is None:
                continue
            lidos += 1
            if diverge(enviado, vivo):
                divergentes.append({"sku": ext, "vivo": float(vivo)})

        if lidos == 0:
            status = STATUS_SEM_LEITURA
            resumo["sem_leitura"] += 1
        elif divergentes:
            status = STATUS_DIVERGENTE
            resumo["divergentes"] += 1
        else:
            status = STATUS_CONFIRMADO
            resumo["confirmados"] += 1

        session.add(
            PricingPushConfirmacao(
                push_key=push.key,
                user_id=push.user_id,
                account_id=account_id,
                product_id=product_id,
                integration_id=integ.id,
                preco_enviado=enviado,
                status=status,
                conferidos=lidos,
                divergentes=divergentes,
                conferido_em=agora,
            )
        )
        await session.commit()

        if status == STATUS_DIVERGENTE:
            await _avisar(session, push, integ, enviado, divergentes)

    logger.info("pricing_confirmacao_amazon_done", **resumo)
    return resumo


async def _avisar(
    session: AsyncSession,
    push: PricingPushIdempotency,
    integ: Integration,
    enviado: float,
    divergentes: list[dict[str, Any]],
) -> None:
    texto = _texto_aviso(integ.name, enviado, divergentes)
    ids = parse_key(push.key)
    # Dedupe pela CÉLULA e pelo preço, não pelo envio: o operador clica três
    # vezes na mesma célula em minutos e o problema é um só — três avisos
    # iguais no Threema só ensinam a ignorar o aviso.
    account_id, product_id = ids if ids else (None, None)
    dedupe = f"pricing_confirmacao:{account_id}:{product_id}:{enviado:.0f}"
    try:
        criado = await emit_alert(
            session,
            user_id=push.user_id,
            type=AlertType.GENERIC,
            severity=AlertSeverity.WARNING,
            title=(
                f"Amazon {integ.name}: preço R$ {enviado:.0f} não aplicado "
                f"em {len(divergentes)} anúncio(s)"
            ),
            message=texto,
            payload={"push_key": push.key, "enviado": enviado, "divergentes": divergentes},
            dedupe_key=dedupe,
            notify_telegram=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("pricing_confirmacao_alert_falhou", err=str(e)[:200])
        criado = None
    if criado is None:
        # Já avisado por esta célula/preço: registra, não repete o Threema.
        logger.info("pricing_confirmacao_ja_avisado", dedupe=dedupe)
        return
    destinos = await _destinos_threema(session)
    if not destinos:
        logger.warning("pricing_confirmacao_sem_destinatario", usuario=DESTINATARIO_THREEMA)
        return
    try:
        await threema.ThreemaClient().send_to_all(texto, destinos)
        logger.info("pricing_confirmacao_avisado", destinos=len(destinos), dedupe=dedupe)
    except Exception as e:  # noqa: BLE001
        logger.warning("pricing_confirmacao_threema_falhou", err=str(e)[:200])
