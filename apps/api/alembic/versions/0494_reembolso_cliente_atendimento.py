"""refunds: o reembolso ao cliente lançado pela conversa do Atendimento (Novos procedimentos 3/6)

17/11/2026, decisões do dono para a 3/6 ("Atendimento: acrescentar ao painel o
reembolso tipo cliente"): o reembolso tipo Cliente passa a ser lançado pela aba
Pedido da conversa (`routers/atendimento_reembolso.py`), com motivo, forma de
pagamento, os dados do Pix num campo protegido e a aprovação do ADM acima do
limite (R$ 100, 25% do valor pago ou o valor inteiro). Tudo na MESMA tabela
`refunds` (a tela Reembolso e a margem continuam lendo dela).

  • Nove colunas, todas anuláveis menos `requer_aprovacao` (NOT NULL, default
    false — nenhuma linha antiga precisa de aprovação):
      motivo           varchar(24)  — defeito_avaria | produto_errado | faltou_peca | frete | outro
      motivo_detalhe   text         — obrigatório quando o motivo é "outro" (a API confere)
      forma_pagamento  varchar(12)  — pix | cupom
      dados_pagamento  text         — os dados do Pix CIFRADOS (só ADM lê; nunca no export)
      requer_aprovacao bool         — o limite pediu o ADM
      aprovado_por     uuid → users (ON DELETE SET NULL, como `created_by`)
      aprovado_em      timestamptz
      origem           varchar(16)  — 'atendimento' nas novas
      conversa_id      text         — a conversa de onde saiu
  • Sem CHECK nos valores (a 4/6 vai mexer nas situações) e sem índice (a
    tabela tem ~1,3 mil linhas e a busca é pelo pedido, que já tem índice).
  • Os 75 reembolsos Cliente antigos ficam como estão (`chamado`/`operacao`
    não mudam). A margem e a conciliação NÃO mudam: só leem `reembolso` e
    `conferido`, como antes.
  • DOWNGRADE: derruba a FK e as nove colunas (o que foi lançado pela conversa
    continua como reembolso Cliente comum, sem motivo nem aprovação).

`lock_timeout` de 10 s, como as outras: ADD COLUMN com default constante é só
catálogo; se a tabela estiver presa por mais que isso, a migration falha
inteira (nada gravado) e é só rodar de novo.

17/11/2026: era 0493; a main ganhou a 0493_sistema_mudancas antes (mesmo pai),
então esta vem depois dela — uma ponta só.

Revision ID: 0494_reembolso_cliente_atendimento
Revises: 0493_sistema_mudancas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0494_reembolso_cliente_atendimento"
down_revision: str | None = "0493_sistema_mudancas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "refunds"
# O nome que a convenção do model dá (`fk_<tabela>_<coluna>_<tabela referida>`), o
# mesmo padrão da `fk_refunds_created_by_users` de produção.
FK_APROVADO_POR = "fk_refunds_aprovado_por_users"


def _colunas() -> list[sa.Column]:
    return [
        sa.Column("motivo", sa.String(24), nullable=True),
        sa.Column("motivo_detalhe", sa.Text(), nullable=True),
        sa.Column("forma_pagamento", sa.String(12), nullable=True),
        sa.Column("dados_pagamento", sa.Text(), nullable=True),
        sa.Column(
            "requer_aprovacao",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("aprovado_por", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origem", sa.String(16), nullable=True),
        sa.Column("conversa_id", sa.Text(), nullable=True),
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for coluna in _colunas():
        op.add_column(TABELA, coluna, schema=SCHEMA)
    op.create_foreign_key(
        FK_APROVADO_POR,
        TABELA,
        "users",
        ["aprovado_por"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_constraint(FK_APROVADO_POR, TABELA, schema=SCHEMA, type_="foreignkey")
    for coluna in reversed(_colunas()):
        op.drop_column(TABELA, coluna.name, schema=SCHEMA)
