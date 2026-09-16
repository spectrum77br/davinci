"""Abrir chamado da aba Status pra TikTok e Shopee — direto pro robô.

Vinicius (16/09/2026): "quando eu cadastrar status no TikTok, Shopee e colocar
chamado sim, ele precisa abrir o chamado. Não faz sentido nosso pessoal ter
deixado desativado isso." E o desenho: "se chegar no painel de chamado, o robô
do Eduardo vai abrir e resolver".

Até aqui a caixa "Abrir chamado" da aba Status só tinha efeito no Mercado
Livre (`logistica_meli.abrir_chamados_em_lote`: mediação pela API ou, sem
reclamação do comprador, o robô do formulário de ajuda). TikTok e Shopee não
têm API pra loja abrir uma reclamação, então a regra não fazia nada e o caso
ficava invisível — foi assim que o 294865 (TikTok Mini) passou do prazo.

Aqui o caminho é o mesmo do "sem reclamação" do ML: a linha que casa uma regra
APLICÁVEL AO ESTADO ATUAL com `abrir_chamado` vai pra aba Chamados no canal
robô, com a Mensagem do chamado e as imagens da regra como abertura PENDENTE.
O robô puxa em `POST /api/chamados/agent/lease` (a tarefa já leva plataforma,
conta, pedido, texto e anexos), abre no Seller Center e devolve o protocolo em
`/agent/resultado`, que cai na linha da Logística (`chamado`) e no histórico.
Enquanto o robô não souber abrir naquela plataforma, a tarefa fica pendente e
visível na aba — nada se perde.

Roda no motor do recarregar ANTES da troca de situação no Bling (mesma razão
do ML: a regra com `status_atual` ainda é a ativa nesse instante). Dedupe pela
linha (`chamado_da_logistica`): já tem protocolo → sincroniza; abertura ainda
pendente/enviando → não enfileira de novo. Commit por linha.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Chamado, Logistica, LogisticaStatus
from app.services import chamados as chamados_svc
from app.services import logistica_match, logistica_rules

logger = structlog.get_logger()

# Plataformas cujo "Abrir chamado" vai direto pro robô (sem API de reclamação).
_PLATAFORMAS_ROBO = logistica_rules._TIKTOK_PLATAFORMAS | logistica_rules._SHOPEE_PLATAFORMAS

# Linha mais velha que isso não entra sozinha (mesma janela dos sweeps): a
# caixa nunca teve efeito nessas plataformas, então na 1ª rodada o backlog
# viraria uma enxurrada de tarefas pro robô. Caso antigo é de olhar na aba.
_JANELA = timedelta(days=45)

# Carimbos em `logistica.chamado_auto_erro` (a tela traduz em CHAMADO_ERROS).
ERRO_SEM_MENSAGEM = "logistica_sem_mensagem_chamado"  # mesmo do ML
ERRO_ENCAMINHADO = "encaminhado_ao_robo"  # mesmo do ML
ERRO_MANUAL_NA_ABA = "chamado_manual_na_aba"  # operador já registrou à mão
ERRO_RESOLVIDO_NA_ABA = "chamado_resolvido_na_aba"  # resolvido sem protocolo
ERRO_OUTRA_ORIGEM = "chamado_de_outra_origem"  # devolução/vendas já cuidam
ERRO_ROBO_SEM_PROTOCOLO = "robo_sem_protocolo"  # robô disse ok sem nº
ERRO_ROBO_FALHOU = "robo_falhou"  # + ": motivo" — reenvio é humano, na aba


def eh_plataforma_robo(plataforma: str | None) -> bool:
    return (plataforma or "").strip().lower() in _PLATAFORMAS_ROBO


async def _chamado_de_outra_origem(session: AsyncSession, row: Logistica) -> Chamado | None:
    """Chamado ABERTO do mesmo pedido vindo de outra aba (devolução, vendas,
    margem, robô via /agent/registrar). Se existe, alguém já cuida do caso —
    abrir um segundo no Seller Center só confunde a plataforma."""
    numero = (row.pedido_bling or "").strip()
    if not numero:
        return None
    return (
        await session.execute(
            select(Chamado)
            .where(
                Chamado.pedido_bling == numero,
                Chamado.origem != "logistica",
                Chamado.resolvido.is_(False),
            )
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _carimbar(row: Logistica, erro: str, agora: datetime) -> bool:
    """Grava o motivo na linha só quando muda (evita commit a cada rodada)."""
    if row.chamado_auto_erro == erro:
        return False
    row.chamado_auto_at = agora
    row.chamado_auto_erro = erro
    return True


async def abrir_chamados_em_lote(
    session: AsyncSession,
    ids: Collection[UUID] | None = None,
    *,
    agora: datetime | None = None,
) -> dict[str, int]:
    """Executor do "Abrir chamado" da aba Status pra TikTok/Shopee.

    - robo: chamado criado agora na aba (canal robô, abertura pendente).
    - adiados: já existe abertura pendente/enviando pro pedido (robô na fila).
    - pulados: regra pede chamado mas está sem "Mensagem do chamado" — nada a
      mandar; fica carimbado `logistica_sem_mensagem_chamado` pra aparecer.
    - falhas: erro inesperado ao registrar (logado; tenta na próxima rodada).
    """
    agora = agora or datetime.now(UTC)
    status_rows = list(
        (
            await session.execute(
                select(LogisticaStatus).options(selectinload(LogisticaStatus.anexos))
            )
        )
        .scalars()
        .all()
    )
    stmt = select(Logistica)
    if ids is not None:
        stmt = stmt.where(Logistica.id.in_(list(ids)))
    rows = [
        r
        for r in (await session.execute(stmt)).scalars().all()
        if eh_plataforma_robo(r.plataforma)
    ]
    hoje = agora.date()
    robo = adiados = pulados = falhas = 0
    for row in rows:
        if (row.chamado or "").strip():
            continue  # já tem chamado (robô ou operador)
        if row.data is not None and row.data < hoje - _JANELA:
            continue  # backlog antigo: não vira tarefa sozinho
        assinatura = logistica_rules.assinatura_para(row.plataforma, row.meli_status or {})
        cands = logistica_match.find_matching_rules(
            status_rows, assinatura=assinatura, plataforma=row.plataforma
        )
        aplicaveis = logistica_match.regras_aplicaveis(cands, row.status_bling)
        rule = next((r for r in aplicaveis if r.abrir_chamado), None)
        if rule is None:
            continue
        mensagem = (rule.mensagem_chamado or "").strip()
        if not mensagem:
            if _carimbar(row, ERRO_SEM_MENSAGEM, agora):
                await session.commit()
            pulados += 1
            continue
        existente = await chamados_svc.chamado_da_logistica(session, row)
        if existente is None:
            outro = await _chamado_de_outra_origem(session, row)
            if outro is not None:
                if (outro.chamado or "").strip():
                    row.chamado = outro.chamado
                    row.chamado_auto_at = agora
                    row.chamado_auto_erro = None
                elif _carimbar(row, ERRO_OUTRA_ORIGEM, agora):
                    pass
                await session.commit()
                adiados += 1
                continue
        else:
            if (existente.chamado or "").strip():
                # O robô (ou o operador) já devolveu o protocolo — só espelha.
                row.chamado = existente.chamado
                row.chamado_auto_at = agora
                row.chamado_auto_erro = None
                await session.commit()
                continue
            if existente.resolvido:
                # Resolvido na aba sem protocolo ("não precisa"): fim.
                if _carimbar(row, ERRO_RESOLVIDO_NA_ABA, agora):
                    await session.commit()
                continue
            if (existente.canal or "") != "robo":
                # Operador registrou à mão (canal manual/api) e ainda vai
                # preencher o protocolo — não passa por cima nem manda o robô
                # abrir outro no Seller Center.
                if _carimbar(row, ERRO_MANUAL_NA_ABA, agora):
                    await session.commit()
                adiados += 1
                continue
            abertura = await chamados_svc.abertura_do_chamado(session, existente)
            if abertura is not None and abertura.status in ("pendente", "enviando"):
                adiados += 1
                continue
            if abertura is not None and abertura.status == "falhou":
                # Robô devolveu falha: fica no histórico da aba Chamados e o
                # reenvio é humano, de lá. Não re-enfileira a cada rodada.
                erro = f"{ERRO_ROBO_FALHOU}: {(abertura.erro or '')[:150]}".rstrip(": ")
                if _carimbar(row, erro, agora):
                    await session.commit()
                falhas += 1
                continue
            if abertura is not None and abertura.status == "enviada":
                # Robô disse que abriu mas não trouxe o nº: o operador
                # preenche na aba; nada a enfileirar.
                if _carimbar(row, ERRO_ROBO_SEM_PROTOCOLO, agora):
                    await session.commit()
                adiados += 1
                continue
        try:
            await chamados_svc.abrir_chamado_logistica(
                session,
                row,
                mensagem=mensagem,
                regra=rule.status_plataforma,
                anexos=list(rule.anexos or []),
            )
        except Exception as e:  # noqa: BLE001 — best-effort, não derruba o lote
            row.chamado_auto_at = agora
            row.chamado_auto_erro = str(e)[:200]
            falhas += 1
            logger.warning(
                "logistica_chamado_robo_falhou",
                id=str(row.id),
                pedido=row.pedido_marketplace,
                plataforma=row.plataforma,
                err=str(e)[:200],
            )
        else:
            row.chamado_auto_at = agora
            row.chamado_auto_erro = ERRO_ENCAMINHADO
            robo += 1
            logger.info(
                "logistica_chamado_robo_enfileirado",
                id=str(row.id),
                pedido=row.pedido_marketplace,
                plataforma=row.plataforma,
                regra=rule.status_plataforma,
            )
        await session.commit()
    await session.commit()
    return {"robo": robo, "adiados": adiados, "pulados": pulados, "falhas": falhas}
