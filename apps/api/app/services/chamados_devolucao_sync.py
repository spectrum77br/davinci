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
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados as chamados_svc
from app.services import chamados_devolucao as cd
from app.services.devolucao_returns import epoch_to_dt, iso_to_dt

logger = structlog.get_logger()

AUTOR = {cd.PLAT_ML: "Mercado Livre", cd.PLAT_TIKTOK: "TikTok Shop", cd.PLAT_SHOPEE: "Shopee"}
AUTOR_ACOMP = "acompanhamento"

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
    texto = (texto or "").strip()
    if not texto or await _ja_tem(session, ch, texto):
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


def _encerrar(session: AsyncSession, ch: Chamado, motivo: str) -> None:
    if ch.resolvido:
        return
    session.add(chamados_svc.marcar_resolvido(ch, True, autor_nome=AUTOR_ACOMP))
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
    if arb in _TT_ARB_TXT:
        txt, fim = _TT_ARB_TXT[arb]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
    if status in _TT_STATUS_TXT:
        txt, fim = _TT_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_TIKTOK, txt)
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


async def _sync_shopee(session: AsyncSession, ch: Chamado, dev: Devolution | None) -> int:
    dev = dev or Devolution(conta=ch.conta or "", pedido_bling=ch.pedido_bling,
                            pedido_marketplace=ch.pedido_marketplace)
    client = await cd._shopee_client_para(session, ch, dev)
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
    comp = det.get("seller_compensation") or {}
    comp_status = (
        str(comp.get("seller_compensation_status") or "").upper().replace("COMPENSATION_", "")
    )
    if comp_status in _SH_COMP_TXT:
        txt, fim = _SH_COMP_TXT[comp_status]
        valor = comp.get("compensation_amount")
        if comp_status == "APPROVED" and valor:
            txt = f"{txt} Valor: R$ {valor}."
        novos += await registrar_recebida(session, ch, cd.PLAT_SHOPEE, txt)
        if fim:
            _encerrar(session, ch, f"shopee:comp:{comp_status}")
    if status in _SH_STATUS_TXT:
        txt, fim = _SH_STATUS_TXT[status]
        novos += await registrar_recebida(session, ch, cd.PLAT_SHOPEE, txt)
        if fim:
            _encerrar(session, ch, f"shopee:{status}")
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
        benef = str(res.get("benefited") or "").lower()
        quem = {"respondent": "a favor do VENDEDOR", "complainant": "a favor do COMPRADOR"}.get(
            benef, "sem beneficiado informado"
        )
        motivo = str(res.get("reason") or "").strip()
        novos += await registrar_recebida(
            session, ch, cd.PLAT_ML,
            f"Reclamação encerrada no Mercado Livre — decisão {quem}"
            + (f" (motivo: {motivo})" if motivo else "") + ".",
        )
        _encerrar(session, ch, "ml:closed")
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
                ChamadoMensagem.status == "enviada",
            )
            .order_by(Chamado.created_at)
        )
    ).all()
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
