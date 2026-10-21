"""atendimento: os dois fatos da garantia Uranyx aprovados pelo dono (21/10/2026)

Eduardo, no chat de 21/10/2026, aprovou o texto da garantia dos aparelhos
Uranyx para o agente (Claude) poder AFIRMAR ao cliente. Dois fatos ATIVOS em
`atendimento_fatos` (a tabela da 0420), da marca Uranyx, assunto `garantia`,
fonte `dono`:
  • `uranyx_garantia_1_ano`: 1 ano de garantia — os 90 primeiros dias com a
    loja e os 9 meses seguintes direto com a Uranyx;
  • `uranyx_garantia_caminho`: depois dos 90 dias, o caminho (o app Uranyx ou
    o site uranyx.com.br/sac).

Só dados, idempotente pela `chave` (`ON CONFLICT (chave) DO NOTHING`, como a
semente da 0420): rodar de novo não duplica, e o fato que já existe com a
mesma chave (alguém o reprovou depois, por exemplo) não é ressuscitado. Os
textos ficam CONGELADOS aqui; o `tsv` é o mesmo que a aplicação calcula
(`busca_texto.texto_de_busca` → `to_tsvector('portuguese')` com peso A).

O downgrade apaga só estas duas chaves.

Revision ID: 0422_atendimento_fatos_uranyx
Revises: 0421_devolucao_estoque_status
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

revision: str = "0422_atendimento_fatos_uranyx"
down_revision: str | None = "0421_devolucao_estoque_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema
FATOS = "atendimento_fatos"

APROVADO_POR = "Eduardo (chat 21/10/2026)"
# A mesma chave de marca da semente da 0420 (`sites_marca.URANYX`).
MARCA = "uranyx"
# Os fatos semeados — congelados em 21/10/2026.
SEMENTE: tuple[dict, ...] = (
    {
        "chave": "uranyx_garantia_1_ano",
        "texto": (
            "Aparelhos Uranyx homologados pela Uranyx têm 1 ano de garantia: os 90 "
            "primeiros dias com a loja e os 9 meses seguintes direto com a Uranyx."
        ),
    },
    {
        "chave": "uranyx_garantia_caminho",
        "texto": (
            "Depois dos 90 primeiros dias, a garantia do aparelho Uranyx é acionada "
            "direto com a Uranyx, pelo app Uranyx ou pelo site uranyx.com.br/sac."
        ),
    },
)
CHAVES = tuple(f["chave"] for f in SEMENTE)


def _busca(texto: str) -> str:
    """O mesmo de `services/atendimento/busca_texto.texto_de_busca` (congelado)."""
    base = unicodedata.normalize("NFKD", texto or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).lower().split())


def _semear() -> None:
    conn = op.get_bind()
    inserir = sa.text(
        f'INSERT INTO "{SCHEMA}".{FATOS} (chave, escopo, marca, assunto, texto, fonte, '  # noqa: S608
        "referencia, ativo, aprovado_por, aprovado_em, tsv) "
        "VALUES (:chave, 'marca', :marca, 'garantia', :texto, 'dono', :referencia, true, "
        ":aprovado_por, now(), setweight(to_tsvector('portuguese'::regconfig, :busca), 'A')) "
        "ON CONFLICT (chave) DO NOTHING"
    )
    for f in SEMENTE:
        conn.execute(
            inserir,
            {
                "chave": f["chave"],
                "marca": MARCA,
                "texto": f["texto"],
                "referencia": "decisão do dono no chat de 21/10/2026 (garantia Uranyx)",
                "aprovado_por": APROVADO_POR,
                "busca": _busca(f["texto"]),
            },
        )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    _semear()


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.get_bind().execute(
        sa.text(f'DELETE FROM "{SCHEMA}".{FATOS} WHERE chave = ANY(:chaves)'),  # noqa: S608
        {"chaves": list(CHAVES)},
    )
