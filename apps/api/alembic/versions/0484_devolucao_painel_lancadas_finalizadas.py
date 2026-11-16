# ruff: noqa: E501, S608
"""Devoluções: o que já estava lançado antes do painel novo nasce finalizado

Vinicius, 16/11/2026 (no dia seguinte à virada): "os pedidos que já estava
tipo com estoque lançado antigo deixa tudo como finalizado já". A largada da
0483 só olhou a situação do Bling; sobraram na lista pedidos que continuam em
Aguardando Devolução / Manutenção no Bling, mas cujo lançamento já tinha
terminado antes do painel novo (estoque lançado, extraviado, não devolvido).

Nasce finalizado o pedido aberto em que:
  - há pelo menos um lançamento e TODOS têm condição;
  - nada está com o técnico (Manutenção sem destino escolhido);
  - nenhuma entrada de estoque está pendente (falhou / conferindo / não
    confirmada);
  - não há chamado aberto no pedido.
É a mesma régua do "Pronto pra resolver" (services/devolucao_painel). Quem
tem peça com o técnico continua aberto, na etapa Manutenção.

Só dado, na marca da largada (não aparece em "Resolvidas" nem no resumo).

Revision ID: 0484_devolucao_painel_lancadas_finalizadas
Revises: 0483_devolucao_painel_resolver
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0484_devolucao_painel_lancadas_finalizadas"
down_revision: str | None = "0483_devolucao_painel_resolver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LARGADA = "antes do painel novo"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.execute(
        f"""
        INSERT INTO "{SCHEMA}".devolucao_rastreio (pedido_bling, resolvido_at, resolvido_como)
        SELECT d.pedido_bling, now(), '{LARGADA}'
        FROM "{SCHEMA}".devolutions d
        WHERE d.pedido_bling IS NOT NULL AND btrim(d.pedido_bling) <> ''
        GROUP BY d.pedido_bling
        HAVING bool_and(COALESCE(btrim(d.condicao_produto), '') <> '')
           AND NOT bool_or(
                 d.condicao_produto = 'Manutenção'
                 AND COALESCE(btrim(d.manutencao_destino), '') = '')
           AND NOT bool_or(
                 COALESCE(d.estoque_status, '') IN ('falhou', 'conferindo', 'sem_confirmacao'))
           AND NOT EXISTS (
                 SELECT 1 FROM "{SCHEMA}".chamados c
                 WHERE c.pedido_bling = d.pedido_bling AND NOT c.resolvido)
           AND EXISTS (
                 SELECT 1 FROM "{SCHEMA}".bling_orders bo
                 WHERE bo.numero = d.pedido_bling
                   AND (bo.aguardando_devolucao_data IS NOT NULL
                        OR bo.situacao IN ('83957', '84677')))
        ON CONFLICT (pedido_bling) DO UPDATE
            SET resolvido_at = EXCLUDED.resolvido_at,
                resolvido_como = EXCLUDED.resolvido_como
            WHERE "{SCHEMA}".devolucao_rastreio.resolvido_at IS NULL
        """
    )


def downgrade() -> None:
    # Só dado: não há como saber quem foi marcado aqui e quem na 0483.
    pass
