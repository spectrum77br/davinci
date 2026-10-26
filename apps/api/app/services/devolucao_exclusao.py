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

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import desc, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    Devolution,
    MargemAudit,
    Refund,
    SituacaoBling,
)
from app.models.enums import AlertSeverity, AlertType
from app.services import devolution_stock_return as dsr
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


def motivo_refund_mexido(refund: Refund, row: Devolution) -> str | None:
    """None = ninguém mexeu no prejuízo automático desde que o DaVinci o criou;
    senão, o que mudou (vai no aviso). Extraviado/Sucata nascem com reembolso 0;
    Manutenção nasce com reembolso = −custo do conserto (_sync_manutencao_to_refund).
    Conferido = alguém da agência já olhou — fica."""
    if refund.conferido:
        return "já foi conferido na tela Reembolso"
    reembolso = float(refund.reembolso or 0)
    if refund.tipo == "Manutenção":
        esperado = -float(row.custo_manutencao or 0)
        if abs(reembolso - esperado) >= _CENTAVO:
            return (
                f"o reembolso ({_brl(reembolso)}) não é mais só o débito do conserto "
                f"({_brl(esperado)})"
            )
        return None
    if abs(reembolso) >= _CENTAVO:
        return f"já tem {_brl(reembolso)} de reembolso lançado"
    return None


async def achar_refund_auto(
    session: AsyncSession, row: Devolution, tipo: str
) -> Refund | None:
    """O prejuízo que o DaVinci criou para ESTA linha, ou None.

    1. Vínculo exato, sem migration: o refund do POST /api/devolutions nasce no
       MESMO INSERT/commit da linha, e `created_at` é server_default now() — o
       início da transação —, então refund.created_at == devolution.created_at.
    2. Sem vínculo exato, o refund nasceu num PATCH (linha que virou Extraviado
       depois): a heurística do custo (prejuízo == custo), entre os criados
       depois da linha e que não são de outra linha do pedido; senão o mais
       recente deles.

    Nunca: refund de outro pedido, de outra conta, de outro tipo ou criado à mão
    na tela Reembolso (o POST de lá grava `created_by`; o automático, não)."""
    conds = _pedido_conds(Refund, row)
    if conds is None:
        return None
    refunds = list(
        (
            await session.execute(
                select(Refund)
                .where(
                    *conds,
                    Refund.tipo == tipo,
                    func.btrim(Refund.conta) == (row.conta or "").strip(),
                    Refund.created_by.is_(None),
                )
                .order_by(desc(Refund.created_at))
            )
        )
        .scalars()
        .all()
    )
    if not refunds:
        return None
    exato = next((r for r in refunds if r.created_at == row.created_at), None)
    if exato is not None:
        return exato
    # Os que nasceram junto com OUTRA linha do pedido são dela (kit com a mesma
    # mala duas vezes: cada linha leva o seu, nunca dois).
    dconds = _pedido_conds(Devolution, row) or []
    outras = {
        c
        for (c,) in (
            await session.execute(
                select(Devolution.created_at).where(
                    *dconds,
                    func.btrim(Devolution.conta) == (row.conta or "").strip(),
                    Devolution.id != row.id,
                )
            )
        ).all()
    }
    candidatos = [
        r
        for r in refunds
        if r.created_at not in outras
        and (row.created_at is None or r.created_at >= row.created_at)
    ]
    if not candidatos:
        return None
    custo = _custo_do_refund(row, tipo)
    return next(
        (r for r in candidatos if abs(float(r.prejuizo or 0) - custo) < _CENTAVO),
        candidatos[0],
    )


async def desfazer_refund_auto(
    session: AsyncSession,
    row: Devolution,
    tipo: str,
    actor_id: UUID | None,
    *,
    exclusao: bool,
) -> dict | None:
    """Apaga o prejuízo automático da linha se ninguém mexeu nele; se mexeram,
    mantém e avisa (sino/Telegram) pra revisar. `exclusao` = a linha está sendo
    excluída (senão: o PATCH tirou a linha de Extraviado/Sucata — o produto
    apareceu, Eduardo 15/09, caso 293843). Devolve None (não achou) ou
    {acao: apagado|mantido, tipo, prejuizo, reembolso, motivo}. NÃO commita."""
    refund = await achar_refund_auto(session, row, tipo)
    if refund is None:
        return None
    info = {
        "refund_id": str(refund.id),
        "tipo": tipo,
        "prejuizo": float(refund.prejuizo or 0),
        "reembolso": float(refund.reembolso or 0),
        "conferido": bool(refund.conferido),
    }
    motivo = motivo_refund_mexido(refund, row)
    if motivo is None:
        await session.delete(refund)
        logger.info(
            "refund_auto_removed",
            refund_id=info["refund_id"],
            pedido_bling=row.pedido_bling,
            tipo=tipo,
            nova_condicao=None if exclusao else row.condicao_produto,
            exclusao=exclusao,
        )
        return {**info, "acao": "apagado", "motivo": None}

    pedido = row.pedido_bling or row.pedido_marketplace or ""
    if exclusao:
        titulo = f"Pedido {pedido}: prejuízo {tipo} ficou — revisar"
        mensagem = (
            f"O lançamento de devolução do pedido {pedido} ({row.sku or 'sem SKU'}) foi "
            f"excluído, mas o prejuízo {tipo} que o DaVinci criou para ele {motivo}, "
            "então ficou. Confira na tela de Reembolso se ele ainda vale."
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
            f"{row.condicao_produto or '—'} (o produto apareceu), mas o reembolso "
            f"{tipo} {motivo}. Confira na tela de Reembolso se ele ainda vale."
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
        tipo=tipo, motivo=motivo, exclusao=exclusao,
    )
    return {**info, "acao": "mantido", "motivo": motivo}


