"""Worker UI dedicado: jobs disparados por click do operador vão pra
fila `davinci_ui` (alta prioridade), não pra fila default que tem
volume alto de webhooks/syncs.

Smoke checks: WorkerSettingsUI configurada com queue/functions/timeouts
corretos; pool UI usa default_queue_name diferente do pool default.
"""
from __future__ import annotations

from app.worker import (
    WorkerSettings,
    WorkerSettingsUI,
    create_bling_kit_for_mark_job,
    sync_import_product_to_bling_job,
)
from app.worker_pool import ARQ_DEFAULT_QUEUE, ARQ_UI_QUEUE


def test_filas_separadas():
    """Default e UI usam queue names distintos."""
    assert ARQ_DEFAULT_QUEUE != ARQ_UI_QUEUE
    assert ARQ_UI_QUEUE == "davinci_ui"


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
