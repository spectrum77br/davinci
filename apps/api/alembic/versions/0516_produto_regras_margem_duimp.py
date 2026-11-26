# ruff: noqa: E501, S608
"""Margem mín, DUIMP padrão e Condição especial POR PRODUTO (M-74, Produtos 2)

Pedido M-74 do Harry (26/11/2026): os três valores, que eram só do segmento (Cadastros ›
Segmentos), passam a poder ser preenchidos no cadastro do produto. Em branco = usa o do segmento,
como hoje. NADA é copiado: as duas tabelas nascem VAZIAS e, com elas vazias, a margem mínima de
todo pedido, a Condição especial e a nota continuam exatamente iguais.

Chave do produto = `sku_base`: cada peça do código sem a tag, em minúsculas
(`"+".join(p.split(".")[0].strip().lower() for p in sku.split("+"))`): dg019.ra → dg019;
dg019.ra+a020.ra → dg019+a020; uaf001m1.110 → uaf001m1.

1. `produto_regras` (sku_base PK; margem_min em fração 0..1; duimp_padrao até 75, sem "|";
   atualizado_por/atualizado_em). `sku` é uma cópia gerada do sku_base, só para o Histórico achar
   o nome do produto (o gatilho do Histórico procura a coluna `sku`).
2. `produto_condicoes_especiais`: a Condição especial do produto (período pela data do pedido e
   piso). Tabela própria, sem mexer em `segment_special_dates` (o segmento continua igual). Quem
   lê: routers/margens._MARGEM_DATA_ESPECIAL_SQL.
3. `vw_bling_pedidos` recriada com AS MESMAS COLUNAS, NA MESMA ORDEM (CREATE OR REPLACE). O corpo
   é o da 0235, copiado aqui (congelado); só muda a margem mínima do item:
       valor do produto → segmento filho → "Queima de estoque" p/ SKU "z" → 9%
   pelo `LEFT JOIN produto_regras pr ON pr.sku_base = <sku_base do item_codigo>` (PK: uma linha
   ou nenhuma, não multiplica). Pedido com vários itens: como hoje, cada item (linha da view)
   tem a sua mínima, pelo código do próprio item; quem junta por pedido (auto-hold, Informar,
   vigia) continua com MAX(margem_minima), sem mudança.
4. O gatilho do Histórico nas duas tabelas novas, se a função do Histórico existir (o worker
   também põe ao subir; aqui é para valer já no primeiro minuto).

Conferência na produção ANTES de publicar (só leitura): `.local/conferir_view_0513.sql`
(compara a view atual com o SELECT novo, com produto_regras vazia; esperado 0 diferenças).

DOWNGRADE: a view volta ao corpo da 0235 e as duas tabelas saem (o que foi preenchido nelas se
perde; a view e a Margem voltam a usar só o segmento).

Revision ID: 0516_produto_regras_margem_duimp
Revises: 0515_certificacoes_sku_anvisa
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0516_produto_regras_margem_duimp"
# No ramo; na junção o coordenador troca para a migration da frente A.
down_revision: str | None = "0515_certificacoes_sku_anvisa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXOS (não importam o modelo vivo; os mesmos textos de models/produto_regras.py).
CHECK_SKU_BASE = "sku_base <> '' AND sku_base = lower(sku_base) AND strpos(sku_base, '.') = 0"
CHECK_MARGEM_MIN = "margem_min BETWEEN 0 AND 1"
CHECK_DUIMP = "strpos(duimp_padrao, '|') = 0"
CHECK_PERIODO = "pedido_ate >= pedido_de"
CHECK_PISO = "piso BETWEEN -1 AND 1"


def sku_base_sql(coluna: str) -> str:
    """Cópia CONGELADA de services/produto_regras.sku_base_sql (o teste confere que são iguais)."""
    return (
        "regexp_replace(regexp_replace(regexp_replace(lower(" + coluna + "),"
        r" '\.[^+]*', '', 'g'), '\s*\+\s*', '+', 'g'), '^\s+|\s+$', '', 'g')"
    )


# ── corpo da view: CTEs, SELECT e cauda da 0235 (vigentes em 26/11/2026, iguais ao
# pg_get_viewdef da cópia da produção previa52) ─────────────────────────────────────
_CTES = r"""
 WITH order_totals AS (
         SELECT bo.numero,
            count(*) AS total_items,
            sum(COALESCE(w.eff_weight, COALESCE(bo.itemvalor, 0::numeric) * COALESCE(bo.item_quantidade, 1)::numeric)) AS total_itemvalor_pedido,
            max(COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)) AS total_valorbase_pedido
           FROM bling_orders bo
             LEFT JOIN vw_bling_item_mp_weight w ON w.bling_order_item_id = bo.id
          WHERE (bo.item_index > 0 OR bo.item_index = 0 AND bo.itemvalor IS NOT NULL) AND (COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric)
          GROUP BY bo.numero
        ), proportional_values AS (
         SELECT bo.id,
            bo.numero,
            bo.numeroloja,
            bo.data,
            bo.totalprodutos,
            bo.total,
            bo.situacao,
            bo.loja,
            bo.store_id,
            bo.itens,
            bo.valorbase,
            bo.custofrete,
            bo.taxacomissao,
            bo.preco_custo,
            bo.item_id,
            bo.itemvalor,
            bo.item_codigo,
            bo.item_produto_id,
            bo.item_descricao,
            bo.item_quantidade,
            bo.item_desconto,
            bo.item_comissao_base,
            bo.item_comissao_valor,
            bo.created_at,
            bo.updated_at,
            bo.bling_id,
            bo.item_index,
            bo.categoria_id,
            bo.categoria_nome,
            bo.verificado,
            bo.status,
            bo.aprovado_por,
            bo.reembolso,
            COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) AS valorbase_eff,
            COALESCE(ot.total_itemvalor_pedido, 0::numeric) AS total_itemvalor_pedido,
            COALESCE(ot.total_items, 1::bigint) AS total_items,
            COALESCE(ot.total_valorbase_pedido, 0::numeric) AS total_valorbase_pedido,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN 1.0
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido
                    ELSE 1.0 / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS item_proportion,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.total
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.total * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.total / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS total_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric)
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE COALESCE(NULLIF(bo.valorbase, 0::numeric), bo.total, 0::numeric) / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS valorbase_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.custofrete
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.custofrete * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.custofrete / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS custofrete_proporcional,
                CASE
                    WHEN COALESCE(ot.total_items, 1::bigint) = 1 THEN bo.taxacomissao
                    WHEN ot.total_itemvalor_pedido > 0::numeric AND bo.itemvalor IS NOT NULL THEN bo.taxacomissao * (COALESCE(w.eff_weight, bo.itemvalor * COALESCE(bo.item_quantidade, 1)::numeric) / ot.total_itemvalor_pedido)
                    ELSE bo.taxacomissao / COALESCE(ot.total_items, 1::bigint)::numeric
                END AS taxacomissao_proporcional
           FROM bling_orders bo
             LEFT JOIN vw_bling_item_mp_weight w ON w.bling_order_item_id = bo.id
             LEFT JOIN order_totals ot ON bo.numero = ot.numero
          WHERE COALESCE(bo.valorbase, 0::numeric) > 0::numeric OR COALESCE(bo.total, 0::numeric) > 0::numeric
        ), with_margin AS (
         SELECT pv.id,
            pv.numero,
            pv.numeroloja,
            pv.data,
            pv.totalprodutos,
            pv.total,
            pv.situacao,
            pv.loja,
            pv.store_id,
            pv.itens,
            pv.valorbase,
            pv.custofrete,
            pv.taxacomissao,
            pv.preco_custo,
            pv.item_id,
            pv.itemvalor,
            pv.item_codigo,
            pv.item_produto_id,
            pv.item_descricao,
            pv.item_quantidade,
            pv.item_desconto,
            pv.item_comissao_base,
            pv.item_comissao_valor,
            pv.created_at,
            pv.updated_at,
            pv.bling_id,
            pv.item_index,
            pv.categoria_id,
            pv.categoria_nome,
            pv.verificado,
            pv.status,
            pv.aprovado_por,
            pv.reembolso,
            pv.valorbase_eff,
            pv.total_itemvalor_pedido,
            pv.total_items,
            pv.total_valorbase_pedido,
            pv.item_proportion,
            pv.total_proporcional,
            pv.valorbase_proporcional,
            pv.custofrete_proporcional,
            pv.taxacomissao_proporcional,
                CASE
                    WHEN COALESCE(pv.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric AND COALESCE(pv.item_quantidade, 0) > 0 AND pv.valorbase_proporcional > 0::numeric THEN (pv.valorbase_proporcional - (pv.custofrete_proporcional + pv.taxacomissao_proporcional) - pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric + COALESCE(pv.reembolso, 0::double precision)::numeric * pv.item_proportion) / (pv.preco_custo::numeric(10,2) * pv.item_quantidade::numeric)
                    ELSE NULL::numeric
                END AS margem
           FROM proportional_values pv
        )
"""

# SELECT vigente (0235) — usado no downgrade e como base do novo.
_SELECT_ATUAL = r"""
 SELECT wm.id,
    wm.numero,
    wm.numeroloja,
    wm.data,
    wm.totalprodutos,
    wm.total_proporcional AS total,
    wm.situacao,
    sb.nome AS situacao_nome,
    wm.loja,
    COALESCE(s.apelido_override, si.account_name::text) AS loja_nome,
    COALESCE(i.platform::text, s.marketplace::text, si.platform::text) AS marketplace,
    wm.itens,
    wm.valorbase_proporcional AS valorbase,
    wm.custofrete_proporcional AS custofrete,
    wm.taxacomissao_proporcional AS taxacomissao,
    wm.item_id,
    wm.itemvalor,
    wm.item_codigo,
    wm.item_produto_id,
    wm.item_descricao,
    wm.item_quantidade,
    wm.item_desconto,
    wm.item_comissao_base,
    wm.item_comissao_valor,
    wm.created_at AS bo_created_at,
    wm.updated_at AS bo_updated_at,
    wm.bling_id,
    wm.item_proportion,
    wm.total_itemvalor_pedido,
    wm.valorbase AS original_valorbase,
    wm.custofrete AS original_custofrete,
    wm.taxacomissao AS original_taxacomissao,
    wm.preco_custo::numeric(10,2) AS preco_custo,
    wm.categoria_id,
    wm.categoria_nome,
    wm.verificado,
    wm.status,
    wm.aprovado_por,
    wm.margem,
    pp.segment_id AS subtype_id,
    leaf.name AS subtype,
    root.name AS segmento,
    COALESCE(leaf.min_margin,
        CASE
            WHEN lower(wm.item_codigo) ~~ 'z%'::text THEN qz.min_margin
            ELSE NULL::numeric
        END, 0.09)::numeric(6,4) AS min_margin,
        CASE
            WHEN wm.margem IS NULL THEN NULL::numeric
            ELSE wm.margem - COALESCE(leaf.min_margin,
        CASE
            WHEN lower(wm.item_codigo) ~~ 'z%'::text THEN qz.min_margin
            ELSE NULL::numeric
        END, 0.09)
        END AS margin_floor_diff,
        CASE
            WHEN COALESCE(wm.preco_custo::numeric(10,2), 0::numeric(10,2)) > 0::numeric AND COALESCE(wm.item_quantidade, 0) > 0 AND wm.valorbase_proporcional > 0::numeric THEN wm.valorbase_proporcional - (wm.custofrete_proporcional + wm.taxacomissao_proporcional) - wm.preco_custo::numeric(10,2) * wm.item_quantidade::numeric + COALESCE(wm.reembolso, 0::double precision)::numeric * wm.item_proportion
            ELSE NULL::numeric
        END AS lucro
   FROM with_margin wm
     LEFT JOIN stores s ON s.bling_store_id =
        CASE
            WHEN wm.loja ~ '^[0-9]+$'::text THEN wm.loja::bigint
            ELSE NULL::bigint
        END
     LEFT JOIN store_info si ON si.bling_store_id = NULLIF(wm.loja, ''::text)"""

# Cauda vigente (0235).
_TAIL_ATUAL = r"""
     LEFT JOIN integrations i ON i.id = s.integration_id
     LEFT JOIN situacao_bling sb ON sb.id::text = wm.situacao
     LEFT JOIN LATERAL ( SELECT v.segment_id
           FROM pricing_product_variant v
          WHERE lower(wm.item_codigo) = v.variant_norm OR lower(wm.item_codigo) ~~ (v.variant_norm || '.%'::text) OR lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text)
          ORDER BY (
                CASE
                    WHEN lower(wm.item_codigo) = v.variant_norm THEN 1
                    WHEN lower(wm.item_codigo) ~~ (v.variant_norm || '+%'::text) THEN 2
                    ELSE 3
                END), v.vlen DESC, v.segment_id
         LIMIT 1) pp ON true
     LEFT JOIN segments leaf ON leaf.id = pp.segment_id
     LEFT JOIN segments root ON root.id = leaf.parent_id
     LEFT JOIN ( SELECT s2.min_margin
           FROM segments s2
          WHERE lower(s2.name) = 'queima de estoque'::text
          LIMIT 1) qz ON true;
