"""desempenho dos vídeos — tirar post do desempenho, e a hora da leitura boa

Eduardo, 24/09/2026: "tirar aqueles 2 tiktoks da outra conta da uranyx" e
"esses vídeos ainda não aparecem… será que demora?".

`marketing_postagens.fora_do_desempenho_*` — MARCA de "não conta", não
DELETE. O post sai de toda soma, média e comparação e deixa de ser lido, mas o
histórico fica, e a tela ganha "Voltar a contar". Da próxima vez que um post
não devesse contar, ninguém precisa mexer no banco.

`marketing_postagem_metricas.lido_em` — a hora da leitura que DEU os números
da linha. `updated_at` não servia: o upsert (`on_conflict_do_update`) não
aplica o `onupdate` do ORM, então ele ficava congelado no primeiro insert do
dia. Com leitura de hora em hora, uma falha às 15h por cima de uma leitura boa
às 13h apagava os números do dia; agora a falha só grava o erro e a tentativa,
e `lido_em` continua dizendo de quando são os números.

O passo de dados marca os dois TikToks publicados em @uranyx_brasil — a conta
da marca no TikTok voltou a ser @uranyx_br. Casa pelo @ do LINK, nunca pela
coluna `conta`: ela é snapshot do nome na hora de publicar, e o canal do
YouTube da Uranyx foi renomeado — o post dele ainda diz `uranyx_brasil` e é da
marca, então tem que continuar contando. Regex e não LIKE porque no LIKE o `_`
de `uranyx_brasil` é curinga e pegaria `@uranyxAbrasil`.

O DDL da FK foi COPIADO do CreateTable do modelo (nome incluído), como a 0315.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0317_desempenho_videos"
down_revision: str | None = "0316_hora_inicio_postagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

MOTIVO_CONTA_ANTIGA = (
    "publicado na conta antiga @uranyx_brasil — a conta da marca no TikTok voltou a ser @uranyx_br"
)


def sql_conta_antiga(schema: str) -> str:
    """O UPDATE que tira do desempenho os TikToks da conta antiga.

    Função pura (o teste roda o mesmo texto no schema de teste): só TikTok,
    só pelo @ do link, e só o que ainda não está marcado — rodar de novo não
    muda a data nem o motivo de quem já saiu. Snapshot nenhum é apagado.
    """
    return f"""
        UPDATE {schema}.marketing_postagens
           SET fora_do_desempenho_em = now(),
               fora_do_desempenho_motivo = '{MOTIVO_CONTA_ANTIGA}'
         WHERE plataforma = 'tiktok'
           AND post_url ~* '^https?://(www\\.|m\\.)?tiktok\\.com/@uranyx_brasil/'
           AND fora_do_desempenho_em IS NULL
    """  # noqa: S608 — schema é constante do código, não entrada de usuário


def upgrade() -> None:
    op.add_column(
        "marketing_postagens",
        sa.Column("fora_do_desempenho_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "marketing_postagens",
        sa.Column("fora_do_desempenho_motivo", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "marketing_postagens",
        sa.Column("fora_do_desempenho_por", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_marketing_postagens_fora_do_desempenho_por_users",
        "marketing_postagens",
        "users",
        ["fora_do_desempenho_por"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.add_column(
        "marketing_postagem_metricas",
        sa.Column("lido_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # Linha antiga: aproximação. Até hoje a coleta era 1x por dia e o
    # `updated_at` ficava no primeiro insert do dia — que era o único.
    op.execute(
        f"UPDATE {SCHEMA}.marketing_postagem_metricas SET lido_em = updated_at WHERE erro IS NULL"  # noqa: S608
    )

    r = op.get_bind().execute(sa.text(sql_conta_antiga(SCHEMA)))
    # Esperado: 2. Se vier outro número, o deploy segue — e o que faltar sai
    # pela tela, em "Tirar do desempenho".
    print(f"[0317] postagens da conta antiga @uranyx_brasil fora do desempenho: {r.rowcount}")


def downgrade() -> None:
    op.drop_column("marketing_postagem_metricas", "lido_em", schema=SCHEMA)
    op.drop_constraint(
        "fk_marketing_postagens_fora_do_desempenho_por_users",
        "marketing_postagens",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("marketing_postagens", "fora_do_desempenho_por", schema=SCHEMA)
    op.drop_column("marketing_postagens", "fora_do_desempenho_motivo", schema=SCHEMA)
    op.drop_column("marketing_postagens", "fora_do_desempenho_em", schema=SCHEMA)
