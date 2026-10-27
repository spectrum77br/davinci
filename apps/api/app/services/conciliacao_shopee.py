"""Conciliação — Shopee: a carteira é o extrato (passo 3, 15/10/2026).

Sondagem de 15/10 na loja Jlas (docs/conciliacao-avaliacao.md, seção 18): a chave
que o DaVinci já tem abre `POST /api/v2/payment/get_wallet_transaction_list`, que
devolve cada lançamento da carteira com `current_balance` (saldo após a linha →
prova dos nove igual ao Mercado Pago), `order_sn` na renda do pedido, saques,
ajustes, penalidades e Ads na mesma lista. Limites: ~15 dias por chamada e 100
por página (`more`). Não existe "fatura" na Shopee: as tarifas já vêm descontadas
na renda do pedido (o detalhe é o `escrow_detail`, que a Margem já lê).

Leitura: um dia por vez (fuso de Brasília), do último dia lido (sobreposição de
um dia; linha repetida é barrada pelo unique `transaction_id`) até ontem.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConciliacaoDia,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    ConciliacaoSync,
    Integration,
)
from app.models.conciliacao import ORIGEM_CARTEIRA
from app.services.conciliacao_dias import reabrir_dias
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ROTA_CARTEIRA = "/api/v2/payment/get_wallet_transaction_list"

# transaction_type da Shopee → tipo nosso (os mesmos nomes do extrato do MP onde cabe)
TIPOS = {
    "ESCROW_VERIFIED_ADD": "venda_liberada",
    # 22/10 (kfa 300588): pedido reembolsado libera NEGATIVO ("Renda do pedido" −0,49, a
    # taxa) — é a liberação do pedido; caía em "outro" e o pedido nunca "liberava"
    "ESCROW_VERIFIED_MINUS": "venda_liberada",
    "ESCROW_VERIFIED_DEDUCT": "estorno",
    # compensação da Shopee por devolução (prazo de carência, produto bloqueado)
    "RETURN_COMPENSATION_SERVICE_ADD": "compensacao_shopee",
    "SELLER_COMPENSATE_ADD": "compensacao_shopee",
    "WITHDRAWAL_CREATED": "saque",
    "WITHDRAWAL_COMPLETED": "saque_confirmado",
    "WITHDRAWAL_CANCELLED": "saque_cancelado",
    "ADJUSTMENT_CENTER_ADD": "ajuste_shopee",
    "ADJUSTMENT_CENTER_DEDUCT": "ajuste_shopee",
    "SPM_DEDUCT": "publicidade",
    "SPM_ADD": "publicidade",
}
# Linha sem pedido que o robô já sabe o que é: ganha a classificação sozinha.
CLASSIFICACAO_AUTOMATICA = {
    "saque": "saque",
    "saque_confirmado": "saque",
    "saque_cancelado": "saque",
    "publicidade": "publicidade",
    # ajuste_shopee (penalidade etc.): sem dono até uma pessoa classificar (16/10 à noite)
}


def _dinheiro(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def e_imposto_difal(descricao: str | None) -> bool:
    """ "Débito por diferencial de alíquota do ICMS (DIFAL)": a Shopee cobra por pedido,
    mas a linha vem sem o nº do pedido. 19/10 (Vinicius): classificar "imposto" sozinho
    — 75 linhas de centavos sem dono em 6 lojas — e mostrar a soma no quadro "Cobranças
    da plataforma" do Resumo, pra ninguém deixar de ver."""
    d = (descricao or "").lower()
    return "diferencial de al" in d and "icms" in d


