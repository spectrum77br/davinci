"""kit celular: 4 variações fixas + 115 bases derivadas dos produtos

Revision ID: 0117_kit_celular_seed
Revises: 0116_seed_celular_products
Create Date: 2026-06-02

Etapa 2 de 4 — habilita a aba Kit do Celular.

Antes desta migration, a UNIQUE(ordem) em import_kit_variations
impedia categorias diferentes de usarem o mesmo número de ordem
(mala ocupa 1-22). Aqui:

  1. Troca UNIQUE(ordem) por UNIQUE(categoria, ordem) — cada categoria
     tem seu próprio range de ordem.
  2. Adiciona UNIQUE(categoria, code) — base pro ON CONFLICT idempotente
     deste seed e dos próximos.
  3. Insere as 4 variations de Celular (a001 Fone com fio, a003 Fone
     sem fio, a004 Relógio, a003+a004 Fone sem fio + Relógio).
  4. Insere 115 bases derivadas dos import_products categoria='celular'
     (seed 0116). `cor` é extraída do final do modelo_bling via regex.

Idempotente: ON CONFLICT em variations; bases usam INSERT ... SELECT
... WHERE NOT EXISTS pra não duplicar (não há UNIQUE em sku_base+
categoria, só em sku_base global).

Downgrade: remove rows celular, restaura UNIQUE(ordem) original.
Restauração assume que apenas mala existia antes — válido neste
ponto (eletro não tem kit).
"""

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "0117_kit_celular_seed"
down_revision = "0116_seed_celular_products"
branch_labels = None
depends_on = None

SCHEMA = "davinci"

# (code, label, ordem). Ordem definida pelo operador.
_VARIATIONS: list[tuple[str, str, int]] = [
    ("a001", "Fone com fio", 1),
    ("a003", "Fone sem fio", 2),
    ("a004", "Relógio", 3),
    ("a003+a004", "Fone sem fio + Relógio", 4),
]


def upgrade() -> None:
    # 1) Troca UNIQUE(ordem) por UNIQUE(categoria, ordem). DROP precisa
    #    rodar antes do CREATE — celular usa ordem 1-4 que já estão em
    #    mala (1-22), seria conflito se UNIQUE(ordem) global persistir.
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_kit_variations "
        f"DROP CONSTRAINT IF EXISTS uq_import_kit_variations_ordem"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_import_kit_variations_cat_ordem "
        f"ON {SCHEMA}.import_kit_variations (categoria, ordem)"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_import_kit_variations_cat_code "
        f"ON {SCHEMA}.import_kit_variations (categoria, code)"
    )

    # 2) Seed das 4 variations celular.
    conn = op.get_bind()
    var_stmt = text(
        f"""
        INSERT INTO {SCHEMA}.import_kit_variations
            (categoria, code, label, ordem, highlight)
        VALUES
            ('celular', :code, :label, :ordem, FALSE)
        ON CONFLICT (categoria, code) DO UPDATE SET
            label = EXCLUDED.label,
            ordem = EXCLUDED.ordem
        """  # noqa: S608
    )
    for code, label, ordem in _VARIATIONS:
        conn.execute(var_stmt, {"code": code, "label": label, "ordem": ordem})

    # 3) Seed das bases celular a partir de import_products. Cada
    #    produto vira 1 base. `cor` extraída do trecho após o último
    #    ' - ' no modelo_bling (todos os 115 modelos seguem esse
    #    padrão; valida-se no seed 0116). Capitaliza 1ª letra pra
    #    consistência (e.g. 'cinza' → 'Cinza').
    #
    #    Sem UNIQUE(sku_base, categoria), usamos NOT EXISTS pra
    #    idempotência. sku_base global é UNIQUE — não vai colidir
    #    porque SKUs celular (i*, dg*) não existem em mala (b*, bp*).
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.import_kit_bases
            (categoria, modelo_bling, sku_base, cor, ordem)
        SELECT
            'celular',
            p.modelo_bling,
            p.sku,
            UPPER(LEFT(regexp_replace(p.modelo_bling, '^.*\\s-\\s', ''), 1))
                || SUBSTRING(regexp_replace(p.modelo_bling, '^.*\\s-\\s', '') FROM 2),
            row_number() OVER (ORDER BY p.sku)
        FROM {SCHEMA}.import_products p
        WHERE p.categoria = 'celular'
          AND NOT EXISTS (
              SELECT 1 FROM {SCHEMA}.import_kit_bases b
              WHERE b.sku_base = p.sku
          )
        """  # noqa: S608
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove kit marks celular (CASCADE de bases/variations não roda
    # automaticamente se a FK não tiver ON DELETE — defensive).
    del_marks = f"DELETE FROM {SCHEMA}.import_kit_marks WHERE categoria = 'celular'"  # noqa: S608
    del_bases = f"DELETE FROM {SCHEMA}.import_kit_bases WHERE categoria = 'celular'"  # noqa: S608
    del_vars = f"DELETE FROM {SCHEMA}.import_kit_variations WHERE categoria = 'celular'"  # noqa: S608
    conn.execute(text(del_marks))
    conn.execute(text(del_bases))
    conn.execute(text(del_vars))

    # Restaura UNIQUE(ordem) global. Assume que só mala persiste com
    # variations neste ponto (eletro não tem kit).
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_import_kit_variations_cat_code"
    )
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_import_kit_variations_cat_ordem"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.import_kit_variations "
        f"ADD CONSTRAINT uq_import_kit_variations_ordem UNIQUE (ordem)"
    )
