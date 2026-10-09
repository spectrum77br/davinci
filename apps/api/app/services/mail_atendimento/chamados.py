"""Os CHAMADOS dos sites (RF6): o formulário vira chamado com o protocolo do site.

O site (Uranyx, Charlots, 7Buyers, Locagil) manda o formulário DE sac@ PARA
sac@ (ou atacado@/duvidas@), com o cliente no corpo e o PROTOCOLO no assunto
("[US-26-0014] Troca"). A ponte (`ponte.py`) já faz o principal:

  • lê o protocolo (nunca gera outro) e o TIPO pelo prefixo — S = SAC,
    A = Atacado, DS = Dúvidas e sugestões; o tipo do protocolo vale mais que
    a caixa que recebeu (com alerta);
  • a conversa do chamado é uma por protocolo (`mail-protocolo:<nº>`, sem
    integração: plataforma `site`), com a marca, o tipo e o protocolo em
    `dados.mail`; o cliente (o e-mail do corpo) é o `comprador_id`;
  • a resposta sai do e-mail do TIPO da marca (`marca_emails`: sac@uranyx…)
    quando a caixa tem ele (`responder.de_alias`), para o cliente do corpo
    (calculado no servidor), com o [protocolo] no assunto.

Aqui fica o resto: SUGERIR AGRUPAR quando a mesma cliente tem outro chamado
ABERTO na mesma marca (`outros_chamados`) e o AGRUPAR por clique de quem mexe
(`agrupar`: as mensagens vão para o chamado escolhido; nada se apaga).
Nunca junta sozinho: dois protocolos podem ser dois assuntos.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem, User
from app.models.mail_atendimento import MailMessageMeta, MailOutboxMeta
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    AUTOR_SISTEMA,
    CANAL_EMAIL,
    CONVERSA_FECHADA,
    FONTE_TUTA,
    MSG_ENVIANDO,
)
from app.services.mail_atendimento import ponte
from app.services.mail_atendimento.constantes import PLATAFORMA_SITE, ROTULO_TIPO_CAIXA

MAX_OUTROS = 10


class AgruparError(ValueError):
    def __init__(self, codigo: str, status: int = 409):
        self.codigo = codigo
        self.status = status
        super().__init__(codigo)


def e_chamado(conversa: AtendimentoConversa) -> bool:
    """A conversa é um chamado de site da ponte (plataforma `site`, e-mail)."""
    return conversa.plataforma == PLATAFORMA_SITE and ponte.e_conversa_da_ponte(conversa)


def resumo_do_chamado(conversa: AtendimentoConversa) -> dict:
    mail = ponte.dados_mail(conversa)
    tipo = mail.get("caixa")
    return {
        "conversa_id": str(conversa.id),
        "protocolo": mail.get("protocolo"),
        "marca": mail.get("marca"),
        "marca_nome": mail.get("marca_nome"),
        "tipo": tipo,
        "tipo_rotulo": ROTULO_TIPO_CAIXA.get(tipo or ""),
        "situacao": conversa.situacao,
        "ultima_mensagem_em": gravar._utc(conversa.ultima_mensagem_em),
    }


async def outros_chamados(session: AsyncSession, conversa: AtendimentoConversa) -> list[dict]:
    """Outros chamados ABERTOS da MESMA cliente na MESMA marca (sugestão de agrupar)."""
    if not e_chamado(conversa) or not conversa.comprador_id:
        return []
    marca = ponte.dados_mail(conversa).get("marca")
    if not marca:
        return []
    linhas = (
        await session.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.plataforma == PLATAFORMA_SITE,
                AtendimentoConversa.canal == CANAL_EMAIL,
                AtendimentoConversa.integration_id.is_(None),
                AtendimentoConversa.comprador_id == conversa.comprador_id,
                AtendimentoConversa.id != conversa.id,
                AtendimentoConversa.situacao != CONVERSA_FECHADA,
                AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
                AtendimentoConversa.dados[ponte.CHAVE]["marca"].astext == marca,
            )
            .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
            .limit(MAX_OUTROS)
        )
    ).scalars()
    return [resumo_do_chamado(c) for c in linhas]


async def agrupar(
    session: AsyncSession,
    origem: AtendimentoConversa,
    destino: AtendimentoConversa,
    user: User,
) -> AtendimentoConversa:
    """Junta o chamado `origem` no `destino` (mesma cliente, mesma marca), por clique.

    As mensagens e os e-mails (`mail_message_meta`, a ligação das respostas)
    passam para o destino; o de origem fica FECHADO e "não precisa de
    resposta", com a marca de para onde foi. Uma nota de sistema registra
    quem agrupou. As duas conversas já vêm travadas por quem chama. Não commita.
    """
    if origem.id == destino.id:
        raise AgruparError("mesmo_chamado")
    if not (e_chamado(origem) and e_chamado(destino)):
        raise AgruparError("nao_e_chamado")
    if (
        ponte.dados_mail(origem).get("marca") != ponte.dados_mail(destino).get("marca")
        or not origem.comprador_id
        or origem.comprador_id != destino.comprador_id
    ):
        raise AgruparError("outra_cliente_ou_marca")
    em_voo = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id.in_((origem.id, destino.id)),
            AtendimentoMensagem.status == MSG_ENVIANDO,
        )
        .limit(1)
    )
    if em_voo is not None:
        # Uma resposta na fila: o "uma em voo por conversa" não deixa juntar agora.
        raise AgruparError("envio_em_andamento")
    await session.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailMessageMeta)
        .where(MailMessageMeta.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailOutboxMeta)
        .where(MailOutboxMeta.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    agora = datetime.now(UTC)
    protocolos = [
        p
        for p in (
            ponte.dados_mail(destino).get("protocolo"),
            ponte.dados_mail(origem).get("protocolo"),
        )
        if p
    ]
    dados = dict(destino.dados or {})
    mail = dict(dados.get(ponte.CHAVE) or {})
    mail["protocolos"] = list(dict.fromkeys([*(mail.get("protocolos") or []), *protocolos]))
    dados[ponte.CHAVE] = mail
    destino.dados = dados
    origem.situacao = CONVERSA_FECHADA
    origem.sem_resposta_necessaria = True
    origem.dados = {
        **(origem.dados or {}),
        ponte.CHAVE: {**ponte.dados_mail(origem), "agrupado_em": str(destino.id)},
    }
    await session.flush()
    await gravar.gravar_mensagem(
        session,
        destino,
        externo_id=f"mail-agrupado:{origem.id}",
        autor=AUTOR_SISTEMA,
        texto=(
            f"Chamado {ponte.dados_mail(origem).get('protocolo') or 'sem protocolo'} agrupado "
            f"neste por {user.name or user.email}."
        ),
        enviada_em=agora,
        payload={ponte.CHAVE: {"agrupado_de": str(origem.id)}},
    )
    await gravar.recalcular_conversa(session, destino)
    await gravar.recalcular_conversa(session, origem)
    return destino


async def conversa_por_id(session: AsyncSession, conversa_id: UUID) -> AtendimentoConversa | None:
    return await session.get(AtendimentoConversa, conversa_id)
