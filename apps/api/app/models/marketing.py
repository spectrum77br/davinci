"""Marketing module — autonomous ad agents (Shopee/ML/Amazon), per-department.

Parallel to the existing `ads_*` module: same conceptual shape but
organised by *department* (celular/mala/eletro) instead of just by account.
A single brand (Poofy, Minas, Kfa…) gets one MarketingAccount per
(platform, department) pair so the agent can target ACOS / budget per
vertical instead of treating the whole shop as one bucket.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.marketing_roteiro import MarketingRoteiro


class MarketingAccount(Base, TimestampMixin):
    __tablename__ = "marketing_accounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "shopee" | "ml" | "amazon"
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    # "celular" | "mala" | "eletro"
    department: Mapped[str] = mapped_column(String(32), nullable=False)

    acos_target: Mapped[float] = mapped_column(
        Float, nullable=False, default=8.0, server_default=text("8.0")
    )
    daily_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # "active" | "reduced" | "paused" | "off"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    current_intensity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default=text("50")
    )
    credit_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Timestamp of the last successful Shopee `get_total_balance` call.
    # The orchestrator uses this to decide whether to call the API again
    # (TTL 6h) or reuse the cached `credit_balance`. Shopee's per-endpoint
    # throttle makes naive every-tick polling impossible across 13 shops.
    credit_balance_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    spend_today: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue_today: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    # ─── Automatic schedule (BRT windows) ────────────────────────────────
    # When True, the reconciler (on the agent node) keeps this account's
    # campaigns paused outside the MarketingSchedule windows and resumed
    # inside them — in America/Sao_Paulo time, see services/marketing/scheduling.py.
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Manual override on top of the schedule. 'pause' forces OFF, 'resume'
    # forces ON, NULL = follow the windows. `override_until` bounds it
    # (NULL = until the operator clears it). The reconciler honours the
    # override while it's active, then falls back to the windows.
    override_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    override_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ─── External executor (AdsPower / marionete) ────────────────────────
    # AdsPower profile the local executor opens to drive this shop in a
    # browser. NULL on ML/Amazon accounts (those use the official API).
    adspower_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Last state ('on'|'off') applied by the external executor. 'browser'
    # accounts (Shopee via AdsPower) have NO synced campaigns — the sync uses
    # the blocked API — so the reconciler compares desired against THIS field
    # instead of MarketingCampaign rows. Updated when the executor reports a
    # pause/resume as done (see routers/marketing.py agent result endpoint).
    applied_state: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ─── Oferta Relâmpago (Shopee flash-sale) ────────────────────────────
    # When True, the daily 01:00 BRT cron (worker.marketing_flash_duplicate)
    # enqueues a whole-account 'flash_duplicate' browser command for this shop —
    # the local executor duplicates the 'Em andamento' offer into the next day
    # with openings. Only the "malas" shops start True (see the seed script).
    flash_duplicate_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class MarketingCommand(Base, TimestampMixin):
    """Outbox queue for ad actions. Every action — manual (a button click)
    or automatic (the schedule reconciler) — becomes one `pending` row here.
    The dedicated agent node consumes it via SELECT … FOR UPDATE SKIP LOCKED,
    runs it through `edit_campaign`, and stamps result/status. Durable across
    restarts: an interrupted command stays `pending`/`claimed` and reconverges
    on the next tick."""

    __tablename__ = "marketing_commands"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = apply to ALL campaigns of the account (whole-account pause/resume).
    campaign_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    # "pause" | "resume" | "set_budget" | "adjust_budget_pct"
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # "pending" | "claimed" | "done" | "failed"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # "manual" | "schedule"
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    # Execution channel: 'api' = internal consumer (ML/Amazon via official
    # API); 'browser' = external local executor (Shopee via AdsPower). 'browser'
    # commands are handed out via /marketing/agent/lease and NEVER pass through
    # `consume_pending_commands` (which would hit the blocked Shopee API).
    executor: Mapped[str] = mapped_column(
        String(16), nullable=False, default="api", server_default=text("'api'")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingSchedule(Base, TimestampMixin):
    __tablename__ = "marketing_schedules"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon
    start_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    end_hour: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketingMetric(Base, TimestampMixin):
    __tablename__ = "marketing_metrics"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    acos: Mapped[float | None] = mapped_column(Float, nullable=True)
    intensity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default=text("50")
    )


class MarketingDecision(Base, TimestampMixin):
    __tablename__ = "marketing_decisions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "no_action" | "enable_all" | "disable_all" | "increase_budget" |
    # "decrease_budget" | "pause_worst"
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    market_intensity: Mapped[int] = mapped_column(Integer, nullable=False)
    in_base_window: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acos_at_time: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketingCampaign(Base, TimestampMixin):
    """One ad campaign inside a MarketingAccount. Holds the same six rolled-up
    metrics that the dashboard's period tables show (Crédito, Gasto,
    Faturamento, Impressões, ACOS, Status) so the spreadsheet grid can render
    directly off this row without re-aggregating per cell. `credit` is only
    meaningful for Shopee (`credit_balance` on the parent account is the
    shop-level pot; this column lets a future real-API hook keep a per-
    campaign credit too — null for ML/Amazon)."""

    __tablename__ = "marketing_campaigns"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # "active" | "reduced" | "paused" | "off"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )

    credit: Mapped[float | None] = mapped_column(Float, nullable=True)
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    acos: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketingPattern(Base, TimestampMixin):
    __tablename__ = "marketing_patterns"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # "peak_hour" | "slow_hour" | "peak_day" | "slow_day" | "seasonal" | "correlation"
    pattern_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# O executor do Melhor Envio (Mac Santiago, desde 24/09/2026) grava o sinal de
# vida nesta mesma tabela com o nome prefixado — quem olha é a Ouvidoria
# (vigia_robo_melhorenvio). Quem lê a presença do executor da SHOPEE tem que
# pular essas linhas, senão o badge do Marketing fica "online" com o executor
# da Shopee desligado.
AGENTE_LOGISTICA_PREFIXO = "logistica:"


class MarketingAgentHeartbeat(Base, TimestampMixin):
    """Presence of the external local executor (marionete). One row per
    `agent_name` (singleton in practice). The dashboard renders online/offline
    from `last_seen_at` (online if seen within the last ~120s). Rows named
    `AGENTE_LOGISTICA_PREFIXO…` belong to the Melhor Envio executor."""

    __tablename__ = "marketing_agent_heartbeat"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    adspower_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    accounts_online: Mapped[int | None] = mapped_column(Integer, nullable=True)
    info: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class MarketingCreative(Base, TimestampMixin):
    """Linha da aba Criativos: briefing (modelo/marca/sku/roteiro) + arquivos
    do criador de conteúdo (1:N em MarketingCreativeFile) + aprovação.
    `aprovado` é tri-state: NULL = pendente, True = aprovado (arquivos vão
    pro MEGA), False = reprovado. `pushed_at`/`pushed_dest` registram o envio
    pra pasta do produto."""

    __tablename__ = "marketing_creatives"
    # A pergunta do robô e da tela "Fila do robô": a fila desta marca.
    # Declarado aqui (create_all dos testes) E na migration 0327.
    __table_args__ = (
        Index("ix_marketing_creatives_marca_fila", "marca_id", "fila_posicao"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    modelo: Mapped[str] = mapped_column(String(160), nullable=False)
    # `marca` (texto livre, como sempre foi) + `marca_id` (migration 0279): o
    # elo com Cadastros › Marcas, que é de onde a postagem tira as CONTAS de
    # rede social. O backfill da 0279 casa lower(marca) = marcas.slug.
    marca: Mapped[str | None] = mapped_column(String(64), nullable=True)
    marca_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sku: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Produto do criativo (migration 0282). Resolvido no POST/PATCH a partir
    # do `sku`, igual ao `marca_id` — NUNCA casado por string na hora de
    # publicar. A corrente é sku → product_links.external_sku → products (o
    # sufixo .ra/.pi/.ci é variante de cor, então casa também pela base).
    # É ele que escolhe o modelo de legenda do PRODUTO; sem produto a
    # cascata cai no padrão da marca, então NULL não trava nada.
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Equipe de marketing dona da linha (nome livre; casa com
    # users.marketing_teams). NULL = sem equipe (só admin/sem-equipe vê).
    equipe: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # DEPRECADA (migration 0299): o briefing virou `marketing_roteiros.texto`.
    # A coluna continua no banco por uma versão porque o downgrade a recriaria
    # VAZIA e os textos de produção não voltariam. Ninguém lê daqui além do
    # backfill — não está no serializador, no schema de entrada nem na tela.
    roteiro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O briefing que esta linha cumpre. SET NULL e não CASCADE: apagar um
    # roteiro não pode apagar a linha de produção nem a entrega que a agência
    # já mandou.
    roteiro_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_roteiros.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Override de legenda DESTE vídeo: ganha da biblioteca de modelos e é o
    # degrau mais alto da cascata depois da própria postagem. Não confundir
    # com `roteiro` — aquilo é briefing de produção ("cena, fala, texto na
    # tela"), em inglês, e publicá-lo põe instrução de gravação no Instagram.
    legenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    aprovado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Posição na fila do robô de autopostagem (migration 0327, Eduardo
    # 25/09/2026: "eu ia querer um video mais antigo rodasse antes"). NULL =
    # ordem normal (o mais recente primeiro); preenchido = furou a fila, e o
    # menor sai primeiro, antes de qualquer NULL. Quem ordena é
    # `autopostagem.ordem_da_fila()` — o robô e a tela usam a MESMA ordem.
    # Só vale pra criativo aprovado da marca: reprovar ou trocar de marca
    # zera (routers/marketing_creatives.py).
    fila_posicao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Recado da equipe interna pra quem produziu — escrito na hora de aprovar
    # ou reprovar e lido no portal das agências (migration 0299). É o único
    # texto daqui que SAI pra fora, então nunca recebe dado de outra linha.
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_dest: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    files: Mapped[list["MarketingCreativeFile"]] = relationship(
        back_populates="creative",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingCreativeFile.created_at",
    )

    # `lazy="selectin"` é obrigatório, não preferência: `list_creatives` e
    # `_get_row` fazem `select(MarketingCreative)` sem selectinload nenhum, e
    # um relacionamento com lazy padrão estoura MissingGreenlet em contexto
    # async — derrubando POST, PATCH, listagem e delete de uma vez.
    roteiro_ref: Mapped[MarketingRoteiro | None] = relationship(lazy="selectin")


class MarketingCreativeFile(Base, TimestampMixin):
    __tablename__ = "marketing_creative_files"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    creative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_creatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Caminho relativo a settings.uploads_dir (ex.: "creatives/<id>/<nome>").
    file_rel: Mapped[str] = mapped_column(String(512), nullable=False)
    # SHA-256 do conteúdo, carimbado no upload (migration 0279). Serve a três
    # coisas na postagem automática: idempotência, "esse vídeo já foi postado?"
    # e a trava de reusar o MESMO vídeo em marcas diferentes — Instagram e
    # TikTok punem conteúdo repetido entre contas em SILÊNCIO (a conta perde
    # recomendação; não vem erro nenhum de API).
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    creative: Mapped[MarketingCreative] = relationship(back_populates="files")
