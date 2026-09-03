"""Situações de pedido do Bling usadas pelas regras do DaVinci — fonte única.

Regra (Eduardo, 03/09/2026): o estado "etiqueta emitida/enviada, esperando a
agência confirmar" DEIXA de ser a situação customizada 83965 ("Enviado
Etiqueta") e passa a ser a situação NATIVA do Bling **21 ("Em digitação")**.
Tudo que o sistema fazia com 83965 (mover o pedido quando a etiqueta sobe,
badge vermelho provisório no Controle de Estoque, triagem de margem, sweeps
83965→15 quando o marketplace confirma o envio, etc.) passa a valer pra 21.

O 83965 continua RECONHECIDO como legado: pedidos que já estavam nele antes
da troca precisam continuar contando como "etiqueta enviada". Por isso as
checagens usam `SITUACOES_ENVIADO_ETIQUETA` (as duas), e só quem MOVE o
pedido usa `SITUACAO_ENVIADO_ETIQUETA` (a nova, 21).

Os módulos antigos declaravam essas constantes localmente (estoque.py,
margens.py, nf.py, bling_orders.py…) ora como str, ora como int — as duas
formas estão aqui pra não espalhar `str()`/`int()` pelas regras.
"""

from __future__ import annotations

# --- nativas do Bling ------------------------------------------------------
SITUACAO_EM_ABERTO = 6
SITUACAO_ATENDIDO = 9
SITUACAO_CANCELADO = 12
SITUACAO_EM_ANDAMENTO = 15
SITUACAO_EM_DIGITACAO = 21

# --- "etiqueta enviada" ------------------------------------------------------
# Nova canônica (nativa "Em digitação"): é pra ONDE o pedido vai quando a
# etiqueta sobe pro DaVinci.
SITUACAO_ENVIADO_ETIQUETA = SITUACAO_EM_DIGITACAO
# Legado (customizada "Enviado Etiqueta"): pedidos antigos ficam nela.
SITUACAO_ENVIADO_ETIQUETA_LEGADO = 83965
# Tudo que significa "etiqueta enviada" — usar nas CHECAGENS.
SITUACOES_ENVIADO_ETIQUETA: tuple[int, ...] = (
    SITUACAO_ENVIADO_ETIQUETA,
    SITUACAO_ENVIADO_ETIQUETA_LEGADO,
)

# Versões em texto (bling_orders.situacao é TEXT no banco).
SITUACAO_ENVIADO_ETIQUETA_STR = str(SITUACAO_ENVIADO_ETIQUETA)
SITUACAO_ENVIADO_ETIQUETA_LEGADO_STR = str(SITUACAO_ENVIADO_ETIQUETA_LEGADO)
SITUACOES_ENVIADO_ETIQUETA_STR: tuple[str, ...] = tuple(
    str(s) for s in SITUACOES_ENVIADO_ETIQUETA
)
# Pronto pra interpolar em SQL: ('21', '83965')
SITUACOES_ENVIADO_ETIQUETA_SQL = (
    "(" + ", ".join(f"'{s}'" for s in SITUACOES_ENVIADO_ETIQUETA_STR) + ")"
)

# Nome exibido pro estado (o que o operador vê no Bling hoje).
NOME_ENVIADO_ETIQUETA = "Em digitação"
NOME_ENVIADO_ETIQUETA_LEGADO = "Enviado Etiqueta"


def eh_enviado_etiqueta(situacao: int | str | None) -> bool:
    """True se `situacao` (int ou str) é um dos estados "etiqueta enviada"."""
    if situacao is None:
        return False
    return str(situacao).strip() in SITUACOES_ENVIADO_ETIQUETA_STR
