"""A REVERSÃO do automático do agente (spec 8; revisão 2 de 20/10/2026).

Spec 8 (rollout): o assunto que o agente manda sozinho SAI do automático —
`auto_orientacao` (os de orientação) e `auto_agente` (os outros) — com alerta
no Telegram quando a auditoria diária acha 1 erro crítico ou a nota "enviaria
sem editar" fica abaixo de 90% em 7 dias. A nota é da rotina do Mac mini (o
Claude lê a exportação do dia, `scripts/agente_auditoria_export.py`); aqui é
a regra (`decidir`, PURA) e o ato (`reverter`, que grava o canal).

Só tira, nunca põe: o assunto volta ao automático pelo clique do dono na tela
(Canais). O `auto_categorias` do canal (o do legado) não muda — com o agente
no canal, tirar do `auto_agente` já basta para ele não sair sozinho.

Quem chama: `scripts/agente_reversao.py` (a rotina, depois da nota do dia).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.atendimento import AtendimentoCanal, AtendimentoConversa

logger = structlog.get_logger()

MOTIVO_ERRO_CRITICO = "erro crítico na auditoria"
MOTIVO_NOTA_BAIXA = "nota abaixo de 90% em 7 dias"
NOTA_MINIMA = 0.90
JANELA_DIAS = 7
# A nota baixa só conta com um mínimo de envios notados do assunto na janela
# (com 2 envios, 1 nota ruim seria 50%); o erro crítico conta sempre.
MINIMO_NOTAS = 10
TIPO_ENVIO = "envio_automatico"


@dataclass(frozen=True)
class Reversao:
    """Um assunto de uma loja que sai do automático do agente."""

    integration_id: str | None
    assunto: str
    motivo: str
    notas: int
    enviaria: int
    criticos: int
    conversas: tuple[str, ...] = field(default_factory=tuple)


def _quando(valor: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def decidir(
    registros: Iterable[Mapping[str, Any]],
    notas: Iterable[Mapping[str, Any]],
    *,
    agora: datetime,
    dias: int = JANELA_DIAS,
    nota_minima: float = NOTA_MINIMA,
    minimo: int = MINIMO_NOTAS,
) -> list[Reversao]:
    """Os assuntos que saem do automático. PURA.

    `registros` = as linhas `envio_automatico` da auditoria (`id`,
    `integration_id`, `conversa_id`, `categoria`, `enviada_em`); `notas` =
    {"id", "enviaria_sem_editar", "erro_critico"} da sessão que dá a nota.
    Por (loja, assunto), só os envios dos últimos `dias`: 1 erro crítico, ou
    `minimo`+ notas com a taxa de "enviaria" abaixo de `nota_minima`.
    """
    por_id = {str(n.get("id")): n for n in notas if isinstance(n, Mapping) and n.get("id")}
    corte = agora - timedelta(days=dias)
    grupos: dict[tuple[str | None, str], dict[str, Any]] = {}
    for r in registros:
        if not isinstance(r, Mapping) or r.get("tipo", TIPO_ENVIO) != TIPO_ENVIO:
            continue
        nota = por_id.get(str(r.get("id")))
        assunto = r.get("categoria")
        quando = _quando(r.get("enviada_em"))
        if nota is None or not assunto or (quando is not None and quando < corte):
            continue
        g = grupos.setdefault(
            (r.get("integration_id"), str(assunto)),
            {"notas": 0, "enviaria": 0, "criticos": 0, "conversas": []},
        )
        g["notas"] += 1
        g["enviaria"] += int(bool(nota.get("enviaria_sem_editar")))
        g["criticos"] += int(bool(nota.get("erro_critico")))
        if r.get("conversa_id") and r["conversa_id"] not in g["conversas"]:
            g["conversas"].append(str(r["conversa_id"]))
    saida = []
    for (loja, assunto), g in sorted(grupos.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        if g["criticos"]:
            motivo = MOTIVO_ERRO_CRITICO
        elif g["notas"] >= minimo and g["enviaria"] / g["notas"] < nota_minima:
            motivo = MOTIVO_NOTA_BAIXA
        else:
            continue
        saida.append(
            Reversao(
                integration_id=loja,
                assunto=assunto,
                motivo=motivo,
                notas=g["notas"],
                enviaria=g["enviaria"],
                criticos=g["criticos"],
                conversas=tuple(g["conversas"]),
            )
        )
    return saida


async def _canais(session: AsyncSession, conversas: Sequence[str]) -> list[AtendimentoCanal]:
    ids = []
    for c in conversas:
        try:
            ids.append(UUID(str(c)))
        except ValueError:
            continue
    if not ids:
        return []
    canal_ids = (
        await session.scalars(
            select(AtendimentoConversa.canal_id)
            .where(AtendimentoConversa.id.in_(ids), AtendimentoConversa.canal_id.is_not(None))
            .distinct()
        )
    ).all()
    if not canal_ids:
        return []
    return list(
        (
            await session.scalars(
                select(AtendimentoCanal).where(AtendimentoCanal.id.in_(canal_ids)).with_for_update()
            )
        ).all()
    )


async def reverter(
    session: AsyncSession, reversoes: Sequence[Reversao], *, gravar: bool = True
) -> list[dict[str, Any]]:
    """Tira cada assunto do `auto_orientacao` e do `auto_agente` dos canais das
    conversas dele. Só `flush` (quem chama faz commit); `gravar=False` só diz o
    que faria. Devolve uma linha por canal mexido (nada de texto de cliente).
    """
    feitas: list[dict[str, Any]] = []
    for rev in reversoes:
        for canal in await _canais(session, rev.conversas):
            orientacao = list(canal.auto_orientacao or [])
            do_agente = list(canal.auto_agente or [])
            if rev.assunto not in orientacao and rev.assunto not in do_agente:
                continue
            feitas.append(
                {
                    "canal_id": str(canal.id),
                    "assunto": rev.assunto,
                    "motivo": rev.motivo,
                    "notas": rev.notas,
                    "enviaria": rev.enviaria,
                    "criticos": rev.criticos,
                }
            )
            if gravar:
                canal.auto_orientacao = [a for a in orientacao if a != rev.assunto]
                canal.auto_agente = [a for a in do_agente if a != rev.assunto]
    if gravar and feitas:
        await session.flush()
        logger.warning("atendimento_agente_reversao", canais=len(feitas))
    return feitas


def texto_do_alerta(feitas: Sequence[Mapping[str, Any]]) -> str | None:
    """O alerta do Telegram (sem dado de cliente): o assunto, o porquê e as contas."""
    if not feitas:
        return None
    linhas = [
        f"• {f['assunto']}: {f['motivo']} ({f['enviaria']}/{f['notas']} enviaria; "
        f"{f['criticos']} crítico)"
        for f in feitas
    ]
    return (
        "🤖 <b>Atendimento</b>: o agente (Claude) saiu do automático nestes assuntos — "
        "voltam só pelo clique na tela (Canais):\n" + "\n".join(linhas)
    )
