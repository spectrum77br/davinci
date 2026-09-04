"""Devolução → chamado AUTOMÁTICO no Mercado Livre, com fotos.

Eduardo, 04/09: "Todos esses motivos aí (Bloqueado, Golpe, Item faltando,
Não recebido, Danificado), se for adicionado lá, vai abrir o chamado
automático … vai ter foto sim e vídeo". O "chamado" de devolução que o
vendedor abre no ML é a **revisão da devolução com problema**
(`POST /post-purchase/v1/returns/{return_id}/return-review`, motivo SRF2–SRF7):
só existe quando o comprador abriu a devolução (claim com return) e o ML
liberou `return_review_fail` pro vendedor — normalmente quando o pacote de
volta consta entregue. Danificado (SRF2) e Produto diferente (SRF4) EXIGEM foto.

Fluxo:
1. tela Devoluções marca um motivo da lista → `garantir_chamado` registra o
   chamado na aba (services/chamados.abrir_chamado_devolucao) e, se a conta é
   ML, deixa uma mensagem `abertura` PENDENTE e troca o canal pra `api`;
2. o router enfileira `agendar_disparo` (worker) → `disparar`: resolve
   claim/return do pedido, sobe as fotos da linha (uma vez cada — `ml_file_name`
   guarda o nome), manda a revisão. O que não dá pra fazer AINDA (sem foto,
   Golpe sem sub-motivo, ML ainda não liberou a revisão) fica `pendente` com o
   código em `erro` — a tela mostra e o cron de hora em hora
   (`processar_pendentes`) tenta de novo, até 45 dias;
3. deu certo → `enviada`, chamado ganha o nº do claim, canal `api` e
   monitoramento ligado (fecha sozinho quando o ML encerrar).

Vídeo: a API do ML aceita JPG/PNG/PDF/TXT até 5 MB; o vídeo fica guardado na
linha (o operador anexa no site se precisar) e não vai pela API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoAnexo,
    Devolution,
    StoreInfo,
)
from app.services import chamados as chamados_svc
from app.services import logistica_meli
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

TIPO_ABERTURA = "abertura"
ACAO_REVISAO = "return_review_fail"

# Motivo da tela (minúsculo) → motivo do ML. None = depende do sub-motivo que
# o operador escolhe (`devolutions.motivo_ml`).
MOTIVO_ML: dict[str, str | None] = {
    "item faltando": "SRF3",  # devolução incompleta
    "danificado (outros)": "SRF2",  # produto chegou danificado (exige foto)
    "não recebido": "SRF7",  # pacote não chegou (motivo do pacote, sem anexo)
    # "Bloqueado" = mala voltou travada por senha (Eduardo 04/09: "produto veio,
    # mas bloqueado por senha abre chamado tbm") → outro problema com o produto.
    "bloqueado": "SRF6",
    "mudou de ideia": "SRF6",  # nome antigo de "Bloqueado" (migration 0239)
    "golpe": None,  # "depende" → SRF4 | SRF5 | SRF6 escolhido na linha
}
GOLPE_TIPOS: tuple[str, ...] = ("SRF4", "SRF5", "SRF6")
REASONS_EXIGEM_FOTO = frozenset({"SRF2", "SRF4"})
REASONS_DO_PACOTE = frozenset({"SRF7"})
REASON_NOME: dict[str, str] = {
    "SRF2": "produto chegou danificado",
    "SRF3": "devolução incompleta",
    "SRF4": "produto diferente do enviado",
    "SRF5": "produto não estava no pacote",
    "SRF6": "outro problema com o produto",
    "SRF7": "pacote da devolução não chegou",
}

# O que o ML aceita como evidência pela API (e o teto por arquivo).
ML_FOTO_TIPOS = frozenset({"image/jpeg", "image/png", "application/pdf"})
ML_FOTO_MAX_BYTES = 5 * 1024 * 1024
# Quanto tempo uma abertura fica pendente esperando o ML liberar a revisão /
# o operador anexar foto antes de virar `falhou`.
PRAZO_PENDENTE = timedelta(days=45)

# Em teste o disparo roda inline na mesma sessão (sem Redis).
ENFILEIRAR = True


class _PendenteError(Exception):
    """Ainda não dá pra abrir — fica `pendente` com o código; o cron repete."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------- motivo/texto


