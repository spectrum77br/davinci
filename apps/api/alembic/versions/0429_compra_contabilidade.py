"""Cadastros › Contabilidade › Compra: catálogo de produtos e compras das empresas do grupo

Eduardo (23/10/2026): a aba Emissão de Serviço vira "Contabilidade" e ganha a
sub-aba "Compra" — a empresa do grupo compra embalagem de uma das contas Bling
de NF, com base no faturamento do mês. Espelha `app/models/compra.py` (ver lá o
porquê de cada tabela). Nasce com o produto "Embalagem" (SKU a001, NCM
3923.21.10, R$ 9,30 — o preço da NF-e 13917 que ele mandou de modelo).

Revision ID: 0429_compra_contabilidade
Revises: 0428_tiktok_localizacao_retornando_remetente
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0429_compra_contabilidade"
down_revision: str | None = "0428_tiktok_localizacao_retornando_remetente"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
UUID = pg.UUID(as_uuid=True)
NUMERO_INICIAL = 8_000_001


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _user_fk(nome: str) -> sa.Column:
    return sa.Column(
        nome, UUID, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    op.create_table(
        "compra_produto",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("ncm", sa.String(8), nullable=False),
        sa.Column("cfop", sa.String(4), nullable=True),
        sa.Column("unidade", sa.String(6), server_default="UN", nullable=False),
        sa.Column("preco_padrao", sa.Numeric(12, 2), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        _user_fk("created_by"),
        *_ts(),
        sa.CheckConstraint("ncm ~ '^[0-9]{8}$'", name=op.f("ck_compra_produto_ncm_8_digitos")),
        sa.CheckConstraint(
            "cfop IS NULL OR cfop ~ '^[1-7][0-9]{3}$'",
            name=op.f("ck_compra_produto_cfop_4_digitos"),
        ),
        sa.CheckConstraint(
            "preco_padrao IS NULL OR preco_padrao > 0",
            name=op.f("ck_compra_produto_preco_positivo"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compra_produto_nome",
        "compra_produto",
        [sa.text("lower(nome)")],
        unique=True,
        schema=SCHEMA,
    )

    op.create_table(
        "compra_pedido",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "numero",
            sa.BigInteger(),
            sa.Identity(start=NUMERO_INICIAL),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("vendedor_cnpj", sa.String(14), nullable=False),
        sa.Column("vendedor_nome", sa.Text(), nullable=False),
        sa.Column("vendedor_uf", sa.String(2), nullable=True),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("data_pedido", sa.Date(), nullable=False),
        sa.Column("faturamento", sa.Numeric(14, 2), nullable=True),
        sa.Column("modo", sa.String(12), nullable=False),
        sa.Column("percentual", sa.Numeric(9, 4), nullable=True),
        sa.Column("cfop", sa.String(4), nullable=False),
        sa.Column("comprador", pg.JSONB(), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(12), server_default="registrada", nullable=False),
        sa.Column("nf_numero", sa.Text(), nullable=True),
        sa.Column("nf_chave", sa.String(44), nullable=True),
        sa.Column("emissao_pedida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emissao_iniciada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emissao_tentativas", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("emissao_erro", sa.Text(), nullable=True),
        sa.Column("nf_emitida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        _user_fk("created_by"),
        _user_fk("updated_by"),
        *_ts(),
        sa.CheckConstraint(
            "status IN ('registrada', 'na_fila', 'emitindo', 'nf_emitida', 'erro', 'cancelada')",
            name=op.f("ck_compra_pedido_status_valido"),
        ),
        sa.CheckConstraint(
            "modo IN ('quantidade', 'percentual')", name=op.f("ck_compra_pedido_modo_valido")
        ),
        sa.CheckConstraint(
            "vendedor_cnpj ~ '^[0-9]{14}$'", name=op.f("ck_compra_pedido_vendedor_cnpj_14")
        ),
        sa.CheckConstraint(
            "cfop ~ '^[1-7][0-9]{3}$'", name=op.f("ck_compra_pedido_cfop_4_digitos")
        ),
        sa.CheckConstraint(
            "extract(day from competencia) = 1", name=op.f("ck_compra_pedido_competencia_dia_1")
        ),
        sa.CheckConstraint(
            "(modo = 'percentual') = (percentual IS NOT NULL)",
            name=op.f("ck_compra_pedido_percentual_so_no_modo"),
        ),
        sa.CheckConstraint("total > 0", name=op.f("ck_compra_pedido_total_positivo")),
        sa.CheckConstraint(
            "status <> 'nf_emitida' OR nf_numero IS NOT NULL",
            name=op.f("ck_compra_pedido_nf_emitida_tem_numero"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_compra_pedido_competencia", "compra_pedido", ["competencia"], schema=SCHEMA)
    op.create_index("ix_compra_pedido_company", "compra_pedido", ["company_id"], schema=SCHEMA)

    op.create_table(
        "compra_pedido_item",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "pedido_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.compra_pedido.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "produto_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.compra_produto.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ordem", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("ncm", sa.String(8), nullable=False),
        sa.Column("unidade", sa.String(6), server_default="UN", nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("valor_unitario", sa.Numeric(12, 2), nullable=False),
        sa.Column("valor_total", sa.Numeric(14, 2), nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "quantidade > 0", name=op.f("ck_compra_pedido_item_quantidade_positiva")
        ),
        sa.CheckConstraint(
            "valor_unitario > 0", name=op.f("ck_compra_pedido_item_unitario_positivo")
        ),
        sa.CheckConstraint("ncm ~ '^[0-9]{8}$'", name=op.f("ck_compra_pedido_item_ncm_8_digitos")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_compra_pedido_item_pedido", "compra_pedido_item", ["pedido_id"], schema=SCHEMA
    )

    op.create_table(
        "compra_vendedora",
        sa.Column("cnpj", sa.String(14), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("adspower_serial", sa.Text(), nullable=True),
        sa.Column("automatico", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        _user_fk("updated_by"),
        *_ts(),
        sa.CheckConstraint("cnpj ~ '^[0-9]{14}$'", name=op.f("ck_compra_vendedora_cnpj_14")),
        sa.CheckConstraint(
            "NOT automatico OR adspower_serial IS NOT NULL",
            name=op.f("ck_compra_vendedora_automatico_tem_perfil"),
        ),
        schema=SCHEMA,
    )
    # As 4 contas Bling de NF (Eduardo: "as empresas são as mesmas que já utilizamos do
    # bling"). Só a BEZERRA (perfil 44 = k13ksbpr) emite hoje; as outras ficam desligadas
    # até voltarem (levantamento de 23/10).
    op.execute(
        "INSERT INTO davinci.compra_vendedora "
        "(cnpj, nome, uf, adspower_serial, automatico, ativo, observacao) VALUES "
        "('67285941000190', 'COMERCIAL BEZERRA', 'SP', 'k13ksbpr', true, true, "
        "'Bling 44 (perfil k13ksbpr), conta exclusiva desde 16/10/2026'), "
        "('67041460000139', 'COMERCIAL TELES', 'SP', NULL, false, true, "
        "'SEFAZ recusa (781 emissor não habilitado) desde 16/10/2026; sem perfil no AdsPower'), "
        "('66829674000101', 'COMERCIAL DL', 'SP', NULL, false, true, "
        "'sem perfil no AdsPower desde 24/09/2026'), "
        "('62570297000188', '62.570.297 PEDRO FIORAVANTE DAL SASSO', 'SP', NULL, false, true, "
        "'sem perfil no AdsPower desde 02/10/2026; Bling com cobrança pendente')"
    )
    op.execute(
        "INSERT INTO davinci.compra_produto "
        "(id, nome, sku, ncm, cfop, unidade, preco_padrao, ativo) "
        "VALUES (gen_random_uuid(), 'Embalagem', 'a001', '39232110', NULL, 'UN', 9.30, true)"
    )


def downgrade() -> None:
    op.drop_table("compra_vendedora", schema=SCHEMA)
    op.drop_index("ix_compra_pedido_item_pedido", table_name="compra_pedido_item", schema=SCHEMA)
    op.drop_table("compra_pedido_item", schema=SCHEMA)
    op.drop_index("ix_compra_pedido_company", table_name="compra_pedido", schema=SCHEMA)
    op.drop_index("ix_compra_pedido_competencia", table_name="compra_pedido", schema=SCHEMA)
    op.drop_table("compra_pedido", schema=SCHEMA)
    op.drop_index("uq_compra_produto_nome", table_name="compra_produto", schema=SCHEMA)
    op.drop_table("compra_produto", schema=SCHEMA)
