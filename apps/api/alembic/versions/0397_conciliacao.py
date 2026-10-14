"""Conciliação — tabelas do painel (passo 2, 14/10/2026)

Desenho em `docs/conciliacao-avaliacao.md`. Cinco tabelas novas e vazias:
`conciliacao_lojas` (quais contas entram e desde quando), `conciliacao_movimentos`
(o livro-razão: linhas do extrato do Mercado Pago e cobranças da fatura do ML),
`conciliacao_pedidos` (uma linha por pedido do Bling com as quatro versões do
dinheiro, resultado, situação e etapa), `conciliacao_sync` (controle das
leituras) e `conciliacao_dias` (fechamento do dia). Nada existente muda.

Dado: liga a loja **jlas2** (Mercado Livre) com início em 01/10/2026 — a loja
da sondagem, escolhida com o Vinicius: pedidos vendidos a partir dessa data
entram; linha de pedido anterior é classificada "anterior ao início". Se não
achar a integração, só avisa (print) e segue.

DOWNGRADE: derruba as cinco tabelas.

Revision ID: 0397_conciliacao
Revises: 0396_ultima_loja_qualquer
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0397_conciliacao"
down_revision: str | None = "0396_ultima_loja_qualquer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
_PLATFORMS = ("bling", "ml", "shopee", "amazon", "tiktok", "temu", "shein", "magalu")


def _platform():
    return postgresql.ENUM(
        *_PLATFORMS, name="integration_platform", schema=SCHEMA, create_type=False
    )


def _uuid_pk():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _integration_fk():
    return sa.Column(
        "integration_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str, nullable: bool = True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _money(name: str):
    return sa.Column(name, sa.Numeric(14, 2), nullable=True)


def _timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("SET lock_timeout = '10s'")

    op.create_table(
        "conciliacao_lojas",
        _uuid_pk(),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("conta", sa.String(128), nullable=False),
        sa.Column("ativa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("inicio", sa.Date(), nullable=False),
        _ts("extrato_config_em"),
        _ts("ultimo_extrato_em"),
        _ts("ultimo_fatura_em"),
        _ts("ultimo_recalculo_em"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        *_timestamps(),
        schema=SCHEMA,
    )

    op.create_table(
        "conciliacao_movimentos",
        _uuid_pk(),
        _integration_fk(),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("origem", sa.String(20), nullable=False),
        sa.Column("id_externo", sa.String(80), nullable=False),
        _ts("data", nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("sub_tipo", sa.String(40), nullable=True),
        sa.Column("pagamento_id", sa.String(40), nullable=True),
        sa.Column("pedido_marketplace", sa.String(40), nullable=True),
        sa.Column("pack_id", sa.String(40), nullable=True),
        sa.Column("shipping_id", sa.String(40), nullable=True),
        sa.Column("pedido_bling", sa.String(20), nullable=True),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        _money("bruto"),
        _money("tarifa"),
        _money("parcelamento"),
        _money("frete"),
        _money("cupom"),
        sa.Column("credito", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("debito", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        _money("saldo_apos"),
        sa.Column("descontado_da_venda", sa.Boolean(), nullable=True),
        sa.Column("sku", sa.String(120), nullable=True),
        sa.Column("item_id", sa.String(40), nullable=True),
        _ts("data_aprovacao"),
        sa.Column("classificacao", sa.String(40), nullable=True),
        sa.Column("vinculado_por", sa.String(120), nullable=True),
        _ts("vinculado_em"),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint(
            "integration_id", "origem", "id_externo", name="uq_conciliacao_mov_externo"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_mov_loja_data",
        "conciliacao_movimentos",
        ["integration_id", "data"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_mov_pedido_mkt",
        "conciliacao_movimentos",
        ["pedido_marketplace"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_mov_pedido_bling", "conciliacao_movimentos", ["pedido_bling"], schema=SCHEMA
    )
    op.create_index(
        "ix_conciliacao_mov_tipo",
        "conciliacao_movimentos",
        ["integration_id", "tipo"],
        schema=SCHEMA,
    )

    op.create_table(
        "conciliacao_pedidos",
        _uuid_pk(),
        _integration_fk(),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("pedido_bling", sa.String(20), nullable=False),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        sa.Column("numeroloja", sa.String(40), nullable=True),
        sa.Column("pack_id", sa.String(40), nullable=True),
        sa.Column("conta", sa.String(128), nullable=True),
        _ts("data_pedido"),
        sa.Column("situacao_bling", sa.String(60), nullable=True),
        _money("bling_total"),
        _money("bling_produtos"),
        _money("bling_valorbase"),
        _money("bling_custofrete"),
        _money("bling_taxacomissao"),
        _money("venda_valor"),
        _money("venda_tarifa"),
        _money("venda_frete"),
        _money("venda_cupom"),
        _money("venda_estorno"),
        _money("venda_liquido"),
        sa.Column("venda_status", sa.String(20), nullable=True),
        _ts("previsao_liberacao"),
        _money("fatura_tarifa"),
        _money("fatura_parcelamento"),
        _money("fatura_frete"),
        _money("fatura_bonificacao"),
        _money("fatura_outros"),
        _ts("liberado_em"),
        _money("liberado_valor"),
        _money("saidas_depois"),
        _money("recebido"),
        _ts("ultimo_movimento_em"),
        _money("custo"),
        _money("reembolso"),
        _money("sobrou"),
        sa.Column("margem", sa.Numeric(10, 4), nullable=True),
        _money("dif_bling_venda"),
        _money("dif_previsto_recebido"),
        sa.Column("situacao", sa.String(30), nullable=False, server_default="aguardando"),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("etapa", sa.String(20), nullable=False, server_default="aguardando"),
        _ts("etapa_em"),
        sa.Column("etapa_por", sa.String(120), nullable=True),
        sa.Column("etapa_motivo", sa.Text(), nullable=True),
        _ts("bateu_em"),
        sa.Column("conferido_por", sa.String(120), nullable=True),
        _ts("conferido_em"),
        sa.Column("observacao", sa.Text(), nullable=True),
        _ts("recalculado_em"),
        *_timestamps(),
        sa.UniqueConstraint("integration_id", "pedido_bling", name="uq_conciliacao_pedido_loja"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_ped_numeroloja", "conciliacao_pedidos", ["numeroloja"], schema=SCHEMA
    )
    op.create_index(
        "ix_conciliacao_ped_situacao",
        "conciliacao_pedidos",
        ["integration_id", "situacao"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_ped_etapa",
        "conciliacao_pedidos",
        ["integration_id", "etapa"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conciliacao_ped_data",
        "conciliacao_pedidos",
        ["integration_id", "data_pedido"],
        schema=SCHEMA,
    )

    op.create_table(
        "conciliacao_sync",
        _uuid_pk(),
        _integration_fk(),
        sa.Column("origem", sa.String(20), nullable=False),
        sa.Column("chave", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pedido"),
        sa.Column("arquivo", sa.Text(), nullable=True),
        _ts("pedido_em"),
        _ts("lido_em"),
        sa.Column("linhas", sa.Integer(), nullable=True),
        sa.Column(
            "detalhe", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("erro", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("integration_id", "origem", "chave", name="uq_conciliacao_sync_chave"),
        schema=SCHEMA,
    )

    op.create_table(
        "conciliacao_dias",
        _uuid_pk(),
        _integration_fk(),
        sa.Column("dia", sa.Date(), nullable=False),
        _money("saldo_inicial"),
        _money("creditos"),
        _money("debitos"),
        _money("saldo_final"),
        _money("saldo_informado"),
        sa.Column("linhas", sa.Integer(), nullable=True),
        sa.Column("pendentes", sa.Integer(), nullable=True),
        _ts("fechado_em"),
        sa.Column("fechado_por", sa.String(120), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("integration_id", "dia", name="uq_conciliacao_dia_loja"),
        schema=SCHEMA,
    )

    # Liga a jlas2 (ML) com início em 01/10/2026 — decisão de 14/10.
    bind = op.get_bind()
    achou = bind.execute(
        sa.text(
            f"""
            INSERT INTO "{SCHEMA}".conciliacao_lojas
                   (id, integration_id, platform, conta, ativa, inicio)
            SELECT gen_random_uuid(), i.id, i.platform, i.name, true, DATE '2026-10-01'
              FROM "{SCHEMA}".integrations i
             WHERE i.platform = 'ml'
               AND lower(trim(i.name)) = 'jlas2'
               AND i.archived_at IS NULL
             ORDER BY (i.status = 'active') DESC, i.created_at
             LIMIT 1
            RETURNING conta, integration_id
            """  # noqa: S608 — SCHEMA é constante do arquivo
        )
    ).fetchall()
    print(
        f"0397_conciliacao: loja ligada = {achou or 'NENHUMA (integração jlas2 ML não encontrada)'}"
    )


def downgrade() -> None:
    op.drop_table("conciliacao_dias", schema=SCHEMA)
    op.drop_table("conciliacao_sync", schema=SCHEMA)
    op.drop_table("conciliacao_pedidos", schema=SCHEMA)
    op.drop_table("conciliacao_movimentos", schema=SCHEMA)
    op.drop_table("conciliacao_lojas", schema=SCHEMA)
