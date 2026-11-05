"""Chamados: foto da revisão de cada dia e limpeza das falas repetidas da TikTok

Vinicius (05/11/2026):

- `chamado_revisao_dias`: painel Dias da aba Chamados ("dia tal tudo conferido, dia
  tal falta conferir tantos"). Espelha `app/models/chamado.py`.
- Limpeza: a TikTok devolve link novo das fotos e vídeos do comprador a cada leitura e
  o cron gravava a fala de novo (303842 chegou a 687 mensagens). Da mesma fala (mesma
  hora e texto, tirando os links) fica só a gravada por último, com o link mais novo.
  O que muda o texto além do link não é repetição e fica.

As linhas apagadas são cópias: o downgrade não as devolve.

Revision ID: 0452_chamados_dias_tiktok
Revises: 0451_chamados_revisao
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0452_chamados_dias_tiktok"
down_revision: str | None = "0451_chamados_revisao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
logger = logging.getLogger("alembic.runtime.migration")


def _limpar_repetidas(bind, schema: str = SCHEMA) -> int:
    r = bind.execute(
        sa.text(
            f"DELETE FROM {schema}.chamado_mensagem m USING ("  # noqa: S608
            "  SELECT id, ROW_NUMBER() OVER ("
            "    PARTITION BY chamado_id, direcao, tipo, created_at, "
            r"      regexp_replace(texto, 'https?://\S+', '', 'g')"
            "    ORDER BY updated_at DESC, id"
            "  ) AS n"
            f"  FROM {schema}.chamado_mensagem"
            "  WHERE direcao = 'recebida' AND tipo = 'resposta' "
            "    AND autor_nome = 'TikTok Shop' AND texto LIKE '%http%'"
            ") d WHERE m.id = d.id AND d.n > 1"
        )
    )
    return r.rowcount


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "chamado_revisao_dias",
        sa.Column("dia", sa.Date(), primary_key=True),
        sa.Column("conferido", sa.Integer(), nullable=False),
        sa.Column("revisar", sa.Integer(), nullable=False),
        sa.Column("fixo", sa.Integer(), nullable=False),
        sa.Column("falta", sa.Integer(), nullable=False),
        sa.Column("prazos", sa.Integer(), nullable=False),
        sa.Column("por_status", JSONB(), nullable=False),
        sa.Column("faltaram", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    apagadas = _limpar_repetidas(op.get_bind())
    logger.info("0452: %s falas repetidas da TikTok apagadas", apagadas)


def downgrade() -> None:
    op.drop_table("chamado_revisao_dias", schema=SCHEMA)
