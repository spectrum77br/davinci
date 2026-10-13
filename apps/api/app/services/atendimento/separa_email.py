"""O E-MAIL fica na aba E-mail; o CHAT fica na Caixa (13/10/2026).

O dono, com o print da Caixa da Amazon cheia de "Comprador | Kia — não precisa
de resposta" (e-mails do Tuta que a ponte trouxe): "vamos deixar o e-mail
aparecer só na aba de e-mail; a do chat de atendimento aparece só no chat
mesmo. Percebi que na Amazon, na caixa de entrada, está vindo os e-mails —
pois lá [na aba E-mail] já estamos linkando com a loja".

UMA régua só, reaproveitada por todo mundo (lista, /resumo, Caixa Humano,
métricas, abas, detalhe, recálculo da fila, ponte):

  CONVERSA DE E-MAIL = `canal = 'email'` + `dados.fonte = 'tuta'` — a conversa
      que a PONTE da Central de e-mail cria (`mail_atendimento/ponte.py`, com
      `dados.mail`; em produção, 13/10/2026: 1.588, todas com `dados.mail`).
      Ela some da Caixa (e de todas as contagens dela) e mora na aba E-mail
      (E-mail › Filas e a visão da conversa de e-mail, aberta pelo id).
      A AMAZON PELO GMAIL (`amazon_email.py`: `canal = 'email'` SEM a marca)
      é o CHAT da Amazon — o comprador escrevendo pela Amazon — e continua na
      Caixa, como sempre.
  MENSAGEM DE E-MAIL = `payload.mail` — o que a ponte grava. Numa conversa de
      e-mail é o conteúdo dela; numa conversa de CHAT (o "aviso na conversa
      da API" que a ponte gravava até 13/10/2026: 67 em produção, em 65
      conversas da Shopee, do TikTok e do ML) ela fica ESCONDIDA pela
      consulta — no detalhe, nas abas, na prévia da lista e no recálculo da
      fila —, sem apagar nada: tirar a régua devolve tudo como era. A ponte
      nova não grava mais e-mail em conversa de chat. Os CARIMBOS guardados
      (a prévia e a ordem da lista: `ultima_mensagem_*`) das conversas de chat
      com e-mail a migration 0391 refaz pela mesma régua (o downgrade volta).

Quem procura "a conversa do pedido" põe a de e-mail por último
(`etiqueta_fatos.conversas_do_pedido`) e não a conta como chat com o
comprador (`reclamacoes_devolucoes.e_com_o_comprador`); o alerta de prazo do
Telegram, o cartão Cliente do chat e a thread da Amazon pelo Gmail ficam só
com a Caixa (`na_caixa_sql`).

As duas expressões SQL nunca dão NULL (`dados` e `canal` são NOT NULL; a marca
ausente vira ''), então o NOT delas nunca perde linha.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, false, func, not_, or_

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento.constantes import CANAL_EMAIL, FONTE_TUTA

# A chave da ponte em `conversa.dados` e em `mensagem.payload` (`ponte.CHAVE`).
CHAVE_MAIL = "mail"
CHAVE_FONTE = "fonte"


def _texto(valor: Any) -> str:
    return "" if valor is None or isinstance(valor, bool) else str(valor).strip()


# ── A conversa ────────────────────────────────────────────────────────────


def e_email(canal: str | None, dados: Any) -> bool:
    """`canal = 'email'` com a marca do Tuta (`dados.fonte = 'tuta'`)? PURA."""
    if canal != CANAL_EMAIL or not isinstance(dados, dict):
        return False
    return _texto(dados.get(CHAVE_FONTE)) == FONTE_TUTA


def e_conversa_de_email(conversa: Any) -> bool:
    """A conversa é de E-MAIL (a da ponte: aba E-mail, nunca a Caixa)? PURA.

    A gêmea de `conversa_de_email_sql`. A Amazon pelo Gmail não é.
    """
    return e_email(getattr(conversa, "canal", None), getattr(conversa, "dados", None))


def conversa_de_email_sql(c: Any = AtendimentoConversa):
    """SQL de `e_conversa_de_email` (nunca NULL). `c` = a tabela (ou um alias)."""
    return and_(
        c.canal == CANAL_EMAIL,
        func.coalesce(func.btrim(c.dados[CHAVE_FONTE].astext), "") == FONTE_TUTA,
    )


def na_caixa_sql(c: Any = AtendimentoConversa):
    """A conversa é da CAIXA (do chat)? — o NOT de `conversa_de_email_sql` (nunca NULL)."""
    return not_(conversa_de_email_sql(c))


# ── A mensagem ────────────────────────────────────────────────────────────


def e_mensagem_de_email(mensagem: Any) -> bool:
    """A mensagem é um e-mail da ponte (`payload.mail`)? PURA — gêmea de `mensagem_de_email_sql`."""
    payload = getattr(mensagem, "payload", None)
    return isinstance(payload, dict) and CHAVE_MAIL in payload


def mensagem_de_email_sql(m: Any = AtendimentoMensagem):
    """SQL de `e_mensagem_de_email` (`payload ? 'mail'`, nunca NULL)."""
    return func.coalesce(m.payload.has_key(CHAVE_MAIL), false())


def visivel_na_conversa(conversa: Any, mensagem: Any) -> bool:
    """A mensagem aparece nesta conversa? PURA.

    Na conversa de e-mail, tudo; na de chat, tudo menos o e-mail (o aviso da
    ponte de antes de 13/10/2026 fica escondido)."""
    return e_conversa_de_email(conversa) or not e_mensagem_de_email(mensagem)


def mensagem_visivel_sql(conversa: Any, m: Any = AtendimentoMensagem):
    """WHERE das mensagens que aparecem na conversa (já carregada); None = todas.

    É `visivel_na_conversa` com a conversa conhecida: a de e-mail mostra tudo;
    a de chat, tudo menos o e-mail."""
    if e_conversa_de_email(conversa):
        return None
    return not_(mensagem_de_email_sql(m))


def mensagem_visivel_correlata_sql(c: Any = AtendimentoConversa, m: Any = AtendimentoMensagem):
    """`visivel_na_conversa` para a subconsulta CORRELATA da lista (a prévia):
    a conversa é de e-mail, ou a mensagem não é e-mail. Nunca NULL."""
    return or_(conversa_de_email_sql(c), not_(mensagem_de_email_sql(m)))
