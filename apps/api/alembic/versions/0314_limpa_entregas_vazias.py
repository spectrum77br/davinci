"""Apaga as linhas de entrega vazias que a sincronização abria sozinha

Eduardo, 23/09/2026: "fiz 1 envio de 1 conta só, da Mindset, preencheu várias
automaticamente — todas preencheu sem envio".

Não foi o envio dele. Entre 15:42:30 e 15:42:50 ele LIGOU as 10 ideias do dia,
uma a cada dois segundos, e cada ativação abria uma entrega vazia por agência
endereçada. Como as ideias vão "para as duas", foram 2 por ideia: 20 linhas que
ninguém enviou, contadas como pendentes na aba Criativos e listadas como entrega
"em análise" no portal, com "nada enviado" ao lado.

A abertura automática saiu do código nesta mesma leva — a tela da ideia passou a
criar a linha no ato do envio, então ela não é mais necessária. Esta migration
tira as que já ficaram.

## O que ela apaga, e o que não encosta

Só o que aquela sincronização poderia ter criado: COM equipe, LIGADA a um
roteiro, sem arquivo, sem veredito, sem envio ao MEGA e sem legenda ou feedback
escritos à mão. Qualquer um desses significa que alguém encostou na linha, e
trabalho de gente não se apaga por limpeza.

Medido em produção antes de aplicar: 19 linhas saem, 54 ficam, e nenhuma
postagem aponta para as que saem.

Sem downgrade útil: recriar linhas vazias seria repor exatamente o lixo que esta
migration existe para remover.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0314_limpa_entregas_vazias"
down_revision: str | None = "0313_conceito_com_personagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.marketing_creatives c
         WHERE c.equipe IS NOT NULL
           AND c.roteiro_id IS NOT NULL
           AND c.aprovado IS NULL
           AND c.pushed_at IS NULL
           AND c.legenda IS NULL
           AND c.feedback IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM {SCHEMA}.marketing_creative_files f
                WHERE f.creative_id = c.id
           )
           AND NOT EXISTS (
               SELECT 1 FROM {SCHEMA}.marketing_postagens p
                WHERE p.creative_id = c.id
           )
        """  # noqa: S608 — SCHEMA é constante do módulo, não entrada de usuário.
    )


def downgrade() -> None:
    # De propósito: recriar linha vazia seria repor o lixo que esta migration
    # existe para tirar, e sem forma de saber quais eram.
    pass
