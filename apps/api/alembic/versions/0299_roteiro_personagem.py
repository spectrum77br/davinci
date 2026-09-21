"""Roteiros e Personagens viram entidades próprias, fora da linha de produção

Eduardo, 21/09/2026: "criaremos em marketing uma aba roteiro e lá poderemos
escrever o roteiro pro item e também poderemos criar personagens que vão
aparecer também no nosso domínio (...) desacoplar o roteiro daqui (...) em
roteiro talvez a gente já mande pra alguém específico (...) uma regra também
de que se não preenchido vai para os 2".

## O que sai do lugar

`marketing_creatives.roteiro` era o briefing morando na linha de produção.
Três consequências medidas disso: o DELETE do criativo apagava o texto
(`delete_creative` faz `session.delete(row)`); o mesmo briefing servindo dois
vídeos obrigava a copiar o texto à mão; e não havia onde dizer PRA QUEM o
briefing vai — `equipe` é a dona da linha, não o destinatário.

A coluna NÃO é dropada aqui. Ela fica morta por uma versão porque o downgrade
a recriaria VAZIA e os textos não voltariam. Nada mais lê dela: saiu do
serializador, do schema de entrada e da tela.

## A regra invertida do NULL

`marketing_roteiros.equipe_destino` NULL = as DUAS agências veem.
`marketing_creatives.equipe` NULL = ninguém de fora vê.

São opostos e convivem. O nome diferente é a trava: um `grep equipe_destino`
acha todo uso da regra invertida, e trocar um helper pelo outro não compila
numa query plausível.

## Personagem tem três partes, não uma

Os dois roteiros que existiam em produção nesta data já chamavam personagem à
mão — um por id de gerador (`<<<48dbb6ed-…>>> (Lívia), estudante brasileira de
22 anos`) e outro por apelido (`@Lívia`), com "Preserve the character's face,
blonde hair, body proportions and identity". Então o cadastro guarda nome,
descrição E `referencia` (a etiqueta que o gerador entende), mais as fotos.

## Backfill, e por que ele é conservador

Medido em produção hoje: 49 linhas em `marketing_creatives`, 2 com roteiro
escrito, e o único valor de `equipe` é a string "1" — que não casa com token
de portal nenhum (`.env` do portal fala em Mindset e Bill Gates), ou seja,
hoje esses 2 textos são invisíveis para as duas agências.

Duas decisões do Eduardo, tomadas nesta data:

1. A equipe "1" é a **Bill Gates**. O rename acontece aqui, nas 49 linhas e no
   `users.marketing_teams` de quem tem `["1"]`.
2. Os 2 roteiros migram com **`ativo = false`**. Eles nascem desligados e
   alguém da equipe liga na tela depois de reler o texto — migração não
   escancara pra terceiro o que hoje ninguém de fora vê.

O downgrade NÃO desfaz o rename: depois que ele roda não dá pra distinguir
"Bill Gates" escrito pela migração de "Bill Gates" digitado por alguém, e
reverter no chute apagaria trabalho. Mesmo critério do downgrade no-op da
0298.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0299_roteiro_personagem"
down_revision: str | None = "0298_tiktok_localizacao_pt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# A equipe que hoje carrega as 49 linhas, e o nome que ela passa a ter.
EQUIPE_ANTIGA = "1"
EQUIPE_NOVA = "Bill Gates"


def _ts(nome: str) -> sa.Column:
    return sa.Column(
        nome, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _autor() -> sa.Column:
    return sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True)


def upgrade() -> None:
    # ─── roteiros ──────────────────────────────────────────────────────────
    op.create_table(
        "marketing_roteiros",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("marca", sa.String(64), nullable=True),
        sa.Column("marca_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sku", sa.String(512), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        # NULL = as DUAS agências. Ver o cabeçalho deste arquivo.
        sa.Column("equipe_destino", sa.String(64), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _autor(),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["marca_id"], [f"{SCHEMA}.marcas.id"],
            name="fk_marketing_roteiros_marca_id_marcas", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{SCHEMA}.products.id"],
            name="fk_marketing_roteiros_product_id_products", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"],
            name="fk_marketing_roteiros_created_by_users", ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_roteiros_equipe_destino", "marketing_roteiros",
        ["equipe_destino"], schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_roteiros_marca_id", "marketing_roteiros", ["marca_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_marketing_roteiros_product_id", "marketing_roteiros", ["product_id"], schema=SCHEMA
    )

    # ─── referências do briefing (imagens + links de produto) ──────────────
    op.create_table(
        "marketing_roteiro_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(16), nullable=False),  # "imagem" | "link"
        sa.Column("titulo", sa.String(200), nullable=True),
        # Só http/https, validado na escrita: vira href no portal PHP.
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(256), nullable=True),
        sa.Column("file_mime", sa.String(128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_rel", sa.String(512), nullable=True),
        _autor(),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["roteiro_id"], [f"{SCHEMA}.marketing_roteiros.id"],
            name="fk_marketing_roteiro_refs_roteiro_id_marketing_roteiros",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"],
            name="fk_marketing_roteiro_refs_created_by_users", ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_roteiro_refs_roteiro_id", "marketing_roteiro_refs",
        ["roteiro_id"], schema=SCHEMA,
    )

    # ─── personagens ───────────────────────────────────────────────────────
    op.create_table(
        "marketing_personagens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        # A etiqueta que o gerador de vídeo entende (`<<<uuid>>>`, `@apelido`).
        sa.Column("referencia", sa.String(200), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _autor(),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"],
            name="fk_marketing_personagens_created_by_users", ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    # Dois "Lívia" no select do roteiro e ninguém sabe qual é qual.
    op.execute(
        f"CREATE UNIQUE INDEX ix_marketing_personagens_nome_unico "  # noqa: S608
        f"ON {SCHEMA}.marketing_personagens (lower(nome))"
    )

    op.create_table(
        "marketing_personagem_imagens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("personagem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_mime", sa.String(128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_rel", sa.String(512), nullable=False),
        _autor(),
        _ts("created_at"),
        _ts("updated_at"),
        # Nome TRUNCADO com hash: é o que o SQLAlchemy gera a partir da
        # NAMING_CONVENTION (o nome inteiro passa dos 63 caracteres do
        # Postgres). Escrever o nome "óbvio" aqui faria produção divergir do
        # schema que os testes montam com create_all.
        sa.ForeignKeyConstraint(
            ["personagem_id"], [f"{SCHEMA}.marketing_personagens.id"],
            name="fk_marketing_personagem_imagens_personagem_id_marketing_b60b",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"],
            name="fk_marketing_personagem_imagens_created_by_users", ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_personagem_imagens_personagem_id", "marketing_personagem_imagens",
        ["personagem_id"], schema=SCHEMA,
    )

    # ─── "neste vídeo use a Lívia" ─────────────────────────────────────────
    op.create_table(
        "marketing_roteiro_personagens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("personagem_id", postgresql.UUID(as_uuid=True), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("roteiro_id", "personagem_id", name="uq_roteiro_personagem"),
        sa.ForeignKeyConstraint(
            ["roteiro_id"], [f"{SCHEMA}.marketing_roteiros.id"],
            name="fk_marketing_roteiro_personagens_roteiro_id_marketing_roteiros",
            ondelete="CASCADE",
        ),
        # Truncado com hash pelo mesmo motivo do de cima.
        sa.ForeignKeyConstraint(
            ["personagem_id"], [f"{SCHEMA}.marketing_personagens.id"],
            name="fk_marketing_roteiro_personagens_personagem_id_marketin_4f78",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_roteiro_personagens_roteiro_id", "marketing_roteiro_personagens",
        ["roteiro_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_roteiro_personagens_personagem_id", "marketing_roteiro_personagens",
        ["personagem_id"], schema=SCHEMA,
    )

    # ─── o criativo aponta pro briefing, e guarda o recado da recusa ───────
    op.add_column(
        "marketing_creatives",
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_marketing_creatives_roteiro_id_marketing_roteiros",
        "marketing_creatives", "marketing_roteiros",
        ["roteiro_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="SET NULL",
    )
    op.create_index(
        "ix_marketing_creatives_roteiro_id", "marketing_creatives",
        ["roteiro_id"], schema=SCHEMA,
    )
    # Recado escrito na aprovação/recusa. Fica na ENTREGA ("por que este vídeo
    # voltou"), não no briefing — são perguntas diferentes.
    op.add_column(
        "marketing_creatives", sa.Column("feedback", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "marketing_creatives",
        sa.Column("feedback_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    _backfill()


def sentencas_backfill(schema: str | None = None) -> list[sa.TextClause]:
    """O SQL do backfill, NA ORDEM, como dados.

    Exposto em vez de embutido no `_backfill` porque
    `tests/test_roteiro_backfill_migration.py` roda ESTAS sentenças, não uma
    cópia delas — cópia de SQL em teste envelhece calada e passa a provar o
    comportamento antigo.

    O schema é lido na CHAMADA, não no default do parâmetro: `def f(s=SCHEMA)`
    congela o valor no import, e o teste roda noutro schema.
    """
    schema = schema or SCHEMA
    return [
        # 1. A equipe "1" passa a se chamar Bill Gates (decisão do Eduardo,
        #    21/09/2026). Antes do INSERT, pro `equipe_destino` já nascer certo.
        sa.text(
            f"UPDATE {schema}.marketing_creatives SET equipe = :nova "  # noqa: S608
            "WHERE equipe = :antiga"
        ).bindparams(nova=EQUIPE_NOVA, antiga=EQUIPE_ANTIGA),
        # O mesmo nome em users.marketing_teams, que é JSONB array de string.
        # `jsonb_typeof = 'array'` é guarda de verdade: há usuário com JSON
        # null gravado ali, e jsonb_array_elements_text estoura em cima dele.
        # `jsonb_exists` é a forma de FUNÇÃO do operador `?` — o operador
        # conflita com o placeholder de bind e deixa o Postgres sem inferir o
        # tipo do parâmetro. Casa a chave EXATA: um LIKE pegaria "10" e "1a".
        sa.text(
            f"""
            UPDATE {schema}.users u
               SET marketing_teams = sub.novo
              FROM (
                    SELECT x.id,
                           jsonb_agg(
                               CASE WHEN t.elem = CAST(:antiga AS text)
                                    THEN CAST(:nova AS text) ELSE t.elem END
                               ORDER BY t.ord
                           ) AS novo
                      FROM {schema}.users x,
                           LATERAL jsonb_array_elements_text(x.marketing_teams)
                                   WITH ORDINALITY AS t(elem, ord)
                     WHERE jsonb_typeof(x.marketing_teams) = 'array'
                       AND jsonb_exists(x.marketing_teams, CAST(:antiga AS text))
                     GROUP BY x.id
                   ) sub
             WHERE u.id = sub.id
            """  # noqa: S608
        ).bindparams(nova=EQUIPE_NOVA, antiga=EQUIPE_ANTIGA),
        # 2. Um roteiro por criativo QUE TEM TEXTO. O id do roteiro é o id do
        #    criativo: determinístico, único, e dispensa RETURNING no passo 3.
        #    `ativo = false` — nascem desligados, alguém liga na tela depois
        #    de reler o texto. marca_id/product_id vêm COPIADOS e não
        #    re-resolvidos: re-resolver mudaria o vínculo se alguém tivesse
        #    renomeado um anúncio desde então.
        sa.text(
            f"""
            INSERT INTO {schema}.marketing_roteiros
                (id, titulo, texto, marca, marca_id, sku, product_id,
                 equipe_destino, ativo, created_by, created_at, updated_at)
            SELECT c.id, left(c.modelo, 160), btrim(c.roteiro),
                   c.marca, c.marca_id, c.sku, c.product_id,
                   c.equipe, false, c.created_by, c.created_at, now()
              FROM {schema}.marketing_creatives c
             WHERE c.roteiro IS NOT NULL AND length(btrim(c.roteiro)) > 0
            """  # noqa: S608
        ),
        # 3. O criativo passa a apontar pro briefing que ele cumpre.
        sa.text(
            f"UPDATE {schema}.marketing_creatives SET roteiro_id = id "  # noqa: S608
            "WHERE roteiro IS NOT NULL AND length(btrim(roteiro)) > 0"
        ),
    ]


def _backfill() -> None:
    for sentenca in sentencas_backfill():
        op.execute(sentenca)


def downgrade() -> None:
    # O rename da equipe NÃO volta: depois que ele roda não há como separar
    # "Bill Gates" escrito pela migração de "Bill Gates" digitado por alguém,
    # e reverter no chute apagaria trabalho. Mesmo critério da 0298.
    op.drop_index("ix_marketing_creatives_roteiro_id", "marketing_creatives", schema=SCHEMA)
    op.drop_constraint(
        "fk_marketing_creatives_roteiro_id_marketing_roteiros",
        "marketing_creatives", schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("marketing_creatives", "roteiro_id", schema=SCHEMA)
    op.drop_column("marketing_creatives", "feedback_em", schema=SCHEMA)
    op.drop_column("marketing_creatives", "feedback", schema=SCHEMA)
    op.drop_table("marketing_roteiro_personagens", schema=SCHEMA)
    op.drop_table("marketing_personagem_imagens", schema=SCHEMA)
    op.drop_table("marketing_personagens", schema=SCHEMA)
    op.drop_table("marketing_roteiro_refs", schema=SCHEMA)
    op.drop_table("marketing_roteiros", schema=SCHEMA)
