"""Avisos do módulo de Marketing no sino dos administradores.

M-20 (23/11/2026): eram mensagens no Telegram; agora cada aviso é um alerta
ADM (`alerts.avisar_administradores`) — todo administrador vê em /alertas,
com Equipe = ADM, a plataforma e a conta da loja.

Três sinais, de todas as plataformas:

  1. Crédito / orçamento restante baixo — `notify_low_credit` (chamado por
     cada sync depois do upsert, quando o saldo < R$ 50).
  2. Três falhas seguidas do sync — `record_sync_failure` soma
     `Integration.consecutive_errors`; exatamente na 3ª avisa uma vez (nada
     mais até um sucesso zerar o contador).
  3. ACOS bem acima do alvo — `notify_high_acos` quando o ACOS da conta passa
     de `alvo × 2.15` (≈15% num alvo de 7%).

No sino, crédito e ACOS avisam no máximo uma vez por dia por loja (a
`dedupe_key`); a trava de 30 min em memória continua poupando o banco a cada
rodada. O aviso nunca derruba o sync.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AlertSeverity
from app.models.integration import Integration
from app.services.alerts import avisar_administradores

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# Trava em memória — a mesma chave não avisa de novo dentro desta janela.
# Vale até o worker reiniciar. O cron roda a cada 30 min, então 1800 s é
# "um aviso por rodada"; no sino, a `dedupe_key` do dia segura o resto.
_DEDUP_WINDOW_SECONDS = 1800
_FAILURE_ALERT_THRESHOLD = 3
_HIGH_ACOS_MULTIPLIER = 2.15  # 7% target → 15% ACOS triggers alert

# (integration_id, alert_key) → last_sent_ts.
_dedup: dict[tuple[UUID, str], datetime] = {}


def _should_send(integration_id: UUID, key: str) -> bool:
    now = datetime.now(UTC)
    last = _dedup.get((integration_id, key))
    if last and (now - last) < timedelta(seconds=_DEDUP_WINDOW_SECONDS):
        return False
    _dedup[(integration_id, key)] = now
    return True


def _platform_value(integration: Integration) -> str:
    p = integration.platform
    return p.value if hasattr(p, "value") else str(p)


_ROTULOS = {
    "shopee": "Shopee",
    "ml": "Mercado Livre",
    "mercadolivre": "Mercado Livre",
    "amazon": "Amazon",
}


def _platform_label(platform: str) -> str:
    return _ROTULOS.get(platform, platform.title())


def _hoje() -> str:
    return datetime.now(_SP).date().isoformat()


async def _avisar(
    integration: Integration,
    titulo: str,
    mensagem: str,
    *,
    severity: AlertSeverity,
    chave: str,
    payload: dict,
    session: AsyncSession | None = None,
) -> bool:
    """O alerta ADM da loja (M-20): com a plataforma e a conta para a tela."""
    return await avisar_administradores(
        titulo,
        mensagem,
        severity=severity,
        payload={"marketing": payload.pop("tipo"), **payload},
        dedupe_key=chave,
        session=session,
        integration_id=integration.id,
        plataforma=_platform_value(integration),
        conta=integration.name,
    )


async def notify_low_credit(
    integration: Integration, *, credit: float, threshold: float = 50.0
) -> None:
    """Fire when balance/remaining-budget falls under the threshold.
    Per-integration dedup so the cron doesn't re-alert every 30 min."""
    if credit >= threshold:
        return
    if not _should_send(integration.id, "low_credit"):
        return
    label = _platform_label(_platform_value(integration))
    await _avisar(
        integration,
        f"{label} Ads: crédito baixo em {integration.name}",
        f"Saldo R$ {credit:.2f} (abaixo de R$ {threshold:.2f}). Recarregar o crédito de Ads.",
        severity=AlertSeverity.WARNING,
        chave=f"mkt_credito_baixo:{integration.id}:{_hoje()}",
        payload={"tipo": "credito_baixo", "saldo": round(credit, 2)},
    )


async def notify_high_acos(integration: Integration, acos: float, acos_target: float) -> None:
    """Fire when the account-level ACOS exceeds target × multiplier. We
    use a multiplier (not an absolute threshold) so shops with different
    acceptable ACOS get appropriate signals."""
    if acos_target <= 0:
        return
    threshold = acos_target * _HIGH_ACOS_MULTIPLIER
    if acos < threshold:
        return
    if not _should_send(integration.id, "high_acos"):
        return
    label = _platform_label(_platform_value(integration))
    await _avisar(
        integration,
        f"{label} Ads: ACOS alto em {integration.name}",
        f"ACOS de hoje {acos:.1f}% (alvo {acos_target:.1f}%).",
        severity=AlertSeverity.WARNING,
        chave=f"mkt_acos_alto:{integration.id}:{_hoje()}",
        payload={"tipo": "acos_alto", "acos": round(acos, 2), "alvo": acos_target},
    )


async def record_sync_failure(
    session: AsyncSession,
    integration: Integration,
    *,
    code: str,
    message: str,
) -> None:
    """Bump the consecutive-failure counter. At exactly the threshold we
    fire one alert and let the counter keep climbing — the next alert won't
    fire until the counter resets to 0 (a successful sync) and climbs back to
    the threshold. This avoids spam while still re-alerting after recovery +
    relapse. O alerta (M-20) entra num savepoint da sessão do sync e sai no
    commit dele."""
    integration.consecutive_errors = (integration.consecutive_errors or 0) + 1
    if integration.consecutive_errors == _FAILURE_ALERT_THRESHOLD:
        if _should_send(integration.id, "consecutive_failures"):
            label = _platform_label(_platform_value(integration))
            agora = datetime.now(_SP)
            await _avisar(
                integration,
                f"{label} Ads: 3 falhas seguidas em {integration.name}",
                f"Último erro: {code}: {message[:200]}",
                severity=AlertSeverity.ERROR,
                chave=f"mkt_falhas:{integration.id}:{agora:%Y%m%d%H%M}",
                payload={"tipo": "falhas_seguidas", "codigo": code},
                session=session,
            )


async def record_sync_success(session: AsyncSession, integration: Integration) -> None:
    """Reset the failure counter on a clean run so the next failure
    streak starts from zero."""
    if integration.consecutive_errors:
        integration.consecutive_errors = 0
