"""Conferência: Mercado Livre e Amazon nas mesmas tabelas da Shopee

Decisão do dono (07/10/2026): o mesmo relatório da Conferência Shopee (a
planilha Mala · Celular · Eletro · Geral) para o Mercado Livre e a Amazon, um
por marketplace. Doc: docs/conferencia-shopee.md (seção "Mercado Livre e
Amazon"). ML e Amazon são coletados pelo SERVIDOR (worker), não pelo executor
do Mac: as contas não têm perfil do AdsPower — são achadas pela integração do
DaVinci e pela loja do Bling.

O que muda (os nomes `conferencia_shopee_*` ficam: renomear mexeria nas
exclusões do Histórico):
  • conta: `plataforma` (shopee | ml | amazon, padrão shopee — as 20 lojas de
    hoje continuam Shopee), `integration_id` (FK integrations, SET NULL),
    `bling_loja_id` (texto, como `bling_orders.loja`); `adspower_user_id`
    passa a aceitar NULL e o UNIQUE dele vira únicos PARCIAIS por plataforma:
    (plataforma, adspower_user_id), (plataforma, integration_id) e
    (plataforma, bling_loja_id) — a mesma loja do Bling em duas contas
    contaria as mesmas vendas duas vezes.
  • execução: `plataforma` (padrão shopee) e um único parcial — uma rodada
    `coletando` por plataforma (a trava da criação já garante; é o cinto).
    Se em produção houver duas rodadas coletando ao mesmo tempo (não deveria:
    fila.criar_execucao não deixa), o CREATE falha e a migration inteira volta.
  • coleta: `adspower_user_id` passa a aceitar NULL (coleta do servidor).
  • semente: as contas do ML e da Amazon da lista do dono (07/10/2026), cada
    uma ligada à integração do DaVinci da mesma plataforma pelo NOME (sem
    espaço nem pontuação: "Jlas 2" → integração "jlas2"; só não arquivada; só
    se for UMA) e à loja do Bling (Amazon: as lojas conferidas no dossiê;
    ML: override da integração → loja do cadastro da integração → loja do
    cadastro de Lojas com o mesmo apelido). Conta da lista que ficou sem
    integração ou sem loja do Bling entra DESATIVADA com a observação do que
    falta (decisão do dono: sem vínculo = desativada com nota). Loja do Bling
    que a busca achou para duas contas da mesma plataforma (ou que já está
    numa conta) fica só na primeira da lista; a outra entra sem loja,
    desativada, com a nota. Idempotente (chave: plataforma + conta_key).

Só ADD COLUMN nulável ou com default constante (sem reescrever tabela; as
tabelas têm dezenas de linhas) + índices pequenos.

O downgrade apaga as rodadas do ML e da Amazon (as coletas vão junto) e as
contas que ESTA semente criou, e volta as colunas e o UNIQUE de antes. Conta do
ML/Amazon que não veio da semente (não há rota que crie) faria o SET NOT NULL
falhar: o downgrade inteiro volta e nada fica pela metade.

`tests/test_conferencia_shopee_migration.py` roda a 0377 + esta num schema
descartável, compara o catálogo com o model, a semente (com integrações e
lojas inventadas) e o downgrade.

Revision ID: 0382_conferencia_plataformas
Revises: 0381_marketing_ads_estado
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0382_conferencia_plataformas"
down_revision: str | None = "0381_marketing_ads_estado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOCK_TIMEOUT = "10s"

# Mesmos valores de app/models/conferencia_shopee.py (a migration não importa o app).
_PLATAFORMAS = ("shopee", "ml", "amazon")

CONTA = "conferencia_shopee_conta"
EXECUCAO = "conferencia_shopee_execucao"
COLETA = "conferencia_shopee_coleta"
UQ_ANTIGO = "uq_conferencia_shopee_conta_adspower_user_id"
UQ_ADSPOWER = "uq_conferencia_shopee_conta_plataforma_adspower_user_id"
UQ_INTEGRACAO = "uq_conferencia_shopee_conta_plataforma_integration_id"
UQ_LOJA = "uq_conferencia_shopee_conta_plataforma_bling_loja_id"
UQ_COLETANDO = "uq_conferencia_shopee_execucao_plataforma_coletando"
FK_INTEGRACAO = "fk_conferencia_shopee_conta_integration"
CK_CONTA = "ck_conferencia_shopee_conta_plataforma"
CK_EXECUCAO = "ck_conferencia_shopee_execucao_plataforma"

_NOVA_ML = "Conta nova do Mercado Livre (perfil de 05/10/2026), ainda sem integração no DaVinci."
_DESATIVADA = "Entrou desativada na lista (decisão de 07/10/2026)."

# (plataforma, nome exibido, grupo, ativo pela lista do dono, chave = nome da
# integração sem espaço/pontuação, loja do Bling já conferida | None, nota).
_CONTAS: tuple[tuple[str, str, str, bool, str, str | None, str | None], ...] = (
    # Mercado Livre — Mala.
    ("ml", "Marquezini", "mala", True, "marquezini", None, None),
    ("ml", "Forpaper", "mala", True, "forpaper", None, None),
    ("ml", "KFA", "mala", True, "kfa", None, None),
    ("ml", "Poofy", "mala", True, "poofy", None, None),
    # Mercado Livre — Celular (o eletro sai item a item).
    ("ml", "Jlas 2", "celular", True, "jlas2", None, None),
    ("ml", "KFA 2", "celular", True, "kfa2", None, None),
    ("ml", "Inova", "celular", True, "inova", None, None),
    ("ml", "Barbosa", "celular", True, "barbosa", None, None),
    ("ml", "Injox", "celular", True, "injox", None, None),
    ("ml", "Aguiar", "celular", True, "aguiar", None, None),
    ("ml", "Aguiar 2", "celular", True, "aguiar2", None, None),
    ("ml", "Kia", "celular", True, "kia", None, None),
    ("ml", "Velasco", "celular", True, "velasco", None, None),
    ("ml", "Victor MEI", "celular", True, "victormei", None, None),
    ("ml", "Mega", "celular", True, "mega", None, None),
    ("ml", "Dream 2", "celular", True, "dream2", None, None),
    ("ml", "Zorvex", "celular", True, "zorvex", None, None),
    ("ml", "Mini", "celular", True, "mini", None, None),
    ("ml", "Vita", "celular", True, "vita", None, None),
    # Mercado Livre — desativadas.
    ("ml", "Atlas", "celular", False, "atlas", None, _NOVA_ML),
    ("ml", "Fiore", "celular", False, "fiore", None, _NOVA_ML),
    ("ml", "VR", "celular", False, "vr", None, _NOVA_ML),
    ("ml", "Jlas", "celular", False, "jlas", None, _DESATIVADA),
    ("ml", "Eron", "celular", False, "eron", None, _DESATIVADA),
    ("ml", "Lucas MEI", "celular", False, "lucasmei", None, _DESATIVADA),
    ("ml", "Counhago", "celular", False, "counhago", None, _DESATIVADA),
    # Amazon (lojas do Bling conferidas no dossiê de 07/10/2026).
    ("amazon", "KFA", "mala", True, "kfa", "204438129", None),
    ("amazon", "Poofy", "mala", True, "poofy", "206099015", None),
    ("amazon", "Kia", "celular", True, "kia", "204713113", None),
    ("amazon", "Nexus", "celular", False, "nexus", "206064394", _DESATIVADA),
)

_SEM_INTEGRACAO = {
    "ml": "Sem integração do Mercado Livre no DaVinci com este nome: ligue em Contas para ativar.",
    "amazon": "Sem integração da Amazon no DaVinci com este nome: ligue em Contas para ativar.",
}
_SEM_LOJA = "Sem loja do Bling (as Vendas saem dos pedidos do Bling): ligue em Contas para ativar."
_LOJA_REPETIDA = (
    "A loja do Bling {loja} achada para esta conta já está em outra conta desta plataforma: "
    "ligue a loja certa em Contas para ativar."
)

# O nome sem espaço nem pontuação, em minúsculas ("Jlas 2" → "jlas2").
_NORMAL = "regexp_replace(lower({col}), '[^a-z0-9]', '', 'g')"


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


def _txt(v: str | None) -> str:
    return "NULL" if v is None else "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    # ---- conta -------------------------------------------------------------
    op.add_column(
        CONTA,
        sa.Column("plataforma", sa.Text(), server_default=sa.text("'shopee'"), nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f(CK_CONTA), CONTA, _in("plataforma", _PLATAFORMAS), schema=SCHEMA
    )
    op.add_column(
        CONTA, sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        op.f(FK_INTEGRACAO),
        CONTA,
        "integrations",
        ["integration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.add_column(CONTA, sa.Column("bling_loja_id", sa.Text(), nullable=True), schema=SCHEMA)
    op.drop_constraint(UQ_ANTIGO, CONTA, type_="unique", schema=SCHEMA)
    op.alter_column(CONTA, "adspower_user_id", nullable=True, schema=SCHEMA)
    op.create_index(
        UQ_ADSPOWER,
        CONTA,
        ["plataforma", "adspower_user_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("adspower_user_id IS NOT NULL"),
    )
    op.create_index(
        UQ_INTEGRACAO,
        CONTA,
        ["plataforma", "integration_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("integration_id IS NOT NULL"),
    )
    op.create_index(
        UQ_LOJA,
        CONTA,
        ["plataforma", "bling_loja_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("bling_loja_id IS NOT NULL"),
    )

    # ---- execução ----------------------------------------------------------
    op.add_column(
        EXECUCAO,
        sa.Column("plataforma", sa.Text(), server_default=sa.text("'shopee'"), nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f(CK_EXECUCAO), EXECUCAO, _in("plataforma", _PLATAFORMAS), schema=SCHEMA
    )
    op.create_index(
        UQ_COLETANDO,
        EXECUCAO,
        ["plataforma"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'coletando'"),
    )

    # ---- coleta ------------------------------------------------------------
    op.alter_column(COLETA, "adspower_user_id", nullable=True, schema=SCHEMA)

    _semear()


def _sql_semente() -> str:
    valores = ",\n".join(
        f"({n}, '{p}', {_txt(nome)}, '{grupo}', {'true' if ativo else 'false'}, '{chave}', "
        f"{_txt(loja)}, {_txt(nota)})"
        for n, (p, nome, grupo, ativo, chave, loja, nota) in enumerate(_CONTAS)
    )
    s = f'"{SCHEMA}"'
    nome_integracao = _NORMAL.format(col="i.name")
    apelido = _NORMAL.format(col="coalesce(nullif(btrim(st.apelido_override), ''), co.apelido)")
    sem_integracao = " ".join(
        f"WHEN '{p}' THEN {_txt(texto)}" for p, texto in _SEM_INTEGRACAO.items()
    )
    antes, depois = _LOJA_REPETIDA.split("{loja}")
    return f"""
