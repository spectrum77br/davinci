"""devolucao_rastreio: a Fraude automática volta a olhar o que largou por "dano" — só dado

16/11/2026 (Vinicius, 307238 — "O item não corresponde à descrição"): "teria que
responder já, mostrando vídeo e falando que enviamos certinho". A 1ª versão da IA
da defesa tinha uma trava fixa que mandava pra pessoa todo motivo padronizado de
dano/defeito/"descrição" e gravava na Observação do pedido o recado "… o motivo que
o comprador escolheu é dano/defeito — o vídeo da embalagem não rebate; o sistema não
responde esse caso sozinho (responder à mão)." Com esse recado o robô não relê o
pedido. Agora esses casos viram defesa com o vídeo: tira SÓ essa linha do recado,
e o robô olha de novo na próxima rodada. Imprime quantos pedidos no log do deploy.

O downgrade não devolve o recado (era só a marca de "já lido").

Revision ID: 0492_devolucao_fraude_reabre_dano
Revises: 0491_flex_modelos_shopee
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0492_devolucao_fraude_reabre_dano"
down_revision: str | None = "0491_flex_modelos_shopee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TRECHO = "o motivo que o comprador escolheu é dano/defeito"


def _sql(schema: str) -> str:
    # A linha inteira do recado (com a quebra de linha antes dela, se houver).
    return (
        f'UPDATE "{schema}".devolucao_rastreio '  # noqa: S608 — nome fixo daqui
        "SET observacao = NULLIF(btrim(regexp_replace(observacao, "
        "'\\n?Fraude automática \\([^)]*\\): [^\\n]*' || :trecho || '[^\\n]*', '', 'g')), '') "
        "WHERE observacao LIKE '%' || :trecho || '%'"
    )


def reabrir(conn: sa.engine.Connection, schema: str) -> int:
    return conn.execute(sa.text(_sql(schema)), {"trecho": TRECHO}).rowcount


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    n = reabrir(op.get_bind(), SCHEMA)
    print(f"[0492] pedidos da Fraude que o robô volta a olhar (largados por 'dano'): {n}")


def downgrade() -> None:
    pass