"""

# A mínima do item: o valor do produto entra NA FRENTE nas duas expressões (min_margin e
# margin_floor_diff). O resto do SELECT é o mesmo texto.
_MINIMA_ATUAL = "COALESCE(leaf.min_margin,"
_MINIMA_NOVA = "COALESCE(pr.margem_min, leaf.min_margin,"


def select_novo() -> str:
    if _SELECT_ATUAL.count(_MINIMA_ATUAL) != 2:
        raise RuntimeError("0516: SELECT da 0235 inesperado (esperava 2 mínimas)")
    return _SELECT_ATUAL.replace(_MINIMA_ATUAL, _MINIMA_NOVA)


def tail_novo(tabela_regras: str | None = None) -> str:
    """A cauda da 0235 + o JOIN do produto. `tabela_regras` troca a tabela (a conferência usa
    um CTE vazio no lugar)."""
    tabela = tabela_regras or f'"{SCHEMA}".produto_regras'
    return (
        _TAIL_ATUAL.rstrip().rstrip(";")
        + f"\n     LEFT JOIN {tabela} pr ON pr.sku_base = {sku_base_sql('wm.item_codigo')};\n"
    )


def view_atual() -> str:
    return f'CREATE OR REPLACE VIEW "{SCHEMA}".vw_bling_pedidos AS' + _CTES + _SELECT_ATUAL + _TAIL_ATUAL


def view_nova() -> str:
    return f'CREATE OR REPLACE VIEW "{SCHEMA}".vw_bling_pedidos AS' + _CTES + select_novo() + tail_novo()


def select_conferencia() -> str:
    """O SELECT novo com produto_regras trocada por um CTE vazio (roda antes da migration)."""
    vazio = "pr_vazia AS (SELECT NULL::text AS sku_base, NULL::numeric(5,4) AS margem_min WHERE false),"
    ctes = _CTES.replace(" WITH order_totals AS (", f" WITH {vazio} order_totals AS (", 1)
    if ctes == _CTES:
        raise RuntimeError("0516: CTEs da 0235 inesperados")
    return ctes + select_novo() + tail_novo("pr_vazia")


def _quem_e_quando(quem: str) -> list[sa.Column]:
    return [
        sa.Column(
            quem,
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            quem.replace("_por", "_em"),
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _sku() -> sa.Column:
    # Cópia gerada do sku_base: o gatilho do Histórico mostra o produto pela coluna `sku`.
    return sa.Column("sku", sa.Text(), sa.Computed("sku_base", persisted=True))


_GATILHO = (
    "DO $$ BEGIN"
    " IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
    "            WHERE n.nspname = '{schema}' AND p.proname = 'historico_captura')"
    " AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = '\"{schema}\".{tabela}'::regclass"
    "                 AND t.tgname = 'historico_captura') THEN"
    "  CREATE TRIGGER historico_captura AFTER INSERT OR UPDATE OR DELETE ON \"{schema}\".{tabela}"
    "  FOR EACH ROW WHEN (coalesce(current_setting('davinci.ator', true), '') <> '')"
    "  EXECUTE FUNCTION \"{schema}\".historico_captura();"
    " END IF; END $$"
)


def upgrade() -> None:
    op.create_table(
        "produto_regras",
        sa.Column("sku_base", sa.Text(), nullable=False),
        _sku(),
        sa.Column("margem_min", sa.Numeric(5, 4), nullable=True),
        sa.Column("duimp_padrao", sa.String(75), nullable=True),
        *_quem_e_quando("atualizado_por"),
        sa.PrimaryKeyConstraint("sku_base", name=op.f("pk_produto_regras")),
        sa.CheckConstraint(CHECK_SKU_BASE, name=op.f("ck_produto_regras_sku_base")),
        sa.CheckConstraint(CHECK_MARGEM_MIN, name=op.f("ck_produto_regras_margem_min")),
        sa.CheckConstraint(CHECK_DUIMP, name=op.f("ck_produto_regras_duimp_padrao")),
        schema=SCHEMA,
    )
    op.create_table(
        "produto_condicoes_especiais",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sku_base", sa.Text(), nullable=False),
        _sku(),
        sa.Column("pedido_de", sa.Date(), nullable=False),
        sa.Column("pedido_ate", sa.Date(), nullable=False),
        sa.Column("piso", sa.Numeric(6, 4), nullable=True),
        *_quem_e_quando("criado_por"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_produto_condicoes_especiais")),
        sa.CheckConstraint(CHECK_SKU_BASE, name=op.f("ck_produto_condicoes_especiais_sku_base")),
        sa.CheckConstraint(CHECK_PERIODO, name=op.f("ck_produto_condicoes_especiais_periodo")),
        sa.CheckConstraint(CHECK_PISO, name=op.f("ck_produto_condicoes_especiais_piso")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_produto_condicoes_especiais_sku_base",
        "produto_condicoes_especiais",
        ["sku_base"],
        schema=SCHEMA,
    )
    op.execute(view_nova())
    for tabela in ("produto_regras", "produto_condicoes_especiais"):
        op.execute(_GATILHO.format(schema=SCHEMA, tabela=tabela))


def downgrade() -> None:
    op.execute(view_atual())
    op.drop_index(
        "ix_produto_condicoes_especiais_sku_base",
        table_name="produto_condicoes_especiais",
        schema=SCHEMA,
    )
    op.drop_table("produto_condicoes_especiais", schema=SCHEMA)
    op.drop_table("produto_regras", schema=SCHEMA)
