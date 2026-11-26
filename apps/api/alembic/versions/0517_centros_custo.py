"""Cadastros › Centro de custo: tabela `centros_custo` com Celular, Eletro e Mala

Pedido M-76 do Harry (25/11/2026): aba nova "Centro de custo" em Cadastros, logo depois de
"Produtos". Celular, Eletro e Mala NÃO são uma lista nova paralela: são as raízes de
Cadastros › Segmentos (as três grandes categorias do sistema). A raiz "Catálogo" não é centro de
custo e fica de fora.

ESTRUTURA (só aditiva, uma tabela nova):
- `centros_custo` (id, nome, segment_id, fixo, criado_em, criado_por):
  - `nome` varchar(60), único sem olhar maiúsculas (índice em lower(nome));
  - `segment_id` → segments(id) ON DELETE RESTRICT, único e opcional: a raiz do centro. Centro
    novo nasce sem raiz;
  - `fixo`: os três de hoje (não mudam de nome e não saem);
  - `criado_por` → users(id) ON DELETE SET NULL.

DADOS INICIAIS: uma linha para cada raiz (parent_id NULL) de slug `celular`, `eletro` e `mala`,
com `fixo = true` e o MESMO nome do segmento. Nada em `segments` é mexido. Imprime o que entrou
e avisa se faltar alguma das três (numa base sem segmentos, como a de teste, não entra nenhuma).

Numeração: no ramo a revision é a 0514 depois da 0511; na junção com as outras frentes o
coordenador renumera (0517, depois da 0516) — o conteúdo não muda.

DOWNGRADE: apaga a tabela (nada de fora aponta para ela). As raízes ficam como estão.

`SET LOCAL lock_timeout` como na 0508: tabela nova não trava ninguém, mas a regra da casa vale
para toda migration.

Revision ID: 0517_centros_custo
Revises: 0516_produto_regras_margem_duimp
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0517_centros_custo"
# Provisória no ramo feat/m75-m76-cadastros (a junção troca pela ponta das outras frentes).
down_revision: str | None = "0516_produto_regras_margem_duimp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "centros_custo"
# FIXO (não importa o modelo vivo): as três raízes que viram centro de custo, na ordem da M-23.
SLUGS_FIXOS: tuple[str, ...] = ("celular", "eletro", "mala")
NOME_MAX = 60


def semear(bind) -> list[str]:
    """Uma linha por raiz celular/eletro/mala, com o nome do segmento (ver docstring)."""
    s = SCHEMA
    return [
        r.nome
        for r in bind.execute(
            sa.text(
                f"""
                INSERT INTO {s}.{TABELA} (nome, segment_id, fixo)
                SELECT left(seg.name, {NOME_MAX}), seg.id, true
                  FROM {s}.segments seg
                 WHERE seg.parent_id IS NULL
                   AND seg.slug = ANY(CAST(:slugs AS text[]))
                 ORDER BY array_position(CAST(:slugs AS text[]), seg.slug::text)
                RETURNING nome
                """
            ),
            {"slugs": list(SLUGS_FIXOS)},
        ).fetchall()
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        TABELA,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("nome", sa.String(NOME_MAX), nullable=False),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fixo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("criado_por", postgresql.UUID(as_uuid=True), nullable=True),
        # Nomes finais (`op.f`), os mesmos que a convenção do modelo dá.
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABELA}")),
        sa.UniqueConstraint("segment_id", name=op.f(f"uq_{TABELA}_segment_id")),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            [f"{SCHEMA}.segments.id"],
            name=op.f(f"fk_{TABELA}_segment_id_segments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f(f"fk_{TABELA}_criado_por_users"),
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_centros_custo_nome", TABELA, [sa.text("lower(nome)")], unique=True, schema=SCHEMA
    )
    entraram = semear(op.get_bind())
    print(f"[0517] centros de custo: {', '.join(entraram) or 'nenhum'}")  # noqa: T201
    if len(entraram) != len(SLUGS_FIXOS):
        print(  # noqa: T201
            f"[0517] AVISO: esperava as raízes {', '.join(SLUGS_FIXOS)} em Cadastros › "
            f"Segmentos; entraram {len(entraram)}."
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.drop_table(TABELA, schema=SCHEMA)
