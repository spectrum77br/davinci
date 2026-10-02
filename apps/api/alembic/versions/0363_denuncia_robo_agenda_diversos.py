"""denuncia_robo_agenda: passo 7 "Denúncias Diversos" ligado às 21:00

Vinicius, 02/10/2026: "criar um passo novo, que é denunciar na loja e tirar print daqueles que
grupo tá marcado como diversos… rodar de madrugada… antes do item 9". O 7 era o Relatório (saiu);
agora é o Diversos — denúncia nova uma vez só e os prints que sobraram; para sozinho quando o 9
(23:00) entra na fila. Começa ligado às 21:00 (muda na aba Robô).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0363_denuncia_robo_agenda_diversos"
down_revision: str | None = "0362_atendimento_carrinho_redes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO davinci.denuncia_robo_agenda (acao, ligado, horarios, atualizado_por) "
        "VALUES ('diversos', true, '[\"21:00\"]'::jsonb, 'migração 0363') "
        "ON CONFLICT (acao) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM davinci.denuncia_robo_agenda WHERE acao = 'diversos'")