def transacao_para_movimento(t: dict[str, Any]) -> dict[str, Any] | None:
    """Um lançamento da carteira → colunas de `conciliacao_movimentos` (sem a loja)."""
    tid = t.get("transaction_id")
    ct = t.get("create_time")
    if tid is None or not isinstance(ct, int | float):
        return None
    tipo_shopee = str(t.get("transaction_type") or "")
    tipo = TIPOS.get(tipo_shopee, "outro")
    valor = _dinheiro(t.get("amount"))
    order_sn = str(t.get("order_sn") or "").strip() or None
    descricao = (
        str(t.get("description") or "").strip()
        or str(t.get("reason") or "").strip()
        or str(t.get("txn_title") or "").strip()
        or tipo_shopee
    )
    saldo = t.get("current_balance")
    classificacao = None if order_sn else CLASSIFICACAO_AUTOMATICA.get(tipo)
    if tipo == "ajuste_shopee" and not order_sn and e_imposto_difal(descricao):
        tipo, classificacao = "imposto", "imposto"
    return {
        "origem": ORIGEM_CARTEIRA,
        "id_externo": str(tid),
        "data": datetime.fromtimestamp(int(ct), tz=UTC),
        "tipo": tipo,
        "descricao": descricao,
        "sub_tipo": tipo_shopee or None,
        "pagamento_id": str(t.get("refund_sn") or "").strip() or None,
        "pedido_marketplace": order_sn,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        # a carteira só traz o líquido; a abertura (tarifa, frete) está no escrow_detail
        "bruto": None,
        "tarifa": None,
        "parcelamento": None,
        "frete": None,
        "cupom": None,
        "credito": valor if valor > 0 else Decimal("0"),
        "debito": -valor if valor < 0 else Decimal("0"),
        "saldo_apos": _dinheiro(saldo) if isinstance(saldo, int | float) else None,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": classificacao,
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": t,
    }


async def renomear_tipos_novos(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Lançamento da carteira gravado como "outro" cujo transaction_type o painel passou
    a conhecer (22/10: liberação negativa e compensação): ganha o tipo certo."""
    mov = ConciliacaoMovimento
    conhecidos = (
        "ESCROW_VERIFIED_MINUS",
        "RETURN_COMPENSATION_SERVICE_ADD",
        "SELLER_COMPENSATE_ADD",
    )
    rows = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_CARTEIRA,
                    mov.tipo == "outro",
                    mov.sub_tipo.in_(conhecidos),
                )
            )
        )
        .scalars()
        .all()
    )
    for m in rows:
        m.tipo = TIPOS[m.sub_tipo]
    if rows:
        await session.flush()
    return len(rows)


async def classificar_impostos(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Linha de DIFAL já gravada como "ajuste da Shopee" sem dono (antes da regra de
    19/10): vira imposto, classificada. O que uma pessoa mexeu fica como está."""
    mov = ConciliacaoMovimento
    rows = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_CARTEIRA,
                    mov.tipo == "ajuste_shopee",
                    mov.pedido_bling.is_(None),
                    mov.vinculado_por.is_(None),
                    mov.classificacao.is_(None),
                    mov.descricao.ilike("%diferencial de al%"),
                )
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for m in rows:
        if e_imposto_difal(m.descricao):
            m.tipo = "imposto"
            m.classificacao = "imposto"
            n += 1
    if n:
        await session.flush()
    return n


async def ler_carteira(
    cliente: Any, t_de: int, t_ate: int, *, page_size: int = 100, max_paginas: int = 50
) -> list[dict[str, Any]]:
    """Todos os lançamentos entre dois instantes (epoch), paginando até `more` = false."""
    out: list[dict[str, Any]] = []
    for page_no in range(max_paginas):
        r = await cliente._request(
            "POST",
            ROTA_CARTEIRA,
            json={
                "page_no": page_no,
                "page_size": page_size,
                "create_time_from": t_de,
                "create_time_to": t_ate,
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"carteira: HTTP {r.status_code} {r.text[:200]}")
        body = r.json() or {}
        if body.get("error"):
            raise RuntimeError(f"carteira: {body.get('error')}: {body.get('message')}")
        resp = body.get("response") if isinstance(body.get("response"), dict) else {}
        lista = [x for x in (resp.get("transaction_list") or []) if isinstance(x, dict)]
        out.extend(lista)
        if not resp.get("more") or not lista:
            break
    return out


async def _sync_row(session: AsyncSession, loja: ConciliacaoLoja, chave: str) -> ConciliacaoSync:
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_CARTEIRA,
                ConciliacaoSync.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_CARTEIRA,
            chave=chave,
            status="pedido",
            detalhe={},
        )
        session.add(row)
        await session.flush()
    return row


