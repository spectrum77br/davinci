"""Conciliação — quem edita a Margem passa a ver e editar a Conciliação

Painel novo (14/10/2026), permissão `conciliacao` (ver/editar). Pra ninguém
ficar trancado fora no primeiro dia: todo usuário NÃO admin que hoje tem
`margem.edit` ganha `conciliacao.view` e `conciliacao.edit` (quem já cuida da
Margem é quem vai conferir pedido a pedido). Admin vê tudo e não precisa.
Depois a tela Permissões ajusta quem quiser. Só dado; print de quem mudou.

DOWNGRADE: tira a chave `conciliacao` de todos os usuários.

Revision ID: 0398_conciliacao_permissao
Revises: 0397_conciliacao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0398_conciliacao_permissao"
down_revision: str | None = "0397_conciliacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    mudados = conn.execute(
        sa.text(
            """
            UPDATE "davinci".users
               SET permissions = jsonb_set(
                       coalesce(permissions, '{}'::jsonb),
                       '{conciliacao}',
                       '{"view": true, "edit": true, "delete": false}'::jsonb,
                       true
                   )
             WHERE role::text <> 'admin'
               AND coalesce(permissions -> 'margem' ->> 'edit', 'false') = 'true'
               AND coalesce(permissions -> 'conciliacao' ->> 'view', 'false') <> 'true'
            RETURNING email
            """
        )
    ).fetchall()
    quem = [m.email for m in mudados] or "ninguém (só admins editam a Margem)"
    print(f"[0398] conciliacao liberada para: {quem}")


def downgrade() -> None:
    op.execute(
        """UPDATE "davinci".users SET permissions = permissions - 'conciliacao' """
        "WHERE permissions ? 'conciliacao'"
    )
