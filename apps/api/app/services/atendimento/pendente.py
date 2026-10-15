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
`aguardando` da lista e a marca de cada linha (o negrito,
`falta_responder` da linha; a bolinha, desde 15/10/2026, é o número da
conversa — abaixo). NÃO muda a FILA de verdade
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
    lojas, "Todas · Shopee" — são o número do DUOKE (`nao_lidas_duoke`, o
    nome do campo ficou): a régua abaixo (15/10/2026);
  • a marca da linha (o negrito) e o filtro "Falta responder" seguem a
    régua (a)/(b) acima — o número NÃO depende deles, e a bolinha da linha é
    o número da conversa (abaixo).

O NÚMERO DA CAIXA = O "NÃO LIDA" DO DUOKE (decisão do dono, 15/10/2026: "os
números da Caixa têm que bater com o Duoke"; o Eduardo: "1 mensagem na kia
… a kia tá mostrando 9 naquela conversa no duoke"). Medido: o número ao lado
da loja no Duoke é o NÃO LIDO DO PRÓPRIO DUOKE — as mensagens do comprador
desde que alguém abriu a conversa dentro do Duoke ou respondeu —, não o da
plataforma: a Shopee zera o não lido dela quando a loja manda QUALQUER
mensagem (até o menu do robô do Duoke), por isso a régua de 14/10 (Σ
`nao_lidas` da plataforma) ficava muito abaixo do Duoke (a Kia com 1 e o
Duoke com 9 numa conversa só). A régua nova, por conversa:

  • de CHAT (`do_chat`: fora a avaliação, a reclamação/devolução/mediação, o
    comentário das redes, o carrinho do site e o e-mail da ponte), não
    fechada — sem depender da FILA (`entra_no_numero`);
  • soma as mensagens DO COMPRADOR (`do_comprador_no_numero`: também a
    `sistema` que ele mesmo mandou — a pergunta do FAQ, a resposta automática
    da conta dele) mandadas depois de `desde_do_numero` (o Duoke foi zerado
    inteiro em 14/10/2026 às 12:35; no máximo 7 dias) e depois
      – da última resposta de PESSOA da loja, achada nas mensagens
        (`resposta_de_pessoa` — não o `ultima_da_loja_em` da fila: a
        automática — o robô e as campanhas do Duoke, as automações do
        DaVinci, o cartão da Shopee — não conta e NÃO zera o número, nem no
        turno só de cartão), no relógio da PLATAFORMA
        (`coalesce(enviada_em, created_at)`, como a fila), e
      – do que a pessoa já VIU da conversa no DaVinci (`visto_em`, migration
        0399 — "visto no DaVinci", como abrir no Duoke: a tela grava pela
        pessoa que mexe na caixa, em qualquer modo de loja,
        `services/atendimento/lida.py`), no relógio do DAVINCI: a CHEGADA da
        mensagem (`created_at`). O sync traz a mensagem do comprador ~1 min
        depois de ele mandar (medido em produção em 15/10/2026, 24 h: mediana
        de 70 s na Shopee — p90 de 213 s —, 65 s na TikTok, 61 s no ML); a
        que chegou depois de a pessoa abrir ela nunca viu, mesmo com o
        `enviada_em` de antes, e no Duoke ela contaria. O `visto_em` é a
        chegada da mensagem mais nova que a tela mostrava (o `visto_ate` do
        detalhe, `lida.visto_ate`) — com a conversa aberta, a tela grava de
        novo a cada mensagem nova que a releitura traz;
  • o e-mail da ponte gravado dentro de um chat (antes de 13/10/2026) não
    aparece na conversa e não conta.

2ª medição (15/10/2026 08:55, conversa a conversa contra a lista do Duoke,
211 conversas da Shopee): 175 batiam com a régua da 1ª; a diferença era (1)
a mensagem `sistema` do próprio comprador (9 conversas — agora conta), (2) a
figurinha 0028 da campanha "Só passando para saber…" fechando a vez como se
fosse pessoa (`constantes.FIGURINHAS_CAMPANHA_DUOKE`) e (3) o Duoke zerado
inteiro às 12:35 de 14/10 (12 conversas de antes, todas 0 lá). Com as três,
ATV 61=61, Jlas 50=50, KIA 28=28, Minas 17=17, Victor, Aguiar e Vortan
iguais; o que sobra (±1–3) é conversa aberta no Duoke sem resposta.

1ª medição, em 15/10/2026 07:32 (antes de a equipe começar, sem `visto_em`), na
Shopee, régua × Duoke: Aguiar 11=11, Victor 14=14, KIA 23=23, Poofy 0=0,
ATV 58×55, Jlas 50×48, Barbosa 36×39, Marquezini 71×67, KFA 15×17, Mini
9×10, Inova 9×11, Minas 10×8, Vita 7×10 — as janelas de 12 h, 48 h e 7 dias
ficaram piores. A diferença que sobra é o "abrir no Duoke", que o DaVinci não
vê.

O número NÃO exige a linha marcada no "Falta responder": a conversa em que o
robô respondeu depois do comprador (a Shopee zerou o não lido) sai do "Falta
responder" — para o Duoke, respondeu — e continua no número, como no Duoke.

A LINHA MOSTRA O NÚMERO DELA (15/10/2026, o Eduardo: "1 mensagem na kia e nem
mostra dessa conversa"; medido às ~08:05: os 27 da Kia vinham de 10 conversas,
todas sem a marca — a dos 9 com a automática por último): cada linha de chat
leva o número dela pela MESMA régua (`numero_duoke` da lista e do detalhe,
`nao_lidas_duoke_sql` por linha) e, na Caixa, a bolinha da linha é ele, com ou
sem a marca — a soma das bolinhas das conversas de uma loja é o número da
loja, e abrir a conversa apaga a bolinha dela (o negrito e o filtro "Falta
responder" continuam a régua (a)/(b): a conversa aberta que ninguém
respondeu segue em negrito, sem bolinha, como no Duoke). Fora do chat (a
avaliação, a reclamação…) a linha não tem número (`None`) e a bolinha segue
a de antes; a Caixa Humano também.

O canal sem mensagem para contar fica com a regra que já tinha: o Direct do
Instagram (adaptador só leitura — as mensagens ficam em `dm_mensagens`, não
em `atendimento_mensagens`) soma 1 por conversa esperando DENTRO da janela de
24 h da Meta (`instagram.no_numero_do_duoke`; a linha dele, o mesmo 1 —
`instagram._resumo`). Toda conversa da Caixa com
fala do comprador tem a mensagem dele gravada (medido em produção em
15/10/2026: nenhuma, em 30 dias, com `ultima_do_cliente_em` sem mensagem do
cliente) — inclusive o e-mail da Amazon, a pergunta do ML e o SAC da Magalu,
que contam pela mesma régua (onde a vez é da plataforma, como no SAC, só a
mensagem do comprador soma: o aviso do mediador não).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, not_, or_, select
from sqlalchemy.orm import aliased

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import gravar, separa_email
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_AVALIACAO,
    CANAL_CARRINHO,
    CANAL_CHAT,
    CANAL_COMENTARIO,
    CANAL_RECLAMACAO,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_FECHADA,
    MSG_FALHOU,
    ORIGEM_NOTA,
    TIPO_NOTA,
    e_mensagem_automatica,
    e_nota,
)

