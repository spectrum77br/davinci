"""Remove logistica_status.desconsiderar: Monitorar + "Mostrar tudo" bastam.

Volta a regra simples: regra da aba Status sem NENHUMA ação esconde o pedido
do painel (chave conhecida/ok). Pra manter uma chave à vista marca-se
Monitorar; pra espiar os escondidos existe o botão "Mostrar tudo".

Antes de derrubar a coluna, as regras sem ação que o operador tinha deixado
com desconsiderar=false (ou seja: "quero ver esses") ganham Monitorar — assim
o deploy não some com nada que estava visível de propósito.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0219_remove_logistica_status_desconsiderar"
down_revision: str | None = "0218_threema_informar_config"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Regra sem ação e SEM a marca desconsiderar estava visível por decisão do
    # operador → vira Monitorar (mesmo efeito: fica no painel).
    op.execute(
        """
        UPDATE davinci.logistica_status
           SET monitoramento = true
         WHERE desconsiderar = false
           AND coalesce(btrim(alterar_status_bling), '') = ''
           AND monitoramento = false
           AND abrir_chamado = false
           AND abrir_reembolso = false
           AND coalesce(btrim(mensagem_chamado), '') = ''
           AND coalesce(btrim(mensagem_bling), '') = ''
           AND coalesce(btrim(mensagem_threema), '') = ''
        """
    )
    op.drop_column("logistica_status", "desconsiderar", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column("desconsiderar", sa.Boolean(), nullable=False, server_default="false"),
        schema=SCHEMA,
    )
    # Espelho do backfill da 0217: regra sem ação volta a esconder via marca.
    op.execute(
        """
        UPDATE davinci.logistica_status
           SET desconsiderar = true
         WHERE coalesce(btrim(alterar_status_bling), '') = ''
           AND monitoramento = false
           AND abrir_chamado = false
           AND abrir_reembolso = false
           AND coalesce(btrim(mensagem_chamado), '') = ''
           AND coalesce(btrim(mensagem_bling), '') = ''
           AND coalesce(btrim(mensagem_threema), '') = ''
        """
    )
