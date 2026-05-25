# ruff: noqa: E501
"""seed cotação COT-2026-0005 (POOFY / Cooking Robot 320un / Tianjin→Santos)

Idempotente: só insere se a numero_cotacao ainda não existe na tabela.

Revision ID: 0084_financeiro_simulacao_seed
Revises: 0083_financeiro_tables
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0084_financeiro_simulacao_seed"
down_revision: str | None = "0083_financeiro_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # Cotação modelo que o spec da página de Simulação usa como exemplo.
    # Os valores em USD são inputs do operador; as alíquotas refletem
    # exatamente o que o usuário pediu (II 18%, IPI 6.5%, PIS 0%,
    # COFINS 9.65% para o NCM 85094010 — cooking robot/liquidificador).
    op.execute(f"""
        INSERT INTO "{SCHEMA}".financeiro_simulacao
            (numero_cotacao, cliente, data, processo, exportador, pais_origem,
             descricao, fornecedor, quantidade, ncm, descricao_ncm, invoice_numero,
             porto_origem, porto_destino, etd, eta, taxa_cambio,
             frete_seguro_usd, valor_unitario_usd,
             aliquota_ii, aliquota_ipi, aliquota_pis, aliquota_cofins,
             taxa_siscomex_usd, armazenagem_usd,
             despachante_sda_usd, despachante_honorarios_usd, corretagem_cambio_usd, inspecao_usd,
             outras_taxas_usd, frete_nacional_usd,
             aliquota_taxas_gerais, aliquota_impostos_fed, aliquota_icms, aliquota_intermediacao)
        SELECT
            'COT-2026-0005', 'POOFY', '2026-05-11', 'CRO1', 'URANYX INDUSTRIES LIMITED', 'HONG KONG',
            'COOKING ROBOT', 'URANYX INDUSTRIES LIMITED', 320, '85094010',
            'Capítulo 85 * Posição 8509 * Subposição 850940 * NCM 8509.40.10 Liquidificadores Vigência desde: 2022-04-01',
            NULL,
            'TIANJIN-CHINA', 'SANTOS-BRASIL', '2026-07-13', '2026-08-25', 4.899,
            4000.00, 199.00,
            0.18, 0.065, 0.0, 0.0965,
            31.48, 0.00,
            102.06, NULL, 81.65, 200.00,
            0.00, 1200.00,
            0.03, 0.035, 0.04, 0.16
        WHERE NOT EXISTS (
            SELECT 1 FROM "{SCHEMA}".financeiro_simulacao
            WHERE numero_cotacao = 'COT-2026-0005'
        );
    """)

    # Também cacheia o NCM 85094010 com a descrição e as alíquotas usadas
    # acima — assim a próxima cotação que digitar esse NCM já vem com tudo.
    op.execute(f"""
        INSERT INTO "{SCHEMA}".ncm_cache
            (ncm, descricao, aliquota_ii, aliquota_ipi, aliquota_pis, aliquota_cofins)
        VALUES
            ('85094010',
             'Capítulo 85 * Posição 8509 * Subposição 850940 * NCM 8509.40.10 Liquidificadores Vigência desde: 2022-04-01',
             0.18, 0.065, 0.0, 0.0965)
        ON CONFLICT (ncm) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute(f"DELETE FROM \"{SCHEMA}\".financeiro_simulacao WHERE numero_cotacao = 'COT-2026-0005'")
    op.execute(f"DELETE FROM \"{SCHEMA}\".ncm_cache WHERE ncm = '85094010'")