def reason_para(dev: Devolution) -> str | None:
    motivo = (dev.motivo_devolucao or "").strip().lower()
    if motivo not in MOTIVO_ML:
        return None
    reason = MOTIVO_ML[motivo]
    if reason is None:
        escolhido = (dev.motivo_ml or "").strip().upper()
        return escolhido if escolhido in GOLPE_TIPOS else None
    return reason


_INTRO: dict[str, str] = {
    "SRF2": "Recebemos a devolução e o produto chegou danificado.",
    "SRF3": "Recebemos a devolução incompleta: faltam itens que foram enviados ao comprador.",
    "SRF4": "Recebemos a devolução e o produto devolvido é diferente do que enviamos.",
    "SRF5": "Recebemos o pacote da devolução sem o produto dentro.",
    "SRF6": "Recebemos a devolução com problema no produto.",
    "SRF7": "O pacote da devolução ainda não chegou até nós.",
}


def texto_padrao(dev: Devolution, reason: str | None, *, fotos: int = 0) -> str:
    """Mensagem que vai pro ML (o operador não digita nada — é automático)."""
    motivo = (dev.motivo_devolucao or "").strip()
    if motivo.lower() in ("bloqueado", "mudou de ideia"):
        intro = (
            "Recebemos a devolução, mas o produto voltou bloqueado por senha "
            "(trava/cadeado com senha definida pelo comprador), o que impede a revenda."
        )
    else:
        intro = _INTRO.get(reason or "", f"Recebemos a devolução com problema ({motivo}).")
    linhas = [intro]
    ident = []
    if (dev.pedido_marketplace or "").strip():
        ident.append(f"Pedido {dev.pedido_marketplace.strip()}")
    if (dev.sku or "").strip():
        ident.append(f"SKU {dev.sku.strip()}")
    if (dev.produtos or "").strip():
        ident.append(dev.produtos.strip())
    if ident:
        linhas.append(" · ".join(ident) + ".")
    if (dev.observacao or "").strip():
        linhas.append(f"Observação: {dev.observacao.strip()}")
    if fotos:
        linhas.append(f"Seguem {fotos} foto(s) em anexo como evidência.")
    linhas.append("Solicitamos a análise do caso.")
    return "\n".join(linhas)


# ---------------------------------------------------------------- consultas


async def _plataforma_da_conta(session: AsyncSession, conta: str | None) -> str | None:
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (await session.execute(select(StoreInfo.platform, StoreInfo.account_name))).all()
    for plat, nome in rows:
        if (nome or "").strip().lower() == key and plat:
            return str(plat)
    return None


async def mensagem_abertura(session: AsyncSession, ch: Chamado) -> ChamadoMensagem | None:
    return (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == TIPO_ABERTURA)
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _linhas_do_pedido(session: AsyncSession, dev: Devolution) -> list[Devolution]:
    """Todas as linhas da devolução do MESMO pedido (kit = várias linhas, um
    chamado): as fotos e o sub-motivo de Golpe valem pra qualquer uma."""
    if not (dev.pedido_bling or "").strip():
        return [dev]
    rows = (
        await session.execute(
            select(Devolution).where(
                Devolution.pedido_bling == dev.pedido_bling.strip(),
                Devolution.conta == dev.conta,
            )
        )
    ).scalars().all()
    return list(rows) or [dev]


async def anexos_de(session: AsyncSession, devolution_ids: list[UUID]) -> list[DevolucaoAnexo]:
    if not devolution_ids:
        return []
    return list(
        (
            await session.execute(
                select(DevolucaoAnexo)
                .where(DevolucaoAnexo.devolution_id.in_(devolution_ids))
                .order_by(DevolucaoAnexo.created_at)
            )
        ).scalars().all()
    )


def _seller_actions(claim: dict) -> set[str]:
    """Ações liberadas pro VENDEDOR no claim (doc de devoluções: player
    `type=seller`; nos claims de mediação vem como `role=respondent`)."""
    out: set[str] = set()
    for p in claim.get("players") or []:
        if not isinstance(p, dict):
            continue
        tipo = (p.get("type") or "").lower()
        papel = (p.get("role") or "").lower()
        if tipo == "seller" or papel == "respondent":
            out |= {
                a.get("action")
                for a in (p.get("available_actions") or [])
                if isinstance(a, dict) and a.get("action")
            }
    return out


