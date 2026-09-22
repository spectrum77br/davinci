"""Varredura das aberturas PRESAS na aba Chamados (Eduardo 17/09).

O buraco (medido em 17/09): o disparo tenta abrir o chamado na plataforma, a API
responde que não dá (`devolucao_sem_return`, `devolucao_sem_foto`, …), a mensagem
de abertura fica `pendente`/`falhou` e ACABA ALI. O `processar_pendentes` (cron de
hora em hora) re-tenta as `pendente`, recebe o mesmo "não dá" e ninguém é avisado;
as `falhou` nem isso. Resultado: 290112 (Marquezini, R$ 6.700) e mais 7 casos
parados desde 08–11/09, somando mais de R$ 16 mil.

Esta varredura fecha o buraco. Para cada chamado ABERTO cuja abertura está presa
há mais tempo que a tolerância do motivo:

  ESPERA      a plataforma ainda vai liberar (pacote a caminho, arbitragem em
              curso). Só vira alerta quando passa de `TOLERANCIA_ESPERA`.
  ROBO        não existe caminho pela API — o chamado é aberto na mão no Seller
              Center / formulário. A mensagem passa pro canal `robo`, ou seja,
              entra na fila do `POST /api/chamados/agent/lease` daquela
              plataforma, e sai da ilusão de que a API vai resolver.
  HUMANO      falta insumo nosso (foto, nº do pedido no marketplace) ou é motivo
              que não abre chamado: avisa no Threema com valor e dias parados.

Cada mudança de rota é carimbada UMA VEZ no histórico do chamado (marca `MARCA`),
então re-rodar não duplica nem no histórico nem no Threema.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados as chamados_svc
from app.services import threema
from app.services.chamados_devolucao import TIPO_ABERTURA
from app.services.chamados_devolucao_sync import ABERTURA_FALHOU_ACOMPANHA
from app.services.chamados_tiktok_reembolso import _destinatarios

logger = structlog.get_logger()

MARCA = "Abertura presa"

# Quanto tempo a abertura pode ficar presa antes da varredura mexer. A tolerância
# maior é pra quem depende do tempo da plataforma (pacote em trânsito).
TOLERANCIA = timedelta(hours=12)
TOLERANCIA_ESPERA = timedelta(days=3)

# A plataforma ainda não liberou — o retry de hora em hora é o caminho certo.
ESPERA = frozenset({
    "tiktok_recusa_bloqueada",
    "tiktok_aguardando_pacote",
    "tiktok_arbitragem",
    "shopee_aguardando_pacote",
    "return_review_indisponivel",
})

# Sem caminho pela API: quem abre é o robô na tela da plataforma.
ROBO = frozenset({
    "devolucao_sem_return",
    "devolucao_nao_encontrada",
    "plataforma_sem_api",
})

# Falta insumo nosso ou é decisão de gente.
HUMANO = frozenset({
    "devolucao_sem_foto",
    "devolucao_motivo_sem_chamado",
    "devolucao_sem_pedido_marketplace",
    "devolucao_sem_claim",
})

_TEXTO_ROTA = {
    "devolucao_sem_return": (
        "a Shopee/TikTok não tem devolução (return) pra este pedido, então não dá pra "
        "contestar pela API — tem que abrir na tela da plataforma"
    ),
    "devolucao_nao_encontrada": "a devolução sumiu do DaVinci — conferir a linha em Devoluções",
    "plataforma_sem_api": "a plataforma não tem API pra abrir chamado",
    "devolucao_sem_foto": "falta foto na linha da devolução pra poder contestar",
    "devolucao_motivo_sem_chamado": "o motivo da devolução não abre chamado nesta plataforma",
    "devolucao_sem_pedido_marketplace": "a linha não tem o número do pedido no marketplace",
    "devolucao_sem_claim": "o Mercado Livre não tem reclamação aberta pra este pedido",
}


def _rota(erro: str | None) -> str:
    e = (erro or "").strip()
    if e in ESPERA:
        return "espera"
    if e in ROBO:
        return "robo"
    if e in HUMANO:
        return "humano"
    if e in ABERTURA_FALHOU_ACOMPANHA:
        return "acompanha"  # o sync já segue o desfecho; nada a fazer aqui
    return "humano"


def _dias(desde: datetime, agora: datetime) -> int:
    return max(0, (agora - desde).days)


def _linha(ch: Chamado, erro: str | None, valor: float | None, dias: int) -> str:
    val = (
        f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if valor
        else "sem valor"
    )
    return (
        f"• {ch.pedido_bling} ({ch.plataforma or '?'} {ch.conta or '?'}) — {val}, "
        f"parado há {dias} dia(s): {_TEXTO_ROTA.get((erro or '').strip(), erro or 'sem motivo')}"
    )


async def _valor(session: AsyncSession, ch: Chamado) -> float | None:
    """Custo do produto da devolução que gerou o chamado (o que está em risco)."""
    if not ch.origem_ref:
        return None
    try:
        dev = await session.get(Devolution, UUID(str(ch.origem_ref)))
    except (ValueError, AttributeError):
        return None
    return float(dev.custo_produto) if dev and dev.custo_produto else None


async def _ja_carimbado(session: AsyncSession, ch: Chamado, erro: str | None) -> bool:
    marca = f"{MARCA} ({(erro or 'sem motivo').strip()})"
    achou = (
        await session.execute(
            select(ChamadoMensagem.id)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo == "sistema",
                ChamadoMensagem.texto.ilike(f"%{marca}%"),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return achou is not None


async def varrer(
    session: AsyncSession, *, agora: datetime | None = None, dry_run: bool = False
) -> dict:
    """Cron: roteia toda abertura presa e avisa no Threema o que precisa de gente.

    Devolve o resumo por rota. `dry_run` não grava nem manda Threema."""
    agora = agora or datetime.now(UTC)
    rows = (
        await session.execute(
            select(ChamadoMensagem, Chamado)
            .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
            .where(
                ChamadoMensagem.tipo == TIPO_ABERTURA,
                ChamadoMensagem.status.in_(("pendente", "falhou")),
                ChamadoMensagem.canal != "robo",  # já está na fila do robô
                Chamado.resolvido.is_(False),
            )
            .order_by(ChamadoMensagem.created_at)
        )
    ).all()

    resumo = {"vistos": len(rows), "robo": 0, "humano": 0, "espera": 0, "acompanha": 0, "novo": 0}
    avisos: list[str] = []
    for msg, ch in rows:
        presa_desde = msg.updated_at or msg.created_at
        rota = _rota(msg.erro)
        limite = TOLERANCIA_ESPERA if rota == "espera" else TOLERANCIA
        if agora - presa_desde < limite:
            continue
        resumo[rota] = resumo.get(rota, 0) + 1
        if rota == "acompanha" or await _ja_carimbado(session, ch, msg.erro):
            continue
        resumo["novo"] += 1
        valor = await _valor(session, ch)
        dias = _dias(presa_desde, agora)
        motivo = _TEXTO_ROTA.get((msg.erro or "").strip(), msg.erro or "sem motivo")
        if dry_run:
            avisos.append(_linha(ch, msg.erro, valor, dias))
            continue
        if rota == "robo":
            # Entra na fila do lease da plataforma: o robô abre no Seller Center.
            msg.canal = "robo"
            msg.status = "pendente"
            nota = (
                f"{MARCA} ({msg.erro}) há {dias} dia(s): {motivo}. "
                "Encaminhado ao robô pra abrir na tela da plataforma."
            )
        else:
            nota = f"{MARCA} ({msg.erro}) há {dias} dia(s): {motivo}. Precisa de gente."
        session.add(chamados_svc.registrar_sistema(ch, nota))
        avisos.append(_linha(ch, msg.erro, valor, dias))

    if not dry_run:
        await session.commit()
        if avisos:
            alvos = _destinatarios()
            texto = (
                "⚠️ Chamados com abertura presa (varredura):\n" + "\n".join(avisos[:15])
                + ("\n…" if len(avisos) > 15 else "")
            )
            if alvos:
                try:
                    await threema.ThreemaClient(contexto="logistica").send_to_all(
                        texto, recipients=alvos
                    )
                except Exception as e:  # noqa: BLE001 — aviso é best-effort
                    logger.warning("chamados_pendencias_threema_falhou", err=str(e)[:200])
    resumo["avisos"] = avisos
    logger.info(
        "chamados_pendencias_varredura",
        **{k: v for k, v in resumo.items() if k != "avisos"},
    )
    return resumo
