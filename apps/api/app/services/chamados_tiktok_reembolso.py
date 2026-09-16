"""TikTok — pedido de SÓ REEMBOLSO do comprador vira chamado com PRAZO (Eduardo 16/09).

Caso 294865 (16/09): o comprador pediu só reembolso ("pacote não recebido") às 23:29 de
10/09, com o pacote entregue pela J&T às 15:14 do mesmo dia. Ninguém viu — a abertura
automática de chamado só olha devolução COM pacote (RETURN_AND_REFUND), e a devolução só
entra no DaVinci quando o pacote volta — e a TikTok aprovou sozinha em 15/09 23:29 ("não
foi analisado dentro do prazo exigido"): R$ 744 pagos ao comprador. Nessa mesma varredura
o 293798 ("pacote chegou vazio", R$ 640,54) estava pendente com prazo 18/09 10:19.

Vigia (cron): por loja TikTok, caso `return_type=REFUND` pendente com a ação
SELLER_RESPOND_REFUND →
  - abre o chamado na aba Chamados (origem vendas, canal api, chamado = return_id,
    `origem_ref = tiktok_reembolso:<return_id>`) com o motivo e a nota do comprador,
    o valor, a entrega do pedido e o PRAZO da TikTok;
  - avisa no Threema (`tiktok_reembolso_threema_recipients`; vazio = grupo
    `nf_sem_estoque_threema_recipients`) na abertura e de novo com menos de 12 h;
  - quando o caso sai de pendente (recusado, aprovado, cancelado), registra o desfecho
    no histórico — aprovado por falta de resposta fica dito com todas as letras.

Contestar é decisão HUMANA: a réplica manual do chamado (texto + fotos anexadas) vira a
recusa do reembolso na TikTok (POST returns/{id}/reject, decision REJECT_REFUND). Nada é
recusado sozinho.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    Integration,
    IntegrationPlatform,
)
from app.services import chamados as chamados_svc
from app.services import logistica_tiktok, threema
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

PREFIXO_REF = "tiktok_reembolso:"
ACAO_RESPONDER = "SELLER_RESPOND_REFUND"
STATUS_PENDENTE = "RETURN_OR_REFUND_REQUEST_PENDING"
JANELA = timedelta(days=10)
ALERTA_URGENTE = timedelta(hours=12)
MARCA_ABERTURA = "Comprador pediu SÓ REEMBOLSO na TikTok"
MARCA_URGENTE = "Faltam menos de 12 h"
MARCA_DESFECHO = "Desfecho do pedido de só reembolso"
# Motivo de recusa preferido (medido 16/09 no 4042339029758936508): a lista pra só
# reembolso vem com reverse_reject_request_reason_1..4 + motivo de cancelamento inválido.
MOTIVO_PREFERIDO = "reverse_reject_request_reason_1"
MAX_FOTOS = 6
_NS = uuid5(NAMESPACE_URL, "davinci:tiktok_reembolso")
_BRT = timedelta(hours=-3)

_DESFECHOS = {
    "RETURN_OR_REFUND_REQUEST_COMPLETE": "reembolso PAGO ao comprador",
    "RETURN_OR_REFUND_REQUEST_SUCCESS": "reembolso aprovado ao comprador",
    "RETURN_OR_REFUND_REQUEST_CANCEL": "o comprador cancelou o pedido de reembolso",
    "REFUND_OR_RETURN_REQUEST_REJECT": "reembolso RECUSADO (valor fica com o vendedor)",
}


def _fmt(ts: object) -> str:
    try:
        return (datetime.fromtimestamp(int(ts), UTC) + _BRT).strftime("%d/%m %H:%M")  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return ""


def e_chamado_reembolso(ch: Chamado) -> bool:
    return (ch.origem_ref or "").startswith(PREFIXO_REF)


def prazo_resposta(caso: dict) -> int | None:
    """Epoch do prazo da ação SELLER_RESPOND_REFUND (None se a TikTok não pede resposta)."""
    for a in caso.get("seller_next_action_response") or []:
        if isinstance(a, dict) and str(a.get("action") or "").upper() == ACAO_RESPONDER:
            try:
                return int(a.get("deadline"))
            except (TypeError, ValueError):
                return None
    return None


def e_pendente_de_resposta(caso: dict) -> bool:
    return (
        isinstance(caso, dict)
        and str(caso.get("return_type") or "").upper() == "REFUND"
        and str(caso.get("return_status") or "").upper() == STATUS_PENDENTE
        and prazo_resposta(caso) is not None
    )


def _valor(caso: dict) -> str:
    v = (caso.get("refund_amount") or {}).get("refund_total")
    return f"R$ {v}" if v else "valor não informado"


def texto_abertura(caso: dict, nota: str, motivo: str, entrega: str) -> str:
    partes = [
        f"{MARCA_ABERTURA} ({_valor(caso)}) — solicitação {caso.get('return_id')}.",
        f"Motivo: {motivo or caso.get('return_reason_text') or 'não informado'}.",
    ]
    if nota:
        partes.append(f'Nota do comprador: "{nota[:300]}".')
    if entrega:
        partes.append(entrega)
    prazo = prazo_resposta(caso)
    partes.append(
        f"PRAZO pra contestar: {_fmt(prazo)} — depois disso a TikTok aprova o reembolso sozinha."
    )
    partes.append(
        "Pra contestar: escreva a réplica aqui (com fotos da expedição/embalagem se tiver) e envie — "
        "ela vira a recusa do reembolso na TikTok."
    )
    return " ".join(partes)


def texto_desfecho(caso: dict, eventos: list[dict]) -> str:
    status = str(caso.get("return_status") or "").upper()
    base = _DESFECHOS.get(status, f"situação {status or 'desconhecida'}")
    extra = ""
    for ev in eventos or []:
        nome = str(ev.get("event") or "").upper()
        if "TIMEOUT" in nome:
            extra = f" — APROVADO PELA TIKTOK POR FALTA DE RESPOSTA NO PRAZO ({_fmt(ev.get('create_time'))})"
            break
    return f"{MARCA_DESFECHO}: {base} ({_valor(caso)}){extra}."


async def _nota_e_motivo(client: TikTokClient, rid: str) -> tuple[str, str, list[dict]]:
    try:
        eventos = await client.get_return_records(rid)
    except Exception as e:  # noqa: BLE001
        logger.info("tiktok_reembolso_records_falhou", return_id=rid, err=str(e)[:120])
        return "", "", []
    for ev in eventos or []:
        if str(ev.get("event") or "").upper() in ("ORDER_REFUND", "ORDER_RETURN"):
            return str(ev.get("note") or "").strip(), str(ev.get("reason_text") or "").strip(), eventos
    return "", "", eventos or []


async def _entrega(client: TikTokClient, oid: str) -> str:
    try:
        od = await client.get_order_detail(oid)
    except Exception:  # noqa: BLE001
        return ""
    o = (od.get("orders") or [od])[0] if isinstance(od, dict) and od else {}
    if not isinstance(o, dict):
        return ""
    itens = o.get("line_items") or []
    rastreio = next((li.get("tracking_number") for li in itens if li.get("tracking_number")), "")
    transp = next((li.get("shipping_provider_name") for li in itens if li.get("shipping_provider_name")), "")
    quando = _fmt(o.get("delivery_time"))
    if not quando:
        return ""
    return f"Pedido ENTREGUE em {quando}" + (f" ({transp} {rastreio})" if rastreio else "") + "."


def _destinatarios() -> list[str]:
    s = get_settings()
    return threema.parse_recipients(
        s.tiktok_reembolso_threema_recipients or s.nf_sem_estoque_threema_recipients
    )


async def _avisar(texto: str) -> None:
    alvos = _destinatarios()
    if not alvos:
        return
    try:
        await threema.ThreemaClient().send_to_all(texto, recipients=alvos)
    except Exception as e:  # noqa: BLE001 — aviso é best-effort
        logger.warning("tiktok_reembolso_threema_falhou", err=str(e)[:200])


async def _historico(session, ch: Chamado) -> list[str]:
    return list(
        (
            await session.execute(
                select(ChamadoMensagem.texto).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
                )
            )
        ).scalars().all()
    )


async def _chamado_do_caso(session, rid: str) -> Chamado | None:
    return (
        await session.execute(
            select(Chamado).where(Chamado.origem_ref == f"{PREFIXO_REF}{rid}").limit(1)
        )
    ).scalar_one_or_none()


async def run_vigia(session, *, agora: datetime | None = None, dry_run: bool = False) -> dict:
    """Uma passada por todas as lojas TikTok. Commita no fim (a não ser em dry_run)."""
    agora = agora or datetime.now(UTC)
    ts = int(agora.timestamp())
    resumo = {"lojas": 0, "pendentes": 0, "abertos": 0, "urgentes": 0, "desfechos": 0, "erros": 0}
    pendentes_vistos: set[str] = set()
    integracoes = (
        await session.execute(select(Integration).where(Integration.platform == IntegrationPlatform.TIKTOK))
    ).scalars().all()
    for integ in integracoes:
        conta = (integ.name or "").strip()
        try:
            client = logistica_tiktok._build_tiktok_client(session, integ)
            casos = await client.get_return_list(
                update_time_from=ts - int(JANELA.total_seconds()), update_time_to=ts
            )
        except Exception as e:  # noqa: BLE001
            resumo["erros"] += 1
            logger.warning("tiktok_reembolso_lista_falhou", conta=conta, err=str(e)[:200])
            continue
        resumo["lojas"] += 1
        for caso in casos or []:
            if not e_pendente_de_resposta(caso):
                continue
            resumo["pendentes"] += 1
            rid = str(caso.get("return_id") or "").strip()
            oid = str(caso.get("order_id") or "").strip()
            prazo = prazo_resposta(caso) or 0
            ch = await _chamado_do_caso(session, rid)
            if ch is None:
                nota, motivo, _ev = await _nota_e_motivo(client, rid)
                entrega = await _entrega(client, oid)
                texto = texto_abertura(caso, nota, motivo, entrega)
                info = await chamados_svc.lookup_pedido(session, oid) or {}
                resumo["abertos"] += 1
                logger.info("tiktok_reembolso_novo", conta=conta, pedido=oid, return_id=rid, prazo=_fmt(prazo))
                if dry_run:
                    continue
                ch = Chamado(
                    data=(agora + _BRT).date(),
                    pedido_bling=info.get("pedido_bling"),
                    pedido_marketplace=oid,
                    plataforma="tiktok",
                    conta=info.get("conta") or conta,
                    produto=info.get("produto"),
                    sku=info.get("sku"),
                    status_bling=info.get("status_bling"),
                    origem="vendas",
                    origem_ref=f"{PREFIXO_REF}{rid}",
                    chamado=rid,
                    canal="api",
                )
                session.add(ch)
                await session.flush()
                session.add(chamados_svc.registrar_sistema(ch, texto))
                await _avisar(
                    f"⚠️ TikTok {ch.conta}: comprador pediu SÓ REEMBOLSO ({_valor(caso)}) — "
                    f"pedido {ch.pedido_bling or oid}. {('Nota: ' + nota[:160] + '. ') if nota else ''}"
                    f"Contestar até {_fmt(prazo)} (depois a TikTok aprova sozinha). Chamado aberto na aba Chamados."
                )
                continue
            if prazo - ts < ALERTA_URGENTE.total_seconds() and not any(
                MARCA_URGENTE in t for t in await _historico(session, ch)
            ):
                resumo["urgentes"] += 1
                if dry_run:
                    continue
                session.add(
                    chamados_svc.registrar_sistema(
                        ch, f"{MARCA_URGENTE} pra contestar o só reembolso (prazo {_fmt(prazo)})."
                    )
                )
                await _avisar(
                    f"🚨 TikTok {ch.conta}: faltam menos de 12 h pra contestar o só reembolso do pedido "
                    f"{ch.pedido_bling or oid} ({_valor(caso)}) — prazo {_fmt(prazo)}."
                )
        pendentes_vistos.update(
            str(c.get("return_id")) for c in casos or [] if e_pendente_de_resposta(c)
        )
    # desfecho: chamados de só reembolso ainda abertos cujo caso saiu de pendente
    abertos = (
        await session.execute(
            select(Chamado).where(
                Chamado.origem_ref.like(f"{PREFIXO_REF}%"), Chamado.resolvido.is_(False)
            )
        )
    ).scalars().all()
    for ch in abertos:
        rid = (ch.origem_ref or "")[len(PREFIXO_REF):]
        if rid in pendentes_vistos or any(MARCA_DESFECHO in t for t in await _historico(session, ch)):
            continue
        try:
            client, caso = await _client_e_caso(session, ch, rid)
        except Exception as e:  # noqa: BLE001
            logger.info("tiktok_reembolso_desfecho_falhou", chamado_id=str(ch.id), err=str(e)[:120])
            continue
        if caso is None or e_pendente_de_resposta(caso):
            continue
        _n, _m, eventos = await _nota_e_motivo(client, rid)
        resumo["desfechos"] += 1
        if not dry_run:
            session.add(chamados_svc.registrar_sistema(ch, texto_desfecho(caso, eventos)))
    if not dry_run:
        await session.commit()
    return resumo


def _motivo_recusa(reasons: list[dict]) -> str | None:
    nomes = [str(r.get("name") or "") for r in reasons or [] if r.get("name")]
    if MOTIVO_PREFERIDO in nomes:
        return MOTIVO_PREFERIDO
    for r in reasons or []:
        txt = str(r.get("text") or r.get("reason_text") or "").lower()
        if "não é válido" in txt or "not valid" in txt or "invalid" in txt:
            return str(r.get("name"))
    return nomes[0] if nomes else None


async def _client_e_caso(session, ch: Chamado, rid: str) -> tuple[TikTokClient, dict | None]:
    """Cliente da loja dona do caso + o caso fresco. A `conta` do chamado vem do espelho
    do pedido (nome da loja: "mini", "TikTok Mini") e nem sempre é o nome da integração
    (" Mini") — tenta a conta e, se não achar o caso, pergunta às outras lojas TikTok."""
    oid = ch.pedido_marketplace or ""
    tentadas: list[Integration] = []
    integ = await logistica_tiktok._tiktok_integration_for_conta(session, ch.conta)
    if integ is not None:
        tentadas.append(integ)
    for it in (
        await session.execute(select(Integration).where(Integration.platform == IntegrationPlatform.TIKTOK))
    ).scalars().all():
        if all(it.id != x.id for x in tentadas):
            tentadas.append(it)
    ultimo: TikTokClient | None = None
    for it in tentadas:
        client = logistica_tiktok._build_tiktok_client(session, it)
        ultimo = client
        try:
            casos = await client.get_return_list(order_ids=[oid])
        except Exception:  # noqa: BLE001
            continue
        caso = next((c for c in casos or [] if str(c.get("return_id")) == rid), None)
        if caso is not None:
            return client, caso
    if ultimo is None:
        raise chamados_svc.ChamadoError("chamado_sem_integracao_tiktok")
    return ultimo, None


async def contestar(session, ch: Chamado, msg: ChamadoMensagem) -> ChamadoMensagem:
    """Réplica manual num chamado de só reembolso → recusa do reembolso na TikTok.
    Nunca levanta: falha vira `status='falhou'` + `erro`."""
    from app.services.chamados_devolucao import preparar_foto

    rid = (ch.origem_ref or "")[len(PREFIXO_REF):] or (ch.chamado or "")
    try:
        client, caso = await _client_e_caso(session, ch, rid)
        if caso is None:
            raise chamados_svc.ChamadoError("tiktok_reembolso_nao_encontrado")
        if not e_pendente_de_resposta(caso):
            _n, _m, eventos = await _nota_e_motivo(client, rid)
            session.add(chamados_svc.registrar_sistema(ch, texto_desfecho(caso, eventos)))
            raise chamados_svc.ChamadoError("tiktok_reembolso_nao_pendente")
        motivo = _motivo_recusa(await client.get_reject_reasons(rid))
        if not motivo:
            raise chamados_svc.ChamadoError("tiktok_motivo_indisponivel")
        anexos = (
            await session.execute(
                select(ChamadoAnexo).where(ChamadoAnexo.mensagem_id == msg.id).order_by(ChamadoAnexo.created_at)
            )
        ).scalars().all()
        images: list[dict] = []
        for a in [x for x in anexos if (x.content_type or "").startswith("image/")][:MAX_FOTOS]:
            nome, dados, ctype = preparar_foto(a)  # type: ignore[arg-type] — mesmos campos do DevolucaoAnexo
            d = await client.upload_image(nome, dados, ctype)
            img: dict = {"image_id": d.get("uri"), "mime_type": ctype}
            if d.get("width"):
                img["width"] = int(d["width"])
            if d.get("height"):
                img["height"] = int(d["height"])
            images.append(img)
        await client.reject_return(
            rid,
            decision="REJECT_REFUND",
            reject_reason=motivo,
            comment=(msg.texto or "")[:2000],
            images=images or None,
            idempotency_key=str(uuid5(_NS, f"{ch.id}:{msg.id}")),
        )
        msg.status = "enviada"
        msg.erro = None
        msg.enviada_at = datetime.now(UTC)
        session.add(
            chamados_svc.registrar_sistema(
                ch, f"Só reembolso CONTESTADO na TikTok (motivo {motivo}, {len(images)} foto(s))."
            )
        )
    except chamados_svc.ChamadoError as e:
        msg.status = "falhou"
        msg.erro = e.code
    except Exception as e:  # noqa: BLE001
        msg.status = "falhou"
        msg.erro = str(e)[:300]
        logger.warning("tiktok_reembolso_contestar_falhou", chamado_id=str(ch.id), err=msg.erro)
    return msg
