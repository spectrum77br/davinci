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
plataforma — a reclamação vencida está marcada (e em "Vencidas"). Desde
14/10/2026 a reclamação, a avaliação e o carrinho saem do filtro "Falta
responder" (só o chat, abaixo); o SAC da Magalu, que é conversa com o
comprador, fica.

"Resolvida" = fechada ou marcada "não precisa de resposta" no DaVinci (as
duas voltam sozinhas quando o comprador escreve de novo). A bloqueada
continua contando (a plataforma não deixa responder por ali, mas a conversa
espera alguém).

O lado do comprador é `ultima_do_cliente_em`; o da loja,
`ultima_loja_qualquer_em` (`gravar.recalcular`, migration 0396). Uma régua só,
em Python (`falta_responder`, a linha da lista) e em SQL
(`falta_responder_sql`, os números do /resumo e o filtro) — o teste confere as
duas.

ONDE vale: o "Falta responder" da CAIXA — o número da aba "Falta
responder" (`aguardando` do /resumo, por plataforma e por loja), o filtro
`aguardando` da lista e a marca de cada linha (negrito e bolinha,
`falta_responder` da linha). NÃO muda a FILA de verdade
(`aguardando_resposta`/`prazo_resposta_em`: a vez do comprador que a IA, o
automático, a Caixa Humano, o prazo/alerta, "Vencendo"/"Vencidas", o selo de
prazo da linha e as métricas usam), nem a aba E-mail (a régua dela é a fila),
nem o Direct do Instagram (adaptador só leitura, régua própria).

