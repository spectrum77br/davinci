"""chamados_leitores.responde_ml — o Mac Santiago responde nas consultas do ML

Vinicius, 30/09/2026 (298394, consulta 484465159): a parte de chamados do ML sai
do computador do Eduardo. A réplica do Cairo ficou "pendente" desde 29/09 porque
ninguém pede a fila "responder" do ML — o robô dele respondia pelo e-mail do
Tuta e parou ~24/09.

Quem responde agora é o executor do Mac Santiago, na página da consulta
("Retomar consulta" › "Digite uma mensagem" › Enviar), com uma senha PRÓPRIA:
a senha de leitura continua só lendo. Esta linha nova tem `responde_ml`; só ela
abre `/agent/leitor/responder/*` e os anexos do chamado.

Só o sha256 do token vai aqui (o token fica no .env do Mac Santiago).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0342_chamados_maos_ml"
down_revision: str | None = "0341_chamados_ia_aviso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

MAOS_ML_TOKEN_SHA256 = "92e192611ab04e750f631ff3b650419caeb121c185fe7bc7e2b204db71256867"  # noqa: S105 — sha256, não o token


def upgrade() -> None:
    op.add_column(
        "chamados_leitores",
        sa.Column("responde_ml", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.chamados_leitores (id, nome, token_hash, responde_ml) "  # noqa: S608
            "VALUES (gen_random_uuid(), 'Mãos do ML (Santiago)', :h, true)"
        ).bindparams(h=MAOS_ML_TOKEN_SHA256)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {SCHEMA}.chamados_leitores WHERE token_hash = :h"  # noqa: S608
        ).bindparams(h=MAOS_ML_TOKEN_SHA256)
    )
    op.drop_column("chamados_leitores", "responde_ml", schema=SCHEMA)