WITH v(ordem_lista, plataforma, nome, grupo, ativo, chave, loja_fixa, nota) AS (
  VALUES {valores}
),
com_integracao AS (
  SELECT v.*,
         (SELECT (array_agg(i.id))[1]
            FROM {s}.integrations i
           WHERE i.platform::text = v.plataforma
             AND i.archived_at IS NULL
             AND {nome_integracao} = v.chave
          HAVING count(*) = 1) AS integration_id
    FROM v
),
com_loja AS (
  SELECT c.*,
         coalesce(
           c.loja_fixa,
           (SELECT i.bling_loja_id::text FROM {s}.integrations i WHERE i.id = c.integration_id),
           (SELECT st.bling_store_id::text
              FROM {s}.integrations i JOIN {s}.stores st ON st.id = i.store_id
             WHERE i.id = c.integration_id),
           (SELECT (array_agg(st.bling_store_id::text))[1]
              FROM {s}.stores st
             WHERE st.integration_id = c.integration_id AND st.bling_store_id IS NOT NULL
            HAVING count(*) = 1),
           (SELECT (array_agg(st.bling_store_id::text))[1]
              FROM {s}.stores st JOIN {s}.companies co ON co.id = st.company_id
             WHERE st.marketplace::text = c.plataforma
               AND st.bling_store_id IS NOT NULL
               AND {apelido} = c.chave
            HAVING count(*) = 1)
         ) AS bling_loja_id
    FROM com_integracao c
),
-- A mesma loja do Bling em duas contas da plataforma contaria as vendas duas
-- vezes (e o único parcial derrubaria a migration): fica na primeira da lista
-- (ativa antes), e só se nenhuma conta que já existe a usa.
com_repetida AS (
  SELECT l.*,
         l.bling_loja_id IS NOT NULL AND (
           row_number() OVER (
             PARTITION BY l.plataforma, l.bling_loja_id ORDER BY l.ativo DESC, l.ordem_lista
           ) > 1
           OR EXISTS (
             SELECT 1 FROM {s}.{CONTA} x
              WHERE x.plataforma = l.plataforma AND x.bling_loja_id = l.bling_loja_id
           )
         ) AS loja_repetida
    FROM com_loja l
),
final AS (
  SELECT r.plataforma, r.nome, r.grupo, r.ativo, r.chave, r.nota, r.integration_id,
         CASE WHEN r.loja_repetida THEN NULL ELSE r.bling_loja_id END AS bling_loja_id,
         CASE WHEN r.loja_repetida
              THEN {_txt(antes)} || r.bling_loja_id || {_txt(depois)} END AS nota_loja
    FROM com_repetida r
)
INSERT INTO {s}.{CONTA}
  (id, plataforma, adspower_user_id, nome, grupo, ativo, ordem, conta_key, integration_id,
   bling_loja_id, observacao)
