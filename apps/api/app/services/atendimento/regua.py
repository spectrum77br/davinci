"""A RÉGUA do envio sozinho por tipo de pergunta (13/11/2026).

O Eduardo: "se bater os 80% continua enviando"; nos sensíveis (reembolso, cancelamento,
troca, mediação, ameaça de processo) 90%. Os 80% são a APROVAÇÃO DA EQUIPE naquele tipo,
nunca a certeza que o modelo declara: das últimas `JANELA` decisões de pessoa sobre o
rascunho daquele tipo (os julgamentos do `juiz_edicao`), quantas foram acerto (igual ou só
forma). Vale a loja quando ela tem `MIN_DECISOES`; senão a plataforma; senão não libera.

`taxas` só lê. Quem decide o envio é o portão do agente, que também exige a resposta do
banco que encaixa no caso (as regras de lastro de sempre) — a régua sozinha não solta nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Integer, and_, case, func, select

from app.services.atendimento import juiz_edicao, tipos_pergunta

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

LIMIAR_PADRAO = 0.80
LIMIAR_SENSIVEL = 0.90
JANELA = 30
MIN_DECISOES = 20


def limiar(tipo: str | None) -> float:
    """A régua do tipo (80%; 90% nos sensíveis). PURA."""
    return LIMIAR_SENSIVEL if tipo in tipos_pergunta.SENSIVEIS else LIMIAR_PADRAO


@dataclass(frozen=True)
class Taxa:
    tipo: str
    decisoes: int
    acertos: int
    escopo: str  # loja | plataforma

    @property
    def fracao(self) -> float:
        return self.acertos / self.decisoes if self.decisoes else 0.0

    @property
    def liberado(self) -> bool:
        return self.decisoes >= MIN_DECISOES and self.fracao >= limiar(self.tipo)


async def _contagens(session: AsyncSession, chave: Any, filtro: Any) -> dict[Any, tuple[int, int]]:
    from app.models.atendimento import AtendimentoJulgamento as J

    ordem = func.row_number().over(partition_by=chave, order_by=J.decidido_em.desc()).label("n")
    base = (
        select(
            *chave,
            J.julgamento.label("julgamento"),
            ordem,
        )
        .where(filtro, J.julgamento != juiz_edicao.SO_SAUDACAO)
        .subquery()
    )
    acerto = case((base.c.julgamento.in_(tuple(juiz_edicao.ACERTOS)), 1), else_=0)
    colunas = [base.c[c.key] for c in chave]
    linhas = (
        await session.execute(
            select(*colunas, func.count().label("d"), func.sum(acerto).cast(Integer).label("a"))
            .where(base.c.n <= JANELA)
            .group_by(*colunas)
        )
    ).all()
    return {tuple(linha[: len(colunas)]): (int(linha.d), int(linha.a or 0)) for linha in linhas}


def desde_da_regua() -> Any:
    """O começo da medida (`atendimento_regua_desde`), ou None (sem corte). 13/11/2026."""
    from datetime import UTC, datetime

    from app.config import get_settings

    bruto = (get_settings().atendimento_regua_desde or "").strip()
    if not bruto:
        return None
    try:
        quando = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


async def taxas_da_loja(
    session: AsyncSession, *, plataforma: str, integration_id: UUID | None
) -> dict[str, Taxa]:
    """Tipo → a taxa que vale para esta loja (a dela com `MIN_DECISOES`, senão a da plataforma)."""
    from app.models.atendimento import AtendimentoJulgamento as J

    desde = desde_da_regua()
    corte = J.decidido_em >= desde if desde is not None else True

    da_loja = (
        await _contagens(
            session,
            (J.tipo,),
            and_(J.plataforma == plataforma, J.integration_id == integration_id, corte),
        )
        if integration_id is not None
        else {}
    )
    da_plataforma = await _contagens(session, (J.tipo,), and_(J.plataforma == plataforma, corte))
    saida: dict[str, Taxa] = {}
    for (tipo,), (d, a) in da_plataforma.items():
        saida[tipo] = Taxa(tipo=tipo, decisoes=d, acertos=a, escopo="plataforma")
    for (tipo,), (d, a) in da_loja.items():
        if d >= MIN_DECISOES or tipo not in saida:
            saida[tipo] = Taxa(tipo=tipo, decisoes=d, acertos=a, escopo="loja")
    return saida


# ── As listas do canal (cron `atendimento_regua`) ───────────────────────────
#
# A régua não mexe no portão do envio (`ia.trava_do_agente`): ela escreve, canal por canal,
# as listas que todas as travas já leem — `auto_categorias`, `auto_agente` (sem os assuntos
# de produto, como hoje) e `auto_orientacao` — com os tipos que passaram. Com a chave
# `atendimento_regua_ativa` desligada, só calcula e guarda o retrato (`atendimento_regua`):
# é o que a tela e o Eduardo veem antes de ligar.


@dataclass(frozen=True)
class Listas:
    auto_categorias: tuple[str, ...]
    auto_agente: tuple[str, ...]
    auto_orientacao: tuple[str, ...]


def reprovados(taxas: dict[str, Taxa]) -> set[str]:
    """Os tipos COM medida (`MIN_DECISOES`) abaixo da régua — a prova para tirar. PURA."""
    return {t for t, x in taxas.items() if x.decisoes >= MIN_DECISOES and not x.liberado}


def com_o_piso(atuais: Listas, da_regua: Listas, fora: set[str]) -> Listas:
    """As listas da virada (o Eduardo, 13/11/2026: nada piora): o que já estava + o que a
    régua soltou, menos o que tem prova de que erra (`reprovados`). PURA."""

    def juntar(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(c for c in dict.fromkeys((*a, *b)) if c not in fora)

    return Listas(
        auto_categorias=juntar(atuais.auto_categorias, da_regua.auto_categorias),
        auto_agente=juntar(atuais.auto_agente, da_regua.auto_agente),
        auto_orientacao=juntar(atuais.auto_orientacao, da_regua.auto_orientacao),
    )


def listas_liberadas(taxas: dict[str, Taxa], categorias: Any) -> Listas:
    """As listas do canal com os tipos que passaram na régua, na ordem dos assuntos. PURA."""
    from app.services.atendimento import constantes

    passou = {t for t, x in taxas.items() if x.liberado}
    ordem = [c for c in categorias if c in passou]
    orientacao = tuple(c for c in constantes.ASSUNTOS_ORIENTACAO if c in passou)
    outros = tuple(c for c in ordem if c not in constantes.ASSUNTOS_ORIENTACAO)
    agente = tuple(c for c in outros if c not in constantes.ASSUNTOS_SO_PESSOA_NO_AGENTE)
    return Listas(auto_categorias=outros, auto_agente=agente, auto_orientacao=orientacao)


async def aplicar(
    session: AsyncSession, *, gravar_listas: bool, agora: Any = None
) -> dict[str, int]:
    """Para cada canal no Automático com o agente: a taxa de cada tipo, o retrato e (com
    `gravar_listas`) as listas do canal. Commit no fim. Devolve contagens."""
    from datetime import UTC, datetime

    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import AtendimentoCanal
    from app.models.atendimento import AtendimentoRegua
    from app.services.atendimento import constantes
    from app.services.atendimento import manual as manual_svc

    agora = agora or datetime.now(UTC)
    categorias = [c["id"] for c in await manual_svc.categorias_ativas(session)] or list(
        constantes.CATEGORIAS_INFO
    )
    canais = (
        await session.scalars(
            select(AtendimentoCanal).where(
                AtendimentoCanal.modo == constantes.MODO_AUTO,
                AtendimentoCanal.agente.is_(True),
                AtendimentoCanal.integration_id.is_not(None),
            )
        )
    ).all()
    resumo = {"canais": 0, "linhas": 0, "mudaram": 0, "tipos_liberados": 0}
    # O assunto SENSÍVEL que a régua solta agora numa loja (13/11/2026, o Eduardo quer saber).
    sensiveis_soltos: list[tuple[Any, Taxa]] = []
    for canal in canais:
        taxas = await taxas_da_loja(
            session, plataforma=canal.plataforma, integration_id=canal.integration_id
        )
        listas = listas_liberadas(taxas, categorias)
        hoje = set(canal.auto_categorias or []) | set(canal.auto_orientacao or [])
        await session.execute(delete(AtendimentoRegua).where(AtendimentoRegua.canal_id == canal.id))
        linhas = [
            {
                "canal_id": canal.id,
                "plataforma": canal.plataforma,
                "integration_id": canal.integration_id,
                "tipo": t.tipo[:32],
                "decisoes": t.decisoes,
                "acertos": t.acertos,
                "escopo": t.escopo,
                "limiar": limiar(t.tipo),
                "liberado": t.liberado,
                "liberado_hoje": t.tipo in hoje,
                "atualizado_em": agora,
            }
            for t in taxas.values()
        ]
        if linhas:
            await session.execute(pg_insert(AtendimentoRegua).values(linhas))
        resumo["canais"] += 1
        resumo["linhas"] += len(linhas)
        resumo["tipos_liberados"] += sum(1 for t in taxas.values() if t.liberado)
        atuais = (
            list(canal.auto_categorias or []),
            list(canal.auto_agente or []),
            list(canal.auto_orientacao or []),
        )
        # O piso (13/11/2026): o que já saía sozinho continua, até a régua provar que erra.
        listas = com_o_piso(Listas(*(tuple(x) for x in atuais)), listas, reprovados(taxas))
        novas = (
            list(listas.auto_categorias),
            list(listas.auto_agente),
            list(listas.auto_orientacao),
        )
        if gravar_listas and novas != atuais:
            canal.auto_categorias, canal.auto_agente, canal.auto_orientacao = novas
            resumo["mudaram"] += 1
        if gravar_listas:
            sensiveis_soltos.extend(
                (canal, t)
                for t in taxas.values()
                if t.liberado and t.tipo in tipos_pergunta.SENSIVEIS and t.tipo not in hoje
            )
    await session.commit()
    if sensiveis_soltos:
        await _avisar_sensiveis_soltos(session, sensiveis_soltos)
    return resumo


async def _avisar_sensiveis_soltos(session: AsyncSession, soltos: list[tuple[Any, Taxa]]) -> None:
    """Um aviso no Telegram por loja e assunto sensível soltos (uma vez por dia). Nunca levanta."""
    from app.models import Integration
    from app.services.atendimento import alertas_sensiveis
    from app.services.atendimento.agente import custo

    try:
        ids = {c.integration_id for c, _ in soltos if c.integration_id}
        nomes = dict(
            (
                await session.execute(
                    select(Integration.id, Integration.name).where(Integration.id.in_(ids))
                )
            ).all()
        )
        for canal, t in soltos:
            await custo.avisar_uma_vez_por_dia(
                f"regua_sensivel:{canal.id}:{t.tipo}",
                alertas_sensiveis.aviso_da_regua(
                    t.tipo, nomes.get(canal.integration_id), t.fracao, t.decisoes
                ),
            )
    except Exception:  # noqa: BLE001 — o aviso nunca derruba a régua
        return
