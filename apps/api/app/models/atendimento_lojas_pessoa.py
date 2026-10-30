"""As lojas de cada pessoa no /atendimento (30/10/2026, migration 0440).

O Eduardo, 30/10: "será separado as lojas no atendimento por pessoas então x
usuario vai poder ver x lojas e outros não isso também se aplica pra caixa de
e-mail das lojas delas" — com a planilha "Separação de contas" (Vendedor 1 =
londres, 2 = israel, 3 = marrocos). Vale também para a Garantia ("eles só vão
poder ver das lojas deles").

A conta da planilha é PLATAFORMA + CONTA (a Barbosa da Shopee é do Vendedor 1,
a do ML do 2 e a da Temu do 3), e no DaVinci ela aparece por três chaves:

  • "f:<store_info.id>" — a ficha da loja (Lojas): os e-mails que a ponte
    ligou só a ela, os pedidos do Bling (`bling_store_id`) e a Garantia;
  • "i:<integrations.id>" — a integração: o chat da API e os e-mails dela;
  • "c:<atendimento_canais.id>" — o canal do ROBÔ (Temu/AliExpress, sem
    integração).

A tela grava a loja inteira (as chaves que andam juntas) e o filtro de e-mail
compara a chave do e-mail ("f:"/"i:", `indice.colunas_do_selo`) com estas.
Sem linha nenhuma, a pessoa que não é admin não vê loja nenhuma no
atendimento (`services/atendimento/escopo.py`); admin vê tudo e não precisa
de linha.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# "i:"/"f:"/"c:" + o uuid minúsculo (36). O mesmo formato das chaves do e-mail.
PADRAO_CHAVE_LOJA = r"^[ifc]:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class AtendimentoLojaPessoa(Base):
    __tablename__ = "atendimento_lojas_pessoa"
    __table_args__ = (CheckConstraint(f"chave ~ '{PADRAO_CHAVE_LOJA}'", name="chave"),)

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
