"""seed import_products categoria=celular — 115 produtos pré-cadastrados

Revision ID: 0116_seed_celular_products
Revises: 0115_devolution_estoque_mov
Create Date: 2026-06-02

Operador entregou planilha com 115 produtos da linha celular (Apple,
Fossibot, Hotwav, Oscal, Oukitel, Tecno) pré-cadastrados pra alimentar
a aba Celular da página /importacao. Esta migration:

  1. Cria índice UNIQUE em (categoria, sku) — não existia ainda (zero
     duplicatas em prod), serve de base pro ON CONFLICT idempotente
     desta migration e de futuros seeds por categoria.
  2. UPSERT dos 115 produtos com fornecedor + modelo_bling + sku. Demais
     campos (custo_bling=0, estoque/consumo/cor=NULL) ficam no default
     — operador preenche depois.

Idempotente: rodar 2x não duplica, só atualiza fornecedor/modelo_bling
das rows existentes (cobre o caso de re-import com correção de nome).

Downgrade: deleta SÓ as 115 rows desta seed (categoria='celular' AND
sku IN (lista)), preservando produtos celular criados manualmente
pela UI depois. Dropa o índice UNIQUE também.
"""

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "0116_seed_celular_products"
down_revision = "0115_devolution_estoque_mov"
branch_labels = None
depends_on = None

SCHEMA = "davinci"
_UNIQUE_INDEX = "uq_import_products_categoria_sku"