def _return_id_de(rets: object) -> str | None:
    """id do return (vivo mais recente; senão o mais recente) do payload v2."""
    lista = logistica_meli._returns_as_list(rets)
    if not lista:
        return None
    vivos = [
        r for r in lista
        if str(r.get("status") or "").strip().lower() not in logistica_meli._RETURN_DEAD
    ]
    escolhido = (vivos or lista)[-1]
    rid = escolhido.get("id") or escolhido.get("return_id")
    return str(rid) if rid else None


async def _resolver_claim(
    client: MercadoLivreClient, ch: Chamado, dev: Devolution
) -> tuple[str, str]:
    """(claim_id, return_id) do caso em que o ML já liberou a revisão da
    devolução pro vendedor. Guarda o claim achado em `ch.chamado` mesmo quando
    a revisão ainda não está liberada (poupa chamadas na retentativa)."""
    if (ch.chamado or "").strip():
        claim_ids = [ch.chamado.strip()]
    else:
        pedido = (dev.pedido_marketplace or ch.pedido_marketplace or "").strip()
        if not pedido:
            raise _PendenteError("devolucao_sem_pedido_marketplace")
        orders = await logistica_meli._orders_do_pedido(client, pedido)
        claim_ids = []
        for o in orders:
            for cid in logistica_meli._mediation_ids(o):
                if str(cid) not in claim_ids:
                    claim_ids.append(str(cid))
        if not claim_ids:
            raise _PendenteError("devolucao_sem_claim")

    com_return: tuple[str, str] | None = None
    for cid in claim_ids:
        claim = await client.get_claim(cid) or {}
        try:
            rets = await client.get_claim_returns(cid)
        except Exception as e:  # noqa: BLE001 — claim sem devolução → 404
            logger.info("chamado_devolucao_sem_return", claim_id=cid, err=str(e)[:120])
            continue
        rid = _return_id_de(rets)
        if not rid:
            continue
        if ACAO_REVISAO in _seller_actions(claim):
            return cid, rid
        if (claim.get("status") or "").lower() != "closed" and com_return is None:
            com_return = (cid, rid)
    if com_return is not None:
        ch.chamado = com_return[0]
        raise _PendenteError("return_review_indisponivel")
    raise _PendenteError("devolucao_sem_return")


# ---------------------------------------------------------------- fotos


def _extensao_para(ctype: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}[ctype]


def _nome_com_extensao(filename: str, ctype: str) -> str:
    ext = _extensao_para(ctype)
    base = (filename or "").strip() or "evidencia"
    low = base.lower()
    if ctype == "image/jpeg" and low.endswith((".jpg", ".jpeg")):
        return base
    if low.endswith(ext):
        return base
    raiz = base.rsplit(".", 1)[0] if "." in base else base
    return f"{raiz}{ext}"


def preparar_foto(anexo: DevolucaoAnexo) -> tuple[str, bytes, str]:
    """(nome, bytes, content-type) prontos pro ML. Imagem acima de 5 MB é
    reduzida (PyMuPDF, já dependência) até caber; PDF grande não tem redução."""
    ctype = (anexo.content_type or "").lower()
    if ctype not in ML_FOTO_TIPOS:
        raise RuntimeError(f"tipo de anexo não aceito pelo ML: {ctype}")
    dados = anexo.blob
    if len(dados) <= ML_FOTO_MAX_BYTES:
        return _nome_com_extensao(anexo.filename, ctype), dados, ctype
    if ctype == "application/pdf":
        raise RuntimeError("PDF acima de 5 MB — o ML não aceita")
    import fitz  # PyMuPDF

    pix = fitz.Pixmap(dados)
    if pix.n - pix.alpha >= 4:  # CMYK → RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    for _ in range(4):
        pix.shrink(1)
        out = pix.tobytes("jpeg", jpg_quality=85)
        if len(out) <= ML_FOTO_MAX_BYTES:
            return _nome_com_extensao(anexo.filename, "image/jpeg"), out, "image/jpeg"
    raise RuntimeError("foto acima de 5 MB e não deu pra reduzir")


# ---------------------------------------------------------------- fluxo


