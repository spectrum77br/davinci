"""Quem vê cada alerta e os filtros da tela /alertas (M-19, 23/11/2026).

A cerca é do SERVIDOR (a lista, o número do sino, o "marcar lido" e o
painel inicial passam por aqui):

- administrador: os ADM (os de todos e os dirigidos a ele), todos os de
  equipe e os pessoais DELE (o pessoal dos outros não: "Nova tarefa atribuída
  a você" é só da pessoa);
- quem não é administrador: os de equipe das lojas que ele vê
  (`deps/team_scope`: com equipe, só as lojas dela; sem equipe, todas — a
  mesma regra das outras telas) e os pessoais dele. Nunca ADM.

A equipe do alerta de anúncio é a da integração AGORA (a loja que muda de
equipe leva os alertas). Sem integração (apagada), vale a `equipe` gravada.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.deps.team_scope import NOMES_EQUIPES, escopo_das_equipes, resolve_team_scope
from app.models import Alert, AlertaLeitura, AlertType, Integration, User, UserRole
from app.models.alert import ESCOPO_ADM, ESCOPO_EQUIPE, ESCOPO_PESSOAL

# Os valores do filtro Equipe ▾ além do número da equipe.
FILTRO_ADM = "adm"
FILTRO_PESSOAL = "pessoal"
# Chave do payload que a migration 0505 usa para guardar o dono de antes.
CHAVES_INTERNAS = ("_m19_user_id",)


def e_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _equipes_do_usuario(user: User) -> list[int]:
    return sorted({int(t) for t in (user.sales_teams or []) if str(t).lstrip("-").isdigit()})


async def clausula_visivel(session: AsyncSession, user: User) -> ColumnElement[bool]:
    """O WHERE do que `user` pode ver (sem as cópias escondidas)."""
    meus = and_(Alert.escopo == ESCOPO_PESSOAL, Alert.user_id == user.id)
    if e_admin(user):
        visivel = or_(
            meus,
            and_(
                Alert.escopo == ESCOPO_ADM,
                or_(Alert.user_id.is_(None), Alert.user_id == user.id),
            ),
            Alert.escopo == ESCOPO_EQUIPE,
        )
    else:
        scope = await resolve_team_scope(session, user)
        if scope.unrestricted:
            da_equipe: ColumnElement[bool] = true()
        else:
            conds = []
            if scope.integration_ids:
                conds.append(Alert.integration_id.in_(sorted(scope.integration_ids)))
            equipes = _equipes_do_usuario(user)
            if equipes:
                conds.append(and_(Alert.integration_id.is_(None), Alert.equipe.in_(equipes)))
            da_equipe = or_(*conds) if conds else false()
        visivel = or_(meus, and_(Alert.escopo == ESCOPO_EQUIPE, da_equipe))
    return and_(Alert.duplicado_de.is_(None), visivel)


def nao_lido(user_id: UUID) -> ColumnElement[bool]:
    # correlate só o `alerts`: a lista também junta `alerta_leituras` no FROM.
    return ~(
        select(AlertaLeitura.alert_id)
        .where(AlertaLeitura.alert_id == Alert.id, AlertaLeitura.user_id == user_id)
        .correlate(Alert)
        .exists()
    )


async def contar_nao_lidos(session: AsyncSession, user: User) -> int:
    """O número do sino: só o que a pessoa vê e ainda não leu."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(await clausula_visivel(session, user), nao_lido(user.id))
        )
    ).scalar_one()


async def equipes_das_integracoes(session: AsyncSession) -> dict[UUID, int]:
    """integração → equipe (101/102/103), pela MESMA régua da cerca."""
    mapa: dict[UUID, int] = {}
    for numero in sorted(NOMES_EQUIPES):
        scope = await escopo_das_equipes(session, {numero})
        for iid in scope.integration_ids:
            mapa.setdefault(iid, numero)
    return mapa


