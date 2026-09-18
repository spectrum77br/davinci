"""TikTok — pedido de SÓ REEMBOLSO do comprador: vigia do prazo + recusa pelo lançamento.

Caso 294865 (Eduardo, 16/09): o comprador pediu só reembolso ("pacote não recebido") às
23:29 de 10/09, com o pacote entregue pela J&T às 15:14 do mesmo dia. Ninguém viu — a
abertura automática de chamado só olhava devolução COM pacote (RETURN_AND_REFUND) — e a
TikTok aprovou sozinha em 15/09 ("não foi analisado dentro do prazo exigido"): R$ 744
pagos ao comprador. Daí nasceu o vigia: abria um chamado direto na aba Chamados, pedia
foto no Threema e, faltando 12 h, contestava sozinho.

Caso 296936 (Vinicius, 18/09, "recebi uma caixa de sabonete"): o chamado direto atropelava
a análise — o pessoal já cuida desses casos na tela Devoluções › Fraude, e o chamado
apagado voltava a cada rodada (a única memória do vigia era o próprio chamado). Fluxo
que vale desde então:

1. o caso cai em Devoluções › **Fraude** sozinho: regra da aba Status
   "TikTok | Reembolso solicitado → Aguardando Devolução" + o sync das devoluções
   (`return_type=REFUND` = fila Fraude, com o prazo p/ responder);
2. o pessoal monta o vídeo (coluna Vídeo) e as fotos;
3. faz o **lançamento** — vídeo obrigatório (`routers/devolutions._exigir_video_fraude`);
4. o lançamento **responde o caso que o vigia achou**: recusa do reembolso na TikTok com
   os fatos da entrega, as fotos e o link do vídeo — `chamados_devolucao` (branch
   só-reembolso de `_disparar_tiktok`), registrado no chamado de devolução.

O vigia (cron :10/:40) então só:
  - **avisa no Threema** faltando 12 h sem resposta e, se continuar sem resposta,
    faltando 3 h (Vinicius 18/09). Dedupe em `devolucao_rastreio.aviso_prazo_acao_at/
    _para` — mesma semântica do `devolucao_acao_avisos`, que pula os casos só-reembolso
    justamente porque este módulo cuida deles;
  - registra o **desfecho** (aprovado / aprovado por falta de resposta / cancelado) no
    chamado que respondeu — inclusive nos chamados antigos `tiktok_reembolso:<id>`.
    "Vendedor recusou" NÃO é desfecho (é a nossa recusa; o comprador ainda recorre) —
    daí em diante quem acompanha é o sync dos chamados, até a TikTok decidir.
Ele NÃO abre chamado e NÃO contesta sozinho. A réplica manual num chamado antigo continua
sendo a recusa (`contestar`).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import structlog
from sqlalchemy import or_, select

from app.config import get_settings
from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoRastreio,
    Integration,
    IntegrationPlatform,
)
from app.services import chamados as chamados_svc
from app.services import logistica_tiktok, threema
from app.services.devolucao_returns import epoch_to_dt
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

PREFIXO_REF = "tiktok_reembolso:"
ACAO_RESPONDER = "SELLER_RESPOND_REFUND"
STATUS_PENDENTE = "RETURN_OR_REFUND_REQUEST_PENDING"
JANELA = timedelta(days=10)
# Avisos no Threema enquanto ninguém respondeu: um faltando 12 h e o último faltando 3 h.
AVISO_PRIMEIRO = timedelta(hours=12)
AVISO_ULTIMO = timedelta(hours=3)
MARCA_DESFECHO = "Desfecho do pedido de só reembolso"
MARCA_AUTO = "Só reembolso CONTESTADO na TikTok"
AUTOR_ROBO = "robô"
# Motivo de recusa preferido (medido 16/09 no 4042339029758936508 e 18/09 no
# 4042357484883052019): a lista pra só reembolso vem com reverse_reject_request_reason_1..4
# ("O motivo da devolução do comprador não é válido" = _1) + motivo de cancelamento inválido.
MOTIVO_PREFERIDO = "reverse_reject_request_reason_1"
MAX_FOTOS = 6
# Limite do `comment` do reject na TikTok. Medido 18/09 no 296936: 560 caracteres
# voltaram "98001004 Invalid parameters — the length of seller words is over limit".
# Contado em BYTES UTF-8 pra valer nas duas leituras possíveis (caractere ou byte).
COMENTARIO_MAX_BYTES = 500
_NS = uuid5(NAMESPACE_URL, "davinci:tiktok_reembolso")
_BRT = timedelta(hours=-3)

_DESFECHOS = {
    "RETURN_OR_REFUND_REQUEST_COMPLETE": "reembolso PAGO ao comprador",
    "RETURN_OR_REFUND_REQUEST_SUCCESS": "reembolso aprovado ao comprador",
    "RETURN_OR_REFUND_REQUEST_CANCEL": "o comprador cancelou o pedido de reembolso",
}
# 18/09 (Vinicius, 296936): "vendedor recusou" (REFUND_OR_RETURN_REQUEST_REJECT) NÃO é
# desfecho — é a nossa recusa registrada; o comprador ainda pode abrir disputa (recorreu
# em 5 de 5 casos medidos). Quem acompanha daí em diante é o sync dos chamados
# (chamados_devolucao_sync): aguardando plataforma → arbitragem → decisão.
STATUS_RECUSADO = "REFUND_OR_RETURN_REQUEST_REJECT"
TEXTO_RECUSA_REGISTRADA = (
    "Recusa do reembolso já registrada na TikTok — o comprador ainda pode recorrer; "
    "o acompanhamento segue até a decisão."
)
# Coluna Status da aba (17/09): o desfecho em ganhou/perdeu.
_STATUS_ABA = {
    "RETURN_OR_REFUND_REQUEST_COMPLETE": chamados_svc.STATUS_PERDEMOS,
    "RETURN_OR_REFUND_REQUEST_SUCCESS": chamados_svc.STATUS_PERDEMOS,
    "RETURN_OR_REFUND_REQUEST_CANCEL": chamados_svc.STATUS_GANHAMOS,
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


def e_so_reembolso(caso: dict) -> bool:
    return isinstance(caso, dict) and str(caso.get("return_type") or "").upper() == "REFUND"


def e_pendente_de_resposta(caso: dict) -> bool:
    return (
        e_so_reembolso(caso)
        and str(caso.get("return_status") or "").upper() == STATUS_PENDENTE
        and prazo_resposta(caso) is not None
    )


def _valor(caso: dict) -> str:
    v = (caso.get("refund_amount") or {}).get("refund_total")
    return f"R$ {v}" if v else "valor não informado"


def texto_desfecho(caso: dict, eventos: list[dict]) -> str:
    status = str(caso.get("return_status") or "").upper()
    if status == STATUS_RECUSADO:
        return TEXTO_RECUSA_REGISTRADA  # sem MARCA_DESFECHO: o desfecho de verdade vem depois
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


async def _entrega_dados(client: TikTokClient, oid: str) -> dict:
    """{quando (epoch), transp, rastreio} da entrega; {} sem entrega registrada."""
    try:
        od = await client.get_order_detail(oid)
    except Exception:  # noqa: BLE001
        return {}
    o = (od.get("orders") or [od])[0] if isinstance(od, dict) and od else {}
    if not isinstance(o, dict) or not _fmt(o.get("delivery_time")):
        return {}
    itens = o.get("line_items") or []
    return {
        "quando": o.get("delivery_time"),
        "rastreio": next((x["tracking_number"] for x in itens if x.get("tracking_number")), ""),
        "transp": next(
            (x["shipping_provider_name"] for x in itens if x.get("shipping_provider_name")), ""
        ),
    }


def _bytes(txt: str) -> int:
    return len(txt.encode("utf-8"))


def _cortar(txt: str, limite: int) -> str:
    """Corta em BYTES UTF-8 sem partir caractere, de preferência num espaço."""
    if _bytes(txt) <= limite:
        return txt
    raw = txt.encode("utf-8")[:limite]
    txt = raw.decode("utf-8", errors="ignore")
    if " " in txt:
        txt = txt[: txt.rfind(" ")]
    return txt.rstrip(" ,;:")


def caber(partes: list[str], limite: int = COMENTARIO_MAX_BYTES) -> str:
    """Junta as partes (ordem = prioridade) dentro do limite: o que não cabe cai a partir
    do FIM — a última parte que ainda entra é cortada num espaço; as seguintes ficam fora.
    Garante que as partes iniciais (entrega, link do vídeo) sobrevivam inteiras."""
    partes = [p.strip() for p in partes if (p or "").strip()]
    saida: list[str] = []
    usados = 0
    for parte in partes:
        custo = _bytes(parte) + (1 if saida else 0)
        if usados + custo <= limite:
            saida.append(parte)
            usados += custo
            continue
        sobra = limite - usados - (1 if saida else 0)
        # Só corta se sobrar espaço pra algo que ainda faça sentido (uma frase curta);
        # reserva os 3 bytes do "…".
        if sobra >= 40:
            cortada = _cortar(parte, sobra - 3)
            if cortada and not cortada.endswith("."):
                cortada += "…"
            saida.append(cortada)
        break
    return " ".join(saida)


def texto_contestacao(
    caso: dict, *, oid: str, produto: str | None, entrega: dict, nota: str,
    pedido_em: object, comprador_mandou_prova: bool, fotos: int,
    video: str | None = None, observacao: str | None = None,
) -> str:
    """Texto da recusa — só FATOS que o DaVinci/TikTok confirmam, dentro do limite do
    `comment` da TikTok (COMENTARIO_MAX_BYTES). Ordem = prioridade: entrega, LINK DO VÍDEO
    (a API não aceita vídeo: vai como link), alegação do comprador, fotos, observação do
    lançamento, pedido final. O que não couber sai do fim."""
    item = f"{oid} ({produto})" if produto else oid
    partes = ["Contestamos o pedido de reembolso."]
    if entrega.get("quando"):
        partes.append(
            f"Pedido {item} entregue em {_fmt(entrega.get('quando'))}"
            + (f" pela {entrega['transp']}" if entrega.get("transp") else "")
            + (f" (rastreio {entrega['rastreio']})" if entrega.get("rastreio") else "")
            + ", sem avaria/violação na entrega."
        )
    else:
        partes.append(f"Pedido {item}.")
    if video:
        partes.append(f"Vídeo da expedição: {video}")
    # A observação do lançamento é evidência NOSSA (ex.: peso conferido) — vem antes da
    # alegação do comprador, que a TikTok já conhece.
    if (observacao or "").strip():
        partes.append(observacao.strip())
    if fotos:
        partes.append(f"Seguem {fotos} foto(s) da expedição/embalagem.")
    try:
        dias = (int(pedido_em) - int(entrega.get("quando"))) // 86400  # type: ignore[arg-type]
    except (TypeError, ValueError):
        dias = -1
    nota = " ".join((nota or "").split())  # a nota vem com quebras de linha
    alegacao = "Alegação do comprador" + (f' ("{nota[:80]}")' if nota else "")
    quando = f" feita em {_fmt(pedido_em)}" if _fmt(pedido_em) else ""
    depois = f", {dias} dia(s) após a entrega" if dias >= 1 else ""
    prova = "" if comprador_mandou_prova else ", sem foto ou vídeo que a comprove"
    partes.append(f"{alegacao}{quando}{depois}{prova}.")
    partes.append("Pedimos que o reembolso seja negado.")
    return caber(partes)


def _pedido_do_comprador(eventos: list[dict]) -> tuple[object, bool]:
    """(create_time do pedido de reembolso, comprador anexou foto/vídeo?)."""
    for ev in eventos or []:
        if str(ev.get("event") or "").upper() in ("ORDER_REFUND", "ORDER_RETURN"):
            chaves = ("images", "videos", "image_list", "video_list", "attachments")
            midia = any(ev.get(k) for k in chaves)
            return ev.get("create_time"), bool(midia)
    return None, False


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


async def chamado_da_resposta(session, rid: str) -> Chamado | None:
    """O chamado que responde (ou respondeu) o caso `rid`: o de devolução que o lançamento
    abriu (`chamado` = id do caso, plataforma tiktok) ou um antigo do vigia
    (`origem_ref = tiktok_reembolso:<rid>`). Aberto antes de resolvido; mais recente antes."""
    return (
        await session.execute(
            select(Chamado)
            .where(
                or_(
                    Chamado.origem_ref == f"{PREFIXO_REF}{rid}",
                    (Chamado.chamado == rid) & (Chamado.plataforma.ilike("tiktok%")),
                )
            )
            .order_by(Chamado.resolvido.asc(), Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def resposta_enviada(session, ch: Chamado) -> ChamadoMensagem | None:
    """A recusa do reembolso já SAIU por esse chamado? (abertura do lançamento ou réplica
    manual com status `enviada`)."""
    return (
        await session.execute(
            select(ChamadoMensagem)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo.in_(("abertura", "replica")),
                ChamadoMensagem.status == "enviada",
            )
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resposta_travada(session, ch: Chamado) -> str | None:
    """Código do que está segurando a resposta do lançamento (abertura pendente/falhou),
    pro aviso dizer o que falta; None se não há abertura."""
    msg = (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura")
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if msg is None:
        return None
    return (msg.erro or msg.status or "").strip() or None


def aviso_devido(rast: DevolucaoRastreio, prazo: datetime, agora: datetime) -> str | None:
    """"12h" / "3h" quando ESTA rodada deve avisar, None se não. Primeiro aviso quando falta
    menos de 12 h e nunca avisou pra ESSE prazo; último quando falta menos de 3 h e o aviso
    anterior saiu antes dessa faixa. Mesmo carimbo do devolucao_acao_avisos."""
    faltam = prazo - agora
    if faltam > AVISO_PRIMEIRO:
        return None
    ultimo = faltam <= AVISO_ULTIMO
    para = rast.aviso_prazo_acao_para
    if para is not None and para.tzinfo is None:
        para = para.replace(tzinfo=UTC)
    if para != prazo:
        return "3h" if ultimo else "12h"
    anterior = rast.aviso_prazo_acao_at
    if anterior is not None and anterior.tzinfo is None:
        anterior = anterior.replace(tzinfo=UTC)
    if ultimo and anterior is not None and prazo - anterior > AVISO_ULTIMO:
        return "3h"
    return None


def texto_aviso(
    *, conta: str, pedido: str, caso: dict, nota: str, prazo: int, ultimo: bool,
    ch: Chamado | None, travada: str | None, agora: datetime | None = None,
) -> str:
    agora = agora or datetime.now(UTC)
    horas = max(0, int((prazo - int(agora.timestamp())) // 3600))
    cabeca = "🚨" if ultimo else "⚠️"
    partes = [
        f"{cabeca} TikTok {conta}: comprador pediu SÓ REEMBOLSO ({_valor(caso)}) — pedido {pedido}."
        f" Faltam {horas} h pra responder (prazo {_fmt(prazo)})."
        + (" ÚLTIMO AVISO." if ultimo else "")
    ]
    if nota:
        partes.append(f"Nota do comprador: {nota[:160]}.")
    if ch is None:
        partes.append(
            "Ninguém lançou ainda: fazer o LANÇAMENTO na aba Devoluções › Fraude (vídeo"
            " obrigatório) — o sistema responde a TikTok com os fatos, as fotos e o vídeo."
        )
    else:
        partes.append(
            f"O lançamento existe, mas a resposta ainda NÃO saiu ({travada or 'pendente'}) —"
            f" veja o chamado do pedido {ch.pedido_bling or pedido} na aba Chamados."
        )
    partes.append("Depois do prazo a TikTok aprova o reembolso sozinha.")
    return " ".join(partes)


async def _rastreio_para_carimbo(session, pedido_bling: str) -> DevolucaoRastreio:
    """Linha do Acompanhamento onde o aviso é carimbado (o sync cria pra quem está em
    Aguardando Devolução; se a regra ainda não moveu o pedido, cria só o carimbo)."""
    rast = await session.get(DevolucaoRastreio, pedido_bling)
    if rast is None:
        rast = DevolucaoRastreio(pedido_bling=pedido_bling, fonte_auto="tiktok")
        session.add(rast)
        await session.flush()
    return rast


async def run_vigia(session, *, agora: datetime | None = None, dry_run: bool = False) -> dict:
    """Uma passada por todas as lojas TikTok. Commita no fim (a não ser em dry_run)."""
    agora = agora or datetime.now(UTC)
    ts = int(agora.timestamp())
    resumo = {
        "lojas": 0, "pendentes": 0, "respondidos": 0, "avisos_12h": 0, "avisos_3h": 0,
        "desfechos": 0, "erros": 0,
    }
    # Todos os casos só-reembolso da janela (pendentes ou não) — o desfecho usa os que saíram.
    casos_vistos: dict[str, tuple[TikTokClient, dict, str]] = {}
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
            if not e_so_reembolso(caso):
                continue
            rid = str(caso.get("return_id") or "").strip()
            if not rid:
                continue
            casos_vistos[rid] = (client, caso, conta)
            if not e_pendente_de_resposta(caso):
                continue
            resumo["pendentes"] += 1
            oid = str(caso.get("order_id") or "").strip()
            prazo = prazo_resposta(caso) or 0
            ch = await chamado_da_resposta(session, rid)
            if ch is not None and await resposta_enviada(session, ch) is not None:
                resumo["respondidos"] += 1
                continue
            prazo_dt = datetime.fromtimestamp(prazo, UTC)
            if prazo_dt - agora > AVISO_PRIMEIRO:
                continue
            info = await chamados_svc.lookup_pedido(session, oid) or {}
            pedido_bling = str(
                info.get("pedido_bling") or (ch.pedido_bling if ch is not None else None) or ""
            ).strip()
            if not pedido_bling:
                logger.info("tiktok_reembolso_sem_pedido_bling", conta=conta, pedido=oid, return_id=rid)
                continue
            rast = await _rastreio_para_carimbo(session, pedido_bling)
            nivel = aviso_devido(rast, prazo_dt, agora)
            if nivel is None:
                continue
            resumo["avisos_3h" if nivel == "3h" else "avisos_12h"] += 1
            logger.info(
                "tiktok_reembolso_aviso", conta=conta, pedido=pedido_bling, return_id=rid,
                nivel=nivel, prazo=_fmt(prazo),
            )
            if dry_run:
                continue
            nota, _m, _ev = await _nota_e_motivo(client, rid)
            travada = await _resposta_travada(session, ch) if ch is not None else None
            await _avisar(texto_aviso(
                conta=(info.get("conta") or conta), pedido=pedido_bling, caso=caso, nota=nota,
                prazo=prazo, ultimo=(nivel == "3h"), ch=ch, travada=travada, agora=agora,
            ))
            rast.aviso_prazo_acao_at = agora
            rast.aviso_prazo_acao_para = prazo_dt
    # Desfecho: chamado que respondeu (lançamento ou antigo) cujo caso saiu de pendente.
    abertos = (
        await session.execute(
            select(Chamado).where(
                Chamado.resolvido.is_(False),
                or_(
                    Chamado.origem_ref.like(f"{PREFIXO_REF}%"),
                    (Chamado.plataforma.ilike("tiktok%")) & (Chamado.chamado.in_(list(casos_vistos) or [""])),
                ),
            )
        )
    ).scalars().all()
    for ch in abertos:
        rid = (ch.origem_ref or "")[len(PREFIXO_REF):] if e_chamado_reembolso(ch) else (ch.chamado or "")
        if not rid or any(MARCA_DESFECHO in t for t in await _historico(session, ch)):
            continue
        visto = casos_vistos.get(rid)
        if visto is not None:
            client, caso, _c = visto
        else:
            try:
                client, caso = await _client_e_caso(session, ch, rid)
            except Exception as e:  # noqa: BLE001
                logger.info("tiktok_reembolso_desfecho_falhou", chamado_id=str(ch.id), err=str(e)[:120])
                continue
        if caso is None or not e_so_reembolso(caso) or e_pendente_de_resposta(caso):
            continue
        status = str(caso.get("return_status") or "").upper()
        if status == STATUS_RECUSADO:
            continue  # nossa recusa, não desfecho — o sync dos chamados acompanha
        if status == "RETURN_OR_REFUND_REQUEST_CANCEL" and _refeito(casos_vistos, caso):
            continue  # o comprador refez o pedido: a briga segue no caso novo (sync)
        _n, _m, eventos = await _nota_e_motivo(client, rid)
        resumo["desfechos"] += 1
        if not dry_run:
            session.add(chamados_svc.registrar_sistema(ch, texto_desfecho(caso, eventos)))
            status_aba = _STATUS_ABA.get(status)
            if status_aba:
                chamados_svc.set_status_plataforma(ch, status_aba, epoch_to_dt(caso.get("update_time")))
    if not dry_run:
        await session.commit()
    return resumo


def _refeito(casos_vistos: dict, caso: dict) -> bool:
    """Há pedido de só reembolso mais novo do mesmo pedido (o comprador refez)?"""
    oid = str(caso.get("order_id") or "")
    rid = str(caso.get("return_id") or "")
    try:
        base = int(caso.get("create_time") or 0)
    except (TypeError, ValueError):
        base = 0
    for _client, outro, _conta in casos_vistos.values():
        if str(outro.get("order_id") or "") != oid or str(outro.get("return_id") or "") == rid:
            continue
        try:
            if int(outro.get("create_time") or 0) > base:
                return True
        except (TypeError, ValueError):
            continue
    return False


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


async def contestar(
    session, ch: Chamado, msg: ChamadoMensagem, *, anexos: list[ChamadoAnexo] | None = None
) -> ChamadoMensagem:
    """Réplica MANUAL num chamado antigo de só reembolso (`tiktok_reembolso:<id>`) → recusa
    do reembolso na TikTok com as fotos da própria réplica. Nunca levanta: falha vira
    `status='falhou'` + `erro`. (O caminho normal desde 18/09 é o lançamento —
    chamados_devolucao — que monta o texto sozinho.)"""
    from app.services.chamados_devolucao import preparar_foto

    rid = (ch.origem_ref or "")[len(PREFIXO_REF):] or (ch.chamado or "")
    try:
        client, caso = await _client_e_caso(session, ch, rid)
        if caso is None:
            raise chamados_svc.ChamadoError("tiktok_reembolso_nao_encontrado")
        if not e_pendente_de_resposta(caso):
            _n, _m, eventos = await _nota_e_motivo(client, rid)
            if not any(MARCA_DESFECHO in t for t in await _historico(session, ch)):
                session.add(chamados_svc.registrar_sistema(ch, texto_desfecho(caso, eventos)))
            status = str(caso.get("return_status") or "").upper()
            arbitragem = str(caso.get("arbitration_status") or "")
            ja = status == "REFUND_OR_RETURN_REQUEST_REJECT" or bool(arbitragem)
            raise chamados_svc.ChamadoError(
                "tiktok_reembolso_ja_contestado" if ja else "tiktok_reembolso_nao_pendente"
            )
        motivo = _motivo_recusa(await client.get_reject_reasons(rid))
        if not motivo:
            raise chamados_svc.ChamadoError("tiktok_motivo_indisponivel")
        if anexos is None:
            anexos = (
                await session.execute(
                    select(ChamadoAnexo)
                    .where(ChamadoAnexo.mensagem_id == msg.id)
                    .order_by(ChamadoAnexo.created_at)
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
            comment=caber([msg.texto or ""]),
            images=images or None,
            idempotency_key=str(uuid5(_NS, f"{ch.id}:{msg.id}")),
        )
        msg.status = "enviada"
        msg.erro = None
        msg.enviada_at = datetime.now(UTC)
        session.add(
            chamados_svc.registrar_sistema(
                ch, f"{MARCA_AUTO} (motivo {motivo}, {len(images)} foto(s))"
                + (" — contestação automática do robô." if msg.autor_nome == AUTOR_ROBO else ".")
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
