"""atendimento: tira o e-mail da PRÉVIA e da ORDEM das conversas de chat

O dono, 13/10/2026: "vamos deixar o e-mail aparecer só na aba de e-mail; a do
chat de atendimento aparece só no chat mesmo". Até 13/10/2026 a ponte da
Central de e-mail gravava o aviso por e-mail das plataformas com API (ML,
Shopee, TikTok, Amazon) DENTRO da conversa de chat do pedido (mensagem com
`payload.mail`). O código novo (`services/atendimento/separa_email.py`) já
esconde essas mensagens pela consulta — detalhe, abas, tipo da prévia,
recálculo da fila —, sem apagar nada.

Mas a prévia e a ordem da Caixa são CARIMBOS guardados na conversa
(`ultima_mensagem_em`, `ultima_mensagem_resumo`, `ultima_autor`) e só se
refazem quando chega mensagem nova. Medido em produção (13/10/2026, só
contagens): 40 conversas de chat têm um e-mail como última mensagem (Shopee
37, TikTok 2, ML 1) — a linha mostra "E-mail da plataforma…" e a busca da
Caixa acha a conversa pelo texto do e-mail. Esta migration refaz SÓ esses três
carimbos, pela última mensagem VISÍVEL (fora a nota interna e o e-mail), nas
conversas de chat que têm e-mail gravado — a mesma conta do
`gravar.recalcular_conversa` (o teste confere as duas).

  • Só DADOS, nenhuma coluna nem índice. A fila ("falta responder", prazo,
    situação, `ultima_do_cliente_em`, `ultima_da_loja_em`) NÃO é tocada: o
    aviso era `sistema`, nunca contou como fala do cliente nem da loja.
  • A conversa de e-mail da ponte (canal 'email' + `dados.fonte = 'tuta'`) e
    a Amazon pelo Gmail (sem e-mail da ponte dentro) ficam como estão.
  • Cada linha só é gravada se os carimbos ainda forem os que foram lidos: a
    conversa que recebeu mensagem nova no meio já está certa e fica de fora.
  • Nenhuma mensagem é apagada. O DOWNGRADE refaz os mesmos carimbos COM o
    e-mail (a conta de antes de 13/10/2026): a prévia volta a ser a de antes.

`lock_timeout` de 10 s, como as outras: se o sync segurar uma dessas linhas
por mais que isso, a migration falha inteira (nada gravado) e é só rodar de
novo.

Revision ID: 0391_email_fora_do_chat
Revises: 0390_denuncia_juridico_pasta
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0391_email_fora_do_chat"
down_revision: str | None = "0390_denuncia_juridico_pasta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Congelados aqui (a migration não muda quando o código mudar): os mesmos de
# `gravar.resumo` / `constantes` em 13/10/2026.
RESUMO_MAX = 160
ORIGEM_NOTA = "davinci_nota"
TIPO_NOTA = "nota"


def _resumo(texto: str | None, tipo: str | None) -> str:
    """`gravar.resumo(texto) or f"[{tipo or 'outro'}]"` — a prévia da lista."""
    uma_linha = " ".join((texto or "").split())
    if len(uma_linha) > RESUMO_MAX:
        uma_linha = uma_linha[: RESUMO_MAX - 1].rstrip() + "…"
    return uma_linha or f"[{tipo or 'outro'}]"


def _consulta(schema: str, *, esconder_email: bool) -> sa.TextClause:
    """As conversas de CHAT com e-mail da ponte gravado + a última mensagem que conta.

    `esconder_email` = a régua nova (o e-mail não conta); sem ela, a de antes.
    `schema` vem do código (a constante, ou o schema do teste), nunca de fora.
    """
    so_visiveis = "AND (m.payload -> 'mail') IS NULL" if esconder_email else ""
    return sa.text(
        f"""
        WITH com_email AS (
            SELECT DISTINCT m.conversa_id AS id
            FROM "{schema}".atendimento_mensagens m
            WHERE (m.payload -> 'mail') IS NOT NULL
        ),
        chat AS (
            SELECT c.id, c.ultima_mensagem_em, c.ultima_mensagem_resumo, c.ultima_autor
            FROM "{schema}".atendimento_conversas c
            JOIN com_email e ON e.id = c.id
            WHERE NOT (
                c.canal = 'email' AND coalesce(btrim(c.dados ->> 'fonte'), '') = 'tuta'
            )
        ),
        ultima AS (
            SELECT DISTINCT ON (m.conversa_id)
                m.conversa_id,
                coalesce(m.enviada_em, m.created_at) AS quando,
                m.texto,
                m.tipo,
                m.autor
            FROM "{schema}".atendimento_mensagens m
            JOIN chat ON chat.id = m.conversa_id
            WHERE m.origem <> :origem_nota AND m.tipo <> :tipo_nota {so_visiveis}
            ORDER BY m.conversa_id, coalesce(m.enviada_em, m.created_at) DESC,
                     m.created_at DESC
        )
        SELECT chat.id, chat.ultima_mensagem_em, chat.ultima_mensagem_resumo,
               chat.ultima_autor, u.quando, u.texto, u.tipo, u.autor
        FROM chat
        LEFT JOIN ultima u ON u.conversa_id = chat.id
        ORDER BY chat.id
        """  # noqa: S608 — o schema é do código; os valores vão por parâmetro
    ).bindparams(origem_nota=ORIGEM_NOTA, tipo_nota=TIPO_NOTA)


def _gravar(schema: str) -> sa.TextClause:
    """Grava os três carimbos SÓ se eles ainda forem os que foram lidos."""
    return sa.text(
        f"""
        UPDATE "{schema}".atendimento_conversas
        SET ultima_mensagem_em = :em, ultima_mensagem_resumo = :resumo, ultima_autor = :autor
        WHERE id = :id
          AND ultima_mensagem_em IS NOT DISTINCT FROM :antes_em
          AND ultima_mensagem_resumo IS NOT DISTINCT FROM :antes_resumo
          AND ultima_autor IS NOT DISTINCT FROM :antes_autor
        """  # noqa: S608 — o schema é do código; os valores vão por parâmetro
    ).bindparams(
        sa.bindparam("em", type_=sa.DateTime(timezone=True)),
        sa.bindparam("antes_em", type_=sa.DateTime(timezone=True)),
        sa.bindparam("resumo", type_=sa.Text()),
        sa.bindparam("antes_resumo", type_=sa.Text()),
        sa.bindparam("autor", type_=sa.String()),
        sa.bindparam("antes_autor", type_=sa.String()),
    )


def refazer(conexao: Any, schema: str, *, esconder_email: bool) -> dict[str, int]:
    """Refaz a prévia/ordem das conversas de chat com e-mail → {conversas, mudaram}.

    Síncrona (a conexão da migration). `esconder_email=True` é o upgrade.
    """
    linhas = conexao.execute(_consulta(schema, esconder_email=esconder_email)).all()
    mudaram = 0
    for cid, antes_em, antes_resumo, antes_autor, quando, texto, tipo, autor in linhas:
        depois: tuple[datetime | None, str | None, str | None]
        if quando is None:
            depois = (None, None, None)
        else:
            depois = (quando, _resumo(texto, tipo), autor)
        if depois == (antes_em, antes_resumo, antes_autor):
            continue
        r = conexao.execute(
            _gravar(schema),
            {
                "id": cid,
                "em": depois[0],
                "resumo": depois[1],
                "autor": depois[2],
                "antes_em": antes_em,
                "antes_resumo": antes_resumo,
                "antes_autor": antes_autor,
            },
        )
        mudaram += r.rowcount or 0
    return {"conversas": len(linhas), "mudaram": mudaram}


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    refazer(op.get_bind(), SCHEMA, esconder_email=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    refazer(op.get_bind(), SCHEMA, esconder_email=False)
