"""Revisão do dia da aba Chamados (Vinicius, 05/11/2026).

"eu abro o chamado no dia de hoje e vejo se está ok ... amanhã esse botão teria que
sumir ... só que alguns ficam fixos, a Shopee falou que vai pagar dia 25/11".
Ele fazia isso à mão na Observação ("5-ok", "5-revisar", "25/11 - até essa data…").

- Conferido / Revisar: valem só no dia em que foram marcados.
- Fixo: vale até a data, com o motivo. Na data vira "prazo chegou hoje", depois
  "prazo venceu"; se a plataforma responde antes, cai pra revisão ("respondeu").

Nada roda à meia-noite: a marca guarda a data e `estado_da_revisao` compara com hoje
(fuso de São Paulo) a cada listagem. A marca fica gravada mesmo depois de vencer —
é o `revisao_estado` que diz o que a linha mostra.

Fechar dia (Cairo, 06/11/2026): "fica ruim ... vai parecer sempre que eu não conferi".
Quem sai antes do fim do dia dá o dia por encerrado e ele fica verde, mas os pendentes
seguem na conta: "vai ser fechado mas vai aparecer ali 6 pendentes". Por isso a foto
não congela; fechar só registra quem e quando.

Encerrar (Vinicius, 11/11/2026): "eles vão acompanhar tudo, resolver e colocar como
encerrar. Daí eu confiro e encerro". A equipe não conclui mais o chamado (o
`/resolver` barra quem tem equipe); marca Encerrar e a marca fica até alguém
concluir ou trocar a marca, sem sumir na virada do dia.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoRevisao, ChamadoRevisaoDia
from app.services.chamados import SAO_PAULO

CONFERIDO = "conferido"
REVISAR = "revisar"
FIXO = "fixo"
ENCERRAR = "encerrar"
# Só no registro (`chamado_revisoes`): a pessoa tirou a marca.
LIMPO = "limpo"

# O que a linha mostra hoje (`ChamadoOut.revisao_estado`).
ESTADO_FALTA = "falta"
ESTADO_PRAZO_HOJE = "prazo_hoje"
ESTADO_PRAZO_VENCIDO = "prazo_vencido"
ESTADO_RESPONDEU = "respondeu"
PRECISA_VER = (ESTADO_PRAZO_HOJE, ESTADO_PRAZO_VENCIDO, ESTADO_RESPONDEU)


class LinhaDoDia(Protocol):
    id: UUID
    status_aba: str | None
    revisao_estado: str


def hoje_sp() -> date:
    return datetime.now(SAO_PAULO).date()


def estado_da_revisao(
    ch: Chamado,
    *,
    ultima_resposta_at: datetime | None,
    ultima_resposta_direcao: str | None,
    hoje: date,
) -> str:
    """Prazo vence antes de "respondeu": chegou a data, a linha volta de qualquer jeito.
    Encerrar não vence: espera quem conclui."""
    if ch.revisao_tipo == ENCERRAR:
        return ENCERRAR
    if ch.revisao_tipo in (CONFERIDO, REVISAR):
        return ch.revisao_tipo if ch.revisao_dia == hoje else ESTADO_FALTA
    if ch.revisao_tipo != FIXO or ch.revisao_ate is None:
        return ESTADO_FALTA
    if ch.revisao_ate < hoje:
        return ESTADO_PRAZO_VENCIDO
    if ch.revisao_ate == hoje:
        return ESTADO_PRAZO_HOJE
    if (
        ultima_resposta_direcao == "recebida"
        and ultima_resposta_at is not None
        and ch.revisao_at is not None
        and ultima_resposta_at > ch.revisao_at
    ):
        return ESTADO_RESPONDEU
    return FIXO


def marcar(
    ch: Chamado,
    *,
    tipo: str | None,
    ate: date | None,
    motivo: str | None,
    autor_nome: str | None,
    autor_id: UUID | None,
    hoje: date,
) -> ChamadoRevisao:
    """Grava a marca no chamado e devolve o registro pra pessoa que chama adicionar à
    sessão. `tipo` nulo tira a marca. Data e motivo só existem no Fixo."""
    fixo = tipo == FIXO
    ch.revisao_tipo = tipo
    ch.revisao_dia = hoje if tipo else None
    ch.revisao_ate = ate if fixo else None
    ch.revisao_motivo = motivo if fixo else None
    ch.revisao_por_nome = autor_nome if tipo else None
    ch.revisao_at = datetime.now(UTC) if tipo else None
    return ChamadoRevisao(
        chamado_id=ch.id,
        tipo=tipo or LIMPO,
        dia=hoje,
        ate=ate if fixo else None,
        motivo=motivo if fixo else None,
        autor_nome=autor_nome,
        autor_id=autor_id,
    )


def resumir_dia(linhas: Iterable[LinhaDoDia]) -> dict:
    """Como os chamados abertos estão agora, no formato da foto do dia."""
    resumo: dict = {
        CONFERIDO: 0,
        REVISAR: 0,
        FIXO: 0,
        ENCERRAR: 0,
        ESTADO_FALTA: 0,
        "prazos": 0,
        "por_status": {},
        "faltaram": [],
    }
    for linha in linhas:
        estado = linha.revisao_estado
        if estado in PRECISA_VER:
            resumo["prazos"] += 1
            resumo["faltaram"].append(str(linha.id))
            continue
        resumo[estado] += 1
        if estado == ESTADO_FALTA:
            status = linha.status_aba or ""
            resumo["por_status"][status] = resumo["por_status"].get(status, 0) + 1
            resumo["faltaram"].append(str(linha.id))
    return resumo


def fechar_dia(foto: ChamadoRevisaoDia, *, autor_nome: str, autor_id: UUID) -> None:
    """Quem fechou primeiro fica: clicar de novo não troca o nome nem a hora."""
    if foto.fechado_at is not None:
        return
    foto.fechado_at = datetime.now(UTC)
    foto.fechado_por_nome = autor_nome
    foto.fechado_por_id = autor_id


def reabrir_dia(foto: ChamadoRevisaoDia) -> None:
    foto.fechado_at = None
    foto.fechado_por_nome = None
    foto.fechado_por_id = None


async def gravar_foto(session: AsyncSession, dia: date, resumo: dict) -> None:
    """Regrava a foto do dia: a última do dia é a que vale. Não toca no `fechado_*`."""
    valores = {
        "conferido": resumo[CONFERIDO],
        "revisar": resumo[REVISAR],
        "fixo": resumo[FIXO],
        "encerrar": resumo[ENCERRAR],
        "falta": resumo[ESTADO_FALTA],
        "prazos": resumo["prazos"],
        "por_status": resumo["por_status"],
        "faltaram": resumo["faltaram"],
        "updated_at": datetime.now(UTC),
    }
    await session.execute(
        insert(ChamadoRevisaoDia)
        .values(dia=dia, **valores)
        .on_conflict_do_update(index_elements=[ChamadoRevisaoDia.dia], set_=valores)
    )
