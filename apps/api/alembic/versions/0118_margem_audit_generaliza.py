"""margem_audit: generaliza bling_situacao_audit para auditar toda a página Margem

Revision ID: 0118_margem_audit_generaliza
Revises: 0117_kit_celular_seed

Renomeia a tabela bling_situacao_audit → margem_audit e generaliza as colunas:
  * situacao_antiga → valor_antigo
  * situacao_nova   → valor_novo (passa a ser NULLABLE)
  * + acao (text): 'situacao' | 'saldo_final' | 'observacao' (default histórico 'situacao')

Assim a mesma trilha cobre mudança de situação no Bling, edição do Saldo Final
(valor_base) e da Observação — todas com quem/quando.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0118_margem_audit_generaliza"
down_revision: str | None = "0117_kit_celular_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    # Renomeia a tabela e os índices.
    op.execute(f'ALTER TABLE "{SCHEMA}".bling_situacao_audit RENAME TO margem_audit')
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_bling_situacao_audit_created_at '
        f'RENAME TO ix_margem_audit_created_at'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_bling_situacao_audit_pedido_bling '
        f'RENAME TO ix_margem_audit_pedido_bling'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_bling_situacao_audit_bling_id '
        f'RENAME TO ix_margem_audit_bling_id'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_bling_situacao_audit_mudado_por '
        f'RENAME TO ix_margem_audit_mudado_por'
    )

    # Renomeia as colunas de situação para nomes genéricos.
    op.alter_column(
        "margem_audit", "situacao_antiga", new_column_name="valor_antigo", schema=SCHEMA
    )
    op.alter_column(
        "margem_audit", "situacao_nova", new_column_name="valor_novo", schema=SCHEMA
    )
    # valor_novo passa a aceitar NULL (ex.: observação apagada).
    op.execute(f'ALTER TABLE "{SCHEMA}".margem_audit ALTER COLUMN valor_novo DROP NOT NULL')

    # Nova coluna acao: linhas existentes (todas de situação) recebem 'situacao'.
    op.execute(
        f'ALTER TABLE "{SCHEMA}".margem_audit '
        f"ADD COLUMN acao text NOT NULL DEFAULT 'situacao'"
    )
    op.execute(f'ALTER TABLE "{SCHEMA}".margem_audit ALTER COLUMN acao DROP DEFAULT')


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')

    op.execute(f'ALTER TABLE "{SCHEMA}".margem_audit DROP COLUMN acao')
    # search_path já é o SCHEMA — sem qualificar, evita escapes de aspas.
    op.execute("UPDATE margem_audit SET valor_novo = '' WHERE valor_novo IS NULL")
    op.execute(f'ALTER TABLE "{SCHEMA}".margem_audit ALTER COLUMN valor_novo SET NOT NULL')
    op.alter_column(
        "margem_audit", "valor_novo", new_column_name="situacao_nova", schema=SCHEMA
    )
    op.alter_column(
        "margem_audit", "valor_antigo", new_column_name="situacao_antiga", schema=SCHEMA
    )

    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_margem_audit_mudado_por '
        f'RENAME TO ix_bling_situacao_audit_mudado_por'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_margem_audit_bling_id '
        f'RENAME TO ix_bling_situacao_audit_bling_id'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_margem_audit_pedido_bling '
        f'RENAME TO ix_bling_situacao_audit_pedido_bling'
    )
    op.execute(
        f'ALTER INDEX "{SCHEMA}".ix_margem_audit_created_at '
        f'RENAME TO ix_bling_situacao_audit_created_at'
    )
    op.execute(f'ALTER TABLE "{SCHEMA}".margem_audit RENAME TO bling_situacao_audit')