# Onde o NÃO LIDO da plataforma entra na régua (b): (plataforma, canal).
NAO_LIDA_CONTA_EM: tuple[tuple[str, str], ...] = (("shopee", CANAL_CHAT), ("tiktok", CANAL_CHAT))

# O que NÃO é chat (14/10/2026): não está no Duoke, não entra no "Falta
# responder" nem nos números da Caixa — cada um tem o filtro dele. A
# reclamação cobre também a mediação e a devolução da plataforma.
CANAIS_FORA_DO_CHAT: frozenset[str] = frozenset(
    {CANAL_AVALIACAO, CANAL_RECLAMACAO, CANAL_COMENTARIO, CANAL_CARRINHO}
)

# Desde quando o número conta (15/10/2026, a 2ª medição, conversa a conversa
# às 08:55 contra a lista do próprio Duoke): o Duoke NÃO tem janela — o não
# lido acumula até alguém abrir a conversa, responder ou "Limpar não lidas".
# O Duoke INTEIRO foi zerado em 14/10/2026 entre 12:34:24 e 12:35:55 (todas
# as lojas e plataformas: nenhuma conversa com não lida antes disso — a
# gssicacris das 12:34:24 com 0 —, todas as de depois contam — as da
# g3orgeribeiro das 12:35:55 em diante); a "janela de 24 h" da 1ª medição era
# esse corte. O DaVinci conta
# dali (`ZERADO_NO_DUOKE_EM`, no relógio da plataforma) e, para o número não
# crescer sem fim nas conversas que ninguém abre, olha no máximo 7 dias para
# trás (`JANELA_DO_NUMERO`). Zerar de novo é o "Zerar não lidas" da Caixa
# (`lida.zerar_nao_lidas`, o mesmo do Duoke).
ZERADO_NO_DUOKE_EM = datetime(2026, 10, 14, 15, 35, tzinfo=UTC)
JANELA_DO_NUMERO = timedelta(days=7)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def desde_do_numero(agora: datetime | None = None) -> datetime:
    """A mensagem do comprador mandada antes disto não conta no número. PURA."""
    return max(ZERADO_NO_DUOKE_EM, _utc(agora or datetime.now(UTC)) - JANELA_DO_NUMERO)


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


