"""E-mail › Agente: as regras do agente no e-mail (+ a do formulário de SAC)

12/11/2026, o Eduardo: o agente do atendimento responde o e-mail como
extensão do atendimento (a mesma chave da loja) e as regras próprias do
e-mail ficam numa parte nova da aba E-mail. A primeira já entra: a
reclamação de produto que chega no SAC sem o formulário de SAC do site
preenchido — o agente pede para abrir pelo formulário (o link e o que ele
pede vêm do código, `email_agente.FORMULARIOS_SAC`) e a assinatura da marca
vai junto, como na resposta da pessoa.

Nada muda no deploy: o agente só responde e-mail da loja (ou do site) cuja
chave do agente já está ligada.

Revision ID: 0477_mail_agente_regras
Revises: 0476_mail_usuarios_pastas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0477_mail_agente_regras"
down_revision: str | None = "0476_mail_usuarios_pastas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = get_settings().database_schema

TITULO_FORMULARIO = "Reclamação de produto no SAC sem o formulário do site"
INSTRUCAO_FORMULARIO = (
    "Quando o cliente escreve direto no e-mail do SAC reclamando de defeito ou de problema "
    "no produto e o atendimento NÃO veio pelo formulário de SAC do site (sem protocolo), "
    "não tente resolver o caso por e-mail: agradeça o contato, diga que, para analisar, "
    "ele precisa abrir o atendimento pelo formulário de SAC do site (o link está nos dados "
    "do e-mail) e liste o que ter em mãos — os dados do comprador, os dados da compra "
    "(onde comprou, número do pedido, datas e nota fiscal), os dados do produto, a "
    "descrição do defeito, as fotos e o vídeo curto do defeito. Diga que, ao enviar, ele "
    "recebe o número do protocolo na hora. Se o e-mail já veio pelo formulário (tem "
    "protocolo) ou o cliente já mandou esses dados, siga o atendimento normal."
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    tabela = op.create_table(
        "mail_agente_regras",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("chave", sa.String(32), nullable=True),
        sa.Column("titulo", sa.String(120), nullable=False),
        sa.Column("instrucao", sa.Text(), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *[
            sa.Column(
                n, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
            )
            for n in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint("chave", name="uq_mail_agente_regras_chave"),
        schema=SCHEMA,
    )
    op.bulk_insert(
        tabela,
        [
            {
                "id": "6f1d3c2e-8a4b-4c7d-9e21-0a5b7c9d1e01",
                "chave": "formulario_sac",
                "titulo": TITULO_FORMULARIO,
                "instrucao": INSTRUCAO_FORMULARIO,
                "ativa": True,
                "ordem": 0,
            }
        ],
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("mail_agente_regras", schema=SCHEMA)
