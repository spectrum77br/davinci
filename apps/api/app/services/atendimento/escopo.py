"""As lojas que a pessoa vê no /atendimento, nos e-mails das lojas e na Garantia.

30/10/2026, o Eduardo: "será separado as lojas no atendimento por pessoas
então x usuario vai poder ver x lojas e outros não isso também se aplica pra
caixa de e-mail das lojas delas" — e, da Garantia, "eles só vão poder ver das
lojas deles". A divisão é a da planilha "Separação de contas" (Vendedor 1 =
londres, 2 = israel, 3 = marrocos), por PLATAFORMA + CONTA, gravada em
`atendimento_lojas_pessoa` (`models/atendimento_lojas_pessoa.py`).

`escopo_do_atendimento` devolve o MESMO `TeamScope` que as telas do
atendimento já aplicavam (o escopo por equipe, `deps/team_scope.py`), agora
montado pelas lojas da pessoa:

  • admin → vê tudo (`unrestricted`), como sempre;
  • quem não é admin → só as lojas dela; SEM loja nenhuma, não vê nenhuma
    (o site, as redes, o Direct e o Zap, que não são loja da planilha, ficam
    só para o admin, como já ficavam para quem tinha equipe).

A equipe de vendas (`sales_teams`) deixa de valer AQUI: a divisão de antes
(101/102/103) não é a da planilha, e a planilha é a regra do atendimento.

As chaves andam juntas (a tela de Usuários grava a loja inteira), mas o
escopo completa pelo vínculo da ponte (`rotear.integracoes_das_fichas`: o FK
ativo, senão o par nome + plataforma): a ficha traz a integração dela e a
integração traz as fichas dela. Da ficha saem também os pedidos do Bling
(`bling_store_ids`: Troca e Garantia) e o nome da conta.

O filtro do e-mail (`mail_atendimento/administracao.filtro_mensagem`) é SQL
síncrono e compara a chave do e-mail ("i:"/"f:") com as chaves GRAVADAS
(`chaves_da_pessoa_sql`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import TeamScope
from app.models import AtendimentoCanal, Integration, StoreInfo, User, UserRole
from app.models.atendimento_lojas_pessoa import PADRAO_CHAVE_LOJA, AtendimentoLojaPessoa
from app.services.atendimento import canais_externos

INTEGRACAO, FICHA, CANAL = "i", "f", "c"
_CHAVE = re.compile(PADRAO_CHAVE_LOJA)
# As fichas que não são loja de venda (o CD, a venda presencial): nunca na lista.
_FICHAS_QUE_NAO_SAO_LOJA = frozenset({"interno", "presencial"})


def chave(tipo: str, ident: UUID | str) -> str:
    return f"{tipo}:{str(ident).lower()}"


def chave_valida(valor: Any) -> bool:
    return isinstance(valor, str) and _CHAVE.match(valor) is not None


def _ids(chaves: Iterable[str], tipo: str) -> set[UUID]:
    return {UUID(c[2:]) for c in chaves if c.startswith(f"{tipo}:")}


def _norm(valor: str | None) -> str | None:
    valor = " ".join((valor or "").split()).lower()
    return valor or None


async def chaves_da_pessoa(session: AsyncSession, user_id: UUID) -> set[str]:
    return set(
        (
            await session.execute(
                select(AtendimentoLojaPessoa.chave).where(AtendimentoLojaPessoa.user_id == user_id)
            )
        ).scalars()
    )


def chaves_da_pessoa_sql(user_id: UUID):
    """As chaves gravadas da pessoa — o SELECT que o filtro do e-mail usa no IN."""
    return select(AtendimentoLojaPessoa.chave).where(AtendimentoLojaPessoa.user_id == user_id)


async def escopo_do_atendimento(session: AsyncSession, user: User) -> TeamScope:
    """O `TeamScope` do atendimento (e da Garantia) pelas lojas da pessoa."""
    if user.role == UserRole.ADMIN:
        return TeamScope(unrestricted=True, email_user=user)
    from app.services.mail_atendimento import rotear

    chaves = await chaves_da_pessoa(session, user.id)
    integracoes = _ids(chaves, INTEGRACAO)
    fichas = _ids(chaves, FICHA)
    scope = TeamScope(unrestricted=False, email_user=user, canal_ids=_ids(chaves, CANAL))
    if integracoes or fichas:
        da_integracao = await rotear.integracoes_das_fichas(session)
        integracoes |= {da_integracao[f] for f in fichas if f in da_integracao}
        fichas |= {f for f, i in da_integracao.items() if i in integracoes}
    scope.integration_ids = integracoes
    scope.store_info_ids = fichas
    if fichas:
        for bling, nome in (
            await session.execute(
                select(StoreInfo.bling_store_id, StoreInfo.account_name).where(
                    StoreInfo.id.in_(fichas)
                )
            )
        ).all():
            if bling:
                scope.bling_store_ids.add(bling)
            if (nm := _norm(nome)) is not None:
                scope.account_names.add(nm)
    return scope


# ── A tela de Usuários: as lojas para marcar e o que cada pessoa tem ──────────


def _plataforma(valor: Any) -> str:
    return str(getattr(valor, "value", valor) or "").strip().lower()


async def lojas_para_vincular(session: AsyncSession) -> list[dict[str, Any]]:
    """As LOJAS que a tela de Usuários lista para marcar, cada uma com as chaves dela.

    Uma linha por ficha de loja (Lojas: a "plataforma + conta" da planilha),
    com a integração ligada a ela (o vínculo da ponte) e o canal do robô da
    mesma plataforma e nome (Temu/AliExpress). A integração e o canal do robô
    sem ficha viram linha própria. `chat`: a loja tem chat no DaVinci (API ou
    robô) — sem ele, a linha leva só os e-mails, os pedidos e a Garantia.
    Só ids, nomes e plataformas.
    """
    from app.services.atendimento import lojas as lojas_svc
    from app.services.mail_atendimento import rotear

    integracoes = list(
        (
            await session.execute(
                select(Integration).where(
                    Integration.archived_at.is_(None), Integration.platform != "bling"
                )
            )
        ).scalars()
    )
    nomes_chat = await lojas_svc.nomes_das_lojas(session, integracoes)
    da_integracao = await rotear.integracoes_das_fichas(session)
    # Os canais SEM integração que são loja: o do robô (Temu/AliExpress), a
    # Amazon só de e-mail ("amazon:<tag>") e o número do WhatsApp (30/10/2026).
    sem_integracao = (
        (
            await session.execute(
                select(AtendimentoCanal).where(
                    AtendimentoCanal.integration_id.is_(None),
                    or_(
                        AtendimentoCanal.robo_perfil_id.is_not(None),
                        AtendimentoCanal.externo_ref.like(f"{canais_externos.PREFIXO_AMAZON}%"),
                        # O número do WhatsApp da loja (30/10/2026): linha própria.
                        AtendimentoCanal.externo_ref.like(f"{canais_externos.PREFIXO_ZAP}%"),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    robos = []
    for c in sem_integracao:
        if c.robo_perfil_id is not None:
            nome = ((c.cursor or {}).get("robo") or {}).get("loja") or ""
        else:
            nome = canais_externos.nome_do_canal(c)
        robos.append((c.id, c.plataforma, nome))
    robo_por_nome: dict[tuple[str, str], tuple[UUID, str]] = {}
    for cid, plataforma, nome in robos:
        robo_por_nome.setdefault((_plataforma(plataforma), _norm(nome) or ""), (cid, nome))

    lojas: list[dict[str, Any]] = []
    usadas_i: set[UUID] = set()
    usados_c: set[UUID] = set()
    fichas = (
        await session.execute(
            select(StoreInfo.id, StoreInfo.platform, StoreInfo.account_name).where(
                StoreInfo.archived_at.is_(None)
            )
        )
    ).all()
    por_id = {i.id: i for i in integracoes}
    for fid, plataforma, nome in fichas:
        plat = rotear.plataforma_da_ficha(_plataforma(plataforma))
        if plat in _FICHAS_QUE_NAO_SAO_LOJA:
            continue
        chaves = [chave(FICHA, fid)]
        nome_chat = None
        iid = da_integracao.get(fid)
        if iid is not None and iid in por_id:
            chaves.append(chave(INTEGRACAO, iid))
            usadas_i.add(iid)
            nome_chat = nomes_chat.get(iid)
        robo = robo_por_nome.get((plat, _norm(nome) or ""))
        if robo is not None:
            chaves.append(chave(CANAL, robo[0]))
            usados_c.add(robo[0])
            nome_chat = nome_chat or robo[1]
        lojas.append(
            {
                "chave": chaves[0],
                "plataforma": plat,
                "nome": " ".join((nome or "").split()) or "—",
                "nome_no_chat": nome_chat,
                "chat": len(chaves) > 1,
                "chaves": chaves,
            }
        )
    for integ in integracoes:
        if integ.id in usadas_i:
            continue
        lojas.append(
            {
                "chave": chave(INTEGRACAO, integ.id),
                "plataforma": _plataforma(integ.platform),
                "nome": nomes_chat.get(integ.id) or " ".join((integ.name or "").split()),
                "nome_no_chat": nomes_chat.get(integ.id),
                "chat": True,
                "chaves": [chave(INTEGRACAO, integ.id)],
            }
        )
    for cid, plataforma, nome in robos:
        if cid in usados_c:
            continue
        nome = nome or "—"
        lojas.append(
            {
                "chave": chave(CANAL, cid),
                "plataforma": _plataforma(plataforma),
                "nome": nome,
                "nome_no_chat": nome,
                "chat": True,
                "chaves": [chave(CANAL, cid)],
            }
        )
    return sorted(lojas, key=lambda lj: (lj["plataforma"], lj["nome"].casefold(), lj["chave"]))


async def vinculos(session: AsyncSession) -> dict[UUID, list[str]]:
    """{pessoa: as chaves gravadas dela} — só quem tem alguma."""
    saida: dict[UUID, list[str]] = {}
    for uid, ch in (
        await session.execute(
            select(AtendimentoLojaPessoa.user_id, AtendimentoLojaPessoa.chave).order_by(
                AtendimentoLojaPessoa.chave
            )
        )
    ).all():
        saida.setdefault(uid, []).append(ch)
    return saida


async def salvar(
    session: AsyncSession, user_id: UUID, chaves: Iterable[str], autor: User
) -> list[str]:
    """Troca as lojas da pessoa pelas `chaves` (já validadas). Não faz commit."""
    novas = sorted({c.lower() for c in chaves})
    await session.execute(
        delete(AtendimentoLojaPessoa).where(AtendimentoLojaPessoa.user_id == user_id)
    )
    session.add_all(
        AtendimentoLojaPessoa(user_id=user_id, chave=c, criado_por=autor.id) for c in novas
    )
    await session.flush()
    return novas
