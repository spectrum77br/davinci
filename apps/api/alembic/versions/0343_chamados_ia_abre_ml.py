"""chamados_cerebros.abre_ml — a IA de Chamado abre a consulta no ML (Fale conosco)

Vinicius, 30/09/2026 (passo 4 da saída do Eduardo): o chamado do ML que não tem
mediação aberta nem devolução pra revisar ia pro "robô do formulário de ajuda" do
Eduardo, que abria a consulta num formulário ("Por que você não conseguiu enviar o
produto?"). Agora quem abre é a IA de Chamado no Mac Santiago, pelo Fale conosco
(assistente › atendente › E-mail › formulário) — é conversa, então é a IA, não um
script fixo.

`abre_ml` ligado: a IA pega essas aberturas (`/agent/abrir-ml/*`) e o token antigo
(Eduardo) para de recebê-las no `/agent/lease`. Nasce desligado: quem liga é a
troca de guarda (`davinci_chamados.py abrir-ml assumir`), depois do teste.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0343_chamados_ia_abre_ml"
down_revision: str | None = "0342_chamados_maos_ml"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "chamados_cerebros",
        sa.Column("abre_ml", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("chamados_cerebros", "abre_ml", schema=SCHEMA)
