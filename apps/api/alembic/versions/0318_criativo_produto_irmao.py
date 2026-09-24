"""criativo sem produto: religa pelo irmão avulso ou pelo cadastro

Eduardo, 24/09/2026, com o print do modal: três criativos `dg046.sp` saíram
com a legenda genérica da marca ("Comprar eletrônico pela internet dá um frio
na barriga…"), sem o nome do aparelho. O produto existe — "Uranyx F110L 8.128
- Preto" —, mas o SKU do criativo só casava com ANÚNCIO, e o anúncio está
cadastrado como `dg046.pi`. O `dg046.sp` existe só no cadastro de produtos.

A regra nova mora em `_product_id_do_sku` (routers/marketing_creatives.py),
que roda no salvamento. Esta migration aplica a MESMA regra, em SQL, a quem já
foi salvo sem produto — senão os três `dg046.sp` de hoje (um deles aprovado,
na fila do robô) continuariam genéricos até alguém reeditar o SKU na mão:

  1. anúncio com o SKU inteiro, depois com a base (o que já existia);
  2. IRMÃO: anúncio avulso (sem "+") de outra variante da mesma base — só
     quando todos os irmãos são o MESMO produto. O `dg023` tem irmãos avulso,
     usado e kit 2 e continua sem produto: sortear um faria a legenda falar
     de celular usado;
  3. CADASTRO: `products.sku` igual ao SKU inteiro. Com irmão E cadastro, o
     irmão ganha se os nomes baterem (pelo irmão vem a frase de ficha), e o
     cadastro ganha se não baterem.

Só PREENCHE product_id nulo; nunca troca um vínculo existente. Vale pra
criativos e roteiros, que usam a mesma função. O downgrade não desfaz: não há
como separar o que esta migration ligou do que alguém ligou depois.
"""

# ruff: noqa: S608 — schema e tabela são constantes deste arquivo, não entrada de usuário.

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0318_criativo_produto_irmao"
down_revision: str | None = "0317_desempenho_videos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def sql_religar(schema: str, tabela: str) -> str:
    """O UPDATE da regra, pra uma tabela com `sku` e `product_id`. Função pura
    pra o teste rodar o MESMO texto no schema de teste."""
    s = schema
    return f"""
    WITH c AS (
        SELECT id, lower(btrim(sku)) AS alvo, split_part(lower(btrim(sku)), '.', 1) AS base
          FROM {s}.{tabela}
         WHERE product_id IS NULL AND coalesce(btrim(sku), '') <> ''
    ),
    direto AS (
        SELECT DISTINCT ON (c.id) c.id, pl.product_id
          FROM c JOIN {s}.product_links pl
            ON lower(btrim(pl.external_sku)) IN (c.alvo, c.base)
         ORDER BY c.id, (lower(btrim(pl.external_sku)) = c.alvo) DESC, pl.created_at, pl.id
    ),
    irmao AS (
        SELECT c.id, (array_agg(DISTINCT pl.product_id))[1] AS product_id
          FROM c JOIN {s}.product_links pl
            ON split_part(lower(btrim(pl.external_sku)), '.', 1) = c.base
           AND position('+' in pl.external_sku) = 0
           AND pl.product_id IS NOT NULL
         GROUP BY c.id
        HAVING count(DISTINCT pl.product_id) = 1
    ),
    cadastro AS (
        SELECT DISTINCT ON (c.id) c.id, p.id AS product_id, p.name
          FROM c JOIN {s}.products p ON lower(btrim(p.sku)) = c.alvo
         ORDER BY c.id, p.created_at, p.id
    ),
    escolha AS (
        SELECT c.id,
               coalesce(
                   d.product_id,
                   CASE
                       WHEN i.product_id IS NOT NULL
                        AND (ca.product_id IS NULL
                             OR lower(btrim(pi.name)) = lower(btrim(ca.name)))
                       THEN i.product_id
                       ELSE ca.product_id
                   END
               ) AS product_id
          FROM c
          LEFT JOIN direto d ON d.id = c.id
          LEFT JOIN irmao i ON i.id = c.id
          LEFT JOIN {s}.products pi ON pi.id = i.product_id
          LEFT JOIN cadastro ca ON ca.id = c.id
    )
    UPDATE {s}.{tabela} t
       SET product_id = e.product_id
      FROM escolha e
     WHERE t.id = e.id AND e.product_id IS NOT NULL AND t.product_id IS NULL
    """


def upgrade() -> None:
    bind = op.get_bind()
    for tabela in ("marketing_creatives", "marketing_roteiros"):
        n = bind.execute(sa.text(sql_religar(SCHEMA, tabela))).rowcount
        print(f"0318: {tabela} religados = {n}")


def downgrade() -> None:
    # Ver docstring: preencher vínculo não tem desfazer seguro.
    pass