def clausula_equipes(valores: set[str], mapa: dict[UUID, int]) -> ColumnElement[bool]:
    """Filtro Equipe ▾: 'adm', 'pessoal' e o número da equipe ('101'...)."""
    ors: list[ColumnElement[bool]] = []
    if FILTRO_ADM in valores:
        ors.append(Alert.escopo == ESCOPO_ADM)
    if FILTRO_PESSOAL in valores:
        ors.append(Alert.escopo == ESCOPO_PESSOAL)
    for valor in sorted(valores):
        if not valor.isdigit():
            continue
        numero = int(valor)
        conds: list[ColumnElement[bool]] = [
            and_(Alert.integration_id.is_(None), Alert.equipe == numero)
        ]
        ids = sorted(iid for iid, eq in mapa.items() if eq == numero)
        if ids:
            conds.append(Alert.integration_id.in_(ids))
        ors.append(and_(Alert.escopo == ESCOPO_EQUIPE, or_(*conds)))
    return or_(*ors) if ors else false()


def clausula_busca(q: str) -> ColumnElement[bool]:
    """Buscar por SKU, conta ou aviso: título (onde fica o SKU), texto e conta."""
    termo = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    padrao = f"%{termo}%"
    return or_(
        Alert.title.ilike(padrao, escape="\\"),
        Alert.message.ilike(padrao, escape="\\"),
        Alert.conta.ilike(padrao, escape="\\"),
        Alert.integration_id.in_(
            select(Integration.id).where(Integration.name.ilike(padrao, escape="\\"))
        ),
    )


async def opcoes_de_equipe(session: AsyncSession, user: User) -> list[str]:
    """As caixas do Equipe ▾: o administrador vê ADM, as três e Pessoal; quem
    é de equipe, só a dele e Pessoal; quem não tem equipe, as três e Pessoal."""
    todas = [str(n) for n in sorted(NOMES_EQUIPES)]
    if e_admin(user):
        return [FILTRO_ADM, *todas, FILTRO_PESSOAL]
    scope = await resolve_team_scope(session, user)
    if scope.unrestricted:
        return [*todas, FILTRO_PESSOAL]
    minhas = [str(n) for n in _equipes_do_usuario(user) if n in NOMES_EQUIPES]
    return [*minhas, FILTRO_PESSOAL]


# O "Tipo" da tabela, pelo tipo do alerta e pelo começo da `dedupe_key`.
_TIPO_POR_TIPO: dict[AlertType, str] = {
    AlertType.SYNC_FAILURE: "Falha sync",
    AlertType.REQUIRES_REVIEW: "Revisão",
    AlertType.LISTING_BANNED: "Banido",
    AlertType.LOW_STOCK: "Estoque baixo",
    AlertType.TAREFA_ATRIBUIDA: "Tarefa",
    AlertType.DAILY_SYNC_COMPLETED: "Robô",
    AlertType.TOKEN_EXPIRING: "Robô",
}
_TIPO_POR_CHAVE: tuple[tuple[str, str], ...] = (
    ("logistica_vigia:", "Robô"),
    ("zap:", "WhatsApp"),
    ("mudanca:", "DaVinci"),
    ("fatura_venc:", "Fatura"),
    ("flex_sem_sp:", "Flex"),
    ("pricing_confirmacao:", "Preço não aplicado"),
    ("webhook_", "Robô"),
    ("rede_social_token:", "Robô"),
)


def tipo_do_alerta(type: AlertType | str, dedupe_key: str | None) -> str:
    chave = dedupe_key or ""
    for prefixo, rotulo in _TIPO_POR_CHAVE:
        if chave.startswith(prefixo):
            return rotulo
    try:
        tipo = AlertType(getattr(type, "value", type))
    except ValueError:
        return "DaVinci"
    return _TIPO_POR_TIPO.get(tipo, "DaVinci")


def payload_publico(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (payload or {}).items() if k not in CHAVES_INTERNAS}
