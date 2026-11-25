"""Aba Contas: par Clássico + Premium do ML, Desconto % e Custo total %

Pedidos M-53…M-56 (prévia aprovada pelo administrador em 25/11/2026; contrato em
relatorios/mudancas_25nov/contrato_M52_M57.md):

- `pricing_accounts.par_id` (UUID, NULL): o mesmo valor no Clássico e no Premium
  da mesma conta ML de kit; nas outras linhas fica NULL. Índice simples e único
  parcial (par_id, listing_type) WHERE par_id IS NOT NULL — o par é sempre um
  Clássico e um Premium. A aba Contas junta o par numa linha e a API grava as
  duas juntas; a grade, o cálculo, o envio de preço e a Margem continuam com as
  duas linhas separadas (nenhum preço muda).
- `pricing_accounts.desconto_pct` (M-55) e `custo_total_pct` (M-56):
  NUMERIC(5,2), de 0 a 100, NULL. Só anotação: fora do cálculo do preço. Todas
  as contas começam vazias (nenhuma fica acima do teto ao entrar no ar).

DADOS (upgrade):
1. Pareia TODOS os pares completos: platform=mercadolivre, canal=kit, mesmo
   segment_id, mesmo name, mesmo kit_number, exatamente 1 'ml classico' e 1
   'ml premium'. Os dois recebem o mesmo gen_random_uuid(). Só parear não muda
   nada.
2. Harmoniza SÓ os pares com as duas linhas visíveis na aba (sem loja e sem
   integração arquivada):
   - Obs: vazia ou contida na outra → fica a maior; senão Clássico + " | " +
     Premium. Grava nas duas.
   - store_info_id: só uma tem (loja não arquivada) → copia para a outra; as
     duas têm e são diferentes → ABORTA.
   - Ads da conta (`pricing_ads_conta`): só uma tem → copia para a outra
     (atualizado_por NULL, atualizado_em now()); as duas têm e são diferentes →
     ABORTA. Ads por produto (`pricing_ads_produto`, 0 linhas no ML hoje)
     diferente entre as duas → ABORTA.
   - Confere que margin1..5, shipping1..5, kit_number, afiliado_pct,
     oferta_limite_pct, desconto_ate, cupom_ate e integration_id são iguais,
     que o catálogo está ligado nas duas ou em nenhuma e que as margens do
     catálogo são iguais. Qualquer diferença → ABORTA.
   ABORTAR = `RuntimeError` com a lista das diferenças, ANTES de gravar
   qualquer harmonização; a transação da migration desfaz o pareamento junto.
   Nunca escolhe sozinha. NÃO mexe em slot1..5_segment_id (Clássico NULL e
   Premium preenchido em atlas/fiore: só afeta a página Margem), sort_order,
   comissão nem nos campos antigos escondidos (discount, affiliate, ads,
   coupon, offer, endereços, user_id…).

`SET LOCAL lock_timeout` como na 0505/0507/0508/0510: ALTER em
`pricing_accounts`, lida a toda hora pela grade; se algo segurar a tabela,
desiste em 5 s e é só rodar de novo.

DOWNGRADE: apaga as três colunas (com os índices e os CHECKs). A harmonização
não é desfeita (Obs, loja e Ads copiados são só anotação).

Revision ID: 0511_contas_ml_par_custo_total
Revises: 0510_estoque_entradas_troca_tag
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0511_contas_ml_par_custo_total"
down_revision: str | None = "0510_estoque_entradas_troca_tag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "pricing_accounts"
UQ_PAR = "uq_pricing_accounts_par_tipo"
CLASSICO = "ml classico"
PREMIUM = "ml premium"
SEPARADOR_OBS = " | "
MARGENS_FRETES = tuple(f"{c}{n}" for n in range(1, 6) for c in ("margin", "shipping"))
# Têm de ser iguais nas duas linhas do par visível.
CAMPOS_IGUAIS = (
    *MARGENS_FRETES,
    "kit_number",
    "afiliado_pct",
    "oferta_limite_pct",
    "desconto_ate",
    "cupom_ate",
    "integration_id",
)
CAMPOS_ADS = ("ads_geral_ligada", "pode_gastar_pct", "gasto_dia", "ads_por_produto_ligada")


def juntar_obs(classico: str | None, premium: str | None) -> str | None:
    """A Obs do par: vazia ou contida na outra → a maior; senão as duas, o
    Clássico primeiro."""
    c = classico if classico and classico.strip() else None
    p = premium if premium and premium.strip() else None
    if c is None or p is None:
        return c or p
    if p in c:
        return c
    if c in p:
        return p
    return f"{c}{SEPARADOR_OBS}{p}"


def parear(bind) -> int:
    """Passo 1: dá o mesmo gen_random_uuid() às duas linhas de cada par
    completo. Devolve quantos pares."""
    res = bind.execute(
        sa.text(
            f"""
            WITH pares AS MATERIALIZED (
                SELECT gen_random_uuid() AS par_id, g.classico, g.premium
                  FROM (
                        SELECT (array_agg(id) FILTER (WHERE listing_type = '{CLASSICO}'))[1]
                                   AS classico,
                               (array_agg(id) FILTER (WHERE listing_type = '{PREMIUM}'))[1]
                                   AS premium
                          FROM {SCHEMA}.{TABELA}
                         WHERE platform = 'mercadolivre' AND canal = 'kit' AND par_id IS NULL
                         GROUP BY segment_id, name, kit_number
                        HAVING count(*) FILTER (WHERE listing_type = '{CLASSICO}') = 1
                           AND count(*) FILTER (WHERE listing_type = '{PREMIUM}') = 1
                       ) g
            )
            UPDATE {SCHEMA}.{TABELA} a
               SET par_id = pares.par_id
              FROM pares
             WHERE a.id IN (pares.classico, pares.premium)
            """
        )
    )
    return res.rowcount // 2


def _linhas_pareadas(bind):
    margens_catalogo = ", ".join(f"f.margin{n} AS cat_margin{n}" for n in range(1, 6))
    campos = ", ".join(f"a.{c}" for c in CAMPOS_IGUAIS)
    return (
        bind.execute(
            sa.text(
                f"""
                SELECT a.id, a.par_id, a.listing_type, a.name, a.observation, a.store_info_id,
                       {campos},
                       seg.slug AS departamento,
                       (s.archived_at IS NOT NULL) AS loja_arquivada,
                       (i.archived_at IS NOT NULL) AS integracao_arquivada,
                       f.id AS catalogo_id, {margens_catalogo}
                  FROM {SCHEMA}.{TABELA} a
                  LEFT JOIN {SCHEMA}.store_info s ON s.id = a.store_info_id
                  LEFT JOIN {SCHEMA}.integrations i ON i.id = a.integration_id
                  LEFT JOIN {SCHEMA}.segments seg ON seg.id = a.segment_id
                  LEFT JOIN {SCHEMA}.{TABELA} f
                         ON f.conta_base_id = a.id AND f.canal = 'catalogo'
                 WHERE a.par_id IS NOT NULL
                 ORDER BY seg.slug, a.name, a.par_id, a.listing_type
                """
            )
        )
        .mappings()
        .all()
    )


def _ads_das_contas(bind) -> tuple[dict, dict]:
    """({conta: (caixas e valores da Geral)}, {conta: {produto: (%, gasto)}})."""
    gerais = {
        r["pricing_account_id"]: tuple(r[c] for c in CAMPOS_ADS)
        for r in bind.execute(
            sa.text(
                f"SELECT pricing_account_id, {', '.join(CAMPOS_ADS)}"
                f" FROM {SCHEMA}.pricing_ads_conta"
            )
        ).mappings()
    }
    produtos: dict = defaultdict(dict)
    for r in bind.execute(
        sa.text(
            "SELECT pricing_account_id, pricing_product_id, pode_gastar_pct, gasto_dia"
            f" FROM {SCHEMA}.pricing_ads_produto"
        )
    ).mappings():
        produtos[r["pricing_account_id"]][r["pricing_product_id"]] = (
            r["pode_gastar_pct"],
            r["gasto_dia"],
        )
    return gerais, produtos


def harmonizar(bind) -> dict[str, int]:
    """Passo 2: só os pares com as duas linhas visíveis. Confere tudo primeiro
    e só grava se não há nenhuma diferença fora do previsto."""
    por_par: dict = defaultdict(list)
    for linha in _linhas_pareadas(bind):
        por_par[linha["par_id"]].append(linha)
    gerais, produtos = _ads_das_contas(bind)

    problemas: list[str] = []
    obs: list[tuple[object, str]] = []  # (conta, obs nova)
    lojas: list[tuple[object, object]] = []  # (conta, loja)
    ads: list[tuple[object, object]] = []  # (de, para)
    visiveis = 0
    for linhas in por_par.values():
        por_tipo = {linha["listing_type"]: linha for linha in linhas}
        c, p = por_tipo.get(CLASSICO), por_tipo.get(PREMIUM)
        rotulo = f"{linhas[0]['departamento']}/{linhas[0]['name']}"
        if len(linhas) != 2 or c is None or p is None:
            problemas.append(f"{rotulo}: o par não tem exatamente um Clássico e um Premium")
            continue
        if any(linha["loja_arquivada"] or linha["integracao_arquivada"] for linha in (c, p)):
            continue
        visiveis += 1
        rotulo = f"{rotulo} (kit {c['kit_number']})"

        for campo in CAMPOS_IGUAIS:
            if c[campo] != p[campo]:
                problemas.append(f"{rotulo}: {campo} Clássico {c[campo]} ≠ Premium {p[campo]}")
        if (c["catalogo_id"] is None) != (p["catalogo_id"] is None):
            ligado = "Clássico" if c["catalogo_id"] is not None else "Premium"
            problemas.append(f"{rotulo}: catálogo ligado só no {ligado}")
        elif c["catalogo_id"] is not None:
            for n in range(1, 6):
                campo = f"cat_margin{n}"
                if c[campo] != p[campo]:
                    problemas.append(
                        f"{rotulo}: margem {n} do catálogo Clássico {c[campo]} ≠ Premium {p[campo]}"
                    )

        if c["store_info_id"] is not None and p["store_info_id"] is not None:
            if c["store_info_id"] != p["store_info_id"]:
                problemas.append(
                    f"{rotulo}: loja Clássico {c['store_info_id']} ≠ Premium {p['store_info_id']}"
                )
        elif c["store_info_id"] is not None:
            lojas.append((p["id"], c["store_info_id"]))
        elif p["store_info_id"] is not None:
            lojas.append((c["id"], p["store_info_id"]))

        ads_c, ads_p = gerais.get(c["id"]), gerais.get(p["id"])
        if ads_c is not None and ads_p is not None:
            if ads_c != ads_p:
                problemas.append(f"{rotulo}: Ads Clássico {ads_c} ≠ Premium {ads_p}")
        elif ads_c is not None:
            ads.append((c["id"], p["id"]))
        elif ads_p is not None:
            ads.append((p["id"], c["id"]))
        if produtos.get(c["id"], {}) != produtos.get(p["id"], {}):
            problemas.append(f"{rotulo}: Ads por produto diferente entre Clássico e Premium")

        nova = juntar_obs(c["observation"], p["observation"])
        for linha in (c, p):
            if nova is not None and linha["observation"] != nova:
                obs.append((linha["id"], nova))

    if problemas:
        raise RuntimeError(
            "Migration 0511: diferença(s) não prevista(s) entre o Clássico e o Premium de"
            " contas ML visíveis; nada foi gravado. Resolva e rode de novo:\n- "
            + "\n- ".join(problemas)
        )

    for conta, texto in obs:
        bind.execute(
            sa.text(f"UPDATE {SCHEMA}.{TABELA} SET observation = :obs WHERE id = :id"),
            {"obs": texto, "id": conta},
        )
    for conta, loja in lojas:
        bind.execute(
            sa.text(f"UPDATE {SCHEMA}.{TABELA} SET store_info_id = :loja WHERE id = :id"),
            {"loja": loja, "id": conta},
        )
    for de, para in ads:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.pricing_ads_conta
                       (pricing_account_id, {", ".join(CAMPOS_ADS)}, atualizado_por, atualizado_em)
                SELECT :para, {", ".join(CAMPOS_ADS)}, NULL, now()
                  FROM {SCHEMA}.pricing_ads_conta
                 WHERE pricing_account_id = :de
                """
            ),
            {"de": de, "para": para},
        )
    return {"visiveis": visiveis, "obs": len(obs), "lojas": len(lojas), "ads": len(ads)}


