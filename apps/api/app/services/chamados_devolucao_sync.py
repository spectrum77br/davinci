"""Acompanhamento das contestações de devolução abertas pela API — a RESPOSTA
da plataforma cai no histórico do chamado e o chamado fecha sozinho.

Eduardo 04/09: "a resposta tem que chegar no histórico tbm". Cron de hora em
hora (junto do `chamados_replica_automatica`): pra cada chamado de origem
`devolucao`, canal `api`, aberto e já ENVIADO, consulta a plataforma:

- **TikTok**: returns/search por order_id → `return_status` / `arbitration_status`
  (REJECT_RECEIVE_PACKAGE = recusa registrada; IN_PROGRESS = comprador contestou;
  SUPPORT_SELLER/SUPPORT_BUYER = decisão; RETURN_OR_REFUND_REQUEST_CANCEL =
  vendedor ficou com o valor; ..._SUCCESS/_COMPLETE = reembolsado) + linha do
  tempo (`returns/{id}/records`: notas do comprador/plataforma).
- **Shopee**: get_return_detail → `status` (SELLER_DISPUTE/JUDGING/CLOSED…),
  `seller_proof` (Shopee pediu prova extra + prazo), `seller_compensation`
  (APPROVED/REJECTED = decisão).
- **Mercado Livre**: claim (status/resolution) + mensagens do mediador/comprador
  (`claims/{id}/messages`).

Cada estado/mensagem novo vira UMA mensagem `recebida` no histórico (dedupe
pelo texto — o cron pode rodar quantas vezes quiser); estado final marca o
chamado como resolvido com um evento de sistema. Best-effort por chamado.

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
    "REJECT_RECEIVE_PACKAGE": (
        "Recusa do pacote registrada na TikTok. O comprador pode contestar (arbitragem) "
        "no prazo da plataforma.",
        False,
    ),
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
    "REFUND_OR_RETURN_REQUEST_REJECT": ("Solicitação de devolução recusada na TikTok.", True),
}
_TT_ARB_TXT: dict[str, tuple[str, bool]] = {
    "IN_PROGRESS": ("O comprador contestou a recusa: caso em ARBITRAGEM na TikTok.", False),
    "SUPPORT_SELLER": ("Arbitragem da TikTok decidida A FAVOR DO VENDEDOR.", False),
    "SUPPORT_BUYER": ("Arbitragem da TikTok decidida a favor do COMPRADOR (reembolso).", False),
    "CLOSED": ("Arbitragem encerrada na TikTok.", False),
}
# Coluna Status da aba (17/09): o que cada estado da TikTok significa pra loja.
_TT_STATUS_STATUS: dict[str, str] = {
    "RETURN_OR_REFUND_REQUEST_CANCEL": chamados_svc.STATUS_GANHAMOS,
    "REFUND_OR_RETURN_REQUEST_REJECT": chamados_svc.STATUS_GANHAMOS,
    "RETURN_OR_REFUND_REQUEST_SUCCESS": chamados_svc.STATUS_PERDEMOS,
    "RETURN_OR_REFUND_REQUEST_COMPLETE": chamados_svc.STATUS_PERDEMOS,
}
_TT_ARB_STATUS: dict[str, str] = {
    "IN_PROGRESS": chamados_svc.STATUS_EM_ANALISE,
    "SUPPORT_SELLER": chamados_svc.STATUS_GANHAMOS,
    "SUPPORT_BUYER": chamados_svc.STATUS_PERDEMOS,
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


async def _ja_tem(session: AsyncSession, ch: Chamado, texto: str) -> bool:
    return (
        await session.execute(
            select(ChamadoMensagem.id).where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.direcao == "recebida",
                ChamadoMensagem.texto == texto,
            ).limit(1)
        )
    ).scalar_one_or_none() is not None


async def registrar_recebida(session: AsyncSession, ch: Chamado, plat: str, texto: str) -> bool:
    """Grava a resposta da plataforma no histórico (uma vez por texto)."""
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
    session.add(msg)
    return True


def _encerrar(
    session: AsyncSession, ch: Chamado, motivo: str, *, valor: Decimal | None = None
) -> None:
    if ch.resolvido:
        return
    session.add(chamados_svc.marcar_resolvido(ch, True, autor_nome=AUTOR_ACOMP, valor=valor))
    logger.info("chamado_devolucao_encerrado", chamado_id=str(ch.id), motivo=motivo)


async def _dev_de(session: AsyncSession, ch: Chamado) -> Devolution | None:
    if not ch.origem_ref:
        return None
    try:
        return await session.get(Devolution, UUID(str(ch.origem_ref)))
    except ValueError:
        return None


# ---------------------------------------------------------------- TikTok


async def _sync_tiktok(session: AsyncSession, ch: Chamado, dev: Devolution | None) -> int:
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
    if arb in _TT_ARB_TXT:
        txt, fim = _TT_ARB_TXT[arb]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
        if arb in _TT_ARB_STATUS:
            chamados_svc.set_status_plataforma(ch, _TT_ARB_STATUS[arb], quando)
    if status in _TT_STATUS_TXT:
        txt, fim = _TT_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
        if status in _TT_STATUS_STATUS:
            chamados_svc.set_status_plataforma(ch, _TT_STATUS_STATUS[status], quando)
        if fim:
            _encerrar(session, ch, f"tiktok:{status}")
    # linha do tempo: notas do comprador / da plataforma (best-effort)
    try:
        registros = await client.get_return_records(rid)
    except Exception as e:  # noqa: BLE001
        logger.info(
            "chamado_devolucao_tiktok_records_falhou", chamado_id=str(ch.id), err=str(e)[:120]
        )
        registros = []
    for r in registros:
        if not isinstance(r, dict):
            continue
        papel = str(r.get("role") or "").upper()
        if papel == "SELLER":
            continue
        nota = str(r.get("note") or r.get("comment") or r.get("description") or "").strip()
        if not nota:
            continue
        quem = {"BUYER": "Comprador", "OPERATOR": "TikTok (operador)", "SYSTEM": "TikTok"}.get(
            papel, papel or "TikTok"
        )
        quando = _fmt_dt(r.get("create_time"))
        novos += await registrar_recebida(
            session, ch, cd.PLAT_TIKTOK, f"{quem}{(' ' + quando) if quando else ''}: {nota}"
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
            _encerrar(session, ch, f"shopee:comp:{comp_status}")
    elif status in ("SELLER_DISPUTE", "JUDGING"):
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_EM_ANALISE, quando)
    # 17/09 (Eduardo, 288567 "ganhamos e o robô deixou na aba"; Vinicius, 292128
    # "perdi ou ganhei?"): no BR a decisão da disputa não muda o return — só o
    # escrow do pedido conta a história (compensação paga / reembolso ao comprador).
    tem_disputa = bool(det.get("dispute_reason")) or valor > 0
    if not ch.resolvido and tem_disputa:
        novos += await _desfecho_shopee(session, ch, client, det, dev_q, status, quando, valor)
    if status in _SH_STATUS_TXT:
        txt, fim = _SH_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_SHOPEE, txt)
        if fim:
            if status == "CANCELLED":
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
            elif not ch.status_plataforma:
                chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_ENCERRADO, quando)
            _encerrar(session, ch, f"shopee:{status}")
    return novos


async def _desfecho_shopee(
    session: AsyncSession, ch: Chamado, client, det: dict, dev_q: Devolution,
    status: str, quando: datetime | None, valor: float,
) -> int:
    """Desfecho da disputa pelo escrow do pedido (uma leitura):
    - compensação paga → ganhamos, fecha com o valor recuperado;
    - `compensation_amount` no return sem ajuste no escrow ainda → ganhamos também;
    - comprador reembolsado e nada pra loja → "reembolso pago"; passada a carência
      (`_SH_PERDEMOS_CARENCIA`) ou com o return CLOSED → perdemos, fecha;
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
        _encerrar(session, ch, "shopee:compensacao_paga",
                  valor=total if ch.valor_recuperado is None else None)
        return novos
    if valor > 0:
        txt, _fim = _SH_COMP_TXT["APPROVED"]
        novos = await registrar_recebida(
            session, ch, cd.PLAT_SHOPEE,
            f"{txt} Valor: R$ {det.get('seller_compensation', {}).get('compensation_amount')}.",
        )
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
        _encerrar(session, ch, "shopee:comp:valor",
                  valor=Decimal(str(valor)) if ch.valor_recuperado is None else None)
        return novos
    reembolsado, reembolsado_em = _reembolso_pago(esc)
    if not reembolsado:
        if status == "CLOSED":
            chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_GANHAMOS, quando)
            return await registrar_recebida(session, ch, cd.PLAT_SHOPEE, _SH_SEM_REEMBOLSO_TXT)
        return 0
    chamados_svc.set_status_plataforma(
        ch, chamados_svc.STATUS_REEMBOLSO_PAGO, reembolsado_em or quando
    )
    desde = ch.status_plataforma_at or datetime.now(UTC)
    if status != "CLOSED" and datetime.now(UTC) - desde < _SH_PERDEMOS_CARENCIA:
        return 0
    novos = await registrar_recebida(session, ch, cd.PLAT_SHOPEE, _SH_PERDEMOS_TXT)
    chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_PERDEMOS, desde)
    _encerrar(session, ch, "shopee:reembolso_sem_compensacao")
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
        _encerrar(session, ch, "ml:closed")
    elif (claim.get("stage") or "").lower() == "dispute":
        # Mediação: o ML entrou como juiz — em análise até o claim fechar.
        chamados_svc.set_status_plataforma(
            ch, chamados_svc.STATUS_EM_ANALISE, chamados_svc.ml_quando(claim)
        )
    return novos


# ---------------------------------------------------------------- cron


async def sync_respostas(session: AsyncSession) -> dict:
    """Passada do cron: chamados de devolução ABERTOS via API (abertura enviada)
    → consulta a plataforma, grava respostas novas e encerra os finalizados.
    Best-effort por chamado; commita no fim."""
    rows = (
        await session.execute(
            select(Chamado, ChamadoMensagem)
            .join(ChamadoMensagem, ChamadoMensagem.chamado_id == Chamado.id)
            .where(
                Chamado.origem == "devolucao",
                Chamado.canal == "api",
                Chamado.resolvido.is_(False),
                Chamado.chamado.is_not(None),
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
            n = await fn(session, ch, dev)
            novos += n
            if ch.resolvido:
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