async def inserir_movimentos(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    movimentos: list[dict[str, Any]],
    *,
    reabrir: bool = True,
) -> int:
    """Grava o que ainda não existe (identidade = id_externo). Linha nova num dia já
    fechado reabre o dia (16/10, revisão, aprovado): quem fechou conferiu outra lista."""
    for m in movimentos:
        m["integration_id"] = loja.integration_id
        m["platform"] = loja.platform
    inseridos = 0
    datas: set[date] = set()
    for k in range(0, len(movimentos), 200):
        stmt = (
            pg_insert(ConciliacaoMovimento)
            .values(movimentos[k : k + 200])
            .on_conflict_do_nothing(constraint="uq_conciliacao_mov_externo")
            .returning(ConciliacaoMovimento.id, ConciliacaoMovimento.data)
        )
        for _id, data in (await session.execute(stmt)).all():
            inseridos += 1
            if data is not None:
                datas.add(data.astimezone(BRT).date())
    await session.flush()
    if reabrir and datas:
        await reabrir_dias(session, loja, datas, motivo="linha nova no extrato")
    return inseridos


PREFIXO_AJUSTE_SALDO = "ajuste-saldo-"


async def _dia_fechado(session: AsyncSession, loja: ConciliacaoLoja, dia: date) -> bool:
    return (
        await session.execute(
            select(ConciliacaoDia.id).where(
                ConciliacaoDia.integration_id == loja.integration_id,
                ConciliacaoDia.dia == dia,
                ConciliacaoDia.fechado_em.isnot(None),
            )
        )
    ).scalar_one_or_none() is not None


async def remover_ajustes_do_robo(session: AsyncSession, loja: ConciliacaoLoja) -> list[str]:
    """16/10 à noite (Vinicius, essência do painel): quando a carteira da Shopee muda de
    saldo sem listar o lançamento, o robô NÃO inventa mais a linha "diferença de saldo
    não detalhada" (até R$ 10) pra fechar o dia — a diferença aparece e eles decidem.
    As linhas que o robô tinha criado (e ninguém mexeu) saem, e o dia delas reabre."""
    linhas = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_CARTEIRA,
                    ConciliacaoMovimento.id_externo.like(f"{PREFIXO_AJUSTE_SALDO}%"),
                    ConciliacaoMovimento.vinculado_por.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not linhas:
        return []
    dias = {m.data.astimezone(BRT).date() for m in linhas if m.data}
    ids = [m.id_externo for m in linhas]
    for m in linhas:
        await session.delete(m)
    await session.flush()
    await reabrir_dias(session, loja, dias, motivo="ajuste automático de saldo da Shopee removido")
    logger.info("conciliacao_shopee_ajustes_removidos", loja=loja.conta, linhas=ids)
    return ids


PREFIXO_NAO_LISTADO = "nao-listado-"
# pedido que ainda espera a liberação: é onde cabe a renda que a carteira não listou
_SITUACOES_SEM_LIBERACAO = ("aguardando", "vencido")
_TOL_PULO = Decimal("0.005")
# um pulo pode juntar mais de um desconto (Barbosa 15/10: −0,98 = dois devolvidos)
_MAX_PEDIDOS_POR_PULO = 3
_MAX_CANDIDATOS_POR_PULO = 30


def _pedidos_do_pulo(cabem: list[ConciliacaoPedido], pulo: Decimal) -> list[ConciliacaoPedido]:
    """Os pedidos cuja venda soma exatamente o pulo: um só antes de dois, e os mais
    novos primeiro (`cabem` já vem do mais novo pro mais velho)."""
    for k in range(1, _MAX_PEDIDOS_POR_PULO + 1):
        for combo in combinations(cabem, k):
            if sum((p.venda_liquido or Decimal("0") for p in combo), Decimal("0")) == pulo:
                return list(combo)
    return []