SELECT gen_random_uuid(), l.plataforma, NULL, l.nome, l.grupo,
       l.ativo AND l.integration_id IS NOT NULL AND l.bling_loja_id IS NOT NULL,
       0, l.chave, l.integration_id, l.bling_loja_id,
       nullif(concat_ws(' ',
         l.nota,
         CASE WHEN l.integration_id IS NULL THEN
           CASE l.plataforma {sem_integracao} END
         END,
         coalesce(l.nota_loja, CASE WHEN l.bling_loja_id IS NULL THEN {_txt(_SEM_LOJA)} END)
       ), '')
  FROM final l
 WHERE NOT EXISTS (
   SELECT 1 FROM {s}.{CONTA} x WHERE x.plataforma = l.plataforma AND x.conta_key = l.chave
 )
"""  # noqa: S608


def _semear() -> None:
    """As contas do ML e da Amazon — idempotente (o teste roda duas vezes)."""
    op.execute(_sql_semente())


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    s = f'"{SCHEMA}"'
    # Rodadas do ML/Amazon somem (as coletas vão junto, ON DELETE CASCADE): sem
    # a coluna `plataforma` elas pareceriam rodadas da Shopee.
    op.execute(f"DELETE FROM {s}.{EXECUCAO} WHERE plataforma <> 'shopee'")  # noqa: S608
    chaves = ", ".join(f"('{p}', '{chave}')" for p, _, _, _, chave, _, _ in _CONTAS)
    op.execute(
        f"DELETE FROM {s}.{CONTA} WHERE (plataforma, conta_key) IN ({chaves})"  # noqa: S608
    )

    op.alter_column(COLETA, "adspower_user_id", nullable=False, schema=SCHEMA)

    op.drop_index(UQ_COLETANDO, table_name=EXECUCAO, schema=SCHEMA)
    op.drop_constraint(op.f(CK_EXECUCAO), EXECUCAO, type_="check", schema=SCHEMA)
    op.drop_column(EXECUCAO, "plataforma", schema=SCHEMA)

    op.drop_index(UQ_LOJA, table_name=CONTA, schema=SCHEMA)
    op.drop_index(UQ_INTEGRACAO, table_name=CONTA, schema=SCHEMA)
    op.drop_index(UQ_ADSPOWER, table_name=CONTA, schema=SCHEMA)
    op.alter_column(CONTA, "adspower_user_id", nullable=False, schema=SCHEMA)
    op.create_unique_constraint(op.f(UQ_ANTIGO), CONTA, ["adspower_user_id"], schema=SCHEMA)
    op.drop_column(CONTA, "bling_loja_id", schema=SCHEMA)
    op.drop_constraint(op.f(FK_INTEGRACAO), CONTA, type_="foreignkey", schema=SCHEMA)
    op.drop_column(CONTA, "integration_id", schema=SCHEMA)
    op.drop_constraint(op.f(CK_CONTA), CONTA, type_="check", schema=SCHEMA)
    op.drop_column(CONTA, "plataforma", schema=SCHEMA)
