"""companies.responsavel_nome — o Responsável passa a ser da EMPRESA

Eduardo, 17/09/2026: "vai ter empresas que não vão ter lojas, que vai precisar
adicionar, o responsável precisa deixar colocar o nome do responsável". Hoje o
Responsável mostrado na tela Empresas vem de `store_info.cpf_name` (um campo
POR LOJA, casado por texto com o apelido) — 15 das 49 empresas não têm loja
nenhuma e a tela recusava a edição com "Crie uma loja para X antes de atribuir
um responsável".

Esta coluna guarda o responsável DA EMPRESA (quem responde pelo CNPJ).
`store_info.cpf_name` continua existindo e significando o responsável DAQUELA
LOJA — são coisas diferentes, e a tela Empresas para de escrever lá (o
casamento por apelido não é 1:1: "dream 2" e "Dream 2" são duas empresas
apontando para a mesma loja, e salvar numa escrevia na outra).

O backfill copia o que a tela já exibia (33 das 49 empresas), preferindo a
loja ATIVA — ninguém precisa redigitar.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0292_companies_responsavel_nome"
down_revision: str | None = "0291_devolucao_fila"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("companies", sa.Column("responsavel_nome", sa.Text(), nullable=True))
    # Mesma regra que a tela usava pra exibir: casa apelido × account_name sem
    # espaços/caixa e, havendo mais de uma loja, vale a ATIVA de menor ordem.
    op.execute(
        r"""
        UPDATE companies c SET responsavel_nome = sub.nome
        FROM (
          SELECT DISTINCT ON (k)
                 lower(regexp_replace(coalesce(si.account_name, ''), '\s', '', 'g')) AS k,
                 trim(si.cpf_name) AS nome
          FROM store_info si
          WHERE coalesce(trim(si.cpf_name), '') <> ''
          ORDER BY k, (si.archived_at IS NOT NULL), si.sort_order NULLS LAST
        ) sub
        WHERE lower(regexp_replace(c.apelido, '\s', '', 'g')) = sub.k
        """
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("companies", "responsavel_nome")