def _linhas_nao_listadas(
    loja: ConciliacaoLoja,
    m: ConciliacaoMovimento,
    antes: ConciliacaoMovimento | None,
    pulo: Decimal,
    pedidos: list[ConciliacaoPedido],
    inicio_dia: datetime,
) -> list[ConciliacaoMovimento]:
    """As linhas que faltaram, logo antes da linha `m` em que o saldo pulou: uma por
    pedido provável, ou uma sem dono com o pulo inteiro."""
    partes: list[tuple[ConciliacaoPedido | None, Decimal]] = [
        (p, p.venda_liquido or Decimal("0")) for p in pedidos
    ] or [(None, pulo)]
    # saldo antes da linha `m` = o que a carteira tinha depois dos descontos
    saldo = Decimal(m.saldo_apos) - (m.credito or Decimal("0")) + (m.debito or Decimal("0"))
    saldo -= pulo  # antes dos descontos
    piso = antes.data if antes is not None else inicio_dia
    linhas: list[ConciliacaoMovimento] = []
    for i, (pedido, valor) in enumerate(partes):
        saldo += valor
        quando = m.data - timedelta(seconds=len(partes) - i)
        comum: dict[str, Any] = {
            "integration_id": loja.integration_id,
            "platform": loja.platform,
            "origem": ORIGEM_CARTEIRA,
            "id_externo": f"{PREFIXO_NAO_LISTADO}{m.id_externo}" + (f"-{i + 1}" if i else ""),
            "data": max(quando, piso),
            "credito": Decimal("0"),
            "debito": -valor,
            "saldo_apos": saldo,
            "raw": {
                "motivo": "saldo da carteira pulou sem lançamento listado",
                "antes_da_linha": m.id_externo,
                "pulo": str(pulo),
                "pedido_provavel": pedido.pedido_bling if pedido else None,
            },
        }
        if pedido is None:
            linhas.append(
                ConciliacaoMovimento(
                    **comum,
                    tipo="ajuste_shopee",
                    sub_tipo="SALDO_NAO_LISTADO",
                    descricao="Desconto que a Shopee não mostrou no extrato "
                    "(nenhum pedido devolvido com esse valor)",
                )
            )
        else:
            linhas.append(
                ConciliacaoMovimento(
                    **comum,
                    tipo="venda_liberada",
                    sub_tipo="ESCROW_NAO_LISTADO",
                    descricao=f"Renda do pedido #{pedido.numeroloja} — a Shopee descontou e "
                    "não mostrou no extrato (pedido provável)",
                    pedido_marketplace=pedido.numeroloja,
                    pedido_bling=pedido.pedido_bling,
                    bling_id=pedido.bling_id,
                )
            )
    return linhas


