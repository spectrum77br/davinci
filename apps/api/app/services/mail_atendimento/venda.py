"""Leitura de e-mails de uma venda identificada, sem usar identidade do comprador.

Uma conversa de caso pode citar vendas diferentes. O vínculo é de cada
e-mail; os demais e-mails desse caso não ficam liberados por esse vínculo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import TeamScope
from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage
from app.models.mail_atendimento import MailMessageMeta, MailOutboxMeta
from app.services.atendimento import gravar, separa_email
from app.services.atendimento.constantes import PLATAFORMAS_EXTERNAS
from app.services.atendimento.etiqueta_fatos import chaves_do_pedido
from app.services.mail_atendimento import administracao, ponte
from app.services.mail_atendimento.constantes import ESTADOS_COM_TEXTO

_NUMERO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
Linha = tuple[MailMessageMeta, MailMessage, AtendimentoConversa]


@dataclass
class EmailsDaVenda:
    estado: str
    pedido: str | None = None
    linhas: list[Linha] = field(default_factory=list)


def _numeros_citados(meta: MailMessageMeta) -> set[str]:
    return {
        str(p["pedido"])
        for p in (meta.pedidos_citados or [])
        if isinstance(p, dict) and p.get("pedido")
    }


async def listar(
    session: AsyncSession, conversa: AtendimentoConversa, scope: TeamScope
) -> EmailsDaVenda:
    """A conversa já autorizada fornece loja/plataforma/pedido, nunca a query.

    Pack/order são aliases explícitos do adaptador na PRÓPRIA conversa.
    Conta por nome, alias de e-mail, pessoa ou família não são prova de venda.
    """
    if separa_email.e_conversa_de_email(conversa) or conversa.plataforma in PLATAFORMAS_EXTERNAS:
        return EmailsDaVenda("indisponivel")
    pedido = str(conversa.pedido_marketplace or "").strip()
    if not _NUMERO.fullmatch(pedido):
        return EmailsDaVenda("sem_pedido")
    numeros = {n for n in chaves_do_pedido(conversa) if _NUMERO.fullmatch(n)}
    c, m = AtendimentoConversa, MailMessageMeta
    if conversa.integration_id is not None:
        if not scope.unrestricted and conversa.integration_id not in scope.integration_ids:
            return EmailsDaVenda("indisponivel", pedido)
        loja_c = (c.integration_id == conversa.integration_id,)
        loja_m = (m.integration_id == conversa.integration_id,)
    elif (ficha := ponte.ficha_da_conversa(conversa)) is not None:
        if not scope.unrestricted and ficha not in scope.store_info_ids:
            return EmailsDaVenda("indisponivel", pedido)
        loja_c = (
            c.integration_id.is_(None),
            c.dados[ponte.CHAVE]["store_info_id"].astext == str(ficha),
        )
        loja_m = (m.integration_id.is_(None), m.store_info_id == ficha)
    else:
        return EmailsDaVenda("sem_loja", pedido)

    # Há índice de conversa_id: primeiro restringe aos grupos que têm alguma
    # prova desta venda, depois confere TODOS os pedidos de cada grupo.
    grupos = select(m.conversa_id).where(
        m.pedido_marketplace.in_(numeros),
        m.plataforma == conversa.plataforma,
        *loja_m,
        m.estado.in_(ESTADOS_COM_TEXTO),
    )
    linhas = (
        await session.execute(
            select(m, MailMessage, c)
            .join(c, c.id == m.conversa_id)
            .join(MailMessage, MailMessage.id == m.message_id)
            .where(
                separa_email.conversa_de_email_sql(c),
                c.plataforma == conversa.plataforma,
                m.plataforma == conversa.plataforma,
                *loja_c,
                *loja_m,
                or_(c.pedido_marketplace.in_(numeros), c.id.in_(grupos)),
            )
            .order_by(MailMessage.received_at.desc(), m.message_id)
        )
    ).all()
    pedidos_por_grupo: dict = {}
    for meta, _message, grupo in linhas:
        provas = pedidos_por_grupo.setdefault(grupo.id, set())
        if grupo.pedido_marketplace:
            provas.add(grupo.pedido_marketplace)
        if meta.pedido_marketplace:
            provas.add(meta.pedido_marketplace)
        provas.update(_numeros_citados(meta))

    selecionadas = []
    for meta, message, grupo in linhas:
        if meta.estado not in ESTADOS_COM_TEXTO:
            continue
        if meta.pedido_marketplace:
            pertence = meta.pedido_marketplace in numeros
        else:
            # Um retorno sem número só herda a venda quando todas as provas
            # do grupo apontam a essa mesma venda; X/Y fica sem inferência.
            provas = pedidos_por_grupo[grupo.id]
            pertence = bool(provas) and provas <= numeros
        if pertence:
            selecionadas.append((meta, message, grupo))
    # Conferir a política DEPOIS de reunir todas as provas do grupo: ocultar
    # Y antes disso faria um retorno sem número parecer exclusivo de X.
    if selecionadas and scope.email_user is not None:
        permitidos = set(
            await session.scalars(
                select(MailMessage.id).where(
                    MailMessage.id.in_([m.message_id for m, _, _ in selecionadas]),
                    administracao.filtro_mensagem(scope.email_user),
                )
            )
        )
        selecionadas = [linha for linha in selecionadas if linha[0].message_id in permitidos]
    return EmailsDaVenda("ok", pedido, selecionadas)


async def mover_conversa(
    session: AsyncSession, de: AtendimentoConversa, para: AtendimentoConversa
) -> None:
    """As mensagens da conversa sem vínculo vão para a conversa do pedido (MOVE, sem duplicar).

    Veio da rota "vincular" (`routers/atendimento_email`, 13/10/2026): o
    religamento das vendas (`scripts/mail_religar_vendas.py`, 21/10/2026) usa o
    mesmo passo. Não commita."""
    await session.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == de.id)
        .values(conversa_id=para.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailOutboxMeta)
        .where(MailOutboxMeta.conversa_id == de.id)
        .values(conversa_id=para.id)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    await gravar.recalcular_conversa(session, para)
    await gravar.recalcular_conversa(session, de)
    de.situacao = "fechada"
    de.sem_resposta_necessaria = True
    de.dados = {
        **(de.dados or {}),
        ponte.CHAVE: {**ponte.dados_mail(de), "movida_para": str(para.id)},
    }
