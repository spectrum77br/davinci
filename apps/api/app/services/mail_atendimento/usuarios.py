"""A lista de usuários do e-mail (12/11/2026) — a nova "Administração" da aba E-mail.

O Eduardo: a administração antiga "está complexa e sem uso atualmente"; no
lugar, uma linha por usuário (como as subcontas de um ERP) e, no Editar, as
caixas de e-mail em checkbox: o admin marca quais caixas a pessoa vê.

Nada de tabela nova. A tela só lê e grava o que JÁ decide o acesso:

  • ESTADO (ligado/desligado) = a permissão do usuário `atendimento_email_leitura`
    (view), a mesma da tela Usuários › permissões — é ela que abre as Caixas na
    aba (`acesso.ve_caixa_email`). Desligar também tira o `view` de
    `atendimento_email_operacao` (sem caixa, não há o que responder). Ligar não
    concede responder nem as Filas (`atendimento_email_aba`), que seguem nas
    permissões do usuário.
  • CAIXAS = a lista de leitores de cada caixa (`mail_mailbox_settings.leitores`,
    a mesma do "Quem mais vê"); `administracao.filtro_caixas` lê dela.
  • PASTAS (o Eduardo: "qnd digo escolher tambem me refiro as pastas das lojas
    ex informativo sac de sites tudo isso realmente checkboxes") = dentro da
    caixa marcada, a conta inteira (sem linha) ou só as pastas marcadas
    (`mail_leitor_pastas`, a `pasta_chave` do índice);
    `administracao.filtro_da_caixa` lê dela.

Admin vê tudo e não é editado aqui. O dono da caixa já a vê (não entra na
lista). Dentro da caixa, quem não é admin nem dono nunca vê o e-mail privado
do dono nem o de segurança (`administracao.filtro_da_caixa`). O /atendimento e
as equipes (lojas por pessoa) não mudam: esta tela não mexe neles.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.enums import UserRole, UserStatus
from app.models.mail import MailMailbox
from app.models.mail_admin import MailAdminEvento, MailLeitorPastas
from app.models.mail_atendimento import MailMailboxSettings
from app.models.user import User
from app.services.atendimento import acesso
from app.services.mail_atendimento.leitores import MAX_LEITORES

LEITURA = "atendimento_email_leitura"
MAX_PASTAS = 200
OPERACAO = "atendimento_email_operacao"


class UsuariosError(ValueError):
    def __init__(self, codigo: str, status: int = 422):
        self.codigo = codigo
        self.status = status
        super().__init__(codigo)


def _grant(user: User, recurso: str) -> dict:
    g = (user.permissions or {}).get(recurso)
    return dict(g) if isinstance(g, dict) else {}


def ligado(user: User) -> bool:
    """Abre as Caixas da aba E-mail (a mesma régua da API)."""
    return user.status == UserStatus.ACTIVE and acesso.ve_caixa_email(user)


def _linha(
    user: User,
    caixas: list[MailMailbox],
    leitores: dict[UUID, list[str]],
    pastas: dict[tuple[UUID, UUID], list[str]],
) -> dict:
    admin = user.role == UserRole.ADMIN
    todas_ids = [str(c.id) for c in caixas]
    if admin:
        marcadas = todas_ids
    else:
        marcadas = [
            str(c.id)
            for c in caixas
            if c.owner_user_id == user.id or str(user.id) in leitores.get(c.id, [])
        ]
    # Só as caixas marcadas em PARTE (algumas pastas); as outras são inteiras.
    em_parte = {
        str(c.id): pastas[(c.id, user.id)]
        for c in caixas
        if not admin
        and c.owner_user_id != user.id
        and str(c.id) in marcadas
        and (c.id, user.id) in pastas
    }
    return {
        "id": str(user.id),
        "login": user.email,
        "nome": user.name or user.email.split("@", 1)[0],
        "admin": admin,
        "ativo": user.status == UserStatus.ACTIVE,
        "ligado": admin or ligado(user),
        "caixas": marcadas,
        "pastas": em_parte,
        "todas": bool(todas_ids) and len(marcadas) == len(todas_ids) and not em_parte,
        "dono_de": [str(c.id) for c in caixas if c.owner_user_id == user.id],
        "criado_em": user.created_at,
        "ultimo_acesso": user.last_login_at,
    }


async def _caixas_e_leitores(
    session: AsyncSession,
) -> tuple[list[MailMailbox], dict[UUID, list[str]]]:
    caixas = list((await session.scalars(select(MailMailbox).order_by(MailMailbox.label))).all())
    leitores = {
        mid: [str(x) for x in (lista or [])]
        for mid, lista in (
            await session.execute(
                select(MailMailboxSettings.mailbox_id, MailMailboxSettings.leitores)
            )
        ).all()
    }
    return caixas, leitores


async def _pastas(session: AsyncSession) -> dict[tuple[UUID, UUID], list[str]]:
    return {
        (p.mailbox_id, p.user_id): list(p.pastas or [])
        for p in (await session.scalars(select(MailLeitorPastas))).all()
    }


async def listar(session: AsyncSession) -> dict:
    """As caixas (sem as pastas: a rota junta a lista de pastas de cada uma) e
    uma linha por usuário."""
    caixas, leitores = await _caixas_e_leitores(session)
    pastas = await _pastas(session)
    usuarios = (await session.scalars(select(User).order_by(User.created_at, User.email))).all()
    return {
        "caixas": [{"id": str(c.id), "label": c.label} for c in caixas],
        "usuarios": [_linha(u, caixas, leitores, pastas) for u in usuarios],
    }


async def salvar(
    session: AsyncSession,
    alvo_id: UUID,
    *,
    ligar: bool,
    caixas: dict[UUID, list[str] | None],
    autor: User,
    agora: datetime | None = None,
) -> dict:
    """Grava ESTADO, CAIXAS e PASTAS de uma pessoa (não admin) e devolve a linha.

    `caixas`: as marcadas → None = a conta inteira, lista = só aquelas pastas.
    Caixa marcada = a pessoa entra na lista de leitores dela; desmarcada = sai
    (e as pastas dela somem). O dono não entra (já vê tudo). Usuário inativo só
    pode ser desligado/desmarcado.
    """
    agora = agora or datetime.now(UTC)
    alvo = await session.scalar(
        select(User)
        .where(User.id == alvo_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if alvo is None:
        raise UsuariosError("usuario_nao_encontrado", 404)
    if alvo.role == UserRole.ADMIN:
        raise UsuariosError("admin_ve_todas")
    if alvo.status != UserStatus.ACTIVE and (ligar or caixas):
        raise UsuariosError("usuario_inativo")
    todas = {c.id: c for c in (await session.scalars(select(MailMailbox))).all()}
    if any(c not in todas for c in caixas):
        raise UsuariosError("caixa_nao_encontrada", 404)
    for lista in caixas.values():
        if lista is not None and (not lista or len(lista) > MAX_PASTAS):
            raise UsuariosError("pastas_invalidas")

    # ESTADO: só os `view` das duas concessões de leitura/operação.
    perms = dict(alvo.permissions or {})
    leitura = _grant(alvo, LEITURA)
    if bool(leitura.get("view")) != ligar:
        perms[LEITURA] = {"view": ligar, "edit": False, "delete": False}
    if not ligar and _grant(alvo, OPERACAO).get("view"):
        perms[OPERACAO] = {"view": False, "edit": False, "delete": False}
    if perms != (alvo.permissions or {}):
        alvo.permissions = perms
        flag_modified(alvo, "permissions")

    uid = str(alvo.id)
    for mid, caixa in sorted(todas.items(), key=lambda kv: str(kv[0])):
        if caixa.owner_user_id == alvo.id:
            continue
        quer = mid in caixas
        # CAIXA: a lista de leitores (trava a linha, cria se faltar).
        await session.execute(
            pg_insert(MailMailboxSettings)
            .values(mailbox_id=mid)
            .on_conflict_do_nothing(index_elements=["mailbox_id"])
        )
        linha = await session.scalar(
            select(MailMailboxSettings)
            .where(MailMailboxSettings.mailbox_id == mid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert linha is not None
        atual = [str(x) for x in (linha.leitores or [])]
        if (uid in atual) != quer:
            novos = [*atual, uid] if quer else [x for x in atual if x != uid]
            if len(novos) > MAX_LEITORES:
                raise UsuariosError("leitores_demais")
            linha.leitores = novos
            linha.leitores_updated_by = autor.id
            linha.leitores_updated_at = agora
            linha.updated_by = autor.id
            session.add(
                MailAdminEvento(
                    mailbox_id=mid,
                    autor_id=autor.id,
                    acao="leitor_incluido" if quer else "leitor_retirado",
                    dados={"user_id": uid},
                )
            )
        # PASTAS: sem linha = a conta inteira.
        querem = sorted(set(caixas[mid])) if quer and caixas[mid] is not None else None
        p = await session.get(MailLeitorPastas, (mid, alvo.id), populate_existing=True)
        antes = sorted(p.pastas or []) if p is not None else None
        if querem == antes:
            continue
        if querem is None:
            await session.delete(p)
        else:
            if p is None:
                p = MailLeitorPastas(mailbox_id=mid, user_id=alvo.id)
                session.add(p)
            p.pastas, p.updated_by = querem, autor.id
        session.add(
            MailAdminEvento(
                mailbox_id=mid,
                autor_id=autor.id,
                acao="leitor_pastas",
                dados={"user_id": uid, "pastas": querem},
            )
        )
    await session.flush()
    caixas_todas, leitores = await _caixas_e_leitores(session)
    return _linha(alvo, caixas_todas, leitores, await _pastas(session))
