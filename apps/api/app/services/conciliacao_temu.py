"""Conciliação — Temu lida da TELA (17/11/2026, piloto na loja Eron).

A Temu (Brasil) não tem API de extrato, repasse nem saldo. O robô do Mac mini (executor,
fila `conciliacao`, ação `temu_extrato`) abre Finanças › Pagamentos no Seller Center pelo
perfil da loja no AdsPower, navega como uma pessoa e guarda o que a PRÓPRIA PÁGINA carrega:

- Visão geral → o saldo (`/mms/statement/amount/detail`);
- Extratos → uma linha por lançamento, com o nº do pedido PO-… (`/mms/statement/order/list`);
- Pedidos não processados → o que a Temu estima pagar por pedido, antes de pagar
  (`/mms/settle/query/amount/selfproduce`);
- Pagamentos → os saques (`/mms/payment/disbursement/list`).

Ele envia tudo num JSON pra `POST /api/conciliacao/temu/leitura` e aqui vira:

- linhas do extrato em `conciliacao_movimentos` (origem `temu_extrato`), com o saldo
  depois de cada uma. A linha mais nova fica com o Saldo total que a tela informou e as
  anteriores são calculadas pra trás; linha que já estava gravada NÃO é reescrita — se a
  Temu mexer no saldo sem linha (um saque que este leitor ainda não conhece, uma linha
  que faltou), a conta do dia não fecha e aparece no painel;
- o previsto por pedido em `conciliacao_previstos` — fora da Margem, de propósito (ver o
  modelo). Acompanha a tela até a Temu pagar; depois de pago não muda mais, e é com ele
  que o recebido é comparado (essência do painel: comparar, não copiar).

O que ainda NÃO se sabe (a Eron não tem): o formato do saque e como chegam etiqueta,
reserva e publicidade. O que não tem regra vira "outro" sem dono, e saque lido sem
formato conhecido fica no aviso da leitura — nada some.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPrevisto,
    ConciliacaoSync,
    LogisticaRoboComando,
)
from app.models.conciliacao import ORIGEM_TEMU
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling
from app.services.conciliacao_shopee import inserir_movimentos

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ZERO = Decimal("0")

# "Tipo de transação" da tela (biz_category, em inglês na chamada) → tipo do painel.
# O resto (Payment Refund, Seller repayment, Reserve amount, Released amount, Others e o
# que aparecer de novo) fica "outro": sem pedido, sem dono até uma pessoa classificar.
TIPOS = {
    "order payment": "venda_liberada",
    "refund": "estorno",
    "shipping label purchase": "frete",
    "shipping label purchase adjustment": "frete",
    "advertising service fee": "publicidade",
}
CLASSIFICACAO_AUTOMATICA = {"publicidade": "publicidade"}
_PEDIDO = re.compile(r"^PO-\d{3}-\d{5,30}$")
# datas da tela vêm em milissegundos: de 2020 a 2100 (fora disso não é data dela)
_MS_MINIMO = 1_577_836_800_000
_MS_MAXIMO = 4_102_444_800_000


class LeituraInvalidaError(ValueError):
    """A leitura do robô não serve (incompleta ou fora do formato): nada é gravado."""


def centavos(v: Any) -> Decimal | None:
    """Valor da chamada da Temu: `{"amount": 2600, "decimals": 2, …}` → 26.00."""
    if not isinstance(v, dict) or v.get("amount") is None:
        return None
    try:
        casas = int(v.get("decimals") if v.get("decimals") is not None else 2)
        valor = (Decimal(str(v["amount"])) / (Decimal(10) ** casas)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # nas linhas conferidas em 17/11 o `amount` já vem com sinal (reembolso = −2600 e
    # `minus: true`); se um dia vier o valor absoluto com a marca, vale a marca
    if v.get("minus") is True and valor > ZERO:
        valor = -valor
    return valor


# sinal: hífen comum ou o "menos" tipográfico (U+2212)
_REAIS = re.compile(
    "^([-" + "\N{MINUS SIGN}" + r"])?\s*R\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?$"
)


def reais(texto: Any) -> Decimal | None:
    """Valor escrito da tela ("R$26,00", "-R$1.234,56") vira Decimal. Só o que é dinheiro
    de verdade: "Entregue em + 7 dias" não vira 7,00."""
    if texto is None:
        return None
    m = _REAIS.match(str(texto).replace("\N{NO-BREAK SPACE}", " ").strip())
    if not m:
        return None
    valor = Decimal(f"{m.group(2).replace('.', '')}.{(m.group(3) or '0').ljust(2, '0')}")
    return -valor if m.group(1) else valor


def _quando(ms: Any) -> datetime | None:
    try:
        n = int(str(ms).strip())
    except (TypeError, ValueError):
        return None
    if not _MS_MINIMO <= n <= _MS_MAXIMO:
        return None
    return datetime.fromtimestamp(n / 1000, tz=UTC)


def _pedido(*candidatos: Any) -> str | None:
    for c in candidatos:
        s = str(c or "").strip()
        if _PEDIDO.match(s):
            return s
    return None


def saldo_da_tela(saldo: Any) -> dict[str, Decimal | None]:
    """Visão geral: Saldo total = Reserva + Fundos disponíveis."""
    r = saldo.get("result") if isinstance(saldo, dict) and "result" in saldo else saldo
    r = r if isinstance(r, dict) else {}
    reserva = centavos(r.get("reserve_amount"))
    disponivel = centavos(r.get("payout_amount"))
    total = (reserva + disponivel) if (reserva is not None and disponivel is not None) else None
    return {
        "total": total,
        "reserva": reserva,
        "disponivel": disponivel,
        "inicial": centavos(r.get("opening_amount")),
        "vendas": centavos(r.get("sales_amount")),
        "reembolsos": centavos(r.get("refund_amount")),
    }


def linha_para_movimento(item: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha de Extratos (`mall_transaction_flow_volist`) → movimento do painel."""
    quando = _quando(item.get("created_at"))
    total = centavos(item.get("total_amount"))
    flow = str(item.get("flow_id") or "").strip()
    if quando is None or total is None or not flow:
        return None
    categoria = str(item.get("biz_category") or "").strip()
    tipo = TIPOS.get(categoria.lower(), "outro")
    pedido = _pedido(item.get("parent_order_sn"), item.get("order_id"))
    nome = str(item.get("biz_category_text") or categoria or "Lançamento").strip()
    nota = str(item.get("note") or "").strip()
    if tipo == "venda_liberada":
        descricao = f"Pagamento do pedido {pedido}" if pedido else nome
    elif tipo == "estorno":
        descricao = f"Reembolso do pedido {pedido}" if pedido else nome
    else:
        descricao = nome + (f" · pedido {pedido}" if pedido else "")
    if nota:
        # só o começo do nome do produto: o texto inteiro (100+ letras) empurrava as
        # colunas do Extrato pra fora da tela; ele continua na linha original
        descricao = f"{descricao} · {nota[:40]}{'…' if len(nota) > 40 else ''}"
    produto = centavos(item.get("prod_amount"))
    tarifa = centavos(item.get("fee_amount"))
    credito, debito = (total, ZERO) if total >= ZERO else (ZERO, -total)
    return {
        "origem": ORIGEM_TEMU,
        "id_externo": flow[:80],
        "data": quando,
        "tipo": tipo,
        "descricao": descricao[:500],
        "sub_tipo": (str(item.get("sub_biz_category") or categoria)[:120] or None),
        # o lote (`batch_id`) junta todos os pedidos pagos na mesma hora: não é o
        # "pagamento" do pedido — fica só na linha original
        "pagamento_id": None,
        "pedido_marketplace": pedido,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": produto if (tipo == "venda_liberada" and produto) else None,
        "tarifa": -tarifa if tarifa else None,
        "parcelamento": None,
        "frete": None,
        "cupom": None,
        "credito": credito,
        "debito": debito,
        "saldo_apos": None,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": None if pedido else CLASSIFICACAO_AUTOMATICA.get(tipo),
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": {**item, "_tipo_temu": categoria},
    }


