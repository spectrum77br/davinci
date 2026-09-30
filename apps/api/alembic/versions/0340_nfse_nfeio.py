"""NFS-e: o motor passa a ser a NFE.io (sai o gov.br direto)

29/09/2026: em vez de montar, assinar e mandar a DPS ao gov.br com o A1, o
DaVinci chama a API da NFE.io (o grupo já usa, com as 25 empresas
cadastradas). A tela e as regras ficam; muda o motor. Por isso:

- `company_fiscal`: entra a ligação com a NFE.io (`nfeio_*`, só leitura, vem
  do sincronizar; `nfeio_resumo` guarda só campos PERMITIDOS, nunca a senha da
  prefeitura), os códigos do serviço (6303 / 10.05 medidos nas notas reais) e
  `retencao_ir` ('auto' | 'sempre' | 'nunca'). Sai o que era só do DPS/gov.br
  (município, regime, tributos aproximados, série, IBS/CBS, certificado,
  convênio) — agora isso é do cadastro na NFE.io.
- `nfse_modelo`: `c_trib_nac`/`c_trib_mun` viram `city_service_code`/
  `federal_service_code` (opcionais; sobrepõem os da empresa).
- `nfse_emissao`: entram `provedor`, `nfeio_id`, `nfeio_external_id` (únicos),
  `nfeio_ambiente`, `flow_status`, `flow_message`, `check_code`, `rps_*` e
  `ir_retido`; status novo `processando` (a NFE.io recebeu e espera a
  prefeitura) no CHECK e no índice de nota viva no mês, que passa a ser por
  ambiente da empresa. Saem série/nº/id da DPS, `c_loc_emi`, `tp_amb`,
  `perfil_assinatura`, `dps_xml_b64` e `c_stat`.
- `nfse_dps_contador`: sai (a NFE.io numera).
- `nfse_chamada`: `tp_amb` vira opcional e entra `ambiente`.

Nenhuma nota saiu pelo motor antigo (0337–0339 nunca foram publicadas); as
linhas de teste locais são convertidas (tp_amb 2 → Development). Espelha
`app/models/nfse.py`.

Revision ID: 0340_nfse_nfeio
Revises: 0339_companies_percentual
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0340_nfse_nfeio"
down_revision: str | None = "0339_companies_percentual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
UUID = pg.UUID(as_uuid=True)

VIVOS_ANTES = "('enviando', 'emitida', 'incerta', 'cancelando')"
VIVOS = "('enviando', 'processando', 'emitida', 'incerta', 'cancelando')"
STATUS_ANTES = "('enviando', 'emitida', 'rejeitada', 'incerta', 'cancelando', 'cancelada')"
STATUS = "('enviando', 'processando', 'emitida', 'rejeitada', 'incerta', 'cancelando', 'cancelada')"

# Colunas do motor gov.br em company_fiscal: (nome, tipo, server_default, nullable).
_FISCAL_GOVBR: tuple[tuple[str, sa.types.TypeEngine, str | None, bool], ...] = (
    ("cmun_ibge", sa.String(7), None, True),
    ("municipio_nome", sa.Text(), None, True),
    ("inscricao_municipal", sa.Text(), None, True),
    ("informar_im", sa.Boolean(), "false", False),
    ("op_simp_nac", sa.SmallInteger(), None, True),
    ("reg_ap_trib_sn", sa.SmallInteger(), None, True),
    ("reg_esp_trib", sa.SmallInteger(), "0", False),
    ("tot_trib_modo", sa.Text(), None, True),
    ("p_tot_trib_sn", sa.Numeric(5, 2), None, True),
    ("p_tot_trib_fed", sa.Numeric(5, 2), None, True),
    ("p_tot_trib_est", sa.Numeric(5, 2), None, True),
    ("p_tot_trib_mun", sa.Numeric(5, 2), None, True),
    ("c_trib_nac", sa.String(6), "100501", False),
    ("c_trib_mun", sa.String(3), None, True),
    ("serie_dps", sa.Integer(), "1", False),
    ("ibscbs_ativo", sa.Boolean(), "false", False),
    ("ibscbs_c_ind_op", sa.String(6), "100301", False),
    ("ibscbs_cst", sa.String(3), "000", False),
    ("ibscbs_c_class_trib", sa.String(6), "000001", False),
    ("convenio_aderente", sa.Boolean(), None, True),
    ("convenio_json", pg.JSONB(), None, True),
    ("convenio_em", sa.DateTime(timezone=True), None, True),
)

_FISCAL_NFEIO: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("nfeio_company_id", sa.Text()),
    ("nfeio_ambiente", sa.Text()),
    ("nfeio_status_fiscal", sa.Text()),
    ("nfeio_regime", sa.Text()),
    ("nfeio_im", sa.Text()),
    ("nfeio_municipio", sa.Text()),
    ("nfeio_uf", sa.String(2)),
    ("nfeio_cert_status", sa.Text()),
    ("nfeio_cert_expira", sa.Date()),
    ("nfeio_resumo", pg.JSONB()),
    ("nfeio_sincronizado_em", sa.DateTime(timezone=True)),
)

_EMISSAO_NFEIO: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("nfeio_id", sa.Text()),
    ("nfeio_external_id", sa.Text()),
    ("flow_status", sa.Text()),
    ("flow_message", sa.Text()),
    ("check_code", sa.Text()),
    ("rps_serie", sa.Text()),
    ("rps_numero", sa.BigInteger()),
    ("ir_retido", sa.Numeric(15, 2)),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    # --- company_fiscal ------------------------------------------------------
    for nome, tipo in _FISCAL_NFEIO:
        op.add_column("company_fiscal", sa.Column(nome, tipo, nullable=True), schema=SCHEMA)
    op.add_column(
        "company_fiscal",
        sa.Column("city_service_code", sa.Text(), server_default="6303", nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "company_fiscal",
        sa.Column("federal_service_code", sa.Text(), server_default="10.05", nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "company_fiscal",
        sa.Column("retencao_ir", sa.Text(), server_default="auto", nullable=False),
        schema=SCHEMA,
    )
    # op.f(): o nome já vem pronto (sem ele a naming convention prefixa de novo).
    op.create_unique_constraint(
        op.f("uq_company_fiscal_nfeio_company_id"),
        "company_fiscal",
        ["nfeio_company_id"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_company_fiscal_retencao_ir"),
        "company_fiscal",
        "retencao_ir IN ('auto', 'sempre', 'nunca')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("fk_company_fiscal_certificado_id_company_certificates"),
        "company_fiscal",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("company_fiscal", "certificado_id", schema=SCHEMA)
    for nome, *_ in _FISCAL_GOVBR:
        op.drop_column("company_fiscal", nome, schema=SCHEMA)

    # --- nfse_modelo ------------------------------------------------------------
    op.add_column(
        "nfse_modelo", sa.Column("city_service_code", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "nfse_modelo", sa.Column("federal_service_code", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.drop_column("nfse_modelo", "c_trib_nac", schema=SCHEMA)
    op.drop_column("nfse_modelo", "c_trib_mun", schema=SCHEMA)

    # --- nfse_emissao -----------------------------------------------------------
    op.add_column(
        "nfse_emissao",
        sa.Column("provedor", sa.Text(), server_default="nfeio", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "nfse_emissao", sa.Column("nfeio_ambiente", sa.Text(), nullable=True), schema=SCHEMA
    )
    for nome, tipo in _EMISSAO_NFEIO:
        op.add_column("nfse_emissao", sa.Column(nome, tipo, nullable=True), schema=SCHEMA)
    # Linhas de teste do motor antigo (só no localhost): o ambiente vem do tpAmb.
    op.execute(
        "UPDATE davinci.nfse_emissao SET provedor = 'govbr',"
        " nfeio_ambiente = CASE WHEN tp_amb = 1 THEN 'Production' ELSE 'Development' END"
    )
    op.alter_column(
        "nfse_emissao", "nfeio_ambiente", existing_type=sa.Text(), nullable=False, schema=SCHEMA
    )
    op.create_unique_constraint(
        op.f("uq_nfse_emissao_nfeio_id"), "nfse_emissao", ["nfeio_id"], schema=SCHEMA
    )
    op.create_unique_constraint(
        op.f("uq_nfse_emissao_nfeio_external_id"),
        "nfse_emissao",
        ["nfeio_external_id"],
        schema=SCHEMA,
    )
    op.drop_constraint(op.f("ck_nfse_emissao_status"), "nfse_emissao", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_nfse_emissao_status"), "nfse_emissao", f"status IN {STATUS}", schema=SCHEMA
    )
    op.drop_index("uq_nfse_emissao_modelo_mes", table_name="nfse_emissao", schema=SCHEMA)
    op.create_index(
        "uq_nfse_emissao_modelo_mes",
        "nfse_emissao",
        ["modelo_id", "competencia", "nfeio_ambiente"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(f"modelo_id IS NOT NULL AND status IN {VIVOS}"),
    )
    op.drop_constraint(
        op.f("uq_nfse_emissao_company_id_tp_amb_c_loc_emi_serie_n_dps"),
        "nfse_emissao",
        type_="unique",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("uq_nfse_emissao_id_dps"), "nfse_emissao", type_="unique", schema=SCHEMA
    )
    for nome in (
        "tp_amb",
        "c_loc_emi",
        "serie",
        "n_dps",
        "id_dps",
        "perfil_assinatura",
        "dps_xml_b64",
        "c_stat",
    ):
        op.drop_column("nfse_emissao", nome, schema=SCHEMA)

    # --- nfse_dps_contador (a NFE.io numera) ------------------------------------
    op.drop_table("nfse_dps_contador", schema=SCHEMA)

    # --- nfse_chamada -------------------------------------------------------------
    op.alter_column(
        "nfse_chamada", "tp_amb", existing_type=sa.SmallInteger(), nullable=True, schema=SCHEMA
    )
    op.add_column("nfse_chamada", sa.Column("ambiente", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")

    # --- nfse_chamada -------------------------------------------------------------
    op.execute(
        "UPDATE davinci.nfse_chamada SET tp_amb = CASE WHEN ambiente = 'Production'"
        " THEN 1 ELSE 2 END WHERE tp_amb IS NULL"
    )
    op.drop_column("nfse_chamada", "ambiente", schema=SCHEMA)
    op.alter_column(
        "nfse_chamada", "tp_amb", existing_type=sa.SmallInteger(), nullable=False, schema=SCHEMA
    )

    # --- nfse_dps_contador ------------------------------------------------------------
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

    # --- nfse_emissao -----------------------------------------------------------
    op.add_column(
        "nfse_emissao", sa.Column("tp_amb", sa.SmallInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "nfse_emissao", sa.Column("c_loc_emi", sa.String(7), nullable=True), schema=SCHEMA
    )
    op.add_column("nfse_emissao", sa.Column("serie", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("nfse_emissao", sa.Column("n_dps", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("nfse_emissao", sa.Column("id_dps", sa.String(45), nullable=True), schema=SCHEMA)
    op.add_column(
        "nfse_emissao", sa.Column("perfil_assinatura", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column("nfse_emissao", sa.Column("dps_xml_b64", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("nfse_emissao", sa.Column("c_stat", sa.Text(), nullable=True), schema=SCHEMA)
    # Notas da NFE.io não têm DPS nossa: número sintético só pra voltar o NOT NULL.
    op.execute(
        "UPDATE davinci.nfse_emissao e SET"
        " tp_amb = CASE WHEN e.nfeio_ambiente = 'Production' THEN 1 ELSE 2 END,"
        " c_loc_emi = '', serie = 1,"
        " n_dps = n.rn, id_dps = 'NFEIO' || replace(e.id::text, '-', ''),"
        " status = CASE WHEN e.status = 'processando' THEN 'incerta' ELSE e.status END"
        " FROM (SELECT id, row_number() OVER (PARTITION BY company_id, nfeio_ambiente"
        " ORDER BY created_at) AS rn FROM davinci.nfse_emissao) n WHERE n.id = e.id"
    )
    for nome in ("tp_amb", "c_loc_emi", "serie", "n_dps", "id_dps"):
        op.alter_column("nfse_emissao", nome, nullable=False, schema=SCHEMA)
    op.create_unique_constraint(
        op.f("uq_nfse_emissao_id_dps"), "nfse_emissao", ["id_dps"], schema=SCHEMA
    )
    op.create_unique_constraint(
        op.f("uq_nfse_emissao_company_id_tp_amb_c_loc_emi_serie_n_dps"),
        "nfse_emissao",
        ["company_id", "tp_amb", "c_loc_emi", "serie", "n_dps"],
        schema=SCHEMA,
    )
    op.drop_index("uq_nfse_emissao_modelo_mes", table_name="nfse_emissao", schema=SCHEMA)
    op.create_index(
        "uq_nfse_emissao_modelo_mes",
        "nfse_emissao",
        ["modelo_id", "competencia", "tp_amb"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(f"modelo_id IS NOT NULL AND status IN {VIVOS_ANTES}"),
    )
    op.drop_constraint(op.f("ck_nfse_emissao_status"), "nfse_emissao", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_nfse_emissao_status"),
        "nfse_emissao",
        f"status IN {STATUS_ANTES}",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("uq_nfse_emissao_nfeio_external_id"), "nfse_emissao", type_="unique", schema=SCHEMA
    )
    op.drop_constraint(
        op.f("uq_nfse_emissao_nfeio_id"), "nfse_emissao", type_="unique", schema=SCHEMA
    )
    for nome, _tipo in _EMISSAO_NFEIO:
        op.drop_column("nfse_emissao", nome, schema=SCHEMA)
    op.drop_column("nfse_emissao", "nfeio_ambiente", schema=SCHEMA)
    op.drop_column("nfse_emissao", "provedor", schema=SCHEMA)

    # --- nfse_modelo ------------------------------------------------------------
    op.add_column(
        "nfse_modelo", sa.Column("c_trib_nac", sa.String(6), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "nfse_modelo", sa.Column("c_trib_mun", sa.String(3), nullable=True), schema=SCHEMA
    )
    op.drop_column("nfse_modelo", "federal_service_code", schema=SCHEMA)
    op.drop_column("nfse_modelo", "city_service_code", schema=SCHEMA)

    # --- company_fiscal ------------------------------------------------------
    for nome, tipo, padrao, nulo in _FISCAL_GOVBR:
        op.add_column(
            "company_fiscal",
            sa.Column(nome, tipo, server_default=padrao, nullable=nulo),
            schema=SCHEMA,
        )
    op.add_column(
        "company_fiscal",
        sa.Column(
            "certificado_id",
            UUID,
            sa.ForeignKey(f"{SCHEMA}.company_certificates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_company_fiscal_retencao_ir"), "company_fiscal", type_="check", schema=SCHEMA
    )
    op.drop_constraint(
        op.f("uq_company_fiscal_nfeio_company_id"),
        "company_fiscal",
        type_="unique",
        schema=SCHEMA,
    )
    for nome in ("retencao_ir", "federal_service_code", "city_service_code"):
        op.drop_column("company_fiscal", nome, schema=SCHEMA)
    for nome, _tipo in _FISCAL_NFEIO:
        op.drop_column("company_fiscal", nome, schema=SCHEMA)
