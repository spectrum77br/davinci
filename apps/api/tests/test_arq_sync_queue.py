"""Worker dedicado do SYNC EM MASSA: `sync_all_run` (Sincronizar Todos),
`auto_link_run` (Vincular Automático), `sync_product_run` (webhook de produto) e
`refresh_bling_stock_run` vão pra fila `davinci_sync`, não pra default que tem o
ingest de pedido + ~25 crons.

Smoke checks: WorkerSettingsSync com queue/functions/timeouts corretos; as 4
funções continuam na default como fallback; pool de sync usa default_queue_name
próprio.
"""
from __future__ import annotations

from app.worker import (
    WorkerSettings,
    WorkerSettingsSync,
    auto_link_run,
    refresh_bling_stock_run,
    sync_all_run,
    sync_product_run,
)
from app.worker_pool import (
    ARQ_DEFAULT_QUEUE,
    ARQ_FINANCIALS_QUEUE,
    ARQ_MARKETPLACE_QUEUE,
    ARQ_SYNC_QUEUE,
    ARQ_UI_QUEUE,
)

# Nomes das funções registradas (arq embrulha algumas em Function via func()).
_SYNC_FN_NAMES = {"sync_all_run", "sync_product_run", "auto_link_run", "refresh_bling_stock_run"}


def _fn_names(functions) -> set[str]:
    names = set()
    for f in functions:
        name = getattr(f, "name", None) or getattr(f, "__name__", None)
        if name is None and hasattr(f, "coroutine"):
            name = f.coroutine.__name__
        names.add(name)
    return names


def test_fila_sync_distinta_das_demais():
    assert ARQ_SYNC_QUEUE == "davinci_sync"
    for other in (
        ARQ_DEFAULT_QUEUE,
        ARQ_UI_QUEUE,
        ARQ_MARKETPLACE_QUEUE,
        ARQ_FINANCIALS_QUEUE,
    ):
        assert ARQ_SYNC_QUEUE != other


def test_worker_sync_registra_os_4_jobs_de_massa():
    """WorkerSettingsSync registra EXATAMENTE os 4 jobs de sync — nada de
    webhook de pedido/crons, senão o split seria fake."""
    names = _fn_names(WorkerSettingsSync.functions)
    assert names == _SYNC_FN_NAMES


def test_worker_sync_consome_da_fila_correta():
    assert WorkerSettingsSync.queue_name == ARQ_SYNC_QUEUE


def test_worker_sync_sem_cron_jobs():
    """Crons ficam no default; o daily_sync_scheduler (default) só ENFILEIRA o
    sync_all na fila de sync. Cron rodando aqui seria duplicação."""
    assert not getattr(WorkerSettingsSync, "cron_jobs", None)


def test_worker_sync_config_de_pool_e_timeout():
    """max_jobs=8 cabe no pool de 30 (1 massa ≤9 conexões + sync_product a 1
    cada). O sync_all sobrescreve o timeout p/ 3h via func()."""
    assert WorkerSettingsSync.max_jobs == 8
    assert WorkerSettingsSync.job_timeout == 1800
    # sync_all_run entra como Function (func(..., timeout=10800)) — teto de 3h.
    sync_all_fn = next(
        f for f in WorkerSettingsSync.functions
        if (getattr(f, "name", None) or getattr(f, "__name__", "")) == "sync_all_run"
    )
    assert getattr(sync_all_fn, "timeout_s", None) == 10800


def test_default_ainda_tem_os_4_jobs_como_fallback():
    """Fallback p/ jobs já enfileirados na default no momento do deploy +
    re-enqueue manual (mesmo padrão do worker_ui/marketplace/financials)."""
    names = _fn_names(WorkerSettings.functions)
    for n in _SYNC_FN_NAMES:
        assert n in names, f"{n} sumiu da fila default (perde jobs no deploy)"


def test_funcoes_de_massa_existem():
    """Sanidade dos imports — as 4 funções são chamáveis."""
    for fn in (sync_all_run, sync_product_run, auto_link_run, refresh_bling_stock_run):
        assert callable(fn)
