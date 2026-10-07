"""Pull live ML Product Ads numbers into the marketing_* tables.

Mirrors `shopee_sync.py` but for Mercado Livre. The shape divergence:

  • ML has no balance pot — `credit_balance` shows "today's remaining
    daily budget" (sum of active campaign budgets minus today's spend).
  • ML has no hourly performance — heatmap stays empty for ML accounts.

Conserto de 07/10/2026 (docs/marketing-ml-ads.md):

  • Métricas vêm do endpoint documentado (campaigns/search, api-version 2,
    aggregation_type=DAILY): UMA linha por dia da janela, com o gasto real do
    dia. O endpoint antigo foi desligado pelo ML em 27/05/2026.
  • Erro NUNCA vira sucesso: 404/4xx/5xx/resposta fora do formato grava
    `sync_status='erro'` na conta, conta falha seguida (Telegram na 3ª) e NÃO
    mexe nos números já gravados. Conta sem Publicidade liberada ou token sem
    o escopo de anúncios → `sync_status='sem_permissao'` (ação do dono, sem
    alarme repetido).
  • A revisão dos últimos 7 dias só reescreve um dia quando a chamada deu
    certo E o ML trouxe aquele dia; dia que o ML não trouxe mantém o que já
    estava gravado (e sai em `avisos.dias_sem_dado_ml` + log `ml_ads_avisos`).
    Resposta paginada incompleta (página repetida/vazia antes do total) é
    erro, nunca dado parcial.
  • Uma linha diária por conta e dia: trava por conta + chave única da 0379
    (cron e backfill ao mesmo tempo não duplicam o dia).
  • Token renovado no meio da coleta é gravado e confirmado na hora, numa
    sessão à parte: rollback nenhum da coleta pode devolver o refresh_token
    já gasto (uso único no ML).
  • Integração arquivada (Lojas › Arquivar) ou conta de Marketing arquivada
    não é chamada.
  • `backfill_ml_ads` regrava até 90 dias (limite do ML) — idempotente; por
    padrão só simula (scripts/ml_ads_backfill.py).

Bling revenue still drives ACOS for the account-level numbers. ML's
own attributed-sales is kept on the campaign rows because Bling can't
slice by campaign.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.db import session_scope
from app.models.integration import Integration
from app.models.marketing import MarketingAccount, MarketingCampaign, MarketingMetric
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.alerts import (
    notify_high_acos,
    record_sync_failure,
    record_sync_success,
)
from app.services.marketing.bling_revenue import BlingRevenue, get_bling_revenue
from app.services.ml_ads import (
    ML_ADS_MAX_DIAS,
    MLAdsClient,
    MLAdsError,
    MLAdsScopeError,
    MLAdsSemPermissaoError,
    MLCampaign,
    MLDailyMetric,
    dias_sem_dado,
    hoje_brt,
    orcamento_diario_restante,
)

logger = structlog.get_logger()

_DAILY_LOOKBACK_DAYS = 7

SYNC_OK = "ok"
SYNC_ERRO = "erro"
SYNC_SEM_PERMISSAO = "sem_permissao"


# ─── cliente / conta ──────────────────────────────────────────────────────


def _client(
    session: AsyncSession, integration: Integration, *, simular: bool
) -> MLAdsClient:
    creds = decrypt_json(integration.credentials)
    integ_id = integration.id

    async def _persist_refreshed_creds(new_creds: dict) -> None:
        # O refresh_token do ML é de USO ÚNICO: o antigo morreu no instante
        # da renovação. Grava numa sessão própria e confirma NA HORA — um
        # rollback da coleta depois daqui (sem permissão, erro, exceção) não
        # pode desfazer isto, senão a loja fica com um refresh_token já gasto
        # e a próxima renovação (pedidos, estoque, Ads) falha até reconectar.
        valores: dict[str, Any] = {"credentials": encrypt_json(new_creds)}
        expires_at = new_creds.get("expires_at")
        if expires_at:
            valores["token_expires_at"] = datetime.fromtimestamp(int(expires_at), tz=UTC)
        try:
            async with session_scope() as s:
                # Não pode ficar preso esperando a própria coleta.
                await s.execute(text("SET LOCAL lock_timeout = '5s'"))
                await s.execute(
                    update(Integration).where(Integration.id == integ_id).values(**valores)
                )
        except Exception as e:  # noqa: BLE001
            # Último recurso: pela sessão da coleta (vale se ela confirmar).
            logger.error(
                "ml_ads_token_renovado_nao_gravado_a_parte",
                integration_id=str(integ_id), err=str(e)[:300],
            )
            for k, v in valores.items():
                setattr(integration, k, v)
            await session.flush()
            return
        # Na sessão da coleta, como valor JÁ gravado (sem outro UPDATE).
        for k, v in valores.items():
            set_committed_value(integration, k, v)

    if simular:
        # Simulação não grava nada — nem token novo. Renovar sem gravar
        # queimaria o refresh_token (uso único no ML), então fica proibido.
        return MLAdsClient(creds, permitir_refresh=False)
    return MLAdsClient(creds, on_token_refresh=_persist_refreshed_creds)


async def _obter_conta(
    session: AsyncSession, integration: Integration, *, criar: bool
) -> MarketingAccount | None:
    """Conta de Marketing da integração. Se não houver, ADOTA uma conta
    órfã do ML com o mesmo nome (jlas2/aguiar2/forpaper ficaram sem
    integration_id em set/2026 — criar outra bateria na unique
    name+platform+department). Sem nenhuma, cria (se `criar`)."""
    acc = (
        await session.execute(
            select(MarketingAccount).where(MarketingAccount.integration_id == integration.id)
        )
    ).scalars().first()
    if acc is not None:
        return acc
    nome = (integration.name or "").strip().lower()
    orfa = (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.platform == "ml",
                MarketingAccount.integration_id.is_(None),
                MarketingAccount.arquivada_em.is_(None),
                func.lower(func.trim(MarketingAccount.name)) == nome,
            )
        )
    ).scalars().first()
    if orfa is not None:
        if criar:
            orfa.integration_id = integration.id
        return orfa
    if not criar:
        return None
    acc = MarketingAccount(
        integration_id=integration.id,
        name=integration.name,
        platform="ml",
        department=(integration.department or "geral").lower(),
        acos_target=7.0,
        agent_enabled=False,
        status="active",
    )
    session.add(acc)
    await session.flush()
    return acc


async def _conta_id_para_simular(
    session: AsyncSession, integration: Integration
) -> UUID | None:
    """Mesma busca do `_obter_conta`, mas lendo SÓ o id (colunas que existem
    antes da 0379): a simulação precisa rodar em produção antes de a
    migration ser aplicada. Não sabe se a conta está arquivada (coluna nova)."""
    acc_id = (
        await session.execute(
            select(MarketingAccount.id).where(MarketingAccount.integration_id == integration.id)
        )
    ).scalars().first()
    if acc_id is not None:
        return acc_id
    nome = (integration.name or "").strip().lower()
    return (
        await session.execute(
            select(MarketingAccount.id).where(
                MarketingAccount.platform == "ml",
                MarketingAccount.integration_id.is_(None),
                func.lower(func.trim(MarketingAccount.name)) == nome,
            )
        )
    ).scalars().first()


def _marcar_estado(
    acc: MarketingAccount | None, estado: str, erro: str | None, agora: datetime
) -> None:
    if acc is None:
        return
    acc.sync_status = estado
    acc.sync_erro = (erro or None) and erro[:1000]
    acc.sync_em = agora
    if estado == SYNC_OK:
        acc.sync_ok_em = agora


def _codigo_mensagem(e: Exception) -> tuple[str, str]:
    if isinstance(e, MLAdsError):
        return e.code, f"{e.status} {e.message} (path={e.path})"
    if isinstance(e, httpx.HTTPError):
        return "rede", f"{type(e).__name__}: {e}"
    texto = str(e)
    if "ml_refresh_failed" in texto or "refresh_token" in texto or "client_secret" in texto:
        # Ex.: Lucas MEI desde 04/09 — o ML recusa o client_id/secret.
        return "token_invalido", texto
    return "erro", f"{type(e).__name__}: {texto}"


# ─── dias (o que gravar em marketing_metrics) ─────────────────────────────


def _ts(day: date) -> datetime:
    # Linha diária = 12:00 UTC do dia (sentinela intensity=0), igual Shopee/Amazon.
    return datetime.combine(day, time(12, 0), tzinfo=UTC)


def _valores(row: MarketingMetric) -> dict:
    return {
        "spend": round(float(row.spend or 0), 2),
        "impressions": int(row.impressions or 0),
        "clicks": int(row.clicks or 0),
        "revenue": round(float(row.revenue or 0), 2),
        "acos": float(row.acos) if row.acos is not None else None,
    }


async def _linhas_existentes(
    session: AsyncSession, account_id: UUID | None, inicio: date, fim: date
) -> tuple[dict[date, MarketingMetric], list[date]]:
    """(linha diária de cada dia, dias com linha diária REPETIDA). Com o
    índice único da 0379 não há como repetir; se aparecer (banco sem a 0379),
    a primeira é a que vale e o dia sai no retorno — quem chama avisa."""
    if account_id is None:
        return {}, []
    rows = (
        await session.execute(
            select(MarketingMetric)
            .where(
                and_(
                    MarketingMetric.account_id == account_id,
                    MarketingMetric.intensity == 0,
                    MarketingMetric.timestamp >= _ts(inicio),
                    MarketingMetric.timestamp <= _ts(fim),
                )
            )
            .order_by(MarketingMetric.timestamp, MarketingMetric.created_at)
        )
    ).scalars().all()
    out: dict[date, MarketingMetric] = {}
    repetidas: list[date] = []
    for r in rows:
        day = r.timestamp.astimezone(UTC).date()
        if r.timestamp.astimezone(UTC) != _ts(day):
            continue  # não é a linha diária (12:00 UTC) — não é nossa
        if day in out:
            # Linha repetida do mesmo dia: as telas somam as duas (gasto em
            # dobro). Atualiza só a primeira e REPORTA (log de erro + retorno).
            logger.error(
                "ml_ads_linha_diaria_duplicada", account_id=str(account_id), day=str(day)
            )
            if day not in repetidas:
                repetidas.append(day)
            continue
        out[day] = r
    return out, repetidas


def planejar_dia(
    *,
    day: date,
    ads: MLDailyMetric | None,
    existente: dict | None,
    bling: BlingRevenue | None,
) -> dict:
    """O que a linha diária desse dia deve ficar, sem tocar no banco.

    - Gasto/impressões/cliques: do ML quando o ML trouxe o dia; senão o que
      já estava gravado (nunca zera um dia guardado por falta de dado); dia
      novo sem dado do ML nasce zerado.
    - Faturamento: Bling do dia (fonte da casa, "faturável"); loja sem Bling
      mapeado cai nas vendas atribuídas pelo próprio ML.
    - ACOS = gasto ÷ faturamento × 100 (definição do operador); None sem
      gasto ou sem faturamento."""
    if ads is not None:
        spend, impressions, clicks = round(ads.spend, 2), ads.impressions, ads.clicks
        fonte = "ml"
    elif existente is not None:
        spend = existente["spend"]
        impressions = existente["impressions"]
        clicks = existente["clicks"]
        fonte = "mantido"
    else:
        spend, impressions, clicks = 0.0, 0, 0
        fonte = "sem_dado"

    ads_revenue = ads.revenue if ads is not None else (existente or {}).get("revenue", 0.0)
    bling_today = bling.by_day.get(day, 0.0) if bling is not None else None
    account_revenue = bling_today if bling_today is not None else ads_revenue
    account_revenue = round(float(account_revenue or 0.0), 2)
    acos = (
        round(spend / account_revenue * 100, 2)
        if account_revenue > 0 and spend > 0
        else None
    )
    depois = {
        "spend": spend,
        "impressions": int(impressions),
        "clicks": int(clicks),
        "revenue": account_revenue,
        "acos": acos,
    }
    if existente is None:
        acao = "criar"
    elif existente == depois:
        acao = "igual"
    else:
        acao = "atualizar"
    return {
        "dia": day.isoformat(),
        "acao": acao,
        "fonte_gasto": fonte,
        "antes": existente,
        "depois": depois,
        "vendas_ads": round(ads.revenue, 2) if ads is not None else None,
        "unidades_ads": ads.units if ads is not None else None,
    }


async def _gravar_dias(
    session: AsyncSession,
    *,
    account_id: UUID | None,
    inicio: date,
    fim: date,
    ads: dict[date, MLDailyMetric],
    bling: BlingRevenue | None,
    simular: bool,
) -> list[dict]:
    """Uma linha diária por dia de [inicio, fim] (cria ou atualiza; nunca
    apaga). Idempotente: rodar de novo com a mesma resposta não muda nada.
    `simular=True` só devolve o plano.

    Duas gravações da MESMA conta ao mesmo tempo (cron :05/:35 + backfill
    --apply, ou o botão de sincronizar) ficam em fila: trava por conta
    (`pg_advisory_xact_lock`, solta no commit/rollback) antes de ler o que já
    existe. E a linha nova entra com INSERT … ON CONFLICT na chave única da
    0379 (conta + dia, intensity=0) — nunca duas linhas do mesmo dia."""
    gravar = not simular and account_id is not None
    if gravar:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:chave))"),
            {"chave": f"ml_ads_diario:{account_id}"},
        )
    existentes, repetidas = await _linhas_existentes(session, account_id, inicio, fim)
    plano: list[dict] = []
    day = inicio
    while day <= fim:
        row = existentes.get(day)
        p = planejar_dia(
            day=day,
            ads=ads.get(day),
            existente=_valores(row) if row is not None else None,
            bling=bling,
        )
        if day in repetidas:
            p["linha_repetida"] = True
        plano.append(p)
        if gravar and p["acao"] != "igual":
            d = p["depois"]
            if row is None:
                valores = {
                    "spend": d["spend"], "revenue": d["revenue"],
                    "impressions": d["impressions"], "clicks": d["clicks"],
                    "acos": d["acos"],
                }
                await session.execute(
                    pg_insert(MarketingMetric)
                    .values(
                        account_id=account_id, timestamp=_ts(day),
                        orders=0, intensity=0, **valores,
                    )
                    .on_conflict_do_update(
                        index_elements=[MarketingMetric.account_id, MarketingMetric.timestamp],
                        # Literal (não parâmetro): o Postgres só casa o índice
                        # parcial com o predicado escrito igual.
                        index_where=text("intensity = 0"),
                        set_={**valores, "updated_at": func.now()},
                    )
                )
            else:
                row.spend = d["spend"]
                row.revenue = d["revenue"]
                row.impressions = d["impressions"]
                row.clicks = d["clicks"]
                row.acos = d["acos"]
        day += timedelta(days=1)
    return plano


def _totais(plano: list[dict]) -> dict:
    return {
        "dias": len(plano),
        "criar": sum(1 for p in plano if p["acao"] == "criar"),
        "atualizar": sum(1 for p in plano if p["acao"] == "atualizar"),
        "iguais": sum(1 for p in plano if p["acao"] == "igual"),
        "gasto": round(sum(p["depois"]["spend"] for p in plano), 2),
        "impressoes": sum(p["depois"]["impressions"] for p in plano),
        "cliques": sum(p["depois"]["clicks"] for p in plano),
        "vendas_ads": round(sum(p["vendas_ads"] or 0.0 for p in plano), 2),
        "linhas_repetidas": sum(1 for p in plano if p.get("linha_repetida")),
    }


def _avisos(
    *,
    integration_id: UUID,
    nome: str | None,
    ads: dict[date, MLDailyMetric],
    inicio: date,
    fim: date,
    hoje: date,
    plano: list[dict],
    campaigns: list[MLCampaign] | None = None,
) -> dict[str, Any]:
    """O que a rodada deu certo mas merece olho (vai no retorno e no log;
    não muda o estado da conta):

    - `dias_sem_dado_ml`: dias já fechados que a resposta COMPLETA do ML não
      trouxe — neles ficou o que estava gravado (ou 0, se não havia linha).
    - `linhas_repetidas`: dias com 2+ linhas diárias (banco sem a 0379).
    - `divergencia_campanhas`: a soma do gasto por dia ≠ a soma do gasto por
      campanha na mesma janela (as duas vêm do mesmo campaigns/search; se
      não batem, um dos dois veio incompleto)."""
    avisos: dict[str, Any] = {}
    faltando = dias_sem_dado(ads, inicio, fim, hoje=hoje)
    if faltando:
        avisos["dias_sem_dado_ml"] = [d.isoformat() for d in faltando]
    repetidas = [p["dia"] for p in plano if p.get("linha_repetida")]
    if repetidas:
        avisos["linhas_repetidas"] = repetidas
    if campaigns is not None:
        por_dia = round(sum(m.spend for m in ads.values()), 2)
        por_campanha = round(sum(c.spend for c in campaigns), 2)
        if abs(por_dia - por_campanha) > max(1.0, 0.01 * max(por_dia, por_campanha)):
            avisos["divergencia_campanhas"] = {
                "gasto_por_dia": por_dia, "gasto_por_campanha": por_campanha,
            }
    if avisos:
        logger.warning(
            "ml_ads_avisos", integration_id=str(integration_id), name=nome, **avisos
        )
    return avisos


# ─── sync (cron a cada 30 min) ────────────────────────────────────────────


async def sync_ml_integration(
    session: AsyncSession,
    integration_id: UUID,
    *,
    simular: bool = False,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Sync one ML Product Ads integration (janela de 7 dias). Commits at
    the end so the manual-trigger endpoint returns a fully-persisted state.

    Retorno `status`: 'ok' | 'erro' | 'sem_permissao' | 'skipped'.
    `simular=True`: calcula tudo e devolve o plano por dia sem gravar nada
    (nem estado, nem token) e sem ler as colunas da 0379 — roda em produção
    antes da migration."""
    integration = await session.get(Integration, integration_id)
    if integration is None:
        return {"status": "error", "code": "integration_not_found"}
    if integration.platform.value != "ml":
        return {"status": "skipped", "reason": "not_ml", "platform": integration.platform.value}
    if integration.archived_at is not None:
        return {"status": "skipped", "reason": "integracao_arquivada"}

    if simular:
        account: MarketingAccount | None = None
        account_id = await _conta_id_para_simular(session, integration)
    else:
        account = await _obter_conta(session, integration, criar=True)
        if account is not None and account.arquivada_em is not None:
            return {"status": "skipped", "reason": "conta_arquivada"}
        account_id = account.id if account is not None else None

    agora_utc = (agora or datetime.now(UTC)).astimezone(UTC)
    today = hoje_brt(agora_utc)
    start_day = today - timedelta(days=_DAILY_LOOKBACK_DAYS - 1)
    client = _client(session, integration, simular=simular)

    try:
        await client.get_advertiser_id()
        campaigns = await client.list_campaigns_with_metrics(
            date_from=start_day, date_to=today, hoje=today
        )
        ads = await client.fetch_daily_metrics(date_from=start_day, date_to=today, hoje=today)
    except (MLAdsScopeError, MLAdsSemPermissaoError) as e:
        # Ação do dono (ligar Publicidade / reconectar com o escopo), não
        # falha passageira: não conta como falha seguida (sem Telegram a
        # cada 30 min) e não mexe em número nenhum.
        logger.warning(
            "ml_ads_sem_permissao",
            integration_id=str(integration_id), name=integration.name,
            code=e.code, status=e.status, message=e.message,
        )
        if simular:
            await session.rollback()
        else:
            _marcar_estado(account, SYNC_SEM_PERMISSAO, f"{e.code}: {e.message}", agora_utc)
            await session.commit()
        return {"status": SYNC_SEM_PERMISSAO, "code": e.code, "message": e.message}
    except (MLAdsError, httpx.HTTPError, RuntimeError) as e:
        code, msg = _codigo_mensagem(e)
        logger.error(
            "ml_ads_sync_erro",
            integration_id=str(integration_id), name=integration.name,
            code=code, message=msg[:300],
        )
        if simular:
            await session.rollback()
        else:
            _marcar_estado(account, SYNC_ERRO, f"{code}: {msg}", agora_utc)
            await record_sync_failure(session, integration, code=code, message=msg)
            await session.commit()
        return {"status": SYNC_ERRO, "code": code, "message": msg[:300]}

    # ─── Faturamento (fonte: aba Faturamento = bling_orders local) ───────
    bling = await get_bling_revenue(session, integration, start=start_day, end=today)

    plano = await _gravar_dias(
        session, account_id=account_id, inicio=start_day, fim=today,
        ads=ads, bling=bling, simular=simular,
    )
    hoje_plano = plano[-1]["depois"]
    hoje_ads = ads.get(today)
    remaining_budget = orcamento_diario_restante(
        campaigns, hoje_ads.spend if hoje_ads is not None else 0.0
    )
    totais = _totais(plano)
    avisos = _avisos(
        integration_id=integration_id, nome=integration.name, ads=ads,
        inicio=start_day, fim=today, hoje=today, plano=plano, campaigns=campaigns,
    )

    if simular:
        nome = integration.name  # antes do rollback (que expira os objetos)
        await session.rollback()
        return {
            "status": SYNC_OK,
            "simulacao": True,
            "integration_id": str(integration_id),
            "name": nome,
            "janela": [start_day.isoformat(), today.isoformat()],
            "conta_nova": account_id is None,
            "totais": totais,
            "avisos": avisos,
            "dias": plano,
            "campanhas": [
                {"id": c.campaign_id, "nome": c.name, "status": c.status,
                 "gasto_7d": c.spend, "vendas_ads_7d": c.revenue}
                for c in campaigns
            ],
        }

    assert account is not None  # criar=True acima
    account.platform = "ml"
    account.department = (integration.department or account.department or "geral").lower()
    account.credit_balance = remaining_budget
    account.spend_today = hoje_plano["spend"]
    account.revenue_today = hoje_plano["revenue"]
    account.impressions_today = hoje_plano["impressions"]
    account.status = "active"
    _marcar_estado(account, SYNC_OK, None, agora_utc)

    await _upsert_campaigns(session, account_id=account.id, campaigns=campaigns)

    integration.last_ads_sync_at = agora_utc
    integration.last_error = None
    integration.last_test_ok = True
    integration.last_test_at = agora_utc
    await record_sync_success(session, integration)
    await session.commit()

    # ─── post-commit alerts ──────────────────────────────────────────────
    if hoje_plano["acos"] is not None:
        await notify_high_acos(integration, hoje_plano["acos"], account.acos_target)

    return {
        "status": SYNC_OK,
        "account_id": str(account.id),
        "credit": remaining_budget,
        "spend": hoje_plano["spend"],
        "revenue": hoje_plano["revenue"],
        "campaigns": len(campaigns),
        "bling_revenue": bling.total if bling else None,
        "janela": totais,
        "avisos": avisos,
    }


