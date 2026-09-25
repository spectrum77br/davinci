"""remove o robô "Vigia Chamados" (vigia_chamados) da Ouvidoria

Vinicius, 25/09/2026: "tem esse outro vigia de chamados … consegue localizar
ele e excluir. pode tirar todo acesso dele e desconectar". Estava DESLIGADO
desde 22/09 (não rodava nem registrava nada). O código saiu junto: catálogo,
agenda do worker, "Rodar agora" e os ganchos nos envios/consultas de chamados.
A leitura na tela das devoluções da Shopee é vigiada pelo "Vigia Robô Leitura
de Chamados" (vigia_robo_leitura).

Apaga a linha do painel e o histórico dela (ocorrências e rodadas). Sem volta
no downgrade: o robô não existe mais no código.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0323_remove_vigia_chamados"
down_revision: str | None = "0322_chamados_leitores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    for tabela in ("ouvidoria_ocorrencias", "ouvidoria_rodadas"):
        op.execute(
            f"DELETE FROM {SCHEMA}.{tabela} WHERE robo_chave = 'vigia_chamados'"  # noqa: S608
        )
    op.execute(f"DELETE FROM {SCHEMA}.ouvidoria_robos WHERE chave = 'vigia_chamados'")  # noqa: S608


def downgrade() -> None:
    pass
