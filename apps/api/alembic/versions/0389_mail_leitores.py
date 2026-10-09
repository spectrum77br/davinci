"""mail: "Quem mais vê" — LEITORES de uma caixa da Central de e-mail

Eduardo, 09/10/2026: "deixe o usuário israel ver a aba de e-mail agora". A
Central do outro dev mostra a caixa só ao DONO e aos admins (`allowed()`, que
continua sendo a regra de ESCREVER). Esta migration só acrescenta, na tabela
NOSSA de configuração (`mail_mailbox_settings`, da 0387):

  • `leitores` — jsonb, lista de user_id (texto), padrão `[]` (= como hoje):
    quem LÊ a caixa inteira (lista, mensagem, anexo), sem responder, resolver,
    configurar nem trocar a chave. Só conta quem está ativo (o serviço confere);
  • `leitores_updated_by` / `leitores_updated_at` — quem e quando mudou a lista
    (o Histórico já captura a tabela: é de pessoa).

Nenhuma linha é criada nem alterada (sem linha = sem leitores). As 4 tabelas
da 0386 não são tocadas. `lock_timeout` de 10 s, como a 0386/0387 (a FK para
users pega ShareRowExclusiveLock). O downgrade tira só as 3 colunas (a lista
se perde; a caixa volta a ser só do dono e dos admins).

MERGE (09/10/2026): esta nasceu em paralelo à `0388_mail_caixa_indice` (o
índice da aba E-mail › Caixas, outra frente). Ao juntar as duas, trocar o
`down_revision` abaixo para "0388_mail_caixa_indice" (uma ponta só) e o
`test_mail_leitores.py::test_migration_*` acompanha pelo `down_revision`.

Revision ID: 0389_mail_leitores
Revises: 0387_mail_atendimento
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0389_mail_leitores"
# No merge com a 0388 (outra frente): trocar para "0388_mail_caixa_indice".
down_revision: str | None = "0388_mail_caixa_indice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CONFIG = "mail_mailbox_settings"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        CONFIG,
        sa.Column("leitores", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema=SCHEMA,
    )
    op.add_column(CONFIG, sa.Column("leitores_updated_by", pg.UUID(as_uuid=True)), schema=SCHEMA)
    op.add_column(
        CONFIG, sa.Column("leitores_updated_at", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_mail_mailbox_settings_leitores_updated_by_users"),
        CONFIG,
        "users",
        ["leitores_updated_by"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_mail_mailbox_settings_leitores_lista"),
        CONFIG,
        "jsonb_typeof(leitores) = 'array'",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_constraint(
        op.f("ck_mail_mailbox_settings_leitores_lista"), CONFIG, type_="check", schema=SCHEMA
    )
    op.drop_constraint(
        op.f("fk_mail_mailbox_settings_leitores_updated_by_users"),
        CONFIG,
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column(CONFIG, "leitores_updated_at", schema=SCHEMA)
    op.drop_column(CONFIG, "leitores_updated_by", schema=SCHEMA)
    op.drop_column(CONFIG, "leitores", schema=SCHEMA)
