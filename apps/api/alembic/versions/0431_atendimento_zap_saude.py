"""atendimento: Zap — a tela "Números do WhatsApp", a saúde e a limpeza (RF10, Onda 4)

27/10/2026 (docs/zap/desenho.md §8.4, §8.5, §10.5 e §12 "Onda 4"). Só
aditivo, em cima das seis tabelas da 0430:

  • `atendimento_zap_contas.saude` (JSONB, nasce `{}`): o que o cron de saúde
    e os botões da tela viram na Meta — os apps inscritos na WABA (o do Duoke
    que ficou lá é o alarme "as mensagens também vão para outro lugar") e a
    última inspeção do token que falhou. Só ids e frases, nunca o token;
  • `atendimento_zap_numeros.caso_migracao` (A / A2 / B, anulável, com CHECK):
    o caso do roteiro do dia da migração (§13.3). O botão "registrar" só
    aparece com A ou B na etapa `migrando` — no A2 o número já está
    registrado pelo Duoke, e registrar de novo só queimaria tentativas (a
    Meta bloqueia o número por 72 h na 10ª);
  • índices: `atendimento_zap_eventos (recebido_em)` (a retenção de 30 dias e
    o alarme do backlog), `atendimento_zap_midias (status, proxima_em)` (a
    fila do baixador) e `(apagar_em)` (a retenção de 180 dias). As tabelas são
    pequenas hoje; o índice entra antes do primeiro número real.

Nada é preenchido: as linhas que já existem ficam com `saude = '{}'` e sem
caso. Tudo continua desligado (as chaves `ATENDIMENTO_ZAP_*` nascem False).

`SET LOCAL lock_timeout` de 3 s, como a 0430. O downgrade tira o que entrou
(o que foi visto na Meta e o caso marcado se perdem; a tela volta a pedir).

Nasceu em cima da 0430 do Zap. O origin/main já tem OUTRA 0430
(`0430_integration_carrefour`): na publicação as duas do Zap podem precisar
ser renumeradas (a 0430 do Zap passa a vir depois da do main, e esta junto).

Revision ID: 0431_atendimento_zap_saude
Revises: 0430_atendimento_zap
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0431_atendimento_zap_saude"
down_revision: str | None = "0430_atendimento_zap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CONTAS = "atendimento_zap_contas"
NUMEROS = "atendimento_zap_numeros"
EVENTOS = "atendimento_zap_eventos"
MIDIAS = "atendimento_zap_midias"

# Cópia de `app/models/atendimento_zap.ZAP_NUMERO_CASOS` (migration não importa
# model; o teste da migration confere que batem).
NUMERO_CASOS = ("A", "A2", "B")


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column(
        CONTAS,
        sa.Column("saude", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(NUMEROS, sa.Column("caso_migracao", sa.String(4), nullable=True), schema=SCHEMA)
    lista = ", ".join(f"'{c}'" for c in NUMERO_CASOS)
    op.create_check_constraint(
        op.f(f"ck_{NUMEROS}_caso_migracao"),
        NUMEROS,
        f"caso_migracao IS NULL OR caso_migracao IN ({lista})",
        schema=SCHEMA,
    )
    op.create_index(op.f(f"ix_{EVENTOS}_recebido_em"), EVENTOS, ["recebido_em"], schema=SCHEMA)
    op.create_index(
        op.f(f"ix_{MIDIAS}_status_proxima_em"), MIDIAS, ["status", "proxima_em"], schema=SCHEMA
    )
    op.create_index(op.f(f"ix_{MIDIAS}_apagar_em"), MIDIAS, ["apagar_em"], schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_index(op.f(f"ix_{MIDIAS}_apagar_em"), table_name=MIDIAS, schema=SCHEMA)
    op.drop_index(op.f(f"ix_{MIDIAS}_status_proxima_em"), table_name=MIDIAS, schema=SCHEMA)
    op.drop_index(op.f(f"ix_{EVENTOS}_recebido_em"), table_name=EVENTOS, schema=SCHEMA)
    op.drop_constraint(op.f(f"ck_{NUMEROS}_caso_migracao"), NUMEROS, type_="check", schema=SCHEMA)
    op.drop_column(NUMEROS, "caso_migracao", schema=SCHEMA)
    op.drop_column(CONTAS, "saude", schema=SCHEMA)
