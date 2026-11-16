# ruff: noqa: E501, S608
"""Devoluções: chamado com Sim/Não, conferência da IA, instrução — e o histórico antigo

Vinicius, 16/11/2026 (2ª etapa do painel novo):

- "Abrir chamado: Sim ou Não" — `devolucao_rastreio.abrir_chamado` (grão de
  pedido, como o chamado): vazio = automático pelo motivo (como sempre foi);
  FALSE = não abrir.
- Conferência do motivo antes de a disputa sair ("será que o painel consegue
  analisar o real motivo antes de abrir chamado?"): o resultado da IA fica em
  `devolucao_rastreio.conferencia` (JSON), com a chave do que ela leu — se
  motivo, condição e observação mudarem, ela confere de novo.
- "Instrução IA" escrita antes de o chamado existir: espera em
  `instrucao_ia_pendente` e entra no chamado como instrução quando ele nasce.

Histórico ("esses antigos não conseguimos trazer já como resolvidos? e na obs
coloca feito antes de mudar o painel"): todo pedido com lançamento que nunca
entrou em Aguardando Devolução pelo carimbo novo (o histórico de antes de
setembro) nasce resolvido na marca da largada, na data do último lançamento.
A tela mostra a marca como "feito antes de mudar o painel". Quem voltar a cair
em Aguardando Devolução reabre sozinho (a regra do aberto compara com a
entrada nova).

Revision ID: 0485_devolucao_chamado_conferencia
Revises: 0484_devolucao_painel_lancadas_finalizadas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0485_devolucao_chamado_conferencia"
down_revision: str | None = "0484_devolucao_painel_lancadas_finalizadas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "devolucao_rastreio"
# Marca própria do histórico (a da 0483/0484 é "antes do painel novo" e marca a
# virada do resumo por mês; esta não pode mexer nela).
LARGADA = "antes do painel novo (histórico)"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(TABELA, sa.Column("abrir_chamado", sa.Boolean(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABELA,
        sa.Column("conferencia", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(TABELA, sa.Column("instrucao_ia_pendente", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABELA,
        sa.Column(
            "instrucao_ia_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        TABELA,
        sa.Column("instrucao_ia_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # O histórico antigo: pedido com lançamento, fora da lista do painel (nunca
    # carimbado em Aguardando Devolução e hoje fora de 83957/84677).
    op.execute(
        f"""
        INSERT INTO "{SCHEMA}".{TABELA} (pedido_bling, resolvido_at, resolvido_como)
        SELECT btrim(d.pedido_bling), max(d.created_at), '{LARGADA}'
        FROM "{SCHEMA}".devolutions d
        WHERE d.pedido_bling IS NOT NULL AND btrim(d.pedido_bling) <> ''
          AND EXISTS (
                SELECT 1 FROM "{SCHEMA}".bling_orders bo
                WHERE bo.numero = btrim(d.pedido_bling))
          AND NOT EXISTS (
                SELECT 1 FROM "{SCHEMA}".bling_orders bo
                WHERE bo.numero = btrim(d.pedido_bling)
                  AND (bo.aguardando_devolucao_data IS NOT NULL
                       OR bo.situacao IN ('83957', '84677')))
        GROUP BY btrim(d.pedido_bling)
        -- Só o que foi lançado ANTES do painel novo lançar (16/11/2026 ~09h): a
        -- "nova devolução" de um pedido fora da lista, feita no painel novo, é
        -- trabalho de agora e fica aberta até o Resolver.
        HAVING max(d.created_at) < TIMESTAMPTZ '2026-11-16 09:00:00-03'
        ON CONFLICT (pedido_bling) DO UPDATE
            SET resolvido_at = EXCLUDED.resolvido_at,
                resolvido_como = EXCLUDED.resolvido_como
            WHERE "{SCHEMA}".{TABELA}.resolvido_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column(TABELA, "instrucao_ia_em", schema=SCHEMA)
    op.drop_column(TABELA, "instrucao_ia_por", schema=SCHEMA)
    op.drop_column(TABELA, "instrucao_ia_pendente", schema=SCHEMA)
    op.drop_column(TABELA, "conferencia", schema=SCHEMA)
    op.drop_column(TABELA, "abrir_chamado", schema=SCHEMA)
