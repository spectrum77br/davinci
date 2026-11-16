# ruff: noqa: S608
"""atendimento: as horas do "Não precisa de resposta" saem de `dados` e viram colunas

16/11/2026, o reforço do "Não precisa de resposta" (que subiu hoje). O PATCH
da conversa gravava em `dados` o corte da bolinha (`processado_ate`) e a hora
do clique que as Métricas leem (`sem_resposta_em`). Defeito confirmado: a
releitura do sync (o ML relendo o pack em `_para_reler` → `upsert_conversa`,
a Magalu) segura um retrato velho da conversa e regrava `dados` inteiro — o
que o PATCH gravou no meio sumia (lost update). Coluna própria só muda
quando alguém a escreve (o mesmo motivo de `notificacoes_*`, 0416).

  • Três colunas na conversa, anuláveis, sem default e sem índice:
    `processado_ate` (o corte), `sem_resposta_em` (a hora do "Não precisa")
    e `precisa_resposta_em` (a hora do "Precisa de resposta", o desfazer: a
    IA não remarca sozinha o turno que a pessoa desfez — `sem_demanda`).
  • O que já estava em `dados` (só desde hoje) vai para as colunas e as
    chaves saem de `dados`. Texto que não é data ISO vira NULL (o código
    também o ignorava, `gravar._iso_utc`); sem fuso é UTC, como lá.
  • DOWNGRADE: devolve as duas chaves para `dados` (ISO em UTC, o formato
    que o código antigo lê) e derruba as três colunas. A hora do "Precisa
    de resposta" nunca morou em `dados`: se perde (o código antigo não a usa).

Atenção do deploy: as Métricas (`routers/atendimento._turnos_das_lojas`) liam a
hora do clique em `dados.sem_resposta_em` — a leitura vai para a coluna junto
com esta migration (sem isso, o clique deixa de contar como resposta).

`lock_timeout` de 10 s, como as outras: se o sync segurar a tabela por mais
que isso, a migration falha inteira (nada gravado) e é só rodar de novo.

Revision ID: 0489_atendimento_nao_precisa_colunas
Revises: 0488_segments_duimp_padrao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0489_atendimento_nao_precisa_colunas"
down_revision: str | None = "0488_segments_duimp_padrao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "atendimento_conversas"
# As chaves de `dados` que viram coluna (o mesmo nome dos dois lados).
CHAVES = ("processado_ate", "sem_resposta_em")
COLUNAS = (*CHAVES, "precisa_resposta_em")
# A data ISO que o código gravava (`datetime.isoformat()`), com e sem fuso.
_ISO = "^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?"
_FUSO = "(Z|[+-][0-9]{2}:[0-9]{2})"


def _data_da_chave(chave: str) -> str:
    """`dados->>chave` como timestamptz em SQL: com fuso, direto; sem fuso, UTC; o resto, NULL."""
    valor = f"(dados->>'{chave}')"
    return (
        f"CASE WHEN {valor} ~ '{_ISO}{_FUSO}$' THEN {valor}::timestamptz "
        f"WHEN {valor} ~ '{_ISO}$' THEN {valor}::timestamp AT TIME ZONE 'UTC' END"
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for coluna in COLUNAS:
        op.add_column(
            TABELA,
            sa.Column(coluna, sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )
    atribuicoes = ", ".join(f"{chave} = {_data_da_chave(chave)}" for chave in CHAVES)
    sem_chaves = "dados" + "".join(f" - '{chave}'" for chave in CHAVES)
    tem_chave = " OR ".join(f"dados->'{chave}' IS NOT NULL" for chave in CHAVES)
    op.execute(
        f'UPDATE "{SCHEMA}".{TABELA} SET {atribuicoes}, dados = {sem_chaves} WHERE {tem_chave}'
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    # ISO em UTC sem depender do fuso da sessão (o `_iso_utc` do código antigo lê).
    volta = " || ".join(
        f"CASE WHEN {chave} IS NULL THEN '{{}}'::jsonb ELSE jsonb_build_object('{chave}', "
        f"to_char({chave} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00') END"
        for chave in CHAVES
    )
    tem_coluna = " OR ".join(f"{chave} IS NOT NULL" for chave in CHAVES)
    op.execute(f'UPDATE "{SCHEMA}".{TABELA} SET dados = dados || {volta} WHERE {tem_coluna}')
    for coluna in reversed(COLUNAS):
        op.drop_column(TABELA, coluna, schema=SCHEMA)
