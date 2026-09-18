"""Aviso Threema do PRAZO DE RESPOSTA da loja nas devoluções em andamento.

Vinicius, 16/09/2026: "teria como colocar no painel prazo resposta da loja para
não perder mais prazo?". O marketplace espera uma ação da loja num caso vivo
(TikTok: confirmar/recusar o pacote que chegou, responder ao reembolso...) e,
passado o prazo, decide sozinho — o 294865 foi aprovado por prazo e a TikTok
devolveu R$ 744 ao cliente sem ninguém ver.

O sync do retorno (`devolucao_rastreio_sync`, a cada 30 min) já grava a ação
pendente e o prazo em `devolucao_rastreio.acao_auto/prazo_acao_auto`. Este
módulo roda logo depois, na mesma rodada, e manda UM aviso por (caso, prazo)
quando faltam menos de `ANTECEDENCIA` — carimbando `aviso_prazo_acao_at` e
`aviso_prazo_acao_para` (o prazo avisado). Se a plataforma abrir outra ação
com prazo novo no mesmo pedido, avisa de novo. Prazo já vencido há mais de
`TOLERANCIA_VENCIDO` não avisa (é passado; o painel mostra "vencido").

Quem recebe é o cadastro `devolucoes_auto` do modal Informar da aba
Devoluções (threema_informar_config) — mesmo mecanismo dos avisos da Amazon
(`logistica_amazon_auto`) e da Margem. Sem destinatário cadastrado não manda
nem carimba: quando alguém for cadastrado, os avisos pendentes saem na rodada
seguinte. Espelho do formato de `logistica_amazon_avisos.mensagem_aviso`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Logistica, ThreemaInformarConfig
from app.services import logistica_rules, threema

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

CONTEXTO_AUTO = "devolucoes_auto"
# Avisa quando falta menos que isto pro prazo (mesma antecedência do
# "Prazo contest." da aba Lançamentos)...
ANTECEDENCIA = timedelta(hours=24)
# ...e de novo, uma vez, quando falta menos que isto — um aviso só, 24 h
# antes, era a última palavra antes de a plataforma decidir sozinha.
URGENTE = timedelta(hours=4)
# Prazo que já passou há mais que isto não rende aviso — a plataforma já
# decidiu; o que resta é o painel dizer "vencido".
TOLERANCIA_VENCIDO = timedelta(hours=24)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _fmt_dt(dt: datetime | None) -> str:
    dt = _utc(dt)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m %H:%M") if dt else "—"


def falta(prazo: datetime | None, agora: datetime | None = None) -> str:
    """"faltam 9 h" / "faltam 2 dias" / "vencido há 3 h", em texto de gente.
    Horas inteiras pra baixo nos dois sentidos (2 h 50 min → "2 h")."""
    prazo = _utc(prazo)
    if prazo is None:
        return ""
    agora = _utc(agora) or datetime.now(UTC)
    segundos = (prazo - agora).total_seconds()
    horas = int(abs(segundos) // 3600)
    if segundos < 0:
        if horas < 1:
            return "vencido há menos de 1 h"
        return f"vencido há {horas} h" if horas < 48 else f"vencido há {horas // 24} dias"
    if horas < 1:
        return "vence em menos de 1 h"
    if horas < 48:
        return f"faltam {horas} h"
    return f"faltam {horas // 24} dias"


def _tem_robo_proprio(row: DevolucaoRastreio) -> bool:
    """O pedido de SÓ reembolso pendente (TikTok `return_type` REFUND +
    ação SELLER_RESPOND_REFUND) já tem robô próprio: o vigia
    `chamados_tiktok_reembolso` avisa no Threema faltando 12 h e 3 h sem
    resposta (carimbando as MESMAS colunas aviso_prazo_acao_*). Aqui não
    repete — a coluna da aba continua mostrando o prazo dele. Só essa
    combinação exata: qualquer outra ação num caso só-reembolso ninguém mais
    avisa."""
    return (
        (row.devolucao_tipo_auto or "").strip().upper() == "REFUND"
        and (row.acao_auto or "").strip().upper() == "SELLER_RESPOND_REFUND"
        and (row.devolucao_status_auto or "").strip().upper()
        == "RETURN_OR_REFUND_REQUEST_PENDING"
    )


def aviso_devido(row: DevolucaoRastreio, agora: datetime | None = None) -> bool:
    """True quando ESTA rodada deve avisar este caso: há prazo, ele está na
    janela [agora - TOLERANCIA_VENCIDO, agora + ANTECEDENCIA], o caso não é
    de um robô que já avisa, e (a) nunca foi avisado pra esse prazo, ou (b)
    entrou na faixa URGENTE e o aviso anterior saiu antes dela (o segundo e
    último aviso)."""
    prazo = _utc(row.prazo_acao_auto)
    if prazo is None or _tem_robo_proprio(row):
        return False
    agora = _utc(agora) or datetime.now(UTC)
    if prazo > agora + ANTECEDENCIA or prazo < agora - TOLERANCIA_VENCIDO:
        return False
    if _utc(row.aviso_prazo_acao_para) != prazo:
        return True
    anterior = _utc(row.aviso_prazo_acao_at)
    urgente = prazo - agora <= URGENTE
    return urgente and anterior is not None and prazo - anterior > URGENTE


def mensagem_aviso(
    row: DevolucaoRastreio, linha: Logistica | None, agora: datetime | None = None
) -> str:
    """Texto do aviso: quem, o quê, até quando, e o que acontece se ninguém
    responder. `linha` = a linha da Logística do pedido (conta, marketplace,
    cliente) — pode faltar; o pedido Bling vai sempre."""
    agora = _utc(agora) or datetime.now(UTC)
    prazo = _utc(row.prazo_acao_auto)
    fonte = (row.fonte_auto or "").strip().lower()
    plataforma = {"tiktok": "TikTok", "shopee": "Shopee", "ml": "Mercado Livre"}.get(
        fonte, (linha.plataforma if linha and linha.plataforma else fonte or "marketplace")
    )
    acao = logistica_rules.acao_plataforma_pt(fonte, row.acao_auto) or "Responder na plataforma"
    quanto = falta(prazo, agora)
    vencido = prazo is not None and prazo < agora
    urgente = prazo is not None and not vencido and prazo - agora <= URGENTE
    if vencido:
        cabecalho = f"🚨 DaVinci — Devolução {plataforma}: prazo de resposta VENCIDO ({quanto})"
    elif urgente:
        cabecalho = f"🚨 DaVinci — Devolução {plataforma}: {quanto} pra responder — ÚLTIMO AVISO"
    else:
        cabecalho = f"⚠️ DaVinci — Devolução {plataforma}: {quanto} pra responder na plataforma"
    conta = (linha.conta or "").strip() if linha else ""
    mk = (linha.pedido_marketplace or "").strip() if linha else ""
    linhas = [
        cabecalho,
        f"Pedido {row.pedido_bling} ({conta or '?'}) · {plataforma} {mk or '?'}",
    ]
    if linha and (linha.cliente_nome or "").strip():
        linhas.append(f"Cliente: {linha.cliente_nome.strip()}")
    rastreio = (row.rastreio or row.rastreio_auto or "").strip()
    if rastreio:
        transp = (row.transportadora_auto or "").strip()
        linhas.append(f"Pacote de volta: {rastreio}{f' ({transp})' if transp else ''}")
    situacao = logistica_rules.devolucao_status_pt(
        plataforma, {"return_status": row.devolucao_status_auto or "", "return_type": row.devolucao_tipo_auto or ""}
    )
    if situacao:
        linhas.append(f"Situação: {situacao}")
    linhas.append(f"O que fazer: {acao}")
    linhas.append(f"Prazo: {_fmt_dt(prazo)} ({quanto})")
    linhas.append(
        "Passado o prazo a plataforma decide sozinha (aprova a devolução/reembolso "
        "sem a loja)."
        if not vencido
        else "A plataforma pode já ter decidido sozinha. Confira o caso agora."
    )
    return "\n".join(linhas)


async def recipients_auto(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO_AUTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def run(
    session: AsyncSession,
    *,
    pedidos: Collection[str],
    linhas: Mapping[str, Logistica] | None = None,
    agora: datetime | None = None,
    client: Any = None,
) -> dict[str, int]:
    """Uma rodada: dos pedidos dados (os que estão em Aguardando Devolução
    agora E cujo caso foi relido nesta rodada — prazo velho de conta que deu
    429 não vira aviso), manda o aviso dos que estão na janela e carimba cada
    um. Commita. `linhas` = {pedido_bling: Logistica} já carregado pelo sync."""
    agora = _utc(agora) or datetime.now(UTC)
    resumo = {"casos": 0, "devidos": 0, "enviados": 0, "falhas": 0, "sem_destinatarios": 0}
    alvo = {str(p).strip() for p in pedidos if str(p).strip()}
    if not alvo:
        return resumo
    rows = (
        await session.execute(
            select(DevolucaoRastreio).where(
                DevolucaoRastreio.pedido_bling.in_(sorted(alvo)),
                DevolucaoRastreio.prazo_acao_auto.is_not(None),
            )
        )
    ).scalars().all()
    resumo["casos"] = len(rows)
    devidos = [r for r in rows if aviso_devido(r, agora)]
    resumo["devidos"] = len(devidos)
    if not devidos:
        return resumo
    recipients = await recipients_auto(session)
    if not recipients:
        resumo["sem_destinatarios"] = 1
        logger.warning("devolucao_acao_avisos_sem_destinatarios", devidos=len(devidos))
        return resumo
    ctx: dict[str, Logistica] = dict(linhas or {})
    faltando = [r.pedido_bling for r in devidos if r.pedido_bling not in ctx]
    if faltando:
        for lg in (
            await session.execute(select(Logistica).where(Logistica.pedido_bling.in_(faltando)))
        ).scalars():
            ctx.setdefault(lg.pedido_bling or "", lg)
    client = client or threema.ThreemaClient()
    for row in devidos:
        texto = mensagem_aviso(row, ctx.get(row.pedido_bling), agora)
        try:
            res = await client.send_to_all(texto, recipients)
        except Exception as e:  # noqa: BLE001 — Threema fora do ar: tenta na próxima
            resumo["falhas"] += 1
            logger.warning(
                "devolucao_acao_aviso_falhou", pedido=row.pedido_bling, err=str(e)[:200]
            )
            continue
        if res.get("sent"):
            row.aviso_prazo_acao_at = agora
            row.aviso_prazo_acao_para = row.prazo_acao_auto
            resumo["enviados"] += 1
            logger.info(
                "devolucao_acao_aviso_enviado",
                pedido=row.pedido_bling,
                acao=row.acao_auto,
                prazo=str(row.prazo_acao_auto),
                sent=res.get("sent"),
                failed=res.get("failed"),
            )
        else:
            resumo["falhas"] += 1
    await session.commit()
    return resumo
