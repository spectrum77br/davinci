"""Devolução → chamado AUTOMÁTICO na plataforma, com fotos (ML, TikTok, Shopee).

Eduardo, 04/09: "Todos esses motivos aí (Bloqueado, Golpe, Item faltando,
Não recebido, Danificado), se for adicionado lá, vai abrir o chamado
automático … vai ter foto sim e vídeo" / "precisamos fazer isso com todos os
marketplaces". O "chamado" de devolução que o vendedor abre é a contestação
da devolução recebida com problema:

- **Mercado Livre**: revisão da devolução com problema
  (`POST /post-purchase/v1/returns/{return_id}/return-review`, SRF2–SRF7);
  só quando o ML liberou `return_review_fail` pro vendedor.
- **TikTok Shop**: recusa do pacote recebido
  (`POST /return_refund/202309/returns/{return_id}/reject`,
  decision REJECT_RECEIVED_PACKAGE + reverse_reject_return_parcel_reason_1..5);
  só com return_status=BUYER_SHIPPED_ITEM, dentro do prazo do vendedor.
- **Shopee**: disputa (`POST /api/v2/returns/dispute` com motivo de
  `get_return_dispute_reason` + fotos por módulo de evidência); a partir do
  pacote entregue/aceito (status ACCEPTED, compensação pendente).
- **Amazon** (e demais): NÃO tem API — SAFE-T só no Seller Central. Fica
  registrado na aba com aviso de abrir na mão.

Fluxo:
1. tela Devoluções marca um motivo da lista → `garantir_chamado` registra o
   chamado na aba (services/chamados.abrir_chamado_devolucao) e, se a
   plataforma tem API, deixa uma mensagem `abertura` PENDENTE e troca o canal
   pra `api`;
2. o router enfileira `agendar_disparo` (worker) → `disparar`: resolve o caso
   na plataforma, sobe as fotos da linha (uma vez cada — `ml_file_name` guarda
   a referência), manda a contestação. O que não dá pra fazer AINDA (sem
   foto, plataforma ainda não liberou) fica `pendente` com o código em `erro`
   — a tela mostra e o cron de hora em hora (`processar_pendentes`) tenta de
   novo, até 45 dias;
3. deu certo → `enviada`, chamado ganha a referência (claim/return) e canal
   `api` (no ML com monitoramento ligado — fecha sozinho quando encerrar).

Vídeo: nenhuma das APIs aceita vídeo do vendedor; o link do vídeo entra no
texto e o arquivo fica guardado na linha.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid5

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoAnexo,
    DevolucaoRastreio,
    Devolution,
    StoreInfo,
    User,
    UserRole,
)
from app.services import chamados as chamados_svc
from app.services import logistica_meli, logistica_rules, logistica_shopee, logistica_tiktok
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

TIPO_ABERTURA = "abertura"
ACAO_REVISAO = "return_review_fail"

PLAT_ML = "ml"
PLAT_TIKTOK = "tiktok"
PLAT_SHOPEE = "shopee"
PLAT_AMAZON = "amazon"
# Plataformas em que a contestação sai pela API.
COM_API = frozenset({PLAT_ML, PLAT_TIKTOK, PLAT_SHOPEE})

# Motivo da tela (minúsculo) → motivo do ML (Eduardo 04/09: "situação golpe é
# pacote vazio, produto diferente usa o status item incorreto").
MOTIVO_ML: dict[str, str] = {
    "item faltando": "SRF3",  # devolução incompleta
    "danificado (outros)": "SRF2",  # produto chegou danificado (exige foto)
    "item incorreto": "SRF4",  # produto diferente do enviado (exige foto)
    "golpe": "SRF5",  # pacote veio sem o produto
    "não recebido": "SRF7",  # pacote não chegou (motivo do pacote, sem anexo)
    # "Bloqueado" = mala voltou travada por senha (Eduardo 04/09: "produto veio,
    # mas bloqueado por senha abre chamado tbm") → outro problema com o produto.
    "bloqueado": "SRF6",
    "mudou de ideia": "SRF6",  # nome antigo de "Bloqueado" (migration 0239)
}
# TikTok — reject reasons do pacote recebido (REJECT_RECEIVED_PACKAGE):
# _1 produto devolvido não é o enviado | _2 não elegível (usado/quebrado) |
# _3 faltam produtos/partes | _4 não recebi o pacote | _5 danificado ou usado.
_TT = "reverse_reject_return_parcel_reason_"
MOTIVO_TIKTOK: dict[str, str] = {
    "item faltando": _TT + "3",
    "danificado (outros)": _TT + "5",
    "item incorreto": _TT + "1",
    "golpe": _TT + "3",  # pacote vazio = todos os itens faltando
    "não recebido": _TT + "4",
    "bloqueado": _TT + "5",  # mala com senha = usada, não revendável
    "mudou de ideia": _TT + "5",
}
# Shopee — o id do motivo muda por devolução/região: casa pelo TEXTO (inglês)
# devolvido em get_return_dispute_reason, na ordem de preferência.
MOTIVO_SHOPEE: dict[str, tuple[str, ...]] = {
    "item faltando": ("incomplete return", "missing"),
    "danificado (outros)": ("physical damage", "damage"),
    "item incorreto": ("wrong return product", "wrong"),
    "golpe": ("incomplete return", "wrong return product", "missing"),
    "não recebido": ("did not receive", "not receive"),
    "bloqueado": ("claim incorrect", "item is used", "used"),
    "mudou de ideia": ("claim incorrect", "item is used", "used"),
}
REASONS_EXIGEM_FOTO = frozenset({"SRF2", "SRF4"})
REASONS_DO_PACOTE = frozenset({"SRF7"})
REASON_NOME: dict[str, str] = {
    "SRF2": "produto chegou danificado",
    "SRF3": "devolução incompleta",
    "SRF4": "produto diferente do enviado",
    "SRF5": "produto não estava no pacote",
    "SRF6": "outro problema com o produto",
    "SRF7": "pacote da devolução não chegou",
    _TT + "1": "produto devolvido não é o enviado",
    _TT + "2": "não elegível (usado/quebrado)",
    _TT + "3": "faltam produtos ou partes",
    _TT + "4": "não recebi o pacote",
    _TT + "5": "produto danificado ou usado",
}
# Motivos da tela em que a foto é obrigatória (ML exige em SRF2/SRF4; TikTok e
# Shopee pedem evidência em tudo que é "recebi com problema").
MOTIVOS_EXIGEM_FOTO = frozenset({"danificado (outros)", "item incorreto"})

# O que as APIs aceitam como evidência (e o teto por arquivo — ML 5 MB, TikTok
# e Shopee 10 MB; 5 MB serve pra todos).
ML_FOTO_TIPOS = frozenset({"image/jpeg", "image/png", "application/pdf"})
FOTO_TIPOS_IMAGEM = frozenset({"image/jpeg", "image/png"})
ML_FOTO_MAX_BYTES = 5 * 1024 * 1024
TIKTOK_MAX_FOTOS = 6
SHOPEE_MAX_FOTOS_MODULO = 3
# Quanto tempo uma abertura fica pendente esperando a plataforma liberar /
# o operador anexar foto antes de virar `falhou`.
PRAZO_PENDENTE = timedelta(days=45)
# Namespace pro idempotency_key do reject da TikTok (mesmo chamado+return →
# mesma chave → a TikTok não duplica a decisão num retry).
_NS_TIKTOK = UUID("6f2a9c1e-5b3d-4c8e-9a1f-2d7e8b4c3a10")
LINK_SAFET_AMAZON = "https://sellercentral.amazon.com.br/safet-claims"

# Em teste o disparo roda inline na mesma sessão (sem Redis).
ENFILEIRAR = True


class _PendenteError(Exception):
    """Ainda não dá pra abrir — fica `pendente` com o código; o cron repete."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------- plataforma


