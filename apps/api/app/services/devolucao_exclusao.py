"""Excluir um lançamento de devolução DESFAZ o que o DaVinci fez sozinho por ele.

Caso 300978 (26/10/2026, Shopee Inova, kit de 5 malas b045): às 10:07 a
operadora lançou 6 linhas "Extraviado / Não recebido" (a b045.12 duplicada) e o
DaVinci, sozinho, mudou o pedido no Bling de Aguardando Devolução para
Problemas, criou 6 prejuízos Extraviado na tela Reembolso e abriu 1 chamado. Às
10:10 ela excluiu as 6 linhas — e ficou tudo para trás: o pedido sumiu da aba
Acompanhamento (que lista só 83957), R$ 441,90 de prejuízo fantasma e o chamado
aberto. Caso anterior idêntico: 298822 (Perdimento tirou o pedido da busca).

Eduardo: "se mudou para Problemas errado e ele excluiu, volta a mudança de
situação" — e o que o DaVinci criou sozinho, ele mesmo apaga. A regra geral é
"só desfaz o que ninguém mexeu depois": o que uma pessoa (ou a plataforma)
tocou fica, e o DaVinci avisa no sino/Telegram.

Três partes, cada uma usada por quem precisa:
* prejuízo automático (Refund) — as DUAS lixeiras (aba Devoluções e chamado),
  via `devolution_delete.excluir_lancamento`, e o PATCH que tira a linha de
  Extraviado/Sucata (`desfazer_refund_auto`, mesma regra nos dois lugares);
* situação do pedido no Bling — só a lixeira da aba Devoluções (a do chamado
  já pede a situação à pessoa);
* chamado automático — só a lixeira da aba Devoluções (a do chamado o apaga).

Nada aqui commita: o caller é dono da transação.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import desc, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    Devolution,
    MailMessageMeta,
    MargemAudit,
    Refund,
    SituacaoBling,
)
from app.models.enums import AlertSeverity, AlertType
from app.services import devolution_stock_return as dsr
from app.services import espelho_situacao
from app.services.alerts import emit_alert
from app.services.margem_audit import record_margem_audit

logger = structlog.get_logger()

# Condições que criam o prejuízo automático na tela Reembolso (_maybe_create_refund
# no router): Extraviado/Sucata com prejuízo = custo do produto e reembolso 0;
# Manutenção com prejuízo = custo do conserto e reembolso = −custo (débito).
REFUND_CONDICOES = frozenset({"Extraviado", "Sucata", "Manutenção"})
# Origens que o FLUXO de devolução grava em margem_audit ao mexer na situação
# (apply_order_situacao). 'devolucoes_manual' é uma PESSOA escolhendo a
# situação na aba Lançamentos — não é o fluxo, e quebra a sequência.
ORIGENS_SITUACAO_DO_FLUXO = frozenset({"devolucao"})
# Origem da volta da situação ao excluir — também quebra a sequência (uma
# volta já feita não é desfeita de novo).
ORIGEM_EXCLUSAO = "devolucao_exclusao"

_NOMES_SITUACAO = {
    dsr.SITUACAO_AGUARDANDO_DEVOLUCAO: "Aguardando Devolução",
    dsr.SITUACAO_EXTRAVIADO: "Problemas",
    dsr.SITUACAO_PERDIMENTO: "Perdimento",
    dsr.SITUACAO_MANUTENCAO: "Manutenção",
    dsr.SITUACAO_RESOLVIDO: "Resolvido",
}
_CENTAVO = 0.005


def _brl(v: float | None) -> str:
    return f"R$ {float(v or 0):.2f}".replace(".", ",")


async def _alerta(
    session: AsyncSession,
    actor_id: UUID | None,
    *,
    titulo: str,
    mensagem: str,
    dedupe_key: str,
    payload: dict,
) -> None:
    """Sino + Telegram de quem excluiu. Best-effort e num savepoint: um erro
    aqui nunca derruba a exclusão (nem envenena a transação dela)."""
    if actor_id is None:
        return
    try:
        async with session.begin_nested():
            await emit_alert(
                session,
                user_id=actor_id,
                type=AlertType.GENERIC,
                severity=AlertSeverity.WARNING,
                title=titulo,
                message=mensagem,
                dedupe_key=dedupe_key,
                payload=payload,
            )
    except Exception:  # noqa: BLE001
        logger.warning("devolucao_exclusao_alerta_falhou", dedupe_key=dedupe_key)


# ── Prejuízo automático (Refund) ───────────────────────────────────────────

# O POST manual da tela Reembolso só passou a gravar `created_by` com a 0133
# (09/06/2026, deploy no mesmo dia). Refund sem `created_by` de antes disso pode
# ter sido lançado à mão — sem o vínculo exato, ele nunca é apagado sozinho.
_CORTE_CREATED_BY = datetime(2026, 6, 10, tzinfo=ZoneInfo("America/Sao_Paulo"))


@dataclass
class RefundAchado:
    """O que `achar_refund_auto` encontrou. `exato` = vínculo pelo created_at
    (é com certeza o desta linha); `duvida` = não dá pra apagar sem risco (o
    motivo vai no aviso) — `refund` pode vir junto (o suspeito) ou None."""

    refund: Refund | None
    exato: bool = False
    duvida: str | None = None


def _pedido_conds(model: Any, row: Devolution) -> list | None:
    if (row.pedido_bling or "").strip():
        return [model.pedido_bling == row.pedido_bling]
    if (row.pedido_marketplace or "").strip():
        return [model.pedido_marketplace == row.pedido_marketplace]
    return None


def _custo_do_refund(row: Devolution, tipo: str) -> float:
    """O prejuízo que o _maybe_create_refund grava: custo do produto em
    Extraviado/Sucata, custo do conserto em Manutenção."""
    if tipo == "Manutenção":
        return float(row.custo_manutencao or 0)
    return float(row.custo_produto or 0)


def _bate_custo(refund: Refund, tipo: str, custo: float) -> bool:
    """O refund tem a cara do automático de uma linha com esse custo. Na
    Manutenção o prejuízo fica congelado no custo da criação e o reembolso
    acompanha o custo do conserto (−custo, _sync_manutencao_to_refund)."""
    if abs(float(refund.prejuizo or 0) - custo) < _CENTAVO:
        return True
    return tipo == "Manutenção" and abs(float(refund.reembolso or 0) + custo) < _CENTAVO


def _linha_do_tipo(d: Devolution, tipo: str) -> bool:
    """A linha pode ter um prejuízo automático desse tipo: está na condição —
    ou, na Manutenção, já passou por ela (o débito do conserto fica)."""
    cond = (d.condicao_produto or "").strip()
    if tipo == "Manutenção":
        return cond == "Manutenção" or bool(d.manutencao)
    return cond == tipo


def valor_do_refund(refund: Refund) -> float:
    """O valor que o prejuízo representa (resumo/toast). Manutenção: o débito
    do conserto — o prejuízo pode ter nascido 0 (custo informado depois) ou com
    o custo antigo, enquanto o reembolso acompanha o custo atual."""
    prejuizo = float(refund.prejuizo or 0)
    if refund.tipo == "Manutenção":
        return max(prejuizo, abs(float(refund.reembolso or 0)))
    return prejuizo


def motivo_refund_mexido(refund: Refund, row: Devolution) -> str | None:
    """None = ninguém mexeu no prejuízo automático desde que o DaVinci o criou;
    senão, o que mudou (vai no aviso). Extraviado/Sucata nascem com reembolso 0;
    Manutenção nasce com reembolso = −custo do conserto (_sync_manutencao_to_refund).
    Conferido = alguém da agência já olhou — fica. Protocolo, observação e
    operação só uma pessoa preenche (na tela Reembolso) — também fica."""
    if refund.conferido:
        return "já foi conferido na tela Reembolso"
    protocolo = (refund.chamado or "").strip()
    if protocolo or (refund.chamado_url or "").strip():
        return (
            f"já tem chamado {protocolo} anotado na tela Reembolso"
            if protocolo
            else "já tem link de chamado anotado na tela Reembolso"
        )
    if refund.chamado_resolvido:
        return "o chamado dele já foi marcado como resolvido na tela Reembolso"
    if (refund.observacao or "").strip() or (refund.operacao or "").strip():
        return "já tem observação/operação anotada na tela Reembolso"
    reembolso = float(refund.reembolso or 0)
    if refund.tipo == "Manutenção":
        # Sem olhar updated_at: o sync do custo do conserto mexe nele sozinho.
        esperado = -float(row.custo_manutencao or 0)
        if abs(reembolso - esperado) >= _CENTAVO:
            return (
                f"o reembolso ({_brl(reembolso)}) não é mais só o débito do conserto "
                f"({_brl(esperado)})"
            )
        return None
    if abs(reembolso) >= _CENTAVO:
        return f"já tem {_brl(reembolso)} de reembolso lançado"
    # Nada automático faz UPDATE em Extraviado/Sucata, e um UPDATE na mesma
    # transação do INSERT mantém updated_at == created_at (now() = início da
    # transação): mudou = alguém editou (data, prejuízo, plataforma…).
    if (
        refund.updated_at is not None
        and refund.created_at is not None
        and refund.updated_at > refund.created_at
    ):
        return "foi editado na tela Reembolso"
    return None


async def achar_refund_auto(
    session: AsyncSession,
    row: Devolution,
    tipo: str,
    *,
    custo: float | None = None,
    travar: bool = True,
    ignorar: frozenset[UUID] = frozenset(),
) -> RefundAchado:
    """O prejuízo que o DaVinci criou para ESTA linha.

    1. Vínculo exato, sem migration: o refund do POST /api/devolutions nasce no
       MESMO INSERT/commit da linha, e `created_at` é server_default now() — o
       início da transação —, então refund.created_at == devolution.created_at.
    2. Sem vínculo exato, o refund nasceu num PATCH (linha que virou Extraviado
       depois). Só entra quem tem a cara do desta linha — prejuízo igual ao
       custo (`custo`, padrão o atual da linha) —, entre os criados depois dela
       e que não são de outra linha. E só quando sobra refund para ela: as
       outras linhas do pedido no mesmo tipo sem vínculo exato (as de mesmo
       custo, em especial) têm direito ao seu. Na dúvida, nada é apagado
       (`duvida`). Entre os de mesmo custo (kit com a mesma mala duas vezes),
       leva primeiro o que ninguém mexeu — o mexido fica para a outra linha.

    Nunca: refund de outro pedido, de outra conta, de outro tipo ou criado à mão
    na tela Reembolso (o POST de lá grava `created_by`; o automático, não; e de
    antes de existir o `created_by` não dá pra saber — vira dúvida).

    `travar`: SELECT … FOR UPDATE com o estado do banco (um PATCH da agência no
    mesmo refund termina antes e a decisão já vê o valor dele).

    `ignorar`: refunds já decididos (o exato que ficou porque alguém mexeu) —
    a busca segue para os outros automáticos da linha (os de PATCH)."""
    conds = _pedido_conds(Refund, row)
    if conds is None:
        return RefundAchado(None)
    q = (
        select(Refund)
        .where(
            *conds,
            Refund.tipo == tipo,
            func.btrim(Refund.conta) == (row.conta or "").strip(),
            Refund.created_by.is_(None),
        )
        .order_by(desc(Refund.created_at))
        .execution_options(populate_existing=True)
    )
    if travar:
        q = q.with_for_update()
    refunds = [r for r in (await session.execute(q)).scalars().all() if r.id not in ignorar]
    if not refunds:
        return RefundAchado(None)
    exato = next((r for r in refunds if r.created_at == row.created_at), None)
    if exato is not None:
        return RefundAchado(exato, exato=True)

    dconds = _pedido_conds(Devolution, row) or []
    outras = list(
        (
            await session.execute(
                select(Devolution).where(
                    *dconds,
                    func.btrim(Devolution.conta) == (row.conta or "").strip(),
                    Devolution.id != row.id,
                )
            )
        )
        .scalars()
        .all()
    )
    # Os que nasceram junto com OUTRA linha do pedido são dela.
    vinculados = {d.created_at for d in outras}
    criados_em = {r.created_at for r in refunds}
    # Outras linhas no mesmo tipo que ainda esperam o seu refund de PATCH.
    outras_sem_vinculo = [
        d for d in outras if _linha_do_tipo(d, tipo) and d.created_at not in criados_em
    ]
    custo = _custo_do_refund(row, tipo) if custo is None else float(custo)
    candidatos = [
        r
        for r in refunds
        if r.created_at not in vinculados
        and (row.created_at is None or r.created_at >= row.created_at)
    ]
    novos = [r for r in candidatos if r.created_at >= _CORTE_CREATED_BY]
    mesmo_custo = [r for r in novos if _bate_custo(r, tipo, custo)]
    sem_vinculo = [
        r for r in refunds if r.created_at not in vinculados and r.created_at >= _CORTE_CREATED_BY
    ]
    sobra = len(sem_vinculo) > len(outras_sem_vinculo)
    irmas_mesmo_custo = sum(
        1 for d in outras_sem_vinculo if abs(_custo_do_refund(d, tipo) - custo) < _CENTAVO
    )
    if mesmo_custo and sobra and len(mesmo_custo) > irmas_mesmo_custo:
        # Intercambiáveis: leva o que ninguém mexeu; o mexido fica para a irmã.
        refund = next(
            (r for r in mesmo_custo if motivo_refund_mexido(r, row) is None), mesmo_custo[0]
        )
        return RefundAchado(refund)
    antigo = next(
        (
            r
            for r in candidatos
            if r.created_at < _CORTE_CREATED_BY and _bate_custo(r, tipo, custo)
        ),
        None,
    )
    if antigo is not None:
        return RefundAchado(
            antigo,
            duvida=(
                "foi lançado antes de o DaVinci registrar quem cria o prejuízo — pode "
                "ter sido à mão"
            ),
        )
    if novos and sobra:
        return RefundAchado(
            None,
            duvida=(
                f"não consegui saber qual prejuízo {tipo} do pedido é o desta linha "
                f"(nenhum sobrando com o custo dela, {_brl(custo)})"
            ),
        )
    return RefundAchado(None)


async def desfazer_refund_auto(
    session: AsyncSession,
    row: Devolution,
    tipo: str,
    actor_id: UUID | None,
    *,
    exclusao: bool,
    avisar_duvida: bool = True,
) -> dict | None:
    """Apaga o prejuízo automático da linha se ninguém mexeu nele; se mexeram
    (ou não dá pra ter certeza de qual é), mantém e avisa (sino/Telegram) pra
    revisar. `exclusao` = a linha está sendo excluída (senão: o PATCH tirou a
    linha de Extraviado/Sucata — o produto apareceu, Eduardo 15/09, caso
    293843). `avisar_duvida=False`: a dúvida sem refund certo vira None (busca
    secundária — o débito de Manutenção de uma linha que já saiu dela). Devolve
    None (não achou) ou {acao: apagado|mantido, tipo, prejuizo, reembolso,
    valor, motivo, extras?} — `extras`: os outros automáticos do mesmo tipo da
    linha (de PATCH), decididos quando o exato ficou. NÃO commita."""
    achado = await achar_refund_auto(session, row, tipo)
    refund = achado.refund
    pedido = row.pedido_bling or row.pedido_marketplace or ""
    if refund is None:
        if achado.duvida is None or not avisar_duvida:
            return None
        await _alerta(
            session,
            actor_id,
            titulo=f"Pedido {pedido}: prejuízo {tipo} pode ter ficado — revisar",
            mensagem=(
                f"O lançamento de devolução do pedido {pedido} ({row.sku or 'sem SKU'}) "
                + ("foi excluído" if exclusao else f"saiu de {tipo}")
                + f", mas {achado.duvida}, então nada foi apagado. Confira na tela de "
                "Reembolso se sobrou prejuízo desta linha."
            ),
            dedupe_key=f"devolucao_exclusao_refund_duvida:{row.id}:{tipo}",
            payload={"devolution_id": str(row.id), "pedido_bling": row.pedido_bling},
        )
        logger.info(
            "refund_auto_duvida", devolution_id=str(row.id), pedido_bling=row.pedido_bling,
            tipo=tipo, motivo=achado.duvida, exclusao=exclusao,
        )
        return {
            "refund_id": None, "tipo": tipo, "prejuizo": None, "reembolso": None,
            "valor": None, "conferido": False, "acao": "mantido", "motivo": achado.duvida,
        }
    if achado.duvida is not None and not avisar_duvida:
        return None
    out = await _apagar_ou_manter(session, row, tipo, actor_id, achado, exclusao=exclusao)
    if achado.exato and out["acao"] == "mantido":
        # O exato ficou (alguém mexeu): a linha pode ter OUTRO automático do
        # mesmo tipo, criado num PATCH (Extraviado → Entregue → Extraviado de
        # novo cria um segundo). Sem isso ele ficava fantasma.
        extras: list[dict] = []
        vistos = {refund.id}
        for _ in range(10):
            extra = await achar_refund_auto(session, row, tipo, ignorar=frozenset(vistos))
            if extra.refund is None or extra.duvida is not None:
                break
            vistos.add(extra.refund.id)
            extras.append(
                await _apagar_ou_manter(session, row, tipo, actor_id, extra, exclusao=exclusao)
            )
        if extras:
            out["extras"] = extras
    return out


async def _apagar_ou_manter(
    session: AsyncSession,
    row: Devolution,
    tipo: str,
    actor_id: UUID | None,
    achado: RefundAchado,
    *,
    exclusao: bool,
) -> dict:
    """Apaga o refund achado se ninguém mexeu; senão mantém e avisa."""
    refund = achado.refund
    if refund is None:  # pragma: no cover — quem chama já conferiu
        raise ValueError("refund ausente")
    pedido = row.pedido_bling or row.pedido_marketplace or ""
    info = {
        "refund_id": str(refund.id),
        "tipo": tipo,
        "prejuizo": float(refund.prejuizo or 0),
        "reembolso": float(refund.reembolso or 0),
        "valor": valor_do_refund(refund),
        "conferido": bool(refund.conferido),
    }
    motivo = achado.duvida or motivo_refund_mexido(refund, row)
    if motivo is None:
        await session.delete(refund)
        logger.info(
            "refund_auto_removed",
            refund_id=info["refund_id"],
            pedido_bling=row.pedido_bling,
            tipo=tipo,
            nova_condicao=None if exclusao else row.condicao_produto,
            exclusao=exclusao,
            exato=achado.exato,
        )
        return {**info, "acao": "apagado", "motivo": None}

    # Sem o vínculo exato não dá pra afirmar que o refund é "desta linha".
    qual = (
        f"o prejuízo {tipo} que o DaVinci criou para ele"
        if achado.exato
        else f"um prejuízo {tipo} automático do pedido de {_brl(info['valor'])}"
    )
    if exclusao:
        titulo = f"Pedido {pedido}: prejuízo {tipo} ficou — revisar"
        mensagem = (
            f"O lançamento de devolução do pedido {pedido} ({row.sku or 'sem SKU'}) foi "
            f"excluído, mas {qual} {motivo}, então ficou. Confira na tela de Reembolso "
            "se ele ainda vale."
        )
        chave = f"devolucao_exclusao_refund:{refund.id}"
    else:
        titulo = (
            f"Pedido {pedido}: reembolso {tipo} já conferido — revisar"
            if refund.conferido
            else f"Pedido {pedido}: reembolso {tipo} já tem valor — revisar"
        )
        mensagem = (
            f"A devolução do pedido {pedido} saiu de {tipo} para "
            f"{row.condicao_produto or '—'} (o produto apareceu), mas "
            + (f"o reembolso {tipo}" if achado.exato else qual)
            + f" {motivo}. Confira na tela de Reembolso se ele ainda vale."
        )
        chave = f"devolucao_refund_revisar:{refund.id}"
    await _alerta(
        session,
        actor_id,
        titulo=titulo,
        mensagem=mensagem,
        dedupe_key=chave,
        payload={"refund_id": info["refund_id"], "pedido_bling": row.pedido_bling},
    )
    logger.info(
        "refund_auto_mantido", refund_id=info["refund_id"], pedido_bling=row.pedido_bling,
        tipo=tipo, motivo=motivo, exclusao=exclusao, exato=achado.exato,
    )
    return {**info, "acao": "mantido", "motivo": motivo}


def resumo_prejuizos(resultados: list[dict | None]) -> dict:
    """Campos da resposta das duas lixeiras: prejuízos automáticos apagados
    (qtd e total — na Manutenção, o débito do conserto) e os mantidos (com o
    motivo — alguém mexeu, ou não deu pra saber qual era). Os `extras` de um
    resultado (outros automáticos da mesma linha) entram como itens próprios."""
    todos: list[dict] = []
    for r in resultados:
        if r:
            todos.append(r)
            todos.extend(x for x in (r.get("extras") or []) if x)
    apagados = [r for r in todos if r.get("acao") == "apagado"]
    mantidos = [r for r in todos if r.get("acao") in ("mantido", "erro")]
    return {
        "prejuizos_apagados": {
            "qtd": len(apagados),
            "total": round(
                sum(float(r.get("valor", r.get("prejuizo")) or 0) for r in apagados), 2
            ),
        },
        "prejuizos_mantidos": [
            {
                "tipo": r.get("tipo"),
                "prejuizo": r.get("prejuizo"),
                "reembolso": r.get("reembolso"),
                "valor": r.get("valor"),
                "motivo": r.get("motivo"),
            }
            for r in mantidos
        ],
    }


# ── Situação do pedido ─────────────────────────────────────────────────────

# Situações que só o fluxo de devolução põe (Problemas, Perdimento, Manutenção):
# o pedido parado nelas sem nenhuma linha que conte é sinal de sobra.
SITUACOES_DO_FLUXO = frozenset(
    {dsr.SITUACAO_EXTRAVIADO, dsr.SITUACAO_PERDIMENTO, dsr.SITUACAO_MANUTENCAO}
)
# Atraso tolerado do espelho (webhook/sync do Bling) em relação à última
# mudança do fluxo: dentro disso, o espelho ainda na situação ANTERIOR a ela é
# só atraso; depois, é de alguém.
_ATRASO_DO_ESPELHO = timedelta(minutes=10)


async def linhas_do_pedido(session: AsyncSession, pedido: str | None) -> list[Devolution]:
    """Todas as linhas do pedido, com o estado do BANCO: a sessão não expira no
    commit, e uma linha lida antes (e trocada por outra pessoa/worker no meio)
    viria velha do identity map — o recálculo usaria estado antigo."""
    pedido = (pedido or "").strip()
    if not pedido:
        return []
    return list(
        (
            await session.execute(
                select(Devolution)
                .where(Devolution.pedido_bling == pedido)
                .order_by(Devolution.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def linhas_que_contam(rows: list[Devolution]) -> list[Devolution]:
    """As linhas que entram na conta da situação (dsr._order_situacao_target):
    sem as neutras (Entregue / Não devolvido) e sem as pendências antigas do
    backfill. Nenhuma sobrando = o pedido volta para antes da devolução."""
    return [
        r
        for r in rows
        if (r.condicao_produto or "").strip() not in dsr._NEUTRAL_CONDICOES
        and not dsr._pendente_do_backfill(r)
    ]


async def nome_situacao(session: AsyncSession, sid: str | int | None) -> str | None:
    if sid is None or not str(sid).isdigit():
        return None
    nome = (
        await session.execute(select(SituacaoBling.nome).where(SituacaoBling.id == int(sid)))
    ).scalar_one_or_none()
    return nome or _NOMES_SITUACAO.get(int(sid)) or str(sid)


async def _sequencia_do_fluxo(session: AsyncSession, pedido: str) -> tuple[list, bool]:
    """Mudanças de situação do pedido em margem_audit, da mais nova para a mais
    velha: devolve a sequência FINAL feita pelo fluxo de devolução (em ordem
    cronológica) e se depois dela alguém mudou por outro caminho (pessoa na aba
    Lançamentos ou Chamados, Margem, robôs, uma volta já feita).

    A sequência é só o ÚLTIMO episódio: se uma mudança do fluxo parte de uma
    situação que as anteriores nunca tocaram, o pedido mudou por fora do DaVinci
    no meio (Logística, painel do Bling — sem trilha) e o que veio antes é de
    outra devolução — voltaria para a situação de meses atrás."""
    audits = (
        (
            await session.execute(
                select(MargemAudit)
                .where(MargemAudit.pedido_bling == pedido, MargemAudit.acao == "situacao")
                .order_by(desc(MargemAudit.created_at))
            )
        )
        .scalars()
        .all()
    )
    seq: list[MargemAudit] = []
    quebra: MargemAudit | None = None
    for a in audits:
        if a.origem not in ORIGENS_SITUACAO_DO_FLUXO:
            quebra = a
            break
        seq.append(a)
    # A última mudança é a volta de uma exclusão anterior: nada a desfazer.
    mudou_depois = (
        not seq
        and quebra is not None
        and quebra.origem != ORIGEM_EXCLUSAO
        and any(a.origem in ORIGENS_SITUACAO_DO_FLUXO for a in audits)
    )
    cronologica = list(reversed(seq))
    inicio = 0
    tocadas: set[str] = set()
    for i, a in enumerate(cronologica):
        antigo, novo = (a.valor_antigo or "").strip(), (a.valor_novo or "").strip()
        # O conjunto acumulado (não só o valor_novo anterior) tolera o espelho
        # atrasado dentro do mesmo episódio (83957→83960 e depois 83957→83956).
        if i and antigo not in tocadas:
            inicio, tocadas = i, set()
        tocadas |= {antigo, novo}
    return cronologica[inicio:], mudou_depois


async def _espelho_do_pedido(session: AsyncSession, pedido: str):
    # `numero` se repete entre anos — o pedido mais recente.
    return (
        await session.execute(
            select(BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero == pedido, BlingOrder.bling_id.is_not(None))
            .order_by(BlingOrder.data.desc().nullslast())
            .limit(1)
        )
    ).first()


def motivo_do_bling(exc: httpx.HTTPStatusError) -> str:
    """O motivo de uma recusa do Bling, em texto curto: `error.fields[].msg`,
    `error.description` ou `error.message` do JSON; senão o corpo cru; senão
    o texto do httpx."""
    corpo = ""
    try:
        corpo = (exc.response.text or "").strip()
        erro = (exc.response.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001 — corpo que não é JSON
        erro = {}
    partes: list[str] = []
    if isinstance(erro, dict):
        for f in erro.get("fields") or []:
            msg = (f.get("msg") if isinstance(f, dict) else None) or ""
            if msg.strip():
                partes.append(msg.strip())
        if not partes:
            for chave in ("description", "message"):
                v = erro.get(chave)
                if isinstance(v, str) and v.strip():
                    partes.append(v.strip())
                    break
    texto = "; ".join(partes) or corpo or str(exc)
    return texto[:300]


async def _situacao_real(client: Any, bling_id: int | None) -> str | None:
    """A situação do pedido no Bling agora (GET do pedido). None = não deu
    para ler (sem cliente, Bling fora) — quem chama fica com o espelho."""
    if client is None or bling_id is None:
        return None
    try:
        pedido = await client.get_order(int(bling_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("devolucao_exclusao_situacao_real_falhou", bling_id=bling_id, erro=str(exc))
        return None
    sit = (pedido or {}).get("situacao")
    sid = sit.get("id") if isinstance(sit, dict) else sit
    return str(sid) if sid is not None and str(sid).isdigit() else None


async def reverter_situacao(
    session: AsyncSession, pedido: str, *, actor_id: UUID | None
) -> dict:
    """Sem nenhuma linha que conte no pedido: volta a situação para a que ele
    tinha ANTES de o fluxo de devolução mexer. Só reverte se a situação atual
    (espelho) ainda for a última que o fluxo pôs — ou a anterior a ela, se a
    mudança é de minutos atrás (o espelho de antes de 26/10 só mudava com o
    sync) — e se ninguém mudou por outro caminho depois. Espelho fora disso:
    confere a situação real no Bling antes de desistir (webhook perdido). Aplica com a regra do
    desvio do endpoint manual, atualiza o espelho e grava margem_audit (origem
    'devolucao_exclusao', mudado_por = quem excluiu). Bling fora/recusando: não
    levanta — devolve `falhou` com a situação que devia ser (e avisa no sino).
    NÃO commita.

    Devolve {status: revertida|ja_estava|sem_historico|mudada_depois|falhou, ...};
    `atual`/`nome_atual` = a situação do espelho, quando há."""
    order_row = await _espelho_do_pedido(session, pedido)
    atual = ((order_row.situacao if order_row else None) or "").strip()
    sobre_atual = {"atual": atual or None, "nome_atual": await nome_situacao(session, atual)}
    seq, mudou_depois = await _sequencia_do_fluxo(session, pedido)
    if mudou_depois:
        return {"status": "mudada_depois", **sobre_atual}
    if not seq:
        return {"status": "sem_historico", **sobre_atual}
    alvo = (seq[0].valor_antigo or "").strip()
    ultima = (seq[-1].valor_novo or "").strip()
    if not alvo.isdigit() or order_row is None:
        return {"status": "sem_historico", **sobre_atual}
    nome_alvo = await nome_situacao(session, alvo)
    # O que o espelho pode mostrar sem ninguém ter mexido: a última situação do
    # fluxo; a anterior a ela só enquanto o sync pode estar atrasado (ou num
    # passo só, em que ela é o próprio alvo). Um passo do MEIO da cadeia no
    # espelho = alguém pôs o pedido lá (ex.: aba Chamados, painel do Bling).
    possiveis = {ultima}
    criada = seq[-1].created_at
    recente = criada is not None and datetime.now(UTC) - criada <= _ATRASO_DO_ESPELHO
    if len(seq) == 1 or recente:
        possiveis.add((seq[-1].valor_antigo or "").strip())
    if ultima == alvo:
        possiveis.add(alvo)
    client = await dsr._get_bling_client(session)
    if atual and atual not in possiveis:
        # O espelho pode estar atrasado (webhook perdido — o Bling V3 perde com
        # frequência — ou o recálculo de uma exclusão anterior antes do sync):
        # antes de dizer "alguém mudou", confere a situação REAL no Bling.
        real = await _situacao_real(client, order_row.bling_id)
        if real is None or real not in possiveis:
            # Mudada no painel do Bling (o sync trouxe pro espelho) — é de alguém.
            if real is not None and real != atual:
                sobre_atual = {"atual": real, "nome_atual": await nome_situacao(session, real)}
            return {
                "status": "mudada_depois", **sobre_atual, "para": int(alvo), "nome": nome_alvo
            }
        atual = real
        sobre_atual = {"atual": real, "nome_atual": await nome_situacao(session, real)}
    if ultima == alvo:
        return {"status": "ja_estava", **sobre_atual, "para": int(alvo), "nome": nome_alvo}

    base = {"de": ultima, "nome_de": await nome_situacao(session, ultima),
            "para": int(alvo), "nome": nome_alvo}
    erro: str | None = None
    via_desvio = False
    if client is None:
        erro = "nenhuma integração Bling encontrada"
    else:
        try:
            via_desvio = await dsr.mudar_situacao_com_desvio(
                client, int(order_row.bling_id), int(alvo), ultima
            )
        except httpx.HTTPStatusError as exc:
            # O motivo que o Bling deu (ex.: pedido com nota fiscal), não a
            # mensagem genérica do httpx com URL interna e link do MDN.
            erro = motivo_do_bling(exc)
        except Exception as exc:  # noqa: BLE001 — a exclusão já valeu
            erro = str(exc)[:300] or exc.__class__.__name__
    if erro is not None:
        logger.warning(
            "devolucao_exclusao_situacao_falhou", pedido_bling=pedido, para=alvo, erro=erro
        )
        await _alerta(
            session,
            actor_id,
            titulo=f"Pedido {pedido}: volte a situação no Bling para {nome_alvo} (manual)",
            mensagem=(
                f"As linhas de devolução do pedido {pedido} foram excluídas, mas o Bling não "
                f"aceitou voltar a situação de {base['nome_de']} para {nome_alvo}. Abra o "
                f"pedido no Bling e mude para {nome_alvo} na mão. ({erro})"
            ),
            dedupe_key=f"devolucao_exclusao_situacao:{pedido}:{alvo}",
            payload={"pedido_bling": pedido, "situacao_alvo": int(alvo)},
        )
        return {**base, "status": "falhou", "erro": erro}

    # Espelho na hora: a aba Acompanhamento (só 83957) lê daqui — com a data
    # de entrada em Aguardando Devolução que o sync carimbaria.
    await espelho_situacao.adiantar_situacao(session, order_row.bling_id, alvo)
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=pedido,
        bling_id=order_row.bling_id,
        valor_antigo=ultima,
        valor_novo=alvo,
        origem=ORIGEM_EXCLUSAO,
        mudado_por=actor_id,
    )
    logger.info(
        "devolucao_exclusao_situacao_revertida",
        pedido_bling=pedido, de=ultima, para=alvo, via_desvio=via_desvio,
    )
    return {**base, "status": "revertida", "via_desvio": via_desvio}


async def tirar_da_fila_de_situacao(pedido: str) -> None:
    """O pedido não tem mais linha que conte: a situação guardada para o worker
    reaplicar (aplicar_situacao_ou_guardar) perdeu o sentido."""
    from app.redis_client import redis as _redis

    try:
        await _redis.srem(dsr.SITUACAO_PENDENTE_KEY, pedido)
    except Exception:  # noqa: BLE001
        logger.warning("devolucao_exclusao_redis_falhou", pedido_bling=pedido)


# ── Chamado automático ─────────────────────────────────────────────────────

# Eventos de sistema que a ABERTURA AUTOMÁTICA grava sem ninguém ter agido: o
# registro do chamado e o encaminhamento pro robô ("o protocolo aparece aqui
# quando ele abrir"). Também os que o próprio DaVinci grava quando a pessoa
# mexe no motivo na aba Devoluções (encerra por falta de motivo, cancela a
# abertura que nem tinha saído, anota o item do kit que chegou) — nada disso é
# ação no chamado. Qualquer outro evento (status do Bling trocado, protocolo
# capturado, disputa aberta, "Atenção: já tinha sido enviada"…) é sinal de que
# alguém — pessoa, robô ou plataforma — mexeu.
_EVENTO_ABERTURA_AUTO = "Chamado aberto automaticamente pela devolução"
_EVENTO_ENCAMINHADO_ROBO = "o protocolo aparece aqui quando ele abrir"
_ENCERRADO_PELO_MOTIVO = (
    "o chamado ficou sem razão de existir",
    "o motivo novo não abre chamado",
    "outro chamado foi aberto com o motivo novo",
)
_EVENTO_ABERTURA_CANCELADA = "A contestação ainda não tinha saído (estava pendente"
_EVENTO_KIT_PARCIAL = "O chamado continua pelos outros"
# Sufixo que abrir_chamado_devolucao põe na observação ao substituir um chamado.
_RE_OBS_SUBSTITUI = re.compile(r' \(substitui o chamado anterior, motivo "[^"]*"\)$')


def _evento_automatico(m: ChamadoMensagem) -> bool:
    from app.services.chamados import ENCERRADO_PREFIXO

    if m.autor_id is not None or (m.autor_nome or "sistema") != "sistema":
        return False
    texto = m.texto or ""
    if texto.startswith(_EVENTO_ABERTURA_AUTO):
        return True
    if texto.startswith(ENCERRADO_PREFIXO):
        return any(t in texto for t in _ENCERRADO_PELO_MOTIVO)
    if texto.startswith(_EVENTO_ABERTURA_CANCELADA):
        return True
    if texto.startswith("Item ") and _EVENTO_KIT_PARCIAL in texto:
        return True
    if _EVENTO_ENCAMINHADO_ROBO not in texto:
        return False
    # "(reclamação 123 encerrada)": o comprador abriu reclamação no ML — agiu.
    # "A Shopee já aprovou o só reembolso": a plataforma decidiu.
    baixo = texto.lower()
    if "reclamação" in baixo and "sem reclamação aberta" not in baixo:
        return False
    return "já aprovou" not in baixo


def _observacao_automatica(obs: str | None) -> bool:
    """A observação ainda é a que a abertura automática gravou: prefixo +
    motivo (que a troca de motivo reescreve) + o sufixo da substituição.
    Qualquer outra coisa — inclusive uma linha acrescentada pelo formulário do
    ML com o texto do operador — foi escrita por uma pessoa."""
    from app.services.chamados import MOTIVOS_ABREM_CHAMADO, OBS_AUTO_PREFIXO

    obs = (obs or "").strip()
    if not obs:
        return True
    if not obs.startswith(OBS_AUTO_PREFIXO):
        return False
    motivo = _RE_OBS_SUBSTITUI.sub("", obs[len(OBS_AUTO_PREFIXO):]).strip()
    return motivo.lower() in MOTIVOS_ABREM_CHAMADO


def _lock_ocupado(exc: DBAPIError) -> bool:
    """FOR UPDATE NOWAIT que achou a linha travada (55P03 lock_not_available)."""
    orig = getattr(exc, "orig", None)
    codigo = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return codigo == "55P03" or "could not obtain lock" in str(exc).lower()


async def motivo_chamado_mexido(session: AsyncSession, ch: Chamado) -> str | None:
    """None = ninguém agiu no chamado automático desde a abertura; senão o
    motivo de mantê-lo. Na dúvida, fica. Trava as mensagens (FOR UPDATE NOWAIT):
    o /agent/lease e o cron das pendentes pulam o que está travado; e se um
    deles está com a abertura agora, o chamado fica ("sendo processado")."""
    from app.services.chamados import (
        STATUS_ABERTURA_POR_ORIGEM,
        STATUS_ENCERRADO,
        encerrado_por_pessoa,
    )

    if ch.created_by is not None:
        return "foi registrado por uma pessoa"
    if (ch.chamado or "").strip() or (ch.chamado_url or "").strip() or ch.chamado_de_tela:
        return "já tem protocolo na plataforma"
    if (ch.consulta_portal or "").strip():
        return "tem consulta no Portal de Atendimento"
    if ch.resolvido:
        return "já foi concluído"
    if ch.leitura_robo_at is not None or ch.leitura_robo_claim_at is not None:
        return "o robô já leu o caso na plataforma"
    if ch.valor_recuperado is not None or ch.valor_sugerido is not None:
        return "já tem valor de resultado"
    if ch.juridico_enviado_at is not None:
        return "foi encaminhado ao jurídico"
    if ch.auto_ligada:
        return "tem réplica automática ligada"
    if (ch.auto_mensagem or "").strip() or ch.auto_dias is not None:
        return "tem réplica automática configurada por uma pessoa"
    status_auto = STATUS_ABERTURA_POR_ORIGEM.get("devolucao")
    if (ch.alterar_status_bling or "").strip() and (ch.alterar_status_bling or "").strip() != (
        status_auto or ""
    ):
        return "tem situação do Bling escolhida por uma pessoa"
    if not _observacao_automatica(ch.observacao):
        return "tem observação escrita por uma pessoa"
    email = (
        await session.execute(
            select(MailMessageMeta.message_id).where(MailMessageMeta.chamado_id == ch.id).limit(1)
        )
    ).first()
    if email is not None:
        return "tem e-mail da Pós-venda ligado"
    # O disparo da contestação (worker e cron das pendentes) roda com este
    # lock: pegando no meio, a contestação pode estar saindo agora.
    livre = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": f"chamado_devolucao_disparar:{ch.id}"},
        )
    ).scalar()
    if not livre:
        return "a contestação está sendo enviada agora"
    # NOWAIT: o lease do robô ou o lote do cron com a mensagem em mãos não
    # pode deixar a exclusão parada até o fim deles — o chamado fica.
    try:
        async with session.begin_nested():
            msgs = list(
                (
                    await session.execute(
                        select(ChamadoMensagem)
                        .where(ChamadoMensagem.chamado_id == ch.id)
                        .order_by(ChamadoMensagem.created_at)
                        .with_for_update(nowait=True)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
    except DBAPIError as exc:
        if not _lock_ocupado(exc):
            raise
        return "está sendo processado agora (robô ou envio da contestação)"
    abertura_ids: set[UUID] = set()
    for m in msgs:
        if m.tipo == "abertura":
            if m.autor_id is not None or m.direcao == "recebida":
                return "tem mensagem de uma pessoa ou da plataforma"
            if m.status in ("enviada", "enviando"):
                return "a contestação já foi enviada à plataforma"
            abertura_ids.add(m.id)
            continue
        if m.tipo == "sistema" and _evento_automatico(m):
            continue
        return "tem mensagens além da abertura automática"
    if ch.status_plataforma:
        # Encerrado porque a PESSOA tirou/trocou o motivo na aba Devoluções
        # (caso 293843), com a contestação que nem saiu (enviada já voltou
        # acima): ninguém agiu no caso.
        encerrado_pelo_motivo = (
            ch.status_plataforma == STATUS_ENCERRADO and encerrado_por_pessoa(msgs) is not None
        )
        if not encerrado_pelo_motivo:
            return f"já tem status oficial na plataforma ({ch.status_plataforma})"
    # Fotos da abertura são cópias das da devolução (o disparo copia); foto sem
    # mensagem (réplica automática) ou de outra mensagem foi posta por alguém.
    fora_da_abertura = [ChamadoAnexo.chamado_id == ch.id]
    if abertura_ids:
        fora_da_abertura.append(
            ChamadoAnexo.mensagem_id.is_(None)
            | ChamadoAnexo.mensagem_id.not_in(list(abertura_ids))
        )
    anexo_de_pessoa = (
        await session.execute(select(ChamadoAnexo.id).where(*fora_da_abertura).limit(1))
    ).first()
    if anexo_de_pessoa is not None:
        return "tem anexo posto por uma pessoa"
    return None


def _linha_de_referencia(restantes: list[Devolution]) -> Devolution | None:
    """A linha que passa a ser a origem do chamado: a mais antiga cujo motivo
    pede chamado; senão a mais antiga."""
    from app.services.chamados import motivo_pede_chamado

    pedem = [d for d in restantes if motivo_pede_chamado(d)]
    return (pedem or restantes or [None])[0]


def _reapontar(ch: Chamado, dev_id: UUID, restantes: list[Devolution], out: dict) -> None:
    """A origem não pode ficar apontando para uma linha que não existe (o
    disparo acharia "devolução não encontrada")."""
    nova = _linha_de_referencia(restantes)
    if nova is not None and (ch.origem_ref or "") == str(dev_id):
        ch.origem_ref = str(nova.id)
        out["reapontado"] = True
        logger.info(
            "devolucao_exclusao_chamado_reapontado",
            chamado_id=str(ch.id), de=str(dev_id), para=str(nova.id),
        )


async def desfazer_chamado(
    session: AsyncSession,
    *,
    pedido: str | None,
    dev_id: UUID,
    restantes: list[Devolution],
    actor_id: UUID | None,
) -> dict:
    """Chamado(s) origem 'devolucao' do pedido depois da exclusão de uma linha.

    * Sobrou linha cujo motivo pede chamado: o chamado continua — e, se a
      origem dele (`origem_ref`) era a linha excluída, passa a apontar para uma
      que ficou.
    * Não sobrou (era a última linha, ou as que ficaram não pedem chamado):
      apaga o chamado que o DaVinci abriu SE ninguém agiu nele; se agiu (o ML já
      abriu reclamação, a contestação já saiu…), mantém e avisa.

    Só entra na conta o chamado que esta exclusão pode desfazer: o que nasceu
    desta linha (`origem_ref`, que o reapontamento mantém) ou o ainda aberto de
    uma linha que ficou. Chamado de episódio anterior (origem numa linha que
    não existe mais) ou já concluído fica quieto, sem aviso.

    Devolve {apagados: [ids], mantidos: [{id, motivo}], reapontado: bool}.
    NÃO commita."""
    from app.services.chamados import STATUS_FINAIS, motivo_pede_chamado

    conds = [Chamado.origem == "devolucao"]
    pedido = (pedido or "").strip() or None
    if pedido:
        conds.append(Chamado.pedido_bling == pedido)
    else:
        conds.append(Chamado.origem_ref == str(dev_id))
    chamados = list(
        (await session.execute(select(Chamado).where(*conds).order_by(Chamado.created_at)))
        .scalars()
        .all()
    )
    out: dict = {"apagados": [], "mantidos": [], "reapontado": False}
    if not chamados:
        return out
    if any(motivo_pede_chamado(d) for d in restantes):
        for ch in chamados:
            _reapontar(ch, dev_id, restantes, out)
        return out
    ids_restantes = {str(d.id) for d in restantes}
    for ch in chamados:
        fechado = bool(ch.resolvido) or ch.status_plataforma in STATUS_FINAIS
        ref = ch.origem_ref or ""
        if ref != str(dev_id) and (fechado or ref not in ids_restantes):
            continue  # de outro episódio / já concluído — não é desta exclusão
        motivo = await motivo_chamado_mexido(session, ch)
        if motivo is None:
            out["apagados"].append(str(ch.id))
            await session.delete(ch)
            logger.info(
                "devolucao_exclusao_chamado_apagado", chamado_id=str(ch.id), pedido_bling=pedido
            )
            continue
        _reapontar(ch, dev_id, restantes, out)
        if fechado:
            # Concluído (ou com o desfecho da plataforma): fica, sem aviso — não
            # há nada a revisar num caso fechado.
            continue
        out["mantidos"].append({"id": str(ch.id), "motivo": motivo})
        ref_pedido = pedido or str(dev_id)
        await _alerta(
            session,
            actor_id,
            titulo=f"Pedido {ref_pedido}: chamado da devolução ficou — revisar",
            mensagem=(
                f"Os lançamentos de devolução do pedido {ref_pedido} foram excluídos, mas o "
                f"chamado aberto automaticamente {motivo}, então ficou. Confira na aba "
                "Chamados se ele ainda vale (encerre lá se não valer)."
            ),
            dedupe_key=f"devolucao_exclusao_chamado:{ch.id}",
            payload={"chamado_id": str(ch.id), "pedido_bling": pedido},
        )
    return out