SÓ O CHAT (14/10/2026, o dono: "o nosso ATV mostra 9, o deles 5 … tá pegando
uns antigos de avaliação"). O Duoke só tem o CHAT — avaliação, reclamação/
devolução/mediação, comentário das redes e carrinho do site não estão nele
(`CANAIS_FORA_DO_CHAT`). Desde então:

  • o filtro e o número da aba "Falta responder" contam só a conversa de
    chat (`falta_responder_no_chat`) — essas outras têm os filtros próprios
    (Avaliação, Reclamação, Devolução, Carrinho, Mídia). A marca da linha
    (`falta_responder`) continua em todas: em "Todas", a avaliação esperando
    resposta segue em negrito;
  • os NÚMEROS da Caixa — o da aba Caixa, o menu de plataformas, a barra de
    lojas, "Todas · Shopee" — são o número do DUOKE (`nao_lidas_duoke`): a
    SOMA DAS MENSAGENS NÃO LIDAS das conversas de chat esperando resposta.
    Medido no Duoke (14/10/2026 13:47, aba Shopee): o número ao lado da loja
    é a soma das não lidas (ATV 5 = uma conversa com 4 + outra com 1), e no
    mesmo minuto o banco deu Σ `nao_lidas` das conversas de chat com
    `aguardando_resposta` (a FILA: o menu automático do robô do Duoke NÃO
    fecha a vez do comprador), não fechadas e com não lida — Aguiar 1=1,
    ATV 5=5, Mini 1=1, Marquezini 14=14 (Jlas, Victor e Inova perto, pelo
    intervalo do sync; a Kia diverge porque ninguém abre a Kia no Duoke e o
    não lido dele é dele). Onde a plataforma NÃO dá o não lido de cada
    conversa (`NAO_LIDA_E_O_NUMERO` não tem o canal: e-mail da Amazon, Zap,
    pergunta no anúncio, SAC da Magalu — nesses o `nao_lidas` é 1/0 de
    "está aberta", ou nada), conta 1 por conversa esperando resposta; o
    Direct do Instagram (adaptador só leitura) também, pela régua dele, só
    dentro da janela de 24 h da Meta (`instagram.no_numero_do_duoke`: o que
    passou dela fica no "Falta responder" e em "Vencidas", não no número —
    eram Directs de 23/09 e 02/10 no número da aba);
  • o número SÓ CONTA A CONVERSA QUE ESTÁ NO "FALTA RESPONDER" (a linha
    marcada, com a bolinha): a pessoa sempre acha o que está contado. No
    ML pós-venda o não lido NÃO zera quando a loja responde (acima) e a
    fila não fecha com a automática — medido 14/10/2026 17:21: 28 dos 29
    pontos do ML eram conversas com a automática da loja 1 min depois do
    comprador, de 8 a 25 dias atrás, sem nenhuma linha marcada (Marquezini
    14, Inova 5, Counhago 4), 13 deles de conversas bloqueadas. Na Shopee e
    na TikTok não muda nada: lá o não lido já marca a linha (régua (b)).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, not_, or_

from app.models import AtendimentoConversa
from app.services.atendimento import separa_email
from app.services.atendimento.constantes import (
    CANAL_AVALIACAO,
    CANAL_CARRINHO,
    CANAL_CHAT,
    CANAL_COMENTARIO,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_FECHADA,
)

# Onde o NÃO LIDO da plataforma entra na régua (b): (plataforma, canal).
NAO_LIDA_CONTA_EM: tuple[tuple[str, str], ...] = (("shopee", CANAL_CHAT), ("tiktok", CANAL_CHAT))

# O que NÃO é chat (14/10/2026): não está no Duoke, não entra no "Falta
# responder" nem nos números da Caixa — cada um tem o filtro dele. A
# reclamação cobre também a mediação e a devolução da plataforma.
CANAIS_FORA_DO_CHAT: frozenset[str] = frozenset(
    {CANAL_AVALIACAO, CANAL_RECLAMACAO, CANAL_COMENTARIO, CANAL_CARRINHO}
)

# Onde o número da conversa é o NÃO LIDO que a plataforma dá, mensagem a
# mensagem (o do Duoke): (plataforma, canal). No resto do chat, 1 por
# conversa esperando resposta (`nao_lidas_duoke`).
NAO_LIDA_E_O_NUMERO: tuple[tuple[str, str], ...] = (
    ("shopee", CANAL_CHAT),
    ("tiktok", CANAL_CHAT),
    ("ml", CANAL_POS_VENDA),
    ("magalu", CANAL_CHAT),
    ("temu", CANAL_CHAT),
    ("aliexpress", CANAL_CHAT),
)


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
    """SQL de `falta_responder` (nunca NULL). O filtro e o número da aba são só
    do chat: `falta_responder_no_chat_sql`."""
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


# ── Só o chat e o número do Duoke (14/10/2026) ────────────────────────────


def do_chat(conversa: Any) -> bool:
    """A conversa é de CHAT (está no Duoke)? PURA — fora a avaliação, a
    reclamação/devolução/mediação, o comentário das redes, o carrinho do site
    e o e-mail da ponte (que mora na aba E-mail)."""
    canal = getattr(conversa, "canal", None)
    return canal not in CANAIS_FORA_DO_CHAT and not separa_email.e_conversa_de_email(conversa)


def falta_responder_no_chat(conversa: Any) -> bool:
    """O filtro "Falta responder" da Caixa: a régua do Duoke, só no chat. PURA."""
    return do_chat(conversa) and falta_responder(conversa)


def nao_lidas_duoke(conversa: Any) -> int:
    """Quanto a conversa soma no número do Duoke (a barra de lojas, o menu de
    plataformas, a aba Caixa). PURA — a gêmea de `nao_lidas_duoke_sql`.

    Conversa de chat, não fechada, esperando resposta na FILA
    (`aguardando_resposta`: a automática não fecha a vez) E no "Falta
    responder" (`falta_responder`: a linha marcada — o não lido do ML, que
    não zera quando a loja responde, não acende número sem linha marcada):
    as não lidas da plataforma onde ela dá o não lido
    (`NAO_LIDA_E_O_NUMERO`, zero se a equipe já leu); 1 nos outros canais do
    chat (e-mail da Amazon, Zap, pergunta, SAC). O resto, zero.
    """
    if not do_chat(conversa) or getattr(conversa, "situacao", None) == CONVERSA_FECHADA:
        return 0
    if not getattr(conversa, "aguardando_resposta", False) or not falta_responder(conversa):
        return 0
    onde = (getattr(conversa, "plataforma", None), getattr(conversa, "canal", None))
    if onde in NAO_LIDA_E_O_NUMERO:
        return max(int(getattr(conversa, "nao_lidas", 0) or 0), 0)
    return 1


def do_chat_sql(c: Any = AtendimentoConversa):
    """SQL de `do_chat` (nunca NULL: `canal` e `dados` são NOT NULL)."""
    return and_(c.canal.not_in(sorted(CANAIS_FORA_DO_CHAT)), separa_email.na_caixa_sql(c))


def falta_responder_no_chat_sql(c: Any = AtendimentoConversa):
    """SQL de `falta_responder_no_chat` — o filtro e o número da aba "Falta responder"."""
    return and_(do_chat_sql(c), falta_responder_sql(c))


def nao_lidas_duoke_sql(c: Any = AtendimentoConversa):
    """SQL de `nao_lidas_duoke` (um inteiro por linha, nunca NULL) — some com `sum`."""
    esperando = and_(
        do_chat_sql(c),
        c.situacao != CONVERSA_FECHADA,
        c.aguardando_resposta.is_(True),
        falta_responder_sql(c),
    )
    da_plataforma = or_(*(and_(c.plataforma == p, c.canal == k) for p, k in NAO_LIDA_E_O_NUMERO))
    return case(
        (not_(esperando), 0),
        (da_plataforma, func.greatest(c.nao_lidas, 0)),
        else_=1,
    )
