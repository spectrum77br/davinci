from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    CadastroStatus,
    CadastroTipo,
    Marketplace,
    StoreStatus,
)


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    # Espelho do índice da migration 0324: um IP por empresa. Fica também no
    # modelo para o banco de teste (create_all) ter a mesma trava que produção.
    __table_args__ = (
        Index(
            "uq_companies_ip",
            text("lower(btrim(ip))"),
            unique=True,
            postgresql_where=text("ip IS NOT NULL AND btrim(ip) <> ''"),
        ),
        # Migration 0339 (op.f, mesmo nome no banco).
        CheckConstraint(
            "percentual_servico IS NULL OR (percentual_servico > 0 AND percentual_servico <= 100)",
            name="percentual_servico",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    razao_social: Mapped[str] = mapped_column(Text, nullable=False)
    apelido: Mapped[str] = mapped_column(Text, nullable=False)
    responsavel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Responsável da EMPRESA (nome livre). `store_info.cpf_name` é outro
    # campo: o responsável DAQUELA LOJA. Eduardo, 17/09: empresa sem loja
    # também precisa ter responsável.
    responsavel_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cnpj: Mapped[str | None] = mapped_column(String(14), unique=True, nullable=True)
    inscricao_estadual: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    contabilidade: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Porcentagem da empresa (Eduardo, 29/09/2026): o % PADRÃO das notas de
    # serviço (NFS-e) de percentual que ELA emite — 0.5 = 0,5%. A nota fixa sem
    # % próprio usa este (migration 0339).
    percentual_servico: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    # IP de saída da empresa nos marketplaces. Único entre empresas (índice
    # `uq_companies_ip`, migration 0324): dois CNPJs no mesmo IP é o que o
    # marketplace usa para ligar contas.
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O que o serviço do Mac confirmou no AdsPower (migration 0325). Pendente
    # = `ip` preenchido e diferente de `ip_adspower`.
    ip_adspower: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_adspower_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_adspower_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Proxy da empresa (migration 0332): com usuário/senha aqui, o serviço do
    # Mac grava o proxy inteiro nos perfis (os proxies novos têm senha por IP).
    # A senha fica cifrada; só admin vê, e só pelas rotas de proxy.
    proxy_tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_porta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_senha_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Perfis do AdsPower da empresa que nenhuma loja aponta (números).
    adspower_perfis_extras: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    # Sobe a cada mudança de IP/proxy: o resultado do serviço traz a versão
    # que aplicou, e uma versão velha não marca a nova como aplicada.
    proxy_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    @property
    def proxy_configurado(self) -> bool:
        """Tem o proxy inteiro (porta, usuário e senha) — só o sinal, nunca a senha."""
        return bool(self.proxy_usuario and self.proxy_senha_enc is not None and self.proxy_porta)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled_marketplaces: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text(
            "ARRAY['ml','shopee','amazon','aliexpress','temu','tiktok','shein',"
            "'magalu','carrefour','netshoes','site']::text[]"
        ),
        default=lambda: [
            "ml", "shopee", "amazon", "aliexpress",
            "temu", "tiktok", "shein", "magalu", "carrefour", "netshoes", "site",
        ],
    )


class Store(Base, TimestampMixin):
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("company_id", "marketplace", name="uq_stores_company_id_marketplace"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    marketplace: Mapped[Marketplace] = mapped_column(_enum(Marketplace, "marketplace"), nullable=False)
    apelido_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[StoreStatus] = mapped_column(
        _enum(StoreStatus, "store_status"),
        nullable=False,
        default=StoreStatus.PENDING,
        server_default=text("'pending'"),
    )
    integration_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=True)
    bling_store_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Cadastro(Base, TimestampMixin):
    __tablename__ = "cadastros"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[CadastroTipo] = mapped_column(_enum(CadastroTipo, "cadastro_tipo"), nullable=False)
    provedor: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsavel_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    codigo: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CadastroStatus] = mapped_column(
        _enum(CadastroStatus, "cadastro_status"),
        nullable=False,
        default=CadastroStatus.ACTIVE,
        server_default=text("'active'"),
    )
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_links: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )


class CadastroStore(Base):
    __tablename__ = "cadastros_stores"

    cadastro_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cadastros.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