async def lancar_descontos_nao_listados(
    session: AsyncSession, loja: ConciliacaoLoja, *, ate: date
) -> list[dict[str, Any]]:
    """23/10 (Vinicius, ATV e mega "saldo não bate −0,49"): a carteira desconta a renda
    negativa de pedido devolvido (−0,49: a taxa de proteção de envio que a Shopee guarda)
    e às vezes NÃO lista a linha — o saldo da renda seguinte sobe 0,49 a menos. ATV tinha
    5 pulos de 19 a 22/10 e 5 pedidos "Aguardando Devolução" com venda −0,49 e nada na
    carteira; mega, 1 e 1.

    No ponto do pulo (só pra baixo, só dia aberto) o robô escreve a linha que faltou e
    liga ao pedido devolvido provável: ainda esperando a liberação, venda da Margem igual
    ao pulo (ou dois/três que somam o pulo) e nenhuma linha na carteira. Liga do pulo mais
    novo pro mais velho, cada um com o pedido mais novo que cabe: pulo antigo (05/10) é de
    pedido de antes do início do painel, não dos devolvidos de agora. Qual pulo é de qual
    pedido é palpite (o valor é o mesmo). Sem pedido provável a linha fica SEM DONO.
    Refeito a cada leitura: se a linha de verdade aparecer, o pulo some e a do robô sai.
    Linha do robô que uma pessoa mexeu (vínculo ou classificação) fica."""
    from app.routers.conciliacao import _ordena_dia, _saldo_anterior

    mov = ConciliacaoMovimento
    fechados = set(
        (
            await session.execute(
                select(ConciliacaoDia.dia).where(
                    ConciliacaoDia.integration_id == loja.integration_id,
                    ConciliacaoDia.fechado_em.isnot(None),
                )
            )
        ).scalars()
    )
    ini = datetime.combine(loja.inicio, datetime.min.time(), tzinfo=BRT)
    fim = datetime.combine(ate + timedelta(days=1), datetime.min.time(), tzinfo=BRT)
    carteira = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_CARTEIRA,
                    mov.data >= ini,
                    mov.data < fim,
                )
            )
        )
        .scalars()
        .all()
    )
    if not carteira:
        return []

    def do_robo(m: ConciliacaoMovimento) -> bool:
        return (
            (m.id_externo or "").startswith(PREFIXO_NAO_LISTADO)
            and m.vinculado_por is None
            and m.classificacao is None
        )

    por_dia: dict[date, list[ConciliacaoMovimento]] = defaultdict(list)
    for m in carteira:
        por_dia[m.data.astimezone(BRT).date()].append(m)

    # 1. os pulos de cada dia aberto, sem as linhas do robô que ninguém mexeu
    robo_por_linha: dict[str, list[ConciliacaoMovimento]] = defaultdict(list)
    pulos: list[tuple[date, ConciliacaoMovimento, ConciliacaoMovimento | None, Decimal]] = []
    for dia in sorted(por_dia):
        if dia in fechados:
            continue
        resto: list[ConciliacaoMovimento] = []
        for m in por_dia[dia]:
            if do_robo(m):
                robo_por_linha[str((m.raw or {}).get("antes_da_linha") or "")].append(m)
            else:
                resto.append(m)
        anterior = await _saldo_anterior(session, loja, dia)
        if anterior is None or not resto:
            continue
        saldo = Decimal(anterior)
        antes: ConciliacaoMovimento | None = None
        for m in _ordena_dia(resto, saldo):
            saldo += (m.credito or Decimal("0")) - (m.debito or Decimal("0"))
            if m.saldo_apos is not None:
                pulo = Decimal(m.saldo_apos) - saldo
                saldo = Decimal(m.saldo_apos)
                if pulo < -_TOL_PULO:
                    pulos.append((dia, m, antes, pulo))
            antes = m

    # 2. pulo que já tem as linhas certas do robô fica como está
    manter: set[Any] = set()
    a_ligar = []
    for p in pulos:
        existentes = robo_por_linha.get(p[1].id_externo, [])
        soma = sum(
            ((e.debito or Decimal("0")) - (e.credito or Decimal("0")) for e in existentes),
            Decimal("0"),
        )
        if existentes and soma == -p[3]:
            manter.update(e.id for e in existentes)
        else:
            a_ligar.append(p)

    # 3. pedido provável: sem linha na carteira (de verdade ou do robô que fica)
    com_linha = {
        x
        for m in carteira
        if not do_robo(m) or m.id in manter
        for x in (m.pedido_bling, m.pedido_marketplace)
        if x
    }
    livres = sorted(
        (
            p
            for p in (
                await session.execute(
                    select(ConciliacaoPedido).where(
                        ConciliacaoPedido.integration_id == loja.integration_id,
                        ConciliacaoPedido.situacao.in_(_SITUACOES_SEM_LIBERACAO),
                        ConciliacaoPedido.venda_liquido < 0,
                    )
                )
            ).scalars()
            if p.pedido_bling not in com_linha and (p.numeroloja or "") not in com_linha
        ),
        key=lambda p: (p.data_pedido or datetime.min.replace(tzinfo=UTC), p.pedido_bling),
        reverse=True,
    )
    escolhas: dict[str, list[ConciliacaoPedido]] = {}
    for _dia, m, _antes, pulo in sorted(a_ligar, key=lambda x: x[1].data, reverse=True):
        cabem = [p for p in livres if p.data_pedido is None or p.data_pedido <= m.data]
        escolha = _pedidos_do_pulo(cabem[:_MAX_CANDIDATOS_POR_PULO], pulo)
        for p in escolha:
            livres.remove(p)
        escolhas[m.id_externo] = escolha

    # 4. sai a linha do robô que não serve mais (a de verdade apareceu) e entram as novas
    for linhas in robo_por_linha.values():
        for e in linhas:
            if e.id not in manter:
                await session.delete(e)
    await session.flush()
    lancadas: list[dict[str, Any]] = []
    for dia, m, antes, pulo in a_ligar:
        inicio_dia = datetime.combine(dia, datetime.min.time(), tzinfo=BRT)
        for linha in _linhas_nao_listadas(loja, m, antes, pulo, escolhas[m.id_externo], inicio_dia):
            session.add(linha)
            lancadas.append(
                {
                    "dia": dia.isoformat(),
                    "antes_da_linha": m.id_externo,
                    "valor": str(-linha.debito),
                    "pedido": linha.pedido_bling,
                }
            )
    await session.flush()
    if lancadas:
        logger.info("conciliacao_shopee_nao_listados", loja=loja.conta, linhas=lancadas)
    return lancadas


