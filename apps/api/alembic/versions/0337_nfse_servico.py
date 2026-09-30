"""NFS-e Nacional: aba NF Faturador › Emissão de Serviço

Eduardo (28/09/2026): "quero criar no davinci uma aba nova emissão de serviço
[...] nota de serviço de intermediação", pelo Emissor Nacional (gov.br), com
tomador do grupo ou de fora e as mesmas notas todo mês. Espelha
`app/models/nfse.py` (ver lá o porquê de cada tabela).

Revision ID: 0337_nfse_servico
Revises: 0336_pricing_embalagens
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0337_nfse_servico"
down_revision: str | None = "0336_pricing_embalagens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
UUID = pg.UUID(as_uuid=True)


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _user_fk(nome: str) -> sa.Column:
    return sa.Column(
        nome, UUID, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    op.create_table(
        "company_fiscal",
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cmun_ibge", sa.String(7), nullable=True),
        sa.Column("municipio_nome", sa.Text(), nullable=True),
        sa.Column("inscricao_municipal", sa.Text(), nullable=True),
        sa.Column("informar_im", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("op_simp_nac", sa.SmallInteger(), nullable=True),
        sa.Column("reg_ap_trib_sn", sa.SmallInteger(), nullable=True),
        sa.Column("reg_esp_trib", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("tot_trib_modo", sa.Text(), nullable=True),
        sa.Column("p_tot_trib_sn", sa.Numeric(5, 2), nullable=True),
        sa.Column("p_tot_trib_fed", sa.Numeric(5, 2), nullable=True),
        sa.Column("p_tot_trib_est", sa.Numeric(5, 2), nullable=True),
        sa.Column("p_tot_trib_mun", sa.Numeric(5, 2), nullable=True),
        sa.Column("c_trib_nac", sa.String(6), server_default="100501", nullable=False),
        sa.Column("c_nbs", sa.String(9), server_default="102010000", nullable=True),
        sa.Column("c_trib_mun", sa.String(3), nullable=True),
        sa.Column("serie_dps", sa.Integer(), server_default="1", nullable=False),
        sa.Column("ibscbs_ativo", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ibscbs_c_ind_op", sa.String(6), server_default="100301", nullable=False),
        sa.Column("ibscbs_cst", sa.String(3), server_default="000", nullable=False),
        sa.Column("ibscbs_c_class_trib", sa.String(6), server_default="000001", nullable=False),
        sa.Column(
            "certificado_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.company_certificates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fone", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("convenio_aderente", sa.Boolean(), nullable=True),
        sa.Column("convenio_json", pg.JSONB(), nullable=True),
        sa.Column("convenio_em", sa.DateTime(timezone=True), nullable=True),
        _user_fk("updated_by"),
        *_ts(),
        schema=SCHEMA,
    )

    op.create_table(
        "nfse_tomador",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("documento", sa.String(14), nullable=True),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("fone", sa.Text(), nullable=True),
        sa.Column("cep", sa.String(8), nullable=True),
        sa.Column("cmun_ibge", sa.String(7), nullable=True),
        sa.Column("logradouro", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("complemento", sa.Text(), nullable=True),
        sa.Column("bairro", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        _user_fk("created_by"),
        *_ts(),
        sa.CheckConstraint("tipo IN ('grupo', 'externo')", name=op.f("ck_nfse_tomador_tipo")),
        sa.CheckConstraint(
            "(tipo = 'grupo' AND company_id IS NOT NULL)"
            " OR (tipo = 'externo' AND documento IS NOT NULL AND nome IS NOT NULL)",
            name=op.f("ck_nfse_tomador_dados"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "nfse_modelo",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tomador_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.nfse_tomador.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("valor", sa.Numeric(15, 2), nullable=False),
        sa.Column("c_trib_nac", sa.String(6), nullable=True),
        sa.Column("c_nbs", sa.String(9), nullable=True),
        sa.Column("c_trib_mun", sa.String(3), nullable=True),
        sa.Column("inf_comp", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        _user_fk("created_by"),
        *_ts(),
        schema=SCHEMA,
    )
    op.create_index("ix_nfse_modelo_company_id", "nfse_modelo", ["company_id"], schema=SCHEMA)

    op.create_table(
        "nfse_dps_contador",
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tp_amb", sa.SmallInteger(), primary_key=True),
        sa.Column("c_loc_emi", sa.String(7), primary_key=True),
        sa.Column("serie", sa.Integer(), primary_key=True),
        sa.Column("ultimo_ndps", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "nfse_emissao",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "company_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tomador_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.nfse_tomador.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "modelo_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.nfse_modelo.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("tp_amb", sa.SmallInteger(), nullable=False),
        sa.Column("c_loc_emi", sa.String(7), nullable=False),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("n_dps", sa.BigInteger(), nullable=False),
        sa.Column("id_dps", sa.String(45), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("valor_servico", sa.Numeric(15, 2), nullable=False),
        sa.Column("perfil_assinatura", sa.Text(), nullable=True),
        sa.Column("chave_acesso", sa.String(50), nullable=True),
        sa.Column("n_nfse", sa.Text(), nullable=True),
        sa.Column("c_stat", sa.Text(), nullable=True),
        sa.Column("dh_emi", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dh_proc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("v_bc", sa.Numeric(15, 2), nullable=True),
        sa.Column("p_aliq_aplic", sa.Numeric(5, 2), nullable=True),
        sa.Column("v_issqn", sa.Numeric(15, 2), nullable=True),
        sa.Column("v_liq", sa.Numeric(15, 2), nullable=True),
        sa.Column("snapshot", pg.JSONB(), server_default="{}", nullable=False),
        sa.Column("dps_xml_b64", sa.Text(), nullable=True),
        sa.Column("nfse_xml_b64", sa.Text(), nullable=True),
        sa.Column("alertas", pg.JSONB(), nullable=True),
        sa.Column("erros", pg.JSONB(), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default="0", nullable=False),
        _user_fk("created_by"),
        _user_fk("emitido_por"),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelada_em", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.CheckConstraint(
            "status IN ('enviando', 'emitida', 'rejeitada', 'incerta', 'cancelando', 'cancelada')",
            name=op.f("ck_nfse_emissao_status"),
        ),
        sa.UniqueConstraint("id_dps", name="uq_nfse_emissao_id_dps"),
        sa.UniqueConstraint("chave_acesso", name="uq_nfse_emissao_chave_acesso"),
        sa.UniqueConstraint(
            "company_id",
            "tp_amb",
            "c_loc_emi",
            "serie",
            "n_dps",
            name="uq_nfse_emissao_company_id_tp_amb_c_loc_emi_serie_n_dps",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_nfse_emissao_company_id", "nfse_emissao", ["company_id"], schema=SCHEMA)
    op.create_index("ix_nfse_emissao_competencia", "nfse_emissao", ["competencia"], schema=SCHEMA)
    op.create_index(
        "uq_nfse_emissao_modelo_mes",
        "nfse_emissao",
        ["modelo_id", "competencia", "tp_amb"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "modelo_id IS NOT NULL AND status IN ('enviando', 'emitida', 'incerta', 'cancelando')"
        ),
    )

    op.create_table(
        "nfse_evento",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "emissao_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.nfse_emissao.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo_evento", sa.String(6), nullable=False),
        sa.Column("c_motivo", sa.SmallInteger(), nullable=False),
        sa.Column("x_motivo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ped_xml_b64", sa.Text(), nullable=True),
        sa.Column("evento_xml_b64", sa.Text(), nullable=True),
        sa.Column("erros", pg.JSONB(), nullable=True),
        _user_fk("created_by"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('enviando', 'registrado', 'rejeitado', 'incerto')",
            name=op.f("ck_nfse_evento_status"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_nfse_evento_emissao_id", "nfse_evento", ["emissao_id"], schema=SCHEMA)

    op.create_table(
        "nfse_chamada",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("company_id", UUID, nullable=True),
        sa.Column("emissao_id", UUID, nullable=True),
        sa.Column("operacao", sa.Text(), nullable=False),
        sa.Column("tp_amb", sa.SmallInteger(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("codigos", pg.JSONB(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("duracao_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_nfse_chamada_emissao_id", "nfse_chamada", ["emissao_id"], schema=SCHEMA)


def downgrade() -> None:
    for tabela in (
        "nfse_chamada",
        "nfse_evento",
        "nfse_emissao",
        "nfse_dps_contador",
        "nfse_modelo",
        "nfse_tomador",
        "company_fiscal",
    ):
        op.drop_table(tabela, schema=SCHEMA)
