"""Quem mais vê uma caixa da Central de e-mail (09/10/2026): os LEITORES.

A Central do outro dev mostra a caixa ao DONO e aos admins (`mail_central.allowed`).
Essa regra continua sendo a de ESCREVER (responder, resolver envio, PATCH da
caixa, trocar a chave, configurar). Para LER há uma regra a mais, só esta:

  `pode_ver_caixa` = dono, admin, ou usuário ATIVO na lista `leitores` da
  configuração da caixa (`mail_mailbox_settings.leitores`, migration 0389).

O leitor vê a caixa em GET /api/mail/mailboxes (marcada `so_leitura`), a lista
de mensagens, a mensagem e os anexos (os mesmos cabeçalhos da Central). Toda
rota de LEITURA de uma caixa usa `routers/mail.readable_mailbox` (que chama
esta função); a de escrita segue com `user_mailbox` (o `allowed()` dele).

Quem muda a lista: admin que MEXE no /atendimento (`acesso.pode_mexer`) — na
caixa privada de outro dono também (é o dono do negócio liberando a caixa da
empresa). Só usuários ativos entram; o dono e os admins já veem (saem da lista).
Quem/quando ficam em `leitores_updated_by`/`leitores_updated_at` e o Histórico
guarda a tabela (é de pessoa).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole, UserStatus
from app.models.mail import MailMailbox
from app.models.mail_atendimento import MailMailboxSettings
from app.models.user import User
from app.services import mail_central

MAX_LEITORES = 50


class LeitoresError(ValueError):
    def __init__(self, codigo: str, status: int = 422):
        self.codigo = codigo
        self.status = status
        super().__init__(codigo)


async def leitores_da_caixa(session: AsyncSession, mailbox_id: UUID) -> list[str]:
    valor = await session.scalar(
        select(MailMailboxSettings.leitores).where(MailMailboxSettings.mailbox_id == mailbox_id)
    )
    return [str(x) for x in (valor or [])]


async def pode_ver_caixa(session: AsyncSession, mailbox: MailMailbox, user: User) -> bool:
    """LER a caixa (lista, mensagem, anexo e as rotas de leitura que vierem):
    dono, admin, ou usuário ativo na lista de leitores. Escrever: `allowed()`."""
    if user.status != UserStatus.ACTIVE:
        return False
    if mail_central.allowed(mailbox, user):
        return True
    return str(user.id) in await leitores_da_caixa(session, mailbox.id)


def caixas_que_le(user: User) -> Select:
    """Os ids das caixas em que esta pessoa está na lista de leitores (subquery)."""
    return select(MailMailboxSettings.mailbox_id).where(
        MailMailboxSettings.leitores.contains([str(user.id)])
    )


def filtro_de_leitura(user: User) -> ColumnElement[bool]:
    """O WHERE da lista de caixas de quem não é admin: as dele e, se ATIVO,
    as em que ele está na lista de leitores."""
    dono = MailMailbox.owner_user_id == user.id
    if user.status != UserStatus.ACTIVE:
        return dono
    return or_(dono, MailMailbox.id.in_(caixas_que_le(user)))


async def _usuarios(session: AsyncSession, ids: list[str]) -> dict[str, User]:
    uuids: list[UUID] = []
    for x in ids:
        try:
            uuids.append(UUID(str(x)))
        except ValueError:
            continue
    if not uuids:
        return {}
    linhas = (await session.scalars(select(User).where(User.id.in_(uuids)))).all()
    return {str(u.id): u for u in linhas}


def _pessoa(u: User) -> dict:
    return {"id": str(u.id), "nome": u.name or u.email.split("@", 1)[0]}


async def visao(session: AsyncSession, mailbox: MailMailbox) -> dict:
    """O que a tela "Quem mais vê" precisa: a lista (com quem está inativo
    marcado, para tirar), os candidatos (ativos, sem o dono e sem admins) e
    quem/quando mudou. Nada de e-mail, senha ou chave."""
    linha = await session.get(MailMailboxSettings, mailbox.id)
    ids = [str(x) for x in (linha.leitores if linha else [])]
    por_id = await _usuarios(session, ids)
    leitores = []
    for i in ids:
        u = por_id.get(i)
        if u is None:
            continue
        leitores.append({**_pessoa(u), "ativo": u.status == UserStatus.ACTIVE})
    candidatos = (
        await session.scalars(
            select(User)
            .where(
                User.status == UserStatus.ACTIVE,
                User.role != UserRole.ADMIN,
                User.id != mailbox.owner_user_id,
            )
            .order_by(User.name, User.email)
        )
    ).all()
    quem = None
    if linha is not None and linha.leitores_updated_by is not None:
        autor = await session.get(User, linha.leitores_updated_by)
        quem = _pessoa(autor) if autor is not None else None
    return {
        "mailbox_id": str(mailbox.id),
        "leitores": leitores,
        "candidatos": [_pessoa(u) for u in candidatos],
        "atualizado_por": quem,
        "atualizado_em": linha.leitores_updated_at if linha is not None else None,
    }


async def salvar(
    session: AsyncSession,
    mailbox: MailMailbox,
    ids: list[UUID],
    user: User,
    *,
    agora: datetime | None = None,
) -> None:
    """Troca a lista inteira. A permissão (admin que mexe) é da rota.

    Usuário que não existe ou não está ativo: 422 `leitor_inativo` (nada
    muda). O dono e os admins saem da lista (já veem a caixa). Sem repetir.
    """
    agora = agora or datetime.now(UTC)
    pedidos = list(dict.fromkeys(str(i) for i in ids))
    if len(pedidos) > MAX_LEITORES:
        raise LeitoresError("leitores_demais")
    por_id = await _usuarios(session, pedidos)
    novos: list[str] = []
    for i in pedidos:
        u = por_id.get(i)
        if u is None or u.status != UserStatus.ACTIVE:
            raise LeitoresError("leitor_inativo")
        if u.id == mailbox.owner_user_id or u.role == UserRole.ADMIN:
            continue
        novos.append(i)
    await session.execute(
        pg_insert(MailMailboxSettings)
        .values(mailbox_id=mailbox.id)
        .on_conflict_do_nothing(index_elements=["mailbox_id"])
    )
    linha = await session.scalar(
        select(MailMailboxSettings)
        .where(MailMailboxSettings.mailbox_id == mailbox.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert linha is not None  # acabou de nascer (ou já existia) e está travada
    linha.leitores = novos
    linha.leitores_updated_by = user.id
    linha.leitores_updated_at = agora
    linha.updated_by = user.id
    await session.flush()