def _ordem(m: dict[str, Any]) -> tuple:
    """Ordem das linhas: horário; no mesmo instante, o que entra antes do que sai
    (17/11: pagamento e reembolso do mesmo pedido no mesmo segundo)."""
    return (m["data"], 0 if m["credito"] > ZERO else 1, m["id_externo"])


def encadear_saldo(movs: list[dict[str, Any]], saldo_total: Decimal) -> None:
    """A linha mais nova termina no Saldo total da tela; as anteriores, pra trás."""
    saldo = saldo_total
    for m in sorted(movs, key=_ordem, reverse=True):
        m["saldo_apos"] = saldo
        saldo = saldo - m["credito"] + m["debito"]


def previsto_do_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha de Pedidos não processados → previsto do pedido."""
    pedido = _pedido(item.get("parent_order_sn"))
    valor = reais(item.get("estimated_revenue"))
    if pedido is None or valor is None:
        return None
    return {
        "pedido_marketplace": pedido,
        "valor": valor,
        "liquidacao_em": _quando(item.get("estimated_settled_time")),
        "status_pedido": (str(item.get("order_status_desc") or "").strip()[:60] or None),
        "raw": item,
    }


def _lista(leitura: dict[str, Any], chave: str) -> list[dict[str, Any]]:
    v = leitura.get(chave)
    if v is None:
        return []
    if not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
        raise LeituraInvalidaError(f"'{chave}' fora do formato")
    return v


def _total_da_tela(leitura: dict[str, Any], chave: str) -> int:
    """Quantas linhas a tela diz que a lista tem (o robô manda junto)."""
    total = leitura.get(f"{chave}_total")
    if total is None:
        raise LeituraInvalidaError(f"faltou '{chave}_total' (quantas linhas a tela mostra)")
    try:
        return int(total)
    except (TypeError, ValueError) as e:
        raise LeituraInvalidaError(f"'{chave}_total' não é número") from e


def _confere_total(leitura: dict[str, Any], chave: str, lidas: int) -> None:
    """Extrato: o robô tem que ter lido TODAS as páginas (o saldo depende de todas)."""
    total = _total_da_tela(leitura, chave)
    if total != lidas:
        raise LeituraInvalidaError(
            f"leitura incompleta de {chave}: a tela tem {total} linha(s) e vieram {lidas}"
        )


def identidade_da_loja(linhas: list[dict[str, Any]]) -> str | None:
    """O nº da loja na Temu (`mall_id`) que vem em cada linha do extrato. Linhas de duas
    lojas na mesma leitura = leitura inválida."""
    ids = {str(x["mall_id"]) for x in linhas if x.get("mall_id") not in (None, "")}
    if len(ids) > 1:
        raise LeituraInvalidaError(f"linhas de mais de uma loja da Temu na mesma leitura: {ids}")
    return next(iter(ids)) if ids else None


def _marca_do_saque(item: dict[str, Any]) -> str:
    bruto = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(bruto.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


async def previstos_dos_pedidos(
    session: AsyncSession, loja: ConciliacaoLoja, pedidos: set[str]
) -> dict[str, ConciliacaoPrevisto]:
    """nº do pedido na Temu → o previsto guardado (pro recálculo)."""
    pedidos = {p for p in pedidos if p}
    if not pedidos:
        return {}
    rows = (
        await session.execute(
            select(ConciliacaoPrevisto).where(
                ConciliacaoPrevisto.integration_id == loja.integration_id,
                ConciliacaoPrevisto.pedido_marketplace.in_(pedidos),
            )
        )
    ).scalars()
    return {p.pedido_marketplace: p for p in rows}


async def _gravar_previstos(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    previstos: list[dict[str, Any]],
    pagos_agora: set[str],
    agora: datetime,
) -> dict[str, int]:
    stats = {"novos": 0, "atualizados": 0, "mudaram": 0, "ja_pagos": 0, "iguais": 0}
    if not previstos:
        return stats
    pedidos = {p["pedido_marketplace"] for p in previstos}
    existentes = await previstos_dos_pedidos(session, loja, pedidos)
    # pedido que a Temu já pagou: o previsto dele congela (o que a tela disser depois —
    # o resto a pagar, por exemplo — não reescreve o que a venda prometeu)
    pagos = set(pagos_agora)
    pagos.update(
        (
            await session.execute(
                select(ConciliacaoMovimento.pedido_marketplace).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_TEMU,
                    ConciliacaoMovimento.tipo == "venda_liberada",
                    ConciliacaoMovimento.pedido_marketplace.in_(pedidos),
                )
            )
        ).scalars()
    )
    for p in previstos:
        ex = existentes.get(p["pedido_marketplace"])
        if ex is None:
            if p["pedido_marketplace"] in pagos:
                # já pago antes de o robô ver o previsto: não inventa um agora
                stats["ja_pagos"] += 1
                continue
            session.add(
                ConciliacaoPrevisto(
                    integration_id=loja.integration_id,
                    pedido_marketplace=p["pedido_marketplace"],
                    valor=p["valor"],
                    primeiro_valor=p["valor"],
                    liquidacao_em=p["liquidacao_em"],
                    status_pedido=p["status_pedido"],
                    primeiro_lido_em=agora,
                    lido_em=agora,
                    raw=p["raw"],
                )
            )
            stats["novos"] += 1
            continue
        if ex.pedido_marketplace in pagos:
            stats["ja_pagos"] += 1
            continue
        mudou = ex.valor != p["valor"]
        if mudou:
            stats["mudaram"] += 1
        if (
            mudou
            or (p["liquidacao_em"] is not None and ex.liquidacao_em != p["liquidacao_em"])
            or ex.status_pedido != p["status_pedido"]
        ):
            stats["atualizados"] += 1
        else:
            stats["iguais"] += 1
        ex.valor = p["valor"]
        if p["liquidacao_em"] is not None:
            ex.liquidacao_em = p["liquidacao_em"]
        ex.status_pedido = p["status_pedido"]
        ex.lido_em = agora
        ex.raw = p["raw"]
    await session.flush()
    return stats


CHAVE_IDENTIDADE = "identidade"


async def _conferir_identidade(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    mall_id: str | None,
    nome_na_tela: str | None,
    agora: datetime,
) -> None:
    """A tela lida é a da loja certa? O `mall_id` da primeira leitura (ou o que a
    migration já gravou) fica guardado; leitura com outro é recusada inteira — perfil do
    AdsPower trocado ou logado em outra conta da Temu gravaria o extrato de outra loja
    aqui."""
    if not mall_id:
        return
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_TEMU,
                ConciliacaoSync.chave == CHAVE_IDENTIDADE,
            )
        )
    ).scalar_one_or_none()
    guardado = str((row.detalhe or {}).get("mall_id") or "") if row is not None else ""
    if guardado and guardado != mall_id:
        raise LeituraInvalidaError(
            f"a tela lida é de outra loja da Temu (loja {mall_id}"
            f"{' · ' + nome_na_tela if nome_na_tela else ''}); esta é a {guardado}: "
            "confira o perfil do AdsPower"
        )
    if row is None:
        session.add(
            ConciliacaoSync(
                integration_id=loja.integration_id,
                origem=ORIGEM_TEMU,
                chave=CHAVE_IDENTIDADE,
                status="lido",
                pedido_em=agora,
                lido_em=agora,
                detalhe={"mall_id": mall_id, "loja_na_tela": nome_na_tela},
            )
        )
    elif not guardado:
        row.detalhe = {**(row.detalhe or {}), "mall_id": mall_id, "loja_na_tela": nome_na_tela}
        row.lido_em = agora


# a tela abre nos últimos 30 dias; o que foi gravado há menos que isto tem que voltar
DIAS_QUE_A_TELA_MOSTRA = 25


async def _conferir_linhas_gravadas(
    session: AsyncSession, loja: ConciliacaoLoja, movs: list[dict[str, Any]], agora: datetime
) -> None:
    """Leitura que NÃO traz linha já gravada do mesmo período é de outra tela (conta
    errada sem linhas pra entregar o `mall_id`, período trocado) ou veio pela metade —
    recusada. Sem isto, uma leitura vazia contava como "lida" e apagava o aviso."""
    mov = ConciliacaoMovimento
    if movs:
        datas = [m["data"] for m in movs]
        de, ate = min(datas), max(datas)
    else:
        de, ate = agora - timedelta(days=DIAS_QUE_A_TELA_MOSTRA), agora
    gravadas = set(
        (
            await session.execute(
                select(mov.id_externo).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_TEMU,
                    mov.data >= de,
                    mov.data <= ate,
                )
            )
        ).scalars()
    )
    faltam = gravadas - {m["id_externo"] for m in movs}
    if faltam:
        raise LeituraInvalidaError(
            f"a leitura não traz {len(faltam)} linha(s) já gravada(s) do mesmo período: "
            "tela de outra loja, período trocado ou leitura pela metade"
        )


async def _saldo_da_ultima_linha(session: AsyncSession, loja: ConciliacaoLoja) -> Decimal | None:
    """O saldo depois da linha mais nova gravada, pela mesma conta do Extrato do dia
    (âncora na última linha de horário único + o que veio depois: pagamento e reembolso
    no mesmo segundo formam um ciclo que a ordem sozinha não resolve)."""
    from app.routers.conciliacao import _saldo_anterior

    mov = ConciliacaoMovimento
    ultima = (
        await session.execute(
            select(mov.data)
            .where(mov.integration_id == loja.integration_id, mov.origem == ORIGEM_TEMU)
            .order_by(mov.data.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ultima is None:
        return None
    return await _saldo_anterior(session, loja, ultima.astimezone(BRT).date() + timedelta(days=1))


async def _saques_ja_vistos(session: AsyncSession, loja: ConciliacaoLoja) -> set[str] | None:
    """As marcas dos saques da leitura anterior (None = ainda não houve leitura)."""
    row = (
        await session.execute(
            select(ConciliacaoSync)
            .where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_TEMU,
                ConciliacaoSync.chave.like("leitura:%"),
            )
            .order_by(ConciliacaoSync.lido_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {str(x) for x in ((row.detalhe or {}).get("saques_vistos") or [])}


async def ingerir_leitura(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    leitura: dict[str, Any],
    *,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Grava uma leitura do robô. Levanta `LeituraInvalidaError` quando ela não serve —
    extrato incompleto (o saldo depende de TODAS as linhas da janela), tela de outra
    loja, valor ilegível. Toda a conferência vem ANTES da primeira gravação: leitura
    recusada não deixa nada pela metade."""
    agora = agora or datetime.now(UTC)
    if not isinstance(leitura, dict):
        raise LeituraInvalidaError("leitura fora do formato")
    linhas = _lista(leitura, "extrato")
    itens_previsto = _lista(leitura, "previstos")
    saques = _lista(leitura, "saques")
    _confere_total(leitura, "extrato", len(linhas))
    total_previstos = _total_da_tela(leitura, "previstos")
    saldo = saldo_da_tela(leitura.get("saldo"))
    if saldo["total"] is None:
        raise LeituraInvalidaError("faltou o saldo da tela (Reserva e Fundos disponíveis)")
    movs = [m for m in (linha_para_movimento(x) for x in linhas) if m]
    if len(movs) != len(linhas):
        raise LeituraInvalidaError(
            f"{len(linhas) - len(movs)} linha(s) do extrato sem data, valor ou identificação"
        )
    ids = [m["id_externo"] for m in movs]
    if len(set(ids)) != len(ids):
        raise LeituraInvalidaError("linha repetida no extrato (a mesma página lida duas vezes?)")
    lidos_previsto = [previsto_do_item(i) for i in itens_previsto]
    ilegiveis = sum(1 for p in lidos_previsto if p is None)
    # o mesmo pedido duas vezes na lista (não visto até 17/11): vale o primeiro
    previstos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for p in lidos_previsto:
        if p is None or p["pedido_marketplace"] in vistos:
            continue
        vistos.add(p["pedido_marketplace"])
        previstos.append(p)
    nome_na_tela = str(leitura.get("loja_na_tela") or "")[:120] or None
    mall_id = identidade_da_loja(linhas)
    avisos: list[str] = []
    if ilegiveis:
        # não trava o extrato por causa do previsto, mas não some calado: o pedido
        # ficaria "sem previsto" depois de pago sem ninguém saber por quê
        avisos.append(f"{ilegiveis} previsto(s) sem nº do pedido ou valor legível")
    if len(itens_previsto) < total_previstos:
        # o robô foi até o fim da lista; a Temu pode contar no total o que não mostra
        # nela. Não trava a loja (um previsto a menos vira "sem previsto" mais tarde, à
        # vista) — mas avisa.
        avisos.append(
            f"a tela diz {total_previstos} pedido(s) não processado(s) e a lista trouxe "
            f"{len(itens_previsto)}"
        )

    await _conferir_linhas_gravadas(session, loja, movs, agora)

    # ----- a partir daqui grava -----
    await _conferir_identidade(session, loja, mall_id, nome_na_tela, agora)
    encadear_saldo(movs, saldo["total"])
    lojas_bling = await bling_store_ids_da_loja(session, await _integracao(session, loja))
    mapa = await resolver_pedidos_bling(
        session, {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}, lojas_bling
    )
    for m in movs:
        achado = mapa.get(m["pedido_marketplace"] or "")
        if achado:
            m["pedido_bling"], m["bling_id"] = achado
    por_tipo: dict[str, int] = {}
    for m in movs:
        por_tipo[m["tipo"]] = por_tipo.get(m["tipo"], 0) + 1
    marcas_saques = sorted({_marca_do_saque(x) for x in saques})
    ja_vistos = await _saques_ja_vistos(session, loja)
    saques_novos = [x for x in marcas_saques if ja_vistos is None or x not in ja_vistos]
    # linha de antes do início da loja não entra no painel (como no arquivo da KFA)
    no_painel = [m for m in movs if m["data"].astimezone(BRT).date() >= loja.inicio]
    inseridos = await inserir_movimentos(session, loja, no_painel) if no_painel else 0
    pagos_agora = {
        m["pedido_marketplace"]
        for m in movs
        if m["tipo"] == "venda_liberada" and m["pedido_marketplace"]
    }
    stats_previstos = await _gravar_previstos(session, loja, previstos, pagos_agora, agora)
    # o saldo andou sem linha nova? (um saque que o leitor ainda não lê, um ajuste sem
    # linha): com linha nova o dia dela já acusa; sem linha nenhuma, só este aviso mostra
    saldo_gravado = await _saldo_da_ultima_linha(session, loja)
    if saldo_gravado is not None and abs(saldo_gravado - saldo["total"]) > Decimal("0.05"):
        avisos.append(
            f"o saldo da tela da Temu ({saldo['total']:.2f}) não é o da última linha do "
            f"extrato ({saldo_gravado:.2f}): o saldo andou sem linha"
        )

    datas: list[date] = [m["data"].astimezone(BRT).date() for m in movs]
    if saques_novos:
        # a Eron (piloto) nunca sacou: o formato do saque ainda não é conhecido. O saldo
        # da tela já desceu; sem a linha, o dia não fecha — e o aviso diz por quê. Só o
        # saque NOVO avisa: a aba Pagamentos é um histórico, o antigo volta em toda leitura.
        avisos.append(
            f"{len(saques_novos)} saque(s) novo(s) na tela da Temu ainda sem leitura: "
            "o saldo do dia pode não fechar"
        )
    stats: dict[str, Any] = {
        "linhas": len(linhas),
        "no_painel": len(no_painel),
        "antes_do_inicio": len(movs) - len(no_painel),
        "inseridos": inseridos,
        "ja_existiam": len(no_painel) - inseridos,
        "por_tipo": por_tipo,
        "previstos": {
            "lidos": len(itens_previsto),
            "na_tela": total_previstos,
            "ilegiveis": ilegiveis,
            "repetidos": len(itens_previsto) - ilegiveis - len(previstos),
            **stats_previstos,
        },
        "saques": len(saques),
        "saques_novos": len(saques_novos),
        "saques_vistos": marcas_saques[:500],
        "de": min(datas).isoformat() if datas else None,
        "ate": max(datas).isoformat() if datas else None,
        "saldo_temu": {
            **{k: (str(v) if v is not None else None) for k, v in saldo.items()},
            "lido_em": agora.isoformat(),
        },
        "loja_na_tela": nome_na_tela,
        "loja_na_temu": mall_id,
        "periodo": str(leitura.get("periodo") or "")[:60] or None,
    }
    if avisos:
        stats["aviso"] = " · ".join(avisos)
    if saques:
        stats["saques_brutos"] = saques[:20]
    session.add(
        ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_TEMU,
            # com microssegundos: duas leituras no mesmo segundo não colidem
            chave=f"leitura:{agora:%Y-%m-%dT%H:%M:%S.%f}",
            status="lido",
            pedido_em=agora,
            lido_em=agora,
            linhas=len(linhas),
            detalhe=stats,
        )
    )
    loja.ultimo_extrato_em = agora
    # prefixo próprio: a rodada da loja só limpa os avisos das leituras DELA (fatura/extrato/
    # recálculo) e o desfecho do robô só limpa "robô:" — este fica até a próxima leitura
    loja.ultimo_erro = f"temu: {stats['aviso']}"[:300] if avisos else None
    await session.flush()
    logger.info(
        "conciliacao_temu_leitura",
        loja=loja.conta,
        linhas=len(linhas),
        inseridos=inseridos,
        previstos=stats_previstos,
        saques=len(saques),
        saques_novos=len(saques_novos),
        saldo=str(saldo["total"]),
    )
    return stats


