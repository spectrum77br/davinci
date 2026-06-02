"""Worker UI dedicado: jobs disparados por click do operador vão pra
fila `davinci_ui` (alta prioridade), não pra fila default que tem
volume alto de webhooks/syncs.

Smoke checks: WorkerSettingsUI configurada com queue/functions/timeouts
corretos; pool UI usa default_queue_name diferente do pool default.
"""
from __future__ import annotations

from app.worker import (
    WorkerSettings,
    WorkerSettingsMarketplace,
    WorkerSettingsUI,
    check_marketplace_shipped_orders,
    create_bling_kit_for_mark_job,
    sync_import_product_to_bling_job,
)
from app.worker_pool import (
    ARQ_DEFAULT_QUEUE,
    ARQ_MARKETPLACE_QUEUE,
    ARQ_UI_QUEUE,
)


def test_filas_separadas():
    """Default, UI e Marketplace usam queue names distintos."""
    assert ARQ_DEFAULT_QUEUE != ARQ_UI_QUEUE
    assert ARQ_DEFAULT_QUEUE != ARQ_MARKETPLACE_QUEUE
    assert ARQ_UI_QUEUE != ARQ_MARKETPLACE_QUEUE
    assert ARQ_UI_QUEUE == "davinci_ui"
    assert ARQ_MARKETPLACE_QUEUE == "davinci_marketplace"


def test_worker_ui_so_tem_jobs_ui():
    """WorkerSettingsUI registra APENAS os 2 jobs UI-triggered.
    Webhooks/syncs/crons NÃO devem rodar nele pra não bloquear."""
    fns = WorkerSettingsUI.functions
    assert len(fns) == 2
    assert sync_import_product_to_bling_job in fns
    assert create_bling_kit_for_mark_job in fns


def test_worker_ui_consome_da_fila_correta():
    """`queue_name` aponta pra fila UI — sem isso o worker UI
    consumiria da fila default e o split seria fake."""
    assert WorkerSettingsUI.queue_name == ARQ_UI_QUEUE


def test_worker_ui_sem_cron_jobs():
    """Crons rodam no worker default. UI worker é só pra ondemand —
    cron rodando aqui seria duplicação."""
    assert not hasattr(WorkerSettingsUI, "cron_jobs") or not WorkerSettingsUI.cron_jobs


def test_worker_ui_timeouts_curtos():
    """Job UI deve ser curto (POST + PUT + supplier no Bling ≈ 5s).
    60s cobre retry no Bling sem trancar a fila."""
    assert WorkerSettingsUI.job_timeout == 60
    # Concorrência baixa pra não estourar rate limit do Bling.
    assert WorkerSettingsUI.max_jobs <= 10


def test_worker_default_ainda_tem_os_2_jobs():
    """Pra cobrir cenário de re-enqueue manual / fallback: WorkerSettings
    (default) também aceita esses jobs. Não é o caminho normal mas evita
    perder jobs antigos enfileirados antes deste fix."""
    fns = WorkerSettings.functions
    assert sync_import_product_to_bling_job in fns
    assert create_bling_kit_for_mark_job in fns


# ── WorkerSettingsMarketplace ────────────────────────────────────────


def test_worker_marketplace_so_tem_check_shipped():
    """Worker marketplace registra APENAS check_marketplace_shipped_orders.
    Outros crons/syncs ficam no default."""
    fns = WorkerSettingsMarketplace.functions
    assert len(fns) == 1
    assert check_marketplace_shipped_orders in fns


def test_worker_marketplace_queue_correta():
    assert WorkerSettingsMarketplace.queue_name == ARQ_MARKETPLACE_QUEUE


def test_worker_marketplace_tem_cron_a_cada_5min():
    """O cron tem que estar registrado AQUI (não no default). Padrão
    `minute=_FIVE_MIN` = {0,5,10,...,55}."""
    crons = WorkerSettingsMarketplace.cron_jobs
    assert len(crons) == 1
    # Cron do arq usa `coroutine` attr pra função; o name expõe o fn.
    assert "check_marketplace_shipped_orders" in (crons[0].name or "")


def test_worker_marketplace_timeouts_e_concorrencia():
    """Tick demora ~tens of seconds (consulta N marketplaces) — timeout
    2 min cobre. Concorrência baixa: a cada 5 min, overlap raro."""
    assert WorkerSettingsMarketplace.job_timeout == 120
    assert WorkerSettingsMarketplace.max_jobs <= 5


def test_default_nao_tem_mais_cron_de_check_shipped():
    """Cron MOVIDO pro marketplace worker — não pode estar duplicado
    no default (rodaria 2x a cada 5 min, hits dobrado nos marketplaces).
    Função fica em `functions` do default como fallback de enqueue manual."""
    default_crons = WorkerSettings.cron_jobs
    cron_names = [c.name or "" for c in default_crons]
    assert not any("check_marketplace_shipped_orders" in n for n in cron_names)
    # Mas a função continua registrada (fallback enqueue manual).
    assert check_marketplace_shipped_orders in WorkerSettings.functions
