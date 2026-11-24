"""devolucao_rastreio: a Fraude automática volta a olhar o que largou por "negociação" — só dado

24/11/2026 (Vinicius: "nele precisa abrir os chamados sozinho"): o robô da Fraude
lia `negotiation_status == PENDING_RESPOND` da Shopee como "o comprador fez uma
proposta" e largava o pedido pra pessoa. No só reembolso esse status é o próprio
pedido do comprador esperando a loja (308634, conferido na tela em 17/11); foi o
que parou o 307496 e o 308570 com o vídeo já na mão. Agora só a proposta de valor
menor que o pedido fica pra pessoa. Aqui: nos pedidos ainda abertos, o recado
"… proposta na Shopee (negociação) — decidir à mão; o sistema não responde esse
caso sozinho (responder à mão)." vira um recado sem a marca de "já lido", e o robô
lê o caso de novo na próxima rodada. Imprime quantos pedidos no log do deploy.

O downgrade não devolve o recado (era só a marca de "já lido").

Revision ID: 0506_devolucao_fraude_reabre_negociacao
Revises: 0505_alertas_por_equipe
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0506_devolucao_fraude_reabre_negociacao"
down_revision: str | None = "0505_alertas_por_equipe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
VELHO = (
    "o comprador fez uma proposta na Shopee (negociação) — decidir à mão; "
    "o sistema não responde esse caso sozinho (responder à mão)."
)
NOVO = (
    'leu "proposta na Shopee", mas era o próprio pedido de reembolso do comprador '
    "(corrigido em 24/11) — o robô lê o caso de novo."
)


def _sql(schema: str) -> str:
    return (
        f'UPDATE "{schema}".devolucao_rastreio '  # noqa: S608 — nome fixo daqui
        "SET observacao = replace(observacao, :velho, :novo) "
        "WHERE resolvido_at IS NULL AND strpos(observacao, :velho) > 0"
    )


def reabrir(conn: sa.engine.Connection, schema: str) -> int:
    return conn.execute(sa.text(_sql(schema)), {"velho": VELHO, "novo": NOVO}).rowcount


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    n = reabrir(op.get_bind(), SCHEMA)
    print(f"[0506] pedidos da Fraude que o robô volta a olhar (largados por 'negociação'): {n}")


def downgrade() -> None:
    pass