# (sku, fornecedor, modelo_bling). 115 produtos. Fonte: Excel
# "Importação > Celular" enviado pelo operador 2026-06-02. Único
# fornecedor é "uranyx". Modelos contém cor embutida ("…- Azul"),
# então `cor` fica NULL (consistente com o que o operador editou no
# Excel — coluna `cor` em branco pra todos).
_PRODUTOS: list[tuple[str, str, str]] = [
    ("i205.sa", "uranyx", "Apple Ipad 11 128 GB - Amarelo"),
    ("i203.sa", "uranyx", "Apple Ipad 11 128 GB - Azul"),
    ("i206.sa", "uranyx", "Apple Ipad 11 128 GB - Prata"),
    ("i204.sa", "uranyx", "Apple Ipad 11 128 GB - Rosa"),
    ("i217.sa", "uranyx", "Apple iPhone 17 Pro 256 GB - Azul"),
    ("i216.sa", "uranyx", "Apple iPhone 17 Pro 256 GB - Laranja"),
    ("i215.sa", "uranyx", "Apple iPhone 17 Pro 256 GB - Prata"),
    ("i220.sa", "uranyx", "Apple iPhone 17 Pro Max 256 GB - Azul"),
    ("i219.sa", "uranyx", "Apple iPhone 17 Pro Max 256 GB - Laranja"),
    ("i218.sa", "uranyx", "Apple iPhone 17 Pro Max 256 GB - Prata"),
    ("i236.sa", "uranyx", "Apple iPhone Air 256 GB - Azul"),
    ("i234.sa", "uranyx", "Apple iPhone Air 256 GB - Branco"),
    ("i237.sa", "uranyx", "Apple iPhone Air 256 GB - Dourado"),
    ("i235.sa", "uranyx", "Apple iPhone Air 256 GB - Preto"),
    ("i213.sa", "uranyx", "Apple MacBook Air M4 16.256 - Azul"),
    ("i212.sa", "uranyx", "Apple MacBook Air M4 16.256 - Cinza"),
    ("i210.sa", "uranyx", "Apple MacBook Air M4 16.256 - Dourado"),
    ("i211.sa", "uranyx", "Apple MacBook Air M4 16.256 - Prata"),
    ("i229.sa", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Azul'),
    ("i228.sp", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Cinza'),
    ("i228.sa", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Cinza'),
    ("i226.sp", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Dourado'),
    ("i226.sa", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Dourado'),
    ("i227.sa", "uranyx", 'Apple Macbook Air M5 16.512 13,6" - Prata'),
    ("i222.sa", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Amarelo'),
    ("i225.sp", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Azul'),
    ("i225.sa", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Azul'),
    ("i224.sp", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Prata'),
    ("i224.sa", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Prata'),
    ("i223.sa", "uranyx", 'Apple Macbook Neo 8.256 13,6" - Rosa'),
    ("i221.sa", "uranyx", "Apple MacMini M2 8.256 - Prata"),
    ("i214.sa", "uranyx", "Apple MacMini M4 16.256 - Prata"),
    ("i238.sa", "uranyx", "Apple Watch S11 42mm - Cinza"),
    ("i239.sa", "uranyx", "Apple Watch S11 42mm - Prata"),
    ("i240.sa", "uranyx", "Apple Watch S11 42mm - Preto"),
    ("i241.sa", "uranyx", "Apple Watch S11 42mm - Rosa"),
    ("i242.sa", "uranyx", "Apple Watch S11 46mm - Cinza"),
    ("i243.sa", "uranyx", "Apple Watch S11 46mm - Prata"),
    ("i244.sa", "uranyx", "Apple Watch S11 46mm - Preto"),
    ("i245.sa", "uranyx", "Apple Watch S11 46mm - Rosa"),
    ("i002.sa", "uranyx", "Apple Watch SE 2 GPS 40mm - Branco"),
    ("i003.sa", "uranyx", "Apple Watch SE 2 GPS 40mm - Prata"),
    ("i001.sa", "uranyx", "Apple Watch SE 2 GPS 40mm - Preto"),
    ("i004.sa", "uranyx", "Apple Watch SE 2 GPS 40mm - Verde Tecido"),
    ("i201.sa", "uranyx", "Apple Watch SE 2 GPS 44mm - Branco"),
    ("i202.sa", "uranyx", "Apple Watch SE 2 GPS 44mm - Prata"),
    ("i200.sa", "uranyx", "Apple Watch SE 2 GPS 44mm - Preto"),
    ("i246.sa", "uranyx", "Apple Watch SE 2 GPS 44mm - Preto (tecido)"),
    ("i005.sa", "uranyx", "Apple Watch SE 2 GPS 44mm - Verde Tecido"),
    ("i231.sa", "uranyx", "Apple Watch SE 3 GPS 40mm - Branco"),
    ("i230.sa", "uranyx", "Apple Watch SE 3 GPS 40mm - Preto"),
    ("i233.sa", "uranyx", "Apple Watch SE 3 GPS 44mm - Branco"),
    ("i232.sa", "uranyx", "Apple Watch SE 3 GPS 44mm - Preto"),
    ("dg019.ra", "uranyx", "Fossibot F105 12.64 - Preto"),
    ("dg018.pi", "uranyx", "Fossibot F109 5G 24.256 - Preto"),
    ("dg017.pi", "uranyx", "Fossibot F109S 24.256 - Preto"),
    ("dg047.pi", "uranyx", "Fossibot F110 Pro 5G 20.128 - Preto"),
    ("dg046.pi", "uranyx", "Fossibot F110L 8.128 - Preto"),
    ("dg083.ra", "uranyx", "Fossibot F112 Pro 5G 24.256 - Azul"),
    ("dg085.ra", "uranyx", "Fossibot F112 Pro 5G 24.256 - Laranja"),
    ("dg086.ra", "uranyx", "Fossibot F112 Pro 5G 24.256 - Preto"),
    ("dg084.ra", "uranyx", "Fossibot F112 Pro 5G 24.256 - Verde"),
    ("dg082.ra", "uranyx", "Fossibot F112 Pro 5G 24.256 - Vermelho"),
    ("dg048.ra", "uranyx", "Fossibot F117 24.256 - Preto"),
    ("dg088.pi", "uranyx", "Fossibot S5 8.128 - Laranja"),
    ("dg089.pi", "uranyx", "Fossibot S5 8.128 - Prata"),
    ("dg009.pi", "uranyx", "Fossibot S7 12.256 - Cinza"),
    ("dg008.pi", "uranyx", "Fossibot S7 12.256 - Dourado"),
    ("dg007.pi", "uranyx", "Fossibot S7 12.256 - Preto"),
    ("dg004.pi", "uranyx", "Fossibot S7 8.128 - Cinza"),
    ("dg003.pi", "uranyx", "Fossibot S7 8.128 - Dourado"),
    ("dg002.pi", "uranyx", "Fossibot S7 8.128 - Preto"),
    ("dg054.ci", "uranyx", "Hotwav A17 Pro Max 12.128 - Branco"),
    ("dg053.ci", "uranyx", "Hotwav A17 Pro Max 12.128 - Laranja"),
    ("dg052.ci", "uranyx", "Hotwav A17 Pro Max 12.128 - Preto"),
    ("dg057.ci", "uranyx", "Hotwav A17 Pro Max 12.64 - Branco"),
    ("dg056.ci", "uranyx", "Hotwav A17 Pro Max 12.64 - Laranja"),
    ("dg055.ci", "uranyx", "Hotwav A17 Pro Max 12.64 - Preto"),
    ("dg087.pi", "uranyx", "Hotwav Hyper 7s 16.256 - Preto"),
    ("dg095.ra", "uranyx", "Oscal Flat 3C 16.128 - Azul"),
    ("dg096.ra", "uranyx", "Oscal Flat 3C 16.128 - Laranja"),
    ("dg094.ra", "uranyx", "Oscal Flat 3C 16.128 - Preto"),
    ("dg093.ra", "uranyx", "Oscal Marine 1 12.128 - Preto"),
    ("dg075.pi", "uranyx", "Oukitel C2 16.128 - Azul"),
    ("dg076.pi", "uranyx", "Oukitel C2 16.128 - Dourado"),
    ("dg074.pi", "uranyx", "Oukitel C2 16.128 - Preto"),
    ("dg077.pi", "uranyx", "Oukitel C2 16.128 - Roxo"),
    ("dg079.pi", "uranyx", "Oukitel C3 16.128 - Azul"),
    ("dg080.pi", "uranyx", "Oukitel C3 16.128 - Dourado"),
    ("dg078.pi", "uranyx", "Oukitel C3 16.128 - Preto"),
    ("dg081.pi", "uranyx", "Oukitel C3 16.128 - Roxo"),
    ("dg062.pi", "uranyx", "Oukitel C68 Plus 16.128 - Azul"),
    ("dg060.pi", "uranyx", "Oukitel C68 Plus 16.128 - cinza"),
    ("dg061.pi", "uranyx", "Oukitel C68 Plus 16.128 - Dourado"),
    ("dg063.pi", "uranyx", "Oukitel C68 Plus 16.128 - Rosa"),
    ("dg066.pi", "uranyx", "Oukitel C68 Plus 24.256 - Azul"),
    ("dg064.pi", "uranyx", "Oukitel C68 Plus 24.256 - cinza"),
    ("dg065.pi", "uranyx", "Oukitel C68 Plus 24.256 - Dourado"),
    ("dg067.pi", "uranyx", "Oukitel C68 Plus 24.256 - Rosa"),
    ("dg011.pi", "uranyx", "Oukitel G1 24.256 - Preto"),
    ("dg069.pi", "uranyx", "Oukitel G1s 8.128 - Preto"),
    ("dg020.pi", "uranyx", "Oukitel WP28E 16.64 - Preto"),
    ("dg015.pi", "uranyx", "Oukitel WP36 16.128 - Preto"),
    ("dg022.pi", "uranyx", "Oukitel WP52 5G 16.256 - Preto"),
    ("dg023.ra", "uranyx", "Oukitel WP53 24.128 - Preto"),
    ("dg072.pi", "uranyx", "Oukitel WP60 24.256 - Amarelo"),
    ("dg073.pi", "uranyx", "Oukitel WP60 24.256 - Branco"),
    ("dg010.pi", "uranyx", "Oukitel WP60 24.256 - Preto"),
    ("dg090.pi", "uranyx", "Oukitel WP60 48.512 - Amarelo"),
    ("dg092.pi", "uranyx", "Oukitel WP60 48.512 - Branco"),
    ("dg091.pi", "uranyx", "Oukitel WP60 48.512 - Preto"),
    ("dg024.ra", "uranyx", "Tecno Spark go 1 6.64 - Branco"),
    ("dg030.ra", "uranyx", "Tecno Spark go 1 6.64 - Dourado"),
    ("dg025.ra", "uranyx", "Tecno Spark go 1 6.64 - Preto"),
    ("dg026.ra", "uranyx", "Tecno Spark go 1 6.64 - Verde"),
]


def upgrade() -> None:
    # 1) Índice UNIQUE pra suportar ON CONFLICT (categoria, sku) tanto
    #    aqui quanto em futuros seeds. Zero duplicatas em prod (mala=354,
    #    eletro=12, todos com (cat,sku) distintos) — criação é seguro.
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_INDEX} '
        f'ON {SCHEMA}.import_products (categoria, sku)'
    )

    # 2) UPSERT dos 115 produtos. Demais colunas ficam no default da
    #    tabela (custo_bling=0, restantes NULL).
    conn = op.get_bind()
    stmt = text(
        f"""
        INSERT INTO {SCHEMA}.import_products
            (categoria, sku, fornecedor, modelo_bling)
        VALUES
            ('celular', :sku, :fornecedor, :modelo_bling)
        ON CONFLICT (categoria, sku) DO UPDATE SET
            fornecedor = EXCLUDED.fornecedor,
            modelo_bling = EXCLUDED.modelo_bling
        """  # noqa: S608
    )
    for sku, fornecedor, modelo_bling in _PRODUTOS:
        conn.execute(stmt, {
            "sku": sku, "fornecedor": fornecedor, "modelo_bling": modelo_bling,
        })


def downgrade() -> None:
    # Remove SÓ as rows desta seed — preserva celular criado manualmente
    # depois (não toca em mala/eletro).
    conn = op.get_bind()
    skus = [p[0] for p in _PRODUTOS]
    sql = f"DELETE FROM {SCHEMA}.import_products WHERE categoria = 'celular' AND sku = ANY(:skus)"  # noqa: E501, S608
    conn.execute(text(sql), {"skus": skus})
    op.execute(f'DROP INDEX IF EXISTS {SCHEMA}.{_UNIQUE_INDEX}')
