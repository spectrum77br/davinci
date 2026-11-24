"""Alertas por equipe: ADM / equipe / pessoal e o "lido" por pessoa

Pedido M-19 do dono (prévia aprovada em 23/11/2026): as equipes não podem ver
avisos administrativos nem os de outras equipes, e a cerca vale no SERVIDOR.
Antes, cada alerta era gravado para UMA pessoa (`alerts.user_id`, com o lido em
`alerts.read_at`) e o sino mostrava só o que era dela.

ESTRUTURA (alerts):
- `escopo` ('adm' | 'equipe' | 'pessoal', padrão 'adm'): quem vê.
- `equipe` (101/102/103 na hora do aviso), `plataforma`, `integration_id`
  (FK SET NULL) e `conta`: a loja do alerta de anúncio.
- `duplicado_de`: a cópia que a mesma `dedupe_key` gravou uma vez por pessoa
  fica escondida; vale a mais antiga.
- `user_id` passa a aceitar NULL (= todos que o escopo deixa ver). CHECKs:
  pessoal tem dono; alerta de equipe não tem.
- Índice ÚNICO da `dedupe_key` do alerta sem destinatário (o de por pessoa
  continua).
- Tabela `alerta_leituras` (user_id, alert_id, lido_em): o "lido" por pessoa.

CONVERSÃO (upgrade), só o que dá para saber pelo tipo e pelo vínculo:
- quem já leu (`read_at`) ganha a linha em `alerta_leituras`;
- pessoal: tarefa atribuída, transferência de estoque e devoluções de quem fez;
- equipe: Falha sync / Revisão / Banido com `link_id` (vínculo → integração →
  loja; vínculo já apagado → `product_links_audit`, que guarda a integração
  dele) e o "preço não aplicado" da Amazon (envio → integração);
- o resto vira ADM (quem é de equipe deixa de ver ADM antigo — é o pedido);
- alerta de equipe e ADM gravado para quem NÃO é administrador ficam sem
  destinatário (senão ninguém os veria). O dono de antes vai para
  `payload._m19_user_id`, para o downgrade;
- cópias da mesma `dedupe_key` sem destinatário: fica a mais antiga, e quem
  leu uma cópia leu a que fica.

DOWNGRADE: volta o dono de antes, apaga os alertas sem destinatário (os
gravados depois desta migration), devolve o `read_at` do dono pelas leituras e
desfaz a estrutura.

Revision ID: 0505_alertas_por_equipe
Revises: 0504_conciliacao_conferido_esperando
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0505_alertas_por_equipe"
down_revision: str | None = "0504_conciliacao_conferido_esperando"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXOS (não importam o modelo vivo).
CHECK_ESCOPO = "escopo IN ('adm', 'equipe', 'pessoal')"
CHECK_PESSOAL = "escopo <> 'pessoal' OR user_id IS NOT NULL"
CHECK_EQUIPE = "escopo <> 'equipe' OR user_id IS NULL"
TIPOS_DE_ANUNCIO = ("sync_failure", "requires_review", "listing_banned")
# Prefixos da `dedupe_key` dos avisos do que a própria pessoa fez.
PREFIXOS_PESSOAIS = ("estoque_transferencia:", "devolucao_situacao_manual:", "devolucao_exclusao")
CHAVE_DONO_ANTES = "_m19_user_id"
UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def _equipe_da_integracao(alias: str) -> str:
    return (
        f"(SELECT si.sales_team FROM {SCHEMA}.store_info si"
        f" WHERE si.integration_id = {alias}.id AND si.sales_team IS NOT NULL"
        " ORDER BY si.sales_team LIMIT 1)"
    )


def converter(bind) -> None:
    """Classifica os alertas que já existem (ver docstring do módulo)."""
    s = SCHEMA
    bind.execute(
        sa.text(
            f"INSERT INTO {s}.alerta_leituras (user_id, alert_id, lido_em)"
            f" SELECT user_id, id, read_at FROM {s}.alerts"
            " WHERE read_at IS NOT NULL AND user_id IS NOT NULL"
            " ON CONFLICT DO NOTHING"
        )
    )
    prefixos = " OR ".join(f"dedupe_key LIKE '{p}%'" for p in PREFIXOS_PESSOAIS)
    bind.execute(
        sa.text(
            f"UPDATE {s}.alerts SET escopo = 'pessoal'"
            f" WHERE user_id IS NOT NULL AND (type::text = 'tarefa_atribuida' OR {prefixos})"
        )
    )
    tipos = ", ".join(f"'{t}'" for t in TIPOS_DE_ANUNCIO)
    bind.execute(
        sa.text(
            f"""
            UPDATE {s}.alerts a
               SET escopo = 'equipe',
                   integration_id = i.id,
                   plataforma = coalesce(pl.platform::text, i.platform::text),
                   conta = i.name,
                   equipe = {_equipe_da_integracao("i")}
              FROM {s}.product_links pl
              JOIN {s}.integrations i ON i.id = pl.integration_id
             WHERE a.type::text IN ({tipos})
               AND a.payload->>'link_id' ~ '{UUID_RE}'
               AND pl.id = (a.payload->>'link_id')::uuid
            """
        )
    )
    # Vínculo apagado depois do alerta (vínculo morto, SKU trocado): a trilha
    # `product_links_audit` guarda de qual integração ele era. Em 23/11/2026
    # eram 9.889 alertas assim, todos com a integração ainda viva.
    tem_trilha = bind.execute(
        sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"{s}.product_links_audit"}
    ).scalar()
    if tem_trilha:
        bind.execute(
            sa.text(
                f"""
                UPDATE {s}.alerts a
                   SET escopo = 'equipe',
                       integration_id = i.id,
                       plataforma = coalesce(t.platform, i.platform::text),
                       conta = i.name,
                       equipe = {_equipe_da_integracao("i")}
                  FROM (SELECT DISTINCT ON (link_id) link_id, integration_id, platform
                          FROM {s}.product_links_audit
                         WHERE link_id IS NOT NULL AND integration_id IS NOT NULL
                         ORDER BY link_id, deleted_at DESC) t
                  JOIN {s}.integrations i ON i.id = t.integration_id
                 WHERE a.escopo = 'adm'
                   AND a.type::text IN ({tipos})
                   AND a.payload->>'link_id' ~ '{UUID_RE}'
                   AND t.link_id = (a.payload->>'link_id')::uuid
                """
            )
        )
    bind.execute(
        sa.text(
            f"""
            UPDATE {s}.alerts a
               SET escopo = 'equipe',
                   integration_id = i.id,
                   plataforma = i.platform::text,
                   conta = i.name,
                   equipe = {_equipe_da_integracao("i")}
              FROM {s}.pricing_push_confirmacao c
              JOIN {s}.integrations i ON i.id = c.integration_id
             WHERE a.dedupe_key LIKE 'pricing_confirmacao:%'
               AND c.push_key = a.payload->>'push_key'
            """
        )
    )
    # O resto fica 'adm' (o padrão da coluna).
    bind.execute(
        sa.text(
            f"""
            UPDATE {s}.alerts a
               SET payload = a.payload
                       || jsonb_build_object('{CHAVE_DONO_ANTES}', a.user_id::text),
                   user_id = NULL
             WHERE a.user_id IS NOT NULL
               AND (a.escopo = 'equipe'
                    OR (a.escopo = 'adm'
                        AND NOT EXISTS (SELECT 1 FROM {s}.users u
                                         WHERE u.id = a.user_id AND u.role::text = 'admin')))
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            WITH r AS (
                SELECT id,
                       first_value(id) OVER (PARTITION BY dedupe_key
                                             ORDER BY created_at, id) AS primeiro
                  FROM {s}.alerts
                 WHERE user_id IS NULL AND dedupe_key IS NOT NULL
            )
            UPDATE {s}.alerts a SET duplicado_de = r.primeiro
              FROM r WHERE a.id = r.id AND r.id <> r.primeiro
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {s}.alerta_leituras (user_id, alert_id, lido_em)
            SELECT l.user_id, a.duplicado_de, min(l.lido_em)
              FROM {s}.alerta_leituras l
              JOIN {s}.alerts a ON a.id = l.alert_id
             WHERE a.duplicado_de IS NOT NULL
             GROUP BY 1, 2
            ON CONFLICT DO NOTHING
            """
        )
    )
    marcar_antigos_lidos_para_admins(bind)


def marcar_antigos_lidos_para_admins(bind) -> None:
    """Decisão do dono (24/11/2026): o administrador passa a ver os alertas
    compartilhados (ADM sem destinatário e os de equipe), uns 18.800 que nunca
    foram dele. Para o sino não abrir em "99+", os ANTIGOS que não eram dele
    entram como lidos para ele. O que era dele (ele era o dono do alerta ou de
    uma cópia da mesma `dedupe_key`) mantém o lido/não lido de antes. Nada é
    apagado; o downgrade ignora estas linhas (só devolve o lido do dono)."""
    s = SCHEMA
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {s}.alerta_leituras (user_id, alert_id, lido_em)
            SELECT u.id, a.id, now()
              FROM {s}.users u
              JOIN {s}.alerts a
                ON a.user_id IS NULL
               AND a.duplicado_de IS NULL
               AND a.escopo IN ('adm', 'equipe')
             WHERE u.role::text = 'admin'
               AND NOT EXISTS (
                     SELECT 1 FROM {s}.alerts c
                      WHERE (c.id = a.id OR c.duplicado_de = a.id)
                        AND c.payload->>'{CHAVE_DONO_ANTES}' = u.id::text)
            ON CONFLICT DO NOTHING
            """
        )
    )


