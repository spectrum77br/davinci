"""Conciliação — as rodadas (cron e botão "sincronizar agora").

Por loja ligada (`conciliacao_lojas.ativa`): lê a fatura do ML, lê/pede o
extrato do Mercado Pago e recalcula os pedidos. Serializado por advisory lock
por loja (duas rodadas da mesma loja nunca correm juntas). O token do ML vem
pelo caminho blindado do Atendimento (`cliente_da_integracao`): a renovação
grava em sessão própria, porque o refresh token do ML é de uso único.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import ConciliacaoDia, ConciliacaoLoja, Integration
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.atendimento.clientes import cliente_da_integracao
from app.services.conciliacao_amazon import run_amazon_sync
from app.services.conciliacao_calculo import recalcular_loja
from app.services.conciliacao_extrato import run_extrato_sync
from app.services.conciliacao_fatura import run_fatura_sync
from app.services.conciliacao_melhor_envio import run_me_sync
from app.services.conciliacao_shopee import reler_escrow_liberados, run_carteira_sync
from app.services.conciliacao_tiktok import reler_financeiro_liquidados, run_tiktok_sync

logger = structlog.get_logger()


def _lock_key(integration_id: UUID) -> int:
    h = hashlib.blake2b(b"conciliacao:" + integration_id.bytes, digest_size=4).digest()
    val = int.from_bytes(h, "big", signed=False)
    return val - 2**32 if val >= 2**31 else val


async def lojas_ativas(session: AsyncSession) -> list[ConciliacaoLoja]:
    return list(
        (
            await session.execute(
                select(ConciliacaoLoja)
                .where(ConciliacaoLoja.ativa.is_(True))
                .order_by(ConciliacaoLoja.conta)
            )
        )
        .scalars()
        .all()
    )


async def loja_por_conta(session: AsyncSession, conta: str) -> ConciliacaoLoja | None:
    chave = (conta or "").strip().lower()
    for loja in await lojas_ativas(session):
        if loja.conta.strip().lower() == chave:
            return loja
    return None


async def _cliente(session: AsyncSession, loja: ConciliacaoLoja) -> Any:
    integ = await session.get(Integration, loja.integration_id)
    if integ is None:
        raise RuntimeError(f"integração da loja {loja.conta} não existe mais")
    return await cliente_da_integracao(integ)


async def sincronizar_loja(
    integration_id: UUID, *, fatura: bool = True, extrato: bool = True, recalculo: bool = True
) -> dict[str, Any]:
    """Uma rodada completa (ou parcial) de uma loja, com trava por loja."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _lock_key(integration_id)},
            )
        ).scalar()
        if not got:
            return {"skipped": "rodada_em_andamento", "integration_id": str(integration_id)}
        resultado: dict[str, Any] = {"integration_id": str(integration_id)}
        async with session_scope() as session:
            loja = (
                await session.execute(
                    select(ConciliacaoLoja).where(ConciliacaoLoja.integration_id == integration_id)
                )
            ).scalar_one_or_none()
            if loja is None:
                return {"skipped": "loja_nao_ligada", "integration_id": str(integration_id)}
            resultado["loja"] = loja.conta
            plataforma = getattr(loja.platform, "value", str(loja.platform))
            cliente = await _cliente(session, loja) if (fatura or extrato) else None
            if fatura and plataforma != "ml":
                fatura = False  # só o ML tem fatura; na Shopee as tarifas já vêm na renda
            if fatura:
                try:
                    resultado["fatura"] = await run_fatura_sync(session, loja, cliente)
                    erros_fatura = resultado["fatura"].get("erros") or []
                    if erros_fatura:
                        # 16/10 (lucas mei, 403 PolicyAgent): período com erro não pode
                        # sumir — fica na loja e o Resumo mostra "fatura não lida"
                        primeiro = erros_fatura[0]
                        texto = primeiro.get("erro") if isinstance(primeiro, dict) else primeiro
                        loja.ultimo_erro = f"fatura: {str(texto)[:280]}"
                        resultado["fatura"]["erro"] = loja.ultimo_erro
                except Exception as e:  # noqa: BLE001
                    loja.ultimo_erro = f"fatura: {type(e).__name__}: {str(e)[:300]}"
                    resultado["fatura"] = {"erro": loja.ultimo_erro}
                    logger.exception("conciliacao_fatura_unhandled", loja=loja.conta)
                await session.commit()
            if extrato:
                try:
                    if plataforma == "amazon":
                        if loja.modo_extrato == "upload":
                            # KFA: a API nega; as transações entram pelo arquivo (16/10)
                            resultado["extrato"] = {"modo": "upload", "pulado": True}
                        else:
                            resultado["extrato"] = await run_amazon_sync(session, loja, cliente)
                        try:
                            resultado["melhor_envio"] = await run_me_sync(session, loja)
                        except Exception as e:  # noqa: BLE001 — frete pago não derruba a rodada
                            resultado["melhor_envio"] = {
                                "erro": f"{type(e).__name__}: {str(e)[:200]}"
                            }
                            logger.warning(
                                "conciliacao_melhor_envio_falhou",
                                loja=loja.conta,
                                erro=str(e)[:200],
                            )
                    elif plataforma == "tiktok":
                        resultado["extrato"] = await run_tiktok_sync(session, loja, cliente)
                        resultado["financeiro"] = await reler_financeiro_liquidados(session, loja)
                    elif plataforma == "shopee":
                        resultado["extrato"] = await run_carteira_sync(session, loja, cliente)
                        # a Shopee fecha a conta da venda só na liberação (Afiliados etc.)
                        resultado["escrow"] = await reler_escrow_liberados(session, loja)
                    else:
                        resultado["extrato"] = await run_extrato_sync(session, loja, cliente)
                except Exception as e:  # noqa: BLE001
                    loja.ultimo_erro = f"extrato: {type(e).__name__}: {str(e)[:300]}"
                    resultado["extrato"] = {"erro": loja.ultimo_erro}
                    logger.exception("conciliacao_extrato_unhandled", loja=loja.conta)
                await session.commit()
            if recalculo:
                try:
                    resultado["recalculo"] = await recalcular_loja(session, loja)
                except Exception as e:  # noqa: BLE001
                    loja.ultimo_erro = f"recálculo: {type(e).__name__}: {str(e)[:300]}"
                    resultado["recalculo"] = {"erro": loja.ultimo_erro}
                    logger.exception("conciliacao_recalculo_unhandled", loja=loja.conta)
                await session.commit()
            if recalculo:
                # 15/10 (Vinicius: "ok pode fechar"): dia 100% casado e saldo batendo
                # fecha sozinho, "fechado pelo robô". Reabrir continua à mão.
                try:
                    resultado["dias"] = await fechar_dias_prontos(session, loja)
                except Exception as e:  # noqa: BLE001
                    resultado["dias"] = {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
                    logger.exception("conciliacao_fechar_dias_unhandled", loja=loja.conta)
                await session.commit()
            # 16/10 (revisão): os leitores devolvem "erros" (lista) e o fim da rodada só
            # olhava "erro" — a falha sumia e o dia ficava "sem movimento" (verde)
            for k in ("extrato", "recalculo"):
                texto = _erro_da_leitura(resultado.get(k))
                if texto and not loja.ultimo_erro:
                    loja.ultimo_erro = f"{k}: {texto[:280]}"
            if not any(
                _erro_da_leitura(resultado.get(k)) for k in ("fatura", "extrato", "recalculo")
            ):
                loja.ultimo_erro = None
            await session.commit()
        resultado["quando"] = datetime.now(UTC).isoformat()
        logger.info(
            "conciliacao_rodada", **{k: v for k, v in resultado.items() if k in ("loja", "quando")}
        )
        return resultado


async def enfileirar_rodadas(
    pool: Any,
    *,
    fatura: bool,
    extrato: bool,
    recalculo: bool,
    tag: str,
    intervalo_ml_min: float = 4.0,
    intervalo_outras_seg: float = 20.0,
    so_plataformas: tuple[str, ...] | None = None,
    so_nao_lidas_hoje: bool = False,
    contas: set[str] | None = None,
    so_sem_carga: bool = False,
) -> list[dict[str, Any]]:
    """Um job por loja (15/10): loja lenta não segura as outras. ML entra em fila com
    `intervalo_ml_min` entre uma e outra (limite de 5 chamadas/min da fatura, dividido
    com a Margem); as demais entram quase juntas. `so_nao_lidas_hoje` pula a loja
    que já leu o extrato hoje (Shopee/TikTok/Amazon leem uma vez por dia; o ML repete
    porque o relatório do Mercado Pago pode ainda não estar pronto)."""
    hoje0 = datetime.now(_BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_scope() as session:
        lojas = await lojas_ativas(session)
        alvo: list[ConciliacaoLoja] = []
        for loja in lojas:
            plataforma = getattr(loja.platform, "value", str(loja.platform))
            if so_plataformas and plataforma not in so_plataformas:
                continue
            if contas is not None and loja.conta.strip().lower() not in contas:
                continue
            if so_sem_carga and loja.ultimo_recalculo_em is not None:
                continue
            if (
                so_nao_lidas_hoje
                and plataforma != "ml"
                and loja.ultimo_extrato_em is not None
                and loja.ultimo_extrato_em >= hoje0
            ):
                continue
            alvo.append(loja)
        plano = [
            (loja.integration_id, loja.conta, getattr(loja.platform, "value", str(loja.platform)))
            for loja in alvo
        ]
    saida: list[dict[str, Any]] = []
    n_ml = 0
    n_outras = 0
    carimbo = datetime.now(UTC).strftime("%Y%m%d%H%M")
    for integration_id, conta, plataforma in plano:
        if plataforma == "ml":
            atraso = timedelta(minutes=intervalo_ml_min * n_ml)
            n_ml += 1
        else:
            atraso = timedelta(seconds=intervalo_outras_seg * n_outras)
            n_outras += 1
        job = await pool.enqueue_job(
            "conciliacao_sincronizar_loja",
            str(integration_id),
            fatura,
            extrato,
            recalculo,
            _job_id=f"conciliacao_sincronizar_loja:{integration_id}:{tag}:{carimbo}",
            _defer_by=atraso,
        )
        saida.append(
            {
                "conta": conta,
                "plataforma": plataforma,
                "em_minutos": round(atraso.total_seconds() / 60, 1),
                "enfileirado": job is not None,
            }
        )
    logger.info("conciliacao_rodadas_enfileiradas", tag=tag, lojas=len(saida), ml=n_ml)
    return saida


async def rodar_todas(*, fatura: bool, extrato: bool, recalculo: bool) -> list[dict[str, Any]]:
    async with session_scope() as session:
        lojas = await lojas_ativas(session)
        ids = [(loja.integration_id, loja.conta) for loja in lojas]
    saida: list[dict[str, Any]] = []
    for integration_id, _conta in ids:
        saida.append(
            await sincronizar_loja(
                integration_id, fatura=fatura, extrato=extrato, recalculo=recalculo
            )
        )
    return saida


FECHADO_PELO_ROBO = "robô"
_BRT = ZoneInfo("America/Sao_Paulo")


def _erro_da_leitura(res: Any) -> str | None:
    """Primeiro erro de um resultado de leitura: `erro` (texto) ou `erros` (lista)."""
    if not isinstance(res, dict):
        return None
    if res.get("erro"):
        return str(res["erro"])
    erros = res.get("erros") or []
    if not erros:
        return None
    e = erros[0]
    if isinstance(e, dict):
        texto = e.get("erro") or e.get("body") or e
        prefixo = e.get("janela") or e.get("extrato") or e.get("periodo")
        return f"{prefixo}: {texto}" if prefixo else str(texto)
    return str(e)


async def fechar_dias_prontos(
    session: AsyncSession, loja: ConciliacaoLoja, *, maximo_dias: int = 31
) -> dict[str, Any]:
    """Fecha sozinho todo dia (até ontem) em que toda linha tem destino e o saldo
    bate — a mesma regra do botão "Fechar o dia", só que sem ninguém clicar.
    Dia com linha sem dono ou saldo diferente fica aberto e aparece no Resumo."""
    from app.routers.conciliacao import _movimentos_do_dia, _saldo_anterior, _totais_do_dia
    from app.services.conciliacao_tiktok import aguardando_repasse

    agora = datetime.now(UTC)
    ontem = datetime.now(_BRT).date() - timedelta(days=1)
    inicio = max(loja.inicio, ontem - timedelta(days=maximo_dias))
    rows = (
        await session.execute(
            select(ConciliacaoDia).where(
                ConciliacaoDia.integration_id == loja.integration_id,
                ConciliacaoDia.dia >= inicio,
            )
        )
    ).scalars()
    por_dia: dict[date, ConciliacaoDia] = {r.dia: r for r in rows}
    saida: dict[str, Any] = {"fechados": [], "abertos": []}
    d = inicio
    while d <= ontem:
        existente = por_dia.get(d)
        if existente is not None and existente.fechado_em is not None:
            d += timedelta(days=1)
            continue
        saldo_ant = await _saldo_anterior(session, loja, d)
        movs = await _movimentos_do_dia(session, loja, d, saldo_ant)
        if movs:
            pendentes = [m for m in movs if not m.pedido_bling and not m.classificacao]
            totais = _totais_do_dia(movs, saldo_ant)
            dif = totais["diferenca"]
            # extrato da TikTok ainda não pago: o dia só fecha quando o repasse entrar
            nao_pago = any(aguardando_repasse(m) for m in movs)
            if not pendentes and not nao_pago and dif is not None and abs(dif) <= 0.05:
                row = existente
                if row is None:
                    row = ConciliacaoDia(integration_id=loja.integration_id, dia=d)
                    session.add(row)
                for k in ("saldo_inicial", "creditos", "debitos", "saldo_final", "saldo_informado"):
                    v = totais[k]
                    setattr(row, k, Decimal(str(v)) if v is not None else None)
                row.linhas = totais["linhas"]
                row.pendentes = 0
                row.fechado_em = agora
                row.fechado_por = FECHADO_PELO_ROBO
                saida["fechados"].append(d.isoformat())
            else:
                saida["abertos"].append(d.isoformat())
        d += timedelta(days=1)
    await session.flush()
    if saida["fechados"]:
        logger.info("conciliacao_dias_fechados_pelo_robo", loja=loja.conta, dias=saida["fechados"])
    return saida
