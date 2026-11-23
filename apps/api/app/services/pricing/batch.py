"""Bulk pricing push runner (Fase 9c).

Runs in an Arq job so the user can fire 100s of cells without keeping the
HTTP request open. Drives `push_one` sequentially (respects per-platform
rate limits already inside MLClient._request) and writes progress to
`background_jobs`. Ao terminar, o relatório do lote vira um alerta ADM no sino
(M-20, 23/11/2026: todo administrador vê em /alertas, com Equipe = ADM; antes
ia ao Telegram de quem tinha o chat configurado).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlertSeverity,
    BackgroundJob,
    BackgroundJobStatus,
    PricingAccount,
    Segment,
    User,
)
from app.services.alerts import avisar_administradores
from app.services.pricing.push import push_one, resultado_para

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


_DEPT_LABEL = {
    "celular": "Celular",
    "mala": "Mala",
    "eletro": "Eletro",
    "catalogo": "Catálogo ML",
}


async def _infer_batch_department(session: AsyncSession, items: list[dict]) -> str | None:
    """Pick the department slug of the first item's pricing_account. The
    bulk-push UI is per-department so this is normally unambiguous; mixed
    batches just get the first one's label."""
    if not items:
        return None
    try:
        acc_id = UUID(items[0]["pricing_account_id"])
    except (KeyError, ValueError):
        return None
    acc = await session.get(PricingAccount, acc_id)
    if acc is None or acc.segment_id is None:
        return None
    seg = await session.get(Segment, acc.segment_id)
    if seg is None:
        return None
    if seg.parent_id is None:
        return _DEPT_LABEL.get(seg.slug, seg.slug)
    parent = await session.get(Segment, seg.parent_id)
    if parent is None:
        return None
    return _DEPT_LABEL.get(parent.slug, parent.slug)


def _format_report(
    summary: dict, samples: list[dict], department: str | None = None
) -> tuple[str, str, int]:
    """O relatório do lote: (título, texto, erros). Separa as falhas em
    "Pulados (encerrados)", "Sem vínculo" e "Erros" para a triagem de olho e
    mostra os SKUs que pedem atenção. Texto simples: o sino não lê HTML
    (M-20, 23/11/2026; antes era a mensagem do Telegram)."""
    # "bloqueado": célula de catálogo sem para onde enviar (sem anúncio,
    # sincronizado, pausado…) — recusada antes de chamar o ML, é pulo e não erro.
    closed_codes = {
        "ml_listing_closed",
        "ml_listing_paused",
        "ml_listing_under_review",
        "bloqueado",
    }
    no_link_codes = {"no_link", "listing_not_found"}

    skipped = []
    no_link = []
    errors = []
    for s in samples:
        code = s.get("code") or ""
        if code in closed_codes:
            skipped.append(s)
        elif code in no_link_codes:
            no_link.append(s)
        elif code not in ("ok", "partial"):
            errors.append(s)

    header_dept = f" — {department}" if department else ""
    titulo = f"Relatório de envio de preços em lote{header_dept}"
    lines = [
        f"Enviados: {summary['ok']}",
        f"Pulados (encerrados): {len(skipped)}",
        f"Erros: {len(errors)}",
        f"Sem vínculo: {len(no_link)}",
    ]

    def _fmt(s: dict) -> str:
        price = f" R$ {s['price']}" if s.get("price") else ""
        detail = s.get("detail") or ""
        if len(detail) > 80:
            detail = detail[:77] + "…"
        return f"• {s.get('code', '')}{price} {detail}".rstrip()

    if errors:
        lines.append("")
        lines.append(f"Erros (até 10 de {len(errors)}):")
        for s in errors[:10]:
            lines.append(_fmt(s))
    if no_link:
        lines.append("")
        lines.append(f"Sem vínculo (até 10 de {len(no_link)}):")
        for s in no_link[:10]:
            lines.append(_fmt(s))
    return titulo, "\n".join(lines), len(errors)


async def run_push_prices_batch(
    session: AsyncSession,
    *,
    job_id: UUID,
    user_id: UUID,
    items: list[dict],
    idempotency_prefix: str | None = None,
) -> None:
    """Idempotency model: if `idempotency_prefix` is set, each item gets
    `f"{prefix}:{i}"` as its key — a re-enqueue with the same prefix replays
    the cached responses without extra marketplace traffic (B13 across the
    whole batch)."""
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("batch_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    job.total = len(items)
    job.processed = 0
    await session.commit()

    user = await session.get(User, user_id)
    if user is None:
        job.status = BackgroundJobStatus.FAILED
        job.error = "user_not_found"
        job.finished_at = _now()
        await session.commit()
        return

    # Mesma cerca de equipe do Enviar (`POST /push`).
    from app.routers.pricing import _contas_que_envia  # tardio: roteador pesado

    permitidas = await _contas_que_envia(session, user)
    summary = {"total": len(items), "ok": 0, "failed": 0, "cached": 0}
    failures: list[dict] = []
    details: list[dict] = []

    for i, item in enumerate(items):
        try:
            account_id = UUID(item["pricing_account_id"])
            product_id = UUID(item["pricing_product_id"])
        except (KeyError, ValueError) as e:
            summary["failed"] += 1
            failures.append({"code": "bad_input", "detail": str(e)})
            job.processed = i + 1
            continue

        key = f"{idempotency_prefix}:{i}" if idempotency_prefix else None
        outcome = await push_one(
            session,
            user=user,
            account_id=account_id,
            product_id=product_id,
            idempotency_key=key,
            contas_permitidas=permitidas,
        )
        # O job fica legível para quem o criou: sem kit/comissão/margem/frete
        # se não for ADM (M-10).
        resultado = resultado_para(user, outcome)
        if outcome.ok:
            summary["ok"] += 1
        else:
            summary["failed"] += 1
            failures.append(resultado)
        if outcome.cached:
            summary["cached"] += 1
        details.append(resultado)

        job.processed = i + 1
        if job.processed % 10 == 0:
            job.last_heartbeat_at = _now()
            await session.commit()

    job.status = BackgroundJobStatus.SUCCEEDED
    job.result = {"summary": summary, "details": details[-50:]}
    job.finished_at = _now()
    await session.commit()

    # M-20 (23/11/2026): o relatório vai ao sino dos administradores (alerta
    # ADM, um por lote), com quem enviou. Nunca derruba o job.
    try:
        department_label = await _infer_batch_department(session, items)
    except Exception as e:  # noqa: BLE001
        logger.warning("batch_push_departamento_falhou", err=type(e).__name__)
        department_label = None
    titulo, texto, erros = _format_report(summary, failures, department_label)
    quem = user.name or user.email or str(user.id)
    await avisar_administradores(
        titulo,
        f"Enviado por: {quem}\n{texto}",
        severity=(
            AlertSeverity.SUCCESS
            if summary["failed"] == 0
            else AlertSeverity.ERROR
            if erros
            else AlertSeverity.WARNING
        ),
        payload={"preco_lote": str(job_id), "summary": summary},
        dedupe_key=f"preco_lote:{job_id}",
    )
