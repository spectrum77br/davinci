"""seed celular: valor_brl_realizado / valor_usd / frete_type por produto

Revision ID: 0124_seed_celular_cotacao_values
Revises: 0123_drop_custo_realizado
Create Date: 2026-06-02

Popula as 3 colunas da aba Cotação do Celular (etapa 3, migration 0119)
a partir do Excel operacional. UPDATE WHERE categoria='celular' AND
modelo_bling = X — aplica em todos os SKUs com aquele modelo (alguns
modelos têm 2 SKUs no DB, ex: i228.sp + i228.sa = "Macbook Air M5 Cinza").

Idempotente: re-rodar sobrescreve com os mesmos valores do JSON (não
duplica). Operador pode editar pela UI depois; próximo upgrade não
trava (mantém estes valores como baseline).

Downgrade: zera os 3 campos APENAS dos modelos listados aqui — não
afeta produtos celular que receberam valor manual em outros caminhos.
"""

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "0124_seed_celular_cotacao_values"
down_revision = "0123_drop_custo_realizado"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

# (modelo_bling, valor_brl_realizado, valor_usd, frete_type). Fonte:
# /tmp/cotacao_seed_celular.json (extraído do Excel operacional
# 2026-06-02). Modelos únicos — UPDATE atinge todas as rows com
# aquele modelo, então duplicatas no JSON foram colapsadas.
_VALUES: list[tuple[str, float, float, str]] = [
    ("Apple Ipad 11 128 GB - Amarelo", 2000.0, 340.0, "regular"),
    ("Apple Ipad 11 128 GB - Azul", 2000.0, 340.0, "regular"),
    ("Apple Ipad 11 128 GB - Prata", 2000.0, 340.0, "regular"),
    ("Apple Ipad 11 128 GB - Rosa", 2000.0, 340.0, "regular"),
    ("Apple iPhone 17 Pro 256 GB - Azul", 6810.0, 1265.0, "swap"),
    ("Apple iPhone 17 Pro 256 GB - Laranja", 6810.0, 1265.0, "swap"),
    ("Apple iPhone 17 Pro 256 GB - Prata", 6810.0, 1265.0, "swap"),
    ("Apple iPhone 17 Pro Max 256 GB - Azul", 7650.0, 1350.0, "swap"),
    ("Apple iPhone 17 Pro Max 256 GB - Laranja", 7650.0, 1350.0, "swap"),
    ("Apple iPhone 17 Pro Max 256 GB - Prata", 7650.0, 1350.0, "swap"),
    ("Apple iPhone Air 256 GB - Azul", 4900.0, 920.0, "swap"),
    ("Apple iPhone Air 256 GB - Branco", 4900.0, 920.0, "swap"),
    ("Apple iPhone Air 256 GB - Dourado", 4900.0, 920.0, "swap"),
    ("Apple iPhone Air 256 GB - Preto", 4900.0, 920.0, "swap"),
    ("Apple MacBook Air M4 16.256 - Azul", 5900.0, 1025.0, "regular"),
    ("Apple MacBook Air M4 16.256 - Cinza", 5900.0, 1025.0, "regular"),
    ("Apple MacBook Air M4 16.256 - Dourado", 5900.0, 1025.0, "regular"),
    ("Apple MacBook Air M4 16.256 - Prata", 5900.0, 1025.0, "regular"),
    ('Apple Macbook Air M5 16.512 13,6" - Azul', 6400.0, 1100.0, "regular"),
    ('Apple Macbook Air M5 16.512 13,6" - Cinza', 6400.0, 1100.0, "regular"),
    ('Apple Macbook Air M5 16.512 13,6" - Dourado', 6400.0, 1100.0, "regular"),
    ('Apple Macbook Air M5 16.512 13,6" - Prata', 6400.0, 1100.0, "regular"),
    ('Apple Macbook Neo 8.256 13,6" - Amarelo', 3910.0, 665.0, "regular"),
    ('Apple Macbook Neo 8.256 13,6" - Azul', 3910.0, 665.0, "regular"),
    ('Apple Macbook Neo 8.256 13,6" - Prata', 3910.0, 665.0, "regular"),
    ('Apple Macbook Neo 8.256 13,6" - Rosa', 3910.0, 665.0, "regular"),
    ("Apple MacMini M2 8.256 - Prata", 2800.0, 475.0, "regular"),
    ("Apple MacMini M4 16.256 - Prata", 3580.0, 575.0, "regular"),
    ("Apple Watch S11 42mm - Cinza", 1980.0, 340.0, "regular"),
    ("Apple Watch S11 42mm - Prata", 1980.0, 340.0, "regular"),
    ("Apple Watch S11 42mm - Preto", 1980.0, 340.0, "regular"),
    ("Apple Watch S11 42mm - Rosa", 1980.0, 340.0, "regular"),
    ("Apple Watch S11 46mm - Cinza", 2130.0, 365.0, "regular"),
    ("Apple Watch S11 46mm - Prata", 2130.0, 365.0, "regular"),
    ("Apple Watch S11 46mm - Preto", 2130.0, 365.0, "regular"),
    ("Apple Watch S11 46mm - Rosa", 2130.0, 365.0, "regular"),
    ("Apple Watch SE 2 GPS 40mm - Branco", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 40mm - Prata", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 40mm - Preto", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 40mm - Verde Tecido", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 44mm - Branco", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 44mm - Prata", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 44mm - Preto", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 44mm - Preto (tecido)", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 2 GPS 44mm - Verde Tecido", 1280.0, 210.0, "regular"),
    ("Apple Watch SE 3 GPS 40mm - Branco", 1560.0, 265.0, "regular"),
    ("Apple Watch SE 3 GPS 40mm - Preto", 1560.0, 265.0, "regular"),
    ("Apple Watch SE 3 GPS 44mm - Branco", 1700.0, 290.0, "regular"),
    ("Apple Watch SE 3 GPS 44mm - Preto", 1700.0, 290.0, "regular"),
    ("Fossibot F105 12.64 - Preto", 670.0, 106.0, "regular"),
    ("Fossibot F109 5G 24.256 - Preto", 1350.0, 203.0, "regular"),
    ("Fossibot F109S 24.256 - Preto", 1050.0, 161.0, "regular"),
    ("Fossibot F110 Pro 5G 20.128 - Preto", 1150.0, 175.0, "regular"),
    ("Fossibot F110L 8.128 - Preto", 800.0, 119.0, "regular"),
    ("Fossibot F112 Pro 5G 24.256 - Azul", 1150.0, 182.0, "regular"),
    ("Fossibot F112 Pro 5G 24.256 - Laranja", 1150.0, 182.0, "regular"),
    ("Fossibot F112 Pro 5G 24.256 - Preto", 1150.0, 182.0, "regular"),
    ("Fossibot F112 Pro 5G 24.256 - Verde", 1150.0, 182.0, "regular"),
    ("Fossibot F112 Pro 5G 24.256 - Vermelho", 1150.0, 182.0, "regular"),
    ("Fossibot F117 24.256 - Preto", 1150.0, 182.0, "regular"),
    ("Fossibot S5 8.128 - Laranja", 900.0, 102.0, "regular"),
    ("Fossibot S5 8.128 - Prata", 900.0, 102.0, "regular"),
    ("Fossibot S7 12.256 - Cinza", 900.0, 130.0, "regular"),
    ("Fossibot S7 12.256 - Dourado", 900.0, 130.0, "regular"),
    ("Fossibot S7 12.256 - Preto", 900.0, 130.0, "regular"),
    ("Fossibot S7 8.128 - Cinza", 645.0, 109.0, "regular"),
    ("Fossibot S7 8.128 - Dourado", 645.0, 109.0, "regular"),
    ("Fossibot S7 8.128 - Preto", 645.0, 109.0, "regular"),
    ("Hotwav A17 Pro Max 12.128 - Branco", 540.0, 91.0, "regular"),
    ("Hotwav A17 Pro Max 12.128 - Laranja", 540.0, 91.0, "regular"),
    ("Hotwav A17 Pro Max 12.128 - Preto", 540.0, 91.0, "regular"),
    ("Hotwav A17 Pro Max 12.64 - Branco", 440.0, 78.0, "regular"),
    ("Hotwav A17 Pro Max 12.64 - Laranja", 440.0, 78.0, "regular"),
    ("Hotwav A17 Pro Max 12.64 - Preto", 440.0, 78.0, "regular"),
    ("Hotwav Hyper 7s 16.256 - Preto", 1180.0, 182.0, "regular"),
    ("Oscal Flat 3C 16.128 - Azul", 580.0, 86.4, "regular"),
    ("Oscal Flat 3C 16.128 - Laranja", 580.0, 86.4, "regular"),
    ("Oscal Flat 3C 16.128 - Preto", 580.0, 86.4, "regular"),
    ("Oscal Marine 1 12.128 - Preto", 680.0, 102.6, "regular"),
    ("Oukitel C2 16.128 - Azul", 480.0, 78.0, "regular"),
    ("Oukitel C2 16.128 - Dourado", 480.0, 78.0, "regular"),
    ("Oukitel C2 16.128 - Preto", 480.0, 78.0, "regular"),
    ("Oukitel C2 16.128 - Roxo", 480.0, 78.0, "regular"),
    ("Oukitel C3 16.128 - Azul", 480.0, 78.0, "regular"),
    ("Oukitel C3 16.128 - Dourado", 480.0, 78.0, "regular"),
    ("Oukitel C3 16.128 - Preto", 480.0, 78.0, "regular"),
    ("Oukitel C3 16.128 - Roxo", 480.0, 78.0, "regular"),
    ("Oukitel C68 Plus 16.128 - Azul", 700.0, 115.0, "regular"),
    ("Oukitel C68 Plus 16.128 - cinza", 700.0, 115.0, "regular"),
    ("Oukitel C68 Plus 16.128 - Dourado", 700.0, 115.0, "regular"),
    ("Oukitel C68 Plus 16.128 - Rosa", 700.0, 115.0, "regular"),
    ("Oukitel C68 Plus 24.256 - Azul", 900.0, 146.0, "regular"),
    ("Oukitel C68 Plus 24.256 - cinza", 900.0, 146.0, "regular"),
    ("Oukitel C68 Plus 24.256 - Dourado", 900.0, 146.0, "regular"),
    ("Oukitel C68 Plus 24.256 - Rosa", 900.0, 146.0, "regular"),
    ("Oukitel G1 24.256 - Preto", 920.0, 156.8, "regular"),
    ("Oukitel G1s 8.128 - Preto", 780.0, 134.4, "regular"),
    ("Oukitel WP28E 16.64 - Preto", 760.0, 113.0, "regular"),
    ("Oukitel WP36 16.128 - Preto", 900.0, 150.0, "regular"),
    ("Oukitel WP52 5G 16.256 - Preto", 1000.0, 175.0, "regular"),
    ("Oukitel WP53 24.128 - Preto", 940.0, 149.8, "regular"),
    ("Oukitel WP60 24.256 - Amarelo", 1600.0, 255.0, "regular"),
    ("Oukitel WP60 24.256 - Branco", 1600.0, 255.0, "regular"),
    ("Oukitel WP60 24.256 - Preto", 1600.0, 255.0, "regular"),
    ("Oukitel WP60 48.512 - Amarelo", 1700.0, 280.0, "regular"),
    ("Oukitel WP60 48.512 - Branco", 1700.0, 280.0, "regular"),
    ("Oukitel WP60 48.512 - Preto", 1700.0, 280.0, "regular"),
    ("Tecno Spark go 1 6.64 - Branco", 460.0, 70.0, "regular"),
    ("Tecno Spark go 1 6.64 - Dourado", 460.0, 70.0, "regular"),
    ("Tecno Spark go 1 6.64 - Preto", 460.0, 70.0, "regular"),
    ("Tecno Spark go 1 6.64 - Verde", 460.0, 70.0, "regular"),
]


def upgrade() -> None:
    conn = op.get_bind()
    stmt = text(
        f"""
        UPDATE {SCHEMA}.import_products
        SET valor_brl_realizado = :brl,
            valor_usd = :usd,
            frete_type = :frete
        WHERE categoria = 'celular' AND modelo_bling = :modelo
        """  # noqa: S608
    )
    for modelo, brl, usd, frete in _VALUES:
        conn.execute(stmt, {
            "modelo": modelo, "brl": brl, "usd": usd, "frete": frete,
        })


def downgrade() -> None:
    conn = op.get_bind()
    # Zera os 3 campos APENAS pros modelos populados aqui. Reverte ao
    # estado pre-0124 (campos null/default). Não toca em registros
    # celular que possam ter recebido valor por outro caminho.
    stmt = text(
        f"""
        UPDATE {SCHEMA}.import_products
        SET valor_brl_realizado = NULL,
            valor_usd = NULL,
            frete_type = 'regular'
        WHERE categoria = 'celular' AND modelo_bling = :modelo
        """  # noqa: S608
    )
    for modelo, _brl, _usd, _frete in _VALUES:
        conn.execute(stmt, {"modelo": modelo})
