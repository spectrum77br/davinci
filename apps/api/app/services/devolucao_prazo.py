"""Prazo da PLATAFORMA pra contestar a devolução (Shopee/TikTok) + aviso.

Eduardo, 09/09/2026: "quando for item faltando tem que abrir chamado
automático também". O automático já existia; o 291835 (Shopee Barbosa)
perdeu a contestação porque o motivo só foi preenchido 3 dias depois de o
pacote voltar — a Shopee já tinha fechado o `return_seller_due_date`. A causa
é o MOTIVO chegar tarde, não o chamado; então:

  1. `atualizar_prazos`: a cada 30 min pergunta à plataforma até quando dá pra
     contestar (uma vez por devolução; de 6 em 6 h nas SEM MOTIVO) e grava
     `prazo_contestacao` (Shopee: `return_seller_due_date` do detalhe da
     devolução; TikTok: menor `deadline` de `seller_next_action_response`).
     ML não expõe prazo — fica sem.
  2. `avisar_prazos`: devolução com MOTIVO VAZIO e prazo em menos de 24 h ganha
     um aviso no Threema (grupo geral, o mesmo dos chamados), uma vez só.
  3. A tela mostra a coluna "Prazo contest." em vermelho quando falta < 24 h.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Devolution
from app.services import chamados_devolucao as cd
from app.services import threema

logger = structlog.get_logger()

BRT = ZoneInfo("America/Sao_Paulo")
JANELA_DIAS = 45
# Reconsulta a plataforma no máximo a cada tantas horas por devolução.
RECONSULTA_HORAS = 6
# Teto de consultas por rodada (cada uma é 1-3 chamadas à API da plataforma).
MAX_POR_RODADA = 60
AVISO_ANTECEDENCIA = timedelta(hours=24)
# Prazo fora dessa faixa = campo em outra unidade/lixo da plataforma: ignora.
PRAZO_MAX_FUTURO = timedelta(days=180)
PRAZO_MAX_PASSADO = timedelta(days=180)


def prazo_shopee(det: dict[str, Any]) -> datetime | None:
    """`return_seller_due_date` (epoch s) do detalhe da devolução."""
    return cd._epoch((det or {}).get("return_seller_due_date"))


def prazo_tiktok(caso: dict[str, Any]) -> datetime | None:
    """Menor `deadline` das ações que a TikTok ainda espera do vendedor
    (`seller_next_action_response: [{action, deadline}]`, epoch s)."""
    prazos = [
        cd._epoch(a.get("deadline"))
        for a in ((caso or {}).get("seller_next_action_response") or [])
        if isinstance(a, dict)
    ]
    prazos = [p for p in prazos if p is not None]
    return min(prazos) if prazos else None


async def _plataforma(session: AsyncSession, dev: Devolution) -> str | None:
    """ml | shopee | tiktok | amazon | None. A `conta` da linha é o NOME DA
    LOJA no Bling ("Shopee Barbosa", "Loja 205660518") e quase nunca casa com
    o nome da integração, então o caminho bom é o espelho do pedido
    (bling_orders.loja → store_info.platform); a conta fica de reserva."""
    info = await cd.chamados_svc.lookup_pedido(session, dev.pedido_bling or "")
    plat = cd.plataforma_de((info or {}).get("plataforma"))
    if plat:
        return plat
    return cd.plataforma_de(await cd._plataforma_da_conta(session, dev.conta))


async def _prazo_da_plataforma(
    session: AsyncSession, dev: Devolution
) -> tuple[str | None, datetime | None]:
    """(plataforma, prazo) consultando a plataforma da conta da devolução."""
    plat = await _plataforma(session, dev)
    if plat == cd.PLAT_SHOPEE:
        client = await cd._shopee_client_para(session, None, dev)
        return_sn = await cd._return_sn_shopee(session, client, dev)
        if not return_sn:
            return plat, None
        det = await client.get_return_detail(return_sn)
        return plat, prazo_shopee(det)
    if plat == cd.PLAT_TIKTOK:
        client = await cd._tiktok_client_para(session, None, dev)
        rastreio = await cd._rastreio_devolucao(session, dev, cd.PLAT_TIKTOK)
        preferido = (rastreio.devolucao_id_auto or "").strip() if rastreio else ""
        caso = await cd._return_tiktok(client, dev, preferido or None)
        return plat, prazo_tiktok(caso) if caso else None
    return plat, None


async def _candidatas(session: AsyncSession) -> list[Devolution]:
    agora = datetime.now(UTC)
    corte_recon = agora - timedelta(hours=RECONSULTA_HORAS)
    sem_motivo = or_(Devolution.motivo_devolucao.is_(None), Devolution.motivo_devolucao == "")
    stmt = (
        select(Devolution)
        .where(Devolution.created_at >= agora - timedelta(days=JANELA_DIAS))
        # Pergunta UMA vez pra toda devolução (assim a coluna aparece pra
        # todo mundo) e fica repetindo só nas SEM MOTIVO — que são as que
        # correm risco de perder o prazo e as que geram o aviso no Threema.
        .where(
            or_(
                Devolution.prazo_contestacao_at.is_(None),
                and_(sem_motivo, Devolution.prazo_contestacao_at < corte_recon),
            )
        )
        # Prazo já vencido há mais de 2 dias: não muda mais, não gasta API.
        .where(
            or_(
                Devolution.prazo_contestacao.is_(None),
                Devolution.prazo_contestacao > agora - timedelta(days=2),
            )
        )
        .order_by(Devolution.prazo_contestacao_at.asc().nullsfirst(), Devolution.created_at.desc())
        .limit(MAX_POR_RODADA)
    )
    return list((await session.execute(stmt)).scalars().all())


async def atualizar_prazos(session: AsyncSession) -> dict[str, int]:
    """Consulta a plataforma e grava `prazo_contestacao` nas devoluções recentes."""
    resumo = {"consultadas": 0, "com_prazo": 0, "sem_api": 0, "erros": 0}
    for dev in await _candidatas(session):
        agora = datetime.now(UTC)
        try:
            plat, prazo = await _prazo_da_plataforma(session, dev)
        except (cd._PendenteError, cd.chamados_svc.ChamadoError) as e:
            # Sem pedido/integração/devolução na plataforma: não é erro de rede,
            # mas também não há prazo — tenta de novo daqui a RECONSULTA_HORAS.
            logger.info("devolucao_prazo_indisponivel", devolucao=str(dev.id), motivo=str(e)[:80])
            plat, prazo = None, None
        except Exception as e:  # noqa: BLE001 — plataforma fora do ar: não derruba a rodada
            logger.warning("devolucao_prazo_falhou", devolucao=str(dev.id), err=str(e)[:200])
            resumo["erros"] += 1
            dev.prazo_contestacao_at = agora
            continue
        resumo["consultadas"] += 1
        if plat not in (cd.PLAT_SHOPEE, cd.PLAT_TIKTOK):
            resumo["sem_api"] += 1
        if prazo is not None and not (agora - PRAZO_MAX_PASSADO < prazo < agora + PRAZO_MAX_FUTURO):
            logger.warning(
                "devolucao_prazo_absurdo", devolucao=str(dev.id), prazo=prazo.isoformat()
            )
            prazo = None
        if prazo is not None:
            dev.prazo_contestacao = prazo
            resumo["com_prazo"] += 1
        dev.prazo_contestacao_at = agora
    await session.commit()
    logger.info("devolucao_prazo_atualizado", **resumo)
    return resumo


def _texto_aviso(devs: list[Devolution]) -> str:
    linhas = []
    for d in devs:
        quando = (
            d.prazo_contestacao.astimezone(BRT).strftime("%d/%m %H:%M")
            if d.prazo_contestacao
            else "?"
        )
        linhas.append(f"• Pedido {d.pedido_bling or '?'} ({d.conta}) — prazo {quando}")
    return (
        "Devolução SEM MOTIVO com prazo de contestação acabando (< 24 h). "
        "Preencha o motivo na aba Devoluções para o chamado sair a tempo:\n" + "\n".join(linhas)
    )


async def avisar_prazos(session: AsyncSession) -> int:
    """Threema (grupo geral) pras devoluções com motivo vazio e prazo em < 24 h
    que ainda não foram avisadas. Devolve quantas avisou."""
    agora = datetime.now(UTC)
    devs = list(
        (
            await session.execute(
                select(Devolution)
                .where(Devolution.aviso_prazo_at.is_(None))
                .where(
                    or_(Devolution.motivo_devolucao.is_(None), Devolution.motivo_devolucao == "")
                )
                .where(Devolution.prazo_contestacao.is_not(None))
                .where(Devolution.prazo_contestacao > agora)
                .where(Devolution.prazo_contestacao <= agora + AVISO_ANTECEDENCIA)
                .order_by(Devolution.prazo_contestacao.asc())
            )
        )
        .scalars()
        .all()
    )
    if not devs:
        return 0
    destinos = threema.parse_recipients(get_settings().threema_recipients)
    if not destinos:
        logger.warning("devolucao_prazo_aviso_sem_threema", devolucoes=len(devs))
        return 0
    try:
        res = await threema.ThreemaClient().send_to_all(_texto_aviso(devs), destinos)
    except Exception as e:  # noqa: BLE001 — Threema fora do ar: tenta na próxima rodada
        logger.warning("devolucao_prazo_aviso_falhou", err=str(e)[:200])
        return 0
    if not res.get("sent"):
        return 0
    for d in devs:
        d.aviso_prazo_at = agora
    await session.commit()
    logger.info("devolucao_prazo_avisado", devolucoes=len(devs), sent=len(res.get("sent") or []))
    return len(devs)


async def run(session: AsyncSession) -> dict[str, int]:
    resumo = await atualizar_prazos(session)
    resumo["avisadas"] = await avisar_prazos(session)
    return resumo
