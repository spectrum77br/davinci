"""Quem está na peça quando não é ninguém do elenco

Pedido de 25/09/2026: "em 'quem está na peça' pode ser marcado 'outro', seria
uma opção também, né? Caso não seja nenhum daqueles personagens ali".

## Por que um campo, e não só uma opção a mais no formulário

Uma opção "outro personagem" sem texto ficaria idêntica a "sem persona" no
banco (`personagem_id` NULL nas duas) — a equipe não saberia que tem alguém na
peça, nem quem. E é exatamente o caso que a casa mais precisa ver: o elenco
passa pela aprovação de procedência (o pedido de personagem novo pergunta de
onde vêm imagem e voz), e um personagem fora dele não passou.

## Por que no PEDIDO e não no criativo

O vídeo vai para `marketing_creatives`, e a postagem automática depende das
colunas de lá — elas não são tocadas. A pergunta "quem está na peça" já mora no
pedido (`personagem_id`, desde a 0313), e é na fila de Pedidos que quem decide
olha a peça. O texto também não entra na `descricao`: no "sim" a descrição vira
o texto do roteiro da casa, e a anotação vazaria para lá.

Coluna nova e opcional: no Postgres isso não reescreve a tabela.

Revision ID: 0330_pedido_outra_pessoa
Revises: 0329_chamado_mensagem_excluida
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0330_pedido_outra_pessoa"
down_revision: str | None = "0329_chamado_mensagem_excluida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "marketing_ideia_requisicoes",
        sa.Column("personagem_outro", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("marketing_ideia_requisicoes", "personagem_outro", schema=SCHEMA)