# ── O número da Caixa = o "não lida" do Duoke (15/10/2026) ───────────────


def resposta_de_pessoa(mensagem: Any) -> bool:
    """A mensagem é RESPOSTA DE PESSOA da loja para o número? PURA — a gêmea do
    `pessoa` de `nao_lidas_duoke_sql`.

    Da loja, que não falhou, que não é nota interna e que não é automática
    (`constantes.e_mensagem_automatica`: o robô e as campanhas do Duoke — a
    figurinha 0007/0028, o cartão do pedido, o "já segue" —, as automações do
    DaVinci, o que a Shopee põe em nome da loja). A resposta da IA que saiu
    sozinha é resposta (15/10/2026: para o comprador, respondeu).

    NÃO é o `ultima_da_loja_em` da FILA (15/10/2026, a 3ª medição): no turno
    em que o comprador só mandou cartão, a fila deixa a campanha fechar a vez
    (`gravar.recalcular_conversa`), e no Duoke o cartão continua não lido — 9
    conversas zeradas aqui e com número lá (o cartão do produto, a figurinha da
    campanha 26 h depois, o cartão do pedido no mesmo minuto).
    """
    if getattr(mensagem, "autor", None) != AUTOR_LOJA:
        return False
    if getattr(mensagem, "status", None) == MSG_FALHOU:
        return False
    if e_nota(getattr(mensagem, "origem", None), getattr(mensagem, "tipo", None)):
        return False
    return not e_mensagem_automatica(
        getattr(mensagem, "texto", None), getattr(mensagem, "payload", None)
    )


def _quando(mensagem: Any) -> datetime | None:
    """O relógio da PLATAFORMA da mensagem (`enviada_em`, sem ele a chegada)."""
    return _utc(getattr(mensagem, "enviada_em", None)) or _utc(
        getattr(mensagem, "created_at", None)
    )


def marcos_do_numero(
    conversa: Any, mensagens: Iterable[Any] | None = None
) -> tuple[datetime | None, datetime | None]:
    """Os dois marcos do número da conversa: (pessoa, visto). PURA.

    `pessoa` = a última resposta de PESSOA da loja entre as `mensagens`
    (`resposta_de_pessoa`), no relógio da PLATAFORMA; sem a lista, o
    `ultima_da_loja_em` da conversa (a fila — só para quem não tem as
    mensagens à mão). `visto` = até onde alguém já viu a conversa no DaVinci
    (`visto_em`), comparado com a CHEGADA da mensagem (`created_at`). None =
    sem esse marco.
    """
    if mensagens is None:
        pessoa = _utc(getattr(conversa, "ultima_da_loja_em", None))
    else:
        quandos = [_quando(m) for m in mensagens if resposta_de_pessoa(m)]
        validos = [q for q in quandos if q is not None]
        pessoa = max(validos) if validos else None
    return pessoa, _utc(getattr(conversa, "visto_em", None))


