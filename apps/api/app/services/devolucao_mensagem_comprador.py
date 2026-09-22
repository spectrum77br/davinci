"""Mensagem ao COMPRADOR quando a devolução volta travada (Shopee).

Vinicius, 22/09/2026: "quando o pessoal faz o lançamento no painel Devoluções e
coloca motivo Bloqueado, além de abrir chamado, também envie mensagem para o
cliente solicitando a senha … pode incluir a foto se conseguir".

O motivo "Bloqueado" é o aparelho que voltou com a senha de tela do comprador
(ou a mala com o segredo do cadeado). Sem a senha não há revenda, e a disputa
desse motivo não recupera nada: medido em 22/09, "alegação incorreta" (o motivo
que a Shopee oferece pro Bloqueado) está 0 ganhas em 5 disparos. Pedir a senha
ao comprador é a única saída que ainda devolve dinheiro.

**Só a Shopee tem canal hoje** (conferido em produção em 22/09):
- Shopee: o chat responde nas 14 lojas e o comprador sai do próprio pedido
  (`buyer_user_id`), mesmo com o pedido COMPLETED/CANCELLED;
- Mercado Livre: a devolução cancela o pedido e o ML fecha o chat
  (`blocked_by_cancelled_order`, 10 de 10 casos) — e a reclamação já tinha
  encerrado antes do lançamento em 8 de 10. Não há canal, nem por robô;
- TikTok: falta o escopo `seller.customer_service` (401 nas 8 lojas);
- Amazon: só o e-mail de retransmissão, com as regras de conteúdo da Amazon.
Nessas três a linha nasce `sem_canal` — a tela mostra "mandar na mão" em vez de
fingir que pediu.

Uma mensagem por PEDIDO (kit = várias linhas de devolução, um comprador só): a
UNIQUE `(pedido_bling, conta, evento)` é o que segura isso e o gancho do router,
que roda no create, em todo patch de motivo e em todo upload de foto.

A resposta do comprador cai no chat da loja (Duoke/Seller Center) — o DaVinci
ainda não lê chat. `resposta_texto`/`resposta_at` já existem pra quando o vigia
de mensagens entrar (`docs/vigia-mensagens-avaliacao.md`), e o `conversa_id`
guardado no envio é a única chave pra reler a conversa depois (a API da Shopee
não busca conversa por comprador).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Chamado,
    DevolucaoAnexo,
    DevolucaoMensagemComprador,
    Devolution,
)
from app.services import chamados as chamados_svc
from app.services import chamados_devolucao, logistica_shopee
from app.services.marketplaces.shopee import ShopeeClient

logger = structlog.get_logger()

EVENTO_SENHA = "senha"
# Mesmos nomes do cadastro da tela (o legado "mudou de ideia" virou "Bloqueado"
# na migration 0239 e continua na lista por segurança).
MOTIVOS_SENHA = frozenset({"bloqueado", "mudou de ideia"})
PLAT_SHOPEE = chamados_devolucao.PLAT_SHOPEE
# Quantas vezes o cron tenta antes de desistir. Mensagem ao comprador que falha
# 3 vezes é problema de gente, não de retentativa eterna (mesmo teto do
# logistica_cliente_mensagens).
MAX_TENTATIVAS = 3
# A tela salva a linha e só DEPOIS sobe as fotos, uma a uma (293839). O envio
# espera a mesma janela do disparo da contestação pra a foto ir junto.
JANELA_FOTOS = chamados_devolucao.JANELA_FOTOS
# Em teste (sem Redis) o envio roda inline na mesma sessão.
ENFILEIRAR = True
# Teto de bytes da imagem do chat: a Shopee não publica o limite do sellerchat,
# então vale o mesmo teto medido no upload de evidência da disputa.
FOTO_MAX_BYTES = chamados_devolucao.SHOPEE_FOTO_MAX_BYTES

STATUS_PENDENTE = "pendente"
STATUS_ENVIADA = "enviada"
STATUS_FALHOU = "falhou"
STATUS_CANCELADA = "cancelada"
STATUS_SEM_CANAL = "sem_canal"


def motivo_pede_senha(dev: Devolution) -> bool:
    return (dev.motivo_devolucao or "").strip().lower() in MOTIVOS_SENHA


def _tipo_produto(sku: str | None) -> str:
    """"mala" (cadeado com segredo), "apple" (iPhone/iPad, trava de conta
    iCloud) ou "aparelho" (Android e demais eletrônicos, trava de tela +
    conta Google). Medido nos lançamentos de Bloqueado dos últimos 4 meses:
    184 linhas de mala, ~270 de aparelho, 32 Apple."""
    from app.services.sku_tags import classify_sku_tag

    s = (sku or "").strip().lower()
    primeiro = s.replace(",", "+").split("+")[0].strip()
    base = primeiro.split(".")[0]
    if base.startswith("i"):
        return "apple"
    if classify_sku_tag(primeiro) == "mala" or base.startswith("b"):
        return "mala"
    return "aparelho"


def texto_para(dev: Devolution, *, loja: str = "") -> str:
    """Texto aprovado pelo Vinicius em 22/09, com a variação por produto. Nunca
    pede a senha da CONTA (Google/iCloud) — conta se resolve removendo o
    aparelho, e pedir senha de conta ao cliente é pedir credencial."""
    pedido = (dev.pedido_marketplace or dev.pedido_bling or "").strip()
    quem = f"Aqui é a loja {loja.strip()}." if loja.strip() else "Aqui é a loja do seu pedido."
    ref = f" do pedido {pedido}" if pedido else ""
    tipo = _tipo_produto(dev.sku)
    if tipo == "mala":
        return (
            f"Olá! {quem} Recebemos de volta a mala{ref}, mas o cadeado está travado "
            "no segredo que você definiu e não conseguimos abrir para concluir a "
            "análise. Pode nos informar o segredo aqui pelo chat? Obrigado!"
        )
    conta = (
        "à sua conta iCloud, também é preciso removê-lo da conta pelo app Buscar "
        "(a senha da sua conta você não precisa enviar)"
        if tipo == "apple"
        else "à sua conta Google ou iCloud, também é preciso removê-lo da conta "
        "(a senha da sua conta você não precisa enviar)"
    )
    return (
        f"Olá! {quem} Recebemos de volta o aparelho{ref}, mas ele chegou bloqueado "
        "com a sua senha e, sem ela, não conseguimos concluir a análise da devolução. "
        "Você pode nos passar por aqui a senha de desbloqueio da tela? Se o aparelho "
        f"ainda estiver ligado {conta}. Obrigado!"
    )


# ---------------------------------------------------------------- consultas


async def linha_do_pedido(
    session: AsyncSession, dev: Devolution, *, evento: str = EVENTO_SENHA
) -> DevolucaoMensagemComprador | None:
    pedido = (dev.pedido_bling or "").strip()
    if not pedido:
        return None
    return (
        await session.execute(
            select(DevolucaoMensagemComprador).where(
                DevolucaoMensagemComprador.pedido_bling == pedido,
                DevolucaoMensagemComprador.conta == (dev.conta or ""),
                DevolucaoMensagemComprador.evento == evento,
            )
        )
    ).scalar_one_or_none()


async def por_pedidos(
    session: AsyncSession, pedidos: set[str], *, evento: str = EVENTO_SENHA
) -> dict[tuple[str, str], DevolucaoMensagemComprador]:
    """Uma consulta em lote pra listagem da aba Devoluções. A chave é
    (pedido, conta) — a mesma da UNIQUE: o número do Bling é global, mas duas
    contas com o mesmo número não podem se misturar na tela."""
    chaves = {p.strip() for p in pedidos if (p or "").strip()}
    if not chaves:
        return {}
    rows = (
        await session.execute(
            select(DevolucaoMensagemComprador).where(
                DevolucaoMensagemComprador.pedido_bling.in_(sorted(chaves)),
                DevolucaoMensagemComprador.evento == evento,
            )
        )
    ).scalars().all()
    out: dict[tuple[str, str], DevolucaoMensagemComprador] = {}
    for r in rows:
        out.setdefault((r.pedido_bling, r.conta or ""), r)
    return out


async def _plataforma_de(session: AsyncSession, dev: Devolution, ch: Chamado | None) -> str | None:
    bruta = (ch.plataforma if ch is not None else None) or await (
        chamados_devolucao._plataforma_da_conta(session, dev.conta)
    )
    return chamados_devolucao.plataforma_de(bruta)


# ---------------------------------------------------------------- fluxo


async def garantir(
    session: AsyncSession, dev: Devolution, *, created_by: UUID | None = None
) -> DevolucaoMensagemComprador | None:
    """Motivo que pede senha → garante a linha PENDENTE do pedido (uma só).
    Motivo que deixou de pedir → cancela a que ainda não saiu. Devolve a linha
    que precisa de envio (`agendar`), ou None. NÃO commita."""
    pedido = (dev.pedido_bling or "").strip()
    linha = await linha_do_pedido(session, dev)
    if not motivo_pede_senha(dev):
        if linha is not None and linha.status == STATUS_PENDENTE:
            linha.status = STATUS_CANCELADA
            linha.erro = "motivo_mudou"
        return None
    if not pedido:
        return None  # sem pedido não há como achar o comprador na plataforma
    if linha is not None and linha.status in (STATUS_ENVIADA, STATUS_FALHOU):
        return None  # já saiu (não há como desfazer) ou já esgotou as tentativas
    ch = await chamados_svc.chamado_da_devolucao(session, dev)
    plat = await _plataforma_de(session, dev, ch)
    texto = texto_para(dev)
    if linha is None:
        linha = DevolucaoMensagemComprador(
            devolution_id=dev.id,
            chamado_id=ch.id if ch is not None else None,
            pedido_bling=pedido,
            pedido_marketplace=(dev.pedido_marketplace or "").strip() or None,
            conta=dev.conta or "",
            plataforma=plat,
            evento=EVENTO_SENHA,
            texto=texto,
            status=STATUS_PENDENTE if plat == PLAT_SHOPEE else STATUS_SEM_CANAL,
            erro=None if plat == PLAT_SHOPEE else f"sem_canal_{plat or 'plataforma'}",
            created_by=created_by,
        )
        session.add(linha)
        await session.flush()
    else:
        # Linha viva (pendente / cancelada / sem_canal): reaproveita, atualiza o
        # que pode ter mudado no lançamento (SKU → texto, chamado, plataforma).
        linha.devolution_id = dev.id
        if ch is not None:
            linha.chamado_id = ch.id
        linha.plataforma = plat or linha.plataforma
        linha.texto = texto
        if plat == PLAT_SHOPEE:
            linha.status = STATUS_PENDENTE
            linha.erro = None
        elif linha.status != STATUS_SEM_CANAL:
            linha.status = STATUS_SEM_CANAL
            linha.erro = f"sem_canal_{plat or 'plataforma'}"
    return linha if linha.status == STATUS_PENDENTE else None


async def agendar(session: AsyncSession, linha: DevolucaoMensagemComprador | None) -> None:
    """Depois do commit do router: manda o envio pro worker (subir foto e falar
    com a Shopee demora). Sem Redis (teste / fila fora), envia inline."""
    if linha is None:
        return
    if ENFILEIRAR:
        try:
            from app.worker_pool import get_arq_pool

            pool = await get_arq_pool()
            await pool.enqueue_job(
                "devolucao_mensagem_comprador_enviar",
                str(linha.id),
                _job_id=f"devolucao_mensagem_comprador:{linha.id}",
                _defer_by=JANELA_FOTOS,
            )
            return
        except Exception as e:  # noqa: BLE001 — fila indisponível → inline
            logger.warning(
                "devolucao_mensagem_comprador_enqueue_falhou",
                linha_id=str(linha.id),
                err=str(e)[:200],
            )
    await enviar(session, linha)
    await session.commit()


async def _cliente_shopee(
    session: AsyncSession, linha: DevolucaoMensagemComprador, dev: Devolution | None
) -> ShopeeClient:
    """Client da loja. A `conta` da linha é o nome no Bling ("Shopee ATV") e a
    integração se chama outra coisa ("ATV", "Victor Mei") — com a devolução viva
    vale o resolvedor do chamado; sem ela, tenta a conta sem o prefixo."""
    if dev is not None:
        ch = await session.get(Chamado, linha.chamado_id) if linha.chamado_id else None
        return await chamados_devolucao._shopee_client_para(session, ch, dev)
    conta = (linha.conta or "").strip()
    for tentativa in (conta, conta.replace("Shopee", "").strip()):
        if not tentativa:
            continue
        integ = await logistica_shopee._shopee_integration_for_conta(session, tentativa)
        if integ is not None:
            return logistica_shopee._build_shopee_client(session, integ)
    raise chamados_svc.ChamadoError("chamado_sem_integracao_shopee")


async def _foto_do_pedido(
    session: AsyncSession, dev: Devolution | None
) -> DevolucaoAnexo | None:
    """Primeira FOTO de verdade das linhas do pedido — a do produto travado é o
    que o comprador reconhece. Vídeo e o cartão do vídeo (PNG com QR da
    expedição) nunca vão pro comprador."""
    if dev is None:
        return None
    linhas = await chamados_devolucao._linhas_do_pedido(session, dev)
    anexos = await chamados_devolucao.anexos_de(session, [x.id for x in linhas])
    for a in anexos:
        if chamados_devolucao._e_cartao_video(a):
            continue
        if (a.content_type or "").lower() in chamados_devolucao.FOTO_TIPOS_IMAGEM:
            return a
    return None


async def _nome_da_loja(client: ShopeeClient) -> str:
    try:
        r = await client.test_connection()
    except Exception:  # noqa: BLE001 — nome é enfeite, não pode travar o envio
        return ""
    return str((r.info or {}).get("name") or "").strip() if r.ok else ""


async def enviar(
    session: AsyncSession, linha: DevolucaoMensagemComprador
) -> DevolucaoMensagemComprador:
    """Manda a mensagem pro comprador na Shopee. Atualiza a linha: `enviada`
    (com o id da conversa) ou `pendente` + `erro` (o cron retenta até
    MAX_TENTATIVAS, depois `falhou`). NÃO commita."""
    if linha.status != STATUS_PENDENTE:
        return linha
    if not get_settings().shopee_mensagens_comprador:
        linha.erro = "envio_desligado"
        return linha
    dev = await session.get(Devolution, linha.devolution_id) if linha.devolution_id else None
    if dev is not None and not motivo_pede_senha(dev):
        linha.status = STATUS_CANCELADA
        linha.erro = "motivo_mudou"
        return linha
    order_sn = (linha.pedido_marketplace or "").strip()
    try:
        if not order_sn:
            raise chamados_svc.ChamadoError("sem_pedido_marketplace")
        client = await _cliente_shopee(session, linha, dev)
        comprador = await client.get_order_buyer(order_sn)
        to_id = comprador.get("buyer_user_id")
        if not to_id:
            raise chamados_svc.ChamadoError("shopee_sem_comprador")
        texto = linha.texto or (texto_para(dev) if dev is not None else "")
        loja = await _nome_da_loja(client)
        if loja and dev is not None:
            texto = texto_para(dev, loja=loja)
        if not texto.strip():
            raise chamados_svc.ChamadoError("sem_texto")
        resp = await client.chat_send_message(to_id, text=texto)
        linha.texto = texto
        linha.destinatario_id = str(to_id)
        linha.conversa_id = str(resp.get("conversation_id") or "") or None
        linha.mensagem_id = str(resp.get("message_id") or "") or None
        linha.status = STATUS_ENVIADA
        linha.erro = None
        linha.enviada_at = datetime.now(UTC)
    except Exception as e:  # noqa: BLE001 — erro cru da Shopee vira estado da linha
        linha.tentativas += 1
        linha.erro = str(e)[:300]
        if linha.tentativas >= MAX_TENTATIVAS:
            linha.status = STATUS_FALHOU
        logger.warning(
            "devolucao_mensagem_comprador_falhou",
            linha_id=str(linha.id),
            pedido=linha.pedido_bling,
            tentativas=linha.tentativas,
            err=str(e)[:200],
        )
        return linha
    # A foto vai DEPOIS do texto e nunca derruba o envio: mensagem sem imagem
    # ainda resolve; imagem sem contexto, não.
    anexo = await _foto_do_pedido(session, dev)
    if anexo is not None:
        try:
            nome, dados, ctype = chamados_devolucao.preparar_foto(anexo, max_bytes=FOTO_MAX_BYTES)
            url = await client.chat_upload_image(nome, dados, ctype)
            await client.chat_send_message(to_id, image_url=url)
            linha.anexo_id = anexo.id
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "devolucao_mensagem_comprador_foto_falhou",
                linha_id=str(linha.id),
                err=str(e)[:200],
            )
    await _registrar_no_chamado(session, linha)
    return linha


async def _registrar_no_chamado(
    session: AsyncSession, linha: DevolucaoMensagemComprador
) -> None:
    """Evento de sistema no histórico do chamado. `direcao="sistema"`, de
    propósito: a coluna Status da aba Chamados é derivada da última fala
    `enviada`/`recebida`, e uma mensagem ao comprador não pode reescrever o
    status da contestação."""
    if not linha.chamado_id:
        return
    ch = await session.get(Chamado, linha.chamado_id)
    if ch is None:
        return
    quando = (linha.enviada_at or datetime.now(UTC)).astimezone(chamados_svc.SAO_PAULO)
    com_foto = " (com a foto do produto)" if linha.anexo_id else ""
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Mensagem enviada ao comprador no chat da Shopee em "
            f"{quando.strftime('%d/%m/%Y %H:%M')} pedindo a senha do produto{com_foto}. "
            "A resposta cai no chat da loja (Duoke/Seller Center).",
        )
    )


async def reabrir(
    session: AsyncSession, dev: Devolution, *, created_by: UUID | None = None
) -> DevolucaoMensagemComprador | None:
    """Botão "pedir senha" da tela: volta a linha pra `pendente` mesmo quando já
    tinha saído, falhado ou sido cancelada. Serve pro reenvio (o comprador não
    respondeu) e pros lançamentos anteriores a 22/09, que nunca tiveram pedido.
    Devolve a linha a enviar, ou None quando não há canal."""
    linha = await garantir(session, dev, created_by=created_by)
    if linha is not None:
        return linha
    atual = await linha_do_pedido(session, dev)
    if atual is None or not motivo_pede_senha(dev):
        return None
    if atual.status == STATUS_SEM_CANAL:
        return None  # ML/TikTok/Amazon: insistir não cria canal
    atual.status = STATUS_PENDENTE
    atual.erro = None
    atual.tentativas = 0
    if dev.id is not None:
        atual.devolution_id = dev.id
    return atual


async def candidatos_pendentes(
    session: AsyncSession, *, dias: int = 30, limite: int = 50
) -> list[Devolution]:
    """Lançamentos de Bloqueado na Shopee, dentro da janela, que ainda não
    tiveram mensagem enviada — a lista do disparo em lote. Uma linha por
    PEDIDO (a mais recente), porque a mensagem é por pedido."""
    from sqlalchemy import func

    recorte = datetime.now(UTC) - timedelta(days=max(1, dias))
    ja_tem = (
        select(DevolucaoMensagemComprador.pedido_bling)
        .where(
            DevolucaoMensagemComprador.evento == EVENTO_SENHA,
            DevolucaoMensagemComprador.status.in_([STATUS_ENVIADA, STATUS_PENDENTE]),
        )
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Devolution)
            .where(
                func.lower(func.btrim(Devolution.motivo_devolucao)).in_(sorted(MOTIVOS_SENHA)),
                Devolution.conta.ilike("Shopee%"),
                Devolution.created_at > recorte,
                func.btrim(func.coalesce(Devolution.pedido_bling, "")) != "",
                Devolution.pedido_bling.not_in(ja_tem),
            )
            .order_by(Devolution.created_at.desc())
        )
    ).scalars().all()
    vistos: set[str] = set()
    out: list[Devolution] = []
    for dev in rows:
        pedido = (dev.pedido_bling or "").strip()
        if pedido in vistos:
            continue
        vistos.add(pedido)
        out.append(dev)
        if len(out) >= max(1, limite):
            break
    return out


async def enviar_por_id(session: AsyncSession, linha_id: str) -> DevolucaoMensagemComprador | None:
    try:
        uid = UUID(str(linha_id))
    except ValueError:
        return None
    linha = await session.get(DevolucaoMensagemComprador, uid)
    if linha is None:
        return None
    return await enviar(session, linha)


async def processar_pendentes(session: AsyncSession) -> dict:
    """Cron: retenta o que ficou `pendente` (token vencido, Shopee fora do ar,
    foto que chegou depois). Best-effort por linha; commita no fim."""
    rows = (
        await session.execute(
            select(DevolucaoMensagemComprador)
            .where(
                DevolucaoMensagemComprador.status == STATUS_PENDENTE,
                DevolucaoMensagemComprador.tentativas < MAX_TENTATIVAS,
            )
            .order_by(DevolucaoMensagemComprador.created_at)
        )
    ).scalars().all()
    enviadas = pendentes = falhas = 0
    for linha in rows:
        try:
            r = await enviar(session, linha)
        except Exception as e:  # noqa: BLE001
            falhas += 1
            logger.warning(
                "devolucao_mensagem_comprador_pendente_erro",
                linha_id=str(linha.id),
                err=str(e)[:200],
            )
            continue
        if r.status == STATUS_ENVIADA:
            enviadas += 1
        elif r.status == STATUS_PENDENTE:
            pendentes += 1
        else:
            falhas += 1
    await session.commit()
    return {
        "verificados": len(rows),
        "enviadas": enviadas,
        "pendentes": pendentes,
        "falhas": falhas,
    }
