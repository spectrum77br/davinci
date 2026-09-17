"""Envio das respostas de DM do Instagram (Eduardo, 16/09/2026).

O outro lado do `routers/webhooks.py`: lá a Meta nos chama e a mensagem é
gravada; aqui a resposta enfileirada sai.

Espelha `services/marketing/postagens.py`, com UMA diferença que endurece
tudo: na postagem existe `container_id`, então dá pra PERGUNTAR à Meta "será
que saiu?" antes de retentar. Em mensagem não existe esse passo do meio. Logo:

    envio ambíguo NUNCA retenta — vai direto pra revisão humana.

Post se apaga. DM não se desvê: a notificação já chegou no celular da pessoa.

As três travas de saída, em ordem de quem pergunta primeiro:
  1. `dm_resposta_commit` — a global. Desligada, nada sai (status `seco`).
  2. `dm_resposta_allowlist` — IGSIDs que recebem DE VERDADE mesmo com a
     global desligada. A Meta não tem sandbox de DM; esta lista é o sandbox.
  3. `RedeSocial.dm_auto` — por conta, pra ligar uma marca de cada vez.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DmConta, DmConversa, DmMensagem, RedeSocial
from app.models.instagram_dm import (
    CONVERSA_HUMANO,
    CONVERSA_RESPONDIDA,
    DIRECAO_ENVIADA,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_PENDENTE,
    MSG_REVISAR,
    MSG_SECO,
)
from app.security.cipher import decrypt_json
from app.services.marketing import meta_client

logger = structlog.get_logger()


def _allowlist() -> set[str]:
    bruto = get_settings().dm_resposta_allowlist or ""
    return {x.strip() for x in bruto.split(",") if x.strip()}


def pode_enviar(igsid: str) -> bool:
    """A trava de saída, num lugar só.

    Com o commit desligado e a allowlist preenchida, o Instagram pessoal do
    Eduardo recebe resposta real e o mundo inteiro fica no seco — que é como
    se testa o caminho completo sem escrever pra cliente nenhum.
    """
    s = get_settings()
    return bool(s.dm_resposta_commit) or igsid in _allowlist()


def _query_do_lease(limit: int):
    """Pendentes prontas pra sair: a mais antiga de CADA conversa.

    DISTINCT ON vai numa subquery porque o Postgres não aceita DISTINCT junto
    de FOR UPDATE.
    """
    candidatas = (
        select(DmMensagem.id)
        .where(DmMensagem.status == MSG_PENDENTE)
        .order_by(DmMensagem.conversa_id, DmMensagem.created_at.asc())
        .distinct(DmMensagem.conversa_id)
    )
    return (
        select(DmMensagem)
        .where(
            DmMensagem.id.in_(candidatas),
            # Repetido AQUI FORA de propósito — mesmo motivo do robô de
            # postagem. Na subquery o status é avaliado no snapshot do início
            # do comando; sob READ COMMITTED, quando outro worker destrava uma
            # linha que acabou de virar `enviando`, o Postgres re-avalia
            # (EvalPlanQual) só este WHERE externo. Sem a repetição, dois
            # workers levam a MESMA linha — e o cliente recebe a resposta
            # duas vezes.
            DmMensagem.status == MSG_PENDENTE,
        )
        .order_by(DmMensagem.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def proximas_para_enviar(session: AsyncSession, *, limit: int = 10) -> list[DmMensagem]:
    rows = (await session.execute(_query_do_lease(limit))).scalars().all()
    agora = datetime.now(UTC)
    for m in rows:
        m.claimed_at = agora
        m.status = MSG_ENVIANDO
    if rows:
        await session.commit()
    logger.info("dm_lease", leased=len(rows))
    return list(rows)


async def _enviadas_hoje(session: AsyncSession, rede_social_id: UUID | None) -> int:
    if rede_social_id is None:
        return 0
    inicio = datetime.now(UTC) - timedelta(hours=24)
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DmMensagem)
            .join(DmConversa, DmConversa.id == DmMensagem.conversa_id)
            .where(
                DmConversa.rede_social_id == rede_social_id,
                DmMensagem.direcao == DIRECAO_ENVIADA,
                DmMensagem.status == MSG_ENVIADA,
                DmMensagem.enviada_em >= inicio,
            )
        )
        or 0
    )


async def revalidar(session: AsyncSession, msg: DmMensagem) -> str | None:
    """Roda as guardas DE NOVO, no instante em que a mensagem vai sair.

    Entre enfileirar e enviar pode ter passado tempo: a janela pode ter
    vencido, um humano pode ter assumido, a conta pode ter sido desligada.
    Devolve o motivo da recusa, ou None se pode seguir.
    """
    s = get_settings()
    conversa = await session.get(DmConversa, msg.conversa_id)
    if conversa is None:
        return "conversa sumiu"
    if not conversa.auto:
        return "humano assumiu a conversa"
    if conversa.ultima_recebida_em is None:
        return "conversa sem mensagem recebida"
    if datetime.now(UTC) - conversa.ultima_recebida_em > timedelta(hours=s.dm_janela_horas):
        return f"fora da janela de {s.dm_janela_horas}h"
    if not (msg.texto or "").strip():
        return "resposta vazia"

    rede = (
        await session.get(RedeSocial, conversa.rede_social_id)
        if conversa.rede_social_id
        else None
    )
    if rede is None or not rede.ativo:
        return "conta inativa ou removida"
    if not getattr(rede, "dm_auto", False):
        return "resposta automática desligada nesta conta"

    teto = s.dm_resposta_max_dia
    if teto and await _enviadas_hoje(session, conversa.rede_social_id) >= teto:
        return f"teto de {teto} respostas em 24h atingido nesta conta"
    return None


async def enviar(session: AsyncSession, msg: DmMensagem) -> None:
    """Envia UMA resposta e grava o desfecho. Nunca levanta."""
    conversa = await session.get(DmConversa, msg.conversa_id)
    if conversa is None:
        msg.status = MSG_FALHOU
        msg.motivo = "conversa sumiu"
        msg.completed_at = datetime.now(UTC)
        await session.commit()
        return

    motivo = await revalidar(session, msg)
    if motivo:
        # Recusa de guarda não é falha de envio: a mensagem não saiu e a
        # conversa vai pra humano, que decide.
        msg.status = MSG_FALHOU
        msg.motivo = motivo
        msg.completed_at = datetime.now(UTC)
        conversa.status = CONVERSA_HUMANO
        conversa.auto = False
        await session.commit()
        logger.info("dm_recusada", conversa=str(conversa.id), motivo=motivo)
        return

    if not pode_enviar(conversa.participante_id):
        msg.status = MSG_SECO
        msg.motivo = "modo seco: passou em todas as guardas e não foi enviada"
        msg.completed_at = datetime.now(UTC)
        await session.commit()
        logger.info("dm_seco", conversa=str(conversa.id), tamanho=len(msg.texto or ""))
        return

    token_row = await session.scalar(
        select(DmConta).where(DmConta.rede_social_id == conversa.rede_social_id)
    )
    if token_row is None or not token_row.token_enc or not token_row.ig_user_id:
        msg.status = MSG_FALHOU
        msg.motivo = "conta sem token conectado"
        msg.completed_at = datetime.now(UTC)
        await session.commit()
        return

    dados = decrypt_json(token_row.token_enc) or {}
    token = dados.get("access_token")
    if not token:
        msg.status = MSG_FALHOU
        msg.motivo = "token ilegível"
        msg.completed_at = datetime.now(UTC)
        await session.commit()
        return

    msg.attempts = (msg.attempts or 0) + 1
    resultado = await meta_client.enviar_dm(
        token,
        ig_id=token_row.ig_user_id,
        destinatario=conversa.participante_id,
        texto=msg.texto or "",
        # Trilha Login do Instagram: sempre graph.instagram.com.
        provedor=meta_client.PROVEDOR_INSTAGRAM,
    )
    agora = datetime.now(UTC)
    msg.completed_at = agora
    msg.result = resultado.erro

    if resultado.ok:
        msg.status = MSG_ENVIADA
        msg.enviada_em = agora
        msg.mid = resultado.mid or msg.mid
        conversa.ultima_enviada_em = agora
        conversa.status = CONVERSA_RESPONDIDA
    elif resultado.ambiguo:
        # A chamada PODE ter saído e não há como perguntar. Ninguém retenta
        # em cima disso — a diferença cruel entre mensagem e postagem.
        msg.status = MSG_REVISAR
        msg.motivo = "envio ambíguo: pode ter saído. Confira a conversa antes de responder."
        conversa.status = CONVERSA_HUMANO
        conversa.auto = False
    else:
        msg.status = MSG_FALHOU
        msg.motivo = resultado.erro
        if (msg.attempts or 0) >= (msg.tentativas_max or 2):
            conversa.status = CONVERSA_HUMANO
            conversa.auto = False

    await session.commit()
    logger.info(
        "dm_envio_resultado",
        conversa=str(conversa.id),
        status=msg.status,
        code=resultado.code,
    )


async def responder_pendentes(session: AsyncSession, *, limit: int = 10) -> int:
    """Um tick: pega o lease e envia cada uma. Devolve quantas tratou."""
    pendentes = await proximas_para_enviar(session, limit=limit)
    for msg in pendentes:
        await enviar(session, msg)
    return len(pendentes)
