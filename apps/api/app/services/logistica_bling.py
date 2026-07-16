"""Executor da Mensagem Bling — anexa a `mensagem_bling` da regra da aba Status
nas Observações (Dados adicionais) do pedido de venda no Bling.

O Bling v3 NÃO tem PATCH parcial de pedido: pra mexer só nas Observações é
preciso reenviar o pedido INTEIRO via `PUT /pedidos/vendas/{id}`. O GET devolve
os objetos aninhados quase todos como referência (`{id: ...}`), mas ainda traz
campos calculados/read-only (`total`, `taxas`, `tributacao`) e objetos com
`id: 0` (`categoria`, `vendedor`, `notaFiscal`, `naturezaOperacao` do item) que
o PUT rejeita. `build_observacoes_put_body` sanitiza o body preservando os
campos que NÃO podem ser perdidos (itens, contato, loja, parcelas, transporte)
e trocando só as Observações.

Regra de escrita (confirmada pelo usuário): APPEND de uma linha datada no TOPO
das Observações, nunca sobrescrever.

`preview_mensagem_bling` faz só GET (leitura) e devolve o body EXATO que seria
enviado — pra conferir antes. `apply_mensagem_bling` faz o PUT de verdade.
"""

from __future__ import annotations

import copy
from datetime import date

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Logistica,
    LogisticaStatus,
    SituacaoBling,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_match, logistica_rules
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()

# Campos calculados/read-only do GET que o Bling recomputa ou rejeita no PUT.
_COMPUTED_DROP = ("totalProdutos", "total", "taxas", "tributacao")


