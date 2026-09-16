"""devolucao_rastreio.devolucao_tipo_auto — o TIPO do caso de pós-venda

Vinicius, 16/09/2026: "reembolso pedido para devolução solicitada são duas
informações diferentes". O pedido 294865 (TikTok/Mini) teve a devolução
cancelada pelo cliente e, um minuto depois, um caso SÓ reembolso ("pacote
incompleto") — mesmo vocabulário de status na API, o que separa é o
`return_type` (RETURN_AND_REFUND × REFUND × REPLACEMENT). O sistema não
guardava o tipo: o painel mostrou "Devolução solicitada", o Bling foi pra
Aguardando Devolução e a aba Acompanhamento ficou esperando um pacote que
nunca viria; ninguém respondeu no TikTok e em 5 dias a plataforma aprovou
por prazo (R$ 744 devolvidos, produto com o cliente).

Esta coluna (0285) guarda o tipo que o sync do retorno (`devolucao_rastreio_sync`)
lê da returns API, ao lado de `devolucao_status_auto`, pra aba Acompanhamento
dizer "Reembolso solicitado — responder no TikTok" em vez de devolução, e
NÃO carimbar "Chegou em" quando o caso só-reembolso fecha.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0285_devolucao_tipo_auto"
down_revision: str | None = "0284_pricing_push_confirmacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column(
        "devolucao_rastreio", sa.Column("devolucao_tipo_auto", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_column("devolucao_rastreio", "devolucao_tipo_auto")