async def run_carteira_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    hoje: date | None = None,
    max_dias: int = 20,
) -> dict[str, Any]:
    """Lê a carteira dia a dia, do último dia lido (de novo, por segurança) até ontem."""
    agora = datetime.now(UTC)
    hoje = hoje or datetime.now(BRT).date()
    ontem = hoje - timedelta(days=1)
    lidos = (
        await session.execute(
            select(ConciliacaoSync.chave).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_CARTEIRA,
                ConciliacaoSync.status == "lido",
            )
        )
    ).scalars()
    ultimo = max((date.fromisoformat(c) for c in lidos), default=None)
    inicio = ultimo if ultimo else loja.inicio
    dias: list[date] = []
    d = inicio
    while d <= ontem and len(dias) < max_dias:
        dias.append(d)
        d += timedelta(days=1)
    resumo: dict[str, Any] = {"dias": [], "inseridos": 0, "chamadas": 0, "erros": []}
    if not dias:
        return resumo
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    # 16/10, revisão: dia fechado (ConciliacaoDia.fechado_em) não tem ajuste apagado nem
    # recriado — nem na releitura do último dia lido (sobreposição) nem na varredura
    # abaixo. Linha nova ainda entra (quem reabre o dia por ela é outro lote).
    fechados = set(
        (
            await session.execute(
                select(ConciliacaoDia.dia).where(
                    ConciliacaoDia.integration_id == loja.integration_id,
                    ConciliacaoDia.fechado_em.isnot(None),
                )
            )
        ).scalars()
    )
    for dia in dias:
        t_de = int(datetime.combine(dia, datetime.min.time(), tzinfo=BRT).timestamp())
        t_ate = t_de + 86400 - 1
        row = await _sync_row(session, loja, dia.isoformat())
        row.pedido_em = agora
        try:
            lista = await ler_carteira(cliente, t_de, t_ate)
            resumo["chamadas"] += 1 + len(lista) // 100
            movs = [m for m in (transacao_para_movimento(t) for t in lista) if m]
            ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
            mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
            for m in movs:
                achado = mapa.get(m["pedido_marketplace"] or "")
                if achado:
                    m["pedido_bling"], m["bling_id"] = achado
            inseridos = await inserir_movimentos(session, loja, movs)
        except Exception as e:  # noqa: BLE001
            row.status = "erro"
            row.erro = f"{type(e).__name__}: {str(e)[:300]}"
            resumo["erros"].append({"dia": dia.isoformat(), "erro": row.erro})
            logger.warning(
                "conciliacao_carteira_falhou", loja=loja.conta, dia=dia.isoformat(), erro=row.erro
            )
            break  # mantém a ordem dos dias; tenta de novo na próxima rodada
        pulos: list[Decimal] = []  # sem ajuste automático (16/10 à noite)
        row.status = "lido"
        row.lido_em = agora
        row.erro = None
        row.linhas = len(lista)
        row.detalhe = {
            **(row.detalhe or {}),
            "inseridos": inseridos,
            "ajustes_de_saldo": [str(d) for d in pulos],
        }
        loja.ultimo_extrato_em = agora
        resumo["inseridos"] += inseridos
        resumo["dias"].append(
            {
                "dia": dia.isoformat(),
                "linhas": len(lista),
                "inseridos": inseridos,
                "ajustes_de_saldo": [str(d) for d in pulos],
                **({"fechado": True} if dia in fechados else {}),
            }
        )
    nao_listados = await lancar_descontos_nao_listados(session, loja, ate=ontem)
    if nao_listados:
        resumo["nao_listados"] = nao_listados
    removidos = await remover_ajustes_do_robo(session, loja)
    if removidos:
        resumo["ajustes_removidos"] = removidos
    await session.flush()
    return resumo


