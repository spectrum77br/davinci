# ruff: noqa: E501
"""financeiro module: consorcio + suprimentos + simulacao + ncm_cache

Cria as 4 tabelas que sustentam a nova seção Financeiro. Tudo no schema
davinci (search_path já vem setado).

  * financeiro_consorcio   — uma linha por cota de consórcio
  * financeiro_suprimentos — certificações (Anatel/Inmetro/isento)
  * financeiro_simulacao   — cotações de importação (1 mercadoria/linha)
  * ncm_cache              — cache de descrição+alíquotas por NCM, populado
                              sob demanda via brasilapi (descrição) e
                              edição manual do operador (alíquotas).

Seed inline (consorcio + suprimentos) só roda se a tabela está vazia —
re-rodar a migration depois de inserts manuais é seguro.

Revision ID: 0083_financeiro_tables
Revises: 0082_user_stock_tags_multi
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0083_financeiro_tables"
down_revision: str | None = "0082_user_stock_tags_multi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # ── consorcio ──────────────────────────────────────────────────────
    op.create_table(
        "financeiro_consorcio",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("credito", sa.Numeric(14, 2), nullable=True),
        sa.Column("emp", sa.Text(), nullable=True),
        sa.Column("grupo", sa.Integer(), nullable=True),
        sa.Column("cota", sa.Integer(), nullable=True),
        sa.Column("alienacao", sa.Text(), nullable=True),
        sa.Column("nf", sa.Text(), nullable=True),
        sa.Column("parc_a_pagar", sa.Integer(), nullable=True),
        sa.Column("lance", sa.Numeric(14, 2), nullable=True),
        sa.Column("valor_parc", sa.Numeric(14, 2), nullable=True),
        sa.Column("atualizado", sa.Date(), nullable=True),
        sa.Column("fundo_reserva", sa.Text(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "alienacao IS NULL OR alienacao IN ('pago', 'ag. repasse', 'a contemplar', '')",
            name="ck_financeiro_consorcio_alienacao",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_financeiro_consorcio_emp", "financeiro_consorcio", ["emp"], schema=SCHEMA)

    # ── suprimentos ────────────────────────────────────────────────────
    op.create_table(
        "financeiro_suprimentos",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("produto", sa.Text(), nullable=True),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column("nome_comercial", sa.Text(), nullable=True),
        sa.Column("certificado", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(14, 2), nullable=True),
        sa.Column("inicio", sa.Date(), nullable=True),
        sa.Column("fim", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "certificado IS NULL OR certificado IN ('anatel', 'inmetro', 'isento', '')",
            name="ck_financeiro_suprimentos_certificado",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_financeiro_suprimentos_fim", "financeiro_suprimentos", ["fim"], schema=SCHEMA)

    # ── simulacao ──────────────────────────────────────────────────────
    op.create_table(
        "financeiro_simulacao",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # Cabeçalho
        sa.Column("numero_cotacao", sa.Text(), nullable=True),
        sa.Column("cliente", sa.Text(), nullable=True),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("processo", sa.Text(), nullable=True),
        sa.Column("exportador", sa.Text(), nullable=True),
        sa.Column("pais_origem", sa.Text(), nullable=True),
        # Mercadoria
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("fornecedor", sa.Text(), nullable=True),
        sa.Column("quantidade", sa.Integer(), nullable=True),
        sa.Column("ncm", sa.Text(), nullable=True),
        sa.Column("descricao_ncm", sa.Text(), nullable=True),
        sa.Column("invoice_numero", sa.Text(), nullable=True),
        # Logística
        sa.Column("porto_origem", sa.Text(), nullable=True),
        sa.Column("porto_destino", sa.Text(), nullable=True),
        sa.Column("etd", sa.Date(), nullable=True),
        sa.Column("eta", sa.Date(), nullable=True),
        sa.Column("taxa_cambio", sa.Numeric(10, 4), nullable=True),
        # Custos USD (inputs)
        sa.Column("frete_seguro_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("valor_unitario_usd", sa.Numeric(14, 4), nullable=True),
        # Alíquotas vindas do NCM (snapshot — não puxa do cache toda hora)
        sa.Column("aliquota_ii", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_ipi", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_pis", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_cofins", sa.Numeric(7, 4), nullable=True),
        # Outras taxas USD
        sa.Column("taxa_siscomex_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("armazenagem_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("despachante_sda_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("despachante_honorarios_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("corretagem_cambio_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("inspecao_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("outras_taxas_usd", sa.Numeric(14, 2), nullable=True),
        # Alíquotas finais editáveis (defaults da planilha-mãe)
        sa.Column("aliquota_taxas_gerais", sa.Numeric(7, 4), server_default=sa.text("0.03"), nullable=True),
        sa.Column("aliquota_impostos_fed", sa.Numeric(7, 4), server_default=sa.text("0.035"), nullable=True),
        sa.Column("aliquota_icms", sa.Numeric(7, 4), server_default=sa.text("0.04"), nullable=True),
        sa.Column("frete_nacional_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("aliquota_intermediacao", sa.Numeric(7, 4), server_default=sa.text("0.16"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_financeiro_simulacao_numero", "financeiro_simulacao", ["numero_cotacao"], schema=SCHEMA)

    # ── ncm_cache ──────────────────────────────────────────────────────
    op.create_table(
        "ncm_cache",
        sa.Column("ncm", sa.Text(), primary_key=True),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("aliquota_ii", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_ipi", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_pis", sa.Numeric(7, 4), nullable=True),
        sa.Column("aliquota_cofins", sa.Numeric(7, 4), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )

    # ── seed (idempotente: só insere se a tabela está vazia) ───────────
    op.execute(f"""
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM "{SCHEMA}".financeiro_consorcio LIMIT 1) THEN
            INSERT INTO "{SCHEMA}".financeiro_consorcio
                (credito, emp, grupo, cota, alienacao, nf, parc_a_pagar, lance, valor_parc, atualizado, fundo_reserva, obs)
            VALUES
                (32343.87, 'LOCAGIL', 2122, 261, 'pago', '', 8, NULL, 934.16, '2025-12-21', '2.5%', ''),
                (28155.24, 'LOCAGIL', 2122, 5303, 'pago', 'JEEP RNF4F71', 8, NULL, 934.16, '2025-12-21', '2.5%', ''),
                (35507.75, 'LOCAGIL', 2122, 3994, 'pago', '', 8, NULL, 1208.58, '2025-12-21', '2.5%', ''),
                (30350.58, 'LOCAGIL', 2124, 6010, 'pago', '', 10, NULL, 1097.11, '2025-12-21', '2.5%', ''),
                (109625, 'Kia', 1524, 5153, 'pago', 'NF92 220', 75, 56075, 1130.56, '2026-02-20', '3%', ''),
                (109625, 'Kia', 1524, 5773, 'pago', 'NF92 220', 75, 56075, 1130.56, '2026-02-20', '3%', ''),
                (125472, 'Kia', 1513, 1245, 'ag. repasse', 'NF87 254', 73, 62925, 1406.09, '2026-02-20', '3%', ''),
                (125472, 'Kia', 1513, 6829, 'ag. repasse', 'NF87 254', 73, 62925, 1406.09, '2026-02-20', '3%', ''),
                (109806, 'Kia', 1553, 2992, 'a contemplar', '', 78, NULL, 1828.50, '2026-02-20', '3%', ''),
                (109806, 'Kia', 1553, 5481, 'a contemplar', '', 78, NULL, 1828.50, '2026-02-20', '3%', ''),
                (109662, 'Kia', 1579, 5200, 'a contemplar', '', 82, NULL, 1697, '2026-02-20', '3%', ''),
                (109662, 'Kia', 1579, 9691, 'a contemplar', '', 82, NULL, 1697, '2026-02-20', '3%', ''),
                (98968, 'Kia', 1651, 739, 'a contemplar', '', 91, NULL, 1380, '2026-02-20', '3%', ''),
                (42092, 'Kia', 1654, 5169, 'ag. repasse', 'NF88 254', 71, 20267.30, 765.50, '2026-02-20', '5%', ''),
                (42092, 'Kia', 1654, 8700, 'ag. repasse', 'NF88 254', 71, 20267.30, 765.50, '2026-02-20', '5%', ''),
                (115085, 'Kia', 1707, 5737, 'ag. repasse', 'NF88 254', 80, 58900, 1793.71, '2026-02-20', '3%', ''),
                (109625, 'Kia', 1524, NULL, '', '', 74, NULL, 1898, NULL, '', '');
        END IF;

        IF NOT EXISTS (SELECT 1 FROM "{SCHEMA}".financeiro_suprimentos LIMIT 1) THEN
            INSERT INTO "{SCHEMA}".financeiro_suprimentos
                (produto, modelo, nome_comercial, certificado, numero, valor, inicio, fim)
            VALUES
                ('Fone Bluetooth', 'U9 pro', 'UFB10', 'anatel', '06764-25-18234', 15000, '2025-11-07', '2027-11-07'),
                ('Smartwach', 'W69', 'USW10', 'anatel', '06763-25-18234', 15000, '2025-11-06', '2027-11-06'),
                ('Smartphone 5G', 'USM001', 'fossibot f105, fossibot f109, fossibot 109s, fossibot s7, fossibot s5, fossibot s3, oukitel wp36, oukitel wp52, oukitel wp53, oukitel c69, oukitel wp60', 'anatel', '08215-25-18234', 120000, '2026-02-20', '2028-02-20'),
                ('Smartphone 4G', 'USM002', 'fossibot f110, fossibot 110 pro, fossibot f117, hotwav 17 pro max, hotwav a36, oukitel g1, oukitel wp23plus, oukitel wp28e, oukitel wp28s, oukitel c68, oukitel c2, oukitel c3, oukitel g7, oukitel g6', 'anatel', '08216-25-18234', 60000, NULL, NULL),
                ('Smartphone 4G', 'USM003', 'A17, A25, hyper 7s, cyber 18, T8, wp68, C1, C17, C62, C65, C66, C72, F112, Flat 3C, marine, tiger, pilot, tank, rock, Bl7000', 'anatel', '', 53000, NULL, NULL),
                ('carregador', 'QZ-0180AE2H', '', 'anatel', '08217-25-18234', 10000, '2026-03-18', '2028-03-11'),
                ('bateria f109', 'f109', '', 'anatel', '08214-25-18234', 10000, '2026-01-19', '2026-07-18'),
                ('bateria f110', 'f110', '', 'anatel', '08201-25-18234', 10000, '2026-01-16', '2026-07-15'),
                ('bateria a17', '', '', 'anatel', '', 10000, NULL, NULL),
                ('airfryer', 'UAF001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('airfryer barbecue', 'UAF002', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('cofee machine', 'UCM001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('slushie maker', 'USL001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('ice cream maker', 'UIC001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('smart cooking', 'USC001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'inmetro', '', NULL, NULL, NULL),
                ('fone com fio', 'UFF001', 'M1, M2, M3, M4, M5, M6, M7, M8, M9, M10', 'isento', '', NULL, NULL, NULL);
        END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_table("ncm_cache", schema=SCHEMA)
    op.drop_index("ix_financeiro_simulacao_numero", table_name="financeiro_simulacao", schema=SCHEMA)
    op.drop_table("financeiro_simulacao", schema=SCHEMA)
    op.drop_index("ix_financeiro_suprimentos_fim", table_name="financeiro_suprimentos", schema=SCHEMA)
    op.drop_table("financeiro_suprimentos", schema=SCHEMA)
    op.drop_index("ix_financeiro_consorcio_emp", table_name="financeiro_consorcio", schema=SCHEMA)
    op.drop_table("financeiro_consorcio", schema=SCHEMA)