async def sync_all_ml_integrations(session: AsyncSession) -> list[dict[str, Any]]:
    """Iterate every ML integration opted into the marketing module. Lojas
    arquivadas (integrations.archived_at) ficam de fora — antes a Nexus,
    arquivada em 07/07, ainda era chamada a cada 30 min."""
    rows = (
        await session.execute(
            select(Integration).where(
                and_(
                    Integration.status == "active",
                    Integration.platform == "ml",
                    Integration.ads_enabled.is_(True),
                    Integration.archived_at.is_(None),
                )
            )
        )
    ).scalars().all()
    # id/nome antes do laço: um rollback numa conta expira os objetos e ler
    # atributo expirado fora do await quebra a sessão assíncrona.
    alvos = [(integ.id, integ.name) for integ in rows]
    out: list[dict[str, Any]] = []
    for integ_id, nome in alvos:
        try:
            r = await sync_ml_integration(session, integ_id)
            out.append({"integration_id": str(integ_id), "name": nome, **r})
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.error("ml_ads_sync_failed", integration_id=str(integ_id), err=str(e)[:300])
            out.append({
                "integration_id": str(integ_id), "name": nome,
                "status": SYNC_ERRO, "error": str(e)[:200],
            })
    resumo = resumir_status(out)
    log = logger.warning if resumo.get(SYNC_ERRO) else logger.info
    log("ml_ads_sync_resumo", **resumo)
    return out


