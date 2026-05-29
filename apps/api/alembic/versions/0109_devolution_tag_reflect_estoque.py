# ruff: noqa: E501
"""devolution tag reflete a tag do controle de estoque

Recalcula `devolutions.tag` para o MESMO vocabulário do Controle de Estoque
(sem ponto à frente) e com as mesmas regras de SKU: sufixo regional/usado,
fake (`fake.`), mala (`b`+dígito) e eletro (`u`). Espelha
`app.services.sku_tags.classify_sku_tag` em SQL puro — precedência via
COALESCE (sufixo vence). Antes a coluna guardava só sufixo com ponto
(`.sp`); agora guarda `sp`/`mala`/`eletro`/… ou NULL.

Revision ID: 0109_devolution_tag_reflect_estoque
Revises: 0108_vw_perfis
Create Date: 2026-05-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0109_devolution_tag_reflect_estoque"
down_revision: str | None = "0108_vw_perfis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.execute(
        r"""
        UPDATE devolutions d
        SET tag = sub.newtag
        FROM (
            SELECT dd.id,
                COALESCE(
                    -- 1. sufixo regional/usado (menor ordinal vence)
                    (SELECT lower((regexp_match(btrim(u.part), '\.([A-Za-z]+)$'))[1])
                     FROM unnest(string_to_array(dd.sku, '+')) WITH ORDINALITY AS u(part, ord)
                     WHERE lower((regexp_match(btrim(u.part), '\.([A-Za-z]+)$'))[1])
                           IN ('ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd')
                     ORDER BY u.ord
                     LIMIT 1),
                    -- 2. fake.
                    (CASE WHEN EXISTS (
                        SELECT 1 FROM unnest(string_to_array(dd.sku, '+')) AS u(part)
                        WHERE lower(btrim(u.part)) LIKE 'fake.%'
                     ) THEN 'fake' END),
                    -- 3. mala (b + dígito)
                    (CASE WHEN EXISTS (
                        SELECT 1 FROM unnest(string_to_array(dd.sku, '+')) AS u(part)
                        WHERE lower(btrim(u.part)) ~ '^b[0-9]'
                     ) THEN 'mala' END),
                    -- 4. eletro (u…)
                    (CASE WHEN EXISTS (
                        SELECT 1 FROM unnest(string_to_array(dd.sku, '+')) AS u(part)
                        WHERE lower(btrim(u.part)) ~ '^u'
                     ) THEN 'eletro' END)
                ) AS newtag
            FROM devolutions dd
        ) sub
        WHERE d.id = sub.id
        """
    )


def downgrade() -> None:
    # Sem reconstrução do formato antigo (`.sp`); a coluna é derivável do SKU.
    pass
