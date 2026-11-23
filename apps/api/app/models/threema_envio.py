"""Envios do Threema (Cadastros › Envios Threema, tarefa 16).

`threema_envio_registro` (migration 0505): uma linha por destinatário e por
tentativa de envio, gravada por `ThreemaClient.send_to_all` (services/
threema.py) através de `services/threema_registro.py`. Diz quando saiu, de
qual envio (`envio_chave`, a chave do catálogo `services/threema_catalogo.K`),
por qual remetente, para quem e se chegou. Uma chamada de envio é um LOTE
(`lote_id`): a tela do Histórico agrupa por ele.

  • `envio_chave` sem FK: o catálogo mora no código (e a linha do robô do Mac
    pode trazer chave desconhecida). NULL = ponto de envio sem marca — em
    produção quer dizer "envio que o levantamento não pegou".
  • Nunca guarda o segredo nem o texto inteiro: só a 1ª linha (`titulo`) e o
    tamanho (`bytes`).
  • Fica FORA do Histórico de alterações (`historico/sql.EXCLUIDAS`): é
    escrito pela máquina e já é, ele mesmo, a trilha. A limpeza diária
    (`worker.alerts_cleanup`) apaga o que passou de 180 dias.

O CADASTRO (migration 0506, C3) — quem recebe cada envio, decidido no banco:

  • `threema_envio`: uma linha por envio do catálogo. As colunas do código
    (painel, nome, descrição, frequência, onde roda) são reescritas pela
    sincronização (`services/threema_envios.sincronizar`) e servem só para
    ler em SQL — a API usa o catálogo do código. As da pessoa: `ativo`
    (NULL = segue o interruptor de hoje), quem mudou e quando, observação.
    `semeado_em` NULL = ainda usa a configuração antiga (legado). Chave que
    saiu do código ganha `descontinuado_em` e nunca é apagada.
  • `threema_contato`: quem recebe sem login no DaVinci (contatos externos).
  • `threema_grupo` + `threema_grupo_membro`: grupos de pessoas e contatos.
  • `threema_envio_destinatario`: quem recebe cada envio, como REFERÊNCIA a
    usuário, grupo ou contato (o ID do Threema é lido na hora, do cadastro de
    Usuários). `logistica_status_id` preenchido = lista própria de uma regra
    de status (item 1); vazio = a lista padrão do envio.
  • `threema_cadastro_estado`: uma linha só (id=1). `usar_legado` é o botão de
    emergência: liga e TODOS os envios voltam à configuração antiga no
    próximo envio, sem recriar container.

Todas as tabelas do Cadastro ficam COM o gatilho do Histórico: quem mudou
quem recebe, ligou ou desligou é exatamente o que o Histórico mostra.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ORIGENS_REGISTRO = ("automatico", "manual", "teste")
VIAS_REGISTRO = ("cadastro", "legado", "cache", "forcado", "env")
ONDE_RODA_REGISTRO = ("servidor", "mac")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


class ThreemaEnvioRegistro(Base):
    __tablename__ = "threema_envio_registro"
    __table_args__ = (
        CheckConstraint(_in("origem", ORIGENS_REGISTRO), name="origem"),
        CheckConstraint(f"via IS NULL OR {_in('via', VIAS_REGISTRO)}", name="via"),
        CheckConstraint(_in("onde_roda", ONDE_RODA_REGISTRO), name="onde_roda"),
        Index("ix_threema_envio_registro_chave_criado", "envio_chave", text("criado_em DESC")),
        Index("ix_threema_envio_registro_criado", text("criado_em DESC")),
        Index(
            "ix_threema_envio_registro_falhas",
            text("criado_em DESC"),
            postgresql_where=text("NOT ok"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Uma chamada de envio (`send_to_all`) = um lote.
    lote_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    envio_chave: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Item 1: o id da regra de status da Logística.
    escopo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # O `contexto` pedido ao ThreemaClient e o canal efetivo de credenciais.
    contexto: Mapped[str | None] = mapped_column(String(20), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    onde_roda: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'servidor'")
    )
    origem: Mapped[str] = mapped_column(String(12), nullable=False)
    # De onde veio a lista de quem recebe (fases seguintes). NULL = antes do
    # Cadastro (cada ponto monta a sua lista como sempre montou).
    via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ator_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ator_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    req_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # NULL quando o envio nem chegou a tentar ninguém (erro de configuração).
    destinatario_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    destinatario_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Sem FK: a pessoa pode sair do cadastro e o registro continua.
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    contato_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    erro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Só a 1ª linha da mensagem — nunca o texto inteiro.
    titulo: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host: Mapped[str | None] = mapped_column(String(60), nullable=True)


# ─────────────────────────────────────────────────────────── o Cadastro (0505)

FREQUENCIAS_ENVIO = ("evento", "horario", "manual", "misto")
ONDE_RODA_ENVIO = ("servidor", "mac", "externo")
ORIGENS_CONTATO = ("tela", "migracao", "semeadura")
TIPOS_MEMBRO = ("usuario", "contato")
TIPOS_DESTINATARIO = ("usuario", "grupo", "contato")
# Mesmo formato do `services/threema._THREEMA_ID` (8 letras/números, ou `*` +
# 7 para ID de Gateway).
THREEMA_ID_REGEX = r"^([A-Z0-9]{8}|\*[A-Z0-9]{7})$"
# Regra de status ausente = lista padrão do envio (índice único por expressão).
SEM_REGRA = "00000000-0000-0000-0000-000000000000"


class ThreemaEnvio(Base, TimestampMixin):
    __tablename__ = "threema_envio"
    __table_args__ = (
        CheckConstraint(_in("frequencia_tipo", FREQUENCIAS_ENVIO), name="frequencia_tipo"),
        CheckConstraint(_in("onde_roda", ONDE_RODA_ENVIO), name="onde_roda"),
    )

    # A chave do catálogo (`services/threema_catalogo.K`). Nunca muda.
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    # ── do código (reescritas pela sincronização; só para ler em SQL)
    painel: Mapped[str] = mapped_column(String(40), nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    frequencia_tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    frequencia_texto: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    onde_roda: Mapped[str] = mapped_column(String(10), nullable=False)
    # ── da pessoa (só admin altera; nunca tocadas pela sincronização)
    # NULL = segue o interruptor de hoje (env, modo do robô…).
    ativo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ativo_alterado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ativo_alterado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── controle
    # NULL = sem semente: o envio segue a configuração antiga (legado).
    semeado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A chave saiu do código: a linha fica (o registro e o Histórico citam).
    descontinuado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ThreemaContato(Base, TimestampMixin):
    """Quem recebe aviso sem login no DaVinci (ex.: "Roma")."""

    __tablename__ = "threema_contato"
    __table_args__ = (
        CheckConstraint(f"threema_id ~ '{THREEMA_ID_REGEX}'", name="threema_id"),
        CheckConstraint(_in("origem", ORIGENS_CONTATO), name="origem"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    nome: Mapped[str] = mapped_column(String(80), nullable=False)
    threema_id: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # tela | migracao (cópia da linha `diretorio`, 0507) | semeadura
    origem: Mapped[str] = mapped_column(String(12), nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThreemaGrupo(Base, TimestampMixin):
    __tablename__ = "threema_grupo"
    __table_args__ = (Index("ix_threema_grupo_nome_lower", text("lower(nome)"), unique=True),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    nome: Mapped[str] = mapped_column(String(80), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThreemaGrupoMembro(Base):
    __tablename__ = "threema_grupo_membro"
    __table_args__ = (
        CheckConstraint(_in("tipo", TIPOS_MEMBRO), name="tipo"),
        CheckConstraint(
            "num_nonnulls(user_id, contato_id) = 1 "
            "AND (tipo = 'usuario') = (user_id IS NOT NULL) "
            "AND (tipo = 'contato') = (contato_id IS NOT NULL)",
            name="referencia",
        ),
        Index(
            "uq_threema_grupo_membro_usuario",
            "grupo_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_threema_grupo_membro_contato",
            "grupo_id",
            "contato_id",
            unique=True,
            postgresql_where=text("contato_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    grupo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("threema_grupo.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contato_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("threema_contato.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Ordem de quem recebe (a semente grava na ordem da lista de hoje).
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ThreemaEnvioDestinatario(Base):
    __tablename__ = "threema_envio_destinatario"
    __table_args__ = (
        CheckConstraint(_in("tipo", TIPOS_DESTINATARIO), name="tipo"),
        CheckConstraint(
            "num_nonnulls(user_id, grupo_id, contato_id) = 1 "
            "AND (tipo = 'usuario') = (user_id IS NOT NULL) "
            "AND (tipo = 'grupo') = (grupo_id IS NOT NULL) "
            "AND (tipo = 'contato') = (contato_id IS NOT NULL)",
            name="referencia",
        ),
        Index(
            "uq_threema_envio_destinatario_usuario",
            "envio_chave",
            text(f"coalesce(logistica_status_id, '{SEM_REGRA}'::uuid)"),
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_threema_envio_destinatario_grupo",
            "envio_chave",
            text(f"coalesce(logistica_status_id, '{SEM_REGRA}'::uuid)"),
            "grupo_id",
            unique=True,
            postgresql_where=text("grupo_id IS NOT NULL"),
        ),
        Index(
            "uq_threema_envio_destinatario_contato",
            "envio_chave",
            text(f"coalesce(logistica_status_id, '{SEM_REGRA}'::uuid)"),
            "contato_id",
            unique=True,
            postgresql_where=text("contato_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    envio_chave: Mapped[str] = mapped_column(
        String(60),
        ForeignKey("threema_envio.chave", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    # Só o item 1 (mensagem da regra de status): a lista própria da regra.
    logistica_status_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("logistica_status.id", ondelete="CASCADE"),
        nullable=True,
    )
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # RESTRICT: grupo em uso não some calado (a tela responde 409 com a lista).
    grupo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("threema_grupo.id", ondelete="RESTRICT"),
        nullable=True,
    )
    contato_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("threema_contato.id", ondelete="CASCADE"),
        nullable=True,
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    criado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ThreemaCadastroEstado(Base):
    """Uma linha só (id=1, a migration insere). `usar_legado=True` = botão de
    emergência: todos os envios voltam à configuração antiga no próximo
    envio, sem recriar container. O env THREEMA_CADASTRO_ATIVO=false é o piso
    (vale mesmo com o banco fora)."""

    __tablename__ = "threema_cadastro_estado"
    __table_args__ = (CheckConstraint("id = 1", name="uma_linha"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    usar_legado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    alterado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    alterado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
