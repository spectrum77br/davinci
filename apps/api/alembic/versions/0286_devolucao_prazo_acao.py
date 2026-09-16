"""devolucao_rastreio.acao_auto / prazo_acao_auto (+ carimbo do aviso) — prazo
de resposta da loja na aba Acompanhamento de Devoluções

Vinicius, 16/09/2026: "teria como colocar no painel prazo resposta da loja
para não perder mais prazo?". O TikTok manda, no mesmo payload que o sync do
retorno já lê a cada 30 min, a ação que espera da loja e até quando
(`seller_next_action_response`): confirmar/recusar o pacote recebido, responder
ao pedido de reembolso... Passado o prazo a plataforma decide sozinha (o
294865 foi aprovado por prazo: R$ 744). Hoje esse prazo só aparecia na aba
Lançamentos ("Prazo contest.", para devoluções já lançadas) e no chamado do
só-reembolso; os 16 pedidos com pacote postado pelo cliente não tinham prazo
em lugar nenhum.

Colunas:
  • `acao_auto` / `prazo_acao_auto`: a ação pendente e o prazo (UTC),
    reescritos a cada rodada do sync (None = nada pendente);
  • `aviso_prazo_acao_at` / `aviso_prazo_acao_para`: quando o aviso Threema
    saiu e pra qual prazo — um aviso por (caso, prazo).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0286_devolucao_prazo_acao"
down_revision: str | None = "0285_devolucao_tipo_auto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_COLS = (
    ("acao_auto", sa.Text()),
    ("prazo_acao_auto", sa.DateTime(timezone=True)),
    ("aviso_prazo_acao_at", sa.DateTime(timezone=True)),
    ("aviso_prazo_acao_para", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    for name, typ in _COLS:
        op.add_column("devolucao_rastreio", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    for name, _ in reversed(_COLS):
        op.drop_column("devolucao_rastreio", name)
