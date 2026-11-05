"""Chamados: coluna Revisão (Conferido / Revisar / Fixo) e conversão da Observação

Vinicius (05/11/2026): a revisão diária saiu da Observação, onde ele anotava à mão
("5-ok", "5-revisar", "25/11 - até essa data conferir se caiu o valor"), e virou
marca. Espelha `app/models/chamado.py`:

- `chamados.revisao_*`: a marca de agora.
- `chamado_revisoes`: o registro de todas as marcas.

Conversão ("converter em marcas", decisão dele): só dos chamados abertos e só da
primeira linha da Observação, que é onde a anotação fica — o resto do texto
continua lá.

- "N-ok", "N - conferido", "N-revisar": no dia N == hoje viram Conferido/Revisar; em
  outro dia já venceram e só saem do texto.
- "revisar" sozinho (sem dia): sai do texto; o chamado fica pra revisar.
- "dd/mm - motivo": vira Fixo até a data, mesmo vencida (aparece como prazo vencido).

Qualquer outro texto fica como está. O original vai para
`chamado_revisoes.observacao_antes`, e o downgrade devolve.

Revision ID: 0451_chamados_revisao
Revises: 0450_compra_emissao_robo
"""

import re
from collections.abc import Sequence
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0451_chamados_revisao"
down_revision: str | None = "0450_compra_emissao_robo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_DIA = re.compile(r"^(\d{1,2})\s*-\s*(ok|conferido|revisar)\.?$", re.IGNORECASE)
_SO_REVISAR = re.compile(r"^revisar\.?$", re.IGNORECASE)
_DATA = re.compile(r"^(\d{1,2})/(\d{1,2})\s*-\s*(.+)$")
_ATE_ESSA_DATA = re.compile(r"^at[eé]\s+essa\s+data\s+", re.IGNORECASE)


def _interpretar(texto: str | None, hoje: date) -> dict | None:
    """O que fazer com a anotação; None = não é anotação de revisão, não mexe.
    `resto` é o que fica na Observação."""
    linhas = (texto or "").strip().splitlines()
    if not linhas:
        return None
    primeira = linhas[0].strip()
    resto = "\n".join(linhas[1:]).strip() or None
    if _SO_REVISAR.match(primeira):
        return {"tipo": None, "ate": None, "motivo": None, "resto": resto}
    m = _DIA.match(primeira)
    if m:
        tipo = None
        if int(m.group(1)) == hoje.day:
            tipo = "revisar" if m.group(2).lower() == "revisar" else "conferido"
        return {"tipo": tipo, "ate": None, "motivo": None, "resto": resto}
    m = _DATA.match(primeira)
    if m:
        try:
            ate = date(hoje.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        motivo = _ATE_ESSA_DATA.sub("", m.group(3).strip()).rstrip(". ")
        return {"tipo": "fixo", "ate": ate, "motivo": motivo or None, "resto": resto}
    return None


def _converter(bind, schema: str = SCHEMA, hoje: date | None = None) -> int:
    hoje = hoje or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    feitas = 0
    linhas = bind.execute(
        sa.text(
            f"SELECT id, observacao FROM {schema}.chamados "  # noqa: S608
            "WHERE resolvido = false AND observacao IS NOT NULL"
        )
    ).all()
    for chamado_id, observacao in linhas:
        r = _interpretar(observacao, hoje)
        if r is None:
            continue
        bind.execute(
            sa.text(
                f"UPDATE {schema}.chamados SET observacao = :resto, revisao_tipo = :tipo, "  # noqa: S608
                "revisao_dia = :dia, revisao_ate = :ate, revisao_motivo = :motivo, "
                "revisao_at = CASE WHEN CAST(:tipo AS text) IS NULL THEN NULL ELSE now() END "
                "WHERE id = :id"
            ),
            {
                "id": chamado_id,
                "resto": r["resto"],
                "tipo": r["tipo"],
                "dia": hoje if r["tipo"] else None,
                "ate": r["ate"],
                "motivo": r["motivo"],
            },
        )
        bind.execute(
            sa.text(
                f"INSERT INTO {schema}.chamado_revisoes "  # noqa: S608
                "(id, chamado_id, tipo, dia, ate, motivo, observacao_antes) "
                "VALUES (:novo, :id, 'convertido', :dia, :ate, :motivo, :antes)"
            ),
            {
                "novo": uuid4(),
                "id": chamado_id,
                "dia": hoje,
                "ate": r["ate"],
                "motivo": r["motivo"],
                "antes": observacao,
            },
        )
        feitas += 1
    return feitas


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for nome, tipo in (
        ("revisao_tipo", sa.Text()),
        ("revisao_dia", sa.Date()),
        ("revisao_ate", sa.Date()),
        ("revisao_motivo", sa.Text()),
        ("revisao_por_nome", sa.Text()),
        ("revisao_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("chamados", sa.Column(nome, tipo, nullable=True), schema=SCHEMA)
    op.create_table(
        "chamado_revisoes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chamado_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.chamados.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("dia", sa.Date(), nullable=False),
        sa.Column("ate", sa.Date(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("autor_nome", sa.Text(), nullable=True),
        sa.Column(
            "autor_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("observacao_antes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chamado_revisoes_chamado_id", "chamado_revisoes", ["chamado_id"], schema=SCHEMA
    )
    _converter(op.get_bind())


def downgrade() -> None:
    op.execute(
        f"UPDATE {SCHEMA}.chamados c SET observacao = r.observacao_antes "  # noqa: S608
        f"FROM {SCHEMA}.chamado_revisoes r "
        "WHERE r.chamado_id = c.id AND r.tipo = 'convertido'"
    )
    op.drop_index("ix_chamado_revisoes_chamado_id", table_name="chamado_revisoes", schema=SCHEMA)
    op.drop_table("chamado_revisoes", schema=SCHEMA)
    for nome in (
        "revisao_at",
        "revisao_por_nome",
        "revisao_motivo",
        "revisao_ate",
        "revisao_dia",
        "revisao_tipo",
    ):
        op.drop_column("chamados", nome, schema=SCHEMA)