def desfazer(bind) -> None:
    """Devolve o dono de antes e o `read_at` dele; apaga o que ficou sem dono."""
    s = SCHEMA
    bind.execute(
        sa.text(
            f"""
            UPDATE {s}.alerts a
               SET user_id = (a.payload->>'{CHAVE_DONO_ANTES}')::uuid,
                   payload = a.payload - '{CHAVE_DONO_ANTES}'
             WHERE a.user_id IS NULL
               AND a.payload->>'{CHAVE_DONO_ANTES}' ~ '{UUID_RE}'
               AND EXISTS (SELECT 1 FROM {s}.users u
                            WHERE u.id = (a.payload->>'{CHAVE_DONO_ANTES}')::uuid)
            """
        )
    )
    bind.execute(sa.text(f"DELETE FROM {s}.alerts WHERE user_id IS NULL"))
    bind.execute(
        sa.text(
            f"""
            UPDATE {s}.alerts a SET read_at = l.lido_em
              FROM {s}.alerta_leituras l
             WHERE l.alert_id = a.id AND l.user_id = a.user_id AND a.read_at IS NULL
            """
        )
    )


def upgrade() -> None:
    # Revisão M-19/M-20 (23/11/2026): `alerts` é tabela quente (o sino de
    # todos consulta, todo robô grava) e a FK nova trava `integrations`. Sem
    # limite, uma transação longa segurando uma delas deixaria o ALTER na fila
    # e TODO o sino parado atrás dele. Com o limite, a migration desiste em 5 s
    # e é só rodar de novo (conferir o alembic_version depois do deploy).
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "alerta_leituras",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.alerts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "lido_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_alerta_leituras_alert_id"), "alerta_leituras", ["alert_id"], schema=SCHEMA
    )
    op.add_column(
        "alerts",
        sa.Column("escopo", sa.Text(), nullable=False, server_default=sa.text("'adm'")),
        schema=SCHEMA,
    )
    op.add_column("alerts", sa.Column("equipe", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("alerts", sa.Column("plataforma", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "alerts",
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.integrations.id",
                ondelete="SET NULL",
                name=op.f("fk_alerts_integration_id_integrations"),
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column("alerts", sa.Column("conta", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "alerts",
        sa.Column("duplicado_de", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.alter_column("alerts", "user_id", nullable=True, schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_alerts_escopo_valido"), "alerts", CHECK_ESCOPO, schema=SCHEMA
    )

    converter(op.get_bind())

    # Depois da conversão: os antigos já estão no formato novo.
    op.create_check_constraint(
        op.f("ck_alerts_pessoal_tem_dono"), "alerts", CHECK_PESSOAL, schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_alerts_equipe_sem_dono"), "alerts", CHECK_EQUIPE, schema=SCHEMA
    )
    op.create_index(
        "ix_alerts_dedupe_compartilhado",
        "alerts",
        ["dedupe_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND user_id IS NULL AND duplicado_de IS NULL"
        ),
    )
    op.create_index(
        "ix_alerts_escopo_created",
        "alerts",
        ["escopo", sa.text("created_at DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("duplicado_de IS NULL"),
    )
    op.create_index(
        "ix_alerts_integration_id",
        "alerts",
        ["integration_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("integration_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # revisão M-19/M-20: ver o upgrade
    op.drop_index("ix_alerts_integration_id", table_name="alerts", schema=SCHEMA)
    op.drop_index("ix_alerts_escopo_created", table_name="alerts", schema=SCHEMA)
    op.drop_index("ix_alerts_dedupe_compartilhado", table_name="alerts", schema=SCHEMA)
    for nome in (
        "ck_alerts_equipe_sem_dono",
        "ck_alerts_pessoal_tem_dono",
        "ck_alerts_escopo_valido",
    ):
        op.drop_constraint(op.f(nome), "alerts", type_="check", schema=SCHEMA)
    desfazer(op.get_bind())
    op.alter_column("alerts", "user_id", nullable=False, schema=SCHEMA)
    for coluna in ("duplicado_de", "conta", "integration_id", "plataforma", "equipe", "escopo"):
        op.drop_column("alerts", coluna, schema=SCHEMA)
    op.drop_index(op.f("ix_alerta_leituras_alert_id"), table_name="alerta_leituras", schema=SCHEMA)
    op.drop_table("alerta_leituras", schema=SCHEMA)
