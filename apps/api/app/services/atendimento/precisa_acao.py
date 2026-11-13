"""O filtro "Precisa de ação" da Caixa (13/11/2026): o que a PESSOA tem de fazer na plataforma.

O Eduardo (13/11/2026): a Caixa Humano sai; tudo fica na Caixa normal, com o rascunho da IA
escrito, e a marca "precisa de ação" nos casos que exigem a pessoa agir (reembolso,
cancelamento, troca, mediação…), para não se perderem. É um recorte dos MESMOS motivos que a
triagem da Caixa Humano grava por conversa (`dados.humano.motivos`, services/atendimento/
humano.py) — só os de ação: o estado do pedido ou da conversa e a ação de pessoa do agente.
Os motivos de dúvida da IA ("a IA não respondeu", "a IA marcou", "assunto só de pessoa",
"pediu atendente") ficam de fora: na Caixa nova, todo turno já tem rascunho para conferir.
"""

from __future__ import annotations

from sqlalchemy import ARRAY, Text, and_, cast

from app.services.atendimento import constantes, humano

MOTIVOS_DE_ACAO: frozenset[str] = frozenset(
    {
        constantes.HUMANO_RECLAMACAO,
        constantes.HUMANO_AG_CANCELAMENTO,
        constantes.HUMANO_DEVOLUCAO,
        constantes.HUMANO_CHAMADO,
        constantes.HUMANO_AVALIACAO,
        constantes.HUMANO_ALERTA,
        constantes.HUMANO_XINGAMENTO,
        constantes.HUMANO_ACAO_PESSOA,
        constantes.HUMANO_SO_ANEXO,
        constantes.HUMANO_BLOQUEADA,
        constantes.HUMANO_OFERTA_BRINDE,
    }
)


def precisa_de_acao(motivos: object) -> bool:
    """A lista de motivos gravada tem algum de ação? PURA."""
    return isinstance(motivos, list) and any(m in MOTIVOS_DE_ACAO for m in motivos)


def precisa_de_acao_sql():
    """A conversa precisa de ação da pessoa no turno atual? Nunca NULL."""
    return and_(
        humano.escopo_sql(),
        humano.triada_sql(),
        humano._campo("motivos").op("?|")(cast(sorted(MOTIVOS_DE_ACAO), ARRAY(Text))),
    )
