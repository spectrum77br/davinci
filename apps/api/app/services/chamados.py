"""Chamados — regras e ações da aba (Pós-venda → Chamados).

A aba centraliza os chamados abertos nas plataformas, venham da Margem
(diferença de frete), da Logística (mediação/reclamação do comprador) ou de
uma Devolução. Este módulo concentra o que não é CRUD:

- `lookup_pedido` / `preencher_do_pedido`: espelho do pedido (bling_orders +
  store_info) pra preencher Data / pedidos / plataforma / conta / produto / sku
  / status Bling sem digitação.
- `enviar_mensagem`: despacha uma réplica pelo canal do chamado — `api` manda
  na mediação do Mercado Livre (claim já aberto pelo comprador; o vendedor não
  abre reclamação do zero), `robo` deixa `pendente` na fila do robô de browser
  (chamados de formulário/protocolo, sem API), `manual` só registra.
- `aplicar_status_bling`: muda a situação do pedido no Bling (mesmo PATCH
  dedicado da Logística) e carimba o histórico.
- `run_replica_automatica`: cron — reenvia a mensagem automática a cada N dias
  enquanto ligada, e fecha sozinho os chamados de API com monitoramento quando
  o ML encerra o claim.

Regra combinada com o usuário (planilha, célula "alterar status bling"):
Logística → "Problemas" ao abrir e "Resolvido"/"Perdimento" ao fechar; Margem
não altera; Devolução ficou em branco (sem padrão).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Chamado, ChamadoMensagem, Devolution, SituacaoBling, StoreInfo
from app.services import logistica_bling, logistica_meli
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Situação Bling padrão ao ABRIR um chamado, por origem (célula M2 da planilha).
STATUS_ABERTURA_POR_ORIGEM: dict[str, str] = {"logistica": "Problemas"}
# Opções oferecidas ao FECHAR, por origem.
STATUS_FECHAMENTO_POR_ORIGEM: dict[str, tuple[str, ...]] = {
    "logistica": ("Resolvido", "Perdimento"),
}

AUTOR_SISTEMA = "sistema"
AUTOR_AUTO = "réplica automática"


class ChamadoError(Exception):
    """Falha de negócio com código legível pro endpoint (422)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------- pedido


async def situacoes_nomes(session: AsyncSession) -> list[str]:
    nomes = (
        (await session.execute(select(SituacaoBling.nome).distinct().order_by(SituacaoBling.nome)))
        .scalars()
        .all()
    )
    return [n for n in nomes if n]


async def status_bling_atual_map(session: AsyncSession, numeros: set[str]) -> dict[str, str]:
    """Situação ATUAL do pedido no espelho bling_orders, por número (nome via
    situacao_bling; fallback no id cru). Em lote — a listagem tem N linhas."""
    limpos = {n for n in numeros if n}
    if not limpos:
        return {}
    rows = await session.execute(
        select(
            BlingOrder.numero,
            func.max(func.coalesce(SituacaoBling.nome, BlingOrder.situacao)),
        )
        .join(SituacaoBling, cast(SituacaoBling.id, Text) == BlingOrder.situacao, isouter=True)
        .where(BlingOrder.numero.in_(list(limpos)))
        .group_by(BlingOrder.numero)
    )
    return {str(numero): nome for numero, nome in rows.all() if numero and nome}


