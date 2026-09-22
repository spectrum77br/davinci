"""Ouvidoria: índices das leituras de 15 min e fechamento das `conta:` legadas

Três coisas que a revisão dos 6 robôs novos (22/09/2026) cobrou e que só se
resolvem no banco.

## 1. `logistica.problema_correios_em` sem índice
O robô "Ocorrência grave nos Correios" (`services/vigia_correios._graves`) lê,
a cada 15 min, TODA linha da Logística com `problema_correios_em` preenchido —
de propósito sem janela de data: uma apreensão de 3 meses continua sendo uma
apreensão, e recortar por data faria a linha sair da rodada e a ocorrência
fechar como "sumiu" com o pacote ainda retido. Sem índice isso é um seq scan
da tabela inteira (≈90 dias de envios, linhas largas), 96×/dia.

O índice é PARCIAL (`WHERE problema_correios_em IS NOT NULL`): a esmagadora
maioria das linhas tem a coluna NULL, então ele fica minúsculo e a leitura vira
um index scan de dezenas de linhas.

## 2. `_ultima_fechada` sem a `chave` no índice
`ouvidoria.registrar` consulta a ÚLTIMA ocorrência fechada de uma (robô, chave)
sempre que não acha uma aberta — é ela que decide se a linha reabre
(`ignorada` nunca reabre; `tratada` só depois de 24 h). O único índice que
ajudava era `(robo_chave, fechada_em)`, e a `chave` sobrava como filtro de
linha sobre um conjunto que só cresce. Com 7 robôs, esse `registrar` é o
caminho mais quente da Ouvidoria.

## 3. As `conta:` legadas do vigia de importação
Até 22/09 era o vigia de importação que abria `conta:<integration_id>` ("Conta
sem acesso à API"). Quem abre agora é o Vigia de credenciais, que sabe separar
token vencido de instabilidade — e o vigia de importação parou de abrir, mas
NÃO fecha as que já estavam abertas: a conta caída entra em `excluir_contas`
do `fechar_nao_vistas` (não olhou ≠ sumiu), o que protege essas linhas
justamente enquanto a conta continuar falhando. Resultado sem esta migração:
duas linhas pelo mesmo fato no painel, `contas_sem_vigilancia` contando em
dobro e, com o robô `ligado`, dois Threemas.

Fechadas aqui como `sumiu` / `robô`, que é exatamente o que elas são: o robô
que as abria não vê mais esse assunto. O Vigia de credenciais reabre a dele na
rodada seguinte se a conta ainda estiver caída.

O downgrade derruba os dois índices e NÃO reabre as ocorrências: quais estavam
abertas se perde no caminho, e o robô certo já as tem.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0302_ouvidoria_indices_e_contas_legadas"
down_revision: str | None = "0301_personagem_voz_video"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
ROBO_VIGIA = "vigia_importacao"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_logistica_problema_correios_em
            ON {SCHEMA}.logistica (problema_correios_em)
         WHERE problema_correios_em IS NOT NULL
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_ouvidoria_ocorrencias_ultima_fechada
            ON {SCHEMA}.ouvidoria_ocorrencias (robo_chave, chave, fechada_em DESC)
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.ouvidoria_ocorrencias
           SET fechada_em = now(),
               fechamento = 'sumiu',
               fechada_por = 'robô',
               updated_at = now()
         WHERE robo_chave = '{ROBO_VIGIA}'
           AND chave LIKE 'conta:%'
           AND fechada_em IS NULL
        """  # noqa: S608
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_ouvidoria_ocorrencias_ultima_fechada")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_logistica_problema_correios_em")
