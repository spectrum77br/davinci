"""marcas + redes_sociais + marca_email_padroes — Cadastros › Marcas, Redes Sociais e E-mails

Eduardo, 15/09/2026: abas novas no grupo Cadastros, vindas da planilha
`redes sociais.xlsx`.

`marcas` espelha a aba `marcas` (a coluna 'atuação' virou `classe`; a
'validade' é do domínio → `dominio_validade`) mais a linha da marca na aba
r.social (`funcao`, `tipo`, `sac_fone`/`sac_email`/`sac_senha_enc` — fone,
usuário e senha das redes/SAC são por marca, como na planilha) e os dados da
assinatura dos e-mails (`company_id`, `site`, `logo`).

`redes_sociais` é UMA linha por (marca, plataforma, conta) — mais pra frente
cada linha vira o alvo do envio automático de vídeos por plataforma.
e-mail/fone/senha da conta são só o que DIFERE da marca (NULL = herda).
`verificacao_status` registra o andamento do selo Meta Verified (na marca,
`whatsapp_verificacao_status` faz o mesmo pro Zap).

`marca_email_padroes`: padrões de e-mail por marca e canal (SAC, Mercado
Livre…) — templates renderizados com logo e assinatura da empresa.

Senhas cifradas (`*_senha_enc`, app.security.cipher — mesmo esquema do
nf_faturador). Unicidades por índice parcial: a mesma conta não pode estar
em duas marcas na mesma plataforma, e cada (marca, plataforma) tem no máximo
uma linha sem conta. Os índices também estão declarados nos models
(app/models/marca.py) pra valer no create_all dos testes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0277_marcas_redes_sociais"
down_revision: str | None = "0276_chamado_pedidos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "marcas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column(
            "inpi_status", sa.String(length=32),
            nullable=False, server_default=sa.text("'nao_registrado'"),
        ),
        sa.Column("usuario", sa.Text(), nullable=True),
        sa.Column("senha_enc", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("dominio_br", sa.Text(), nullable=True),
        sa.Column("dominio", sa.Text(), nullable=True),
        sa.Column("dono_dominio", sa.Text(), nullable=True),
        sa.Column("dominio_validade", sa.Date(), nullable=True),
        sa.Column("classe", sa.Text(), nullable=True),
        sa.Column("funcao", sa.Text(), nullable=True),
        sa.Column("tipo", sa.Text(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # aba r.social — linha da marca
        sa.Column("sac_fone", sa.Text(), nullable=True),
        sa.Column("sac_email", sa.Text(), nullable=True),
        sa.Column("sac_senha_enc", sa.Text(), nullable=True),
        sa.Column(
            "whatsapp_verificacao_status", sa.String(length=32),
            nullable=False, server_default=sa.text("'nao_solicitado'"),
        ),
        sa.Column("whatsapp_verificacao_obs", sa.Text(), nullable=True),
        # assinatura dos e-mails
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.companies.id",
                ondelete="SET NULL",
                name="fk_marcas_company_id_companies",
            ),
            nullable=True,
        ),
        sa.Column("site", sa.Text(), nullable=True),
        sa.Column("logo", sa.LargeBinary(), nullable=True),
        sa.Column("logo_mime", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("slug", name="uq_marcas_slug"),
        schema=SCHEMA,
    )
    op.create_index("ix_marcas_company_id", "marcas", ["company_id"], schema=SCHEMA)

    op.create_table(
        "redes_sociais",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marcas.id",
                ondelete="CASCADE",
                name="fk_redes_sociais_marca_id_marcas",
            ),
            nullable=False,
        ),
        sa.Column("plataforma", sa.String(length=32), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("usuario", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("fone", sa.Text(), nullable=True),
        sa.Column("senha_enc", sa.Text(), nullable=True),
        sa.Column(
            "verificacao_status", sa.String(length=32),
            nullable=False, server_default=sa.text("'nao_solicitado'"),
        ),
        sa.Column("verificacao_obs", sa.Text(), nullable=True),
        sa.Column("obs", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_redes_sociais_marca_id", "redes_sociais", ["marca_id"], schema=SCHEMA
    )
    # A mesma conta (@) não existe em duas marcas na mesma plataforma.
    op.create_index(
        "uq_redes_sociais_plataforma_conta",
        "redes_sociais",
        ["plataforma", sa.text("lower(conta)")],
        unique=True,
        postgresql_where=sa.text("conta IS NOT NULL"),
        schema=SCHEMA,
    )
    # No máximo UMA linha "sem conta" por (marca, plataforma).
    op.create_index(
        "uq_redes_sociais_marca_plataforma_sem_conta",
        "redes_sociais",
        ["marca_id", "plataforma"],
        unique=True,
        postgresql_where=sa.text("conta IS NULL"),
        schema=SCHEMA,
    )

    op.create_table(
        "marca_email_padroes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marca_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.marcas.id",
                ondelete="CASCADE",
                name="fk_marca_email_padroes_marca_id_marcas",
            ),
            nullable=False,
        ),
        sa.Column("contexto", sa.String(length=32), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("remetente_nome", sa.Text(), nullable=True),
        sa.Column("remetente_email", sa.Text(), nullable=True),
        sa.Column("assunto", sa.Text(), nullable=False),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column(
            "incluir_logo", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "incluir_assinatura", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marca_email_padroes_marca_id", "marca_email_padroes", ["marca_id"], schema=SCHEMA
    )
    # Nome único por (marca, contexto) sem caixa.
    op.create_index(
        "uq_marca_email_padroes_marca_contexto_nome",
        "marca_email_padroes",
        ["marca_id", "contexto", sa.text("lower(nome)")],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_marca_email_padroes_marca_contexto_nome",
        table_name="marca_email_padroes",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_marca_email_padroes_marca_id", table_name="marca_email_padroes", schema=SCHEMA
    )
    op.drop_table("marca_email_padroes", schema=SCHEMA)
    op.drop_index(
        "uq_redes_sociais_marca_plataforma_sem_conta",
        table_name="redes_sociais",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_redes_sociais_plataforma_conta", table_name="redes_sociais", schema=SCHEMA
    )
    op.drop_index("ix_redes_sociais_marca_id", table_name="redes_sociais", schema=SCHEMA)
    op.drop_table("redes_sociais", schema=SCHEMA)
    op.drop_index("ix_marcas_company_id", table_name="marcas", schema=SCHEMA)
    op.drop_table("marcas", schema=SCHEMA)
