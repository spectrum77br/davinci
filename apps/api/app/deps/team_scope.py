"""Escopo por equipe de vendas (Fase 2).

Fonte única que resolve, para um usuário não-admin com equipe(s), o conjunto
de "chaves de loja" que ele pode enxergar. Todas as telas de dados
(Produtos, Anúncios, Tabela de Preços, Reembolso, Logística, Integrações,
Sync Logs, etc.) chegam à `store_info.sales_team` por UMA destas chaves:

  - integration_id  → Integração / ProductLink / SyncLog / Produtos (via link)
  - bling_store_id  → BlingOrder.loja / Reembolso / Logística (via pedido)
  - (plataforma, nome da conta) → Reembolso.conta / Logística.conta (texto,
    por `clausula_conta` / `conta_no_escopo`)

O nome sozinho NÃO é chave (30/10/2026, separação Londres/Israel/Marrocos):
"inova", "barbosa", "mega" e "eron" existem em plataformas diferentes de
equipes diferentes, e a Shopee inova de uma equipe aparecia na Logística da
outra por causa do ML inova. A conta casa pela plataforma + nome.

O resolver faz UM SELECT em `store_info` (todas as lojas: as de fora da equipe
só servem para saber quais nomes se repetem) e monta os conjuntos. Cada router
aplica o filtro pela chave que tiver.

Semântica (igual à Fase 1 / `user_scope`):
  - admin → `unrestricted=True` (vê tudo, sem filtro)
  - usuário SEM equipe → `unrestricted=True` (vê tudo)
  - usuário COM equipe → escopo pelos conjuntos (só a sua equipe)

Assim o rollout é seguro: quem não tem equipe atribuída continua vendo tudo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from uuid import UUID

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationPlatform, Store, User, UserRole
from app.models.enums import PLATFORMS
from app.models.integration import Integration
from app.models.pricing import StoreInfo
from app.services.atendimento.lojas import _PLATAFORMA_DA_FICHA, _PREFIXOS, _SEPARADOR

# Os nomes das equipes de vendas (30/10/2026, Eduardo: "agora vai ter 3
# equipes: 1 - londres, 2 - Israel, 3 - marrocos" — a "Separação de contas":
# Vendedor 1 e 2 Celular/Eletro, Vendedor 3 Mala). O número é o de
# `store_info.sales_team` / `users.sales_teams`; sem nome, "Equipe N".
NOMES_EQUIPES: dict[int, str] = {101: "Londres", 102: "Israel", 103: "Marrocos"}


def nome_da_equipe(numero: int) -> str:
    return NOMES_EQUIPES.get(numero, f"Equipe {numero}")


@dataclass
class TeamScope:
    """Chaves de loja visíveis ao usuário. `unrestricted` = sem filtro."""

    unrestricted: bool
    store_info_ids: set[UUID] = field(default_factory=set)
    integration_ids: set[UUID] = field(default_factory=set)
    bling_store_ids: set[str] = field(default_factory=set)
    # O nome sozinho (lower/trim). Não filtra linha: vaza entre equipes.
    account_names: set[str] = field(default_factory=set)
    # (plataforma canônica, chave do nome) de cada loja da equipe: é por aqui
    # que a conta de Logística/Reembolso casa (`clausula_conta`), 30/10/2026.
    platform_accounts: set[tuple[str, str]] = field(default_factory=set)
    # As chaves de nome que NENHUMA loja fora da equipe usa: as únicas que
    # valem numa linha sem plataforma (nem na coluna, nem na frente da conta).
    unshared_account_keys: set[str] = field(default_factory=set)
    # Os canais do ROBÔ (Temu/AliExpress, sem integração) que a pessoa vê no
    # /atendimento (30/10/2026, `services/atendimento/escopo.py`). O escopo
    # por equipe não os conhece: fica vazio aqui.
    canal_ids: set[UUID] = field(default_factory=set)
    # A leitura das conversas de e-mail tem uma política adicional por pessoa.
    # Mantém o usuário da requisição disponível aos detalhes compartilhados;
    # não muda o escopo comercial nem a igualdade dos escopos existentes.
    email_user: User | None = field(default=None, compare=False, repr=False)


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip().lower()
    return s or None


# A régua do nome (30/10/2026): minúsculo, sem acento e só letras e números.
# "Jlas 2" (o Reembolso) e "jlas2" (o cadastro), "Shopee - Atlas" e "shopee
# atlas" viram a mesma chave. A MESMA régua roda no banco (`_chave_sql`): o
# Python monta as chaves da equipe e o SQL normaliza a coluna, sem os dois
# lados divergirem. As maiúsculas acentuadas estão na tabela porque o lower()
# do Postgres não as baixa em todo locale.
_COM_ACENTO = "áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ"
_SEM_ACENTO = "aaaaaeeeeiiiiooooouuuucn" * 2
_TIRA_ACENTO = str.maketrans(_COM_ACENTO, _SEM_ACENTO)
_NAO_ALFANUM = "[^a-z0-9]+"


def _chave(valor: object) -> str:
    texto = str(getattr(valor, "value", valor) or "")
    return re.sub(_NAO_ALFANUM, "", texto.lower().translate(_TIRA_ACENTO))


def _chave_sql(coluna):
    return func.regexp_replace(
        func.translate(func.lower(coluna), _COM_ACENTO, _SEM_ACENTO), _NAO_ALFANUM, "", "g"
    )


# A plataforma pela chave de `store_info.platform` ("ml", "shopee"...). Cada
# tabela escreve de um jeito: a Logística grava o rótulo ("Mercado Livre",
# "TikTok"), o Reembolso a chave ("ml") ou nada e, na frente da conta, o
# rótulo curto ("ML Aguiar 2", "Shopee ATV"); a própria store_info tem "ml" e
# "mercadolivre". As grafias são as do atendimento (`_PREFIXOS`), a mesma
# régua do par ficha × integração do e-mail (`mail_atendimento.rotear`).
_PLATAFORMA_CANONICA: dict[str, str] = {
    **{_chave(grafia): plat for grafia, plat in _PLATAFORMA_DA_FICHA.items()},
    **{_chave(grafia): plat for plat, grafias in _PREFIXOS.items() for grafia in (*grafias, plat)},
}


def plataforma_canonica(valor: object) -> str:
    """'Mercado Livre' / 'mercadolivre' / 'ML' → 'ml'; sem grafia conhecida, a chave dela."""
    chave = _chave(valor)
    return _PLATAFORMA_CANONICA.get(chave, chave)


@cache
def _grafias(plataforma: str) -> frozenset[str]:
    """As chaves com que a plataforma aparece escrita, na coluna ou na frente da conta."""
    return frozenset({g for g, p in _PLATAFORMA_CANONICA.items() if p == plataforma} | {plataforma})


def _nome_sem_prefixo(nome: str | None, plataforma: str) -> str:
    """O nome sem o nome da PRÓPRIA plataforma na frente ("carrefour poofy" na
    Carrefour → "poofy", "Shopee - Atlas" → "Atlas"); se sobrar nada, o nome
    todo. A regra de `mail_atendimento.rotear._nome_do_par`."""
    limpo = " ".join((nome or "").split())
    prefixos = (*_PREFIXOS.get(plataforma, ()), plataforma) if plataforma else ()
    for prefixo in prefixos:
        m = re.match(rf"{re.escape(prefixo)}{_SEPARADOR}(?P<resto>.+)$", limpo, re.IGNORECASE)
        if m and m.group("resto").strip():
            return m.group("resto").strip()
    return limpo


def _chave_da_conta(plataforma: object, nome: str | None) -> tuple[str, str] | None:
    """(plataforma canônica, chave do nome) de uma loja ou integração; None se falta um."""
    plat = plataforma_canonica(plataforma)
    chave = _chave(_nome_sem_prefixo(nome, plat))
    return (plat, chave) if plat and chave else None


def _chaves_de_conta(scope: TeamScope) -> tuple[set[tuple[str, str]], set[str]]:
    """As lojas da equipe em cada grafia da plataforma: os pares (plataforma
    escrita, nome) e as contas com a plataforma na frente do nome."""
    pares: set[tuple[str, str]] = set()
    contas: set[str] = set()
    for plataforma, nome in scope.platform_accounts:
        for grafia in _grafias(plataforma):
            pares.add((grafia, nome))
            contas.add(grafia + nome)
    return pares, contas


def clausula_conta(scope: TeamScope, col_plataforma, col_conta):
    """WHERE da equipe pela conta da linha (texto); None = irrestrito ou sem chave.

    A linha diz a loja de três jeitos:
      - a plataforma na coluna e o nome na conta (Logística: "Mercado Livre" +
        "inova");
      - a plataforma na FRENTE do nome, com a coluna vazia ou não (Reembolso:
        "Shopee ATV");
      - sem plataforma nenhuma: aí só vale o nome que nenhuma loja fora da
        equipe usa (nome repetido em outra equipe, ninguém adivinha).
    """
    if scope.unrestricted:
        return None
    pares, contas = _chaves_de_conta(scope)
    plataforma = _chave_sql(col_plataforma)
    conta = _chave_sql(col_conta)
    ors = []
    if pares:
        ors.append(tuple_(plataforma, conta).in_(sorted(pares)))
    if contas:
        ors.append(conta.in_(sorted(contas)))
    if scope.unshared_account_keys:
        ors.append(
            and_(
                func.coalesce(plataforma, "") == "",
                conta.in_(sorted(scope.unshared_account_keys)),
            )
        )
    return or_(*ors) if ors else None


def conta_no_escopo(scope: TeamScope, plataforma: str | None, conta: str | None) -> bool:
    """A régua de `clausula_conta` numa linha já carregada (mesmo resultado)."""
    if scope.unrestricted:
        return True
    nome = _chave(conta)
    if not nome:
        return False
    plat = _chave(plataforma)
    pares, contas = _chaves_de_conta(scope)
    return (
        (plat, nome) in pares
        or nome in contas
        or (not plat and nome in scope.unshared_account_keys)
    )


async def resolve_team_scope(session: AsyncSession, user: User) -> TeamScope:
    """Monta o TeamScope do usuário a partir das lojas da(s) equipe(s) dele."""
    if user.role == UserRole.ADMIN:
        return TeamScope(unrestricted=True, email_user=user)

    teams = user.sales_teams or []
    if not teams:
        return TeamScope(unrestricted=True, email_user=user)

    equipes = set(teams)
    rows = (
        await session.execute(
            select(
                StoreInfo.id,
                StoreInfo.integration_id,
                StoreInfo.bling_store_id,
                StoreInfo.account_name,
                StoreInfo.platform,
                StoreInfo.sales_team,
            )
        )
    ).all()

    scope = TeamScope(unrestricted=False, email_user=user)
    # Lojas sem o FK `integration_id` preenchido (backfill preguiçoso) — caso
    # comum: só `marquezini` tinha o FK. Resolvemos a integração pelo par
    # (nome, plataforma), espelhando `_resolve_linked_integration`, pra que
    # TODAS as contas do membro apareçam (Integrações/Sync de estoque), não só
    # as que já tinham o FK. Leitura pura (sem backfill aqui).
    pendentes: set[tuple[str, str]] = set()
    nomes_da_equipe: set[str] = set()
    nomes_de_fora: set[str] = set()
    # A integração que o FK já dá a uma loja de fora: nem o par nem a ponte a
    # trazem para a equipe (duas "inova" do ML em equipes diferentes).
    fk_de_fora: set[UUID] = set()
    # Loja da equipe sem o FK → a plataforma dela, pelo nº do Bling (a ponte).
    sem_fk_pelo_bling: dict[int, str] = {}
    for store_info_id, integration_id, bling_store_id, account_name, platform, team in rows:
        conta = _chave_da_conta(platform, account_name)
        nome = conta[1] if conta else _chave(account_name)
        if team not in equipes:
            if nome:
                nomes_de_fora.add(nome)
            if integration_id is not None:
                fk_de_fora.add(integration_id)
            continue
        scope.store_info_ids.add(store_info_id)
        if nome:
            nomes_da_equipe.add(nome)
        if conta:
            scope.platform_accounts.add(conta)
        if integration_id is not None:
            scope.integration_ids.add(integration_id)
        elif conta:
            pendentes.add(conta)
            if bling_store_id and str(bling_store_id).strip().isdigit():
                sem_fk_pelo_bling[int(bling_store_id)] = conta[0]
        if bling_store_id:
            scope.bling_store_ids.add(bling_store_id)
        nm = _norm(account_name)
        if nm:
            scope.account_names.add(nm)
    scope.unshared_account_keys = nomes_da_equipe - nomes_de_fora

    # O par vale com a plataforma canônica dos dois lados ("mercadolivre" na
    # loja × "ml" na integração) e sem o nome da plataforma na frente (30/10:
    # as integrações "carrefour poofy" / "carrefour vr" das lojas "poofy" /
    # "VR" ficavam fora do Atendimento da equipe). Plataforma fora do enum das
    # integrações (netshoes, interno...) não tem integração, e o IN quebraria.
    plataformas = {plat for plat, _ in pendentes} & set(PLATFORMS)
    if plataformas:
        integ_rows = (
            await session.execute(
                select(Integration.id, Integration.name, Integration.platform).where(
                    Integration.platform.in_([IntegrationPlatform(p) for p in sorted(plataformas)])
                )
            )
        ).all()
        for integ_id, integ_name, integ_platform in integ_rows:
            if integ_id in fk_de_fora:
                continue
            if _chave_da_conta(integ_platform, integ_name) in pendentes:
                scope.integration_ids.add(integ_id)

    # A ponte do cadastro de Empresas (`stores`: a loja do Bling → a
    # integração) para a loja cujo nome não casa com o da integração (30/10:
    # a shopee atlas é a integração "Jlas", a fiore é a " Kia"). Só lê: o FK
    # fica como está (gravá-lo mudaria o nome da loja no Atendimento).
    if sem_fk_pelo_bling:
        ponte = (
            await session.execute(
                select(Store.bling_store_id, Integration.id, Integration.platform)
                .join(Integration, Integration.id == Store.integration_id)
                .where(Store.bling_store_id.in_(sorted(sem_fk_pelo_bling)))
            )
        ).all()
        for bling, integ_id, integ_platform in ponte:
            if (
                plataforma_canonica(integ_platform) == sem_fk_pelo_bling.get(bling)
                and integ_id not in fk_de_fora
            ):
                scope.integration_ids.add(integ_id)
    return scope
