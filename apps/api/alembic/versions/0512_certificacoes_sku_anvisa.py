"""Certificações: Anvisa no Certificado (M-68)

Pedido M-68 (aprovado pelo administrador em 26/11/2026; contrato em
relatorios/mudancas_26nov/contrato_M66_M74.md): a coluna Certificado da aba
Importação › Certificações ganha a opção "Anvisa", só manual (não existe
consulta de Anvisa). O valor continua gravado em minúsculas no mesmo campo
`financeiro_suprimentos.certificado`.

ESTRUTURA (upgrade):
- Refaz a trava do campo `certificado`, que passa a aceitar também 'anvisa':
  NULL, '', 'anatel', 'inmetro', 'anvisa' ou 'isento'.
- Nome da trava: a 0083 declarou `ck_financeiro_suprimentos_certificado`, mas a
  convenção de nomes do Base (ck_%(table_name)s_%(constraint_name)s) foi
  aplicada por cima e o nome que existe no banco é
  `ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado` (conferido
  na cópia da produção, previa52). A trava nova fica com ESSE nome (o mesmo de
  hoje). Por segurança, apaga também a grafia curta com IF EXISTS.
- O model não declara essa trava (models/financeiro.py); quem barra valor
  fora da lista na API é o schema (SuprimentosPatch).
- Nenhum dado muda: todos os valores aceitos antes continuam aceitos.

M-69 (etapa 2, ainda não feita): a tabela `financeiro_suprimento_skus` e a
conversão da ligação pelo nome entram NESTA MESMA migration, como funções
próprias chamadas pelo upgrade()/downgrade() abaixo (veja os marcadores).

`SET LOCAL lock_timeout` como na 0505…0511: o ALTER segura a tabela por um
instante (são poucas dezenas de linhas); se algo a estiver segurando, desiste
em 5 s e é só rodar de novo.

DOWNGRADE: volta a trava antiga (mesmo nome). Se houver
certificação marcada como 'anvisa', PARA com RuntimeError e não muda nada:
nenhum dado é apagado nem trocado sozinho (troque o Certificado dessas linhas
na tela antes de voltar).

Revision ID: 0512_certificacoes_sku_anvisa
Revises: 0511_contas_ml_par_custo_total
"""

# ruff: noqa: S608 — SQL montado só com nomes e valores fixos desta migration (schema), sem entrada de fora
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0512_certificacoes_sku_anvisa"
down_revision: str | None = "0511_contas_ml_par_custo_total"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "financeiro_suprimentos"
# O nome que a 0083 deixou no banco (a convenção do Base aplicada por cima do
# nome declarado). A trava nova usa o mesmo nome.
TRAVA_CERTIFICADO = "ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado"
# A grafia que a 0083 declarou; só por segurança (não existe nos bancos conferidos).
TRAVA_CERTIFICADO_CURTA = "ck_financeiro_suprimentos_certificado"

# Valores aceitos no campo `certificado` (sempre em minúsculas; '' = sem).
CERTIFICADOS_0083 = ("anatel", "inmetro", "isento", "")
CERTIFICADOS = ("anatel", "inmetro", "anvisa", "isento", "")


def condicao_certificado(valores: Sequence[str]) -> str:
    """O CHECK do campo `certificado` para estes valores (o texto da 0083)."""
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"certificado IS NULL OR certificado IN ({lista})"


def trocar_trava_certificado(bind, valores: Sequence[str]) -> None:
    """Apaga a trava do `certificado` (qualquer das duas grafias) e cria a
    mesma trava com estes valores."""
    for antigo in (TRAVA_CERTIFICADO, TRAVA_CERTIFICADO_CURTA):
        bind.execute(sa.text(f"ALTER TABLE {SCHEMA}.{TABELA} DROP CONSTRAINT IF EXISTS {antigo}"))
    bind.execute(
        sa.text(
            f"ALTER TABLE {SCHEMA}.{TABELA} ADD CONSTRAINT {TRAVA_CERTIFICADO}"
            f" CHECK ({condicao_certificado(valores)})"
        )
    )


def anvisa_no_certificado(bind) -> None:
    """M-68: a trava passa a aceitar 'anvisa'."""
    trocar_trava_certificado(bind, CERTIFICADOS)


def tirar_anvisa_do_certificado(bind) -> None:
    """Volta a trava da 0083. Linha com 'anvisa' → para sem mudar nada."""
    linhas = (
        bind.execute(
            sa.text(
                f"SELECT coalesce(nullif(trim(modelo), ''), nullif(trim(produto), ''), id::text)"
                f" FROM {SCHEMA}.{TABELA} WHERE certificado = 'anvisa' ORDER BY 1"
            )
        )
        .scalars()
        .all()
    )
    if linhas:
        raise RuntimeError(
            f"{len(linhas)} certificação(ões) com Certificado = Anvisa: "
            + ", ".join(linhas)
            + ". Troque o Certificado dessas linhas na tela antes de voltar a migration."
        )
    trocar_trava_certificado(bind, CERTIFICADOS_0083)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    anvisa_no_certificado(bind)
    # M-69 (etapa 2): criar `financeiro_suprimento_skus` e converter a ligação
    # pelo nome entram aqui, depois da trava.


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    # M-69 (etapa 2): desfazer a tabela de SKUs entra aqui, antes da trava.
    tirar_anvisa_do_certificado(bind)