class BlingObsError(Exception):
    """Falha de negócio ao aplicar a Mensagem Bling (código legível pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _id_ref(obj: object) -> dict | None:
    """`{id: N}` quando N>0 (referência válida); None caso contrário (id=0 ou
    ausente — o Bling rejeita `id: 0`)."""
    if isinstance(obj, dict):
        i = obj.get("id")
        try:
            if i is not None and int(i) > 0:
                return {"id": int(i)}
        except (TypeError, ValueError):
            return None
    return None


def compose_observacoes(atual: str | None, mensagem: str, *, hoje: date | None = None) -> str:
    """Monta as Observações novas = linha datada nova NO TOPO + as anteriores.
    Ex.: "15/07 - <mensagem>\\n<observações anteriores>". Não duplica se a
    linha idêntica já estiver no topo."""
    hoje = hoje or date.today()
    linha = f"{hoje:%d/%m} - {mensagem.strip()}"
    atual = (atual or "").strip()
    if not atual:
        return linha
    if atual.splitlines()[0].strip() == linha:
        return atual  # idempotente: já anexada hoje com a mesma mensagem
    return f"{linha}\n{atual}"


def build_observacoes_put_body(order: dict, novo_texto: str) -> dict:
    """Body sanitizado pro PUT /pedidos/vendas/{id} com as Observações trocadas.

    Preserva TUDO que não pode ser perdido (itens, contato, loja, parcelas,
    transporte, desconto, datas) e remove só o que o Bling recomputa ou rejeita:
    campos calculados (`_COMPUTED_DROP`) e objetos com `id: 0`. Nested vira
    referência por id.
    """
    body = copy.deepcopy(order)
    body["observacoes"] = novo_texto
    for k in _COMPUTED_DROP:
        body.pop(k, None)
    # contato: manda só a referência (não mexe no cadastro do cliente).
    ref = _id_ref(body.get("contato"))
    if ref is not None:
        body["contato"] = ref
    # objetos opcionais com id inválido (0) -> remover.
    for k in ("categoria", "vendedor", "notaFiscal"):
        if _id_ref(body.get(k)) is None:
            body.pop(k, None)
    # loja: referência por id.
    lref = _id_ref(body.get("loja"))
    if lref is not None:
        body["loja"] = lref
    # situacao: mantém a MESMA (referência por id) — não movemos status aqui.
    sref = _id_ref(body.get("situacao"))
    if sref is not None:
        body["situacao"] = sref
    else:
        body.pop("situacao", None)
    # itens: produto vira referência; remove sub-objetos zerados/inválidos.
    for it in body.get("itens") or []:
        pref = _id_ref(it.get("produto"))
        if pref is not None:
            it["produto"] = pref
        if _id_ref(it.get("naturezaOperacao")) is None:
            it.pop("naturezaOperacao", None)
        com = it.get("comissao")
        if isinstance(com, dict) and not any(com.get(x) for x in ("base", "aliquota", "valor")):
            it.pop("comissao", None)
    # transporte.contato com id=0 -> remove.
    tp = body.get("transporte")
    if isinstance(tp, dict) and _id_ref(tp.get("contato")) is None:
        tp.pop("contato", None)
    return body


def mensagem_bling_para(rows: list[LogisticaStatus], row: Logistica) -> str | None:
    """`mensagem_bling` da regra da aba Status que casa com a assinatura PT da
    linha (prefere específica da plataforma, cai na geral). None se nenhuma
    casar ou a que casou não tiver mensagem."""
    assinatura = logistica_rules.assinatura_pt(row.meli_status or {})
    rule = logistica_match.find_matching_rule(rows, assinatura=assinatura, plataforma=row.plataforma)
    if rule is None:
        return None
    msg = (rule.mensagem_bling or "").strip()
    return msg or None


async def _bling_order_id_for_row(session: AsyncSession, row: Logistica) -> int:
    """Resolve o id interno do pedido no Bling a partir de `pedido_bling`
    (número) da linha da Logística."""
    numero = (row.pedido_bling or "").strip()
    if not numero:
        raise BlingObsError("logistica_sem_pedido_bling")
    bid = (
        await session.execute(
            select(BlingOrder.bling_id)
            .where(BlingOrder.numero == numero, BlingOrder.bling_id.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if bid is None:
        raise BlingObsError("logistica_pedido_bling_nao_achado")
    return int(bid)


async def _bling_client(session: AsyncSession) -> BlingClient:
    integ = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.BLING).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        raise BlingObsError("logistica_sem_integracao_bling")
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def _resolve(session: AsyncSession, row: Logistica) -> tuple[int, str, BlingClient]:
    """Valida a linha e devolve (bling_order_id, mensagem, client). Levanta
    BlingObsError com código quando falta algo."""
    rows = list((await session.execute(select(LogisticaStatus))).scalars().all())
    mensagem = mensagem_bling_para(rows, row)
    if not mensagem:
        raise BlingObsError("logistica_sem_mensagem_bling")
    bling_id = await _bling_order_id_for_row(session, row)
    client = await _bling_client(session)
    return bling_id, mensagem, client


async def preview_mensagem_bling(session: AsyncSession, row: Logistica) -> dict:
    """Dry-run: faz só GET (leitura) e devolve o que SERIA feito, sem escrever
    no Bling. `{bling_order_id, mensagem, observacoes_atual, observacoes_novo,
    put_body}`."""
    bling_id, mensagem, client = await _resolve(session, row)
    order = await client.get_order(bling_id)
    atual = order.get("observacoes")
    novo = compose_observacoes(atual, mensagem)
    body = build_observacoes_put_body(order, novo)
    return {
        "bling_order_id": bling_id,
        "mensagem": mensagem,
        "observacoes_atual": atual,
        "observacoes_novo": novo,
        "put_body": body,
    }


async def apply_mensagem_bling(session: AsyncSession, row: Logistica) -> dict:
    """Aplica de verdade: GET -> compõe Observações -> PUT. Retorna
    `{bling_order_id, observacoes_novo}`."""
    bling_id, mensagem, client = await _resolve(session, row)
    order = await client.get_order(bling_id)
    novo = compose_observacoes(order.get("observacoes"), mensagem)
    body = build_observacoes_put_body(order, novo)
    await client.update_order(bling_id, body)
    logger.info("logistica_mensagem_bling_aplicada", bling_order_id=bling_id, pedido=row.pedido_bling)
    return {"bling_order_id": bling_id, "observacoes_novo": novo}


# ---- Executor: Alterar Status Bling (muda a situação do pedido no Bling) ----
#
# A regra da aba Status guarda `alterar_status_bling` = o NOME de uma situação
# (dropdown alimentado por `situacao_bling.nome`). O Bling muda situação por ID
# via `PATCH /pedidos/vendas/{id}/situacoes/{idSituacao}` — endpoint dedicado,
# NÃO destrutivo (não reenvia o pedido). Aqui: casa a regra -> nome do alvo ->
# resolve o id da situação -> PATCH.


def _regra_status_para(rows: list[LogisticaStatus], row: Logistica) -> LogisticaStatus | None:
    """Regra da aba Status que casa com a linha (prefere específica, cai na
    geral). None se nenhuma casar."""
    assinatura = logistica_rules.assinatura_pt(row.meli_status or {})
    return logistica_match.find_matching_rule(
        rows, assinatura=assinatura, plataforma=row.plataforma
    )


def alterar_status_bling_para(rows: list[LogisticaStatus], row: Logistica) -> str | None:
    """Nome da situação-alvo (`alterar_status_bling`) da regra que casa. None se
    nenhuma casar ou a regra não pedir mudança."""
    rule = _regra_status_para(rows, row)
    if rule is None:
        return None
    alvo = (rule.alterar_status_bling or "").strip()
    return alvo or None


async def _situacao_id_por_nome(session: AsyncSession, nome: str) -> int:
    """id da situação no Bling a partir do nome (case-insensitive). Levanta se
    o nome não existir no catálogo."""
    sid = await _situacao_id_por_nome_opt(session, nome)
    if sid is None:
        raise BlingObsError("logistica_status_bling_desconhecido")
    return sid


async def _situacao_id_por_nome_opt(session: AsyncSession, nome: str) -> int | None:
    """id da situação por nome (case-insensitive) ou None se não achar."""
    sid = (
        await session.execute(
            select(SituacaoBling.id)
            .where(func.lower(func.trim(SituacaoBling.nome)) == nome.strip().lower())
            .order_by(SituacaoBling.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(sid) if sid is not None else None


async def _situacao_nome_por_id(session: AsyncSession, sid: int) -> str | None:
    return (
        await session.execute(select(SituacaoBling.nome).where(SituacaoBling.id == sid))
    ).scalar_one_or_none()


async def _resolve_status(
    session: AsyncSession, row: Logistica
) -> tuple[int, str | None, int | None, str, int, BlingClient]:
    """Valida a linha e devolve (bling_order_id, nome_de, id_de, nome_alvo,
    id_alvo, client). `de` = coluna "Status Atual" da regra (a situação a partir
    da qual a mudança faz sentido); None quando a regra não define."""
    rows = list((await session.execute(select(LogisticaStatus))).scalars().all())
    rule = _regra_status_para(rows, row)
    alvo = (rule.alterar_status_bling or "").strip() if rule else ""
    if not alvo:
        raise BlingObsError("logistica_sem_status_bling")
    alvo_id = await _situacao_id_por_nome(session, alvo)
    de = (rule.status_atual or "").strip() if rule else ""
    de = de or None
    de_id = await _situacao_id_por_nome_opt(session, de) if de else None
    bling_id = await _bling_order_id_for_row(session, row)
    client = await _bling_client(session)
    return bling_id, de, de_id, alvo, alvo_id, client


async def preview_alterar_status_bling(session: AsyncSession, row: Logistica) -> dict:
    """Dry-run: lê a situação ATUAL do pedido e mostra a transição da regra
    (Status Atual -> Alterar Status Bling), sem escrever. `aplicavel`=o pedido
    está na situação "de" esperada (ou a regra não exige "de"); `ja_no_alvo`=já
    está no alvo."""
    bling_id, de, de_id, alvo, alvo_id, client = await _resolve_status(session, row)
    order = await client.get_order(bling_id)
    atual_id = (order.get("situacao") or {}).get("id")
    atual_id = int(atual_id) if atual_id else None
    atual_nome = await _situacao_nome_por_id(session, atual_id) if atual_id else None
    return {
        "bling_order_id": bling_id,
        "situacao_de": de,
        "situacao_de_id": de_id,
        "situacao_alvo": alvo,
        "situacao_alvo_id": alvo_id,
        "situacao_atual_id": atual_id,
        "situacao_atual_nome": atual_nome,
        "ja_no_alvo": atual_id == alvo_id,
        "aplicavel": de_id is None or atual_id == de_id,
    }


async def apply_alterar_status_bling(session: AsyncSession, row: Logistica) -> dict:
    """Aplica de verdade: só muda se o pedido estiver na situação "de" da regra
    (guarda contra regressão/estado errado), então PATCH da situação no Bling e
    sincroniza o `status_bling` local. `logistica_status_atual_divergente` se o
    pedido não está no "de" esperado."""
    bling_id, de, de_id, alvo, alvo_id, client = await _resolve_status(session, row)
    order = await client.get_order(bling_id)
    atual_id = (order.get("situacao") or {}).get("id")
    atual_id = int(atual_id) if atual_id else None
    if de_id is not None and atual_id != de_id:
        raise BlingObsError("logistica_status_atual_divergente")
    await client.update_order_situacao(bling_id, alvo_id)
    row.status_bling = alvo
    await session.flush()
    logger.info(
        "logistica_alterar_status_bling_aplicada",
        bling_order_id=bling_id,
        situacao_de=de_id,
        situacao_id=alvo_id,
        pedido=row.pedido_bling,
    )
    return {"bling_order_id": bling_id, "situacao_alvo": alvo, "situacao_alvo_id": alvo_id}
