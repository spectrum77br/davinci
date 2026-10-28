"""Conciliação — o painel recomeça do zero em 24/10/2026, em todas as lojas

Vinicius, 28/10: "vamos zerar ... puxar as informações do dia 24/10 para frente; daí
arrumamos dia por dia, agora acumulou muito". O painel ligou em 01/10 e juntou 46 dias
abertos, 44 linhas sem dono e 69 avisos de pedido — quase tudo de antes de 24/10.

O que faz, em todas as lojas da conciliação:

* guarda uma cópia das cinco tabelas como estavam (`<tabela>_ate_20261028`): é a volta,
  se precisar;
* apaga as linhas do extrato e da fatura (`conciliacao_movimentos`), os pedidos
  (`conciliacao_pedidos`), os dias fechados e reabertos (`conciliacao_dias`) e o
  controle das leituras (`conciliacao_sync`) — sem o controle, cada leitura pede de novo
  a partir do início da loja;
* o início de toda loja vira 24/10: pedido vendido antes é "anterior ao início"; o
  extrato do Mercado Pago, a carteira da Shopee, os extratos da TikTok e as transações
  da Amazon são lidos de 24/10 em diante;
* "última leitura", "último recálculo" e o último erro voltam a vazio: a loja aparece
  "extrato não lido" até a primeira rodada, e a carga inicial a pega.

Fica como está: os fretes pagos no Melhor Envio (`conciliacao_fretes_pagos`, fato do
Melhor Envio, relido pela janela) e o que o arquivo da Amazon KFA gravou na Margem.
Imprime quanto apagou de cada tabela; o deploy grava a saída no
/var/log/davinci-deploy.log.

DOWNGRADE: devolve as tabelas a partir da cópia e apaga a cópia.

Revision ID: 0437_conciliacao_recomeca_24_10
Revises: 0436_atendimento_canal_auto_continuacao
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa

from alembic import op

revision: str = "0437_conciliacao_recomeca_24_10"
down_revision: str | None = "0436_atendimento_canal_auto_continuacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"
INICIO = date(2026, 10, 24)
SUFIXO = "_ate_20261028"
APAGADAS = ("conciliacao_movimentos", "conciliacao_pedidos", "conciliacao_dias", "conciliacao_sync")
COPIADAS = (*APAGADAS, "conciliacao_lojas")
CAMPOS_DA_LOJA = (
    "inicio",
    "ultimo_extrato_em",
    "ultimo_fatura_em",
    "ultimo_recalculo_em",
    "ultimo_erro",
)


def _t(nome: str) -> str:
    return f'"{SCHEMA}"."{nome}"'


def upgrade() -> None:
    op.execute("SET lock_timeout = '30s'")
    conn = op.get_bind()
    for tabela in COPIADAS:
        conn.execute(sa.text(f"CREATE TABLE {_t(tabela + SUFIXO)} AS TABLE {_t(tabela)}"))
    for tabela in APAGADAS:
        n = conn.execute(sa.text(f"DELETE FROM {_t(tabela)}")).rowcount  # noqa: S608
        print(f"[0437] {tabela}: {n} linha(s) apagada(s) (cópia em {tabela}{SUFIXO})")
    n = conn.execute(
        sa.text(
            f"UPDATE {_t('conciliacao_lojas')} SET inicio = :inicio, "  # noqa: S608
            "ultimo_extrato_em = NULL, ultimo_fatura_em = NULL, "
            "ultimo_recalculo_em = NULL, ultimo_erro = NULL"
        ),
        {"inicio": INICIO},
    ).rowcount
    print(f"[0437] conciliacao_lojas: {n} loja(s) recomeçam em {INICIO:%d/%m/%Y}")


def downgrade() -> None:
    conn = op.get_bind()
    for tabela in APAGADAS:
        conn.execute(sa.text(f"DELETE FROM {_t(tabela)}"))  # noqa: S608
        conn.execute(sa.text(f"INSERT INTO {_t(tabela)} SELECT * FROM {_t(tabela + SUFIXO)}"))  # noqa: S608
    volta = ", ".join(f"{c} = c.{c}" for c in CAMPOS_DA_LOJA)
    conn.execute(
        sa.text(
            f"UPDATE {_t('conciliacao_lojas')} AS l SET {volta} "  # noqa: S608
            f"FROM {_t('conciliacao_lojas' + SUFIXO)} AS c WHERE c.id = l.id"
        )
    )
    for tabela in COPIADAS:
        conn.execute(sa.text(f"DROP TABLE {_t(tabela + SUFIXO)}"))
