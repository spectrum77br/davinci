"""Leitura das caixas e lixeira local, sem mudar caixa, ponte ou Tuta.

12/11/2026, o Eduardo: a administração antiga (regra por pessoa × caixa ×
loja × venda, tabela `mail_acessos`) "está complexa e sem uso" — saiu. Ficou:

  • `filtro_caixas`/`pode_ver_caixa` — quais CAIXAS a pessoa abre: admin, o
    dono, e quem o admin marcou na lista de usuários do e-mail (os leitores da
    caixa, `services/mail_atendimento/usuarios.py`);
  • `filtro_da_caixa` — dentro da caixa inteira, quem não é admin nem dono
    nunca vê o e-mail privado do dono nem o de segurança;
  • `filtro_mensagem`/`filtro_conversa`/`visao_rapida` — a lixeira LOCAL
    esconde o e-mail (e a conversa que tem um deles) de todos;
  • `mudar_lixeira` — excluir/restaurar no DaVinci, nunca no Tuta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import (
    Uuid,
    and_,
    case,
    cast,
    false,
    func,
    literal,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.models.enums import UserRole, UserStatus
from app.models.mail import MailMailbox, MailMessage, MailOutbox
from app.models.mail_admin import MailAdminEvento, MailLeitorPastas, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice, MailMailboxSettings, MailMessageMeta
from app.models.user import User
from app.services.atendimento import separa_email
from app.services.mail_atendimento import indice


def filtro_mensagem(user: User, message_id=MailMessage.id, *, incluir_lixeira=False):
    """Expressão SQL correlata: aplicar ANTES de contar/paginar/decifrar.

    É uma restrição, não uma concessão de caixa/equipe; a rota mantém sua
    autorização original e acrescenta esta expressão. Desde 12/11/2026 (o
    Eduardo: a administração antiga "está complexa e sem uso") não há mais
    regra por pessoa × loja × venda: a lixeira LOCAL esconde de todos, e
    quem não está ativo não vê nada. Quem vê qual CAIXA é `filtro_caixas`.
    """
    tr = aliased(MailLixeira)
    excluida = (
        select(literal(1))
        .where(tr.message_id == message_id, tr.excluido_em.is_not(None))
        .correlate_except(tr)
        .exists()
    )
    visivel = literal(True) if incluir_lixeira else not_(excluida)
    if user.role == UserRole.ADMIN:
        return visivel
    if user.status != UserStatus.ACTIVE:
        return false()
    return visivel


SELOS_DA_EQUIPE = (indice.SELO_LOJA, indice.SELO_SITE, indice.SELO_SEM_LOJA)


def filtro_da_caixa(user: User, message_id=MailMessage.id, *, incluir_lixeira=False):
    """A CAIXA INTEIRA (Caixas da aba E-mail, `/api/mail`): `filtro_mensagem` e,
    para quem não é admin nem dono da caixa, só o e-mail das LOJAS (loja, site,
    sem loja). O e-mail PRIVADO do dono (caixa "só aliases de loja", fora das
    lojas) e o de SEGURANÇA (código, senha, acesso) nunca aparecem para a pessoa
    a quem o admin marcou a caixa (12/11/2026). Se o admin marcou só ALGUMAS
    pastas da caixa (`mail_leitor_pastas`), só o e-mail dessas pastas. Sem selo
    ou sem pasta no índice: falha fechada."""
    base = filtro_mensagem(user, message_id, incluir_lixeira=incluir_lixeira)
    if user.role == UserRole.ADMIN or user.status != UserStatus.ACTIVE:
        return base
    msg, caixa, m, i, lp = (
        aliased(MailMessage),
        aliased(MailMailbox),
        aliased(MailMessageMeta),
        aliased(MailCaixaIndice),
        aliased(MailLeitorPastas),
    )
    selo = indice.colunas_do_selo(i, m)["selo"]
    na_pasta = or_(
        lp.user_id.is_(None),
        func.coalesce(lp.pastas.bool_op("?")(i.pasta_chave), false()),
    )
    liberada = (
        select(literal(1))
        .select_from(msg)
        .join(caixa, caixa.id == msg.mailbox_id)
        .outerjoin(m, m.message_id == msg.id)
        .outerjoin(i, i.message_id == msg.id)
        .outerjoin(lp, and_(lp.mailbox_id == msg.mailbox_id, lp.user_id == user.id))
        .where(
            msg.id == message_id,
            or_(
                caixa.owner_user_id == user.id,
                and_(selo.in_(SELOS_DA_EQUIPE), na_pasta),
            ),
        )
        .correlate_except(msg, caixa, m, i, lp)
        .exists()
    )
    return and_(base, liberada)


def filtro_conversa(user: User, c=AtendimentoConversa):
    """Não libera timeline/prévia de conversa com qualquer e-mail não visível.

    Confere também `payload.mail.message_id`: reprocessar/apagar meta não
    pode fazer reaparecer um corpo antigo ainda copiado no Atendimento.
    """
    m, a, origem = (
        aliased(MailMessageMeta),
        aliased(AtendimentoMensagem),
        aliased(MailMessage),
    )
    meta_negada = (
        select(literal(1))
        .where(m.conversa_id == c.id, not_(filtro_mensagem(user, m.message_id)))
        .correlate_except(m)
        .exists()
    )
    ref = a.payload["mail"]["message_id"].astext
    # A referência vem de JSON legado: o CAST precisa ficar dentro do CASE
    # para uma string malformada nunca interromper a autorização. Converter
    # a referência (em vez da PK) permite buscar o original pelo índice UUID.
    # O formato canônico minúsculo mantém a igualdade textual anterior.
    ref_id = case(
        (
            and_(
                func.length(ref) == 36,
                ref.op("~")(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
            ),
            cast(ref, Uuid()),
        ),
        else_=None,
    )
    existe_original = (
        select(literal(1)).where(origem.id == ref_id).correlate_except(origem).exists()
    )
    original_permitido = (
        select(literal(1))
        .where(origem.id == ref_id, filtro_mensagem(user, origem.id))
        .correlate_except(origem)
        .exists()
    )
    copia_negada = (
        select(literal(1))
        .where(
            a.conversa_id == c.id,
            func.coalesce(ref, "") != "",
            existe_original,
            not_(original_permitido),
        )
        .correlate_except(a)
        .exists()
    )
    return or_(
        not_(separa_email.conversa_de_email_sql(c)),
        and_(not_(meta_negada), not_(copia_negada)),
    )


@dataclass(frozen=True)
class VisaoRapida:
    """`filtro_mensagem`/`filtro_conversa` decididos UMA vez por pedido (27/10/2026).

    O Eduardo, 27/10: "otimiza pra abrir rápido" — a barra de lojas da Caixa
    levava ~5 s em produção para quem não é admin, quase tudo no JIT do
    Postgres compilando as subconsultas correlatas do filtro (o custo estimado
    passava de 1 milhão; medido com EXPLAIN ANALYZE). A pessoa ativa (e o
    admin) vê exatamente o que o filtro deixa: tudo, menos a LIXEIRA local.
    Para ela, o filtro vira uma lista de ids pronta (os e-mails na lixeira e as
    conversas que têm um deles: a meta ou a cópia da mensagem) — o MESMO
    resultado, com predicados simples e indexados. Quem não está ativa segue
    no filtro inteiro (que não deixa nada), sem atalho
    (`tests/test_mail_visao_rapida.py` confere os dois caminhos)."""

    exato: bool
    user: User
    excluidos: frozenset[UUID] = frozenset()
    conversas_escondidas: frozenset[UUID] = frozenset()

    def filtro_mensagem(self, message_id=MailMessage.id):
        if self.exato:
            return filtro_mensagem(self.user, message_id)
        return message_id.notin_(sorted(self.excluidos)) if self.excluidos else true()

    def filtro_conversa(self, c=AtendimentoConversa):
        if self.exato:
            return filtro_conversa(self.user, c)
        if not self.conversas_escondidas:
            return true()
        return or_(
            not_(separa_email.conversa_de_email_sql(c)),
            c.id.notin_(sorted(self.conversas_escondidas)),
        )


async def visao_rapida(session: AsyncSession, user: User) -> VisaoRapida:
    """A `VisaoRapida` de quem pede (uma ou duas consultas leves)."""
    if user.role != UserRole.ADMIN and user.status != UserStatus.ACTIVE:
        return VisaoRapida(exato=True, user=user)
    # A lixeira que vale: a do e-mail que existe na Central (a cópia na
    # conversa só esconde quando o original existe — `filtro_conversa`).
    excluidos = frozenset(
        (
            await session.execute(
                select(MailLixeira.message_id)
                .join(MailMessage, MailMessage.id == MailLixeira.message_id)
                .where(MailLixeira.excluido_em.is_not(None))
            )
        ).scalars()
    )
    if not excluidos:
        return VisaoRapida(exato=False, user=user)
    pelas_metas = select(MailMessageMeta.conversa_id).where(
        MailMessageMeta.message_id.in_(sorted(excluidos)),
        MailMessageMeta.conversa_id.is_not(None),
    )
    # A cópia: `payload.mail.message_id` igual ao id canônico (minúsculo, 36
    # caracteres — o mesmo que o CASE do filtro converte).
    pelas_copias = select(AtendimentoMensagem.conversa_id).where(
        AtendimentoMensagem.payload["mail"]["message_id"].astext.in_(
            sorted(str(i) for i in excluidos)
        )
    )
    escondidas = frozenset((await session.execute(pelas_metas.union(pelas_copias))).scalars())
    return VisaoRapida(exato=False, user=user, excluidos=excluidos, conversas_escondidas=escondidas)


async def exigir_mensagem(session: AsyncSession, user: User, message_id: UUID) -> None:
    if not await session.scalar(
        select(MailMessage.id).where(MailMessage.id == message_id, filtro_mensagem(user))
    ):
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})


def filtro_caixas(user: User):
    """As CAIXAS que a pessoa abre (12/11/2026, a lista de usuários do e-mail):
    admin, todas; quem não está ativo, nenhuma; o resto, as que são dela (dono)
    e as em que o admin a marcou (`mail_mailbox_settings.leitores`, a mesma
    lista do "Quem mais vê"). Abrir a aba é outra regra
    (`acesso.ve_caixa_email`); o que aparece dentro, `filtro_da_caixa`."""
    if user.role == UserRole.ADMIN:
        return literal(True)
    if user.status != UserStatus.ACTIVE:
        return false()
    leitores = select(MailMailboxSettings.mailbox_id).where(
        MailMailboxSettings.leitores.contains([str(user.id)])
    )
    return or_(MailMailbox.owner_user_id == user.id, MailMailbox.id.in_(leitores))


async def pode_ver_caixa(session: AsyncSession, mailbox: MailMailbox, user: User) -> bool:
    return bool(
        await session.scalar(
            select(MailMailbox.id).where(MailMailbox.id == mailbox.id, filtro_caixas(user))
        )
    )


async def pode_responder_caixa(session: AsyncSession, mailbox: MailMailbox, user: User) -> bool:
    """Admin ou operação explícita, sempre sujeita ao acesso atual da caixa.

    A mesma regra vale na resposta, na conferência própria e no conector.
    """
    from app.services.atendimento import acesso

    if not await pode_ver_caixa(session, mailbox, user):
        return False
    return acesso.opera_caixa_email(user)


def _erro(code: str, status=422):
    return HTTPException(status, detail={"code": code})


async def mudar_lixeira(
    session: AsyncSession,
    message_id: UUID,
    autor: User,
    *,
    excluir: bool,
    motivo: str | None = None,
):
    message = await session.scalar(
        select(MailMessage).where(MailMessage.id == message_id).with_for_update()
    )
    if message is None:
        raise _erro("email_nao_encontrado", 404)
    item = await session.get(MailLixeira, message_id)
    estava = item is not None and item.excluido_em is not None
    if estava == excluir:
        return {"message_id": str(message_id), "excluido": excluir, "alterado": False}
    if excluir and await session.scalar(
        select(MailOutbox.id)
        .where(
            MailOutbox.message_id == message_id,
            MailOutbox.status.in_(("queued", "leased", "uncertain")),
        )
        .limit(1)
    ):
        raise _erro("email_envio_pendente", 409)
    agora = datetime.now(UTC)
    if item is None:
        item = MailLixeira(message_id=message_id)
        session.add(item)
    if excluir:
        item.excluido_em, item.excluido_por = agora, autor.id
        item.motivo = (motivo or "").replace("\x00", "").strip() or None
        item.restaurado_em, item.restaurado_por = None, None
    else:
        item.excluido_em = None
        item.restaurado_em, item.restaurado_por = agora, autor.id
    session.add(
        MailAdminEvento(
            mailbox_id=message.mailbox_id,
            autor_id=autor.id,
            acao="email_excluido" if excluir else "email_restaurado",
            dados={"message_id": str(message_id)},
        )
    )
    await session.flush()
    return {"message_id": str(message_id), "excluido": excluir, "alterado": True}
