"""Denúncia — cópia, no DaVinci, do sistema de Fiscalização da MAKISA.

Vinicius, 30/09/2026: o sistema de Fiscalização (Flask + SQLite, feito no
projeto "Fiscal" do Claude da Makisa) sai do Hostinger e passa a rodar no
próprio Mac mini da Makisa, junto do robô que varre os marketplaces e faz as
denúncias. O DaVinci não alcança o mini (ele está atrás do roteador do
escritório), então é o mini que MANDA: o `enviar_ao_davinci.py` roda a cada
5 min, lê o banco do sistema em modo só-leitura e posta aqui o que mudou.

Só três telas interessam (Anúncios, Denúncias, Casos) — por isso só as
tabelas que elas mostram vêm pra cá. O DaVinci só consulta; quem muda
status, abre caso etc. continua sendo o sistema do mini.

Cada tabela guarda a linha original inteira em `dados` (JSONB) e repete em
colunas próprias só o que a tela filtra, ordena ou cruza. Assim uma coluna
nova no sistema do mini chega sem migration: aparece em `dados` e a tela
passa a mostrar quando alguém quiser.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class _Espelho:
    """Colunas comuns: a linha inteira como veio do mini + quando chegou."""

    dados: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recebido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DenunciaAnuncio(_Espelho, Base):
    """Anúncio de marketplace que a varredura viu (id = id no marketplace)."""

    __tablename__ = "denuncia_anuncios"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    marketplace: Mapped[str | None] = mapped_column(Text)
    shop_id: Mapped[str | None] = mapped_column(Text)
    loja: Mapped[str | None] = mapped_column(Text)
    titulo: Mapped[str | None] = mapped_column(Text)
    grupo: Mapped[str | None] = mapped_column(Text)
    escopo: Mapped[str | None] = mapped_column(Text)
    situacao: Mapped[str | None] = mapped_column(Text)
    propria: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    vendas: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    visto_primeiro: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_denuncia_anuncios_shop_id", "shop_id"),)


class DenunciaLoja(_Espelho, Base):
    """Vendedor (shop_id do marketplace)."""

    __tablename__ = "denuncia_lojas"

    shop_id: Mapped[str] = mapped_column(Text, primary_key=True)
    marketplace: Mapped[str | None] = mapped_column(Text)
    nome: Mapped[str | None] = mapped_column(Text)
    propria: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class DenunciaDenuncia(_Espelho, Base):
    """Uma linha por anúncio denunciado. Várias linhas com o mesmo canal +
    protocolo são UMA denúncia que cobriu vários anúncios (a tela junta)."""

    __tablename__ = "denuncia_denuncias"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    anuncio_id: Mapped[str | None] = mapped_column(Text)
    canal: Mapped[str | None] = mapped_column(Text)
    protocolo: Mapped[str | None] = mapped_column(Text)
    data: Mapped[str | None] = mapped_column(Text)
    situacao: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str | None] = mapped_column(Text)
    prazo: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_denuncia_denuncias_anuncio_id", "anuncio_id"),
        Index("ix_denuncia_denuncias_canal_protocolo", "canal", "protocolo"),
    )


class DenunciaCaso(_Espelho, Base):
    """Caso jurídico (CASO-00N)."""

    __tablename__ = "denuncia_casos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str | None] = mapped_column(Text)
    anuncio_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)


class DenunciaCompra(_Espelho, Base):
    """Compra de prova (vai dentro do caso)."""

    __tablename__ = "denuncia_compras"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    anuncio_id: Mapped[str | None] = mapped_column(Text)
    caso_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(Text)


class DenunciaProva(_Espelho, Base):
    """Print/PDF/vídeo ligado a anúncio, denúncia ou caso. O arquivo chega
    depois da linha (`PUT /api/denuncia/sync/provas/{id}/arquivo`);
    `arquivo_local` fica nulo até lá e a tela mostra "arquivo ainda não
    chegou"."""

    __tablename__ = "denuncia_provas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    anuncio_id: Mapped[str | None] = mapped_column(Text)
    caso_id: Mapped[int | None] = mapped_column(BigInteger)
    denuncia_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    tamanho: Mapped[int | None] = mapped_column(BigInteger)
    arquivo_local: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_denuncia_provas_anuncio_id", "anuncio_id"),)


class DenunciaVerificacao(_Espelho, Base):
    """Cada checagem "o anúncio ainda está no ar?"."""

    __tablename__ = "denuncia_verificacoes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    anuncio_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_denuncia_verificacoes_anuncio_id", "anuncio_id"),)


class DenunciaRemetente(Base):
    """Quem pode mandar a cópia (o Mac mini da Makisa). Só o sha256 do token
    fica aqui — o token mora em `~/.davinci_denuncia.json` no mini. Guarda
    também o último envio, pra tela dizer "cópia de há 3 min"."""

    __tablename__ = "denuncia_remetentes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ultimo_envio_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
