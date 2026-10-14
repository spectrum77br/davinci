"""O "Falta responder" da Caixa IGUAL AO DUOKE (14/10/2026).

O dono: "tem que arrumar pro atendimento ter literalmente o comportamento do
Duoke, pra estar marcado literalmente as mesmas". A Caixa mostrava Shopee 99+
(ATV 65, vortan 60…) enquanto o filtro "Pendente" do Duoke mostrava 15: o
DaVinci não contava a mensagem automática como resposta (o menu do robô, as
campanhas), e o Duoke conta.

A régua do Duoke (comparando as 15 do "Pendente" dele com o banco, 14/10
12:05) vale para as conversas de CHAT — a mensagem que vai e volta (o chat da
Shopee e da TikTok, o pós-venda e a pergunta do ML, a Amazon, os comentários
das redes). A conversa está pendente quando NÃO foi resolvida E

  (a) a última mensagem que não é do sistema é do COMPRADOR — qualquer
      mensagem da loja depois dela é resposta, até a automática (o menu "Olá,
      por favor selecione sua dúvida…", "a sua mensagem foi recebida…", a
      figurinha, o cupom, o cartão de pedido) e a da plataforma em nome da
      loja; a de autor `sistema`/`mediador` (avisos, "já segue nossa loja",
      o chatbot da plataforma) não conta para nenhum lado; a que falhou não
      saiu e a nota interna não é mensagem — OU
  (b) está NÃO LIDA na plataforma (`nao_lidas > 0`, que o sync mantém fresco
      lendo o não lido de cada canal — `gravar.aplicar_nao_lidas`) — só onde
      esse não lido é o do Duoke (`NAO_LIDA_CONTA_EM`: o chat da Shopee e da
      TikTok, onde ele zera quando alguém abre a conversa) e só na conversa
      que tem fala do COMPRADOR. Medido em produção (14/10/2026):
        • ML pós-venda: o não lido do ML NÃO zera quando a loja responde
          (100 das 190 com a loja por último continuavam "não lidas") — o ML
          fica só com a (a);
        • a conversa SÓ de aviso do sistema (o "já segue nossa loja", o
          cartão de status do pedido da TikTok) fica não lida na plataforma e
          o Duoke não mostra: 14 da Shopee e 27 da TikTok entravam só por
          isso, nenhuma entre as 15 — nem a conversa sem mensagem nenhuma (20
          das 25 "não lidas" da Shopee Inova);
        • já o aviso DEPOIS da fala do comprador não tira: duas das 15 (Kia,
          comprador › menu do robô › aviso) estavam no Pendente do Duoke.

PELA FILA: quando a PLATAFORMA diz de quem é a vez (`dados` com
`CHAVE_VEZ_DA_LOJA`: a reclamação, a devolução, a avaliação, o SAC da
Magalu, o carrinho do site) ou a conversa é o e-mail da ponte, o "Falta
responder" é a própria fila (`aguardando_resposta`, `gravar._derivar`): nesses
canais o Duoke não entra, a mensagem do mediador é a cobrança e o prazo é o da
plataforma — a reclamação vencida tem que estar no "Falta responder".

"Resolvida" = fechada ou marcada "não precisa de resposta" no DaVinci (as
duas voltam sozinhas quando o comprador escreve de novo). A bloqueada
continua contando (a plataforma não deixa responder por ali, mas a conversa
espera alguém).

O lado do comprador é `ultima_do_cliente_em`; o da loja,
`ultima_loja_qualquer_em` (`gravar.recalcular`, migration 0396). Uma régua só,
em Python (`falta_responder`, a linha da lista) e em SQL
(`falta_responder_sql`, os números do /resumo e o filtro) — o teste confere as
duas.

ONDE vale: o "Falta responder" da CAIXA — os números do /resumo
(`aguardando` por plataforma e por loja: o total do topo, o menu de
plataformas, a barra de lojas, o número da aba), o filtro `aguardando` da
lista e a marca de cada linha (negrito e bolinha, `falta_responder` da
linha). NÃO muda a FILA de verdade (`aguardando_resposta`/`prazo_resposta_em`:
a vez do comprador que a IA, o automático, a Caixa Humano, o prazo/alerta,
"Vencendo"/"Vencidas", o selo de prazo da linha e as métricas usam), nem a
aba E-mail (a régua dela é a fila), nem o Direct do Instagram (adaptador só
leitura, régua própria).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, not_, or_

from app.models import AtendimentoConversa
from app.services.atendimento import separa_email
from app.services.atendimento.constantes import (
    CANAL_CHAT,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_FECHADA,
)

# Onde o NÃO LIDO da plataforma entra na régua (b): (plataforma, canal).
NAO_LIDA_CONTA_EM: tuple[tuple[str, str], ...] = (("shopee", CANAL_CHAT), ("tiktok", CANAL_CHAT))


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def pela_fila(conversa: Any) -> bool:
    """A plataforma diz de quem é a vez, ou é o e-mail da ponte: vale a fila. PURA."""
    dados = getattr(conversa, "dados", None)
    return (
        isinstance(dados, dict) and CHAVE_VEZ_DA_LOJA in dados
    ) or separa_email.e_conversa_de_email(conversa)


def comprador_por_ultimo(conversa: Any) -> bool:
    """(a): a última mensagem que não é do sistema é do comprador? PURA."""
    do_cliente = _utc(getattr(conversa, "ultima_do_cliente_em", None))
    da_loja = _utc(getattr(conversa, "ultima_loja_qualquer_em", None))
    return do_cliente is not None and (da_loja is None or do_cliente > da_loja)


def nao_lida_conta(conversa: Any) -> bool:
    """(b): não lida na plataforma, onde e quando o Duoke conta? PURA."""
    onde = (getattr(conversa, "plataforma", None), getattr(conversa, "canal", None))
    return (
        onde in NAO_LIDA_CONTA_EM
        and (getattr(conversa, "nao_lidas", 0) or 0) > 0
        and getattr(conversa, "ultima_do_cliente_em", None) is not None
    )


def falta_responder(conversa: Any) -> bool:
    """A conversa conta no "Falta responder" da Caixa (igual ao Duoke)? PURA.

    A gêmea de `falta_responder_sql` (o teste confere as duas).
    """
    if pela_fila(conversa):
        return bool(getattr(conversa, "aguardando_resposta", False))
    if getattr(conversa, "situacao", None) == CONVERSA_FECHADA:
        return False
    if getattr(conversa, "sem_resposta_necessaria", False):
        return False
    return nao_lida_conta(conversa) or comprador_por_ultimo(conversa)


def pela_fila_sql(c: Any = AtendimentoConversa):
    """SQL de `pela_fila` (nunca NULL: `dados` é NOT NULL)."""
    return or_(c.dados.has_key(CHAVE_VEZ_DA_LOJA), separa_email.conversa_de_email_sql(c))


def comprador_por_ultimo_sql(c: Any = AtendimentoConversa):
    """SQL de `comprador_por_ultimo` (nunca NULL). `c` = a tabela (ou um alias)."""
    return and_(
        c.ultima_do_cliente_em.is_not(None),
        or_(
            c.ultima_loja_qualquer_em.is_(None),
            c.ultima_do_cliente_em > c.ultima_loja_qualquer_em,
        ),
    )


def nao_lida_conta_sql(c: Any = AtendimentoConversa):
    """SQL de `nao_lida_conta` (nunca NULL)."""
    return and_(
        or_(*(and_(c.plataforma == p, c.canal == k) for p, k in NAO_LIDA_CONTA_EM)),
        c.nao_lidas > 0,
        c.ultima_do_cliente_em.is_not(None),
    )


def falta_responder_sql(c: Any = AtendimentoConversa):
    """SQL de `falta_responder` (nunca NULL) — os números e o filtro da Caixa."""
    fila = pela_fila_sql(c)
    return or_(
        and_(fila, c.aguardando_resposta.is_(True)),
        and_(
            not_(fila),
            c.situacao != CONVERSA_FECHADA,
            c.sem_resposta_necessaria.is_(False),
            or_(nao_lida_conta_sql(c), comprador_por_ultimo_sql(c)),
        ),
    )