def resumo_prejuizos(resultados: list[dict | None]) -> dict:
    """Campos da resposta das duas lixeiras: prejuízos automáticos apagados
    (qtd e total) e os mantidos (com o motivo — alguém mexeu)."""
    apagados = [r for r in resultados if r and r.get("acao") == "apagado"]
    mantidos = [r for r in resultados if r and r.get("acao") in ("mantido", "erro")]
    return {
        "prejuizos_apagados": {
            "qtd": len(apagados),
            "total": round(sum(float(r.get("prejuizo") or 0) for r in apagados), 2),
        },
        "prejuizos_mantidos": [
            {
                "tipo": r.get("tipo"),
                "prejuizo": r.get("prejuizo"),
                "reembolso": r.get("reembolso"),
                "motivo": r.get("motivo"),
            }
            for r in mantidos
        ],
    }


# ── Situação do pedido ─────────────────────────────────────────────────────


async def linhas_do_pedido(session: AsyncSession, pedido: str | None) -> list[Devolution]:
    pedido = (pedido or "").strip()
    if not pedido:
        return []
    return list(
        (
            await session.execute(
                select(Devolution)
                .where(Devolution.pedido_bling == pedido)
                .order_by(Devolution.created_at)
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
    Lançamentos, Margem, robôs, uma volta já feita)."""
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
    return list(reversed(seq)), mudou_depois


async def reverter_situacao(
    session: AsyncSession, pedido: str, *, actor_id: UUID | None
) -> dict:
    """Sem nenhuma linha que conte no pedido: volta a situação para a que ele
    tinha ANTES de o fluxo de devolução mexer. Só reverte se a situação atual
    ainda for uma que o fluxo pôs (espelho — que o fluxo não atualiza na hora,
    então pode estar um passo atrás) e se ninguém mudou por outro caminho
    depois. Aplica com a regra do desvio do endpoint manual, atualiza o espelho
    e grava margem_audit (origem 'devolucao_exclusao', mudado_por = quem
    excluiu). Bling fora/recusando: não levanta — devolve `falhou` com a
    situação que devia ser (e avisa no sino). NÃO commita.

    Devolve {status: revertida|ja_estava|sem_historico|mudada_depois|falhou, ...}."""
    seq, mudou_depois = await _sequencia_do_fluxo(session, pedido)
    if mudou_depois:
        return {"status": "mudada_depois"}
    if not seq:
        return {"status": "sem_historico"}
    alvo = (seq[0].valor_antigo or "").strip()
    ultima = (seq[-1].valor_novo or "").strip()
    if not alvo.isdigit():
        return {"status": "sem_historico"}
    nome_alvo = await nome_situacao(session, alvo)
    order_row = (
        await session.execute(
            select(BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero == pedido, BlingOrder.bling_id.is_not(None))
            .order_by(BlingOrder.data.desc().nullslast())
            .limit(1)
        )
    ).first()
    if order_row is None:
        return {"status": "sem_historico"}
    atual = (order_row.situacao or "").strip()
    da_sequencia = {alvo} | {(a.valor_novo or "").strip() for a in seq} | {
        (a.valor_antigo or "").strip() for a in seq
    }
    if atual and atual not in da_sequencia:
        # Mudada no painel do Bling (o sync trouxe pro espelho) — é de alguém.
        return {
            "status": "mudada_depois",
            "atual": atual,
            "nome_atual": await nome_situacao(session, atual),
            "para": int(alvo),
            "nome": nome_alvo,
        }
    if ultima == alvo:
        return {"status": "ja_estava", "para": int(alvo), "nome": nome_alvo}

    base = {"de": ultima, "nome_de": await nome_situacao(session, ultima),
            "para": int(alvo), "nome": nome_alvo}
    erro: str | None = None
    via_desvio = False
    client = await dsr._get_bling_client(session)
    if client is None:
        erro = "nenhuma integração Bling encontrada"
    else:
        try:
            via_desvio = await dsr.mudar_situacao_com_desvio(
                client, int(order_row.bling_id), int(alvo), ultima
            )
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

    # Espelho na hora: a aba Acompanhamento (só 83957) lê daqui.
    await session.execute(
        update(BlingOrder).where(BlingOrder.numero == pedido).values(situacao=str(alvo))
    )
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
# quando ele abrir"). Qualquer outro evento (status do Bling trocado, protocolo
# capturado, disputa aberta, encerrado, réplica cancelada…) é sinal de que
# alguém — pessoa, robô ou plataforma — mexeu.
_EVENTO_ABERTURA_AUTO = "Chamado aberto automaticamente pela devolução"
_EVENTO_ENCAMINHADO_ROBO = "o protocolo aparece aqui quando ele abrir"


def _evento_automatico(m: ChamadoMensagem) -> bool:
    if m.autor_id is not None or (m.autor_nome or "sistema") != "sistema":
        return False
    texto = m.texto or ""
    if texto.startswith(_EVENTO_ABERTURA_AUTO):
        return True
    if _EVENTO_ENCAMINHADO_ROBO not in texto:
        return False
    # "(reclamação 123 encerrada)": o comprador abriu reclamação no ML — agiu.
    # "A Shopee já aprovou o só reembolso": a plataforma decidiu.
    baixo = texto.lower()
    if "reclamação" in baixo and "sem reclamação aberta" not in baixo:
        return False
    return "já aprovou" not in baixo


async def motivo_chamado_mexido(session: AsyncSession, ch: Chamado) -> str | None:
    """None = ninguém agiu no chamado automático desde a abertura; senão o
    motivo de mantê-lo. Trava as mensagens (FOR UPDATE): o /agent/lease que
    chegar no meio espera e, depois do DELETE, não acha mais nada."""
    if ch.created_by is not None:
        return "foi registrado por uma pessoa"
    if (ch.chamado or "").strip() or (ch.chamado_url or "").strip() or ch.chamado_de_tela:
        return "já tem protocolo na plataforma"
    if (ch.consulta_portal or "").strip():
        return "tem consulta no Portal de Atendimento"
    if ch.resolvido:
        return "já foi concluído"
    if ch.status_plataforma:
        return "a plataforma já deu status ao caso"
    if ch.leitura_robo_at is not None or ch.leitura_robo_claim_at is not None:
        return "o robô já leu o caso na plataforma"
    if ch.valor_recuperado is not None or ch.valor_sugerido is not None:
        return "já tem valor de resultado"
    if ch.juridico_enviado_at is not None:
        return "foi encaminhado ao jurídico"
    if ch.auto_ligada:
        return "tem réplica automática ligada"
    # O disparo da contestação (worker) roda com este lock: pegando no meio,
    # a contestação pode estar saindo agora.
    livre = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": f"chamado_devolucao_disparar:{ch.id}"},
        )
    ).scalar()
    if not livre:
        return "a contestação está sendo enviada agora"
    msgs = list(
        (
            await session.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
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
      que ficou (senão o disparo acharia "devolução não encontrada").
    * Não sobrou (era a última linha, ou as que ficaram não pedem chamado):
      apaga o chamado que o DaVinci abriu SE ninguém agiu nele; se agiu (o ML já
      abriu reclamação, a contestação já saiu…), mantém e avisa.

    Devolve {apagados: [ids], mantidos: [{id, motivo}], reapontado: bool}.
    NÃO commita."""
    from app.services.chamados import motivo_pede_chamado

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
        nova = _linha_de_referencia(restantes)
        for ch in chamados:
            if nova is not None and (ch.origem_ref or "") == str(dev_id):
                ch.origem_ref = str(nova.id)
                out["reapontado"] = True
                logger.info(
                    "devolucao_exclusao_chamado_reapontado",
                    chamado_id=str(ch.id), de=str(dev_id), para=str(nova.id),
                )
        return out
    for ch in chamados:
        motivo = await motivo_chamado_mexido(session, ch)
        if motivo is None:
            out["apagados"].append(str(ch.id))
            await session.delete(ch)
            logger.info(
                "devolucao_exclusao_chamado_apagado", chamado_id=str(ch.id), pedido_bling=pedido
            )
            continue
        out["mantidos"].append({"id": str(ch.id), "motivo": motivo})
        # A origem não pode ficar apontando para uma linha que não existe.
        nova = _linha_de_referencia(restantes)
        if nova is not None and (ch.origem_ref or "") == str(dev_id):
            ch.origem_ref = str(nova.id)
            out["reapontado"] = True
        ref = pedido or str(dev_id)
        await _alerta(
            session,
            actor_id,
            titulo=f"Pedido {ref}: chamado da devolução ficou — revisar",
            mensagem=(
                f"Os lançamentos de devolução do pedido {ref} foram excluídos, mas o "
                f"chamado aberto automaticamente {motivo}, então ficou. Confira na aba "
                "Chamados se ele ainda vale (encerre lá se não valer)."
            ),
            dedupe_key=f"devolucao_exclusao_chamado:{ch.id}",
            payload={"chamado_id": str(ch.id), "pedido_bling": pedido},
        )
    return out