async def reler_escrow_liberados(
    session: AsyncSession, loja: ConciliacaoLoja, *, maximo: int = 60
) -> dict[str, Any]:
    """Venda liberada cujo escrow a Margem leu ANTES da liberação: relê agora.

    15/10, os 5 "recebido a menos" da Jlas: a Shopee só põe a comissão de
    Afiliados (`order_ams_commission_fee`) e a taxa de serviço final no escrow na
    hora de liberar; a Margem tinha lido dias antes e guardava 16–73 a mais. A
    carteira estava certa. Então: pra cada renda de pedido cuja leitura da Margem
    é mais velha que a liberação, pede à Margem pra reler (uma chamada por pedido).
    """
    from app.models import MarketplaceOrderFinancial
    from app.services.marketplace_financials import run_sync_marketplace_financials_for_bling_order

    mov = ConciliacaoMovimento
    fin = MarketplaceOrderFinancial
    rows = (
        await session.execute(
            select(mov.bling_id, mov.pedido_marketplace, mov.data, fin.fetched_at)
            .select_from(mov)
            .join(
                fin,
                (fin.integration_id == mov.integration_id)
                & (fin.external_order_id == mov.pedido_marketplace),
            )
            .where(
                mov.integration_id == loja.integration_id,
                mov.origem == ORIGEM_CARTEIRA,
                mov.tipo == "venda_liberada",
                mov.bling_id.isnot(None),
                fin.fetched_at < mov.data,
            )
            .order_by(mov.data.desc())
            .limit(maximo)
        )
    ).all()
    vistos: set[int] = set()
    relidos = 0
    erros: list[dict[str, Any]] = []
    for bling_id, order_sn, _data, _lido in rows:
        if bling_id in vistos:
            continue
        vistos.add(bling_id)
        try:
            # 27/10 (303469): pedido excluído no Bling que a Shopee pagou também relê
            await run_sync_marketplace_financials_for_bling_order(
                session,
                bling_order_id=int(bling_id),
                trigger="conciliacao_liberacao",
                mesmo_excluido=True,
            )
            relidos += 1
        except Exception as e:  # noqa: BLE001
            erros.append({"pedido": order_sn, "erro": f"{type(e).__name__}: {str(e)[:200]}"})
            logger.warning("conciliacao_escrow_releitura_falhou", loja=loja.conta, pedido=order_sn)
    await session.flush()
    return {"candidatos": len(rows), "relidos": relidos, "erros": erros}
