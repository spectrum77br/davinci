"""Cadastros › Produtos: tipos e nomes (categorias e tags de envio)

Pedido M-29 do Harry (prévia aprovada em 24/11/2026): aba nova "Produtos" no grupo Cadastros,
com a tabela Tipo | Nome. Os filtros Tags e Categoria do Produtos 2 (M-30) leem esta lista.

ESTRUTURA (só aditiva, duas tabelas novas):
- `produto_cadastro_tipos` (id, nome, chave, created_at, updated_at): nome único sem olhar
  maiúsculas (índice em lower(nome)); `chave` única e opcional marca os tipos com regra
  ('categoria', 'tag_envio') — a regra segue o tipo mesmo renomeado.
- `produto_cadastros` (id, tipo_id → tipos RESTRICT, nome, created_at, updated_at): nome único
  por tipo sem olhar maiúsculas (índice em (tipo_id, lower(nome))).

DADOS INICIAIS (os do pedido):
- Categoria: Acessórios, Celular, Celular Kit, Eletro, Eletro Kit, Insumos, Mala, Mala Kit — as 8
  da base do ERP, com o mesmo nome. A ligação produto → categoria continua na base do ERP e é pelo
  nome: nada lá é mexido, nenhum produto perde a categoria.
- Tag de envio: .cd, .ci, .pi, .ra, .sa, .sp.

PERMISSÃO (item 11 do pedido: "mesma permissão das outras abas do grupo Cadastros"): a aba tem a
chave própria `cadastro_produtos`, como cada aba do grupo. Para ninguém que já trabalha em Cadastros
ficar sem ela no dia do deploy, todo usuário NÃO admin com `view` em alguma aba de Cadastros com
chave própria (ABAS_CADASTROS) ganha `cadastro_produtos.view` — só ver; criar, editar e excluir são
só de admin no servidor. Admin vê tudo e não precisa. Depois a tela Permissões ajusta quem quiser
(molde da 0398). Imprime quem ganhou.

DOWNGRADE: tira a chave `cadastro_produtos` de todos os usuários e apaga as duas tabelas (nada de
fora aponta para elas).

`SET LOCAL lock_timeout` como na 0505/0507: tabelas novas não travam ninguém, mas a regra da casa
vale para toda migration — se algo segurar o catálogo, desiste em 5 s e é só rodar de novo.

Revision ID: 0508_produto_cadastros
Revises: 0507_marketing_gasto_dia
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0508_produto_cadastros"
# Depois da 0507 do M-22 (gasto de Ads por dia), que vem depois da 0506 da Fraude do main.
down_revision: str | None = "0507_marketing_gasto_dia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TIPOS = "produto_cadastro_tipos"
CADASTROS = "produto_cadastros"
# FIXOS (não importam o modelo vivo).
INICIAIS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "categoria",
        "Categoria",
        (
            "Acessórios",
            "Celular",
            "Celular Kit",
            "Eletro",
            "Eletro Kit",
            "Insumos",
            "Mala",
            "Mala Kit",
        ),
    ),
    ("tag_envio", "Tag de envio", (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")),
)
# As abas do grupo Cadastros (apps/web/lib/navGroups.ts, TABS_CADASTROS e TABS_INTEGRACOES) com
# chave própria, em 24/11/2026. Fora: Legendas (usa a chave dos Criativos, que é do Marketing) e
# Imobilizado (sem chave: todo mundo vê os próprios itens).
ABAS_CADASTROS: tuple[str, ...] = (
    "empresa",
    "emissao_servico",
    "cadastro",
    "lojas_info",
    "segmentos",
    "marcas",
    "redes_sociais",
    "email_padroes",
    "integracoes",
    "sincronizacoes",
    "alertas",
)


def _datas() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def semear(bind) -> None:
    """Os 2 tipos e os 14 nomes do pedido (ver docstring do módulo)."""
    s = SCHEMA
    for chave, tipo, nomes in INICIAIS:
        tipo_id = bind.execute(
            sa.text(f"INSERT INTO {s}.{TIPOS} (nome, chave) VALUES (:nome, :chave) RETURNING id"),
            {"nome": tipo, "chave": chave},
        ).scalar_one()
        for nome in nomes:
            bind.execute(
                sa.text(f"INSERT INTO {s}.{CADASTROS} (tipo_id, nome) VALUES (:tipo, :nome)"),
                {"tipo": tipo_id, "nome": nome},
            )


def liberar_aba(bind) -> list[str]:
    """`cadastro_produtos.view` para quem já vê alguma aba de Cadastros (ver docstring)."""
    return [
        r.name or r.email
        for r in bind.execute(
            sa.text(
                f"""
                UPDATE {SCHEMA}.users
                   SET permissions = jsonb_set(
                           coalesce(permissions, '{{}}'::jsonb),
                           '{{cadastro_produtos}}',
                           '{{"view": true, "edit": false, "delete": false}}'::jsonb,
                           true
                       )
                 WHERE role::text <> 'admin'
                   AND coalesce(permissions -> 'cadastro_produtos' ->> 'view', 'false') <> 'true'
                   AND EXISTS (
                       SELECT 1 FROM unnest(CAST(:abas AS text[])) AS aba
                        WHERE coalesce(permissions -> aba ->> 'view', 'false') = 'true'
                   )
                RETURNING name, email
                """
            ),
            {"abas": list(ABAS_CADASTROS)},
        ).fetchall()
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        TIPOS,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("nome", sa.String(60), nullable=False),
        sa.Column("chave", sa.String(30), nullable=True),
        *_datas(),
        # Nomes finais (`op.f`), os mesmos que a convenção do modelo dá.
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TIPOS}")),
        sa.UniqueConstraint("chave", name=op.f(f"uq_{TIPOS}_chave")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_produto_cadastro_tipos_nome",
        TIPOS,
        [sa.text("lower(nome)")],
        unique=True,
        schema=SCHEMA,
    )
    op.create_table(
        CADASTROS,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tipo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome", sa.String(60), nullable=False),
        *_datas(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CADASTROS}")),
        sa.ForeignKeyConstraint(
            ["tipo_id"],
            [f"{SCHEMA}.{TIPOS}.id"],
            name=op.f(f"fk_{CADASTROS}_tipo_id_{TIPOS}"),
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_produto_cadastros_tipo_nome",
        CADASTROS,
        ["tipo_id", sa.text("lower(nome)")],
        unique=True,
        schema=SCHEMA,
    )
    semear(op.get_bind())
    quem = liberar_aba(op.get_bind()) or "ninguém (só admins veem Cadastros)"
    print(f"[0508] aba Cadastros › Produtos liberada (só ver) para: {quem}")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.execute(
        f"UPDATE {SCHEMA}.users SET permissions = permissions - 'cadastro_produtos' "
        "WHERE permissions ? 'cadastro_produtos'"
    )
    op.drop_table(CADASTROS, schema=SCHEMA)
    op.drop_table(TIPOS, schema=SCHEMA)
