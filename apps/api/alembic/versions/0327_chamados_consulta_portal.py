"""chamados.consulta_portal: a consulta do Portal de Atendimento ao Vendedor da Shopee

Vinicius/Cairo (25/09/2026, pedido 294571): a devolução é acompanhada pela API
pelo nº da solicitação (`chamado` = 260914016HQB8XN), mas a pessoa abriu À MÃO
uma consulta no Portal de Atendimento (seller-service.cs.shopee.com.br, id
2103108212314644514) e anotou só na Observação. O executor de leitura não
sabia que tinha que ler lá. Os dois números passam a morar no chamado:

  chamado          nº da devolução (API + Seller Center) — como sempre
  consulta_portal  ID da consulta no Portal (só dígitos) — o executor lê também

Preenchimento: à mão no chamado (link ou número), ou sozinho quando o link/ID
aparece na Observação ou numa instrução. Esta migração faz o mesmo com o que
já está escrito nos chamados abertos da Shopee.

Revision ID: 0327_chamados_consulta_portal
Revises: 0326_companies_ip_adspower
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0327_chamados_consulta_portal"
down_revision: str | None = "0326_companies_ip_adspower"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

_LINK = r"seller-service\.cs\.shopee\.com\.br/detail/([0-9]{15,})"
_ID = r"(?i)consulta[^0-9]{0,40}?([0-9]{19})"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column("chamados", sa.Column("consulta_portal", sa.Text(), nullable=True), schema=SCHEMA)
    alvo = (
        "c.consulta_portal IS NULL AND c.resolvido IS FALSE "
        "AND COALESCE(c.chamado_de_tela, false) IS FALSE "
        "AND lower(btrim(COALESCE(c.plataforma, ''))) LIKE '%shopee%'"
    )
    op.execute(
        f"UPDATE {SCHEMA}.chamados c SET consulta_portal = COALESCE("  # noqa: S608
        f"substring(c.observacao from '{_LINK}'), substring(c.observacao from '{_ID}')) "
        f"WHERE {alvo} AND c.observacao IS NOT NULL"
    )
    op.execute(
        f"UPDATE {SCHEMA}.chamados c SET consulta_portal = COALESCE("  # noqa: S608
        f"substring(i.textos from '{_LINK}'), substring(i.textos from '{_ID}')) "
        f"FROM (SELECT chamado_id, string_agg(texto, ' ') AS textos FROM {SCHEMA}.chamado_mensagem "
        "WHERE tipo = 'instrucao' GROUP BY chamado_id) i "
        f"WHERE i.chamado_id = c.id AND {alvo}"
    )


def downgrade() -> None:
    op.drop_column("chamados", "consulta_portal", schema=SCHEMA)
