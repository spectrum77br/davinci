"""garantias + atendimentos, anexos e log — Painel de Garantia Uranyx

Documento "Painel de Garantia — Uranyx (DaVinci)" (07/10/2026): cadastro da
garantia (pedido, NF, nome, CPF) com os prazos de hardware (entrega + 3 meses)
e de software (entrega + 12 meses), os atendimentos do Comunicador gravados na
garantia, o arquivo dos anexos e o log de quem consultou/alterou.

Atendimento, anexo e log só recebem linha nova: o gatilho
`garantia_so_insercao` recusa UPDATE, DELETE e TRUNCATE (a marca de sessão
`davinci.garantia_expurgo = 'sim'` libera, para um expurgo da LGPD feito por
quem administra o banco). As quatro tabelas ficam fora do gatilho do Histórico
(`historico/sql.py`, EXCLUIDAS): têm nome, CPF e texto de comprador.

Tabelas novas e vazias: nada existente muda.

Revision ID: 0380_garantias
Revises: 0379_pricing_carrefour_netshoes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.garantia import (
    FUNCAO_SO_INSERCAO,
    GARANTIA_COBERTURAS,
    GARANTIA_LOG_ACOES,
    GARANTIA_TABELAS_SO_INSERCAO,
    GARANTIA_TIPOS_PROBLEMA,
    sql_funcao_so_insercao,
    sql_gatilhos_so_insercao,
)

revision: str = "0380_garantias"
down_revision: str | None = "0379_pricing_carrefour_netshoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"


def _user_fk(nome: str, *, nullable: bool) -> sa.Column:
    return sa.Column(
        nome,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.create_table(
        "garantias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pedido_bling", sa.String(20), nullable=False),
        sa.Column("pedido_marketplace", sa.String(64), nullable=True),
        sa.Column("loja", sa.String(32), nullable=True),
        sa.Column("plataforma", sa.String(32), nullable=True),
        sa.Column("conta", sa.String(128), nullable=True),
        sa.Column("nf_numero", sa.String(9), nullable=False),
        sa.Column("nf_serie", sa.String(3), nullable=False, server_default=""),
        sa.Column("nf_chave", sa.String(44), nullable=True),
        sa.Column("nf_emitente_cnpj", sa.String(14), nullable=True),
        sa.Column("cliente_nome", sa.String(200), nullable=False),
        sa.Column("cpf", sa.String(11), nullable=False),
        sa.Column("data_inicio", sa.Date(), nullable=True),
        sa.Column("fim_hardware", sa.Date(), nullable=True),
        sa.Column("fim_software", sa.Date(), nullable=True),
        sa.Column("entrega_origem", sa.String(20), nullable=True),
        sa.Column("entrega_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entrega_verificada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "itens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _user_fk("criado_por", nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        _user_fk("atualizado_por", nullable=True),
        sa.UniqueConstraint("cpf", "nf_numero", "nf_serie", name="uq_garantias_cpf_nf"),
        sa.CheckConstraint("cpf ~ '^[0-9]{11}$'", name="cpf_digitos"),
        sa.CheckConstraint("nf_numero ~ '^[1-9][0-9]{0,8}$'", name="nf_numero_digitos"),
        sa.CheckConstraint("nf_serie ~ '^([1-9][0-9]{0,2})?$'", name="nf_serie_digitos"),
        sa.CheckConstraint("nf_chave IS NULL OR nf_chave ~ '^[0-9]{44}$'", name="nf_chave_digitos"),
        sa.CheckConstraint(
            "(data_inicio IS NULL) = (fim_hardware IS NULL)"
            " AND (data_inicio IS NULL) = (fim_software IS NULL)"
            " AND (data_inicio IS NULL) = (entrega_origem IS NULL)",
            name="prazos_completos",
        ),
        sa.CheckConstraint(
            "data_inicio IS NULL OR (fim_hardware > data_inicio AND fim_software >= fim_hardware)",
            name="prazos_em_ordem",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_garantias_nf_chave",
        "garantias",
        ["nf_chave"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("nf_chave IS NOT NULL"),
    )
    op.create_index("ix_garantias_pedido_bling", "garantias", ["pedido_bling"], schema=SCHEMA)
    op.create_index(
        "ix_garantias_pedido_marketplace", "garantias", ["pedido_marketplace"], schema=SCHEMA
    )
    op.create_index("ix_garantias_nf_numero", "garantias", ["nf_numero"], schema=SCHEMA)
    op.create_index("ix_garantias_loja", "garantias", ["loja"], schema=SCHEMA)
    op.create_index("ix_garantias_cpf", "garantias", ["cpf"], schema=SCHEMA)

    op.create_table(
        "garantia_atendimentos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "garantia_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.garantias.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("data_atendimento", sa.DateTime(timezone=True), nullable=False),
        _user_fk("atendente_id", nullable=False),
        sa.Column("atendente_nome", sa.Text(), nullable=False),
        sa.Column("conversa_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversa_link", sa.Text(), nullable=False),
        sa.Column("conversa_plataforma", sa.String(16), nullable=True),
        sa.Column("conversa_canal", sa.String(16), nullable=True),
        sa.Column("conversa_conta", sa.Text(), nullable=True),
        sa.Column("conversa_externo_id", sa.String(191), nullable=True),
        sa.Column("conversa_pedido", sa.String(64), nullable=True),
        sa.Column("resumo", sa.Text(), nullable=False),
        sa.Column(
            "mensagens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "anexos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tipo_problema", sa.String(10), nullable=False),
        sa.Column("cobertura", sa.String(20), nullable=False),
        sa.Column("fim_considerado", sa.Date(), nullable=True),
        sa.Column("solucao", sa.Text(), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            f"tipo_problema IN ({_lista(GARANTIA_TIPOS_PROBLEMA)})", name="tipo_valido"
        ),
        sa.CheckConstraint(
            f"cobertura IN ({_lista(GARANTIA_COBERTURAS)})", name="cobertura_valida"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_atendimentos_garantia",
        "garantia_atendimentos",
        ["garantia_id", "data_atendimento"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_atendimentos_data",
        "garantia_atendimentos",
        ["data_atendimento"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_atendimentos_conversa",
        "garantia_atendimentos",
        ["conversa_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "garantia_atendimento_anexos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "atendimento_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.garantia_atendimentos.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mensagem_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("url_original", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("tamanho", sa.BigInteger(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_atendimento_anexos_atendimento",
        "garantia_atendimento_anexos",
        ["atendimento_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "garantia_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "garantia_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.garantias.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("acao", sa.String(30), nullable=False),
        sa.Column("campo", sa.String(30), nullable=True),
        sa.Column("valor_anterior", sa.Text(), nullable=True),
        sa.Column("valor_novo", sa.Text(), nullable=True),
        sa.Column("detalhe", sa.Text(), nullable=True),
        _user_fk("user_id", nullable=True),
        sa.Column("user_nome", sa.Text(), nullable=False),
        sa.Column("em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"acao IN ({_lista(GARANTIA_LOG_ACOES)})", name="acao_valida"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_log_garantia", "garantia_log", ["garantia_id", "em"], schema=SCHEMA
    )

    # Só inserção — os MESMOS comandos que o create_all dos testes roda.
    op.execute(sql_funcao_so_insercao(SCHEMA))
    for tabela in GARANTIA_TABELAS_SO_INSERCAO:
        for comando in sql_gatilhos_so_insercao(SCHEMA, tabela):
            op.execute(comando)


def downgrade() -> None:
    op.drop_table("garantia_log", schema=SCHEMA)
    op.drop_table("garantia_atendimento_anexos", schema=SCHEMA)
    op.drop_table("garantia_atendimentos", schema=SCHEMA)
    op.drop_table("garantias", schema=SCHEMA)
    op.execute(f'DROP FUNCTION IF EXISTS "{SCHEMA}".{FUNCAO_SO_INSERCAO}()')
