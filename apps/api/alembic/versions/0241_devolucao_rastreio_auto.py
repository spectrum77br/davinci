# ruff: noqa: E501
"""Acompanhamento de Devoluções: rastreio AUTOMÁTICO do pacote que volta — Eduardo, 2026-09-03.

"o TikTok não está pegando o número de rastreio correto" / "esse rastreio está
incorreto, precisa sempre estar atualizadinho" / "em devolução desde todas as
datas estão iguais". A aba mostrava o rastreio da ENTREGA original; o pacote
que interessa é o da DEVOLUÇÃO (código/transportadora próprios, vindos da
returns API de TikTok/Shopee/ML). Colunas novas em `devolucao_rastreio`
(grão pedido; as manuais continuam mandando):

- rastreio_auto / transportadora_auto — código e transportadora do retorno
- localizacao_auto / localizacao_auto_data — último evento do pacote de volta
  (17track para códigos Correios) e quando
- devolucao_status_auto / devolucao_id_auto / fonte_auto — status cru, id do
  caso e marketplace de origem
- devolucao_criada_em / devolucao_atualizada_em — quando a devolução foi
  aberta (vira o "Em devolução desde" real) e a última mexida
- auto_sync_at — última passada do job

Revision ID: 0241_devolucao_rastreio_auto
Revises: 0240_chamados_valor_recuperado
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0241_devolucao_rastreio_auto"
down_revision: str | None = "0240_chamados_valor_recuperado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_COLS = (
    ("rastreio_auto", sa.Text()),
    ("transportadora_auto", sa.Text()),
    ("localizacao_auto", sa.Text()),
    ("localizacao_auto_data", sa.DateTime(timezone=True)),
    ("devolucao_status_auto", sa.Text()),
    ("devolucao_id_auto", sa.Text()),
    ("fonte_auto", sa.Text()),
    ("devolucao_criada_em", sa.DateTime(timezone=True)),
    ("devolucao_atualizada_em", sa.DateTime(timezone=True)),
    ("auto_sync_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    for name, typ in _COLS:
        op.add_column("devolucao_rastreio", sa.Column(name, typ, nullable=True))
    # O webhook do 17track procura a linha pelo código do retorno.
    op.create_index(
        "ix_devolucao_rastreio_rastreio_auto",
        "devolucao_rastreio",
        ["rastreio_auto"],
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index("ix_devolucao_rastreio_rastreio_auto", table_name="devolucao_rastreio")
    for name, _ in reversed(_COLS):
        op.drop_column("devolucao_rastreio", name)
