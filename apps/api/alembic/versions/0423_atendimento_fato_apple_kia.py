"""atendimento: fato da Kia — produtos Apple novos e lacrados, garantia Apple de 1 ano (21/10/2026)

Eduardo, no chat de 21/10/2026 (caso "Esse relógio é recondicionado?" na Kia
Shopee), aprovou o fato para o agente (Claude) poder AFIRMAR ao cliente:
"Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano a
partir da ativação." Um fato ATIVO em `atendimento_fatos`, escopo `loja` (a
integração Shopee da Kia), sem assunto (vale para condição e garantia), fonte
`dono`.

Só dados, idempotente pela `chave` (`ON CONFLICT (chave) DO NOTHING`). O texto
fica CONGELADO aqui; o `tsv` é o mesmo que a aplicação calcula.

O downgrade apaga só esta chave.

Revision ID: 0423_atendimento_fato_apple_kia
Revises: 0422_atendimento_fatos_uranyx
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

revision: str = "0423_atendimento_fato_apple_kia"
down_revision: str | None = "0422_atendimento_fatos_uranyx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema
FATOS = "atendimento_fatos"

APROVADO_POR = "Eduardo (chat 21/10/2026)"
# A integração Shopee da Kia (o canal 1ca982c3, o piloto do agente).
KIA_SHOPEE = "2ac2633b-c698-4718-99cb-6e5443a80689"
SEMENTE: tuple[dict, ...] = (
    {
        "chave": "kia_apple_novo_lacrado",
        "texto": (
            "Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano "
            "a partir da ativação."
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
    # Sem a integração (banco de teste ou de outro ambiente), não há loja para o fato.
    existe = conn.execute(
        sa.text(f'SELECT 1 FROM "{SCHEMA}".integrations WHERE id = CAST(:id AS uuid)'),  # noqa: S608
        {"id": KIA_SHOPEE},
    ).first()
    if existe is None:
        return
    inserir = sa.text(
        f'INSERT INTO "{SCHEMA}".{FATOS} (chave, escopo, plataforma, integration_id, texto, '  # noqa: S608
        "fonte, referencia, ativo, aprovado_por, aprovado_em, tsv) "
        "VALUES (:chave, 'loja', 'shopee', CAST(:integration_id AS uuid), :texto, 'dono', "
        ":referencia, true, :aprovado_por, now(), "
        "setweight(to_tsvector('portuguese'::regconfig, :busca), 'A')) "
        "ON CONFLICT (chave) DO NOTHING"
    )
    for f in SEMENTE:
        conn.execute(
            inserir,
            {
                "chave": f["chave"],
                "integration_id": KIA_SHOPEE,
                "texto": f["texto"],
                "referencia": "decisão do dono no chat de 21/10/2026 (Apple da Kia)",
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
