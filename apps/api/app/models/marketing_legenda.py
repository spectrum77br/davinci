"""Biblioteca de legendas do robô de postagem (Eduardo, 16/09/2026).

"com base no produto do criativo e se não, cai num padrão da marca… também
tem que sair quando o post sai automático" — hoje o modal pré-preenche a
legenda com o `roteiro` do criativo, que é prompt de geração do vídeo em
inglês, e dos dois Reels publicados um saiu SEM legenda nenhuma.

Uma linha aqui é UMA VARIAÇÃO de legenda. A cascata que resolve o texto é
`legenda da postagem → legenda do criativo → modelo do PRODUTO → modelo da
MARCA → recusa`, e ela roda UMA vez, quando a postagem é criada: o texto
renderizado vira SNAPSHOT em `marketing_postagens.legenda`. Mexer na
biblioteca depois não reescreve post nenhum — o que o operador viu no modal
é byte a byte o que foi pro Instagram.

`texto` é Jinja renderizado em SANDBOX (`{{ marca }}`, `{{ produto }}`,
`{{ whatsapp }}`, `{{ email_sac }}`, `{{ instagram }}`) — o mesmo caminho dos
padrões de e-mail da marca, porque quem escreve a variação é o operador e
template de operador não pode virar execução de código.

Não há unicidade por (marca, produto): VÁRIAS variações da mesma chave é o
ponto. O publicador faz rodízio (a menos usada recentemente naquela conta)
porque mesmo texto + mesmo formato + API + intervalo cravado é exatamente o
retrato que o detector de automação do Instagram procura.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MarketingLegendaModelo(Base, TimestampMixin):
    __tablename__ = "marketing_legenda_modelos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # CASCADE (e NOT NULL): toda variação é de uma marca e só faz sentido
    # dentro dela — sem a marca não há `{{ whatsapp }}`, `{{ email_sac }}`
    # nem conta pra postar. Diferente do histórico de postagens, aqui não há
    # o que preservar: é cadastro, não registro do que já saiu.
    marca_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = padrão da MARCA (o degrau de baixo da cascata); preenchido =
    # legenda daquele produto. SET NULL em vez de CASCADE porque apagar o
    # produto não pode levar junto um texto escrito à mão: a variação
    # sobrevive rebaixada a padrão da marca, e o operador decide.
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    # Desligar a variação sem apagá-la: o rodízio ignora, mas as postagens
    # antigas continuam apontando pra ela (`legenda_modelo_id`).
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# Os dois índices da busca da cascata — declarados no model (create_all dos
# testes) E na migration 0282, como manda o precedente de models/marca.py.
# O composto atende o degrau do PRODUTO (marca + produto exato).
Index(
    "ix_marketing_legenda_modelos_marca_id_product_id",
    MarketingLegendaModelo.marca_id,
    MarketingLegendaModelo.product_id,
)
# O parcial atende o degrau da MARCA, que é o caminho comum: quase todo
# criativo cai nele (só 2 dos 41 têm SKU que casa exato) e o rodízio varre
# essa lista inteira a cada postagem. NULL não é indexável de forma útil no
# composto acima — por isso o índice próprio.
Index(
    "ix_marketing_legenda_modelos_marca_padrao",
    MarketingLegendaModelo.marca_id,
    postgresql_where=text("product_id IS NULL"),
)
