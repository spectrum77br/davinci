"""Administração de leitura e lixeira local do E-mail.

Revision ID: 0415_mail_admin
Revises: 0414_vigia_conciliacao_chave_etapa
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0415_mail_admin"
down_revision: str | None = "0414_vigia_conciliacao_chave_etapa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = get_settings().database_schema


def _fk(name, table, *, primary_key=False, nullable=True, ondelete="CASCADE"):
    return sa.Column(
        name,
        pg.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.{table}.id", ondelete=ondelete),
        primary_key=primary_key,
        nullable=False if primary_key else nullable,
    )


def _timestamps():
    return [
        sa.Column(n, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for n in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    arrays = ("lojas_permitidas", "lojas_bloqueadas", "tipos_permitidos", "pedidos_permitidos")
    op.create_table(
        "mail_acessos",
        _fk("mailbox_id", "mail_mailboxes", primary_key=True),
        _fk("user_id", "users", primary_key=True),
        sa.Column("modo", sa.String(16), nullable=False),
        *[
            sa.Column(n, pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False)
            for n in arrays
        ],
        _fk("updated_by", "users", ondelete="SET NULL"),
        *_timestamps(),
        sa.CheckConstraint("modo IN ('padrao', 'selecionadas', 'bloqueado')", name="modo"),
        *[sa.CheckConstraint(f"jsonb_typeof({n}) = 'array'", name=f"{n}_lista") for n in arrays],
        schema=SCHEMA,
    )
    op.create_index("ix_mail_acessos_user_id", "mail_acessos", ["user_id"], schema=SCHEMA)
    op.create_table(
        "mail_lixeira",
        _fk("message_id", "mail_messages", primary_key=True),
        sa.Column("excluido_em", sa.DateTime(timezone=True)),
        _fk("excluido_por", "users", ondelete="SET NULL"),
        sa.Column("motivo", sa.String(300)),
        sa.Column("restaurado_em", sa.DateTime(timezone=True)),
        _fk("restaurado_por", "users", ondelete="SET NULL"),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_lixeira_excluido_em", "mail_lixeira", ["excluido_em", "message_id"], schema=SCHEMA
    )
    op.create_table(
        "mail_admin_eventos",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _fk("mailbox_id", "mail_mailboxes", nullable=False),
        _fk("autor_id", "users", ondelete="SET NULL"),
        sa.Column("acao", sa.String(32), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("dados", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_admin_eventos_criado_em", "mail_admin_eventos", ["criado_em"], schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("mail_admin_eventos", schema=SCHEMA)
    op.drop_table("mail_lixeira", schema=SCHEMA)
    op.drop_table("mail_acessos", schema=SCHEMA)
