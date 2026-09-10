"""junta as duas cabeças: rastreio_lido_em (logística) e vw_perfis (chamados)

Duas sessões criaram migration a partir da 0255 no mesmo dia:
`0256_logistica_rastreio_lido_em` de um lado e a cadeia
`0256_segment_condicao_especial → 0257 → 0258 → 0259 → 0260 → 0261` do outro.
O alembic parou com "Multiple head revisions" e a coluna
`logistica.rastreio_lido_em` ficou sem aplicar — a API subiu e quebrava a tela
de Logística com UndefinedColumnError.

Merge vazio: só reúne as duas pontas, sem DDL.
"""

from collections.abc import Sequence

revision: str = "0262_merge_perfis_rastreio"
down_revision: tuple[str, ...] = (
    "0256_logistica_rastreio_lido_em",
    "0261_vw_perfis_prefere_israel",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
