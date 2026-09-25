"""Acompanhamento das contestações de devolução abertas pela API — a RESPOSTA
da plataforma cai no histórico do chamado e o chamado fecha sozinho.

Eduardo 04/09: "a resposta tem que chegar no histórico tbm". Cron de hora em
hora (junto do `chamados_replica_automatica`): pra cada chamado de origem
`devolucao`, canal `api`, aberto e já ENVIADO, consulta a plataforma:

- **TikTok**: returns/search por order_id → `return_status` / `arbitration_status`
  (REJECT_RECEIVE_PACKAGE / REFUND_OR_RETURN_REQUEST_REJECT = a NOSSA recusa
  registrada, o comprador ainda pode recorrer → aguardando plataforma;
  IN_PROGRESS = comprador contestou; SUPPORT_SELLER/SUPPORT_BUYER = decisão;
  RETURN_OR_REFUND_REQUEST_CANCEL = vendedor ficou com o valor — a não ser que o
  comprador tenha refeito o pedido, aí o chamado segue o caso novo;
  ..._SUCCESS/_COMPLETE = reembolsado; recusa sem recurso por 10 dias = ganhamos)
  + linha do tempo (`returns/{id}/records`: notas do comprador/plataforma, com
  a hora real de cada nota).
- **Shopee**: get_return_detail → `status` (SELLER_DISPUTE/JUDGING/CLOSED…),
  `seller_proof` (Shopee pediu prova extra + prazo), `seller_compensation`
  (APPROVED/REJECTED = decisão).
- **Mercado Livre**: claim (status/resolution) + mensagens do mediador/comprador
  (`claims/{id}/messages`).

Cada estado/mensagem novo vira UMA mensagem `recebida` no histórico (dedupe
pelo texto — o cron pode rodar quantas vezes quiser). Best-effort por chamado.

19/09 (Vinicius): estado final NÃO fecha mais o chamado. Até aqui o
acompanhamento marcava `resolvido` sozinho e gravava o valor — e ninguém
conferia lucro/prejuízo nem a situação do Bling. Agora a decisão da plataforma
põe o chamado no estado "Encerrado" (`status_plataforma` final + evento
"Plataforma encerrou o caso (…) — aguardando fechamento") e a compensação lida
na API vira SUGESTÃO (`valor_sugerido`); só uma pessoa conclui, pelo
`/resolver`. Chamado já Encerrado sai da varredura (ver `_encerrado_na_plataforma`).

17/09 (Vinicius, coluna "Status" da aba): cada passada também grava em
`Chamado.status_plataforma` o que a plataforma diz do caso (em análise, pediu
prova, reembolso pago, ganhamos/perdemos) e desde quando — ver
`chamados.set_status_plataforma`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados as chamados_svc
from app.services import chamados_devolucao as cd
from app.services import chamados_tiktok_reembolso as tiktok_reembolso
from app.services import logistica_rules, logistica_tiktok
from app.services.devolucao_returns import epoch_to_dt, iso_to_dt
from app.services.texto_html import limpar_html

logger = structlog.get_logger()

AUTOR = {cd.PLAT_ML: "Mercado Livre", cd.PLAT_TIKTOK: "TikTok Shop", cd.PLAT_SHOPEE: "Shopee"}
AUTOR_ACOMP = "acompanhamento"
# Abertura que falhou mas cujo caso segue vivo (ou já decidido) na plataforma.
ABERTURA_FALHOU_ACOMPANHA = (
    "shopee_ja_contestada", "shopee_prazo_contestacao_esgotado", "shopee_devolucao_encerrada",
    "tiktok_ja_recusada", "tiktok_devolucao_encerrada",
    "ml_claim_encerrada", "ml_claim_encerrada_sem_prejuizo",
)

# ---- TikTok --------------------------------------------------------------------
_TT_STATUS_TXT: dict[str, tuple[str, bool]] = {
    # texto, encerra?
    "RETURN_OR_REFUND_REQUEST_CANCEL": (
        "Devolução CANCELADA na TikTok — a recusa foi mantida e o valor fica com o vendedor.",
        True,
    ),
    "RETURN_OR_REFUND_REQUEST_SUCCESS": (
        "TikTok aprovou o reembolso ao comprador (a recusa não foi mantida).",
        True,
    ),
    "RETURN_OR_REFUND_REQUEST_COMPLETE": (
        "Devolução concluída na TikTok com reembolso ao comprador.",
        True,
    ),
}
# 18/09 (Vinicius, 296936): "vendedor recusou" NÃO é ganhamos — é a NOSSA recusa
# registrada na TikTok; o comprador ainda pode abrir disputa (medido ao vivo em
# 60 dias: recorreu em 5 de 5 só-reembolsos e 17 de 17 pacotes recusados, de
# 4 min a 3,7 dias depois; a TikTok levou de 11 min a 35 dias pra decidir).
# Antes o caso virava "Ganhamos" e o chamado FECHAVA às 12:25 — se o comprador
# recorresse, ninguém via a disputa nem a decisão. Agora: evento de sistema (não
# é fala da plataforma), status "aguardando plataforma" e o acompanhamento
# segue até a TikTok decidir; sem recurso em TT_RECUSA_CARENCIA, ganhamos.
_TT_RECUSA_NOSSA: dict[str, str] = {
    "REFUND_OR_RETURN_REQUEST_REJECT": (
        "Recusa do reembolso registrada na TikTok. O comprador ainda pode contestar "
        "(disputa) ou refazer o pedido no prazo da plataforma — o acompanhamento segue "
        "até a decisão."
    ),
    "REJECT_RECEIVE_PACKAGE": (
        "Recusa do pacote registrada na TikTok. O comprador ainda pode contestar "
        "(arbitragem) no prazo da plataforma — o acompanhamento segue até a decisão."
    ),
}
TT_RECUSA_CARENCIA = timedelta(days=10)
_TT_RECUSA_VENCIDA_TXT = (
    "10 dias sem recurso do comprador depois da recusa — a recusa ficou valendo (ganhamos)."
)
_TT_ARB_TXT: dict[str, tuple[str, bool]] = {
    "IN_PROGRESS": ("O comprador contestou a recusa: caso em ARBITRAGEM na TikTok.", False),
    "SUPPORT_SELLER": ("Arbitragem da TikTok decidida A FAVOR DO VENDEDOR.", False),
    "SUPPORT_BUYER": ("Arbitragem da TikTok decidida a favor do COMPRADOR (reembolso).", False),
    "CLOSED": ("Arbitragem encerrada na TikTok.", False),
}
# Coluna Status da aba (17/09): o que cada estado da TikTok significa pra loja.
_TT_STATUS_STATUS: dict[str, str] = {
    "RETURN_OR_REFUND_REQUEST_CANCEL": chamados_svc.STATUS_GANHAMOS,
    "RETURN_OR_REFUND_REQUEST_SUCCESS": chamados_svc.STATUS_PERDEMOS,
    "RETURN_OR_REFUND_REQUEST_COMPLETE": chamados_svc.STATUS_PERDEMOS,
}
_TT_ARB_STATUS: dict[str, str] = {
    "IN_PROGRESS": chamados_svc.STATUS_EM_ANALISE,
    "SUPPORT_SELLER": chamados_svc.STATUS_GANHAMOS,
    "SUPPORT_BUYER": chamados_svc.STATUS_PERDEMOS,
}
_TT_TIPO = {"REFUND": "só reembolso", "RETURN_AND_REFUND": "devolução"}
# Estados em que aquele caso ACABOU. Vinicius 22/09 (293798): a compradora abriu
# um caso novo depois que o anterior terminou e o painel não mostrou nada — a
# troca pelo caso novo só acontecia quando o anterior estava CANCELADO. Agora
# qualquer caso morto cede a vez pro caso vivo do mesmo pedido.
# Acabou MESMO (reembolso pago, concluído, cancelado) — diferente de `_TT_MORTOS`,
# que inclui a NOSSA recusa: ali o caso segue vivo, o comprador ainda recorre.
_TT_FINALIZADOS = frozenset(
    {
        "RETURN_OR_REFUND_REQUEST_SUCCESS",
        "RETURN_OR_REFUND_REQUEST_COMPLETE",
        "RETURN_OR_REFUND_REQUEST_CANCEL",
    }
)
_TT_MORTOS = frozenset(
    {
        "RETURN_OR_REFUND_REQUEST_CANCEL",
        "RETURN_OR_REFUND_REQUEST_SUCCESS",
        "RETURN_OR_REFUND_REQUEST_COMPLETE",
        "REFUND_OR_RETURN_REQUEST_REJECT",
        "REJECT_RECEIVE_PACKAGE",
    }
)
_TT_STATUS_NOME = {
    "RETURN_OR_REFUND_REQUEST_PENDING": "aguardando a nossa resposta",
    "REFUND_OR_RETURN_REQUEST_REJECT": "já recusado",
    "AWAITING_BUYER_SHIP": "aguardando o comprador postar",
    "BUYER_SHIPPED_ITEM": "pacote a caminho",
    "RETURN_OR_REFUND_REQUEST_SUCCESS": "reembolso aprovado",
    "RETURN_OR_REFUND_REQUEST_COMPLETE": "reembolsado",
    "RETURN_OR_REFUND_REQUEST_CANCEL": "cancelado",
}

# ---- Shopee --------------------------------------------------------------------
_SH_STATUS_TXT: dict[str, tuple[str, bool]] = {
    "SELLER_DISPUTE": ("Disputa registrada na Shopee — em análise.", False),
    "JUDGING": ("A Shopee está julgando a disputa.", False),
    "CLOSED": ("Devolução ENCERRADA na Shopee.", True),
    "CANCELLED": ("Devolução CANCELADA na Shopee.", True),
}
_SH_COMP_TXT: dict[str, tuple[str, bool]] = {
    "APPROVED": ("Shopee APROVOU a compensação ao vendedor.", True),
    "REJECTED": ("Shopee NEGOU a compensação ao vendedor.", True),
    "REQUESTED": ("Pedido de compensação registrado na Shopee — aguardando análise.", False),
}
_SH_COMP_STATUS: dict[str, str] = {
    "APPROVED": chamados_svc.STATUS_GANHAMOS,
    "REJECTED": chamados_svc.STATUS_PERDEMOS,
    "REQUESTED": chamados_svc.STATUS_EM_ANALISE,
}
# Medido 17/09 nas 16 disputas Shopee da aba: `seller_compensation_status` vem
# SEMPRE vazio no BR — a decisão aparece em `compensation_amount` (+ lista) e,
# no escrow, como `order_adjustment` "Logistics Related Compensation". Quando a
# disputa foi registrada e a Shopee reembolsou o comprador SEM compensar a loja
# (`seller_return_refund` < 0 ou ajuste "reembolso aprovado"), ela finalizou
# contra a loja — o 2608300N7X5K2HF fechou 4 min depois da disputa. Como no caso
# ganho a compensação chegou ~1 h depois do reembolso (2608170J49H1EBQ), o
# "perdemos" só é decretado passada esta carência desde o reembolso.
_SH_PERDEMOS_CARENCIA = timedelta(hours=24)
_SH_PERDEMOS_TXT = (
    "Shopee ENCERROU a disputa sem compensação — reembolso integral ao comprador (perdemos)."
)
_SH_SEM_REEMBOLSO_TXT = "Devolução encerrada na Shopee SEM reembolso ao comprador (ganhamos)."
# 17/09 (Eduardo, 292317 "a Shopee não nos respondeu?"): a Shopee reembolsou o comprador 1 h
# depois da disputa, sem compensar a loja, e o histórico ficou mudo 24 h (só a coluna Status
# mudava). Diz na hora; a carência de 24 h continua valendo só pra FECHAR como perdido.
_SH_REEMBOLSO_SEM_COMP_TXT = (
    "Shopee REEMBOLSOU o comprador sem compensação à loja — a disputa foi recusada. "
    "Se a compensação não aparecer em 24 h, o chamado fecha como perdido."
)


def _fmt_dt(v) -> str:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str) and not v.strip().isdigit():
        dt = iso_to_dt(v)
    else:
        dt = epoch_to_dt(v)
    if dt is None:
        return ""
    try:
        return dt.astimezone(chamados_svc.SAO_PAULO).strftime("%d/%m %H:%M")
    except Exception:  # noqa: BLE001
        return ""


async def _ja_tem(
    session: AsyncSession, ch: Chamado, texto: str, *, direcao: str | None = "recebida"
) -> bool:
    q = select(ChamadoMensagem.id).where(
        ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.texto == texto
    )
    if direcao:
        q = q.where(ChamadoMensagem.direcao == direcao)
    return (await session.execute(q.limit(1))).scalar_one_or_none() is not None


async def registrar_recebida(
    session: AsyncSession, ch: Chamado, plat: str, texto: str, *, quando: datetime | None = None
) -> bool:
    """Grava a resposta da plataforma no histórico (uma vez por texto). `quando` =
    hora em que a plataforma/comprador falou de verdade (linha do tempo da TikTok):
    a mensagem entra com essa data, não com a da passada do cron — a nota original
    do comprador (anterior à nossa recusa) não pode virar "plataforma respondeu"
    depois que respondemos (18/09, 296936)."""
    bruto = (texto or "").strip()
    # 15/09: mediador do ML vem em HTML — grava legível. O dedupe olha as duas
    # formas: as mensagens antigas ficaram gravadas cruas e não podem duplicar.
    texto = limpar_html(bruto)
    if not texto or await _ja_tem(session, ch, texto):
        return False
    if bruto != texto and await _ja_tem(session, ch, bruto):
        return False
    msg = chamados_svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="resposta",
        direcao="recebida",
        autor_nome=AUTOR.get(plat, plat),
        status="registrada",
    )
    msg.canal = "api"
    if quando is not None:
        msg.created_at = quando
        msg.enviada_at = quando
    session.add(msg)
    return True


async def registrar_evento(session: AsyncSession, ch: Chamado, texto: str) -> bool:
    """Evento de sistema no histórico, uma vez por texto (qualquer direção — as
    linhas antigas têm o mesmo aviso gravado como `recebida`)."""
    if await _ja_tem(session, ch, texto, direcao=None):
        return False
    session.add(chamados_svc.registrar_sistema(ch, texto))
    return True


async def _encerrado_na_plataforma(
    session: AsyncSession, ch: Chamado, motivo: str, *, valor: Decimal | None = None
) -> None:
    """A plataforma encerrou o caso: estado Encerrado (19/09) — status oficial
    final (quem chamou já gravou ganhamos/perdemos; senão `encerrado`), a
    compensação como sugestão de valor e UM evento de sistema. Não fecha: a
    pessoa conclui pela aba. Chamado já resolvido: nada."""
    if ch.resolvido:
        return
    if ch.status_plataforma not in chamados_svc.STATUS_FINAIS:
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_ENCERRADO)
    # 19/09: com a decisão tomada, a réplica automática não tem mais a quem cobrar
    # — desliga aqui pra próxima passada do cron não enfileirar nada.
    ch.auto_ligada = False
    if valor is not None and ch.valor_sugerido is None:
        ch.valor_sugerido = valor
    await registrar_evento(
        session, ch, f"Plataforma encerrou o caso ({motivo}) — aguardando fechamento"
    )
    logger.info("chamado_devolucao_encerrado", chamado_id=str(ch.id), motivo=motivo)


async def _dev_de(session: AsyncSession, ch: Chamado) -> Devolution | None:
    if not ch.origem_ref:
        return None
    try:
        return await session.get(Devolution, UUID(str(ch.origem_ref)))
    except ValueError:
        return None


# ---------------------------------------------------------------- TikTok


def _tt_vivo(caso: dict) -> bool:
    return str(caso.get("return_status") or "").strip().upper() not in _TT_MORTOS


def _tiktok_caso_novo(casos: list[dict], caso: dict) -> dict | None:
    """O caso do MESMO pedido que o chamado deve passar a acompanhar: o mais
    novo entre os abertos depois deste, preferindo os que ainda estão VIVOS.

    A TikTok diz explicitamente qual é o sucessor quando o comprador edita o
    pedido (`next_return_id`); quando vier, ele manda. Senão vale a data de
    abertura — um caso vivo sempre ganha de um morto, porque é nele que a briga
    (e a nossa resposta) continua."""
    rid = str(caso.get("return_id") or "")
    por_id = {str(c.get("return_id") or ""): c for c in casos}
    seguinte = str(caso.get("next_return_id") or "").strip()
    if seguinte and seguinte != rid and seguinte in por_id:
        return por_id[seguinte]
    try:
        base = int(caso.get("create_time") or 0)
    except (TypeError, ValueError):
        base = 0
    novos = []
    for c in casos:
        if str(c.get("return_id") or "") == rid:
            continue
        try:
            criado = int(c.get("create_time") or 0)
        except (TypeError, ValueError):
            continue
        if criado > base:
            novos.append((criado, c))
    if not novos:
        return None
    vivos = [x for x in novos if _tt_vivo(x[1])]
    return max(vivos or novos, key=lambda x: x[0])[1]


async def _tt_estado_do_caso(
    session: AsyncSession, ch: Chamado, caso: dict, *, agora: datetime | None = None
) -> int:
    """Diz, na última linha do histórico, o que a TikTok está esperando AGORA.

    Vinicius 22/09 (293798): o painel parou em "Arbitragem encerrada na TikTok"
    e a equipe leu como caso acabado — enquanto no Seller Center o caso estava
    "Aguardando emissão de…", com 19h56 para APROVAÇÃO AUTOMÁTICA e um botão
    Responder. Arbitragem encerrada não é caso encerrado: ele volta pro fluxo
    normal e o relógio do reembolso volta a correr contra a loja.

    A TikTok entrega isso em `seller_next_action_response[].action/deadline` —
    o mesmo campo que a Logística já usa na coluna de prazo. Aqui vira fala da
    plataforma, então a aba manda o chamado pra Análise Humano em vez de
    deixá-lo parecendo resolvido. O prazo no texto é o que faz o dedupe: prazo
    novo, linha nova; mesmo prazo, nada se repete."""
    # Caso já encerrado não espera resposta de ninguém: se a TikTok deixar um
    # prazo velho no payload, ignoramos — dizer "esperando a nossa resposta —
    # concluído, reembolso pago" seria pior do que não dizer nada.
    if str(caso.get("return_status") or "").strip().upper() in _TT_FINALIZADOS:
        return 0
    acao, prazo = logistica_tiktok._acao_pendente(caso)
    if prazo is None:
        return 0
    status = str(caso.get("return_status") or "").strip().upper()
    situacao = logistica_rules.TIKTOK_RETURN_STATUS_LABELS_PT.get(
        status, _TT_STATUS_NOME.get(status, status.lower() or "situação desconhecida")
    )
    acao_pt = logistica_rules.acao_plataforma_pt("tiktok", acao) or "Responder na plataforma"
    prazo_txt = prazo.astimezone(chamados_svc.SAO_PAULO).strftime("%d/%m/%Y %H:%M")
    texto = (
        f"A TikTok está esperando a NOSSA resposta neste caso — {situacao}. "
        f"O que fazer: {acao_pt}. Prazo até {prazo_txt} — sem resposta, a TikTok "
        "aprova o reembolso ao comprador automaticamente."
    )
    return 1 if await registrar_recebida(session, ch, cd.PLAT_TIKTOK, texto, quando=agora) else 0


# O que a resposta automática do caso reaberto diz quando não sai (o código cru
# vai pro log).
_REABERTO_FALHA = {
    "devolucao_sem_video": "o lançamento não tem vídeo nem foto",
    "tiktok_motivo_indisponivel": "a TikTok não ofereceu o motivo de recusa",
}


async def _tt_responder_caso_reaberto(
    session: AsyncSession,
    ch: Chamado,
    dev: Devolution | None,
    client,
    caso: dict,
    *,
    agora: datetime,
    exigir_novidade: bool = False,
) -> int:
    """Responde SOZINHO o caso que a TikTok reabriu, com a mesma contestação do
    lançamento (mesmo vídeo, mesmas fotos, texto montado dos fatos da entrega).

    Vinicius 23/09 (293798, R$ 640,54): o lançamento recusou o primeiro caso em
    18/09, a compradora abriu disputa e o suporte da TikTok criou um caso novo
    esperando a nossa resposta até 23/09 08:24. O painel mostrou o prazo, mas
    ninguém respondeu — ele respondeu à mão 25 min antes. "3 pode fazer."

    Só quando: o lançamento já contestou um só reembolso neste chamado (a marca
    no histórico — devolução com pacote não entra, o texto seria outro); o caso
    está pendente da nossa resposta; e é a primeira vez pra esse (caso, prazo).
    Uma tentativa só: se não sair, o histórico diz por quê e a réplica do chamado
    responde (mesma recusa, com o texto digitado).

    `exigir_novidade`: o caso é o MESMO que o chamado já acompanhava — só conta
    como reaberto se a TikTok mexeu nele depois da nossa última resposta (senão é
    a lista da TikTok ainda atrasada, logo depois da recusa do lançamento)."""
    if dev is None or dev.id is None or not tiktok_reembolso.e_pendente_de_resposta(caso):
        return 0
    rid = str(caso.get("return_id") or "").strip()
    prazo = tiktok_reembolso.prazo_resposta(caso)
    if not rid or prazo is None:
        return 0
    abertura = await cd.mensagem_abertura(session, ch)
    if abertura is None or abertura.status != "enviada":
        return 0  # o lançamento ainda não respondeu: quem tenta é ele (cron de hora em hora)
    sistema = list(
        (
            await session.execute(
                select(ChamadoMensagem.texto).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
                )
            )
        ).scalars()
    )
    if not any((t or "").startswith(tiktok_reembolso.MARCA_AUTO) for t in sistema):
        return 0
    if exigir_novidade:
        ultima = await tiktok_reembolso.resposta_enviada(session, ch)
        feita = (ultima.enviada_at or ultima.created_at) if ultima is not None else None
        if feita is not None and feita.tzinfo is None:
            feita = feita.replace(tzinfo=UTC)
        mexeu = epoch_to_dt(caso.get("update_time"))
        if feita is not None and (mexeu is None or mexeu <= feita):
            return 0
    prazo_txt = (
        datetime.fromtimestamp(prazo, UTC)
        .astimezone(chamados_svc.SAO_PAULO)
        .strftime("%d/%m/%Y %H:%M")
    )
    marca = f"caso reaberto {rid} (prazo {prazo_txt})"
    if any(marca in (t or "") for t in sistema):
        return 0
    linhas = await cd._linhas_do_pedido(session, dev)
    fotos = [
        a for a in await cd.anexos_de(session, [d.id for d in linhas])
        if (a.content_type or "").lower() in cd.FOTO_TIPOS_IMAGEM
    ]
    rastreio = await cd._rastreio_devolucao(session, dev, cd.PLAT_TIKTOK)
    msg = chamados_svc.nova_mensagem(
        ch, texto="", tipo="replica", autor_nome=tiktok_reembolso.AUTOR_ROBO, status="enviada"
    )
    msg.canal = "api"
    try:
        await cd._recusar_reembolso(
            session, ch, dev, client, caso, fotos, rastreio, msg, None,
            rodada=str(prazo), como=f"sozinho no {marca}",
        )
    except Exception as e:  # noqa: BLE001 — erro da TikTok vira aviso no histórico
        code = str(getattr(e, "code", "") or e)[:200]
        logger.warning(
            "chamado_tiktok_caso_reaberto_falhou", chamado_id=str(ch.id), return_id=rid, err=code
        )
        session.add(
            chamados_svc.registrar_sistema(
                ch,
                f"A resposta automática do {marca} NÃO saiu ({_REABERTO_FALHA.get(code, code)})"
                " — responda pela réplica do chamado antes do prazo.",
            )
        )
        return 1
    # Depois da linha "A TikTok está esperando a NOSSA resposta" desta passada
    # (gravada com `agora`) — é a nossa resposta que fica por último.
    msg.created_at = msg.enviada_at = max(datetime.now(UTC), agora + timedelta(seconds=1))
    session.add(msg)
    logger.info("chamado_tiktok_caso_reaberto_respondido", chamado_id=str(ch.id), return_id=rid)
    return 1


async def _sync_tiktok(
    session: AsyncSession, ch: Chamado, dev: Devolution | None, *, agora: datetime | None = None
) -> int:
    dev_real = dev  # o sintético abaixo não tem linhas, fotos nem vídeo
    dev = dev or Devolution(conta=ch.conta or "", pedido_bling=ch.pedido_bling,
                            pedido_marketplace=ch.pedido_marketplace)
    client = await cd._tiktok_client_para(session, ch, dev)
    oid = (dev.pedido_marketplace or ch.pedido_marketplace or "").strip()
    rid = (ch.chamado or "").strip()
    casos = [c for c in await client.get_return_list(order_ids=[oid]) if isinstance(c, dict)]
    caso = next((c for c in casos if str(c.get("return_id") or "") == rid), None)
    if caso is None:
        return 0
    novos = 0
    status = str(caso.get("return_status") or "").strip().upper()
    arb = str(caso.get("arbitration_status") or "").strip().upper()
    quando = epoch_to_dt(caso.get("update_time"))
    agora = agora or datetime.now(UTC)
    if arb in _TT_ARB_TXT:
        txt, fim = _TT_ARB_TXT[arb]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
        if arb in _TT_ARB_STATUS:
            chamados_svc.set_status_plataforma(ch, _TT_ARB_STATUS[arb], quando)
    if status in _TT_RECUSA_NOSSA:
        # A nossa recusa, confirmada pela TikTok: evento (não é fala da plataforma).
        novos += await registrar_evento(session, ch, _TT_RECUSA_NOSSA[status])
        if not arb:
            if quando is not None and agora - quando >= TT_RECUSA_CARENCIA:
                novos += await registrar_recebida(
                    session, ch, cd.PLAT_TIKTOK, _TT_RECUSA_VENCIDA_TXT
                )
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, agora)
                await _encerrado_na_plataforma(session, ch, f"tiktok:{status}:sem_recurso")
            else:
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_AGUARDANDO, quando)
    # 22/09 (293798): antes só um caso CANCELADO cedia a vez. Agora qualquer caso
    # morto cede — recusado, concluído, reembolsado — desde que exista um caso
    # VIVO mais novo do mesmo pedido. Arbitragem ganha por nós (SUPPORT_SELLER)
    # continua fora: ali o caso acabou a nosso favor e caso novo é briga nova.
    novo = None
    if status in _TT_MORTOS and arb != "SUPPORT_SELLER":
        candidato = _tiktok_caso_novo(casos, caso)
        if candidato is not None and (
            status == "RETURN_OR_REFUND_REQUEST_CANCEL" or _tt_vivo(candidato)
        ):
            novo = candidato
    if novo is not None:
        # Medido 18/09 (jlas 585710261573748632): o comprador EDITOU o pedido — a
        # TikTok cancela a solicitação antiga e cria outra. Não é ganhamos: a
        # briga continua no caso novo, e é ele que o chamado passa a acompanhar
        # (a réplica do chamado responde o caso novo).
        novo_id = str(novo.get("return_id") or "")
        st_novo = str(novo.get("return_status") or "").upper()
        tipo = _TT_TIPO.get(str(novo.get("return_type") or "").upper(), "solicitação")
        situacao = _TT_STATUS_NOME.get(st_novo, st_novo.lower() or "situação desconhecida")
        if status == "RETURN_OR_REFUND_REQUEST_CANCEL":
            aviso = (
                f"O comprador refez o pedido na TikTok: {tipo} {novo_id} ({situacao}) — a "
                f"solicitação {rid} foi cancelada por isso. O chamado passa a acompanhar o "
                "caso novo."
            )
        else:
            aviso = (
                f"O comprador ABRIU OUTRO caso na TikTok depois que o anterior terminou: "
                f"{tipo} {novo_id} ({situacao}). A solicitação {rid} está "
                f"{_TT_STATUS_NOME.get(status, status.lower())}. O chamado passa a "
                "acompanhar o caso novo — confira se ainda dá pra responder."
            )
        novos += await registrar_recebida(
            session, ch, cd.PLAT_TIKTOK, aviso,
            quando=epoch_to_dt(novo.get("create_time")),
        )
        ch.chamado = novo_id
        # O caso novo entra JÁ nesta passada (antes a linha do tempo dele só
        # aparecia uma hora depois, e a do caso velho nunca mais era lida).
        novos += await _tiktok_linha_do_tempo(session, ch, client, novo_id)
        novos += await _tiktok_conversa_do_pedido(session, ch, client, casos, novo_id)
        novos += await _tt_estado_do_caso(session, ch, novo, agora=agora)
        novos += await _tt_responder_caso_reaberto(session, ch, dev_real, client, novo, agora=agora)
        return novos
    if status in _TT_STATUS_TXT:
        txt, fim = _TT_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
        if status in _TT_STATUS_STATUS:
            chamados_svc.set_status_plataforma(ch, _TT_STATUS_STATUS[status], quando)
        if fim:
            await _encerrado_na_plataforma(session, ch, f"tiktok:{status}")
    novos += await _tiktok_linha_do_tempo(session, ch, client, rid)
    novos += await _tiktok_conversa_do_pedido(session, ch, client, casos, rid)
    # Por último, o que a plataforma espera AGORA: é a linha que a equipe lê
    # primeiro e a que decide se o chamado está mesmo parado ou correndo prazo.
    novos += await _tt_estado_do_caso(session, ch, caso, agora=agora)
    novos += await _tt_responder_caso_reaberto(
        session, ch, dev_real, client, caso, agora=agora, exigir_novidade=True
    )
    return novos


def _tt_texto_do_registro(r: dict) -> str:
    """TUDO que veio escrito naquele evento, sem repetir. Vinicius 22/09: "na
    tela do TikTok a mulher escreve, não veio a escrita" — a API manda até três
    campos de texto por evento (`note` livre, `description` do evento e
    `reason_text` do motivo escolhido) e o código lia só o primeiro que
    estivesse preenchido, então o que ela digitou sumia atrás do rótulo."""
    partes: list[str] = []
    for campo in ("note", "comment", "description", "reason_text"):
        t = str(r.get(campo) or "").strip()
        if t and not any(t.lower() == p.lower() for p in partes):
            partes.append(t)
    return " — ".join(partes)


def _tt_midia_do_registro(r: dict) -> str:
    """Fotos e vídeo que o COMPRADOR anexou (a TikTok só deixa comprador enviar
    vídeo). Vinicius 22/09: "a mulher mandou vídeo, não veio o vídeo" — vinha no
    mesmo pacote e era descartado. Vai o link: o arquivo fica na TikTok."""
    def _urls(chaves: tuple[str, ...]) -> list[str]:
        out: list[str] = []
        for chave in chaves:
            for item in r.get(chave) or []:
                url = ""
                if isinstance(item, dict):
                    url = str(item.get("url") or item.get("image_url") or "").strip()
                elif isinstance(item, str):
                    url = item.strip()
                if url and url not in out:
                    out.append(url)
        return out

    fotos = _urls(("images", "image_list"))
    videos = _urls(("videos", "video_list"))
    if not fotos and not videos:
        return ""
    contagem = []
    if videos:
        contagem.append(f"{len(videos)} vídeo" + ("s" if len(videos) > 1 else ""))
    if fotos:
        contagem.append(f"{len(fotos)} foto" + ("s" if len(fotos) > 1 else ""))
    return "Anexou " + " e ".join(contagem) + ": " + " | ".join(videos + fotos)


async def _tiktok_linha_do_tempo(
    session: AsyncSession, ch: Chamado, client, rid: str, *, etiqueta: str = ""
) -> int:
    """Linha do tempo de UM caso: o que o comprador e a TikTok escreveram, com
    os anexos. Best-effort — o acompanhamento não pode cair por causa dela."""
    rid = (rid or "").strip()
    if not rid:
        return 0
    try:
        registros = await client.get_return_records(rid)
    except Exception as e:  # noqa: BLE001
        logger.info(
            "chamado_devolucao_tiktok_records_falhou", chamado_id=str(ch.id), err=str(e)[:120]
        )
        return 0
    novos = 0
    for r in registros:
        if not isinstance(r, dict):
            continue
        papel = str(r.get("role") or "").upper()
        if papel == "SELLER":
            continue
        nota = _tt_texto_do_registro(r)
        midia = _tt_midia_do_registro(r)
        if not nota and not midia:
            continue
        quem = {"BUYER": "Comprador", "OPERATOR": "TikTok (operador)", "SYSTEM": "TikTok"}.get(
            papel, papel or "TikTok"
        )
        quando = _fmt_dt(r.get("create_time"))
        corpo = " ".join(t for t in (nota, midia) if t)
        novos += await registrar_recebida(
            session, ch, cd.PLAT_TIKTOK,
            f"{quem}{etiqueta}{(' ' + quando) if quando else ''}: {corpo}",
            quando=epoch_to_dt(r.get("create_time")),
        )
    return novos


# Quantos casos ANTERIORES do mesmo pedido têm a conversa relida por passada.
TT_CASOS_ANTERIORES = 4


async def _tiktok_conversa_do_pedido(
    session: AsyncSession, ch: Chamado, client, casos: list[dict], rid: str
) -> int:
    """A conversa de TODOS os casos do pedido, não só a do que o chamado
    acompanha. Vinicius 22/09: "vai pegar tudo o que a mulher falou? é isso que
    preciso". Quando o comprador refaz o pedido, a TikTok cria outro caso e o
    que ele escreveu no anterior fica num `return_id` que ninguém relê — pior
    ainda porque até hoje o código lia só o primeiro campo de texto de cada
    evento, então o que ficou gravado antes está incompleto. As falas dos casos
    anteriores entram com o número do caso no rótulo, pra ninguém confundir com
    a conversa atual, e o dedupe por texto evita repetição."""
    novos = 0
    outros = [
        str(c.get("return_id") or "")
        for c in casos
        if str(c.get("return_id") or "") and str(c.get("return_id") or "") != rid
    ]
    for outro in outros[:TT_CASOS_ANTERIORES]:
        novos += await _tiktok_linha_do_tempo(
            session, ch, client, outro, etiqueta=f" (caso {outro})"
        )
    return novos


# ---------------------------------------------------------------- Shopee


PROVA_PREFIXO = "Prova adicional enviada à Shopee"
_SEM_FOTO_TXT = (
    "A Shopee pediu prova adicional e a devolução não tem foto no DaVinci — anexar as fotos "
    "na tela Devoluções (o robô envia sozinho na próxima passada) ou subir pelo Seller Center."
)


async def _enviar_prova_shopee(
    session: AsyncSession, ch: Chamado, dev: Devolution | None, client, det: dict
) -> bool:
    """Shopee pediu prova extra (`seller_proof` PENDING): manda as fotos da
    devolução + o texto da abertura pela API (`upload_proof`), UMA vez por
    pedido de prova (Eduardo 09/09: o robô toma conta; caso Mega/260827DBUMDT1W
    venceria sem ninguém subir). Sem foto na devolução, avisa no histórico."""
    return_sn = (ch.chamado or "").strip()
    if not return_sn or dev is None:
        return False
    msgs = (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at, ChamadoMensagem.id)
        )
    ).scalars().all()
    ja_enviada = False
    for m in msgs:
        if m.direcao == "recebida" and "PROVA ADICIONAL" in (m.texto or ""):
            ja_enviada = False  # pedido novo de prova reabre o ciclo
        elif m.direcao == "enviada" and (m.texto or "").startswith(PROVA_PREFIXO):
            ja_enviada = m.status in ("enviada", "pendente", "enviando")
    if ja_enviada:
        return False
    linhas = await cd._linhas_do_pedido(session, dev)
    anexos = await cd.anexos_de(session, [d.id for d in linhas] or [dev.id])
    fotos = [a for a in anexos if (a.content_type or "").lower() in cd.FOTO_TIPOS_IMAGEM]
    if not fotos:
        if not any(m.tipo == "sistema" and m.texto == _SEM_FOTO_TXT for m in msgs):
            session.add(chamados_svc.registrar_sistema(ch, _SEM_FOTO_TXT))
        return False
    abertura = await cd.mensagem_abertura(session, ch)
    texto = (abertura.texto if abertura and abertura.texto else "").strip() or (
        "Segue evidência adicional da contestação da devolução."
    )
    msg = chamados_svc.nova_mensagem(
        ch,
        texto=f"{PROVA_PREFIXO} ({len(fotos[:5])} foto(s)): {texto}",
        tipo="replica",
        direcao="enviada",
        autor_nome=AUTOR_ACOMP,
        status="pendente",
    )
    msg.canal = "api"
    session.add(msg)
    await session.flush()
    try:
        urls: list[str] = []
        for a in fotos[:5]:
            url = cd._ref_foto(a).get("ref")
            if not url:
                url = await cd._subir_foto_shopee(client, return_sn, a)
                a.ml_file_name = url
                await session.flush()
            urls.append(url)
        await client.upload_proof(return_sn, proof_text=[texto[:1000]], proof_image=urls)
    except Exception as e:  # noqa: BLE001
        msg.status = "falhou"
        msg.erro = str(e)[:300]
        logger.warning(
            "chamado_devolucao_shopee_prova_falhou", chamado_id=str(ch.id), err=str(e)[:200]
        )
        return False
    msg.status = "enviada"
    msg.enviada_at = datetime.now(UTC)
    logger.info("chamado_devolucao_shopee_prova_enviada", chamado_id=str(ch.id), fotos=len(urls))
    return True


def _brl(v: Decimal) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _numero(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ajustes_escrow(esc: dict) -> list[dict]:
    """`order_adjustment` do escrow — vem dentro de `order_income` (API) ou na
    raiz (payloads antigos/teste)."""
    ajustes = (
        esc.get("order_adjustment") or (esc.get("order_income") or {}).get("order_adjustment") or []
    )
    return [a for a in ajustes if isinstance(a, dict)] if isinstance(ajustes, list) else []


def _compensacoes_pagas(esc: dict) -> list[tuple[Decimal, str, str]]:
    """Compensação que a Shopee JÁ PAGOU ao vendedor: `order_adjustment` do escrow
    com motivo "... Compensation" e valor positivo → [(valor, dd/mm, motivo)].

    Medido 17/09 (288567/290985/289899, disputas abertas À MÃO no Seller Center):
    o `get_return_detail` fica ACCEPTED com `seller_compensation_status` VAZIO para
    sempre — só o escrow mostra "Logistics Related Compensation" com valor e data.
    """
    out = []
    for a in _ajustes_escrow(esc):
        motivo = str(a.get("adjustment_reason") or "")
        try:
            valor = Decimal(str(a.get("amount")))
        except (InvalidOperation, ValueError):
            continue
        if "compensation" in motivo.lower() and valor > 0:
            out.append((valor, _fmt_dt(a.get("date")), motivo))
    return out


def _reembolso_pago(esc: dict) -> tuple[bool, datetime | None]:
    """O escrow já desconta o reembolso ao comprador? `seller_return_refund`
    negativo (medido 17/09: -779 no 2608300N7X5K2HF) ou ajuste negativo de
    reembolso ("Ajuste após reembolso aprovado", 2608300N932530Q). Devolve
    também a data do ajuste, quando há."""
    oi = esc.get("order_income") if isinstance(esc.get("order_income"), dict) else {}
    pago = _numero(oi.get("seller_return_refund")) < 0
    quando: datetime | None = None
    for a in _ajustes_escrow(esc):
        motivo = str(a.get("adjustment_reason") or "").lower()
        if _numero(a.get("amount")) < 0 and ("reembolso" in motivo or "refund" in motivo):
            pago = True
            quando = quando or epoch_to_dt(a.get("date"))
    return pago, quando


async def _sync_shopee(session: AsyncSession, ch: Chamado, dev: Devolution | None) -> int:
    dev_q = dev or Devolution(conta=ch.conta or "", pedido_bling=ch.pedido_bling,
                              pedido_marketplace=ch.pedido_marketplace)
    client = await cd._shopee_client_para(session, ch, dev_q)
    det = await client.get_return_detail((ch.chamado or "").strip())
    novos = 0
    status = str(det.get("status") or "").strip().upper()
    prova = det.get("seller_proof") or {}
    if str(prova.get("seller_proof_status") or "").upper() == "PENDING":
        prazo = _fmt_dt(prova.get("seller_evidence_deadline"))
        novos += await registrar_recebida(
            session, ch, cd.PLAT_SHOPEE,
            "A Shopee pediu PROVA ADICIONAL na disputa"
            + (f" — prazo até {prazo}" if prazo else "")
            + ". Anexar pelo Seller Center (Devolução e Reembolso).",
        )
        await session.flush()
        if await _enviar_prova_shopee(session, ch, dev, client, det):
            novos += 1
    quando = epoch_to_dt(det.get("update_time"))
    if str(prova.get("seller_proof_status") or "").upper() == "PENDING":
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_PROVA, quando)
    comp = det.get("seller_compensation") or {}
    comp_status = (
        str(comp.get("seller_compensation_status") or "").upper().replace("COMPENSATION_", "")
    )
    valor = _numero(comp.get("compensation_amount"))
    if comp_status in _SH_COMP_TXT:
        txt, fim = _SH_COMP_TXT[comp_status]
        if comp_status == "APPROVED" and valor > 0:
            txt = f"{txt} Valor: R$ {comp.get('compensation_amount')}."
        novos += await registrar_recebida(session, ch, cd.PLAT_SHOPEE, txt)
        chamados_svc.set_status_plataforma(ch, _SH_COMP_STATUS[comp_status], quando)
        if fim:
            await _encerrado_na_plataforma(session, ch, f"shopee:comp:{comp_status}")
    elif status in ("SELLER_DISPUTE", "JUDGING"):
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_EM_ANALISE, quando)
    # 17/09 (Eduardo, 288567 "ganhamos e o robô deixou na aba"; Vinicius, 292128
    # "perdi ou ganhei?"): no BR a decisão da disputa não muda o return — só o
    # escrow do pedido conta a história (compensação paga / reembolso ao comprador).
    tem_disputa = bool(det.get("dispute_reason")) or valor > 0
    if ch.status_plataforma not in chamados_svc.STATUS_FINAIS and tem_disputa:
        novos += await _desfecho_shopee(
            session, ch, client, det, dev_q, status, quando, valor, comp_status=comp_status
        )
    if status in _SH_STATUS_TXT:
        txt, fim = _SH_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_SHOPEE, txt)
        if fim:
            if status == "CANCELLED":
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
            elif not ch.status_plataforma:
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_ENCERRADO, quando)
            await _encerrado_na_plataforma(session, ch, f"shopee:{status}")
    return novos


async def _disputa_registrada_em(session: AsyncSession, ch: Chamado) -> datetime | None:
    """Quando a NOSSA disputa entrou na Shopee (abertura enviada pela API)."""
    ab = await cd.mensagem_abertura(session, ch)
    if ab is None or ab.status != "enviada":
        return None
    return ab.enviada_at or ab.created_at


async def _desfecho_shopee(
    session: AsyncSession, ch: Chamado, client, det: dict, dev_q: Devolution,
    status: str, quando: datetime | None, valor: float, *, comp_status: str = "",
) -> int:
    """Desfecho da disputa pelo escrow do pedido (uma leitura):
    - compensação paga → ganhamos, fecha com o valor recuperado;
    - `compensation_amount` no return sem ajuste no escrow ainda → ganhamos também;
    - comprador reembolsado DEPOIS da disputa e nada pra loja → "reembolso pago";
      passada a carência (`_SH_PERDEMOS_CARENCIA`) ou com o return CLOSED →
      perdemos, fecha — a menos que o pedido de compensação siga em análise
      (`seller_compensation_status` REQUESTED: a Shopee ainda não decidiu);
    - reembolso ANTERIOR à disputa (só reembolso já pago quando contestamos) não
      é resposta a ela: 18/09 (289545) o chamado fechou como perdido 7 s depois
      de aberto porque o reembolso era de 3 dias antes;
    - CLOSED sem reembolso ao comprador → ganhamos.
    Escrow indisponível = não decide (o resto do sync vale)."""
    oid = (dev_q.pedido_marketplace or ch.pedido_marketplace or det.get("order_sn") or "").strip()
    if not oid:
        return 0
    try:
        esc = await client.get_escrow_detail(oid) or {}
    except Exception as e:  # noqa: BLE001 — escrow é apoio; o resto do sync vale
        logger.info("chamado_devolucao_shopee_escrow_falhou", chamado_id=str(ch.id), err=str(e)[:120])
        return 0
    pagas = _compensacoes_pagas(esc)
    if pagas:
        total = sum((v for v, _, _ in pagas), Decimal("0"))
        partes = "; ".join(f"{_brl(v)}{(' em ' + d) if d else ''} ({m})" for v, d, m in pagas)
        novos = await registrar_recebida(
            session, ch, cd.PLAT_SHOPEE,
            f"Shopee PAGOU a compensação ao vendedor — disputa ganha: {partes}.",
        )
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
        await _encerrado_na_plataforma(session, ch, "shopee:compensacao_paga", valor=total)
        return novos
    if valor > 0:
        txt, _fim = _SH_COMP_TXT["APPROVED"]
        novos = await registrar_recebida(
            session, ch, cd.PLAT_SHOPEE,
            f"{txt} Valor: R$ {det.get('seller_compensation', {}).get('compensation_amount')}.",
        )
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
        await _encerrado_na_plataforma(
            session, ch, "shopee:comp:valor", valor=Decimal(str(valor))
        )
        return novos
    reembolsado, reembolsado_em = _reembolso_pago(esc)
    if not reembolsado:
        if status == "CLOSED":
            chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
            return await registrar_recebida(session, ch, cd.PLAT_SHOPEE, _SH_SEM_REEMBOLSO_TXT)
        return 0
    disputa_em = await _disputa_registrada_em(session, ch)
    if reembolsado_em is not None and disputa_em is not None and reembolsado_em < disputa_em:
        return 0  # reembolso de antes da disputa: a Shopee ainda não respondeu
    chamados_svc.set_status_plataforma(
        ch, chamados_svc.STATUS_REEMBOLSO_PAGO, reembolsado_em or quando
    )
    avisou = await registrar_recebida(session, ch, cd.PLAT_SHOPEE, _SH_REEMBOLSO_SEM_COMP_TXT)
    desde = ch.status_plataforma_at or datetime.now(UTC)
    if status != "CLOSED" and (
        datetime.now(UTC) - desde < _SH_PERDEMOS_CARENCIA or comp_status == "REQUESTED"
    ):
        return int(avisou)
    novos = int(avisou) + await registrar_recebida(session, ch, cd.PLAT_SHOPEE, _SH_PERDEMOS_TXT)
    chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_PERDEMOS, desde)
    await _encerrado_na_plataforma(session, ch, "shopee:reembolso_sem_compensacao")
    return novos


# ---------------------------------------------------------------- Mercado Livre


async def _sync_ml(session: AsyncSession, ch: Chamado, dev: Devolution | None) -> int:
    dev = dev or Devolution(conta=ch.conta or "", pedido_bling=ch.pedido_bling,
                            pedido_marketplace=ch.pedido_marketplace)
    client = await cd._ml_client_para(session, ch, dev)
    claim_id = (ch.chamado or "").strip()
    claim = await client.get_claim(claim_id) or {}
    novos = 0
    try:
        msgs = await client.get_claim_messages(claim_id)
    except Exception as e:  # noqa: BLE001
        logger.info(
            "chamado_devolucao_ml_messages_falhou", chamado_id=str(ch.id), err=str(e)[:120]
        )
        msgs = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        papel = str(m.get("sender_role") or (m.get("sender") or {}).get("role") or "").lower()
        if papel == "respondent":
            continue
        texto = str(m.get("message") or m.get("text") or "").strip()
        if not texto:
            continue
        quem = {"mediator": "Mediador do ML", "complainant": "Comprador"}.get(papel, papel or "ML")
        quando = _fmt_dt(m.get("date_created"))
        novos += await registrar_recebida(
            session, ch, cd.PLAT_ML, f"{quem}{(' ' + quando) if quando else ''}: {texto}"
        )
    if (claim.get("status") or "").lower() == "closed":
        res = claim.get("resolution") or {}
        benef = chamados_svc.ml_beneficiado(claim)
        quem = {"respondent": "a favor do VENDEDOR", "complainant": "a favor do COMPRADOR"}.get(
            benef, "sem beneficiado informado"
        )
        motivo = str(res.get("reason") or "").strip()
        novos += await registrar_recebida(
            session, ch, cd.PLAT_ML,
            f"Reclamação encerrada no Mercado Livre — decisão {quem}"
            + (f" (motivo: {motivo})" if motivo else "") + ".",
        )
        chamados_svc.set_status_plataforma(
            ch, chamados_svc.ml_status_encerrado(claim), chamados_svc.ml_quando(claim)
        )
        await _encerrado_na_plataforma(session, ch, "ml:closed")
    elif (claim.get("stage") or "").lower() == "dispute":
        # Mediação: o ML entrou como juiz — em análise até o claim fechar.
        chamados_svc.set_status_plataforma(
            ch, chamados_svc.STATUS_EM_ANALISE, chamados_svc.ml_quando(claim)
        )
    return novos


# ---------------------------------------------------------------- cron


# 19/09: a plataforma já decidiu (Encerrado) — não há mais o que ler; o chamado
# fica esperando a pessoa concluir e sai da varredura (antes saía por `resolvido`).
# O mesmo filtro vale pra réplica automática e pro reenvio de abertura pendente —
# mora em `chamados_svc` pra todo cron usar o mesmo.
_nao_encerrado = chamados_svc.NAO_ENCERRADO_SQL
# 22/09: o número em `chamados.chamado` foi capturado pelo robô na TELA — nenhuma
# API sabe responder por ele. Quem lê esses casos é o `chamados_leitura`.
_aberto_na_tela = chamados_svc.CASO_DE_TELA_SQL


async def sync_um(
    session: AsyncSession, ch: Chamado, *, agora: datetime | None = None
) -> dict:
    """Relê ESTE chamado na plataforma agora, sem esperar o cron das :25.

    Vinicius 22/09: "não consegue subir e dar um rodar agora? pra já testar
    novamente?" — e vale muito além do teste: caso com prazo correndo (a TikTok
    aprova o reembolso sozinha) não pode depender de uma janela de uma hora.
    Mesmas funções do cron, um chamado só; não mexe em quem não tem plataforma
    com API. Commita."""
    plat = cd.plataforma_de(ch.plataforma)
    fn = {cd.PLAT_TIKTOK: _sync_tiktok, cd.PLAT_SHOPEE: _sync_shopee, cd.PLAT_ML: _sync_ml}.get(
        plat
    )
    if fn is None:
        return {"plataforma": plat, "novos": 0, "lido": False, "erro": "plataforma_sem_api"}
    try:
        dev = await _dev_de(session, ch)
        if fn is _sync_tiktok:
            novos = await _sync_tiktok(session, ch, dev, agora=agora)
        else:
            novos = await fn(session, ch, dev)
    except Exception as e:  # noqa: BLE001 — o erro da plataforma vira resposta da tela
        # 22/09: o `str(ch.id)` do log tem que ser lido ANTES do rollback — ele
        # expira os atributos da linha e reler `ch.id` aqui dispara IO fora do
        # greenlet (MissingGreenlet). O tratamento do erro quebrava, e o que
        # chegava na tela era 500 em vez do motivo da plataforma.
        cid = str(ch.id)
        await session.rollback()
        logger.warning(
            "chamado_devolucao_sync_um_falhou",
            chamado_id=cid, plataforma=plat, err=str(e)[:200],
        )
        return {"plataforma": plat, "novos": 0, "lido": False, "erro": str(e)[:200]}
    await session.commit()
    logger.info("chamado_devolucao_sync_um", chamado_id=str(ch.id), plataforma=plat, novos=novos)
    return {"plataforma": plat, "novos": novos, "lido": True, "erro": None}


async def sync_respostas(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Passada do cron: chamados de devolução ABERTOS via API (abertura enviada)
    e ainda sem decisão da plataforma → consulta a plataforma, grava respostas
    novas e põe os decididos no estado Encerrado (`encerrados` conta os que
    mudaram nesta passada). Best-effort por chamado; commita no fim."""
    rows = (
        await session.execute(
            select(Chamado, ChamadoMensagem)
            .join(ChamadoMensagem, ChamadoMensagem.chamado_id == Chamado.id)
            .where(
                Chamado.origem == "devolucao",
                Chamado.canal == "api",
                Chamado.resolvido.is_(False),
                _nao_encerrado,
                Chamado.chamado.is_not(None),
                # 22/09 (292592): caso ABERTO NA TELA não tem o que ser lido aqui —
                # o número guardado é protocolo de tela e a API responde "essa
                # devolução não existe" de hora em hora, o que virava falha falsa
                # na Ouvidoria. Quem lê esses é o robô (`chamados_leitura`).
                ~_aberto_na_tela,
                ChamadoMensagem.tipo == cd.TIPO_ABERTURA,
                or_(
                    ChamadoMensagem.status == "enviada",
                    # 17/09: abertura que "falhou" porque a disputa já existia (feita à
                    # mão), o prazo venceu ou o caso já fechou também tem desfecho na
                    # plataforma — sem isto o chamado ficava aberto pra sempre (288567).
                    and_(ChamadoMensagem.status == "falhou",
                         ChamadoMensagem.erro.in_(ABERTURA_FALHOU_ACOMPANHA)),
                ),
            )
            .order_by(Chamado.created_at)
        )
    ).all()
    # 14/09 (Eduardo, "agente tem que cobrir todos os chamados da aba"): reclamação
    # do Mercado Livre aberta FORA da devolução (origem logística/manual, canal API,
    # nº do claim de 10 dígitos) também é acompanhada pela API — `_sync_ml` já
    # funciona sem Devolution. Antes ficava "fora" da cobertura (2 casos em 14/09).
    extras = (
        await session.execute(
            select(Chamado).where(
                Chamado.canal == "api",
                Chamado.resolvido.is_(False),
                _nao_encerrado,
                ~_aberto_na_tela,  # idem: TikTok/ML abertos na tela saem daqui
                Chamado.origem != "devolucao",
                or_(
                    and_(
                        Chamado.chamado.op("~")(r"^\d{10}$"),
                        func.lower(func.coalesce(Chamado.plataforma, "")).in_(
                            ("ml", "mercado livre", "mercadolivre", "meli")
                        ),
                    ),
                    # 15/09 (Eduardo: "as consultas manuais têm que ser feitas também"):
                    # disputa de devolução do TikTok aberta fora da devolução (origem
                    # logística) com o return_id no lugar do protocolo — `_sync_tiktok`
                    # lê status, arbitragem e a linha do tempo pela API.
                    and_(
                        Chamado.chamado.op("~")(r"^\d{15,}$"),
                        func.lower(func.coalesce(Chamado.plataforma, "")) == "tiktok",
                    ),
                ),
            )
        )
    ).scalars().all()
    rows = list(rows) + [(ch, None) for ch in extras]
    vistos: set[UUID] = set()
    verificados = novos = encerrados = falhas = 0
    for ch, _msg in rows:
        if ch.id in vistos:
            continue
        vistos.add(ch.id)
        plat = cd.plataforma_de(ch.plataforma)
        fn = {
            cd.PLAT_TIKTOK: _sync_tiktok, cd.PLAT_SHOPEE: _sync_shopee, cd.PLAT_ML: _sync_ml
        }.get(plat)
        if fn is None:
            continue
        verificados += 1
        try:
            dev = await _dev_de(session, ch)
            if fn is _sync_tiktok:
                n = await _sync_tiktok(session, ch, dev, agora=agora)
            else:
                n = await fn(session, ch, dev)
            novos += n
            if ch.status_plataforma in chamados_svc.STATUS_FINAIS:
                encerrados += 1
            await session.flush()
        except Exception as e:  # noqa: BLE001
            falhas += 1
            logger.warning(
                "chamado_devolucao_sync_falhou",
                chamado_id=str(ch.id),
                plataforma=plat,
                err=str(e)[:200],
            )
    await session.commit()
    out = {"verificados": verificados, "novos": novos, "encerrados": encerrados, "falhas": falhas}
    logger.info("chamado_devolucao_sync_done", **out, agora=datetime.now(UTC).isoformat())
    return out
