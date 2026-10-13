"""denuncia_juridico_pastas — "Montar pasta jurídico" / "Enviar ao jurídico" na ficha do caso

Vinicius, 13/10/2026: o passo 9 (Jurídico) sai da lista de passos do robô e vira botão dentro do
caso — "eu acionar esses botão dentro dos casos e enviar quando eu quiser". "Montar pasta jurídico"
pede ao Mac mini (comando `juridico_montar`) o dossiê em PDF + os anexos + o vídeo, zipados; o mini
sobe o zip pra cá (`PUT /api/denuncia/sync/juridico/pastas/{id}/arquivo`). "Enviar ao jurídico"
(comando `juridico_enviar`) faz o mini mandar o e-mail pelo Tuta dele; enviado de verdade = o caso
vira "Com jurídico" (`denuncia_casos_extra.juridico_data`). A pessoa escolhe o destinatário na
hora: e-mail diferente do advogado = teste (`envio_status` 'teste'), o caso não muda. Uma linha por
pedido de montagem; a tela mostra a última do caso. Só o zip da última fica no disco.

`denuncia_casos_extra.selo_anatel` ('sim' | 'nao'): a pergunta "Selo Anatel aparece nas fotos?"
do checklist — vai no dossiê.

Revision ID: 0390_denuncia_juridico_pasta
Revises: 0389_mail_leitores
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0390_denuncia_juridico_pasta"
down_revision: str | None = "0389_mail_leitores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("denuncia_casos_extra", sa.Column("selo_anatel", sa.Text()), schema=SCHEMA)
    op.create_table(
        "denuncia_juridico_pastas",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("caso_id", sa.BigInteger(), nullable=False),
        sa.Column("pedido_por", sa.Text()),
        sa.Column(
            "pedido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default="pedida", nullable=False),
        sa.Column("resultado", sa.Text()),
        sa.Column("info", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("arquivo_local", sa.Text()),
        sa.Column("tamanho", sa.BigInteger()),
        sa.Column("pronta_em", sa.DateTime(timezone=True)),
        sa.Column("envio_status", sa.Text()),
        sa.Column("envio_pedido_por", sa.Text()),
        sa.Column("envio_pedido_em", sa.DateTime(timezone=True)),
        sa.Column("envio_em", sa.DateTime(timezone=True)),
        sa.Column("envio_resultado", sa.Text()),
        sa.Column("envio_info", pg.JSONB()),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_denuncia_juridico_pastas_caso", "denuncia_juridico_pastas", ["caso_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index(
        "ix_denuncia_juridico_pastas_caso", table_name="denuncia_juridico_pastas", schema=SCHEMA
    )
    op.drop_table("denuncia_juridico_pastas", schema=SCHEMA)
    op.drop_column("denuncia_casos_extra", "selo_anatel", schema=SCHEMA)
