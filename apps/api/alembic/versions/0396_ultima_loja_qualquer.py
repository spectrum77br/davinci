"""atendimento: `ultima_loja_qualquer_em` — o "Falta responder" igual ao Duoke

O dono, 14/10/2026: "tem que arrumar pro atendimento ter literalmente o
comportamento do Duoke, pra estar marcado literalmente as mesmas". A Caixa
mostrava Shopee 99+ (ATV 65, vortan 60…) e o "Pendente" do Duoke, 15. Para o
Duoke, qualquer mensagem da loja depois do comprador é resposta — até a
automática (o menu do robô, "a sua mensagem foi recebida", a figurinha, o
cupom, o cartão de pedido); o DaVinci só contava a resposta de pessoa
(`ultima_da_loja_em`, a FILA da IA e do prazo — que continua como está).

Esta migration cria o lado da loja da régua nova
(`services/atendimento/pendente.py`): a hora da ÚLTIMA mensagem da loja de
qualquer tipo — menos a que falhou (não saiu), a nota interna (não é
mensagem) e o e-mail da ponte gravado dentro de um chat (não aparece nele,
`separa_email`). É a mesma conta do `gravar.recalcular_conversa` (o teste
confere as duas); daqui para frente quem mantém é o `gravar.recalcular`.

  • Uma coluna nova, anulável, sem índice (a régua entra num COUNT FILTER e
    num WHERE da lista que já varrem as conversas da Caixa).
  • O preenchimento é um UPDATE só, pelas mensagens já gravadas (~100 mil em
    produção). A conversa sem mensagem da loja fica NULL.
  • DOWNGRADE: tira a coluna (nada mais dependia dela) — volta exatamente ao
    que era.

`lock_timeout` de 10 s, como as outras: se o sync segurar a tabela por mais
que isso, a migration falha inteira (nada gravado) e é só rodar de novo.

Revision ID: 0396_ultima_loja_qualquer
Revises: 0395_cairo_tabela_precos_contas
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0396_ultima_loja_qualquer"
down_revision: str | None = "0395_cairo_tabela_precos_contas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TABELA = "atendimento_conversas"
COLUNA = "ultima_loja_qualquer_em"

# Congelados aqui (a migration não muda quando o código mudar): os mesmos de
# `constantes` em 14/10/2026.
AUTOR_LOJA = "loja"
MSG_FALHOU = "falhou"
ORIGEM_NOTA = "davinci_nota"
TIPO_NOTA = "nota"
CANAL_EMAIL = "email"
FONTE_TUTA = "tuta"


def _preencher(schema: str) -> sa.TextClause:
    """O UPDATE que preenche a coluna pelas mensagens. `schema` vem do código."""
    return sa.text(
        f"""
        UPDATE "{schema}".atendimento_conversas c
        SET {COLUNA} = x.quando
        FROM (
            SELECT m.conversa_id, max(coalesce(m.enviada_em, m.created_at)) AS quando
            FROM "{schema}".atendimento_mensagens m
            JOIN "{schema}".atendimento_conversas c2 ON c2.id = m.conversa_id
            WHERE m.autor = :autor_loja
              AND coalesce(m.status, '') <> :falhou
              AND coalesce(m.origem, '') <> :origem_nota
              AND coalesce(m.tipo, '') <> :tipo_nota
              AND (
                  (c2.canal = :canal_email
                   AND coalesce(btrim(c2.dados ->> 'fonte'), '') = :fonte_tuta)
                  OR (m.payload -> 'mail') IS NULL
              )
            GROUP BY m.conversa_id
        ) x
        WHERE c.id = x.conversa_id AND c.{COLUNA} IS DISTINCT FROM x.quando
        """  # noqa: S608 — o schema e a coluna são do código; os valores vão por parâmetro
    ).bindparams(
        autor_loja=AUTOR_LOJA,
        falhou=MSG_FALHOU,
        origem_nota=ORIGEM_NOTA,
        tipo_nota=TIPO_NOTA,
        canal_email=CANAL_EMAIL,
        fonte_tuta=FONTE_TUTA,
    )


def preencher(conexao: Any, schema: str) -> int:
    """Preenche `ultima_loja_qualquer_em` de todas as conversas → quantas mudaram.

    Síncrona (a conexão da migration; o teste roda no schema dele).
    """
    return conexao.execute(_preencher(schema)).rowcount or 0


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        TABELA,
        sa.Column(COLUNA, sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    preencher(op.get_bind(), SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column(TABELA, COLUNA, schema=SCHEMA)
