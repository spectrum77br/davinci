"""Conciliação — zera o custo de 7 devoluções que o Cairo finalizou antes da regra (22/10)

Só dado, sem tabela. Vinicius, 22/10: "pode zerar o custo dos 7 que o Cairo finalizou
como devolução voltou, registrando o motivo dele. O 301333 (em devolução) fica como
está." Os 7 foram finalizados à mão pelo cairo sa antes de existir o "manter ou zerar
o custo" (0425) e seguiam com o custo cheio no Sobrou (Bling em Resolvido conta).

Mexe só no pedido que continua finalizado pelo cairo sa, com custo e sem custo zerado
— o mesmo que "Zerar custo e finalizar" na tela: custo 0, regra "manual" (o recálculo
respeita), Sobrou + custo, margem vazia, motivo do Cairo guardado. Imprime cada um; o
deploy grava a saída no /var/log/davinci-deploy.log.

O downgrade não devolve o custo (o recálculo não sabe que era zerado à mão).

Revision ID: 0426_conciliacao_zera_custo_devolvidos_cairo
Revises: 0425_conciliacao_custo_zerado
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0426_conciliacao_zera_custo_devolvidos_cairo"
down_revision: str | None = "0425_conciliacao_custo_zerado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
PEDIDOS = (
    ("ATV (shopee)", "302328"),
    ("ATV (shopee)", "302216"),
    ("ATV (shopee)", "301992"),
    ("ATV (shopee)", "301119"),
    ("ATV (shopee)", "300634"),
    ("Barbosa (shopee)", "300836"),
    ("Barbosa (shopee)", "300559"),
)


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    for conta, pedido in PEDIDOS:
        linha = conn.execute(
            sa.text(
                f'UPDATE "{SCHEMA}".conciliacao_pedidos SET '  # noqa: S608 — nome fixo daqui
                "sobrou = CASE WHEN sobrou IS NULL THEN NULL ELSE sobrou + custo END, "
                "custo = 0, margem = NULL, custo_regra = 'manual', "
                "custo_zerado_por = 'Vinicius (correção de 22/10)', custo_zerado_em = now(), "
                "custo_zerado_motivo = 'motivo do cairo sa ao finalizar: ' "
                "|| coalesce(etapa_motivo, '') "
                "WHERE conta = :conta AND pedido_bling = :pedido AND etapa = 'finalizado' "
                "AND etapa_por = 'cairo sa' AND custo > 0 AND custo_zerado_em IS NULL "
                "RETURNING sobrou, custo_zerado_motivo"
            ),
            {"conta": conta, "pedido": pedido},
        ).first()
        if linha is None:
            print(f"[0426] NADA MUDOU em {conta} {pedido}: não está como esperado")
        else:
            print(
                f"[0426] OK {conta} {pedido}: sobrou {linha.sobrou} ({linha.custo_zerado_motivo})"
            )


def downgrade() -> None:
    pass
