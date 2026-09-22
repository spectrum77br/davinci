from uuid import UUID, uuid4

from sqlalchemy import BigInteger, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImagemPublica(Base, TimestampMixin):
    """Imagem guardada no banco e servida por link ABERTO (sem login).

    Vinicius, 22/09/2026: "sobe essa imagem no servidor e me passa o link dela…
    no banco de dados". Serve pra figura que precisa ser vista fora do painel —
    logo de transportadora num aviso, imagem colada numa mensagem ao cliente,
    cartão que o robô manda. O painel inteiro exige login, então um link que só
    abre logado não serviria pra isso.

    Aberto quer dizer aberto: quem tiver o link vê a imagem. Só entra aqui o que
    pode ser público (logo, ícone, arte) — nunca foto de devolução, documento ou
    etiqueta com dado de cliente. O id é UUID, então não dá pra varrer.

    Blob no próprio Postgres, como o `chamado_anexo` e o `devolucao_anexo`: o
    servidor não tem pasta de upload pra app (e desde a blindagem de 22/09
    ninguém escreve arquivo nele por fora do deploy).
    """

    __tablename__ = "imagem_publica"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nome só pra gente saber o que é (não entra na URL).
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False, default="image/png")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
