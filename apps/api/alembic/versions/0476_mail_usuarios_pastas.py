"""E-mail: a lista de usuários com as pastas em checkbox; sai a regra antiga

12/11/2026, o Eduardo: a administração do e-mail "está complexa e sem uso
atualmente" — no lugar, a lista de usuários. No Editar, cada conta de e-mail
com as pastas dela (informativo, SAC dos sites, as pastas das lojas…) em
checkbox: o admin marca o que a pessoa vê.

- sai `mail_acessos` (a regra por pessoa × caixa × loja × venda). Estava VAZIA
  em produção (conferido em 12/11); o downgrade a recria vazia, igual à 0415;
- entra `mail_leitor_pastas` (caixa, usuário, pastas): sem linha = a conta
  inteira; com linha = só aquelas pastas. Quem está na caixa continua sendo a
  lista de leitores (`mail_mailbox_settings.leitores`) e abrir a aba, a
  permissão `atendimento_email_leitura`. Nasce VAZIA: nada muda no deploy.

`mail_lixeira` e `mail_admin_eventos` ficam (lixeira de cada caixa e auditoria).
O gatilho do Histórico da tabela nova entra quando o worker sobe
(historico_manutencao).

Revision ID: 0476_mail_usuarios_pastas
Revises: 0475_flex_ajuste_kits
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0476_mail_usuarios_pastas"
down_revision: str | None = "0475_flex_ajuste_kits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = get_settings().database_schema
ARRAYS = ("lojas_permitidas", "lojas_bloqueadas", "tipos_permitidos", "pedidos_permitidos")


def _fk(name, table, *, primary_key=False, ondelete="CASCADE"):
    return sa.Column(
        name,
        pg.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.{table}.id", ondelete=ondelete),
        primary_key=primary_key,
        nullable=not primary_key,
    )


def _timestamps():
    return [
        sa.Column(n, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for n in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("mail_acessos", schema=SCHEMA)
    op.create_table(
        "mail_leitor_pastas",
        _fk("mailbox_id", "mail_mailboxes", primary_key=True),
        _fk("user_id", "users", primary_key=True),
        sa.Column("pastas", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        _fk("updated_by", "users", ondelete="SET NULL"),
        *_timestamps(),
        sa.CheckConstraint("jsonb_typeof(pastas) = 'array'", name="pastas_lista"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_leitor_pastas_user_id", "mail_leitor_pastas", ["user_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("mail_leitor_pastas", schema=SCHEMA)
    op.create_table(
        "mail_acessos",
        _fk("mailbox_id", "mail_mailboxes", primary_key=True),
        _fk("user_id", "users", primary_key=True),
        sa.Column("modo", sa.String(16), nullable=False),
        *[
            sa.Column(n, pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False)
            for n in ARRAYS
        ],
        _fk("updated_by", "users", ondelete="SET NULL"),
        *_timestamps(),
        sa.CheckConstraint("modo IN ('padrao', 'selecionadas', 'bloqueado')", name="modo"),
        *[sa.CheckConstraint(f"jsonb_typeof({n}) = 'array'", name=f"{n}_lista") for n in ARRAYS],
        schema=SCHEMA,
    )
    op.create_index("ix_mail_acessos_user_id", "mail_acessos", ["user_id"], schema=SCHEMA)