def migrar(bind) -> dict[str, int]:
    pares = parear(bind)
    return {"pares": pares, **harmonizar(bind)}


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        TABELA, sa.Column("par_id", postgresql.UUID(as_uuid=True), nullable=True), schema=SCHEMA
    )
    op.create_index(op.f("ix_pricing_accounts_par_id"), TABELA, ["par_id"], schema=SCHEMA)
    op.create_index(
        UQ_PAR,
        TABELA,
        ["par_id", "listing_type"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("par_id IS NOT NULL"),
    )
    for coluna in ("desconto_pct", "custo_total_pct"):
        op.add_column(TABELA, sa.Column(coluna, sa.Numeric(5, 2), nullable=True), schema=SCHEMA)
        op.create_check_constraint(
            op.f(f"ck_pricing_accounts_{coluna}"),
            TABELA,
            f"{coluna} BETWEEN 0 AND 100",
            schema=SCHEMA,
        )
    migrar(op.get_bind())


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index(UQ_PAR, table_name=TABELA, schema=SCHEMA)
    op.drop_index(op.f("ix_pricing_accounts_par_id"), table_name=TABELA, schema=SCHEMA)
    # Apagar a coluna apaga o CHECK dela.
    for coluna in ("custo_total_pct", "desconto_pct", "par_id"):
        op.drop_column(TABELA, coluna, schema=SCHEMA)
