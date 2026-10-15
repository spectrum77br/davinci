"""atendimento: `visto_em` — "visto no DaVinci", o número da Caixa igual ao Duoke

Decisão do dono (15/10/2026): os números da Caixa têm que bater com o Duoke.
Medido: o número da loja no Duoke é o "não lida" do PRÓPRIO Duoke — as
mensagens do comprador desde que alguém abriu a conversa lá dentro ou
respondeu —, não o da plataforma (a Shopee zera o não lido dela quando a loja
manda qualquer mensagem, até o robô do Duoke). A régua nova
(`services/atendimento/pendente.py`) conta as mensagens do cliente depois da
última resposta de PESSOA da loja e do que alguém já VIU da conversa no
DaVinci — esta coluna (a chegada, `created_at`, da mensagem mais nova que a
tela mostrava quando a pessoa abriu; com a conversa aberta, a cada mensagem
nova).

  • Uma coluna nova, anulável, sem índice e SEM preenchimento: até alguém
    abrir a conversa na tela do DaVinci ela é NULL e o número fica só com a
    última resposta da loja (o que foi medido às 07:32 de 15/10/2026).
  • Quem grava é a tela, pela pessoa que mexe na caixa
    (`services/atendimento/lida.py`: POST /conversas/{id}/visto ou /lida).
  • DOWNGRADE: tira a coluna (nada mais dependia dela) — volta exatamente ao
    que era.

`lock_timeout` de 10 s, como as outras: se o sync segurar a tabela por mais
que isso, a migration falha inteira (nada gravado) e é só rodar de novo.

Revision ID: 0399_atendimento_visto_em
Revises: 0398_conciliacao_permissao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0399_atendimento_visto_em"
down_revision: str | None = "0398_conciliacao_permissao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "atendimento_conversas"
COLUNA = "visto_em"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        TABELA,
        sa.Column(COLUNA, sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
