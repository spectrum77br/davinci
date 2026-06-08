"""users: coluna password_hash (login por senha definida pelo admin)

Revision ID: 0131_user_password
Revises: 0130_devolution_prazo

Adiciona suporte a login por e-mail + senha. A senha é definida/resetada
pelos administradores (POST /api/users/{id}/password); o usuário comum não
troca sozinho. O login por código no e-mail (OTP) continua existindo como
recuperação ("Esqueci minha senha").

Coluna nullable: usuários existentes ficam sem senha até um admin definir —
nesse intervalo eles entram pelo OTP. password_hash guarda um hash bcrypt
(SHA-256 pre-hash), nunca a senha em claro.
"""

from alembic import op
import sqlalchemy as sa

revision = "0131_user_password"
down_revision = "0130_devolution_prazo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