def entra_no_numero(conversa: Any, agora: datetime | None = None) -> bool:
    """A conversa pode somar no número da Caixa? PURA — o pré-filtro.

    Chat, não fechada, sem o "não precisa de resposta", com mensagem depois
    de `desde_do_numero` e depois da última
    resposta de pessoa (se a resposta foi a última, não há o que contar). NÃO
    depende da FILA (`aguardando_resposta`): a mensagem do comprador que o
    DaVinci grava como `sistema` (a pergunta do FAQ, a resposta automática da
    conta dele) não abre a vez dele, e no Duoke conta (15/10/2026).
    """
    if not do_chat(conversa):
        return False
    if getattr(conversa, "situacao", None) == CONVERSA_FECHADA:
        return False
    # "Não precisa de resposta" = o "marcar como processado" do Duoke, que
    # zera o não lido (a fala nova do comprador desmarca, `gravar`).
    if getattr(conversa, "sem_resposta_necessaria", False):
        return False
    ultima = _utc(getattr(conversa, "ultima_mensagem_em", None))
    return ultima is not None and ultima > desde_do_numero(agora)


def do_comprador_no_numero(mensagem: Any, comprador: Any = None) -> bool:
    """A mensagem é do COMPRADOR para o número? PURA.

    O autor `cliente` e, desde 15/10/2026, a mensagem `sistema` que o próprio
    comprador mandou (`payload.from_id` = o `comprador_id` da conversa): na
    Shopee, a pergunta que ele escolheu no FAQ (`bundle_message`) e a
    resposta automática da conta dele (o comprador que também vende: "Olá.
    Bem-vindo! Como posso ajudá-lo?", "Prezado comprador, a sua mensagem foi
    recebida…"). O DaVinci grava as duas como `sistema` (não abrem a vez dele
    na fila), mas o Duoke conta como não lida — medido em 15/10/2026: 9
    conversas a menos no DaVinci, todas com uma delas. A do sistema da loja
    (as campanhas, o "já segue") tem o `from_id` da loja e não conta.
    """
    autor = getattr(mensagem, "autor", None)
    if autor == AUTOR_CLIENTE:
        return True
    if autor != AUTOR_SISTEMA or comprador in (None, ""):
        return False
    payload = getattr(mensagem, "payload", None)
    remetente = payload.get("from_id") if isinstance(payload, dict) else None
    return remetente is not None and str(remetente) == str(comprador)


def mensagem_no_numero(
    mensagem: Any,
    pessoa: datetime | None = None,
    visto: datetime | None = None,
    *,
    comprador: Any = None,
    desde: datetime | None = None,
) -> bool:
    """A mensagem soma no número? PURA — a gêmea do WHERE de `nao_lidas_duoke_sql`.

    Do COMPRADOR (`do_comprador_no_numero`), que aparece na conversa (o
    e-mail da ponte gravado dentro de um chat não), mandada depois da última
    resposta de pessoa (`pessoa`) e de `desde` (`desde_do_numero`), no
    relógio da plataforma (`enviada_em`, sem ele a chegada), e CHEGADA ao
    DaVinci depois do que a pessoa já viu (`visto`, no relógio do DaVinci:
    `created_at` — o sync traz a mensagem ~1 min depois; a que chegou depois
    de abrir, ela não viu). Os marcos: `marcos_do_numero`.
    """
    if not do_comprador_no_numero(mensagem, comprador):
        return False
    if separa_email.e_mensagem_de_email(mensagem):
        return False
    chegou = _utc(getattr(mensagem, "created_at", None))
    quando = _utc(getattr(mensagem, "enviada_em", None)) or chegou
    if pessoa is not None and (quando is None or quando <= pessoa):
        return False
    if desde is not None and (quando is None or quando <= desde):
        return False
    if visto is not None and (chegou is None or chegou <= visto):
        return False
    return True


