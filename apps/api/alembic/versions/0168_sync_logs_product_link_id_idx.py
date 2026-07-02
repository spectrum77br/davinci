# ruff: noqa: E501, S608
"""Índice em sync_logs(product_link_id) — destrava o DELETE de product_link.

`sync_logs.product_link_id` tem FK ON DELETE SET NULL, mas NÃO tinha índice.
Ao apagar UM product_link, o gatilho RI de FK varre TODAS as partições de
sync_logs (~3M linhas / ~1,5 GB) pra achar as linhas que apontam pro link e
zerá-las. Medido em prod (EXPLAIN ANALYZE, rolled back), um link típico:

    ANTES (sem índice):  Trigger sync_logs_product_link_id_fkey = 2620 ms  (Execution 2628 ms)
    DEPOIS (com índice): Trigger sync_logs_product_link_id_fkey =    9 ms  (Execution   11 ms)

~240× — a diferença entre "excluir trava/estoura statement_timeout" e instantâneo.
Vale pro delete individual E pro delete em lote (Item 4), e pro delete de produto
(que apaga os product_links antes → cada um pagava a varredura).

`sync_logs.product_id` JÁ é coberto (ix_sync_logs_product_created, product_id é a
coluna líder), então só falta product_link_id. Índice PARCIAL (WHERE ... IS NOT
NULL) porque o gatilho de SET NULL só busca por valores não-nulos e muitas linhas
(refresh de bling / webhook_unmatched) têm product_link_id nulo — espelha o
ix_sync_logs_job (parcial em job_id).

sync_logs é PARTICIONADA por mês: CREATE INDEX no pai constrói e anexa o índice
em cada partição existente e nas futuras.

⚠️ CREATE INDEX (não-CONCURRENTLY) pega SHARE lock em sync_logs durante o build
(bloqueia INSERT/UPDATE/DELETE por alguns segundos até a partição maior indexar).
Rodar junto do deploy (workers reiniciando) ou numa janela curta — leituras não
bloqueiam. IF NOT EXISTS pra ser idempotente.

Revision ID: 0168_sync_logs_product_link_id_idx
Revises: 0167_product_links_dedup_encoding
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0168_sync_logs_product_link_id_idx"
down_revision: str | None = "0167_product_links_dedup_encoding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
INDEX = "ix_sync_logs_product_link_id"


def upgrade() -> None:
    op.execute(
        f'CREATE INDEX IF NOT EXISTS {INDEX} '
        f'ON "{SCHEMA}".sync_logs (product_link_id) '
        "WHERE product_link_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS "{SCHEMA}".{INDEX}')
