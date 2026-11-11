"""Jurídico: a referência da Denúncia é o caso; "Ajuizado" sai dos casos

Vinicius (11/11/2026), depois de ver o painel no ar:
- "as denúncias não têm pedido, vai confundir — o caso 006 deveria ficar em Pedido / Ref.":
  o pedido da compra de prova que a 0466 copiou para os processos da Denúncia sai (a tela
  mostra o caso no lugar).
- "pode tirar esse ajuizado, tem que deixar com jurídico": o status "Ajuizado" marcado à mão
  nos casos volta ao automático (todos foram ao advogado → "Com jurídico").

Só dados.

Revision ID: 0467_juridico_ref_caso_sem_ajuizado
Revises: 0466_juridico_processos
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0467_juridico_ref_caso_sem_ajuizado"
down_revision: str | None = "0466_juridico_processos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None



def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("UPDATE davinci.juridico_processos SET pedido = NULL WHERE origem = 'denuncia'")
    op.execute(
        """UPDATE davinci.denuncia_casos_extra
            SET status_manual = NULL, status_manual_por = NULL, status_manual_em = NULL
            WHERE status_manual = 'Ajuizado'"""
    )


def downgrade() -> None:
    # os valores antigos não voltam (o pedido da compra de prova continua no caso)
    pass