def resumir_status(resultados: list[dict]) -> dict[str, int]:
    """{'ok': 12, 'erro': 1, 'sem_permissao': 2, ...} para o log do cron."""
    c: Counter[str] = Counter()
    for r in resultados:
        st = str(r.get("status") or "?")
        if st == "skipped":
            st = f"skipped_{r.get('reason') or '?'}"
        c[st] += 1
    return dict(c)


# ─── backfill (até 90 dias) ───────────────────────────────────────────────


async def backfill_ml_ads(
    session: AsyncSession,
    *,
    dias: int,
    integration_ids: list[UUID] | None = None,
    simular: bool = True,
    agora: datetime | None = None,
) -> list[dict[str, Any]]:
    """Regrava as linhas diárias de Ads do ML dos últimos `dias` (1..90 —
    o ML não devolve mais que isso) com o gasto/impressões/cliques reais por
    dia + o faturamento do Bling. Corrige o histórico inflado (até 09/07) e
    os zeros (desde 09/07).

    Idempotente (chave = conta + dia, linha das 12:00 UTC com intensity=0):
    rodar duas vezes dá "iguais" na segunda. Nunca apaga linha. Erro de API
    numa conta não mexe nas linhas dela e não para as outras.

    `simular=True` (padrão) só calcula e devolve o plano por dia — sem
    gravar nada e sem renovar token."""
    if not 1 <= dias <= ML_ADS_MAX_DIAS:
        raise ValueError(f"dias precisa ficar entre 1 e {ML_ADS_MAX_DIAS} (pedido: {dias})")
    agora_utc = (agora or datetime.now(UTC)).astimezone(UTC)
    fim = hoje_brt(agora_utc)
    inicio = fim - timedelta(days=dias - 1)

    stmt = select(Integration).where(
        Integration.platform == "ml",
        Integration.archived_at.is_(None),
    )
    if integration_ids:
        stmt = stmt.where(Integration.id.in_(integration_ids))
    else:
        stmt = stmt.where(Integration.status == "active", Integration.ads_enabled.is_(True))
    integracoes = (await session.execute(stmt.order_by(Integration.name))).scalars().all()
    alvos = [(integ.id, integ.name) for integ in integracoes]

    out: list[dict[str, Any]] = []
    for integ_id, nome in alvos:
        base = {"integration_id": str(integ_id), "name": nome}
        try:
            r = await _backfill_uma(
                session, integ_id, inicio=inicio, fim=fim, simular=simular,
            )
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.error("ml_ads_backfill_falhou", integration_id=str(integ_id), err=str(e)[:300])
            r = {"status": SYNC_ERRO, "code": "excecao", "message": str(e)[:300]}
        out.append({**base, **r})
    logger.info(
        "ml_ads_backfill_resumo", simular=simular, dias=dias,
        inicio=str(inicio), fim=str(fim), **resumir_status(out),
    )
    return out


