"""situacao_bling — inativa o que não existe mais no Bling (lista manual)

Eduardo, 15/09/2026: o dropdown "Situação no Bling ao fechar" (Chamados) e o
"Alterar status Bling" (Logística) mostravam um monte de situação que o Bling
já apagou ("Enviado Geral CI/PI/SA/SP", "Enviado Fake", "Golpe"…). O sync
automático (services/bling_situacoes_sync, migration 0270) não roda porque o
app do Bling ainda não tem permissão pro módulo Situações (403). Enquanto a
permissão não é liberada, esta migration aplica a lista que o Eduardo mandou
da tela Preferências › Vendas › Situações do Bling (15 situações):

    Em aberto · Atendido · Cancelado · Em andamento · Venda Agenciada ·
    Em digitação · Verificado · Entregue · Aguardando Cancelamento ·
    Perdimento · Aguardando Devolução · Problemas · Verificar Cancelamento ·
    Manutenção · Resolvido

Regra: `ativo = nome está na lista` (sem caixa/espaços, com e sem acento).
Não apaga linha: pedidos antigos em `bling_orders.situacao` ainda apontam
pros ids e precisam do nome. Quando o sync ganhar a permissão, ele passa a
mandar (e corrige esta lista sozinho).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0271_situacao_bling_lista_manual"
down_revision: str | None = "0270_situacao_bling_ativo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Nomes em minúsculas; variantes sem acento por segurança (catálogo veio do
# userdb antigo e pode ter grafia diferente).
EXISTEM_NO_BLING: tuple[str, ...] = (
    "em aberto",
    "atendido",
    "cancelado",
    "em andamento",
    "venda agenciada",
    "em digitação",
    "em digitacao",
    "verificado",
    "entregue",
    "aguardando cancelamento",
    "perdimento",
    "aguardando devolução",
    "aguardando devolucao",
    "problemas",
    "verificar cancelamento",
    "manutenção",
    "manutencao",
    "resolvido",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE davinci.situacao_bling SET ativo = (lower(trim(nome)) = ANY(:nomes))"
        ).bindparams(sa.bindparam("nomes", value=list(EXISTEM_NO_BLING), type_=sa.ARRAY(sa.Text())))
    )


def downgrade() -> None:
    op.execute("UPDATE davinci.situacao_bling SET ativo = true")
