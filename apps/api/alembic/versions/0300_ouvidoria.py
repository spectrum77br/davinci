"""Ouvidoria › Robôs — catálogo dos robôs, rodadas e ocorrências; vigia_importacao migra pra cá

Vinicius, 21/09/2026: "ouvidoria pode ser o último no painel, acima apenas de
Admin". Uma seção nova do menu pra enxergar o que os robôs da casa estão
fazendo: modo (ligado / silencioso / desligado), última rodada, quem eles
avisam, e o que encontraram e ainda ninguém tratou.

## Três tabelas

`ouvidoria_robos` — uma linha por robô, chave estável no código
(`services/ouvidoria.ROBOS`). O catálogo escreve nome/descrição/cadência; o
que a pessoa mexe na tela (modo, destinatários, config) o catálogo não toca.

`ouvidoria_rodadas` — uma linha por execução (quando, duração, ok, resumo,
contadores). GC de 30 dias no serviço.

`ouvidoria_ocorrencias` — o que o robô viu. Só UMA aberta por (robô, chave)
— índice único PARCIAL (`WHERE fechada_em IS NULL`): fechou e voltou, é linha
nova, o histórico fica.

## O vigia de importação muda de casa

A tabela `vigia_importacao` (0230) era o estado anti-spam do único robô que
existia: uma linha por pedido pago no ML que não caiu no Bling. É exatamente
uma ocorrência da Ouvidoria, então esta migração COPIA as linhas pra
`ouvidoria_ocorrencias` (robô `vigia_importacao`, chave `ml:<numero_loja>`)
e DERRUBA a tabela antiga:

- `resolvido_em` preenchido → fechada como `sumiu` naquele instante;
- aberta cuja `ultima_verificacao` (fallback `detectado_em`) tem mais de 7
  dias → também `sumiu`, em `ultima_verificacao`. O vigia antigo só carimbava
  `ultima_verificacao` enquanto o pedido continuava vindo como pago na busca
  do ML (janela de 72 h); uma linha parada há uma semana é pedido que saiu da
  janela ou foi cancelado, e reabrir isso como "pessoa precisa agir" seria
  alarme falso no primeiro dia da tela;
- `avisado_em` → `avisada_em` (a tabela antiga sobrescrevia o carimbo a cada
  re-aviso, então é o último aviso que se tem; `reavisada_em` fica NULL).

A tabela antiga não tinha o valor do pedido — o título migrado sai sem o
"(R$ …)" que o vigia refeito grava. O robô `vigia_importacao` é inserido
aqui, em modo `ligado`, pra o worker não precisar de um primeiro tick só
pra existir.

O downgrade recria `vigia_importacao` VAZIA com as colunas da 0230 (o vigia
antigo reconstrói o estado sozinho na rodada seguinte) e derruba as três
tabelas — as ocorrências dos outros robôs que vierem depois não têm pra
onde voltar, mesmo critério de "não se reconstrói" das 0298/0299.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0300_ouvidoria"
down_revision: str | None = "0299_roteiro_personagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

ROBO_VIGIA = "vigia_importacao"
# Espelho de services/ouvidoria.ROBOS[ROBO_VIGIA] na data. Copiado (e não
# importado) pelo mesmo motivo da 0298: o alembic carrega TODAS as versões em
# qualquer comando, e uma mudança futura no catálogo não pode travar o grafo.
VIGIA_NOME = "Vigia de importação"
VIGIA_DESCRICAO = (
    "Pedido pago no marketplace que não caiu no Bling. Confere no Bling ao vivo "
    "antes de abrir ocorrência; conta cuja API falhou também vira ocorrência."
)
VIGIA_CADENCIA = "a cada 30 min (:09 e :39)"
VIGIA_PLATAFORMAS = '["ml", "shopee", "tiktok", "amazon"]'
VIGIA_CONFIG = (
    '{"tolerancia_min": 90, "janela_horas": 72, "cadencia_min": 30, '
    '"amazon_a_cada_rodadas": 3}'
)
VIGIA_ACAO = (
    "Importar manualmente: Bling › Vendas › Pedidos de lojas virtuais "
    "(importar pedidos manualmente)"
)


def _ts(nome: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default_now else None,
        nullable=nullable,
    )


def colunas_vigia_importacao() -> list[sa.Column]:
    """As colunas da `vigia_importacao` como a 0230 criou — o downgrade recria
    a tabela com elas, e `tests/test_ouvidoria.py` monta a mesma tabela no
    schema de teste pra rodar a cópia de verdade (o conftest usa create_all
    e, sem o modelo VigiaImportacao, a tabela não nasce sozinha)."""
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("numero_loja", sa.Text(), nullable=False),
        sa.Column("pack_id", sa.Text(), nullable=True),
        sa.Column("pago_em", sa.DateTime(timezone=True), nullable=True),
        _ts("detectado_em", nullable=False, default_now=True),
        _ts("ultima_verificacao"),
        _ts("avisado_em"),
        _ts("resolvido_em"),
        sa.UniqueConstraint(
            "plataforma", "numero_loja", name="uq_vigia_importacao_plataforma_numero"
        ),
    ]


def upgrade() -> None:
    # ─── robôs ─────────────────────────────────────────────────────────────
    op.create_table(
        "ouvidoria_robos",
        sa.Column("chave", sa.String(60), primary_key=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("area", sa.String(40), nullable=True),
        sa.Column("cadencia_texto", sa.String(60), nullable=True),
        sa.Column(
            "plataformas", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("modo", sa.String(12), nullable=False, server_default="ligado"),
        sa.Column("modo_alterado_por", sa.String(120), nullable=True),
        _ts("modo_alterado_em"),
        sa.Column("threema_recipients", sa.Text(), nullable=True),
        sa.Column("reaviso_horas", sa.Integer(), nullable=False, server_default="24"),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts("ultima_rodada_em"),
        sa.Column("ultima_rodada_ok", sa.Boolean(), nullable=True),
        sa.Column("ultima_rodada_resumo", sa.Text(), nullable=True),
        sa.Column("ultima_rodada_duracao_ms", sa.Integer(), nullable=True),
        _ts("ultima_falha_em"),
        sa.Column("ultima_falha_erro", sa.Text(), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        schema=SCHEMA,
    )

    # ─── rodadas ───────────────────────────────────────────────────────────
    op.create_table(
        "ouvidoria_rodadas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("robo_chave", sa.String(60), nullable=False),
        _ts("iniciada_em", nullable=False),
        _ts("terminada_em"),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column(
            "contadores", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["robo_chave"], [f"{SCHEMA}.ouvidoria_robos.chave"],
            name="fk_ouvidoria_rodadas_robo_chave_ouvidoria_robos", ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ouvidoria_rodadas_robo_chave_iniciada_em", "ouvidoria_rodadas",
        ["robo_chave", sa.text("iniciada_em DESC")], schema=SCHEMA,
    )

    # ─── ocorrências ───────────────────────────────────────────────────────
    op.create_table(
        "ouvidoria_ocorrencias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("robo_chave", sa.String(60), nullable=False),
        sa.Column("chave", sa.String(200), nullable=False),
        sa.Column("plataforma", sa.String(20), nullable=True),
        sa.Column("conta", sa.String(120), nullable=True),
        sa.Column("pedido", sa.String(80), nullable=True),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.Column("acao", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("severidade", sa.String(16), nullable=False, server_default="pessoa"),
        sa.Column(
            "precisa_pessoa", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "dados", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts("aberta_em", nullable=False),
        _ts("ultima_vista_em", nullable=False),
        _ts("avisada_em"),
        _ts("reavisada_em"),
        _ts("fechada_em"),
        sa.Column("fechamento", sa.String(12), nullable=True),
        sa.Column("fechada_por", sa.String(120), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.ForeignKeyConstraint(
            ["robo_chave"], [f"{SCHEMA}.ouvidoria_robos.chave"],
            name="fk_ouvidoria_ocorrencias_robo_chave_ouvidoria_robos", ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    # Uma aberta por (robô, chave); as fechadas podem repetir — histórico.
    op.create_index(
        "uq_ouvidoria_ocorrencias_robo_chave_aberta", "ouvidoria_ocorrencias",
        ["robo_chave", "chave"], unique=True, schema=SCHEMA,
        postgresql_where=sa.text("fechada_em IS NULL"),
    )
    op.create_index(
        "ix_ouvidoria_ocorrencias_robo_chave_fechada_em", "ouvidoria_ocorrencias",
        ["robo_chave", "fechada_em"], schema=SCHEMA,
    )
    op.create_index(
        "ix_ouvidoria_ocorrencias_aberta_em", "ouvidoria_ocorrencias",
        ["aberta_em"], schema=SCHEMA,
    )

    # ─── o vigia muda de casa ──────────────────────────────────────────────
    for sentenca in sentencas_copia():
        op.execute(sentenca)
    op.drop_index(
        "ix_vigia_importacao_resolvido_em", table_name="vigia_importacao", schema=SCHEMA
    )
    op.drop_table("vigia_importacao", schema=SCHEMA)


def sentencas_copia(schema: str | None = None) -> list[sa.TextClause]:
    """O SQL da mudança de casa, NA ORDEM, como dados — `tests/test_ouvidoria.py`
    roda ESTAS sentenças contra uma `vigia_importacao` montada à mão, não uma
    cópia delas (cópia de SQL em teste envelhece calada). O schema é lido na
    chamada, não no default do parâmetro, porque o teste roda noutro schema."""
    schema = schema or SCHEMA
    return [
        # 1. O robô nasce ligado, com a config que o serviço usaria de padrão.
        #    ON CONFLICT: se o worker subiu antes da migração e o catálogo já
        #    inseriu a linha, não sobrescreve o que estiver lá.
        sa.text(
            f"""
            INSERT INTO {schema}.ouvidoria_robos
                (chave, nome, descricao, area, cadencia_texto, plataformas, modo,
                 reaviso_horas, config)
            VALUES (:chave, :nome, :descricao, 'pedidos', :cadencia,
                    CAST(:plataformas AS jsonb), 'ligado', 24, CAST(:config AS jsonb))
            ON CONFLICT (chave) DO NOTHING
            """  # noqa: S608
        ).bindparams(
            chave=ROBO_VIGIA, nome=VIGIA_NOME, descricao=VIGIA_DESCRICAO,
            cadencia=VIGIA_CADENCIA, plataformas=VIGIA_PLATAFORMAS, config=VIGIA_CONFIG,
        ),
        # 2. Cada linha do vigia antigo vira uma ocorrência. `fechada_em` é
        #    `resolvido_em` quando houve match no Bling; senão, a última vez
        #    que o vigia viu o pedido, se isso foi há mais de 7 dias (ver o
        #    cabeçalho). `id` é o id antigo: determinístico, e re-rodar a
        #    sentença num banco meio migrado não duplica.
        sa.text(
            f"""
            INSERT INTO {schema}.ouvidoria_ocorrencias
                (id, robo_chave, chave, plataforma, conta, pedido, titulo, detalhe,
                 acao, link, severidade, precisa_pessoa, dados,
                 aberta_em, ultima_vista_em, avisada_em, reavisada_em,
                 fechada_em, fechamento, fechada_por, created_at, updated_at)
            SELECT v.id,
                   :robo,
                   'ml:' || v.numero_loja,
                   'ml',
                   left(v.conta, 120),
                   left(v.numero_loja, 80),
                   CASE WHEN v.pago_em IS NULL
                        THEN 'Pago e não caiu no Bling'
                        ELSE 'Pago '
                             || to_char(v.pago_em AT TIME ZONE 'America/Sao_Paulo',
                                        'DD/MM HH24:MI')
                             || ' e não caiu no Bling'
                   END,
                   'Migrado do vigia antigo (só Mercado Livre, sem valor do pedido).',
                   :acao,
                   NULL,
                   'pessoa',
                   true,
                   jsonb_strip_nulls(jsonb_build_object(
                       'pago_em', v.pago_em, 'pack_id', v.pack_id)),
                   v.detectado_em,
                   COALESCE(v.ultima_verificacao, v.detectado_em),
                   v.avisado_em,
                   NULL,
                   f.fechada_em,
                   CASE WHEN f.fechada_em IS NULL THEN NULL ELSE 'sumiu' END,
                   CASE WHEN f.fechada_em IS NULL THEN NULL ELSE 'robô' END,
                   v.detectado_em,
                   now()
              FROM {schema}.vigia_importacao v
              CROSS JOIN LATERAL (
                   SELECT CASE
                            WHEN v.resolvido_em IS NOT NULL THEN v.resolvido_em
                            WHEN COALESCE(v.ultima_verificacao, v.detectado_em)
                                 < now() - interval '7 days'
                                 THEN COALESCE(v.ultima_verificacao, v.detectado_em)
                            ELSE NULL
                          END AS fechada_em
              ) f
             WHERE NOT EXISTS (
                   SELECT 1 FROM {schema}.ouvidoria_ocorrencias o WHERE o.id = v.id
             )
            """  # noqa: S608
        ).bindparams(robo=ROBO_VIGIA, acao=VIGIA_ACAO),
    ]


def downgrade() -> None:
    # As ocorrências não voltam pra tabela antiga: o vigia antigo reconstrói
    # o estado dele sozinho na rodada seguinte (busca os pagos das últimas
    # 72 h de novo). Os outros robôs não têm pra onde voltar.
    op.drop_index(
        "ix_ouvidoria_ocorrencias_aberta_em", table_name="ouvidoria_ocorrencias",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_ouvidoria_ocorrencias_robo_chave_fechada_em",
        table_name="ouvidoria_ocorrencias", schema=SCHEMA,
    )
    op.drop_index(
        "uq_ouvidoria_ocorrencias_robo_chave_aberta", table_name="ouvidoria_ocorrencias",
        schema=SCHEMA,
    )
    op.drop_table("ouvidoria_ocorrencias", schema=SCHEMA)
    op.drop_index(
        "ix_ouvidoria_rodadas_robo_chave_iniciada_em", table_name="ouvidoria_rodadas",
        schema=SCHEMA,
    )
    op.drop_table("ouvidoria_rodadas", schema=SCHEMA)
    op.drop_table("ouvidoria_robos", schema=SCHEMA)

    op.create_table("vigia_importacao", *colunas_vigia_importacao(), schema=SCHEMA)
    op.create_index(
        "ix_vigia_importacao_resolvido_em", "vigia_importacao", ["resolvido_em"],
        schema=SCHEMA,
    )