async def garantir_chamado(session: AsyncSession, dev: Devolution) -> Chamado | None:
    """Garante o chamado na aba pra uma devolução cujo motivo pede chamado e,
    se a conta é do Mercado Livre, a mensagem de abertura PENDENTE (canal
    `api`). Devolve o chamado que precisa de disparo (`agendar_disparo`), ou
    None quando não há o que fazer (motivo sem chamado, já aberto no ML,
    resolvido, conta de outra plataforma). NÃO commita."""
    if not chamados_svc.motivo_pede_chamado(dev):
        return None
    ch = await chamados_svc.abrir_chamado_devolucao(session, dev)
    if ch is None:
        ch = await chamados_svc.chamado_da_devolucao(session, dev)
    if ch is None or ch.resolvido:
        return None
    if not (ch.plataforma or "").strip():
        ch.plataforma = await _plataforma_da_conta(session, dev.conta or ch.conta)
    if not chamados_svc._eh_ml(ch):
        return None
    msg = await mensagem_abertura(session, ch)
    if msg is not None and msg.status == "enviada":
        return None
    if ch.canal == "manual":
        ch.canal = "api"
    if msg is None:
        msg = chamados_svc.nova_mensagem(
            ch,
            texto=texto_padrao(dev, reason_para(dev)),
            tipo=TIPO_ABERTURA,
            autor_nome=chamados_svc.AUTOR_SISTEMA,
            status="pendente",
        )
        msg.canal = "api"
        session.add(msg)
        await session.flush()
    elif msg.status == "falhou":
        msg.status = "pendente"
    return ch


async def disparar(
    session: AsyncSession,
    ch: Chamado,
    dev: Devolution,
    *,
    client: MercadoLivreClient | None = None,
    agora: datetime | None = None,
) -> ChamadoMensagem | None:
    """Tenta abrir no ML agora. Atualiza a mensagem `abertura` do chamado:
    `enviada` (abriu), `pendente` + código (ainda não dá — repete no cron) ou
    `falhou` + erro (o ML recusou / conta sem integração). Nunca levanta; NÃO
    commita."""
    agora = agora or datetime.now(UTC)
    msg = await mensagem_abertura(session, ch)
    if msg is None or msg.status == "enviada":
        return msg
    linhas = await _linhas_do_pedido(session, dev)
    # Sub-motivo de Golpe pode ter sido escolhido em outra linha do kit.
    dev_ref = dev
    if reason_para(dev) is None:
        for outra in linhas:
            if reason_para(outra) is not None:
                dev_ref = outra
                break
    reason = reason_para(dev_ref)
    anexos = await anexos_de(session, [d.id for d in linhas])
    fotos = [a for a in anexos if (a.content_type or "").lower() in ML_FOTO_TIPOS]
    if reason in REASONS_DO_PACOTE:
        fotos = []  # motivo do pacote (SRF7): sem anexo
    msg.texto = texto_padrao(dev_ref, reason, fotos=len(fotos))
    claim_id = return_id = None
    try:
        if reason is None:
            motivo = (dev_ref.motivo_devolucao or "").strip().lower()
            raise _PendenteError(
                "devolucao_sem_tipo_golpe" if motivo == "golpe" else "devolucao_motivo_sem_chamado"
            )
        if reason in REASONS_EXIGEM_FOTO and not fotos:
            raise _PendenteError("devolucao_sem_foto")
        client = client or await chamados_svc._ml_client_para(session, ch.conta or dev.conta)
        claim_id, return_id = await _resolver_claim(client, ch, dev_ref)
        nomes: list[str] = []
        for a in fotos:
            if not a.ml_file_name:
                nome, dados, ctype = preparar_foto(a)
                a.ml_file_name = await client.upload_return_attachment(claim_id, nome, dados, ctype)
                await session.flush()
            nomes.append(a.ml_file_name)
        await client.return_review_fail(
            return_id, reason, msg.texto, attachments=nomes or None
        )
    except _PendenteError as p:
        msg.status = "pendente"
        msg.erro = p.code
        criada = msg.created_at
        if criada is not None and agora - criada > PRAZO_PENDENTE:
            msg.status = "falhou"
            msg.erro = "devolucao_prazo_esgotado"
        return msg
    except chamados_svc.ChamadoError as e:
        msg.status = "falhou"
        msg.erro = e.code
        return msg
    except Exception as e:  # noqa: BLE001 — erro cru da API do ML
        msg.status = "falhou"
        msg.erro = str(e)[:300]
        logger.warning(
            "chamado_devolucao_falhou",
            chamado_id=str(ch.id),
            devolution_id=str(dev.id),
            err=msg.erro,
        )
        return msg

    msg.status = "enviada"
    msg.erro = None
    msg.enviada_at = agora
    msg.canal = "api"
    ch.chamado = claim_id
    ch.canal = "api"
    ch.monitoramento = True
    for a in fotos:
        session.add(
            ChamadoAnexo(
                chamado_id=ch.id,
                mensagem_id=msg.id,
                filename=a.filename,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                blob=a.blob,
                created_by=a.created_by,
            )
        )
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Chamado aberto no Mercado Livre — revisão da devolução: "
            f"{REASON_NOME.get(reason, reason)} ({len(fotos)} foto(s)); claim {claim_id}",
        )
    )
    logger.info(
        "chamado_devolucao_aberto",
        chamado_id=str(ch.id),
        claim_id=claim_id,
        return_id=return_id,
        reason=reason,
        fotos=len(fotos),
    )
    return msg