async def _integracao(session: AsyncSession, loja: ConciliacaoLoja) -> Any:
    from app.models import Integration

    return await session.get(Integration, loja.integration_id)


# ---------- robô do Mac mini ----------
# O botão ⟳ da loja e o cron da manhã criam um comando `temu_extrato` na fila do robô da
# Logística; o executor do mini (fila `conciliacao`) abre o perfil no AdsPower, lê a tela
# e envia a leitura (X-Agent-Token). O desfecho volta por /agent/comandos/{id}/resultado
# e cai em `conciliacao_amazon_csv.registrar_resultado_robo` (falhou → aviso na loja).


def e_loja_temu_por_robo(loja: ConciliacaoLoja) -> bool:
    return (
        getattr(loja.platform, "value", str(loja.platform)) == "temu"
        and loja.modo_extrato == "upload"
    )


async def enfileirar_leitura(
    session: AsyncSession, loja: ConciliacaoLoja, *, user_id: Any = None
) -> tuple[LogisticaRoboComando, bool]:
    """Cria o comando pro robô; se já há um pendente/em execução desta loja, devolve ele
    (novo=False)."""
    from app.services.logistica_robo import ACAO_TEMU_EXTRATO

    aberto = (
        await session.execute(
            select(LogisticaRoboComando)
            .where(
                LogisticaRoboComando.acao == ACAO_TEMU_EXTRATO,
                LogisticaRoboComando.status.in_(("pending", "claimed")),
                LogisticaRoboComando.payload["integration_id"].astext == str(loja.integration_id),
            )
            .order_by(LogisticaRoboComando.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if aberto is not None:
        return aberto, False
    cmd = LogisticaRoboComando(
        logistica_id=None,
        acao=ACAO_TEMU_EXTRATO,
        payload={
            "loja": loja.conta,
            "integration_id": str(loja.integration_id),
            "plataforma": "temu",
        },
        created_by=user_id,
    )
    session.add(cmd)
    await session.flush()
    logger.info("conciliacao_temu_enfileirado", loja=loja.conta, comando=str(cmd.id))
    return cmd, True


async def enfileirar_leitura_todas(session: AsyncSession) -> list[str]:
    """Cron da manhã: uma tarefa por loja da Temu."""
    from app.services.conciliacao_jobs import lojas_ativas

    novas: list[str] = []
    for loja in await lojas_ativas(session):
        if not e_loja_temu_por_robo(loja):
            continue
        _cmd, novo = await enfileirar_leitura(session, loja)
        if novo:
            novas.append(loja.conta)
    await session.commit()
    return novas
