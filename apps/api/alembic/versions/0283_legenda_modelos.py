"""marketing_legenda_modelos — legenda automática do robô de postagem

Eduardo, 16/09/2026: "com base no produto do criativo e se não, cai num
padrão da marca… também tem que sair quando o post sai automático, pois a
ideia futuramente é deixar todos postando automático".

Hoje o modal pré-preenche a legenda com o `roteiro`, que é prompt de geração
do vídeo em inglês, e dos dois Reels já publicados um saiu SEM legenda —
sendo que a legenda é o único texto que a busca do Instagram e o Google leem
daquele vídeo. Esta migration monta o banco da cascata
`legenda da postagem → legenda do criativo → modelo do PRODUTO → modelo da
MARCA → recusa`:

  • `marketing_legenda_modelos`: a biblioteca. Uma linha = uma variação
    (Jinja). SEM unicidade por (marca, produto) de propósito: várias
    variações da mesma chave é o ponto — é delas que sai o rodízio que
    impede o mesmo texto de repetir na conta.
  • `marketing_creatives.product_id` + BACKFILL por `product_links.
    external_sku`: medido em produção, essa corrente casa 39 dos 41
    criativos (por `products.sku` direto casam ZERO). É o que decide se o
    criativo pega o modelo do produto ou o padrão da marca.
  • `marketing_creatives.legenda`: override por vídeo, o degrau mais alto
    da cascata depois da própria postagem.
  • `marketing_postagens.legenda_modelo_id`: qual variação saiu. É o estado
    do rodízio ("a que faz mais tempo que não sai nesta conta"), derivado —
    sem tabela de contador.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0283_legenda_modelos"
down_revision: str | None = "0282_robo_comando_sem_logistica"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "marketing_legenda_modelos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marcas.id", ondelete="CASCADE",
                name="fk_marketing_legenda_modelos_marca_id_marcas",
            ),
            nullable=False,
        ),
        # NULL = padrão da marca; preenchido = legenda daquele produto.
        # SET NULL: apagar o produto não pode levar junto um texto escrito à
        # mão — a variação sobrevive rebaixada a padrão da marca.
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.products.id", ondelete="SET NULL",
                name="fk_marketing_legenda_modelos_product_id_products",
            ),
            nullable=True,
        ),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.users.id", ondelete="SET NULL",
                name="fk_marketing_legenda_modelos_created_by_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )
    # Os dois índices da busca da cascata — também declarados no model
    # (create_all dos testes), como manda o precedente da 0277/0281.
    op.create_index(
        "ix_marketing_legenda_modelos_marca_id_product_id",
        "marketing_legenda_modelos",
        ["marca_id", "product_id"],
        schema=SCHEMA,
    )
    # Parcial pro degrau da MARCA, que é o caminho comum (quase todo criativo
    # cai nele) e é varrido inteiro a cada postagem pelo rodízio.
    op.create_index(
        "ix_marketing_legenda_modelos_marca_padrao",
        "marketing_legenda_modelos",
        ["marca_id"],
        postgresql_where=sa.text("product_id IS NULL"),
        schema=SCHEMA,
    )

    # Criativo → produto (de onde sai o modelo de legenda do produto).
    op.add_column(
        "marketing_creatives",
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.products.id", ondelete="SET NULL",
                name="fk_marketing_creatives_product_id_products",
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_creatives_product_id", "marketing_creatives", ["product_id"], schema=SCHEMA
    )
    # Backfill: o SKU do criativo casa com `product_links.external_sku` pelo
    # texto inteiro OU pela base — o sufixo (".ra", ".pi", ".ci") é variante
    # de cor e o anúncio do marketplace costuma estar cadastrado na base.
    # DISTINCT ON + ORDER BY pl.product_id: o mesmo SKU aparece em VÁRIOS
    # links (um por anúncio/marketplace) e nada garante que todos apontem
    # pro mesmo produto; sem desempate, o backfill daria resultado diferente
    # a cada execução. Fica o menor id — arbitrário, mas igual em dev, no
    # dump e em produção, que é o que faz o teste valer. O que não casar fica
    # NULL e a cascata cai no padrão da marca (a tela mostra, não trava).
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.marketing_creatives c
               SET product_id = escolhido.product_id
              FROM (
                    SELECT DISTINCT ON (cr.id)
                           cr.id AS creative_id,
                           pl.product_id
                      FROM {SCHEMA}.marketing_creatives cr
                      JOIN {SCHEMA}.product_links pl
                        ON lower(btrim(pl.external_sku)) IN (
                               lower(btrim(cr.sku)),
                               lower(split_part(btrim(cr.sku), '.', 1))
                           )
                     WHERE cr.product_id IS NULL
                       AND btrim(coalesce(cr.sku, '')) <> ''
                       AND btrim(coalesce(pl.external_sku, '')) <> ''
                     ORDER BY cr.id,
                              -- SKU EXATO ganha do SKU base. `dg017.pi` é a
                              -- variante rosa; `dg017` é a linha. Sem este
                              -- critério o desempate cai no menor uuid e o
                              -- criativo da variante pode herdar o produto da
                              -- linha — e aí `{{ produto }}` publica o nome
                              -- errado no Instagram, que não se edita.
                              (lower(btrim(pl.external_sku))
                               <> lower(btrim(cr.sku))) ASC,
                              pl.product_id
                   ) escolhido
             WHERE c.id = escolhido.creative_id
            """  # noqa: S608 — SCHEMA é constante do módulo, não entrada
        )
    )

    # Override de legenda por vídeo (o modal passa a editar ISSO, não o roteiro).
    op.add_column(
        "marketing_creatives", sa.Column("legenda", sa.Text(), nullable=True), schema=SCHEMA
    )

    # Qual variação saiu nesta postagem: estado do rodízio, SET NULL pra
    # apagar a variação não apagar o histórico do que já foi publicado.
    # Nome de FK curto na mão porque a convenção do repo passaria de 63
    # caracteres e o Postgres truncaria calado (o model usa o mesmo nome).
    op.add_column(
        "marketing_postagens",
        sa.Column(
            "legenda_modelo_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marketing_legenda_modelos.id", ondelete="SET NULL",
                name="fk_marketing_postagens_legenda_modelo_id_legenda_modelos",
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # A FK de marketing_postagens sai ANTES da tabela que ela referencia.
    op.drop_column("marketing_postagens", "legenda_modelo_id", schema=SCHEMA)
    op.drop_column("marketing_creatives", "legenda", schema=SCHEMA)
    op.drop_index(
        "ix_marketing_creatives_product_id", table_name="marketing_creatives", schema=SCHEMA
    )
    op.drop_column("marketing_creatives", "product_id", schema=SCHEMA)
    for idx in (
        "ix_marketing_legenda_modelos_marca_padrao",
        "ix_marketing_legenda_modelos_marca_id_product_id",
    ):
        op.drop_index(idx, table_name="marketing_legenda_modelos", schema=SCHEMA)
    op.drop_table("marketing_legenda_modelos", schema=SCHEMA)
