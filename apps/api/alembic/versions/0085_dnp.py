# ruff: noqa: E501
"""DNP — Desenvolvimento de Produtos

Tabelas:
  * dnp_config    — singleton (id=1) com dolar_dia + certificado.
                    Operador edita pelo cabeçalho da página; o dólar
                    é refrescado automaticamente via awesomeapi do
                    lado do cliente, mas o valor persistido aqui é
                    o que vale pros cálculos da próxima carga.
  * dnp_produtos  — uma linha por produto sob avaliação.

Seed: 35 produtos da planilha-mãe (cooker, airfryer, TVs, lavadoras
etc.). Idempotente — só insere se a tabela está vazia.

Revision ID: 0085_dnp
Revises: 0084_financeiro_simulacao_seed
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0085_dnp"
down_revision: str | None = "0084_financeiro_simulacao_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # ── dnp_config (singleton id=1) ────────────────────────────────────
    op.create_table(
        "dnp_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dolar_dia", sa.Numeric(10, 4), nullable=True),
        sa.Column("certificado", sa.Numeric(14, 2), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_dnp_config_singleton"),
        schema=SCHEMA,
    )
    op.execute(f"""
        INSERT INTO "{SCHEMA}".dnp_config (id, dolar_dia, certificado)
        VALUES (1, 5.0, 10000)
        ON CONFLICT (id) DO NOTHING;
    """)

    # ── dnp_produtos ───────────────────────────────────────────────────
    op.create_table(
        "dnp_produtos",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("produto", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("fabrica", sa.Text(), nullable=True),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column("moq", sa.Integer(), nullable=True),
        sa.Column("voltagem", sa.Text(), nullable=True),
        sa.Column("cor", sa.Text(), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("tamanho", sa.Text(), nullable=True),
        sa.Column("potencia", sa.Text(), nullable=True),
        sa.Column("valor_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("projecao_compra", sa.Integer(), nullable=True),
        sa.Column("fator", sa.Numeric(8, 4), nullable=True),
        sa.Column("venda_estimada", sa.Numeric(14, 2), nullable=True),
        sa.Column("frete", sa.Numeric(14, 2), nullable=True),
        sa.Column("comissao", sa.Numeric(7, 4), nullable=True),
        sa.Column("inmetro", sa.Text(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_dnp_produtos_produto", "dnp_produtos", ["produto"], schema=SCHEMA)

    # ── Seed ───────────────────────────────────────────────────────────
    op.execute(f"""
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM "{SCHEMA}".dnp_produtos LIMIT 1) THEN
            INSERT INTO "{SCHEMA}".dnp_produtos
                (produto, link, valor_usd, projecao_compra, fator, venda_estimada, frete, comissao, inmetro, obs)
            VALUES
                ('cooker', 'https://www.alibaba.com/product-detail/Anbolife-Multi-Cooker-Electric-Pressure-Cooker_1601273270316.html', 78, 500, 2, 1500, 30, 0.14, '', ''),
                ('cooker', 'https://www.alibaba.com/product-detail/Anbo-Hot-Sale-Smart-Control-air_1601473099012.html', 35, 500, 2, 620, 30, 0.14, '', ''),
                ('airfryer', 'https://www.alibaba.com/product-detail/Anbo-Hot-Sale-Smart-Control-air_1601473099012.html', 35.5, 500, 2, 650, 30, 0.14, '', ''),
                ('airfryer', 'https://www.alibaba.com/product-detail/Anbo-Hot-Sale-Smart-Control-air_1601473099012.html', 42.5, 500, 2, 730, 30, 0.14, '', ''),
                ('iron', 'https://www.alibaba.com/product-detail/Anbo-1500W-Ceramic-Soleplate-Cordless-Steam_1601287164029.html', 6.7, 500, 2, 120, 30, 0.14, '', ''),
                ('iron', 'https://www.alibaba.com/product-detail/Anbo-1500W-Ceramic-Soleplate-Cordless-Steam_1601287164029.html', 6.7, 500, 2, 120, 30, 0.14, '', ''),
                ('iron', 'https://www.alibaba.com/product-detail/Anbolife-2200W-Multi-function-Cordless-Electric_1601287164029.html', 7.5, 500, 2, 120, 30, 0.14, '', ''),
                ('coffee machine', 'https://www.alibaba.com/product-detail/Anbo-Fully-Automatic-Drip-Coffee-Maker_1601473099012.html', 11.5, 500, 2, NULL, 30, 0.14, '', ''),
                ('coffee machine', 'https://www.alibaba.com/product-detail/Anbo-Fully-Automatic-Drip-Coffee-Maker_1601473099012.html', 28, 500, 2, NULL, 30, 0.14, '', ''),
                ('coffee machine', 'https://www.alibaba.com/product-detail/Anbo-Fully-Automatic-Drip-Coffee-Maker_1601473099012.html', 35, 500, 2, NULL, 30, 0.14, '', ''),
                ('fogao 5 bocas', 'https://www.alibaba.com/product-detail/Gas-Stove-5-Burner-Electric-Gas_1601377947984.html', 42, 500, 2, NULL, 30, 0.14, '', ''),
                ('fogao 5 bocas', 'https://www.alibaba.com/product-detail/Gas-Stove-5-Burner-Electric-Gas_1601377947984.html', 55, 500, 2, NULL, 30, 0.14, '', ''),
                ('lava louça 14 serviços', 'https://www.alibaba.com/product-detail/Kitchen-Appliances-Made-in-China-Household_1600567120113.html', 198, 500, 2, NULL, 30, 0.14, 'teste iec 60335-2', ''),
                ('lava louça 12 serviços', 'https://www.alibaba.com/product-detail/Smart-Household-Kitchen-Appliance-Freestanding_1601205564037.html', 109, 500, 2, NULL, 30, 0.14, 'teste iec 60335-2', ''),
                ('lava roupa 8kg', 'https://www.alibaba.com/product-detail/Home-Use-White-Front-Load-Washer_1601567432968.html', 165, 500, 2, NULL, 30, 0.14, 'teste iec 60335-2', ''),
                ('aspirador po vertical 800w', 'https://www.alibaba.com/product-detail/Hot-Sale-Cordless-Stick-Vacuum-Cleaner_1601033296461.html', 25, 500, 2, NULL, 30, 0.14, 'teste ruido', ''),
                ('aspirador po vertical 300w', 'https://www.alibaba.com/product-detail/High-Quality-Cordless-Handheld-Vacuum-Stick_1601034734409.html', 14.5, 500, 2, NULL, 30, 0.14, 'teste ruido', ''),
                ('TV 32', 'https://www.alibaba.com/product-detail/32-Inch-LED-TV-Sets-Custom_1601451548470.html', 44, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 43', 'https://www.alibaba.com/product-detail/43-Inch-Smart-TV-4K-Ultra_1601178016932.html', 73, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 50', 'https://www.alibaba.com/product-detail/Wholesale-50inch-Flat-Screen-LED-TV_1600592860060.html', 104, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 55', 'https://www.alibaba.com/product-detail/Factory-OEM-TV-55inch-Flat-Screen_1600593671179.html', 119, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 65', 'https://www.alibaba.com/product-detail/Big-Screen-LED-TV-65Inch-4K_1600594577803.html', 162, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 75', 'https://www.alibaba.com/product-detail/Explosion-proof-Screen-75inch-Ultra-HD_1600716969792.html', 253, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 85', 'https://www.alibaba.com/product-detail/Manufacturer-Smart-TV-85inch-Television-4K_1601198864127.html', 413, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV OLED 43', 'https://www.alibaba.com/product-detail/No-Frame-Smart-TV-43-inch_1601296273406.html', 105, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 50 OLED', 'https://www.alibaba.com/product-detail/Accept-Custom-50-Inch-4K-OLED_1601297150511.html', 131, 500, 2, NULL, 30, 0.14, '2 amostra de cada, da maior 3 amostra - familia - 2 familia oled e led - 35000 - a cada 8 meses', ''),
                ('TV 55 OLED', 'https://www.alibaba.com/product-detail/Manufacturer-Smart-TV-Televisor-55-Pouces_1601297505648.html', 153, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('TV 65 OLED', 'https://www.alibaba.com/product-detail/OLED-4K-8K-TV-65-Inch_1601297763869.html', 198, 500, 2, NULL, 30, 0.14, 'teste brasil', ''),
                ('bike eletrico 350w', 'https://www.alibaba.com/product-detail/2025-New-Model-500w-Powerful-Motor_1601126019396.html', 138, 500, 2, NULL, 30, 0.14, '', ''),
                ('patinete eletrico 350w', 'https://www.alibaba.com/product-detail/2025-Hot-Sale-350w-36v-10_1601018221130.html', 110, 500, 2, NULL, 30, 0.14, '', ''),
                ('processadores de alimento', 'https://www.alibaba.com/product-detail/4-in-1-Blender-for-Kitchen_1601205564037.html', 16.78, 500, 2, NULL, 30, 0.14, '', ''),
                ('purificador agua', 'https://www.alibaba.com/product-detail/Uf-Membrane-Water-Purifier-Home-Water_1601155236820.html', 79.9, 500, 2, NULL, 30, 0.14, '', ''),
                ('bebedouro agua quente e fria', 'https://www.alibaba.com/product-detail/New-Touch-Plane-Control-Bottom-Loading_1600207925942.html', 78, 500, 2, NULL, 30, 0.14, '', ''),
                ('bebedouro agua quente e fria mesa', 'https://www.alibaba.com/product-detail/Novel-Fashion-Glass-Computer-Display-Screen_1600055589576.html', 60, 500, 2, NULL, 30, 0.14, '', ''),
                ('cadeira', 'já tenho as referencias', NULL, 500, 2, 355, 30, 0.14, '', '');
        END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_index("ix_dnp_produtos_produto", table_name="dnp_produtos", schema=SCHEMA)
    op.drop_table("dnp_produtos", schema=SCHEMA)
    op.drop_table("dnp_config", schema=SCHEMA)