def nao_lidas_duoke(
    conversa: Any, mensagens: Iterable[Any] = (), *, agora: datetime | None = None
) -> int:
    """Quanto a conversa soma no número da Caixa (a barra de lojas, o menu de
    plataformas, a aba Caixa) — o "não lida" do Duoke. PURA, a gêmea de
    `nao_lidas_duoke_sql`; `mensagens` = as da conversa (as outras não contam).

    Fora do número (`entra_no_numero`), zero; dentro, as mensagens do
    comprador depois da última resposta de pessoa da loja e do que alguém já
    viu no DaVinci (`mensagem_no_numero`, `marcos_do_numero`).
    """
    if not entra_no_numero(conversa, agora):
        return 0
    cid = getattr(conversa, "id", None)
    dela = [m for m in mensagens if getattr(m, "conversa_id", cid) == cid]
    pessoa, visto = marcos_do_numero(conversa, dela)
    comprador = getattr(conversa, "comprador_id", None)
    desde = desde_do_numero(agora)
    return sum(
        1 for m in dela if mensagem_no_numero(m, pessoa, visto, comprador=comprador, desde=desde)
    )


def do_chat_sql(c: Any = AtendimentoConversa):
    """SQL de `do_chat` (nunca NULL: `canal` e `dados` são NOT NULL)."""
    return and_(c.canal.not_in(sorted(CANAIS_FORA_DO_CHAT)), separa_email.na_caixa_sql(c))


def falta_responder_no_chat_sql(c: Any = AtendimentoConversa):
    """SQL de `falta_responder_no_chat` — o filtro e o número da aba "Falta responder"."""
    return and_(do_chat_sql(c), falta_responder_sql(c))


def nao_lidas_duoke_sql(c: Any = AtendimentoConversa, agora: datetime | None = None):
    """SQL de `nao_lidas_duoke` (um inteiro por linha, nunca NULL) — some com `sum`.

    A contagem é uma subconsulta CORRELATA pelo índice de
    `atendimento_mensagens.conversa_id`, dentro do CASE: o Postgres só a roda
    para a conversa que passa no pré-filtro (`entra_no_numero`: chat com
    mensagem depois de `desde_do_numero`), não para a Caixa
    inteira — e por linha na lista (`numero_duoke`, 50 por vez). Dois
    relógios: a resposta de pessoa e o `desde` contra o da PLATAFORMA
    (`coalesce(enviada_em, created_at)`, como a fila) e o "visto no DaVinci"
    contra a CHEGADA da mensagem (`created_at`, `mensagem_no_numero`). Sem
    marco (NULL), aquele lado não corta nada.
    """
    m = aliased(AtendimentoMensagem)
    momento = func.coalesce(m.enviada_em, m.created_at)
    desde = desde_do_numero(agora)
    # A última resposta de PESSOA, pelas mensagens (`resposta_de_pessoa`) — não
    # o `ultima_da_loja_em` da fila. Uma vez por conversa (correlata só a `c`).
    h = aliased(AtendimentoMensagem)
    pessoa = (
        select(func.max(func.coalesce(h.enviada_em, h.created_at)))
        .where(
            h.conversa_id == c.id,
            h.autor == AUTOR_LOJA,
            func.coalesce(h.status, "") != MSG_FALHOU,
            func.coalesce(h.origem, "") != ORIGEM_NOTA,
            func.coalesce(h.tipo, "") != TIPO_NOTA,
            not_(gravar.mensagem_automatica_sql(h.texto, h.payload)),
        )
        .correlate(c)
        .scalar_subquery()
    )
    do_comprador = or_(
        m.autor == AUTOR_CLIENTE,
        and_(m.autor == AUTOR_SISTEMA, m.payload["from_id"].astext == c.comprador_id),
    )
    mensagens = (
        select(func.count())
        .select_from(m)
        .where(
            m.conversa_id == c.id,
            do_comprador,
            not_(separa_email.mensagem_de_email_sql(m)),
            momento > desde,
            momento > func.coalesce(pessoa, desde),
            or_(c.visto_em.is_(None), m.created_at > c.visto_em),
        )
        .correlate(c)
        .scalar_subquery()
    )
    entra = and_(
        do_chat_sql(c),
        c.situacao != CONVERSA_FECHADA,
        c.sem_resposta_necessaria.is_(False),
        c.ultima_mensagem_em > desde,
    )
    return case((entra, mensagens), else_=0)