def plataforma_de(valor: str | None) -> str | None:
    """Normaliza `chamado.plataforma` / `store_info.platform` → ml | tiktok |
    shopee | amazon | (outro, minúsculo) | None."""
    v = (valor or "").strip().lower()
    if not v:
        return None
    if v in logistica_meli._ML_PLATAFORMAS:
        return PLAT_ML
    if v in logistica_rules._TIKTOK_PLATAFORMAS:
        return PLAT_TIKTOK
    if v in logistica_rules._SHOPEE_PLATAFORMAS:
        return PLAT_SHOPEE
    if v in logistica_rules._AMAZON_PLATAFORMAS:
        return PLAT_AMAZON
    return v


def _motivo(dev: Devolution) -> str:
    return (dev.motivo_devolucao or "").strip().lower()


def reason_para(dev: Devolution) -> str | None:
    """Motivo do ML pro motivo da tela (None = não abre chamado)."""
    return MOTIVO_ML.get(_motivo(dev))


def reason_tiktok(dev: Devolution) -> str | None:
    return MOTIVO_TIKTOK.get(_motivo(dev))


def exige_foto(dev: Devolution) -> bool:
    return _motivo(dev) in MOTIVOS_EXIGEM_FOTO


def _base_sku(sku: str | None) -> str:
    """Primeiro SKU da linha (kit vem "dg048.ra+a003.ra"), sem sufixo regional."""
    s = (sku or "").strip().lower()
    if not s:
        return ""
    primeiro = s.replace(",", "+").split("+")[0].strip()
    return primeiro.split(".")[0]


