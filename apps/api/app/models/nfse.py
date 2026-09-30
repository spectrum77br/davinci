"""NFS-e — aba NF Faturador › Emissão de Serviço (28/09/2026; motor NFE.io desde 29/09).

Eduardo: "quero criar no davinci uma aba nova emissão de serviço [...] nota de
serviço de intermediação", com tomador do grupo ou de fora, "todo mês, as
mesmas". Quem monta, assina e manda à prefeitura é a NFE.io (API REST, uma
chave para as 25 empresas da conta). Por isso:

- `CompanyFiscal`: a ligação da empresa com a NFE.io (id, ambiente, situação,
  regime, certificado — só leitura, vem do sincronizar) e o que é nosso
  (códigos do serviço, NBS, regra de retenção de IR). Tabela à parte pra não
  cair na senha extra da tela Empresas.
- `NfseTomador` / `NfseModelo`: quem recebe e as notas que se repetem todo mês.
- `NfseEmissao`: uma linha por nota que o DaVinci mandou, com o `externalId`
  (idempotência na NFE.io) e o que voltou (`*_b64`, que o Histórico mascara).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Quem ainda pode virar nota: bloqueia emitir o mesmo modelo 2x no mês.
# 'processando' (29/09): a NFE.io recebeu e espera a prefeitura.
STATUS_VIVOS = ("enviando", "processando", "emitida", "incerta", "cancelando")
STATUS_EMISSAO = (
    "enviando",
    "processando",
    "emitida",
    "rejeitada",
    "incerta",
    "cancelando",
    "cancelada",
)
# O que a rotina de conferência (e a tela, depois de enviar) ainda pergunta à NFE.io.
STATUS_EM_ANDAMENTO = ("enviando", "processando", "incerta", "cancelando")
RETENCAO_IR = ("auto", "sempre", "nunca")


class CompanyFiscal(Base, TimestampMixin):
    __tablename__ = "company_fiscal"
    __table_args__ = (
        CheckConstraint("retencao_ir IN ('auto', 'sempre', 'nunca')", name="retencao_ir"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    # --- a empresa na NFE.io (só leitura: vem do "Sincronizar"/"Atualizar") ---
    # 24 hex (a maioria) ou 32 hex (criadas pela API v2).
    nfeio_company_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # 'Production' (nota real) | 'Development' (simulada) | 'Staging'.
    nfeio_ambiente: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'Active' | 'CityNotSupported' | 'Pending' ...
    nfeio_status_fiscal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'SimplesNacional' | 'LucroPresumido' | 'LucroReal' | None (manda na regra do IR).
    nfeio_regime: Mapped[str | None] = mapped_column(Text, nullable=True)
    nfeio_im: Mapped[str | None] = mapped_column(Text, nullable=True)
    nfeio_municipio: Mapped[str | None] = mapped_column(Text, nullable=True)
    nfeio_uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    nfeio_cert_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    nfeio_cert_expira: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Lista PERMITIDA de campos da empresa e das Inscrições Municipais (nunca
    # login/senha da prefeitura) + a última nota que a NFE.io tem dela.
    nfeio_resumo: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    nfeio_sincronizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- o que é nosso ---
    # Padrão medido nas 137 notas reais: 6303 / 10.05 (intermediação).
    city_service_code: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default="6303"
    )
    federal_service_code: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default="10.05"
    )
    c_nbs: Mapped[str | None] = mapped_column(String(9), nullable=True, server_default="102010000")
    # IR retido (1,5%): 'auto' segue a regra (Lucro Presumido + tomador PJ,
    # dispensa até R$ 10,00); 'sempre'/'nunca' é a contabilidade sobrepondo.
    retencao_ir: Mapped[str] = mapped_column(Text, nullable=False, server_default="auto")
    fone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NfseTomador(Base, TimestampMixin):
    __tablename__ = "nfse_tomador"
    __table_args__ = (
        CheckConstraint("tipo IN ('grupo', 'externo')", name="tipo"),
        CheckConstraint(
            "(tipo = 'grupo' AND company_id IS NOT NULL)"
            " OR (tipo = 'externo' AND documento IS NOT NULL AND nome IS NOT NULL)",
            name="dados",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # Do grupo: CNPJ e razão social saem da empresa na hora de emitir.
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    # De fora: CNPJ (14) ou CPF (11), só dígitos.
    documento: Mapped[str | None] = mapped_column(String(14), nullable=True)
    nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    fone: Mapped[str | None] = mapped_column(Text, nullable=True)
    cep: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cmun_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    logradouro: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    complemento: Mapped[str | None] = mapped_column(Text, nullable=True)
    bairro: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NfseModelo(Base, TimestampMixin):
    """Uma nota que se repete todo mês: prestador → tomador, descrição e valor.

    29/09 (Eduardo: "quero emitir uma nota de serviço de 0,5%"): o valor pode
    ser fixo ou um percentual sobre uma base que a pessoa digita na hora de
    emitir (base R$ 200.000,00 × 0,5% = nota de R$ 1.000,00).
    """

    __tablename__ = "nfse_modelo"
    __table_args__ = (
        CheckConstraint("tipo_valor IN ('fixo', 'percentual')", name="tipo_valor"),
        # 0339: a nota fixa de percentual pode ficar sem % (usa a da empresa).
        CheckConstraint(
            "(tipo_valor = 'fixo' AND valor IS NOT NULL) OR tipo_valor = 'percentual'",
            name="valor_tipo",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tomador_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("nfse_tomador.id", ondelete="RESTRICT"), nullable=False
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    # Aceita {mes}, {ano}, {competencia} ("09/2026"), {percentual} ("0,5%") e
    # {base} ("R$ 200.000,00").
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    # 'fixo': `valor` · 'percentual': `percentual` (0.5 = 0,5%) sobre a base
    # digitada na hora; `base_padrao` só sugere a base. `percentual` NULL no
    # percentual = usa `companies.percentual_servico` do prestador (0339).
    tipo_valor: Mapped[str] = mapped_column(Text, nullable=False, server_default="fixo")
    valor: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    percentual: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    base_padrao: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # Preenchidos, sobrepõem os códigos da empresa (company_fiscal).
    city_service_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    federal_service_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    c_nbs: Mapped[str | None] = mapped_column(String(9), nullable=True)
    inf_comp: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NfseEmissao(Base, TimestampMixin):
    __tablename__ = "nfse_emissao"
    __table_args__ = (
        CheckConstraint(
            "status IN ('enviando', 'processando', 'emitida', 'rejeitada', 'incerta',"
            " 'cancelando', 'cancelada')",
            name="status",
        ),
        # Um modelo não sai 2x no mês (por ambiente: a nota simulada de teste
        # não segura a real).
        Index(
            "uq_nfse_emissao_modelo_mes",
            "modelo_id",
            "competencia",
            "nfeio_ambiente",
            unique=True,
            postgresql_where=text(
                "modelo_id IS NOT NULL"
                " AND status IN ('enviando', 'processando', 'emitida', 'incerta', 'cancelando')"
            ),
        ),
        Index("ix_nfse_emissao_competencia", "competencia"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tomador_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("nfse_tomador.id", ondelete="SET NULL"), nullable=True
    )
    modelo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("nfse_modelo.id", ondelete="SET NULL"), nullable=True
    )
    competencia: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    valor_servico: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # Nota de percentual: a conta que deu o valor (valor = base × percentual ÷ 100).
    base_calculo: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    percentual: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    # --- NFE.io ---
    provedor: Mapped[str] = mapped_column(Text, nullable=False, server_default="nfeio")
    nfeio_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # "dv-<id sem hífens>-<tentativa>": a NFE.io recusa externalId repetido, e
    # é por ele que se descobre se um envio sem resposta virou nota.
    nfeio_external_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # Ambiente da empresa na hora de emitir ('Production' = nota real).
    nfeio_ambiente: Mapped[str] = mapped_column(Text, nullable=False)
    flow_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    flow_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    check_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    rps_serie: Mapped[str | None] = mapped_column(Text, nullable=True)
    rps_numero: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ir_retido: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # O que voltou da prefeitura (via NFE.io). Chave de acesso só no Emissor Nacional.
    chave_acesso: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    n_nfse: Mapped[str | None] = mapped_column(Text, nullable=True)
    dh_emi: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dh_proc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    v_bc: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    p_aliq_aplic: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    v_issqn: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    v_liq: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # Prestador/tomador/serviço como estavam na hora de emitir (inclui o id da
    # empresa na NFE.io e o JSON enviado, sem chave).
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    nfse_xml_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    alertas: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    erros: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    emitido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NfseEvento(Base):
    """Pedido de cancelamento (101101). A API da NFE.io não recebe motivo: o
    motivo digitado fica só aqui."""

    __tablename__ = "nfse_evento"
    __table_args__ = (
        CheckConstraint(
            "status IN ('enviando', 'registrado', 'rejeitado', 'incerto')", name="status"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    emissao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("nfse_emissao.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tipo_evento: Mapped[str] = mapped_column(String(6), nullable=False)
    c_motivo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    x_motivo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ped_xml_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    evento_xml_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    erros: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NfseChamada(Base):
    """Log de cada ida à NFE.io (sem chave, sem corpo, sem dado sensível)."""

    __tablename__ = "nfse_chamada"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    emissao_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    operacao: Mapped[str] = mapped_column(Text, nullable=False)
    # Só das chamadas antigas ao gov.br (1 produção, 2 restrita).
    tp_amb: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Ambiente da empresa na NFE.io ('Production' | 'Development' ...).
    ambiente: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codigos: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    duracao_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
