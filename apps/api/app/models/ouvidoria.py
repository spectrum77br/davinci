"""Ouvidoria › Robôs — catálogo dos robôs, suas rodadas e o que eles encontraram.

Vinicius, 21/09/2026: uma seção só pra enxergar o que os robôs da casa estão
fazendo. Cada robô (vigia de importação, vigia de credenciais, …) tem UMA
linha em `ouvidoria_robos` com o modo (ligado / silencioso / desligado), a
última rodada e quem ele avisa; cada execução vira uma linha em
`ouvidoria_rodadas` (com o resumo e os contadores); e cada problema que o
robô viu vira uma `ouvidoria_ocorrencias`, que ele mesmo re-vê a cada rodada
e fecha sozinho quando o problema some — ou uma pessoa marca como tratada.

O primeiro morador é o vigia de importação, que antes tinha a tabela própria
`vigia_importacao` (migration 0230) — a 0300 copia as linhas pra cá e derruba
a tabela antiga. Regras de negócio (idempotência, re-aviso, fechamento) ficam
em `services/ouvidoria.py`; aqui é só o formato.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Modo do robô. `ligado` roda, registra e avisa; `silencioso` roda e registra
# mas não manda Threema; `desligado` nem roda (o tick do worker retorna sem
# gravar rodada).
MODOS = ("ligado", "silencioso", "desligado")
# Gravidade da ocorrência — o que a coluna Status mostra enquanto aberta.
# `robo_segurou` = o robô já protegeu (ex.: segurou o pedido no Bling), falta
# alguém decidir; `info` = só registro, ninguém precisa agir.
SEVERIDADES = ("urgente", "pessoa", "baixa", "robo_segurou", "info")
# Como a ocorrência fechou: `sumiu` = o robô parou de ver o problema;
# `tratada` / `ignorada` = uma pessoa marcou. Ignorada NUNCA reabre sozinha.
FECHAMENTOS = ("sumiu", "tratada", "ignorada")
# `fechada_por` quando foi o robô (fechamento `sumiu`).
FECHADA_PELO_ROBO = "robô"


class OuvidoriaRobo(Base, TimestampMixin):
    """Um robô cadastrado. A chave é o nome estável no código
    (`services/ouvidoria.ROBOS`); nome/descrição/cadência vêm do catálogo a
    cada `sincronizar_catalogo`, o resto (modo, destinatários, config) é o
    que a pessoa mexeu na tela e o catálogo NÃO sobrescreve."""

    __tablename__ = "ouvidoria_robos"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pedidos / contas / devolucoes / estoque / interno — só pra agrupar na tela.
    area: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cadencia_texto: Mapped[str | None] = mapped_column(String(60), nullable=True)
    plataformas: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    modo: Mapped[str] = mapped_column(
        String(12), nullable=False, default="ligado", server_default="ligado"
    )
    modo_alterado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    modo_alterado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Arquivado = sai da LISTA do painel e nada mais. Vinicius, 22/09/2026:
    # "uma lixeira pra tirar o robô daqui, não pra excluir o robô". Ele decidiu
    # que arquivar não mexe no modo: robô arquivado que está ligado continua
    # rodando, registrando ocorrência e avisando no Threema igual. Por isso o
    # painel guarda quem arquivou e mostra um atalho "N arquivados" pra trazer
    # de volta — some da vista, não do trabalho.
    arquivado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    arquivado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Override dos destinatários Threema (IDs separados por vírgula). Vazio =
    # cai no env do robô (RoboDef.env_threema_recipients) e depois no
    # OUVIDORIA_THREEMA_RECIPIENTS genérico.
    threema_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaviso_horas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, server_default="24"
    )
    # Parâmetros do robô mostrados na linha expandida (tolerância, janela…).
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    ultima_rodada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultima_rodada_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ultima_rodada_resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultima_rodada_duracao_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultima_falha_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultima_falha_erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class OuvidoriaRodada(Base):
    """Uma execução do robô: quando começou/terminou, deu certo, o resumo em
    linguagem de operação e os contadores brutos (pedidos conferidos, contas,
    novas…). GC de 30 dias (`services/ouvidoria.gc_rodadas`)."""

    __tablename__ = "ouvidoria_rodadas"
    __table_args__ = (
        Index(
            "ix_ouvidoria_rodadas_robo_chave_iniciada_em",
            "robo_chave",
            text("iniciada_em DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    robo_chave: Mapped[str] = mapped_column(
        String(60),
        ForeignKey("ouvidoria_robos.chave", ondelete="CASCADE"),
        nullable=False,
    )
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    contadores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class OuvidoriaOcorrencia(Base, TimestampMixin):
    """O que um robô encontrou. `chave` é a idempotência DENTRO do robô
    (`tiktok:<order_id>`, `conta:<integration_id>`): só pode existir UMA
    aberta por (robô, chave) — índice único parcial abaixo. Fechou e o
    problema voltou → linha nova (histórico fica), exceto quando alguém
    marcou `ignorada` (não reabre nunca) ou `tratada` há menos de 24 h."""

    __tablename__ = "ouvidoria_ocorrencias"
    __table_args__ = (
        Index(
            "uq_ouvidoria_ocorrencias_robo_chave_aberta",
            "robo_chave",
            "chave",
            unique=True,
            postgresql_where=text("fechada_em IS NULL"),
        ),
        Index("ix_ouvidoria_ocorrencias_robo_chave_fechada_em", "robo_chave", "fechada_em"),
        Index("ix_ouvidoria_ocorrencias_aberta_em", "aberta_em"),
        # A última FECHADA de uma (robô, chave) é consultada em TODO `registrar`
        # que não achou aberta (é ela que decide se a linha reabre: `ignorada`
        # nunca, `tratada` só depois de 24 h). Com 7 robôs o histórico só
        # cresce, e sem a `chave` no índice essa leitura varria todas as
        # fechadas do robô. Migração 0301.
        Index(
            "ix_ouvidoria_ocorrencias_ultima_fechada",
            "robo_chave",
            "chave",
            text("fechada_em DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    robo_chave: Mapped[str] = mapped_column(
        String(60),
        ForeignKey("ouvidoria_robos.chave", ondelete="CASCADE"),
        nullable=False,
    )
    chave: Mapped[str] = mapped_column(String(200), nullable=False)
    # ml / shopee / tiktok / amazon / magalu / bling / interno.
    plataforma: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Rótulo da conta como a operação fala ("Mercado Livre marquezini").
    conta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Número do pedido NA PLATAFORMA (o que casa com bling_orders.numeroloja).
    pedido: Mapped[str | None] = mapped_column(String(80), nullable=True)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O que a pessoa deve fazer ("Importar em Bling › Vendas › …").
    acao: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    severidade: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pessoa", server_default="pessoa"
    )
    precisa_pessoa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Extras que a tela mostra no balão (valor, sku, status na plataforma…).
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    aberta_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Carimbada a cada rodada em que o robô AINDA vê o problema — é o que o
    # `fechar_nao_vistas` usa pra saber que sumiu.
    ultima_vista_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    avisada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reavisada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fechada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fechamento: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Usuário (tratada/ignorada) ou "robô" (sumiu).
    fechada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)

    @property
    def aberta(self) -> bool:
        return self.fechada_em is None