def produto_mala_ou_eletro(sku: str | None) -> bool:
    """Mala (tag mala do sku_tags: b<dígito>, bp*, acessórios a006…) ou eletro
    (celulares dg*, airfryer/eletro u*). É o que exige o Link de envio."""
    from app.services.sku_tags import classify_sku_tag

    s = (sku or "").strip().lower()
    if not s:
        return False
    for parte in s.replace(",", "+").split("+"):
        parte = parte.strip()
        if not parte:
            continue
        if classify_sku_tag(parte) in ("mala", "eletro"):
            return True
        if _base_sku(parte).startswith(("dg", "u")):
            return True
    return False


def link_envio_obrigatorio(dev: Devolution) -> bool:
    """Trava do Eduardo (04/09): "mala e eletro é obrigatória, desde que esteja
    nos motivos que abrem chamado"."""
    return chamados_svc.motivo_pede_chamado(dev) and produto_mala_ou_eletro(dev.sku)


# ---------------------------------------------------------------- texto


_INTRO: dict[str, str] = {
    "SRF2": "Recebemos a devolução e o produto chegou danificado.",
    "SRF3": "Recebemos a devolução incompleta: faltam itens que foram enviados ao comprador.",
    "SRF4": "Recebemos a devolução e o produto devolvido é diferente do que enviamos.",
    "SRF5": "Recebemos o pacote da devolução sem o produto dentro.",
    "SRF6": "Recebemos a devolução com problema no produto.",
    "SRF7": "O pacote da devolução ainda não chegou até nós.",
}