async def lookup_pedido(session: AsyncSession, pedido: str) -> dict | None:
    """Dados do pedido pra preencher a linha: casa `numero` (Bling) OU
    `numeroloja` (marketplace). Produto/SKU = itens do pedido deduplicados,
    juntados por "; " / ", ". None se o pedido não está no espelho."""
    pedido = (pedido or "").strip()
    if not pedido:
        return None
    rows = list(
        (
            await session.execute(
                select(BlingOrder)
                .where(or_(BlingOrder.numero == pedido, BlingOrder.numeroloja == pedido))
                .order_by(BlingOrder.data.desc().nulls_last(), BlingOrder.item_index)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    first = rows[0]
    # O espelho pode ter mais de um pedido com o mesmo numeroloja (raro):
    # fica só com os itens do pedido mais recente.
    itens = [r for r in rows if r.numero == first.numero]
    produtos: list[str] = []
    skus: list[str] = []
    for r in itens:
        if r.item_descricao and r.item_descricao not in produtos:
            produtos.append(r.item_descricao)
        if r.item_codigo and r.item_codigo not in skus:
            skus.append(r.item_codigo)

    store = None
    if first.loja:
        store = (
            await session.execute(
                select(StoreInfo).where(StoreInfo.bling_store_id == str(first.loja)).limit(1)
            )
        ).scalar_one_or_none()

    situacao_nome = None
    if first.situacao:
        situacao_nome = (
            await session.execute(
                select(SituacaoBling.nome).where(cast(SituacaoBling.id, Text) == first.situacao)
            )
        ).scalar_one_or_none()

    return {
        "data": first.data.astimezone(SAO_PAULO).date() if first.data else None,
        "pedido_bling": first.numero,
        "pedido_marketplace": first.numeroloja,
        "plataforma": (store.platform if store else None),
        "conta": (store.account_name if store else None),
        "produto": "; ".join(produtos) or None,
        "sku": ", ".join(skus) or None,
        "status_bling": situacao_nome or first.situacao,
    }


async def preencher_do_pedido(session: AsyncSession, ch: Chamado) -> bool:
    """Completa os campos VAZIOS do chamado com o espelho do pedido. Nunca
    sobrescreve o que o operador digitou. True se achou o pedido."""
    info = await lookup_pedido(session, ch.pedido_bling or ch.pedido_marketplace or "")
    if not info:
        return False
    for campo, valor in info.items():
        if valor is None:
            continue
        if getattr(ch, campo) in (None, ""):
            setattr(ch, campo, valor)
    return True


# ---------------------------------------------------------------- histórico


def nova_mensagem(
    ch: Chamado,
    *,
    texto: str,
    tipo: str,
    direcao: str = "enviada",
    autor_nome: str | None = None,
    autor_id: UUID | None = None,
    status: str = "registrada",
) -> ChamadoMensagem:
    return ChamadoMensagem(
        chamado_id=ch.id,
        direcao=direcao,
        tipo=tipo,
        texto=texto,
        canal=ch.canal,
        status=status,
        autor_nome=autor_nome,
        autor_id=autor_id,
    )


def registrar_sistema(ch: Chamado, texto: str) -> ChamadoMensagem:
    """Evento do sistema no histórico (status alterado, resolvido, etc.)."""
    return nova_mensagem(
        ch, texto=texto, tipo="sistema", direcao="sistema", autor_nome=AUTOR_SISTEMA
    )


# ---------------------------------------------------------------- devolução

# Motivos de devolução que ABREM CHAMADO SOZINHOS na aba Chamados (Eduardo
# 03/09: "mudou de ideia … tem que abrir chamado sozinho / oque for golpe abre
# chamado sozinho / quando for item faltando … tbm / Não recebido, abrir
# chamado / danificado a mesma coisa"). Comparação case-insensitive pra
# aguentar variação de digitação em linhas antigas (motivo é texto livre).
# "Bloqueado" é o nome novo de "Mudou de ideia" (03/09: "mudou de ideia -
# bloqueado"; migration 0239 renomeou as linhas antigas) — o legado fica na
# lista por segurança. "Item Incorreto" entrou 04/09 ("produto diferente usa o
# status item incorreto") — é a revisão SRF4 do ML.
MOTIVOS_ABREM_CHAMADO = frozenset(
    {
        "bloqueado",
        "mudou de ideia",
        "golpe",
        "item incorreto",
        "item faltando",
        "não recebido",
        "danificado (outros)",
    }
)


def motivo_pede_chamado(dev: Devolution) -> bool:
    return (dev.motivo_devolucao or "").strip().lower() in MOTIVOS_ABREM_CHAMADO


async def chamado_da_devolucao(session: AsyncSession, dev: Devolution) -> Chamado | None:
    """Chamado de origem `devolucao` já registrado pra essa linha: por pedido
    Bling (kit com 3 linhas = 1 chamado) ou, sem pedido, pelo id da linha
    (`origem_ref`). O mais recente quando houver mais de um."""
    conds = [Chamado.origem == "devolucao"]
    if (dev.pedido_bling or "").strip():
        conds.append(Chamado.pedido_bling == dev.pedido_bling.strip())
    else:
        conds.append(Chamado.origem_ref == str(dev.id))
    return (
        await session.execute(
            select(Chamado).where(*conds).order_by(Chamado.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def abrir_chamado_devolucao(session: AsyncSession, dev: Devolution) -> Chamado | None:
    """Abre (registra) automaticamente um chamado de origem `devolucao` quando
    o motivo da devolução pede chamado. Aqui só REGISTRA na aba Chamados (canal
    `manual`); quem leva pro Mercado Livre — revisão da devolução com problema,
    com as fotos da linha — é `services/chamados_devolucao.garantir_chamado`,
    que troca o canal pra `api` quando a conta é ML.

    Dedupe: UM chamado de devolução por pedido Bling (kit com 3 linhas de
    devolução não vira 3 chamados); sem pedido Bling, cai pro id da linha
    (`origem_ref`). NÃO commita — o caller controla a transação. Devolve o
    chamado criado, ou None quando o motivo não pede/já existe."""
    motivo = (dev.motivo_devolucao or "").strip()
    if motivo.lower() not in MOTIVOS_ABREM_CHAMADO:
        return None
    if await chamado_da_devolucao(session, dev) is not None:
        return None
    ch = Chamado(
        data=datetime.now(SAO_PAULO).date(),
        pedido_bling=(dev.pedido_bling or "").strip() or None,
        pedido_marketplace=(dev.pedido_marketplace or "").strip() or None,
        conta=dev.conta,
        produto=dev.produtos,
        sku=dev.sku,
        origem="devolucao",
        origem_ref=str(dev.id),
        canal="manual",
        observacao=f"Aberto automaticamente pela devolução — motivo: {motivo}",
    )
    # Espelho do pedido completa o que a devolução não tem (plataforma/status
    # Bling/data) sem sobrescrever o que veio dela.
    await preencher_do_pedido(session, ch)
    session.add(ch)
    await session.flush()
    session.add(
        registrar_sistema(ch, f"Chamado aberto automaticamente pela devolução (motivo: {motivo})")
    )
    logger.info(
        "chamado_auto_devolucao",
        chamado_id=str(ch.id),
        devolution_id=str(dev.id),
        pedido_bling=ch.pedido_bling,
        motivo=motivo,
    )
    return ch


# ---------------------------------------------------------------- envio


def _eh_ml(ch: Chamado) -> bool:
    return (ch.plataforma or "").strip().lower() in logistica_meli._ML_PLATAFORMAS


async def _ml_client_para(session: AsyncSession, conta: str | None) -> MercadoLivreClient:
    integ = await logistica_meli._ml_integration_for_conta(session, conta)
    if integ is None:
        raise ChamadoError("chamado_sem_integracao_ml")
    return logistica_meli._build_ml_client(session, integ)


async def _enviar_api_ml(session: AsyncSession, ch: Chamado, texto: str) -> None:
    """Manda `texto` na reclamação (claim) do pedido via API do ML. Fala com o
    mediador quando a mediação está aberta; senão com o comprador."""
    if not (ch.chamado or "").strip():
        raise ChamadoError("chamado_sem_numero")
    if not _eh_ml(ch):
        raise ChamadoError("chamado_nao_ml")
    client = await _ml_client_para(session, ch.conta)
    claim = await client.get_claim(ch.chamado.strip())
    if (claim.get("status") or "").lower() == "closed":
        raise ChamadoError("chamado_encerrado")
    actions = logistica_meli._respondent_actions(claim)
    if "send_message_to_mediator" in actions:
        role = "mediator"
    elif "send_message_to_complainant" in actions:
        role = "complainant"
    else:
        raise ChamadoError("chamado_sem_acao")
    await client.send_claim_message(ch.chamado.strip(), texto, receiver_role=role)


async def enviar_mensagem(
    session: AsyncSession, ch: Chamado, msg: ChamadoMensagem
) -> ChamadoMensagem:
    """Despacha a mensagem pelo canal do chamado e atualiza status/erro/
    enviada_at nela. NÃO commita — o caller controla a transação. Nunca
    levanta: falha vira `status='falhou'` + `erro` (o histórico mostra)."""
    msg.canal = ch.canal
    if ch.canal == "manual":
        msg.status = "registrada"
    elif ch.canal == "robo":
        # Fila do robô de browser (formulário/protocolo). Ele marca enviada.
        msg.status = "pendente"
    else:
        try:
            await _enviar_api_ml(session, ch, msg.texto)
            msg.status = "enviada"
            msg.enviada_at = datetime.now(UTC)
        except ChamadoError as e:
            msg.status = "falhou"
            msg.erro = e.code
        except Exception as e:  # noqa: BLE001 — erro cru da API do ML
            msg.status = "falhou"
            msg.erro = str(e)[:300]
            logger.warning("chamado_envio_api_falhou", chamado_id=str(ch.id), err=msg.erro)
    return msg


# ---------------------------------------------------------------- Bling


async def _bling_order_id(session: AsyncSession, ch: Chamado) -> int:
    numero = (ch.pedido_bling or "").strip()
    if not numero:
        raise ChamadoError("chamado_sem_pedido_bling")
    bid = (
        await session.execute(
            select(BlingOrder.bling_id)
            .where(BlingOrder.numero == numero, BlingOrder.bling_id.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if bid is None:
        raise ChamadoError("chamado_pedido_bling_nao_achado")
    return int(bid)


async def aplicar_status_bling(session: AsyncSession, ch: Chamado, nome: str) -> dict:
    """PATCH da situação do pedido no Bling (endpoint dedicado, reversível) e
    sincroniza o snapshot `status_bling` + histórico. NÃO commita."""
    nome = (nome or "").strip()
    if not nome:
        raise ChamadoError("chamado_sem_status_bling")
    sid = await logistica_bling._situacao_id_por_nome_opt(session, nome)
    if sid is None:
        raise ChamadoError("chamado_status_bling_desconhecido")
    bling_id = await _bling_order_id(session, ch)
    try:
        client = await logistica_bling._bling_client(session)
    except logistica_bling.BlingObsError as e:
        raise ChamadoError("chamado_sem_integracao_bling") from e
    await client.update_order_situacao(bling_id, sid)
    # Nome do catálogo pro id realmente aplicado: regra escrita com o apelido
    # legado "Enviado Etiqueta" move pra 21 → snapshot/histórico dizem
    # "Em digitação" (mesmo ajuste de logistica_bling.apply_alterar_status_bling).
    nome_aplicado = await logistica_bling._situacao_nome_por_id(session, sid) or nome
    ch.status_bling = nome_aplicado
    session.add(registrar_sistema(ch, f"Status Bling alterado para {nome_aplicado}"))
    return {"bling_order_id": bling_id, "situacao": nome_aplicado, "situacao_id": sid}


# ---------------------------------------------------------------- resolvido


def marcar_resolvido(
    ch: Chamado, resolvido: bool, *, autor_nome: str | None = None
) -> ChamadoMensagem:
    agora = datetime.now(UTC)
    ch.resolvido = resolvido
    ch.resolvido_at = agora if resolvido else None
    if resolvido:
        # Réplica automática não faz sentido em chamado fechado.
        ch.auto_ligada = False
        quem = f" por {autor_nome}" if autor_nome else ""
        return registrar_sistema(ch, f"Chamado marcado como resolvido{quem}")
    return registrar_sistema(ch, f"Chamado reaberto{(' por ' + autor_nome) if autor_nome else ''}")


# ---------------------------------------------------------------- cron


def auto_proximo_envio(ch: Chamado) -> datetime | None:
    """Quando a próxima réplica automática sai (ou None se desligada). Base =
    último envio automático; ao ligar, o PATCH carimba `auto_ultimo_envio_at`
    pra primeira réplica só sair depois de N dias."""
    if not ch.auto_ligada or not ch.auto_dias or ch.resolvido:
        return None
    base = ch.auto_ultimo_envio_at or ch.created_at
    if base is None:
        return None
    return base + timedelta(days=ch.auto_dias)


async def run_replica_automatica(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Passada do cron (de hora em hora):
    1. réplica automática ligada + vencida → cria a mensagem no histórico e
       despacha pelo canal; carimba `auto_ultimo_envio_at` (mesmo se falhou —
       a próxima tentativa é dali a N dias, sem spammar o histórico);
    2. monitoramento em chamado de API → se o ML já encerrou o claim, marca
       resolvido sozinho.
    Best-effort por linha: falha de uma não derruba as outras."""
    agora = agora or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(Chamado).where(
                    Chamado.resolvido.is_(False),
                    or_(Chamado.auto_ligada.is_(True), Chamado.monitoramento.is_(True)),
                )
            )
        )
        .scalars()
        .all()
    )
    enviados = resolvidos = falhas = 0
    for ch in rows:
        texto = (ch.auto_mensagem or "").strip()
        if ch.auto_ligada and ch.auto_dias and texto:
            proximo = auto_proximo_envio(ch)
            if proximo is None or proximo <= agora:
                msg = nova_mensagem(ch, texto=texto, tipo="replica_auto", autor_nome=AUTOR_AUTO)
                session.add(msg)
                await enviar_mensagem(session, ch, msg)
                ch.auto_ultimo_envio_at = agora
                enviados += 1
                if msg.status == "falhou":
                    falhas += 1
        if ch.monitoramento and ch.canal == "api" and (ch.chamado or "").strip() and _eh_ml(ch):
            try:
                client = await _ml_client_para(session, ch.conta)
                claim = await client.get_claim(ch.chamado.strip())
                if (claim.get("status") or "").lower() == "closed":
                    ch.resolvido = True
                    ch.resolvido_at = agora
                    ch.auto_ligada = False
                    session.add(
                        registrar_sistema(ch, "Chamado encerrado na plataforma (claim fechado)")
                    )
                    resolvidos += 1
            except Exception as e:  # noqa: BLE001
                falhas += 1
                logger.warning(
                    "chamado_monitoramento_falhou", chamado_id=str(ch.id), err=str(e)[:300]
                )
    await session.commit()
    return {
        "verificados": len(rows),
        "enviados": enviados,
        "resolvidos": resolvidos,
        "falhas": falhas,
    }
