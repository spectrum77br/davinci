# ruff: noqa: E501, S608
"""Devoluções: reabre o que foi dado como resolvido sem ter voltado pro estoque

Vinicius, 16/11/2026: "resolvido pra nós é um pouco diferente — se já voltou
estoque tá resolvido… o chamado em aberto é outra coisa". As marcas da largada
(0483: o Bling já tinha saído de Aguardando Devolução/Manutenção; 0485: o
histórico antigo) deram como resolvido pedido cujo produto NÃO terminou:

  - peça com o técnico (Manutenção sem destino escolhido);
  - entrada de estoque que não confirmou no Bling (falhou / conferindo / não
    confirmada).

Esses voltam pra lista de abertas (a pessoa conclui e clica Resolver). Medido
em produção na hora: 6 com o técnico, 14 com o estoque pendente. Os lançados
SEM condição ficam como estão (decisão do dono, abaixo).

Só dado, só nas marcas do sistema (resolução feita por pessoa não é tocada).

Revision ID: 0486_devolucao_reabre_o_que_nao_terminou
Revises: 0485_devolucao_chamado_conferencia
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0486_devolucao_reabre_o_que_nao_terminou"
down_revision: str | None = "0485_devolucao_chamado_conferencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
MARCAS = ("antes do painel novo", "antes do painel novo (histórico)")
# Lançado sem condição também volta? O dono decidiu que não (16/11/2026, "ficam
# resolvidos"): 77 pedidos com o Bling já em Resolvido/Entregue/Perdimento/Problemas.
REABRE_SEM_CONDICAO = False


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    sem_condicao = (
        "OR COALESCE(btrim(d.condicao_produto), '') = ''" if REABRE_SEM_CONDICAO else ""
    )
    op.execute(
        f"""
        UPDATE "{SCHEMA}".devolucao_rastreio r
           SET resolvido_at = NULL, resolvido_por = NULL, resolvido_como = NULL
         WHERE r.resolvido_como IN ('{MARCAS[0]}', '{MARCAS[1]}')
           AND EXISTS (
                 SELECT 1 FROM "{SCHEMA}".devolutions d
                 WHERE btrim(d.pedido_bling) = r.pedido_bling
                   AND (
                         (d.condicao_produto = 'Manutenção'
                          AND COALESCE(btrim(d.manutencao_destino), '') = '')
                      OR COALESCE(d.estoque_status, '') IN ('falhou', 'conferindo', 'sem_confirmacao')
                      {sem_condicao}
                   ))
        """
    )


def downgrade() -> None:
    # Só dado: não há como saber quais foram reabertos aqui.
    pass