def texto_padrao(
    dev: Devolution,
    reason: str | None,
    *,
    fotos: int = 0,
    link_envio: str | None = None,
) -> str:
    """Mensagem que vai pra plataforma (o operador não digita nada — é
    automático). `reason` é o motivo do ML (SRF*) — nas outras plataformas o
    texto é o mesmo, só muda o código enviado."""
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
    envio = (link_envio or dev.link_envio or "").strip()
    if envio:
        linhas.append(f"Comprovante da expedição (fotos/vídeo do envio): {envio}")
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
    chamado): as fotos e o link do vídeo valem pra qualquer uma."""
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


async def _rastreio_devolucao(
    session: AsyncSession, dev: Devolution, fonte: str
) -> DevolucaoRastreio | None:
    """Linha do Acompanhamento de Devoluções do pedido (o sync de 30 min já
    guarda o id da devolução na plataforma em `devolucao_id_auto`)."""
    pb = (dev.pedido_bling or "").strip()
    if not pb:
        return None
    row = (
        await session.execute(
            select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pb).limit(1)
        )
    ).scalar_one_or_none()
    if row is None or (row.fonte_auto or "").strip().lower() != fonte:
        return None
    return row


async def _email_operador(session: AsyncSession) -> str:
    """E-mail que a Shopee exige no dispute: variável DEVOLUCAO_DISPUTE_EMAIL,
    senão o primeiro admin do DaVinci, senão qualquer usuário."""
    env = (os.environ.get("DEVOLUCAO_DISPUTE_EMAIL") or "").strip()
    if env:
        return env
    for cond in (User.role == UserRole.ADMIN, None):
        q = select(User.email).order_by(User.created_at).limit(1)
        if cond is not None:
            q = q.where(cond)
        email = (await session.execute(q)).scalar_one_or_none()
        if email:
            return str(email)
    return ""


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


# ---------------------------------------------------------------- clientes


async def _contas_candidatas(session: AsyncSession, ch: Chamado, dev: Devolution) -> list[str]:
    """A `conta` da devolução vem da busca do pedido = NOME DA LOJA no Bling
    ("Shopee Marquezini", "Loja 206081922"), que nem sempre é o nome da
    integração ("mega", "injox"). Tenta, nesta ordem: conta do chamado, conta
    da linha e a conta do store_info do pedido (espelho bling_orders)."""
    out: list[str] = []
    for c in (ch.conta, dev.conta):
        c = (c or "").strip()
        if c and c not in out:
            out.append(c)
    info = await chamados_svc.lookup_pedido(session, dev.pedido_bling or ch.pedido_bling or "")
    c = ((info or {}).get("conta") or "").strip()
    if c and c not in out:
        out.append(c)
    return out


async def _ml_client_para(
    session: AsyncSession, ch: Chamado, dev: Devolution
) -> MercadoLivreClient:
    for conta in await _contas_candidatas(session, ch, dev):
        try:
            return await chamados_svc._ml_client_para(session, conta)
        except chamados_svc.ChamadoError:
            continue
    raise chamados_svc.ChamadoError("chamado_sem_integracao_ml")


async def _tiktok_client_para(
    session: AsyncSession, ch: Chamado, dev: Devolution
) -> TikTokClient:
    for conta in await _contas_candidatas(session, ch, dev):
        integ = await logistica_tiktok._tiktok_integration_for_conta(session, conta)
        if integ is not None:
            return logistica_tiktok._build_tiktok_client(session, integ)
    raise chamados_svc.ChamadoError("chamado_sem_integracao_tiktok")


async def _shopee_client_para(
    session: AsyncSession, ch: Chamado, dev: Devolution
) -> ShopeeClient:
    for conta in await _contas_candidatas(session, ch, dev):
        integ = await logistica_shopee._shopee_integration_for_conta(session, conta)
        if integ is not None:
            return logistica_shopee._build_shopee_client(session, integ)
    raise chamados_svc.ChamadoError("chamado_sem_integracao_shopee")


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
    """(nome, bytes, content-type) prontos pra plataforma. Imagem acima de 5 MB
    é reduzida (PyMuPDF, já dependência) até caber; PDF grande não tem redução."""
    ctype = (anexo.content_type or "").lower()
    if ctype not in ML_FOTO_TIPOS:
        raise RuntimeError(f"tipo de anexo não aceito: {ctype}")
    dados = anexo.blob
    if len(dados) <= ML_FOTO_MAX_BYTES:
        return _nome_com_extensao(anexo.filename, ctype), dados, ctype
    if ctype == "application/pdf":
        raise RuntimeError("PDF acima de 5 MB — a plataforma não aceita")
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


def _ref_foto(anexo: DevolucaoAnexo) -> dict:
    """Referência guardada em `ml_file_name` (string no ML/Shopee; JSON na TikTok)."""
    raw = (anexo.ml_file_name or "").strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return {"ref": raw} if raw else {}


# ---------------------------------------------------------------- fluxo


async def garantir_chamado(session: AsyncSession, dev: Devolution) -> Chamado | None:
    """Garante o chamado na aba pra uma devolução cujo motivo pede chamado e,
    se a plataforma tem API, a mensagem de abertura PENDENTE (canal `api`).
    Plataforma sem API (Amazon…) ganha uma mensagem `registrada` com o aviso
    de abrir na mão. Devolve o chamado que precisa de disparo
    (`agendar_disparo`), ou None quando não há o que disparar. NÃO commita."""
    if not chamados_svc.motivo_pede_chamado(dev):
        return None
    ch = await chamados_svc.abrir_chamado_devolucao(session, dev)
    if ch is None:
        ch = await chamados_svc.chamado_da_devolucao(session, dev)
    if ch is None or ch.resolvido:
        return None
    if not (ch.plataforma or "").strip():
        ch.plataforma = await _plataforma_da_conta(session, dev.conta or ch.conta)
    plat = plataforma_de(ch.plataforma)
    msg = await mensagem_abertura(session, ch)
    if plat not in COM_API:
        if msg is None:
            nome = (ch.plataforma or "a plataforma").strip()
            if plat == PLAT_AMAZON:
                texto = (
                    "A Amazon não tem API pra contestar devolução: abrir a reivindicação "
                    f"SAFE-T no Seller Central ({LINK_SAFET_AMAZON}) com as fotos da linha. "
                    "Não emitir reembolso antes — reembolso pelo vendedor perde o direito à SAFE-T."
                )
            else:
                texto = f"{nome} não tem API pra contestar devolução: abrir na mão na plataforma."
            msg = chamados_svc.nova_mensagem(
                ch,
                texto=texto,
                tipo=TIPO_ABERTURA,
                autor_nome=chamados_svc.AUTOR_SISTEMA,
                status="registrada",
            )
            msg.erro = "plataforma_sem_api"
            session.add(msg)
            await session.flush()
        return None
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
    elif msg.status in ("falhou", "registrada"):
        msg.status = "pendente"
        msg.canal = "api"
    return ch


async def _resolver_claim_ml(
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


async def _disparar_ml(
    session: AsyncSession, ch: Chamado, dev: Devolution, fotos: list[DevolucaoAnexo], texto: str
) -> tuple[str, str]:
    reason = reason_para(dev)
    if reason is None:
        raise _PendenteError("devolucao_motivo_sem_chamado")
    if reason in REASONS_DO_PACOTE:
        fotos = []  # motivo do pacote (SRF7): sem anexo
    if reason in REASONS_EXIGEM_FOTO and not fotos:
        raise _PendenteError("devolucao_sem_foto")
    client = await _ml_client_para(session, ch, dev)
    claim_id, return_id = await _resolver_claim_ml(client, ch, dev)
    nomes: list[str] = []
    for a in fotos:
        if not a.ml_file_name:
            nome, dados, ctype = preparar_foto(a)
            a.ml_file_name = await client.upload_return_attachment(claim_id, nome, dados, ctype)
            await session.flush()
        nomes.append(a.ml_file_name)
    await client.return_review_fail(return_id, reason, texto, attachments=nomes or None)
    return claim_id, REASON_NOME.get(reason, reason)


async def _return_tiktok(
    client: TikTokClient, dev: Devolution, preferido: str | None
) -> dict | None:
    """Caso de devolução do pedido na TikTok (returns/search por order_id),
    com status FRESCO. Prefere o id que o Acompanhamento já conhece; senão o
    vivo mais recente. Só RETURN_AND_REFUND tem pacote."""
    oid = (dev.pedido_marketplace or "").strip()
    if not oid:
        raise _PendenteError("devolucao_sem_pedido_marketplace")
    casos = [
        c for c in await client.get_return_list(order_ids=[oid])
        if isinstance(c, dict)
        and str(c.get("return_type") or "").upper() in ("", "RETURN_AND_REFUND")
    ]
    if not casos:
        return None
    if preferido:
        for c in casos:
            if str(c.get("return_id") or "") == preferido:
                return c
    melhor = logistica_tiktok._melhor_devolucao_por_pedido(casos)
    return melhor.get(oid) or casos[-1]


def _tiktok_reason_por_texto(reasons: list[dict], motivo: str) -> str | None:
    """Quando o name esperado não está na lista da TikTok, casa pelo texto
    (pt/en) e, em último caso, usa o ÚNICO motivo de pacote recebido que a
    TikTok oferece — medido ao vivo (04/09, pedido 290845): pra devolução BR a
    lista veio só com `reverse_reject_return_parcel_reason_2` ("O produto foi
    usado e devolvido em uma condição inadequada para revenda") + "acordo com
    o cliente"; recusar com esse é o que o Seller Center também oferece."""
    chaves = {
        "danificado (outros)": ("damaged", "danific", "inadequad", "usado", "used"),
        "bloqueado": ("damaged or used", "usado", "used", "inadequad"),
        "mudou de ideia": ("damaged or used", "usado", "used", "inadequad"),
        "item incorreto": ("not the product", "não é o produto", "diferente", "wrong"),
        "golpe": ("missing", "falt", "not the product", "diferente"),
        "item faltando": ("missing", "falt", "incomplet"),
        "não recebido": ("haven't received", "not received", "não receb"),
    }.get(motivo, ())
    for chave in chaves:
        for r in reasons:
            txt = str(r.get("text") or r.get("reason_text") or "").lower()
            if chave in txt and r.get("name"):
                return str(r["name"])
    de_pacote = [
        str(r["name"]) for r in reasons
        if str(r.get("name") or "").startswith(_TT)
    ]
    if len(de_pacote) == 1 and motivo != "não recebido":
        return de_pacote[0]
    return None


async def _disparar_tiktok(
    session: AsyncSession, ch: Chamado, dev: Devolution, fotos: list[DevolucaoAnexo], texto: str
) -> tuple[str, str]:
    motivo = _motivo(dev)
    reason = reason_tiktok(dev)
    if reason is None:
        raise _PendenteError("devolucao_motivo_sem_chamado")
    if exige_foto(dev) and not fotos:
        raise _PendenteError("devolucao_sem_foto")
    client = await _tiktok_client_para(session, ch, dev)
    rastreio = await _rastreio_devolucao(session, dev, PLAT_TIKTOK)
    preferido = (ch.chamado or "").strip() or (
        (rastreio.devolucao_id_auto or "").strip() if rastreio else ""
    )
    caso = await _return_tiktok(client, dev, preferido or None)
    if caso is None:
        raise _PendenteError("devolucao_sem_return")
    rid = str(caso.get("return_id") or "").strip()
    if not rid:
        raise _PendenteError("devolucao_sem_return")
    ch.chamado = rid
    status = str(caso.get("return_status") or "").strip().upper()
    if caso.get("is_quick_refund"):
        raise chamados_svc.ChamadoError("tiktok_quick_refund")
    if str(caso.get("arbitration_status") or "").upper() == "IN_PROGRESS":
        raise _PendenteError("tiktok_arbitragem")
    if status == "REJECT_RECEIVE_PACKAGE":
        raise chamados_svc.ChamadoError("tiktok_ja_recusada")
    acoes = {
        str(a.get("action") or "").upper()
        for a in (caso.get("seller_next_action_response") or [])
        if isinstance(a, dict)
    }
    if status != "BUYER_SHIPPED_ITEM" or (acoes and "SELLER_RESPOND_RECEIVE_PACKAGE" not in acoes):
        raise _PendenteError("tiktok_aguardando_pacote")
    reasons = await client.get_reject_reasons(rid)
    names = {str(r.get("name") or "") for r in reasons}
    if names and reason not in names:
        alt = _tiktok_reason_por_texto(reasons, motivo)
        if not alt:
            raise chamados_svc.ChamadoError("tiktok_motivo_indisponivel")
        reason = alt
    images: list[dict] = []
    for a in fotos[:TIKTOK_MAX_FOTOS]:
        ref = _ref_foto(a)
        if not ref.get("uri"):
            nome, dados, ctype = preparar_foto(a)
            d = await client.upload_image(nome, dados, ctype)
            ref = {
                "uri": d.get("uri"),
                "width": d.get("width"),
                "height": d.get("height"),
                "mime": ctype,
            }
            a.ml_file_name = json.dumps(ref)
            await session.flush()
        img: dict = {"image_id": ref["uri"], "mime_type": ref.get("mime") or a.content_type}
        if ref.get("width"):
            img["width"] = int(ref["width"])
        if ref.get("height"):
            img["height"] = int(ref["height"])
        images.append(img)
    await client.reject_return(
        rid,
        decision="REJECT_RECEIVED_PACKAGE",
        reject_reason=reason,
        comment=texto,
        images=images or None,
        idempotency_key=str(uuid5(_NS_TIKTOK, f"{ch.id}:{rid}")),
    )
    return rid, REASON_NOME.get(reason, reason)


# Shopee — ids dos motivos de disputa (V2.0 Data Definition, ReturnDisputeReasonId;
# três séries com o mesmo significado: 46–56 SEA, 81–89 BR/TW, 1–13 legado). Medido
# ao vivo 04/09 (return 2608310QMDCH65V, só reembolso): a API devolveu SÓ os ids
# (53, 54) + `dispute_requirement`, sem texto — por isso a tabela.
_SHOPEE_ID_SEM = {
    # devolução COM pacote de volta (recebi com problema)
    46: "nao_recebi", 81: "nao_recebi",
    47: "danificado", 82: "danificado",
    48: "incompleto", 83: "incompleto",
    49: "produto_errado", 84: "produto_errado",
    50: "alegacao_incorreta", 86: "alegacao_incorreta",
    56: "usado", 89: "usado",
    # só reembolso / alegação do comprador
    53: "alegacao_incorreta", 42: "enviei_correto", 43: "enviei_bom_estado",
    41: "enviei_com_prova", 1: "rejeito_nao_recebimento",
    55: "outras_preocupacoes", 44: "sem_acordo", 54: "valor_errado",
}
# motivo da tela → semânticas aceitas, na ordem de preferência
_SHOPEE_PREF_PACOTE: dict[str, tuple[str, ...]] = {
    "danificado (outros)": ("danificado", "usado", "alegacao_incorreta"),
    "item incorreto": ("produto_errado", "alegacao_incorreta"),
    "golpe": ("incompleto", "produto_errado", "alegacao_incorreta"),
    "item faltando": ("incompleto", "alegacao_incorreta"),
    "não recebido": ("nao_recebi",),
    "bloqueado": ("alegacao_incorreta", "usado"),
    "mudou de ideia": ("alegacao_incorreta", "usado"),
}
_SHOPEE_PREF_REEMBOLSO: dict[str, tuple[str, ...]] = {
    # comprador alega (pacote vazio / danificado / errado / faltando) e NÃO há
    # pacote voltando: contesta a alegação com as fotos da expedição
    "golpe": ("alegacao_incorreta", "enviei_correto", "enviei_bom_estado", "enviei_com_prova"),
    "danificado (outros)": ("enviei_bom_estado", "alegacao_incorreta"),
    "item incorreto": ("enviei_correto", "alegacao_incorreta"),
    "item faltando": ("enviei_correto", "alegacao_incorreta"),
    "não recebido": ("rejeito_nao_recebimento", "enviei_com_prova", "alegacao_incorreta"),
    "bloqueado": ("alegacao_incorreta",),
    "mudou de ideia": ("alegacao_incorreta",),
}
_SHOPEE_TEXTO_SEM = (
    ("did not receive", "nao_recebi"), ("physical damage", "danificado"),
    ("incomplete", "incompleto"), ("wrong return product", "produto_errado"),
    ("item is used", "usado"), ("claim is incorrect", "alegacao_incorreta"),
    ("claim incorrect", "alegacao_incorreta"), ("correct item", "enviei_correto"),
    ("good working condition", "enviei_bom_estado"), ("proof of shipment", "enviei_com_prova"),
    ("non-receipt", "rejeito_nao_recebimento"), ("wrong amount", "valor_errado"),
)


def _shopee_semantica(r: dict) -> str | None:
    rid = _shopee_reason_id(r)
    if rid in _SHOPEE_ID_SEM:
        return _SHOPEE_ID_SEM[rid]
    txt = str(r.get("dispute_reason_text") or r.get("reason_text") or r.get("text") or "").lower()
    for chave, sem in _SHOPEE_TEXTO_SEM:
        if chave in txt:
            return sem
    return None


def _shopee_reason(reasons: list[dict], motivo: str, *, so_reembolso: bool = False) -> dict | None:
    """Motivo de disputa da Shopee pro motivo da tela: por id (tabela oficial)
    ou pelo texto, respeitando se o caso tem pacote voltando (devolução) ou é
    só reembolso (contestar a alegação do comprador)."""
    prefs = (_SHOPEE_PREF_REEMBOLSO if so_reembolso else _SHOPEE_PREF_PACOTE).get(motivo, ())
    sem_por_reason = [(r, _shopee_semantica(r)) for r in reasons]
    for sem in prefs:
        for r, s in sem_por_reason:
            if s == sem:
                return r
    return None


def _shopee_reason_id(r: dict) -> int | None:
    v = r.get("dispute_reason")
    if v is None:
        v = r.get("dispute_reason_id")
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _return_sn_shopee(session: AsyncSession, dev: Devolution) -> str | None:
    rastreio = await _rastreio_devolucao(session, dev, PLAT_SHOPEE)
    if rastreio and (rastreio.devolucao_id_auto or "").strip():
        return rastreio.devolucao_id_auto.strip()
    oid = (dev.pedido_marketplace or "").strip()
    pb = (dev.pedido_bling or "").strip()
    if not oid or not pb:
        raise _PendenteError("devolucao_sem_pedido_marketplace")
    linha = SimpleNamespace(
        plataforma="shopee",
        pedido_marketplace=oid,
        pedido_bling=pb,
        conta=dev.conta,
        data=dev.data.date() if dev.data else None,
    )
    achados = await logistica_shopee.returns_por_pedido(session, [linha])  # type: ignore[arg-type]
    info = achados.get(pb)
    return (info.return_id or "").strip() if info else None


async def _disparar_shopee(
    session: AsyncSession, ch: Chamado, dev: Devolution, fotos: list[DevolucaoAnexo], texto: str
) -> tuple[str, str]:
    motivo = _motivo(dev)
    if motivo not in MOTIVO_SHOPEE:
        raise _PendenteError("devolucao_motivo_sem_chamado")
    if motivo != "não recebido" and not fotos:
        # a Shopee exige foto em todo motivo "recebi com problema"
        raise _PendenteError("devolucao_sem_foto")
    client = await _shopee_client_para(session, ch, dev)
    return_sn = (ch.chamado or "").strip() or await _return_sn_shopee(session, dev)
    if not return_sn:
        raise _PendenteError("devolucao_sem_return")
    ch.chamado = return_sn
    det = await client.get_return_detail(return_sn)
    status = str(det.get("status") or "").strip().upper()
    if status in ("SELLER_DISPUTE", "JUDGING"):
        raise chamados_svc.ChamadoError("shopee_ja_contestada")
    if status in ("CLOSED", "CANCELLED"):
        raise chamados_svc.ChamadoError("shopee_devolucao_encerrada")
    if status not in ("REQUESTED", "PROCESSING", "ACCEPTED"):
        raise _PendenteError("shopee_aguardando_pacote")
    comp = det.get("seller_compensation") or {}
    comp_status = str(comp.get("seller_compensation_status") or "").upper()
    # medido ao vivo: vem "PENDING_REQUEST" (sem o prefixo COMPENSATION_ da doc)
    if comp_status.replace("COMPENSATION_", "") in ("REQUESTED", "APPROVED", "REJECTED"):
        raise chamados_svc.ChamadoError("shopee_ja_contestada")
    # return_solution 1 / needs_logistics false = só reembolso: não há pacote
    # voltando — a disputa é contra a alegação do comprador (fotos da expedição).
    so_reembolso = str(det.get("return_solution")) == "1" or det.get("needs_logistics") is False
    reasons = await client.get_return_dispute_reason(return_sn)
    escolhido = _shopee_reason(reasons, motivo, so_reembolso=so_reembolso)
    rid = _shopee_reason_id(escolhido) if escolhido else None
    if escolhido is None or rid is None:
        raise chamados_svc.ChamadoError("shopee_motivo_indisponivel")
    urls: list[str] = []
    for a in fotos:
        ref = _ref_foto(a)
        url = ref.get("ref")
        if not url:
            nome, dados, ctype = preparar_foto(a)
            url = await client.convert_image(return_sn, nome, dados, ctype)
            a.ml_file_name = url
            await session.flush()
        urls.append(url)
    modulos = [
        m for m in (escolhido.get("evidence_module_list") or []) if isinstance(m, dict)
    ]
    image_list: list[dict] = []
    if urls:
        if modulos:
            for m in modulos:
                image_list.append(
                    {
                        "module_index": m.get("module_index"),
                        "requirement": m.get("requirement") or "",
                        "image_url": urls[:SHOPEE_MAX_FOTOS_MODULO],
                    }
                )
        else:
            image_list.append({"module_index": 1, "requirement": "", "image_url": urls[:3]})
    email = await _email_operador(session)
    if not email:
        raise chamados_svc.ChamadoError("shopee_sem_email")
    await client.dispute(
        return_sn,
        email=email,
        dispute_reason_id=rid,
        image_list=image_list or None,
        text=texto,
    )
    nome_motivo = str(
        escolhido.get("dispute_reason_text")
        or escolhido.get("reason_text")
        or f"{_shopee_semantica(escolhido) or 'motivo'} (id {rid})"
    )
    return return_sn, nome_motivo


async def disparar(
    session: AsyncSession,
    ch: Chamado,
    dev: Devolution,
    *,
    agora: datetime | None = None,
) -> ChamadoMensagem | None:
    """Tenta abrir na plataforma agora. Atualiza a mensagem `abertura` do
    chamado: `enviada` (abriu), `pendente` + código (ainda não dá — repete no
    cron) ou `falhou` + erro (a plataforma recusou / conta sem integração).
    Nunca levanta; NÃO commita."""
    agora = agora or datetime.now(UTC)
    msg = await mensagem_abertura(session, ch)
    if msg is None or msg.status == "enviada":
        return msg
    plat = plataforma_de(ch.plataforma)
    linhas = await _linhas_do_pedido(session, dev)
    reason = reason_para(dev)
    anexos = await anexos_de(session, [d.id for d in linhas])
    fotos = [a for a in anexos if (a.content_type or "").lower() in ML_FOTO_TIPOS]
    if plat == PLAT_ML and reason in REASONS_DO_PACOTE:
        fotos = []
    if plat in (PLAT_TIKTOK, PLAT_SHOPEE):
        fotos = [a for a in fotos if (a.content_type or "").lower() in FOTO_TIPOS_IMAGEM]
    # O link da expedição pode estar em outra linha do kit.
    envio = next(
        ((d.link_envio or "").strip() for d in linhas if (d.link_envio or "").strip()), None
    )
    msg.texto = texto_padrao(dev, reason, fotos=len(fotos), link_envio=envio)
    referencia = detalhe = None
    try:
        if plat == PLAT_ML:
            referencia, detalhe = await _disparar_ml(session, ch, dev, fotos, msg.texto)
        elif plat == PLAT_TIKTOK:
            referencia, detalhe = await _disparar_tiktok(session, ch, dev, fotos, msg.texto)
        elif plat == PLAT_SHOPEE:
            referencia, detalhe = await _disparar_shopee(session, ch, dev, fotos, msg.texto)
        else:
            raise chamados_svc.ChamadoError("plataforma_sem_api")
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
    except Exception as e:  # noqa: BLE001 — erro cru da API da plataforma
        msg.status = "falhou"
        msg.erro = str(e)[:300]
        logger.warning(
            "chamado_devolucao_falhou",
            chamado_id=str(ch.id),
            devolution_id=str(dev.id),
            plataforma=plat,
            err=msg.erro,
        )
        return msg

    msg.status = "enviada"
    msg.erro = None
    msg.enviada_at = agora
    msg.canal = "api"
    ch.chamado = referencia
    ch.canal = "api"
    if plat == PLAT_ML:
        ch.monitoramento = True  # o cron fecha quando o ML encerrar o claim
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
    nome_plat = {PLAT_ML: "Mercado Livre", PLAT_TIKTOK: "TikTok Shop", PLAT_SHOPEE: "Shopee"}.get(
        plat, plat or "plataforma"
    )
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Chamado aberto na {nome_plat} — contestação da devolução: {detalhe} "
            f"({len(fotos)} foto(s)); referência {referencia}",
        )
    )
    logger.info(
        "chamado_devolucao_aberto",
        chamado_id=str(ch.id),
        plataforma=plat,
        referencia=referencia,
        motivo=detalhe,
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
    a plataforma — upload de foto — pode demorar). Sem Redis (teste / fila
    fora), roda inline na sessão dada e commita."""
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
    """Cron (de hora em hora): retenta as aberturas `pendente` (plataforma
    ainda não liberou, foto que chegou depois). Best-effort por linha; commita
    no fim."""
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
