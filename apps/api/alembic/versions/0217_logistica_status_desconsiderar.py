"""logistica_status.desconsiderar: esconder pedido do painel vira decisão explícita.

Antes, uma regra da aba Status SEM NENHUMA ação preenchida escondia do painel
os pedidos casados ("chave conhecida/ok" implícita). Isso sumia com pedidos com
problema — ex. a chave "Pago | Pronto p/ envio | Aguardando NF" travada, ou as
chaves de retido — sem ninguém ter decidido isso.

Agora esconder é opt-in: o botão "Desconsiderar" da aba Status. Regra sem ação
e sem a marca MANTÉM o pedido visível no painel.

O backfill marca desconsiderar=true nas regras que hoje são sem-ação — assim o
deploy sozinho não muda NADA no painel; o operador destrava chave por chave
pelo botão novo.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0217_logistica_status_desconsiderar"
down_revision: str | None = "0216_store_info_excecoes"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "logistica_status",
        sa.Column("desconsiderar", sa.Boolean(), nullable=False, server_default="false"),
        schema=SCHEMA,
    )
    # Regras sem NENHUMA ação escondiam o pedido; preserva esse comportamento
    # marcando-as como desconsiderar até o operador decidir o contrário.
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


def downgrade() -> None:
    op.drop_column("logistica_status", "desconsiderar", schema=SCHEMA)