async def disparar_por_id(session: AsyncSession, chamado_id: UUID) -> ChamadoMensagem | None:
    ch = await session.get(Chamado, chamado_id)
    if ch is None or ch.origem != "devolucao":
        return None
    dev = None
    if ch.origem_ref:
        try:
            dev = await session.get(Devolution, UUID(str(ch.origem_ref)))
        except ValueError:
            dev = None
    if dev is None:
        msg = await mensagem_abertura(session, ch)
        if msg is not None and msg.status != "enviada":
            msg.status = "falhou"
            msg.erro = "devolucao_nao_encontrada"
        return msg
    return await disparar(session, ch, dev)


async def agendar_disparo(session: AsyncSession, ch: Chamado, dev: Devolution) -> None:
    """Depois do commit do router: manda o disparo pro worker (a conversa com
    o ML — upload de foto — pode demorar). Sem Redis (teste / fila fora),
    roda inline na sessão dada e commita."""
    if ENFILEIRAR:
        try:
            from app.worker_pool import get_arq_pool

            pool = await get_arq_pool()
            await pool.enqueue_job("chamado_devolucao_disparar", str(ch.id))
            return
        except Exception as e:  # noqa: BLE001 — fila indisponível → inline
            logger.warning(
                "chamado_devolucao_enqueue_falhou", chamado_id=str(ch.id), err=str(e)[:200]
            )
    await disparar(session, ch, dev)
    await session.commit()


async def processar_pendentes(
    session: AsyncSession, *, agora: datetime | None = None
) -> dict:
    """Cron (de hora em hora): retenta as aberturas `pendente` (ML ainda não
    liberou a revisão, foto que chegou depois, sub-motivo escolhido depois).
    Best-effort por linha; commita no fim."""
    rows = (
        await session.execute(
            select(ChamadoMensagem, Chamado)
            .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
            .where(
                ChamadoMensagem.tipo == TIPO_ABERTURA,
                ChamadoMensagem.status == "pendente",
                Chamado.origem == "devolucao",
                Chamado.resolvido.is_(False),
            )
            .order_by(ChamadoMensagem.created_at)
        )
    ).all()
    abertos = pendentes = falhas = 0
    for msg, ch in rows:
        try:
            dev = None
            if ch.origem_ref:
                dev = await session.get(Devolution, UUID(str(ch.origem_ref)))
            if dev is None:
                msg.status = "falhou"
                msg.erro = "devolucao_nao_encontrada"
                falhas += 1
                continue
            r = await disparar(session, ch, dev, agora=agora)
        except Exception as e:  # noqa: BLE001
            falhas += 1
            logger.warning(
                "chamado_devolucao_pendente_erro", chamado_id=str(ch.id), err=str(e)[:200]
            )
            continue
        st = (r.status if r is not None else msg.status) or ""
        if st == "enviada":
            abertos += 1
        elif st == "pendente":
            pendentes += 1
        else:
            falhas += 1
    await session.commit()
    return {"verificados": len(rows), "abertos": abertos, "pendentes": pendentes, "falhas": falhas}