async def _backfill_uma(
    session: AsyncSession,
    integration_id: UUID,
    *,
    inicio: date,
    fim: date,
    simular: bool,
) -> dict[str, Any]:
    integration = await session.get(Integration, integration_id)
    if integration is None:
        return {"status": "skipped", "reason": "integracao_sumiu"}
    if simular:
        account_id = await _conta_id_para_simular(session, integration)
    else:
        account = await _obter_conta(session, integration, criar=True)
        if account is not None and account.arquivada_em is not None:
            return {"status": "skipped", "reason": "conta_arquivada"}
        account_id = account.id if account is not None else None
    client = _client(session, integration, simular=simular)
    try:
        ads = await client.fetch_daily_metrics(date_from=inicio, date_to=fim, hoje=fim)
    except (MLAdsError, httpx.HTTPError, RuntimeError) as e:
        sem_permissao = isinstance(e, MLAdsScopeError | MLAdsSemPermissaoError)
        code, msg = (e.code, e.message) if sem_permissao else _codigo_mensagem(e)
        # O token renovado já foi gravado à parte (_client); aqui só se decide
        # o que mais desta conta fica: simulação nunca grava nada.
        if simular:
            await session.rollback()
        else:
            await session.commit()
        return {
            "status": SYNC_SEM_PERMISSAO if sem_permissao else SYNC_ERRO,
            "code": code, "message": msg[:300],
        }

    bling = await get_bling_revenue(session, integration, start=inicio, end=fim)
    plano = await _gravar_dias(
        session, account_id=account_id, inicio=inicio, fim=fim,
        ads=ads, bling=bling, simular=simular,
    )
    avisos = _avisos(
        integration_id=integration_id, nome=integration.name, ads=ads,
        inicio=inicio, fim=fim, hoje=fim, plano=plano,
    )
    if simular:
        await session.rollback()
    else:
        await session.commit()
    return {
        "status": SYNC_OK,
        "simulacao": simular,
        "janela": [inicio.isoformat(), fim.isoformat()],
        # Só faz sentido na simulação: sem conta de Marketing ainda, o --apply cria.
        "conta_nova": (account_id is None) if simular else None,
        "totais": _totais(plano),
        "avisos": avisos,
        "dias": plano,
    }


