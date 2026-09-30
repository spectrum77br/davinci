"""Denúncia: acesso total (ver/editar/excluir) para o Cairo

Vinicius, 30/09/2026: "coloca acesso para o cairo tudo os acesso desse painel"
(seção Denúncia — Anúncios, Denúncias, Casos, publicada na 0344). O Cairo
(`sa.geral@tutamail.com`) é usuário comum e não havia ninguém com login de
administrador à mão; Vinicius escolheu liberar por aqui em vez da tela
Usuários › Permissões. Só acrescenta a chave `denuncia` — as outras permissões
dele ficam como estão. Daqui pra frente, mexer nisso é pela tela.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0345_denuncia_acesso_cairo"
down_revision: str | None = "0344_denuncia_espelho"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
EMAIL = "sa.geral@tutamail.com"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.users "  # noqa: S608
            "SET permissions = COALESCE(permissions, '{}'::jsonb) "
            "|| jsonb_build_object('denuncia', jsonb_build_object('view', true, 'edit', true, 'delete', true)) "
            "WHERE lower(email) = :email"
        ).bindparams(email=EMAIL)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.users SET permissions = permissions - 'denuncia' "  # noqa: S608
            "WHERE lower(email) = :email"
        ).bindparams(email=EMAIL)
    )