# ─── internals ────────────────────────────────────────────────────────────


def _map_ml_status(status: str) -> str:
    s = (status or "").lower()
    if s in ("active", "ongoing", "running"):
        return "active"
    if s == "paused":
        return "paused"
    if s in ("ended", "finished", "deleted"):
        return "off"
    return s or "active"


async def _upsert_campaigns(
    session: AsyncSession,
    *,
    account_id: UUID,
    campaigns: list[MLCampaign],
) -> None:
    """Campanhas com as métricas dos últimos 7 dias (igual à Shopee):
    gasto, impressões, vendas atribuídas pelo ML (total_amount) e o ACOS do
    próprio ML (gasto ÷ vendas atribuídas)."""
    existing = (
        await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.account_id == account_id)
        )
    ).scalars().all()
    by_ext = {c.external_id: c for c in existing if c.external_id}
    for camp in campaigns:
        ext_id = str(camp.campaign_id)
        status = _map_ml_status(camp.status)
        row = by_ext.get(ext_id)
        if row is None:
            session.add(
                MarketingCampaign(
                    account_id=account_id,
                    name=camp.name or f"Campanha {camp.campaign_id}",
                    external_id=ext_id,
                    status=status,
                    credit=None,  # ML has no per-campaign credit
                    spend=camp.spend,
                    revenue=camp.revenue,
                    impressions=camp.impressions,
                    acos=camp.acos,
                )
            )
        else:
            row.name = camp.name or row.name
            row.status = status
            row.spend = camp.spend
            row.revenue = camp.revenue
            row.impressions = camp.impressions
            row.acos = camp.acos
